"""Coverage geometry: ring validity, union location, and segment splitting.

These primitives are separated from :mod:`topoaudit.geometry` to keep each
module focused.  They never mutate input geometry; callers still receive
original coordinates in evidence.  ``geometry`` re-exports every name here
so existing imports continue to work.
"""
from __future__ import annotations
import math
from typing import Sequence
from .geometry_core import (
    is_finite_point,
    line_self_intersections,
    point_on_segment,
    ring_closed,
)
Point = tuple[float, float]
Ring = tuple[Point, ...]
Polygon = tuple[Ring, ...]

# Ordered, stable vocabulary for polygon validity problems.  The order is
# used both for deterministic de-duplication and for evidence readability.
POLYGON_ISSUE_ORDER = (
    "non_finite_coordinate",
    "ring_too_few_points",
    "ring_not_closed",
    "ring_degenerate",
    "ring_self_intersection",
)
POLYGON_ISSUE_RANK = {name: index for index, name in enumerate(POLYGON_ISSUE_ORDER)}

def ring_signed_area(ring: Sequence[Point]) -> float:
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        total += float(x1) * float(y2) - float(x2) * float(y1)
    return total / 2.0

def point_location(ring: Sequence[Point], point: Point, epsilon: float = 0.0) -> str:
    """Classify a point against a ring as inside, boundary, or outside."""
    if len(ring) < 2:
        return "outside"
    inside = False
    count = len(ring)
    for index in range(count):
        a = ring[index]
        b = ring[(index + 1) % count]
        if point_on_segment(point, a, b, epsilon):
            return "boundary"
        if (a[1] > point[1]) != (b[1] > point[1]):
            x_at_y = (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]
            if x_at_y > point[0]:
                inside = not inside
    return "inside" if inside else "outside"

def point_in_polygon(polygon: Sequence[Sequence[Point]], point: Point, epsilon: float = 0.0) -> str:
    """Location of a point in a polygon with optional holes."""
    if not polygon:
        return "outside"
    outer = point_location(polygon[0], point, epsilon)
    if outer != "inside":
        return outer
    for hole in polygon[1:]:
        location = point_location(hole, point, epsilon)
        if location == "inside":
            return "outside"
        if location == "boundary":
            return "boundary"
    return "inside"

def polygon_validity(
    polygon: Sequence[Sequence[Point]], epsilon: float
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(is_valid, issues)`` for one polygon component.

    Issues are stable strings of the form ``rings[<i>]:<reason>``.  A
    polygon is valid only when every ring is finite, closed, has at least
    four coordinates, encloses non-zero area and has no non-adjacent
    segment intersection.  This predicate drives line-coverage status:
    invalid components may never contribute to an "inside" conclusion.
    """
    if not polygon:
        return False, ("empty",)
    found: set[str] = set()
    for ring_index, ring in enumerate(polygon):
        prefix = f"rings[{ring_index}]:"
        if not ring:
            found.add(prefix + "ring_too_few_points")
            continue
        if not all(is_finite_point(point) for point in ring):
            found.add(prefix + "non_finite_coordinate")
            continue
        if len(ring) < 4:
            found.add(prefix + "ring_too_few_points")
        if not ring_closed(ring, epsilon):
            found.add(prefix + "ring_not_closed")
        if len(ring) >= 3 and abs(ring_signed_area(ring)) <= epsilon * epsilon:
            found.add(prefix + "ring_degenerate")
        if len(ring) >= 3:
            if line_self_intersections(ring, ring_closed(ring, epsilon), epsilon):
                found.add(prefix + "ring_self_intersection")
    issues = sorted(
        found,
        key=lambda value: (
            int(value.split(":")[0][6:-1]),
            POLYGON_ISSUE_RANK.get(value.split(":", 1)[1], 99),
        ),
    )
    return (not issues), tuple(issues)

def union_point_location(
    polygons: Sequence[Polygon],
    point: Point,
    epsilon: float = 0.0,
    probe: float = 0.0,
) -> tuple[str, tuple[int, ...]]:
    """Locate a point against a union of polygons (outer rings minus holes).

    Returns ``(location, contributing_indexes)`` where location is one of
    ``interior``, ``boundary_outer``, ``boundary_hole`` or ``exterior``.

    Being inside any polygon is unambiguously ``interior``.  A point lying
    exactly on ring boundaries is resolved with ``probe``: if the union
    occupies both sides (a shared seam between adjacent or overlapping
    polygons) it is ``interior`` of the union, which lets two abutting
    faces cover a crossing line continuously.  Otherwise it is a genuine
    outer-ring or hole boundary.
    """
    inside: list[int] = []
    for index, polygon in enumerate(polygons):
        if polygon and point_in_polygon(polygon, point, epsilon) == "inside":
            inside.append(index)
    if inside:
        return "interior", tuple(inside)
    hits: list[tuple[int, int, Point, Point]] = []
    for index, polygon in enumerate(polygons):
        for ring_index, ring in enumerate(polygon):
            if len(ring) < 2:
                continue
            if point_location(ring, point, epsilon) != "boundary":
                continue
            for position in range(len(ring)):
                a = ring[position]
                b = ring[(position + 1) % len(ring)]
                if point_on_segment(point, a, b, epsilon):
                    hits.append((index, ring_index, a, b))
    if not hits:
        return "exterior", ()
    if probe > 0.0:
        for _, _, a, b in hits:
            dx = float(b[0]) - float(a[0])
            dy = float(b[1]) - float(a[1])
            norm = math.hypot(dx, dy)
            if norm <= 0.0:
                continue
            nx, ny = -dy / norm, dx / norm
            sides: list[bool] = []
            for sign in (1.0, -1.0):
                sample = (
                    float(point[0]) + sign * probe * nx,
                    float(point[1]) + sign * probe * ny,
                )
                sides.append(
                    any(
                        point_in_polygon(polygon, sample, epsilon) == "inside"
                        for polygon in polygons
                        if polygon
                    )
                )
            if all(sides):
                # Both sides belong to the union: this is an internal seam.
                return "interior", ()
    outer = {index for index, ring_index, _, _ in hits if ring_index == 0}
    holes = {index for index, ring_index, _, _ in hits if ring_index > 0}
    if outer:
        return "boundary_outer", tuple(sorted(outer))
    return "boundary_hole", tuple(sorted(holes))

