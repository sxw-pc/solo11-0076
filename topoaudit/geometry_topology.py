"""Topology-clarity screening for certified polygon-overlap verdicts."""
from __future__ import annotations
from typing import Any, Optional
from .geometry import (
    Point,
    Polygon,
    line_self_intersections,
    point_location,
    ring_signed_area,
)
from .geometry_core import format_point, segments_intersect

def polygon_topology_issues(polygon: Polygon, epsilon: float = 0.0) -> list[dict[str, Any]]:
    """Structural defects that make a polygon interior ill-defined.

    An empty result means the polygon is topologically clear enough for
    certified pair verdicts.  Unclosed or too-short rings are handled by
    ``valid_polygon`` and the geometry rules, not here.
    """
    issues: list[dict[str, Any]] = []
    if not polygon:
        return issues
    rings = [tuple(ring) for ring in polygon]
    for index, ring in enumerate(rings):
        if len(ring) < 4:
            continue
        pairs = line_self_intersections(ring, True, epsilon)
        if pairs:
            issues.append({
                "issue": "ring_self_intersection",
                "ring_index": index,
                "segment_pairs": [list(pair) for pair in pairs[:20]],
            })
    for index in range(1, len(rings)):
        if abs(ring_signed_area(rings[index])) <= epsilon * epsilon:
            issues.append({"issue": "hole_degenerate", "ring_index": index})
    shell = rings[0]
    for index in range(1, len(rings)):
        hole = rings[index]
        outside_vertex = None
        boundary_vertex = None
        for vertex in hole[:-1]:
            location = point_location(shell, vertex, epsilon)
            if location == "outside" and outside_vertex is None:
                outside_vertex = vertex
            if location == "boundary" and boundary_vertex is None:
                boundary_vertex = vertex
        if outside_vertex is not None:
            issues.append({
                "issue": "hole_outside_shell",
                "ring_index": index,
                "vertex": format_point(outside_vertex),
            })
        if boundary_vertex is not None:
            issues.append({
                "issue": "hole_touches_shell",
                "ring_index": index,
                "vertex": format_point(boundary_vertex),
            })
        contact = _first_ring_contact(shell, hole, epsilon)
        if contact is not None:
            issues.append({
                "issue": "hole_shell_contact",
                "ring_index": index,
                "segments": [contact[0], contact[1]],
            })
    for left in range(1, len(rings)):
        for right in range(left + 1, len(rings)):
            hole_a, hole_b = rings[left], rings[right]
            nested = None
            for vertex in hole_a[:-1]:
                if point_location(hole_b, vertex, epsilon) == "inside":
                    nested = vertex
                    break
            if nested is None:
                for vertex in hole_b[:-1]:
                    if point_location(hole_a, vertex, epsilon) == "inside":
                        nested = vertex
                        break
            if nested is not None:
                issues.append({
                    "issue": "hole_nested",
                    "ring_indexes": [left, right],
                    "vertex": format_point(nested),
                })
            contact = _first_ring_contact(hole_a, hole_b, epsilon)
            if contact is not None:
                issues.append({
                    "issue": "hole_hole_contact",
                    "ring_indexes": [left, right],
                    "segments": [contact[0], contact[1]],
                })
    return issues

def _first_ring_contact(
    ring_a: tuple[Point, ...], ring_b: tuple[Point, ...], epsilon: float
) -> Optional[tuple[int, int]]:
    for i in range(len(ring_a) - 1):
        for j in range(len(ring_b) - 1):
            if segments_intersect(ring_a[i], ring_a[i + 1], ring_b[j], ring_b[j + 1], epsilon):
                return (i, j)
    return None
