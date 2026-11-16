"""Witness construction and budgeted boundary refinement for overlap verdicts.

All functions here are deterministic helpers used by
:mod:`topoaudit.geometry_overlap`.  They never mutate input coordinates;
witness points are newly computed evidence locations.
"""
from __future__ import annotations
import math
from collections import deque
from typing import Any, Iterable, Optional
from .geometry import (
    Point,
    Polygon,
    Ring,
    cross,
    distance,
    point_in_polygon,
    polygon_bbox,
    ring_signed_area,
)
from .geometry_core import point_on_segment, segments_cross_properly, segments_intersect

def iter_ring_edges(ring: Ring) -> Iterable[tuple[int, Point, Point]]:
    for index in range(len(ring) - 1):
        yield index, ring[index], ring[index + 1]

def segment_intersection_point(p1: Point, p2: Point, q1: Point, q2: Point) -> Point:
    dx1, dy1 = float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1])
    dx2, dy2 = float(q2[0]) - float(q1[0]), float(q2[1]) - float(q1[1])
    denominator = dx1 * dy2 - dy1 * dx2
    if denominator == 0.0:
        return (
            (float(p1[0]) + float(p2[0]) + float(q1[0]) + float(q2[0])) / 4.0,
            (float(p1[1]) + float(p2[1]) + float(q1[1]) + float(q2[1])) / 4.0,
        )
    t = ((float(q1[0]) - float(p1[0])) * dy2 - (float(q1[1]) - float(p1[1])) * dx2) / denominator
    return (float(p1[0]) + t * dx1, float(p1[1]) + t * dy1)

def inward_normal(ring: Ring, is_hole: bool, p1: Point, p2: Point) -> Point:
    """Unit normal of an edge pointing into the polygon interior."""
    dx, dy = float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1])
    length = math.hypot(dx, dy)
    if length == 0.0:
        return (0.0, 0.0)
    left = (-dy / length, dx / length)
    interior_left = ring_signed_area(ring) > 0
    if is_hole:
        interior_left = not interior_left
    if interior_left:
        return left
    return (-left[0], -left[1])

def vertex_bisector(ring: Ring, is_hole: bool, vertex_index: int) -> Point:
    """Bisector direction of the polygon-interior angle at a vertex."""
    count = len(ring) - 1
    if count < 1:
        return (0.0, 0.0)
    prev = ring[(vertex_index - 1) % count]
    curr = ring[vertex_index]
    nxt = ring[(vertex_index + 1) % count]
    n1 = inward_normal(ring, is_hole, prev, curr)
    n2 = inward_normal(ring, is_hole, curr, nxt)
    return (n1[0] + n2[0], n1[1] + n2[1])

def reference_scale(a: Polygon, b: Polygon) -> float:
    diagonals: list[float] = []
    for polygon in (a, b):
        box = polygon_bbox(polygon)
        if box is not None:
            diagonals.append(math.hypot(box[2] - box[0], box[3] - box[1]))
    if not diagonals:
        return 1.0
    return max(min(diagonals) / 16.0, 1e-9)

def ladder_witness(
    point: Point,
    direction: Point,
    a: Polygon,
    b: Polygon,
    epsilon: float,
    rungs: int = 10,
) -> Optional[Point]:
    """Nudge ``point`` along ``direction`` until strictly inside both polygons."""
    dx, dy = direction
    norm = math.hypot(dx, dy)
    if norm == 0.0:
        return None
    dx, dy = dx / norm, dy / norm
    scale = reference_scale(a, b)
    for rung in range(rungs):
        offset = max(4.0 * epsilon, scale / (2.0 ** rung))
        candidate = (point[0] + dx * offset, point[1] + dy * offset)
        if (
            point_in_polygon(a, candidate, epsilon) == "inside"
            and point_in_polygon(b, candidate, epsilon) == "inside"
        ):
            return candidate
    return None

def crossing_witness(a: Polygon, b: Polygon, crossing: dict[str, Any], epsilon: float) -> Optional[Point]:
    ring_a = a[crossing["ring_a"]]
    ring_b = b[crossing["ring_b"]]
    normal_a = inward_normal(ring_a, crossing["ring_a"] > 0, crossing["p1"], crossing["p2"])
    normal_b = inward_normal(ring_b, crossing["ring_b"] > 0, crossing["q1"], crossing["q2"])
    direction = (normal_a[0] + normal_b[0], normal_a[1] + normal_b[1])
    return ladder_witness(crossing["point"], direction, a, b, epsilon)

def collinear_stretch(p1: Point, p2: Point, q1: Point, q2: Point) -> Optional[tuple[float, float]]:
    """Exact collinear overlap of two segments as parameters along p1->p2."""
    if cross(p1, p2, q1) != 0.0 or cross(p1, p2, q2) != 0.0:
        return None
    dx, dy = float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1])
    if dx == 0.0 and dy == 0.0:
        return None
    if abs(dx) >= abs(dy):
        def param(point: Point) -> float:
            return (float(point[0]) - float(p1[0])) / dx
    else:
        def param(point: Point) -> float:
            return (float(point[1]) - float(p1[1])) / dy
    lo = max(0.0, min(param(q1), param(q2)))
    hi = min(1.0, max(param(q1), param(q2)))
    if not lo < hi:
        return None
    return (lo, hi)

