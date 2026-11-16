"""Polygon geometry, overlap predicates, and geometry component views."""
from __future__ import annotations
from typing import Any, Iterable, Optional, Sequence
from .geometry_core import (
    Point,
    consecutive_duplicates,
    cross,
    distance,
    format_point,
    is_finite_number,
    is_finite_point,
    iter_numeric_points,
    line_self_intersections,
    orientation,
    point_on_segment,
    points_close,
    ring_closed,
    segments_cross_properly,
    segments_intersect,
    zero_length_segments,
)
Ring = tuple[Point, ...]
Polygon = tuple[Ring, ...]

def valid_ring(ring: Sequence[Point], epsilon: float) -> bool:
    return len(ring) >= 4 and ring_closed(ring, epsilon)

def valid_polygon(polygon: Sequence[Sequence[Point]], epsilon: float) -> bool:
    return (
        bool(polygon)
        and all(valid_ring(ring, epsilon) for ring in polygon)
        and abs(ring_signed_area(polygon[0])) > epsilon * epsilon
    )

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

def ring_signed_area(ring: Sequence[Point]) -> float:
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        total += float(x1) * float(y2) - float(x2) * float(y1)
    return total / 2.0

def ring_perimeter(ring: Sequence[Point]) -> float:
    if len(ring) < 2:
        return 0.0
    return sum(
        distance(ring[index], ring[(index + 1) % len(ring)])
        for index in range(len(ring))
    )

def polygon_area(polygon: Sequence[Sequence[Point]]) -> float:
    if not polygon:
        return 0.0
    area = abs(ring_signed_area(polygon[0]))
    area -= sum(abs(ring_signed_area(hole)) for hole in polygon[1:])
    return max(0.0, area)

def polygon_perimeter(polygon: Sequence[Sequence[Point]]) -> float:
    return sum(ring_perimeter(ring) for ring in polygon)

def area_perimeter_ratio(area: float, perimeter: float) -> float:
    if perimeter <= 0:
        return 0.0
    return area / perimeter

def bbox_of_points(points: Iterable[Point]) -> Optional[tuple[float, float, float, float]]:
    values = list(points)
    if not values:
        return None
    xs = [float(point[0]) for point in values]
    ys = [float(point[1]) for point in values]
    return (min(xs), min(ys), max(xs), max(ys))

def bboxes_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

def bbox_intersection(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> Optional[list[float]]:
    if not bboxes_overlap(a, b):
        return None
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]

def polygon_bbox(polygon: Sequence[Sequence[Point]]) -> Optional[tuple[float, float, float, float]]:
    return bbox_of_points(point for ring in polygon for point in ring)

def polygon_centroid(polygon: Sequence[Sequence[Point]]) -> Optional[Point]:
    if not polygon:
        return None
    ring = polygon[0]
    signed = ring_signed_area(ring)
    if abs(signed) <= 1e-15:
        return None
    cx = 0.0
    cy = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        factor = float(x1) * float(y2) - float(x2) * float(y1)
        cx += (float(x1) + float(x2)) * factor
        cy += (float(y1) + float(y2)) * factor
    return (cx / (6.0 * signed), cy / (6.0 * signed))

def polygons_overlap(a: Polygon, b: Polygon, epsilon: float = 0.0) -> bool:
    """Conservative, deterministic interior-overlap predicate.

    Bounding boxes prefilter; proper boundary crossings and strict vertex
    containment then establish overlap.  Shared edges alone do not.
    """
    if not valid_polygon(a, epsilon) or not valid_polygon(b, epsilon):
        return False
    box_a = polygon_bbox(a)
    box_b = polygon_bbox(b)
    if box_a is None or box_b is None or not bboxes_overlap(box_a, box_b):
        return False
    ring_a, ring_b = a[0], b[0]
    for i in range(len(ring_a) - 1):
        for j in range(len(ring_b) - 1):
            if segments_cross_properly(ring_a[i], ring_a[i + 1], ring_b[j], ring_b[j + 1], epsilon):
                return True
    for point in ring_a[:-1]:
        if point_in_polygon(b, point, epsilon) == "inside":
            return True
    for point in ring_b[:-1]:
        if point_in_polygon(a, point, epsilon) == "inside":
            return True
    centroid_a = polygon_centroid(a)
    if centroid_a is not None:
        if (
            point_in_polygon(a, centroid_a, epsilon) == "inside"
            and point_in_polygon(b, centroid_a, epsilon) == "inside"
        ):
            return True
    return False

def line_components(geometry: Any) -> list[tuple[str, tuple[Point, ...]]]:
    if geometry.kind == "LineString":
        return [("parts[0]", tuple(geometry.coordinates))]
    if geometry.kind == "MultiLineString":
        return [
            (f"parts[{index}]", tuple(part))
            for index, part in enumerate(geometry.coordinates)
        ]
    return []

