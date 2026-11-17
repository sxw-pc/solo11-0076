"""Primitive two-dimensional predicates and sequence helpers."""
from __future__ import annotations
import math
from typing import Any, Iterator, Sequence
from .util import stable_round
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

# Intersection categories for an ordered pair of non-degenerate segments.
SEGMENT_PAIR_CATEGORIES = (
    "none",
    "shared_endpoint",
    "point_touch",
    "proper_crossing",
    "collinear_overlap",
    "undecidable",
)
# Positional relationship between two segments of one feature.
SEGMENT_PAIR_RELATIONSHIPS = (
    "adjacent",
    "degenerate_adjacent",
    "closure",
    "non_adjacent",
    "cross_part",
)

def nonzero_segments(
    points: Sequence[Point], epsilon: float
) -> list[tuple[int, Point, Point]]:
    """Non-degenerate segments as ``(start_index, a, b)`` in original order.

    Zero-length segments are excluded here because they are reported by the
    dedicated ZERO_LENGTH_SEGMENT rule; their indices are still preserved so
    adjacency between the remaining segments is judged on original positions.
    """
    segments: list[tuple[int, Point, Point]] = []
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        if distance(a, b) > epsilon:
            segments.append((index, a, b))
    return segments

def classify_segment_pair(
    a: Point, b: Point, c: Point, d: Point, epsilon: float
) -> tuple[str, dict[str, Any]]:
    """Classify how two non-degenerate segments intersect.

    Returns ``(category, detail)`` where category is one of
    ``SEGMENT_PAIR_CATEGORIES`` and detail carries category-specific,
    JSON-serializable evidence (points, overlap lengths, reasons).
    """
    o1 = orientation(a, b, c, epsilon)
    o2 = orientation(a, b, d, epsilon)
    o3 = orientation(c, d, a, epsilon)
    o4 = orientation(c, d, b, epsilon)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return "proper_crossing", {"point": _crossing_point(a, b, c, d)}
    if o1 == 0 and o2 == 0:
        return _classify_collinear(a, b, c, d, epsilon)
    touches = (
        (o1 == 0 and point_on_segment(c, a, b, epsilon))
        or (o2 == 0 and point_on_segment(d, a, b, epsilon))
        or (o3 == 0 and point_on_segment(a, c, d, epsilon))
        or (o4 == 0 and point_on_segment(b, c, d, epsilon))
    )
    if not touches:
        return "none", {}
    shared = _shared_endpoint_point(a, b, c, d, epsilon)
    if shared is not None:
        return "shared_endpoint", {"point": format_point(shared)}
    return "point_touch", {"point": format_point(_touch_point(a, b, c, d, epsilon))}

def line_segment_contacts(
    points: Sequence[Point], closed: bool, epsilon: float
) -> list[dict[str, Any]]:
    """All contacting segment pairs inside one line/ring part.

    Adjacent segments and the closing pair of a closed sequence are examined
    like any other pair: sharing only the connecting vertex is classified as
    ``shared_endpoint`` (legal), while a retrace beyond that vertex surfaces
    as ``collinear_overlap`` or ``undecidable``.  Zero-length segments never
    take part in pairs, but they also never hide pairs of real segments.
    """
    segments = nonzero_segments(points, epsilon)
    zero_indices = {
        index
        for index in range(len(points) - 1)
        if distance(points[index], points[index + 1]) <= epsilon
    }
    contacts: list[dict[str, Any]] = []
    for left in range(len(segments)):
        i, a, b = segments[left]
        for right in range(left + 1, len(segments)):
            j, c, d = segments[right]
            category, detail = classify_segment_pair(a, b, c, d, epsilon)
            if category == "none":
                continue
            relationship = _pair_relationship(
                i, j, left, right, len(segments), zero_indices, closed
            )
            contacts.append(
                {
                    "segment_indexes": (i, j),
                    "relationship": relationship,
                    "category": category,
                    "detail": detail,
                }
            )
    return contacts

