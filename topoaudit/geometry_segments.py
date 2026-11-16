"""Segment splitting and proximity primitives for line-coverage audits.

Kept separate from :mod:`topoaudit.geometry_coverage` (ring validity and
union location) so each module stays small and single-purpose.  Both are
re-exported through :mod:`topoaudit.geometry`.
"""
from __future__ import annotations
from typing import Optional, Sequence
from .geometry_core import distance, point_on_segment, segments_intersect
Point = tuple[float, float]

def closest_point_on_segment(point: Point, a: Point, b: Point) -> Point:
    """Closest point on segment ``ab`` to ``point`` (deterministic)."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    px, py = float(point[0]), float(point[1])
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= 0.0:
        return (ax, ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_squared
    if t <= 0.0:
        return (ax, ay)
    if t >= 1.0:
        return (bx, by)
    return (ax + t * dx, ay + t * dy)

def segment_distance(a: Point, b: Point, c: Point, d: Point, epsilon: float = 0.0) -> float:
    """Minimum distance between two closed segments."""
    if segments_intersect(a, b, c, d, epsilon):
        return 0.0
    candidates = (
        distance(a, closest_point_on_segment(a, c, d)),
        distance(b, closest_point_on_segment(b, c, d)),
        distance(c, closest_point_on_segment(c, a, b)),
        distance(d, closest_point_on_segment(d, a, b)),
    )
    return min(candidates)

def _projection_param(point: Point, a: Point, b: Point) -> float:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= 0.0:
        return 0.0
    return ((float(point[0]) - ax) * dx + (float(point[1]) - ay) * dy) / length_squared

def _intersection_params(
    a: Point, b: Point, c: Point, d: Point, epsilon: float
) -> list[float]:
    """Parameters ``t`` on ``ab`` where it meets segment ``cd``."""
    length_ab = distance(a, b)
    if length_ab <= epsilon:
        return []
    params: list[float] = []
    for point in (c, d):
        if point_on_segment(point, a, b, epsilon):
            params.append(_projection_param(point, a, b))
    rx, ry = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    sx, sy = float(d[0]) - float(c[0]), float(d[1]) - float(c[1])
    denominator = rx * sy - ry * sx
    length_cd = distance(c, d)
    parallel_epsilon = epsilon * max(length_ab, 1.0) * max(length_cd, 1.0)
    if abs(denominator) > parallel_epsilon:
        t = (
            (float(c[0]) - float(a[0])) * sy
            - (float(c[1]) - float(a[1])) * sx
        ) / denominator
        u = (
            (float(c[0]) - float(a[0])) * ry
            - (float(c[1]) - float(a[1])) * rx
        ) / denominator
        tolerance = epsilon / length_ab if length_ab > 0 else 0.0
        if -tolerance <= t <= 1.0 + tolerance and -tolerance <= u <= 1.0 + tolerance:
            params.append(t)
    return params

def split_segment(
    a: Point, b: Point, rings: Sequence[Sequence[Point]], epsilon: float
) -> list[float]:
    """Split parameters for segment ``ab`` against boundary rings.

    Returns a sorted, de-duplicated list starting at ``0.0`` and ending at
    ``1.0``.  Every open interval between consecutive parameters has a
    constant inside/boundary/outside relation to the rings.  Rings are
    treated as cyclic even when unclosed, so a malformed ring still
    provides stable split points for conservative undetermined carving.
    """
    length = distance(a, b)
    if length <= epsilon:
        return [0.0, 1.0]
    raw: list[float] = [0.0, 1.0]
    for ring in rings:
        if len(ring) < 2:
            continue
        for index in range(len(ring)):
            start = ring[index]
            end = ring[(index + 1) % len(ring)]
            if distance(start, end) <= epsilon:
                continue
            raw.extend(_intersection_params(a, b, start, end, epsilon))
    clamped = sorted(
        0.0 if value <= 0.0 else 1.0 if value >= 1.0 else float(value)
        for value in raw
    )
    merged: list[float] = []
    for value in clamped:
        if not merged or value - merged[-1] > 1e-12:
            merged.append(value)
    merged[0] = 0.0
    merged[-1] = 1.0
    return merged

def interpolate(a: Point, b: Point, t: float) -> Point:
    if t <= 0.0:
        return (float(a[0]), float(a[1]))
    if t >= 1.0:
        return (float(b[0]), float(b[1]))
    return (
        float(a[0]) + t * (float(b[0]) - float(a[0])),
        float(a[1]) + t * (float(b[1]) - float(a[1])),
    )

def segment_bbox_interval(
    a: Point, b: Point, bbox: tuple[float, float, float, float]
) -> Optional[tuple[float, float]]:
    """Parameters ``[t0, t1]`` where segment ``ab`` lies inside ``bbox``.

    Liang-Barsky clipping.  ``None`` means the segment never enters the
    box.  Used to carve conservative undetermined zones out of exterior
    length without treating the whole segment as unknowable.
    """
    x_min, y_min, x_max, y_max = (float(value) for value in bbox)
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        if x_min <= ax <= x_max and y_min <= ay <= y_max:
            return (0.0, 1.0)
        return None
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, ax - x_min),
        (dx, x_max - ax),
        (-dy, ay - y_min),
        (dy, y_max - ay),
    ):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > upper:
                return None
            if ratio > lower:
                lower = ratio
        else:
            if ratio < lower:
                return None
            if ratio < upper:
                upper = ratio
    if lower > upper or upper < 0.0 or lower > 1.0:
        return None
    return (max(0.0, lower), min(1.0, upper))
