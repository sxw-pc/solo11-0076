"""Per-segment slicing and classification for the coverage audit.

Splits one source line segment at every polygon-ring crossing and every
invalid-source extent, classifies each resulting slice against the union,
and accumulates length once per slice.  Slices retain the original line
coordinates so evidence is directly reviewable.
"""
from __future__ import annotations
from typing import Any, Optional
from .coverage_slices import (
    SLICE_EXTERIOR,
    SLICE_TOLERANCE_EXTERIOR,
    SLICE_UNDETERMINED,
    classify_union,
    near_boundary,
    seam_probe,
)
from .geometry import (
    distance,
    format_point,
    interpolate,
    is_finite_point,
    segment_bbox_interval,
    split_segment,
)
from .model import CoverageRuleConfig
from .util import stable_round
Point = tuple[float, float]
Ring = tuple[Point, ...]
Polygon = tuple[Ring, ...]

# Smallest geometric guard; keeps degenerate segments from dividing by zero.
_MIN_GEOMETRY_EPSILON = 1e-12

def _box_contains_with_tolerance(
    box: tuple[float, float, float, float], point: Point, tolerance: float
) -> bool:
    return (
        box[0] - tolerance <= float(point[0]) <= box[2] + tolerance
        and box[1] - tolerance <= float(point[1]) <= box[3] + tolerance
    )

def _safe_point(point: tuple[Any, Any]) -> Optional[list[Any]]:
    return format_point(point) if is_finite_point(point) else None

def coverage_segment(
    rule: CoverageRuleConfig,
    part: str,
    index: int,
    a: tuple[Any, Any],
    b: tuple[Any, Any],
    valid_polygons: tuple[Polygon, ...],
    rings: tuple[Ring, ...],
    invalid_boxes: tuple[tuple[float, float, float, float], ...],
    tally: Any,
    reasons: set[str],
    coordinate_epsilon: float,
) -> Optional[dict[str, Any]]:
    if not is_finite_point(a) or not is_finite_point(b):
        tally.add(0.0, SLICE_UNDETERMINED)
        reasons.add("non_finite_coordinate")
        return {
            "part": part,
            "segment": index,
            "start": _safe_point(a),
            "end": _safe_point(b),
            "slices": [
                {
                    "kind": SLICE_UNDETERMINED,
                    "length": 0.0,
                    "start": _safe_point(a),
                    "end": _safe_point(b),
                    "reason": "non_finite_coordinate",
                }
            ],
        }
    guard = max(coordinate_epsilon, _MIN_GEOMETRY_EPSILON)
    length = distance(a, b)
    probe = seam_probe(guard, length)
    if length <= guard:
        kind = classify_union(valid_polygons, a, guard, probe)
        if kind == SLICE_EXTERIOR:
            kind = SLICE_TOLERANCE_EXTERIOR
        tally.add(0.0, kind)
        return {
            "part": part,
            "segment": index,
            "start": format_point(a),
            "end": format_point(b),
            "slices": [
                {
                    "kind": kind,
                    "length": 0.0,
                    "start": format_point(a),
                    "end": format_point(b),
                }
            ],
        }
    params = split_segment(a, b, rings, guard)
    # Also split at invalid-source bounds so an undetermined zone is
    # confined to the invalid face's extent, not the whole segment.
    for box in invalid_boxes:
        interval = segment_bbox_interval(a, b, box)
        if interval is None:
            continue
        params = sorted(set(params) | {interval[0], interval[1]})
    slices: list[dict[str, Any]] = []
    for left, right in zip(params, params[1:]):
        if right - left <= 1e-15:
            continue
        start = interpolate(a, b, left)
        end = interpolate(a, b, right)
        midpoint = interpolate(a, b, (left + right) / 2.0)
        kind = classify_union(valid_polygons, midpoint, guard, probe)
        if kind == SLICE_EXTERIOR and near_boundary(
            valid_polygons, midpoint, rule.tolerance
        ):
            kind = SLICE_TOLERANCE_EXTERIOR
        unknown = kind in (SLICE_EXTERIOR, SLICE_TOLERANCE_EXTERIOR) and any(
            _box_contains_with_tolerance(box, midpoint, rule.tolerance)
            for box in invalid_boxes
        )
        if unknown:
            kind = SLICE_UNDETERMINED
            reasons.add("invalid_polygon_source")
        slice_length = length * (right - left)
        tally.add(slice_length, kind)
        entry: dict[str, Any] = {
            "kind": kind,
            "length": stable_round(slice_length),
            "start": format_point(start),
            "end": format_point(end),
        }
        if unknown:
            entry["reason"] = "invalid_polygon_source"
        slices.append(entry)
    if not slices:
        return None
    return {
        "part": part,
        "segment": index,
        "start": format_point(a),
        "end": format_point(b),
        "slices": slices,
    }