def line_self_intersections(
    points: Sequence[Point], closed: bool, epsilon: float
) -> list[tuple[int, int]]:
    """Index pairs that violate the strict simple-line (boundary) contract.

    Kept as a convenience wrapper over :func:`line_segment_contacts`; a pair
    is returned when it is a crossing, a positive-length or undecidable
    overlap, a point touch, or a non-adjacent shared endpoint.
    """
    found: list[tuple[int, int]] = []
    for contact in line_segment_contacts(points, closed, epsilon):
        category = contact["category"]
        relationship = contact["relationship"]
        if category in ("proper_crossing", "collinear_overlap", "undecidable", "point_touch"):
            found.append(contact["segment_indexes"])
        elif category == "shared_endpoint" and relationship == "non_adjacent":
            found.append(contact["segment_indexes"])
    return found

def _pair_relationship(
    i: int,
    j: int,
    left: int,
    right: int,
    segment_count: int,
    zero_indices: set[int],
    closed: bool,
) -> str:
    if j == i + 1:
        return "adjacent"
    if closed and left == 0 and right == segment_count - 1:
        return "closure"
    if all(index in zero_indices for index in range(i + 1, j)):
        return "degenerate_adjacent"
    return "non_adjacent"

def _classify_collinear(
    a: Point, b: Point, c: Point, d: Point, epsilon: float
) -> tuple[str, dict[str, Any]]:
    """Classify two segments that are collinear within ``epsilon``."""
    axis = 0 if abs(float(b[0]) - float(a[0])) >= abs(float(b[1]) - float(a[1])) else 1
    lo1, hi1 = sorted((float(a[axis]), float(b[axis])))
    lo2, hi2 = sorted((float(c[axis]), float(d[axis])))
    overlap = min(hi1, hi2) - max(lo1, lo2)
    if overlap > 0:
        low_point, high_point = _overlap_endpoints(a, b, c, d, axis, lo1, hi1, lo2, hi2)
        endpoints = [format_point(low_point), format_point(high_point)]
        if overlap > epsilon:
            return "collinear_overlap", {
                "overlap": endpoints,
                "overlap_length": stable_round(distance(low_point, high_point)),
            }
        return "undecidable", {
            "overlap": endpoints,
            "overlap_length": stable_round(distance(low_point, high_point)),
            "reason": (
                "collinear overlap is positive but does not exceed "
                "coordinate_epsilon; cannot stably distinguish a point touch "
                "from a positive-length overlap"
            ),
        }
    if overlap >= -epsilon:
        shared = _shared_endpoint_point(a, b, c, d, epsilon)
        if shared is not None:
            return "shared_endpoint", {"point": format_point(shared)}
        return "undecidable", {
            "reason": (
                "near-collinear segments are separated by less than "
                "coordinate_epsilon; contact cannot be stably determined"
            ),
        }
    return "none", {}

def _overlap_endpoints(
    a: Point,
    b: Point,
    c: Point,
    d: Point,
    axis: int,
    lo1: float,
    hi1: float,
    lo2: float,
    hi2: float,
) -> tuple[Point, Point]:
    points_by_value: dict[float, Point] = {}
    for point in (a, b, c, d):
        points_by_value.setdefault(float(point[axis]), point)
    return points_by_value[max(lo1, lo2)], points_by_value[min(hi1, hi2)]

def _shared_endpoint_point(
    a: Point, b: Point, c: Point, d: Point, epsilon: float
) -> Point | None:
    """The shared endpoint if an endpoint pair coincides within epsilon."""
    for first, second in ((b, c), (b, d), (a, c), (a, d)):
        if points_close(first, second, epsilon):
            return first
    return None

def _touch_point(a: Point, b: Point, c: Point, d: Point, epsilon: float) -> Point:
    """The endpoint of one segment that lies on the other segment."""
    for point in (c, d):
        if point_on_segment(point, a, b, epsilon):
            return point
    for point in (a, b):
        if point_on_segment(point, c, d, epsilon):
            return point
    return c

def _crossing_point(a: Point, b: Point, c: Point, d: Point) -> list[Any]:
    """Intersection point of two properly crossing segments."""
    denominator = (float(b[0]) - float(a[0])) * (float(d[1]) - float(c[1])) - (
        float(b[1]) - float(a[1])
    ) * (float(d[0]) - float(c[0]))
    numerator = (float(c[0]) - float(a[0])) * (float(d[1]) - float(c[1])) - (
        float(c[1]) - float(a[1])
    ) * (float(d[0]) - float(c[0]))
    t = numerator / denominator
    return [
        stable_round(float(a[0]) + t * (float(b[0]) - float(a[0]))),
        stable_round(float(a[1]) + t * (float(b[1]) - float(a[1]))),
    ]
