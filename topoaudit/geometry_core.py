"""Primitive two-dimensional predicates and sequence helpers."""
from __future__ import annotations
import math
from typing import Any, Iterator, Sequence
Point = tuple[float, float]

def is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )

def is_finite_point(point: Any) -> bool:
    return (
        isinstance(point, (tuple, list))
        and len(point) == 2
        and is_finite_number(point[0])
        and is_finite_number(point[1])
    )

def format_point(point: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    for value in point:
        if isinstance(value, float):
            if math.isfinite(value):
                result.append(0.0 if value == 0 else value)
            else:
                result.append(str(value))
        else:
            result.append(value)
    return result

def iter_numeric_points(value: Any, path: str = "$.coordinates") -> Iterator[tuple[str, Any]]:
    """Yield every coordinate pair-shaped leaf with a stable JSON-style path."""
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        yield path, value
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_numeric_points(child, f"{path}[{index}]")

def distance(a: Point, b: Point) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

def points_close(a: Point, b: Point, epsilon: float) -> bool:
    return distance(a, b) <= epsilon

def cross(a: Point, b: Point, c: Point) -> float:
    return (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - (
        float(b[1]) - float(a[1])
    ) * (float(c[0]) - float(a[0]))

def orientation(a: Point, b: Point, c: Point, epsilon: float = 0.0) -> int:
    value = cross(a, b, c)
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0

def point_on_segment(point: Point, a: Point, b: Point, epsilon: float = 0.0) -> bool:
    length = distance(a, b)
    if length <= epsilon:
        return points_close(point, a, epsilon)
    if not (
        min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
    ):
        return False
    return abs(cross(a, b, point)) <= epsilon * max(length, 1.0)

def segments_intersect(a: Point, b: Point, c: Point, d: Point, epsilon: float = 0.0) -> bool:
    """Return true for crossing, touching, or collinearly overlapping segments."""
    o1 = orientation(a, b, c, epsilon)
    o2 = orientation(a, b, d, epsilon)
    o3 = orientation(c, d, a, epsilon)
    o4 = orientation(c, d, b, epsilon)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    if o1 == 0 and point_on_segment(c, a, b, epsilon):
        return True
    if o2 == 0 and point_on_segment(d, a, b, epsilon):
        return True
    if o3 == 0 and point_on_segment(a, c, d, epsilon):
        return True
    if o4 == 0 and point_on_segment(b, c, d, epsilon):
        return True
    return False

def segments_cross_properly(a: Point, b: Point, c: Point, d: Point, epsilon: float = 0.0) -> bool:
    return (
        orientation(a, b, c, epsilon) * orientation(a, b, d, epsilon) < 0
        and orientation(c, d, a, epsilon) * orientation(c, d, b, epsilon) < 0
    )

def consecutive_duplicates(points: Sequence[Point], epsilon: float) -> list[int]:
    """Indices of points that repeat the immediately preceding point."""
    return [
        index
        for index in range(1, len(points))
        if points_close(points[index - 1], points[index], epsilon)
    ]

def zero_length_segments(points: Sequence[Point], epsilon: float) -> list[int]:
    """Indices of zero-length segments (segment starts at this index)."""
    return [
        index
        for index in range(len(points) - 1)
        if distance(points[index], points[index + 1]) <= epsilon
    ]

def ring_closed(ring: Sequence[Point], epsilon: float) -> bool:
    return bool(ring) and points_close(ring[0], ring[-1], epsilon)

def line_self_intersections(
    points: Sequence[Point], closed: bool, epsilon: float
) -> list[tuple[int, int]]:
    segments: list[tuple[int, Point, Point]] = []
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        if distance(a, b) > epsilon:
            segments.append((index, a, b))
    found: list[tuple[int, int]] = []
    for left in range(len(segments)):
        i, a, b = segments[left]
        for right in range(left + 1, len(segments)):
            j, c, d = segments[right]
            if right == left + 1:
                continue
            if closed and left == 0 and right == len(segments) - 1:
                continue
            if segments_intersect(a, b, c, d, epsilon):
                found.append((i, j))
    return found
