"""Explicit opt-in orthogonal parcel coverage-gap audit.

The audit answers one question only: inside the configured AOI, which connected
regions are covered by no audited parcel, how large are they, and where are
they?  It never rewrites source features.  All arithmetic uses the same planar
coordinate semantics as the rest of the engine: no projection, no geodesic
correction.

Supported inputs are deliberately narrow:

* AOI geometry must be a valid ``Polygon``/``MultiPolygon``;
* every ring must be closed, finite and strictly axis-parallel;
* holes must not touch or cross their shell, and must not overlap each other.

Anything else is reported as ``indeterminate`` with a stable reason code rather
than approximated from a bounding box.  Because gaps are computed against the
union of parcels, overlapping parcels never double-count coverage, and overlap
area can never offset a gap elsewhere.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from .coverage_document import (
    aoi_document,
    blocked_document,
    adjacent_feature_ids,
    collect_parcels,
    coverage_input_digest,
    gap_document,
)
from .coverage_grid import boundary_contact, components, difference_pieces
from .geometry import (
    bbox_intersection,
    coverage_polygon_issue,
    polygon_area,
    polygon_bbox,
    polygon_components,
)
from .model import Geometry, LoadedLayer
from .util import stable_round
Point = tuple[float, float]
Polygon = tuple[tuple[Point, ...], ...]
MODES = ("internal_only", "all_uncovered")
_BLOCKING_GEOMETRY_KINDS = ("Polygon", "MultiPolygon")

@dataclass(frozen=True)
class CoverageConfig:
    """Computed coverage request handed to :func:`audit_coverage`."""
    aoi: Geometry
    layer_id: str
    mode: str

def aoi_polygons(geometry: Geometry) -> list[Polygon]:
    if geometry.kind not in _BLOCKING_GEOMETRY_KINDS:
        return []
    return [
        tuple(
            tuple((float(point[0]), float(point[1])) for point in ring)
            for ring in polygon
        )
        for _, polygon in polygon_components(geometry)
    ]

def aoi_issue(geometry: Geometry, epsilon: float) -> Optional[str]:
    """Validate the AOI and return a stable reason code, or ``None``."""
    if geometry.kind not in _BLOCKING_GEOMETRY_KINDS:
        return "aoi_not_polygon"
    polygons = aoi_polygons(geometry)
    if not polygons:
        return "aoi_empty"
    for polygon in polygons:
        issue = coverage_polygon_issue(polygon, epsilon)
        if issue is not None:
            return f"aoi_{issue}"
    return _multipolygon_issue(polygons, epsilon)

def _multipolygon_issue(
    polygons: Sequence[Polygon], epsilon: float
) -> Optional[str]:
    for left in range(len(polygons)):
        box_a = polygon_bbox(polygons[left])
        if box_a is None:
            continue
        for right in range(left + 1, len(polygons)):
            box_b = polygon_bbox(polygons[right])
            if box_b is None:
                continue
            intersection = bbox_intersection(box_a, box_b)
            if intersection is None:
                continue
            if (
                intersection[2] - intersection[0] > epsilon
                and intersection[3] - intersection[1] > epsilon
            ):
                return "aoi_parts_overlap"
    return None

def request_from_config(config: Any) -> Optional[CoverageConfig]:
    """Build the compute request from an :class:`AuditConfig`, if opted in."""
    if getattr(config, "coverage", None) is None:
        return None
    return CoverageConfig(
        aoi=config.coverage.aoi_geometry,
        layer_id=config.coverage.layer_id,
        mode=config.coverage.mode,
    )

def audit_coverage(
    config: CoverageConfig,
    layers: Sequence[LoadedLayer],
    epsilon: float,
) -> dict[str, Any]:
    """Compute the deterministic coverage report for one run.

    ``status`` is ``ok``, or ``indeterminate`` with a stable ``blocked.reason``
    when supported geometry cannot be established.  Uncertainty is never
    coerced into zero or into a bbox approximation.
    """
    layer = next(
        (item for item in layers if item.config.layer_id == config.layer_id), None
    )
    polygons = aoi_polygons(config.aoi)
    issue = aoi_issue(config.aoi, epsilon)
    if issue is not None:
        return _blocked(config, issue, polygons, layer, None)
    if layer is None:
        return _blocked(config, "layer_not_found", polygons, layer, None)
    parcel_issues, clip, contributors = collect_parcels(layer, epsilon)
    if parcel_issues:
        return _blocked(config, "unsupported_parcel_geometry", polygons, layer, parcel_issues)
    gaps = [
        gap_document(
            index,
            group,
            config.mode,
            boundary_contact(group, polygons, epsilon),
            adjacent_feature_ids(group, layer, epsilon),
        )
        for index, group in enumerate(
            components(difference_pieces(polygons, clip, epsilon), epsilon), start=1
        )
    ]
    total_area = sum(polygon_area(polygon) for polygon in polygons)
    gap_area = sum(item["area"] for item in gaps)
    required = [item for item in gaps if item["required"]]
    boundary = [item for item in gaps if item["classification"] == "boundary"]
    return {
        "status": "ok",
        "mode": config.mode,
        "layer_id": config.layer_id,
        "aoi": aoi_document(config.aoi, polygons, total_area, epsilon),
        "coverage": {
            "aoi_area": stable_round(total_area),
            "covered_area": stable_round(max(0.0, total_area - gap_area)),
            "gap_area": stable_round(gap_area),
            "gap_component_count": len(gaps),
            "required_component_count": len(required),
            "required_area": stable_round(sum(item["area"] for item in required)),
            "boundary_component_count": len(boundary),
            "boundary_area": stable_round(sum(item["area"] for item in boundary)),
            "uncovered_percent": stable_round(
                0.0 if total_area <= 0 else gap_area / total_area * 100.0
            ),
        },
        "components": gaps,
        "contributors": contributors,
        "blocked": None,
        "input_digest": coverage_input_digest(
            config.aoi, config.layer_id, config.mode, layer, contributors
        ),
    }

def _blocked(
    config: CoverageConfig,
    reason: str,
    polygons: Sequence[Polygon],
    layer: Optional[LoadedLayer],
    issues: Optional[list[dict[str, Any]]],
) -> dict[str, Any]:
    return blocked_document(
        mode=config.mode,
        layer_id=config.layer_id,
        aoi=aoi_document(config.aoi, polygons, None, None),
        reason=reason,
        issues=issues,
        input_digest=coverage_input_digest(
            config.aoi, config.layer_id, config.mode, layer, []
        ),
    )
