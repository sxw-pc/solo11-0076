"""Primitive two-dimensional predicates and sequence helpers."""
from __future__ import annotations
import math
from typing import Any, Iterator, Optional, Sequence
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


# ---------------------------------------------------------------------------
# Network-level predicates: real intersection geometry and grade decisions.
#
# Every predicate here is exact with respect to ``epsilon``: two segments
# whose only relation is overlapping bounding boxes, or whose points are
# merely closer than a proximity radius but do not truly touch, are reported
# as disjoint.  These functions never mutate source coordinates; all snapped
# points are local copies.
# ---------------------------------------------------------------------------

def segment_intersection_points(
    a: Point, b: Point, c: Point, d: Point, epsilon: float = 0.0
) -> list[Point]:
    """Return the real geometric intersection of two segments.

    Possible results are:

    * empty list          -- the segments do not touch at all;
    * one point           -- proper crossing, endpoint touch or T contact;
    * two ordered points  -- the collinear overlap interval.

    A proximity-only pair (distance above ``epsilon``) always yields an
    empty list even when the bounding boxes overlap.
    """
    if not segments_intersect(a, b, c, d, epsilon):
        return []
    o1 = orientation(a, b, c, epsilon)
    o2 = orientation(a, b, d, epsilon)
    o3 = orientation(c, d, a, epsilon)
    o4 = orientation(c, d, b, epsilon)
    if o1 == 0 and o2 == 0 and o3 == 0 and o4 == 0:
        return _collinear_overlap(a, b, c, d, epsilon)
    if o1 == 0 and point_on_segment(c, a, b, epsilon):
        return [_snap_point(c, epsilon)]
    if o2 == 0 and point_on_segment(d, a, b, epsilon):
        return [_snap_point(d, epsilon)]
    if o3 == 0 and point_on_segment(a, c, d, epsilon):
        return [_snap_point(a, epsilon)]
    if o4 == 0 and point_on_segment(b, c, d, epsilon):
        return [_snap_point(b, epsilon)]
    return [_proper_intersection(a, b, c, d)]


def _snap_point(point: Point, epsilon: float) -> Point:
    """Normalise negative zero and values indistinguishable from zero."""
    x, y = float(point[0]), float(point[1])
    if epsilon > 0:
        if abs(x) <= epsilon:
            x = 0.0
        if abs(y) <= epsilon:
            y = 0.0
    return (x, y)


def _proper_intersection(a: Point, b: Point, c: Point, d: Point) -> Point:
    denominator = (
        (float(a[0]) - float(b[0])) * (float(c[1]) - float(d[1]))
        - (float(a[1]) - float(b[1])) * (float(c[0]) - float(d[0]))
    )
    if denominator == 0.0:
        # Defensive fallback: orientation checks already established contact.
        return _snap_point(c, 0.0)
    dx, dy = float(a[0]) - float(c[0]), float(a[1]) - float(c[1])
    numerator_t = dx * (float(c[1]) - float(d[1])) - dy * (float(c[0]) - float(d[0]))
    t = numerator_t / denominator
    return (
        float(a[0]) + t * (float(b[0]) - float(a[0])),
        float(a[1]) + t * (float(b[1]) - float(a[1])),
    )


def _collinear_overlap(
    a: Point, b: Point, c: Point, d: Point, epsilon: float
) -> list[Point]:
    # The overlap interval is bounded by endpoints lying on *both* segments.
    shared: list[Point] = []
    for point in (a, b, c, d):
        if not point_on_segment(point, a, b, epsilon):
            continue
        if not point_on_segment(point, c, d, epsilon):
            continue
        if any(points_close(point, earlier, epsilon) for earlier in shared):
            continue
        shared.append(point)
    if len(shared) == 1:
        return [_snap_point(shared[0], epsilon)]
    anchor = min(shared, key=lambda item: (float(item[0]), float(item[1])))
    farthest = max(shared, key=lambda item: distance(anchor, item))
    return [_snap_point(anchor, epsilon), _snap_point(farthest, epsilon)]