def touch_point(p1: Point, p2: Point, q1: Point, q2: Point) -> Point:
    for point in (p1, p2):
        if point_on_segment(point, q1, q2, 0.0):
            return point
    for point in (q1, q2):
        if point_on_segment(point, p1, p2, 0.0):
            return point
    return segment_intersection_point(p1, p2, q1, q2)

def point_segment_distance(point: Point, a: Point, b: Point) -> float:
    dx, dy = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return distance(point, a)
    t = ((float(point[0]) - float(a[0])) * dx + (float(point[1]) - float(a[1])) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    projection = (float(a[0]) + t * dx, float(a[1]) + t * dy)
    return distance(point, projection)

def segment_distance(p1: Point, p2: Point, q1: Point, q2: Point) -> float:
    if segments_intersect(p1, p2, q1, q2, 0.0):
        return 0.0
    return min(
        point_segment_distance(p1, q1, q2),
        point_segment_distance(p2, q1, q2),
        point_segment_distance(q1, p1, p2),
        point_segment_distance(q2, p1, p2),
    )

Stretch = tuple[float, float, float, float, float, float]

def boundary_contacts(
    a: Polygon, b: Polygon, epsilon: float
) -> tuple[list[Stretch], list[Point], list[tuple[Point, float]], list[Point]]:
    """Classify every boundary edge pair of two clear polygons.

    Returns (same-side exact stretches, exact touch points, fuzzy contacts
    within ``(0, epsilon]``, exact transversal crossings below tolerance).
    """
    stretches: list[Stretch] = []
    exact_contacts: list[Point] = []
    fuzzy_contacts: list[tuple[Point, float]] = []
    unstable_crossings: list[Point] = []
    for ring_a_index, ring_a in enumerate(a):
        for ring_b_index, ring_b in enumerate(b):
            for _, p1, p2 in iter_ring_edges(ring_a):
                for _, q1, q2 in iter_ring_edges(ring_b):
                    if segments_cross_properly(p1, p2, q1, q2, 0.0):
                        unstable_crossings.append(segment_intersection_point(p1, p2, q1, q2))
                        continue
                    stretch = collinear_stretch(p1, p2, q1, q2)
                    if stretch is not None:
                        lo, hi = stretch
                        dx, dy = float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1])
                        start = (float(p1[0]) + lo * dx, float(p1[1]) + lo * dy)
                        end = (float(p1[0]) + hi * dx, float(p1[1]) + hi * dy)
                        normal_a = inward_normal(ring_a, ring_a_index > 0, p1, p2)
                        normal_b = inward_normal(ring_b, ring_b_index > 0, q1, q2)
                        if normal_a[0] * normal_b[0] + normal_a[1] * normal_b[1] > 0.0:
                            stretches.append(
                                (start[0], start[1], end[0], end[1], normal_a[0], normal_a[1])
                            )
                        else:
                            exact_contacts.append(
                                ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
                            )
                        continue
                    if segments_intersect(p1, p2, q1, q2, 0.0):
                        exact_contacts.append(touch_point(p1, p2, q1, q2))
                        continue
                    gap = segment_distance(p1, p2, q1, q2)
                    if 0.0 < gap <= epsilon:
                        fuzzy_contacts.append((touch_point(p1, p2, q1, q2), gap))
    stretches.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return stretches, exact_contacts, fuzzy_contacts, unstable_crossings

def refine_stretches(
    stretches: list[Stretch],
    a: Polygon,
    b: Polygon,
    epsilon: float,
    budget: int,
) -> tuple[Optional[str], int, dict[str, Any]]:
    """Search same-side coincident stretches for a common interior point.

    Consumes one budget unit per evaluated candidate.  Returns
    ``("overlap", consumed, {"witness": ...})``,
    ``("budget_exhausted", consumed, {...})``,
    ``("tolerance_unstable", consumed, {...})`` or ``(None, consumed, {})``
    when every stretch was decided without finding overlap.
    """
    consumed = 0
    unstable: list[Point] = []
    queue: deque[Stretch] = deque(stretches)
    min_split = max(16.0 * epsilon, 1e-12)
    while queue:
        x0, y0, x1, y1, nx, ny = queue.popleft()
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        length = math.hypot(x1 - x0, y1 - y0)
        if consumed >= budget:
            return "budget_exhausted", consumed, {
                "location": (mx, my),
                "pending": len(queue) + 1,
            }
        consumed += 1
        delta = max(4.0 * epsilon, length / 4.0)
        candidate = (mx + nx * delta, my + ny * delta)
        if (
            point_in_polygon(a, candidate, epsilon) == "inside"
            and point_in_polygon(b, candidate, epsilon) == "inside"
        ):
            return "overlap", consumed, {
                "witness": candidate,
                "stretch": [[x0, y0], [x1, y1]],
            }
        if length / 2.0 <= min_split:
            unstable.append((mx, my))
            continue
        queue.append((x0, y0, mx, my, nx, ny))
        queue.append((mx, my, x1, y1, nx, ny))
    if unstable:
        return "tolerance_unstable", consumed, {"locations": unstable}
    return None, consumed, {}
