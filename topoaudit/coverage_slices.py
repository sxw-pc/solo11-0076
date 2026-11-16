"""Classification, status, and evidence shaping for coverage slices.

Kept separate from :mod:`topoaudit.rule_coverage` (orchestration and source
validation) so each module stays small.  All functions are pure and
deterministic; they consume already-computed slice dictionaries.
"""
from __future__ import annotations
from typing import Any
from .geometry import segment_distance, union_point_location
Point = tuple[float, float]
Polygon = tuple[tuple[Point, ...], ...]

SLICE_INTERIOR = "interior"
SLICE_BOUNDARY_OUTER = "boundary_outer"
SLICE_BOUNDARY_HOLE = "boundary_hole"
SLICE_TOLERANCE_EXTERIOR = "tolerance_exterior"
SLICE_EXTERIOR = "exterior"
SLICE_UNDETERMINED = "undetermined"
SLICE_KINDS = (
    SLICE_INTERIOR,
    SLICE_BOUNDARY_OUTER,
    SLICE_BOUNDARY_HOLE,
    SLICE_TOLERANCE_EXTERIOR,
    SLICE_EXTERIOR,
    SLICE_UNDETERMINED,
)

def classify_union(
    polygons: tuple[Polygon, ...],
    point: Point,
    epsilon: float,
    probe: float = 0.0,
) -> str:
    if not polygons:
        return SLICE_EXTERIOR
    location, _ = union_point_location(polygons, point, epsilon, probe)
    if location == "interior":
        return SLICE_INTERIOR
    if location == "boundary_outer":
        return SLICE_BOUNDARY_OUTER
    if location == "boundary_hole":
        return SLICE_BOUNDARY_HOLE
    return SLICE_EXTERIOR

def near_boundary(
    polygons: tuple[Polygon, ...], point: Point, tolerance: float
) -> bool:
    if tolerance <= 0.0:
        return False
    for polygon in polygons:
        for ring in polygon:
            for index in range(len(ring) - 1):
                if (
                    segment_distance(point, point, ring[index], ring[index + 1], 0.0)
                    <= tolerance
                ):
                    return True
    return False

def seam_probe(guard: float, length: float) -> float:
    """Tiny perpendicular offset used to detect shared seams.

    Large enough to escape floating-point noise at the boundary, small
    enough to stay within even thin polygons.  Used only to decide whether
    both sides of a boundary point belong to the union; it never changes
    length accounting.
    """
    return max(guard * 100.0, min(length * 1e-6, 1e-6), 1e-12)

def mark_all_undetermined(segments: list[dict[str, Any]], reason: str) -> None:
    for segment in segments:
        for entry in segment["slices"]:
            entry["kind"] = SLICE_UNDETERMINED
            entry["reason"] = reason

def coverage_status(
    total: float,
    accepted: float,
    uncovered: float,
    undetermined: float,
    segments: list[dict[str, Any]],
    undetermined_count: int,
) -> str:
    if not segments:
        return "empty"
    # Any undetermined length (invalid face, non-finite vertex, or a
    # mistrusted layer CRS) blocks a positive "covered" conclusion, even
    # when the affected slices happen to be zero length.
    if undetermined > 0.0 or undetermined_count > 0:
        return "undetermined"
    if uncovered > 0.0:
        return "partial" if accepted > 0.0 else "uncovered"
    if total <= 0.0:
        return "empty"
    return "covered"

def uncovered_slices(
    segments: list[dict[str, Any]], boundary_mode: str
) -> list[dict[str, Any]]:
    rejected = {SLICE_EXTERIOR, SLICE_TOLERANCE_EXTERIOR, SLICE_UNDETERMINED}
    if boundary_mode == "strict_interior":
        rejected |= {SLICE_BOUNDARY_OUTER, SLICE_BOUNDARY_HOLE}
    result: list[dict[str, Any]] = []
    for segment in segments:
        for entry in segment["slices"]:
            if entry["kind"] not in rejected:
                continue
            item = {
                "part": segment["part"],
                "segment": segment["segment"],
                "kind": entry["kind"],
                "length": entry["length"],
                "start": entry["start"],
                "end": entry["end"],
            }
            if "reason" in entry:
                item["reason"] = entry["reason"]
            result.append(item)
    return result

def zero_lengths() -> dict[str, float]:
    return {name: 0.0 for name in SLICE_KINDS}
