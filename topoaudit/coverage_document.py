"""JSON-document projection for the opt-in coverage audit.

Kept separate from :mod:`topoaudit.coverage` so each module stays small and the
grid/validation logic is not tangled with report shaping.  Every value written
here is stable-rounded and order-independent.
"""
from __future__ import annotations
from typing import Any, Optional, Sequence
from .coverage_grid import cells_document, group_bbox
from .geometry import (
    coverage_polygon_issue,
    edge_lies_on_ring,
    polygon_area,
    polygon_components,
    ring_axis_edges,
)
from .model import Geometry, LoadedLayer, public_geometry
from .util import canonical_json, sha256_text, stable_round
Point = tuple[float, float]
Polygon = tuple[tuple[Point, ...], ...]

def geometry_document(geometry: Geometry) -> dict[str, Any]:
    """Canonical inline GeoJSON shape used for AOI digests."""
    return public_geometry(geometry)

def aoi_document(
    geometry: Geometry,
    polygons: Sequence[Polygon],
    area: Optional[float],
    epsilon: Optional[float],
) -> dict[str, Any]:
    box = group_bbox(polygons)
    return {
        "geometry_type": geometry.kind,
        "part_count": len(polygons),
        "hole_count": sum(len(polygon) - 1 for polygon in polygons),
        "area": None if area is None else stable_round(area),
        "bbox": None if box is None else [stable_round(value) for value in box],
        "geometry_digest": sha256_text(canonical_json(geometry_document(geometry)))[:16],
        "coordinate_epsilon": epsilon,
    }

def blocked_document(
    mode: str,
    layer_id: str,
    aoi: dict[str, Any],
    reason: str,
    issues: Optional[list[dict[str, Any]]],
    input_digest: str,
) -> dict[str, Any]:
    return {
        "status": "indeterminate",
        "mode": mode,
        "layer_id": layer_id,
        "aoi": aoi,
        "coverage": {key: None for key in (
            "aoi_area", "covered_area", "gap_area", "gap_component_count",
            "required_component_count", "required_area", "boundary_component_count",
            "boundary_area", "uncovered_percent",
        )},
        "components": [],
        "contributors": [],
        "blocked": {
            "reason": reason,
            "feature_issues": [] if issues is None else issues,
        },
        "input_digest": input_digest,
    }

def collect_parcels(
    layer: LoadedLayer, epsilon: float
) -> tuple[list[dict[str, Any]], list[Polygon], list[dict[str, Any]]]:
    """Collect supported parcel polygons plus traceable contributor records."""
    issues: list[dict[str, Any]] = []
    polygons: list[Polygon] = []
    contributors: list[dict[str, Any]] = []
    for feature in sorted(layer.features, key=lambda item: item.source_index):
        parts = polygon_components(feature.geometry)
        if not parts:
            issues.append(
                {
                    "feature_id": feature.feature_id,
                    "source_index": feature.source_index,
                    "geometry_type": feature.geometry.kind,
                    "reason": "not_a_polygon",
                }
            )
            continue
        for label, polygon in parts:
            issue = coverage_polygon_issue(polygon, epsilon)
            if issue is not None:
                issues.append(
                    {
                        "feature_id": feature.feature_id,
                        "source_index": feature.source_index,
                        "component_index": label,
                        "geometry_type": feature.geometry.kind,
                        "reason": issue,
                    }
                )
                continue
            polygons.append(polygon)
            contributors.append(
                {
                    "feature_id": feature.feature_id,
                    "source_index": feature.source_index,
                    "component_index": label,
                    "area": stable_round(polygon_area(polygon)),
                    "has_holes": len(polygon) > 1,
                }
            )
    return issues, polygons, contributors

def gap_document(
    index: int,
    group: Sequence[Polygon],
    mode: str,
    touches_boundary: bool,
    adjacent_ids: Sequence[str],
) -> dict[str, Any]:
    internal = not touches_boundary
    box = group_bbox(group)
    return {
        "component_id": f"G{index:03d}",
        "classification": "internal" if internal else "boundary",
        "touches_aoi_boundary": touches_boundary,
        "required": internal or mode == "all_uncovered",
        "area": stable_round(sum(polygon_area(piece) for piece in group)),
        "bbox": [] if box is None else [stable_round(value) for value in box],
        "cells": cells_document(group),
        "cell_count": len(group),
        "adjacent_feature_ids": sorted(adjacent_ids),
    }

def adjacent_feature_ids(
    group: Sequence[Polygon], layer: LoadedLayer, epsilon: float
) -> list[str]:
    """Feature IDs whose polygon boundary touches the gap boundary."""
    edges = [edge for piece in group for edge in ring_axis_edges(piece[0], epsilon)]
    names: set[str] = set()
    for feature in layer.features:
        if feature.feature_id in names:
            continue
        for _, polygon in polygon_components(feature.geometry):
            if coverage_polygon_issue(polygon, epsilon) is not None:
                continue
            if any(
                edge_lies_on_ring(edge, ring, epsilon)
                for edge in edges
                for ring in polygon
            ):
                names.add(feature.feature_id)
                break
    return sorted(names)

def coverage_input_digest(
    aoi: Geometry,
    layer_id: str,
    mode: str,
    layer: Optional[LoadedLayer],
    contributors: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "aoi": geometry_document(aoi),
        "layer_id": layer_id,
        "mode": mode,
        "source_sha256": None if layer is None else layer.input_file.sha256,
        "contributors": list(contributors),
    }
    return sha256_text(canonical_json(payload))