def polygon_components(geometry: Any) -> list[tuple[str, Polygon]]:
    if geometry.kind == "Polygon":
        return [("polygons[0]", tuple(tuple(ring) for ring in geometry.coordinates))]
    if geometry.kind == "MultiPolygon":
        return [
            (
                f"polygons[{index}]",
                tuple(tuple(ring) for ring in polygon),
            )
            for index, polygon in enumerate(geometry.coordinates)
        ]
    return []

# --- Orthogonal coverage support -------------------------------------------------
#
# These helpers are used only by the explicitly opted-in coverage audit.  They
# validate and decompose rings but never mutate source coordinates.

def coverage_ring_issue(ring: Sequence[Point], epsilon: float) -> Optional[str]:
    """Return a stable reason code when a ring is not a clean axis-aligned loop."""
    if len(ring) < 4:
        return "ring_too_few_points"
    if not all(is_finite_point(point) for point in ring):
        return "non_finite_coordinate"
    if not ring_closed(ring, epsilon):
        return "ring_not_closed"
    body = list(ring[:-1])
    for index in range(len(body)):
        a, b = body[index], body[(index + 1) % len(body)]
        if points_close(a, b, epsilon):
            return "zero_length_segment"
        if abs(float(a[0]) - float(b[0])) > epsilon and abs(float(a[1]) - float(b[1])) > epsilon:
            return "non_axis_aligned_segment"
    if line_self_intersections(ring, True, epsilon):
        return "self_intersection"
    if abs(ring_signed_area(ring)) <= epsilon * epsilon:
        return "zero_area_ring"
    return None

def _rings_intersect(left: Sequence[Point], right: Sequence[Point], epsilon: float) -> bool:
    return any(
        segments_intersect(left[i], left[i + 1], right[j], right[j + 1], epsilon)
        for i in range(len(left) - 1)
        for j in range(len(right) - 1)
    )

def coverage_polygon_issue(polygon: Sequence[Sequence[Point]], epsilon: float) -> Optional[str]:
    """Validate a polygon as a clean orthogonal shell plus disjoint holes.

    Returns ``None`` when supported, or a stable reason code.  Self-intersection,
    touching or nested rings, holes outside the shell and non-finite coordinates
    are rejected rather than silently repaired.
    """
    if not polygon:
        return "empty_polygon"
    for ring in polygon:
        issue = coverage_ring_issue(ring, epsilon)
        if issue is not None:
            return issue
    shell = polygon[0]
    for hole_index, hole in enumerate(polygon[1:], start=1):
        if _rings_intersect(shell, hole, epsilon):
            return "hole_touches_or_crosses_shell"
        if any(point_location(shell, point, epsilon) != "inside" for point in hole[:-1]):
            return "hole_outside_shell"
        for other in polygon[hole_index + 1:]:
            if _rings_intersect(hole, other, epsilon):
                return "nested_or_overlapping_holes"
            if any(point_location(other, point, epsilon) != "outside" for point in hole[:-1]):
                return "nested_or_overlapping_holes"
            if any(point_location(hole, point, epsilon) != "outside" for point in other[:-1]):
                return "nested_or_overlapping_holes"
    return None

def ring_axis_edges(ring: Sequence[Point], epsilon: float) -> list[tuple[float, float, float, float]]:
    """Split an orthogonal closed ring into open axis-parallel strips.

    Each strip is ``(min_x, min_y, max_x, max_y)`` with one zero-sized dimension.
    Orientation is ignored and the closing point is never emitted twice.
    """
    edges: list[tuple[float, float, float, float]] = []
    body = list(ring[:-1]) if ring and points_close(ring[0], ring[-1], epsilon) else list(ring)
    for index in range(len(body)):
        a, b = body[index], body[(index + 1) % len(body)]
        if points_close(a, b, epsilon):
            continue
        x1, y1, x2, y2 = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        edges.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return edges

def polygon_boundary_edges(polygon: Sequence[Sequence[Point]], epsilon: float) -> list[tuple[float, float, float, float]]:
    return [edge for ring in polygon for edge in ring_axis_edges(ring, epsilon)]

def edge_lies_on_ring(edge: tuple[float, float, float, float], ring: Sequence[Point], epsilon: float) -> bool:
    """True when a positive-length axis-aligned strip is contained in a ring edge."""
    x0, y0, x1, y1 = edge
    for sx0, sy0, sx1, sy1 in ring_axis_edges(ring, epsilon):
        if (y1 - y0) <= epsilon and (sy1 - sy0) <= epsilon:
            if abs(y0 - sy0) <= epsilon and x0 >= sx0 - epsilon and x1 <= sx1 + epsilon:
                return True
        elif (x1 - x0) <= epsilon and (sx1 - sx0) <= epsilon:
            if abs(x0 - sx0) <= epsilon and y0 >= sy0 - epsilon and y1 <= sy1 + epsilon:
                return True
    return False

def coverage_geometry_issue(geometry: Any, epsilon: float) -> Optional[str]:
    """Validate every polygon part of a Polygon/MultiPolygon geometry."""
    parts = polygon_components(geometry)
    if not parts:
        return "not_a_polygon"
    for _, polygon in parts:
        issue = coverage_polygon_issue(polygon, epsilon)
        if issue is not None:
            return issue
    return None
