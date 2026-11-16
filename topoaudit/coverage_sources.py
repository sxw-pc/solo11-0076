"""Polygon source validation and shared coverage data types.

A coverage source is one polygon component from the polygon layer.  Its
validity comes from :func:`topoaudit.geometry.polygon_validity`; invalid
components are retained only so the audit can report why a stretch of
line is undetermined.  Declared-CRS conflicts are folded into the same
reason channel.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from .coverage_slices import SLICE_KINDS
from .geometry import (
    bbox_of_points,
    is_finite_point,
    line_components,
    polygon_bbox,
    polygon_components,
    polygon_validity,
)
from .model import CRS, DeclaredCRS, Feature, LayerConfig, LoadedLayer
Point = tuple[float, float]
Ring = tuple[Point, ...]
Polygon = tuple[Ring, ...]

@dataclass(frozen=True)
class PolygonSource:
    """One polygon component offered as a coverage source."""

    feature_id: str
    source_index: int
    component: str
    polygon: Polygon
    bbox: Optional[tuple[float, float, float, float]]
    reasons: tuple[str, ...]
    @property
    def valid(self) -> bool:
        return not self.reasons

@dataclass
class CoverageTally:
    """Accumulates length and slice count per slice class."""

    lengths: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in SLICE_KINDS}
    )
    counts: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in SLICE_KINDS}
    )
    def add(self, length: float, kind: str) -> None:
        self.lengths[kind] += length
        self.counts[kind] += 1

def declared_crs_reason(layer: LayerConfig, global_crs: CRS) -> Optional[str]:
    declared: Optional[DeclaredCRS] = layer.declared_crs
    if declared is None:
        return None
    if declared.name != global_crs.name or declared.kind != global_crs.kind:
        return "declared_crs_mismatch"
    return None

def polygon_sources(
    layer: LoadedLayer,
    epsilon: float,
    extra_issue: Optional[str] = None,
) -> tuple[PolygonSource, ...]:
    sources: list[PolygonSource] = []
    for feature in sorted(layer.features, key=lambda item: item.source_index):
        for label, polygon in polygon_components(feature.geometry):
            valid, issues = polygon_validity(polygon, epsilon)
            reasons: tuple[str, ...] = () if valid else issues
            if extra_issue is not None:
                reasons = reasons + (extra_issue,)
            sources.append(
                PolygonSource(
                    feature_id=feature.feature_id,
                    source_index=feature.source_index,
                    component=label,
                    polygon=tuple(tuple(ring) for ring in polygon),
                    bbox=polygon_bbox(polygon),
                    reasons=reasons,
                )
            )
    return tuple(sources)

def invalid_source_evidence(
    sources: tuple[PolygonSource, ...],
) -> list[dict[str, object]]:
    return [
        {
            "feature_id": source.feature_id,
            "source_index": source.source_index,
            "component": source.component,
            "reasons": list(source.reasons),
        }
        for source in sources
    ]

def line_bbox(feature: Feature) -> Optional[tuple[float, float, float, float]]:
    points = [
        point
        for _, line in line_components(feature.geometry)
        for point in line
        if is_finite_point(point)
    ]
    return bbox_of_points(points)

def boxes_near(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    return not (
        a[2] + tolerance < b[0]
        or b[2] + tolerance < a[0]
        or a[3] + tolerance < b[1]
        or b[3] + tolerance < a[1]
    )