def point_is_interior(
    point: Point, a: Point, b: Point, epsilon: float = 0.0
) -> bool:
    """True when ``point`` lies strictly inside segment ``a-b`` (not an end)."""
    if not point_on_segment(point, a, b, epsilon):
        return False
    return not (
        points_close(point, a, epsilon) or points_close(point, b, epsilon)
    )


def segment_relation(
    a: Point,
    b: Point,
    c: Point,
    d: Point,
    epsilon: float = 0.0,
) -> dict[str, Any]:
    """Classify the real relation between two finite segments.

    The returned dictionary has a stable shape::

        {"relation": "disjoint|crossing|touch|overlap",
         "points": [...],          # real intersection geometry, if any
         "interior_to_ab": bool,   # at least one contact point inside a-b
         "interior_to_cd": bool,
         "segment_pairs": [[i, j]]}  # one pair, usable as review evidence
    """
    contacts = segment_intersection_points(a, b, c, d, epsilon)
    if not contacts:
        return {
            "relation": "disjoint",
            "points": [],
            "interior_to_ab": False,
            "interior_to_cd": False,
            "segment_pairs": [],
        }
    interior_ab = any(point_is_interior(point, a, b, epsilon) for point in contacts)
    interior_cd = any(point_is_interior(point, c, d, epsilon) for point in contacts)
    if len(contacts) == 2:
        relation = "overlap"
    elif interior_ab or interior_cd:
        relation = "crossing"
    else:
        relation = "touch"
    return {
        "relation": relation,
        "points": [_snap_point(point, epsilon) for point in contacts],
        "interior_to_ab": interior_ab,
        "interior_to_cd": interior_cd,
        "segment_pairs": [],
    }


# Grade (passage level) decisions for network construction.  A grade value is
# only "known" when it is a non-blank scalar attribute; null, missing, object
# or list values cannot be guessed into a traversable connection.

def grade_value(value: Any) -> Optional[str]:
    """Normalise a declared passage-level attribute into a comparable token."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def grades_known(grade_a: Optional[str], grade_b: Optional[str]) -> bool:
    return grade_a is not None and grade_b is not None


def grades_equal(grade_a: Optional[str], grade_b: Optional[str]) -> bool:
    return grade_a is not None and grade_a == grade_b


def grade_connection_decision(
    grade_a: Optional[str],
    grade_b: Optional[str],
) -> dict[str, Any]:
    """Decide whether a real contact is traversable from grade attributes.

    * ``connected``           -- both grades declared and equal;
    * ``grade_separated``     -- both declared but different (bridge/tunnel);
    * ``grade_undecidable``   -- at least one side lacks a usable grade.

    The decision never infers connectivity from missing attributes.
    """
    if not grades_known(grade_a, grade_b):
        return {
            "decision": "grade_undecidable",
            "connected": False,
            "grade_a": grade_a,
            "grade_b": grade_b,
            "reason": "missing_or_blank_grade_attribute",
        }
    if grades_equal(grade_a, grade_b):
        return {
            "decision": "same_grade",
            "connected": True,
            "grade_a": grade_a,
            "grade_b": grade_b,
            "reason": "equal_declared_grade",
        }
    return {
        "decision": "grade_separated",
        "connected": False,
        "grade_a": grade_a,
        "grade_b": grade_b,
        "reason": "different_declared_grade",
    }


def cluster_touching_points(
    points: Sequence[Point], epsilon: float
) -> list[list[int]]:
    """Group point indices whose coordinates coincide within ``epsilon``.

    Only points at true coordinate distance ``<= epsilon`` are merged; the
    function is transitive (A~B and B~C puts A, B, C in one cluster) and
    returns clusters with their member indices in ascending order, themselves
    ordered by their smallest member index.  Bounding-box proximity alone is
    never enough to merge points.
    """
    parent = list(range(len(points)))

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            if points_close(points[left], points[right], epsilon):
                union(left, right)
    groups: dict[int, list[int]] = {}
    for index in range(len(points)):
        groups.setdefault(find(index), []).append(index)
    return [groups[key] for key in sorted(groups)]
