"""Polygon geometry, overlap predicates, and geometry component views."""
from __future__ import annotations
import math
from dataclasses import dataclass
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

@dataclass(frozen=True)
class ClearanceArrangement:
    """Labelled elements of the exact half-width erosion arrangement.

    Grid lines are every ring edge coordinate plus every coordinate shifted
    by the erosion half-width.  On such a grid the distance from any point to
    an axis-aligned boundary is, inside each cell, the minimum distance of
    the cell's four vertices to the boundary segments, so a cell is entirely
    clear of the boundary by ``half_width`` exactly when all four of its
    vertices are.  Positive-area components are unions of clear cells; a
    corridor whose surviving width is exactly the clearance width has no
    interior cells but keeps a clear centre-line grid edge, which still joins
    the cells on its two sides.  Point-only contact never unites components.
    """
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    clear_cells: set[tuple[int, int]]
    clear_h_edges: set[tuple[int, int]]
    clear_v_edges: set[tuple[int, int]]

    def connected_components(self) -> list["ClearanceComponent"]:
        """Build positive-area components of the eroded region.

        Cell nodes carry area; clear grid-edge nodes carry none and exist to
        represent centre-line passages whose surviving width is exactly the
        clearance width.  Edges only union when they share a clear cell (the
        ordinary two-dimensional case) or when two collinear edges share an
        endpoint (an exactly-fitting straight corridor).  Perpendicular edges
        that merely meet at a grid vertex never union, preserving the rule
        that point-only contact cannot connect positive-area components.
        """
        cell_node = lambda cell: ("c", cell)
        h_node = lambda edge: ("h", edge)
        v_node = lambda edge: ("v", edge)
        parent: dict[Any, Any] = {}

        def find(node: Any) -> Any:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: Any, b: Any) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[max(root_a, root_b)] = min(root_a, root_b)

        for cell in self.clear_cells:
            find(cell_node(cell))
        # Ordinary side-adjacent cells share a two-dimensional region.
        for i, j in self.clear_cells:
            if (i + 1, j) in self.clear_cells:
                union(cell_node((i, j)), cell_node((i + 1, j)))
            if (i, j + 1) in self.clear_cells:
                union(cell_node((i, j)), cell_node((i, j + 1)))
        # Centre-line edges only represent the exactly-fitting corridor case.
        # A horizontal edge h(i, j) at y=ys[j] separates cell (i, j-1) below
        # from cell (i, j) above; it is a connector when neither side is a
        # clear cell (surviving width there is exactly 2*half_width, no open
        # interior).  An edge merely bordering a clear cell is that cell's
        # boundary and never bridges a narrower passage.
        for edge in self.clear_h_edges:
            i, j = edge
            below = (i, j - 1)
            above = (i, j)
            if below not in self.clear_cells and above not in self.clear_cells:
                find(h_node(edge))
        # A vertical edge v(i, j) at x=xs[i] separates cell (i-1, j) left
        # from cell (i, j) right.
        for edge in self.clear_v_edges:
            i, j = edge
            left = (i - 1, j)
            right = (i, j)
            if left not in self.clear_cells and right not in self.clear_cells:
                find(v_node(edge))
        # A connector edge attaches to a clear cell whenever it borders one:
        # its two own-side cells plus the four cells touching each endpoint,
        # which captures how a corridor mouth opens back into a chamber.
        for edge in self.clear_h_edges:
            i, j = edge
            node = h_node(edge)
            if node not in parent:
                continue
            for cell in (
                (i, j - 1), (i, j),
                (i - 1, j - 1), (i - 1, j),
                (i + 1, j - 1), (i + 1, j),
            ):
                if cell in self.clear_cells:
                    union(node, cell_node(cell))
        for edge in self.clear_v_edges:
            i, j = edge
            node = v_node(edge)
            if node not in parent:
                continue
            for cell in (
                (i - 1, j), (i, j),
                (i - 1, j - 1), (i, j - 1),
                (i - 1, j + 1), (i, j + 1),
            ):
                if cell in self.clear_cells:
                    union(node, cell_node(cell))
        # Collinear connector edges sharing an endpoint continue an
        # exactly-fitting straight centre-line passage.  Perpendicular edges
        # that only meet at a grid vertex never union.
        for edge in self.clear_h_edges:
            if h_node(edge) not in parent:
                continue
            i, j = edge
            for next_edge in ((i + 1, j), (i - 1, j)):
                if h_node(next_edge) in parent:
                    union(h_node(edge), h_node(next_edge))
        for edge in self.clear_v_edges:
            if v_node(edge) not in parent:
                continue
            i, j = edge
            for next_edge in ((i, j + 1), (i, j - 1)):
                if v_node(next_edge) in parent:
                    union(v_node(edge), v_node(next_edge))
        groups: dict[Any, list[tuple[int, int]]] = {}
        for cell in sorted(self.clear_cells):
            groups.setdefault(find(cell_node(cell)), []).append(cell)
        components: list[ClearanceComponent] = []
        for root in sorted(groups):
            cells = tuple(sorted(groups[root]))
            area = sum(
                (self.xs[i + 1] - self.xs[i]) * (self.ys[j + 1] - self.ys[j])
                for i, j in cells
            )
            indices_x = {i for i, _ in cells}
            indices_y = {j for _, j in cells}
            bbox = (
                min(self.xs[i] for i in indices_x),
                min(self.ys[j] for j in indices_y),
                max(self.xs[i + 1] for i in indices_x),
                max(self.ys[j + 1] for j in indices_y),
            )
            components.append(
                ClearanceComponent(cells=cells, area=area, bbox=bbox)
            )
        components.sort(key=lambda component: (-component.area, component.bbox))
        return components

@dataclass(frozen=True)
class ClearanceComponent:
    cells: tuple[tuple[int, int], ...]
    area: float
    bbox: tuple[float, float, float, float]

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


# ---------------------------------------------------------------------------
# Orthogonal clearance primitives
#
# The clearance audit works only on axis-aligned, provably valid 2D polygons.
# Erosion by half-width h is represented exactly (no raster sampling) on an
# arrangement built from all ring edge coordinates: a cell is "clear" only
# when the whole open cell is farther than h from every boundary segment, and
# grid edges/vertices are tested explicitly so that a corridor of width
# exactly 2h (a zero-width surviving centre line) is still recognised as a
# pass.  Point-only contact never unions two positive-area components.
# ---------------------------------------------------------------------------

def ring_is_axis_aligned(ring: Sequence[Point], epsilon: float) -> bool:
    """True when every non-degenerate edge is parallel to an axis."""
    if len(ring) < 2:
        return False
    for index in range(len(ring) - 1):
        a = ring[index]
        b = ring[index + 1]
        if points_close(a, b, epsilon):
            return False
        horizontal = abs(float(a[1]) - float(b[1])) <= epsilon
        vertical = abs(float(a[0]) - float(b[0])) <= epsilon
        if not (horizontal or vertical):
            return False
    return True


def polygon_is_axis_aligned(
    polygon: Sequence[Sequence[Point]], epsilon: float
) -> bool:
    return bool(polygon) and all(
        ring_is_axis_aligned(ring, epsilon) for ring in polygon
    )


def provably_valid_orthogonal_polygon(
    polygon: Sequence[Sequence[Point]], epsilon: float
) -> tuple[bool, str]:
    """Conservative validity gate for the exact clearance audit.

    Returns ``(True, "")`` only when the polygon can be proven to be a valid
    axis-aligned polygon with non-self-touching outer ring and holes strictly
    contained without mutual contact.  Any geometry the gate cannot prove is
    rejected with a stable reason; callers must then report ``unknown``.
    """
    if not valid_polygon(polygon, epsilon):
        return False, "invalid_polygon_structure"
    if not polygon_is_axis_aligned(polygon, epsilon):
        return False, "non_axis_aligned_edge"
    outer = polygon[0]
    if not _ring_has_unique_vertices(outer, epsilon):
        return False, "ring_self_touch_or_repeated_vertex"
    for hole in polygon[1:]:
        if not _ring_has_unique_vertices(hole, epsilon):
            return False, "ring_self_touch_or_repeated_vertex"
        # Every hole vertex must be strictly inside the outer ring: boundary
        # contact would make the hole touch the exterior (point/edge pinch).
        for vertex in hole[:-1]:
            if point_location(outer, vertex, epsilon) != "inside":
                return False, "hole_not_strictly_inside_outer"
    for left in range(1, len(polygon)):
        for right in range(left + 1, len(polygon)):
            if _rings_touch(polygon[left], polygon[right], epsilon):
                return False, "holes_touch_each_other"
    return True, ""


def _ring_has_unique_vertices(ring: Sequence[Point], epsilon: float) -> bool:
    body = ring[:-1]
    for index in range(len(body)):
        for other in range(index + 1, len(body)):
            if points_close(body[index], body[other], epsilon):
                return False
    return True


def _rings_touch(
    ring_a: Sequence[Point], ring_b: Sequence[Point], epsilon: float
) -> bool:
    for i in range(len(ring_a) - 1):
        a, b = ring_a[i], ring_a[i + 1]
        if points_close(a, b, epsilon):
            continue
        for j in range(len(ring_b) - 1):
            c, d = ring_b[j], ring_b[j + 1]
            if points_close(c, d, epsilon):
                continue
            if segments_intersect(a, b, c, d, epsilon):
                return True
    return False


def distance_point_axis_segment(point: Point, a: Point, b: Point) -> float:
    """Exact distance from a point to an axis-aligned segment."""
    px, py = float(point[0]), float(point[1])
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    if abs(ay - by) <= 0.0:
        clamped_x = min(max(px, min(ax, bx)), max(ax, bx))
        return math.hypot(px - clamped_x, py - ay)
    clamped_y = min(max(py, min(ay, by)), max(ay, by))
    return math.hypot(px - ax, py - clamped_y)


def _rectangle_segment_distance(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    a: Point,
    b: Point,
    epsilon: float,
) -> float:
    """Exact minimum Euclidean distance between a rectangle and a segment.

    Both shapes are axis aligned.  When the segment's coordinate range
    overlaps the rectangle along the segment's own axis, the minimum is the
    perpendicular gap; otherwise the nearest points are a rectangle corner
    and a segment endpoint.
    """
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    if abs(ay - by) <= epsilon:
        seg_min_x, seg_max_x = min(ax, bx), max(ax, bx)
        if seg_max_x >= min_x and seg_min_x <= max_x:
            return min(abs(ay - min_y), abs(ay - max_y))
        if seg_max_x < min_x:
            return math.hypot(min_x - seg_max_x, ay - min(max(ay, min_y), max_y))
        return math.hypot(seg_min_x - max_x, ay - min(max(ay, min_y), max_y))
    seg_min_y, seg_max_y = min(ay, by), max(ay, by)
    if seg_max_y >= min_y and seg_min_y <= max_y:
        return min(abs(ax - min_x), abs(ax - max_x))
    if seg_max_y < min_y:
        return math.hypot(ax - min(max(ax, min_x), max_x), min_y - seg_max_y)
    return math.hypot(ax - min(max(ax, min_x), max_x), seg_min_y - max_y)


def _h_edge_segment_distance(
    x0: float, x1: float, y: float, a: Point, b: Point, epsilon: float
) -> float:
    """Exact minimum distance from a horizontal edge interval to a segment."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    if abs(ay - by) <= epsilon:
        if max(ax, bx) >= x0 and min(ax, bx) <= x1:
            return abs(ay - y)
        nearest_x = max(ax, bx) if max(ax, bx) < x0 else min(ax, bx)
        edge_x = x0 if max(ax, bx) < x0 else x1
        return math.hypot(edge_x - nearest_x, ay - y)
    # Vertical segment: perpendicular gap when its y range covers y, else
    # the distance to its nearer endpoint.
    if max(ay, by) >= y and min(ay, by) <= y:
        return abs(ax - _clamp(ax, x0, x1))
    endpoint_y = max(ay, by) if max(ay, by) < y else min(ay, by)
    return math.hypot(ax - _clamp(ax, x0, x1), endpoint_y - y)


def _v_edge_segment_distance(
    y0: float, y1: float, x: float, a: Point, b: Point, epsilon: float
) -> float:
    """Exact minimum distance from a vertical edge interval to a segment."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    if abs(ax - bx) <= epsilon:
        if max(ay, by) >= y0 and min(ay, by) <= y1:
            return abs(ax - x)
        nearest_y = max(ay, by) if max(ay, by) < y0 else min(ay, by)
        edge_y = y0 if max(ay, by) < y0 else y1
        return math.hypot(ax - x, edge_y - nearest_y)
    if max(ax, bx) >= x and min(ax, bx) <= x:
        return abs(ay - _clamp(ay, y0, y1))
    endpoint_x = max(ax, bx) if max(ax, bx) < x else min(ax, bx)
    return math.hypot(endpoint_x - x, ay - _clamp(ay, y0, y1))


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _cell_has_strict_clear_point(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    segments: Sequence[tuple[Point, Point]],
    half_width: float,
    epsilon: float,
) -> bool:
    """Whether the rectangle's open interior contains a point whose distance
    to every axis-aligned boundary segment is at least ``half_width``.

    The arrangement contains every edge line and every line offset by
    ``half_width``, so within one cell each segment's distance constraint is
    constant except across the circular corner arcs.  The exact decision is:

    1. the rectangle's minimum distance to every segment is at least
       ``half_width``; and
    2. when the cell merely touches an offset boundary (its centre is not
       strictly beyond ``half_width``), reject it unless the cell extends to
       the far side of that offset line.  This removes zero-thickness strips
       and thin side channels while preserving chamber interiors.

    Corridors of exactly twice the half-width produce no cells here and
    survive only as centre-line grid edges.
    """
    if (x1 - x0) <= epsilon or (y1 - y0) <= epsilon:
        return False
    centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    for a, b in segments:
        if _rectangle_segment_distance(x0, y0, x1, y1, a, b, epsilon) < half_width - epsilon:
            return False
        centre_distance = distance_point_axis_segment(centre, a, b)
        if centre_distance > half_width + epsilon:
            continue
        if abs(float(a[1]) - float(b[1])) <= epsilon:
            line_y = float(a[1])
            extent = max(abs(y1 - line_y), abs(y0 - line_y))
        else:
            line_x = float(a[0])
            extent = max(abs(x1 - line_x), abs(x0 - line_x))
        if extent <= half_width + epsilon:
            return False
    return True


def clearance_cells(
    polygon: Sequence[Sequence[Point]],
    half_width: float,
    epsilon: float,
) -> Optional["ClearanceArrangement"]:
    """Erode a provably valid axis-aligned polygon exactly (read-only).

    The arrangement grid contains every ring edge coordinate and every
    coordinate shifted by ``half_width``: every straight erosion boundary
    lands on a grid line, with corner arcs centred on grid vertices.  A cell
    contributes positive-area usable region only when an exact half-plane
    intersection proves it contains a point at least ``half_width`` from
    every boundary segment (see :func:`_cell_has_strict_clear_point`).
    Grid edges are tested separately so that a corridor of exactly twice the
    half-width keeps a traversable centre line even though it has no cells.
    No raster sampling is involved: every grid line derives from the face.
    """
    valid, _reason = provably_valid_orthogonal_polygon(polygon, epsilon)
    if not valid:
        return None
    raw_x = sorted({float(point[0]) for ring in polygon for point in ring})
    raw_y = sorted({float(point[1]) for ring in polygon for point in ring})
    xs = sorted(
        {value for base in raw_x for value in (base, base + half_width, base - half_width)}
    )
    ys = sorted(
        {value for base in raw_y for value in (base, base + half_width, base - half_width)}
    )
    segments: list[tuple[Point, Point]] = []
    for ring in polygon:
        for index in range(len(ring) - 1):
            segments.append((ring[index], ring[index + 1]))

    def point_edge_clear(point: Point) -> bool:
        """Non-strict point for centre-line grid edges (distance == h allowed)."""
        if point_in_polygon(polygon, point, epsilon) != "inside":
            return False
        return all(
            distance_point_axis_segment(point, a, b) >= half_width - epsilon
            for a, b in segments
        )

    def h_edge_clear(i: int, j: int) -> bool:
        """Whole open horizontal grid edge stays at least ``half_width`` away.

        Centre lines of corridors whose surviving width is exactly the
        clearance width sit at distance ``half_width`` and must remain
        traversable, so edges use a non-strict, tolerance-widened test.
        """
        if not point_edge_clear((xs[i], ys[j])) or not point_edge_clear((xs[i + 1], ys[j])):
            return False
        return all(
            _h_edge_segment_distance(xs[i], xs[i + 1], ys[j], a, b, epsilon)
            >= half_width - epsilon
            for a, b in segments
        )

    def v_edge_clear(i: int, j: int) -> bool:
        """Whole open vertical grid edge stays at least ``half_width`` away."""
        if not point_edge_clear((xs[i], ys[j])) or not point_edge_clear((xs[i], ys[j + 1])):
            return False
        return all(
            _v_edge_segment_distance(ys[j], ys[j + 1], xs[i], a, b, epsilon)
            >= half_width - epsilon
            for a, b in segments
        )

    clear_cells: set[tuple[int, int]] = set()
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            midpoint = ((xs[i] + xs[i + 1]) / 2.0, (ys[j] + ys[j + 1]) / 2.0)
            if point_in_polygon(polygon, midpoint, epsilon) != "inside":
                continue
            # Exact open-erosion test: the cell contributes positive-area
            # usable region only when it contains a point whose distance to
            # every boundary segment is at least half_width.  The half-plane
            # intersection is exact on this offset-line arrangement and
            # rejects thin channels and zero-thickness strips; an exactly-2h
            # corridor has no such cell and survives as centre-line edges.
            if _cell_has_strict_clear_point(
                xs[i], ys[j], xs[i + 1], ys[j + 1], segments, half_width, epsilon
            ):
                clear_cells.add((i, j))
    clear_h_edges: set[tuple[int, int]] = set()
    for i in range(len(xs) - 1):
        for j in range(len(ys)):
            if h_edge_clear(i, j):
                clear_h_edges.add((i, j))
    clear_v_edges: set[tuple[int, int]] = set()
    for i in range(len(xs)):
        for j in range(len(ys) - 1):
            if v_edge_clear(i, j):
                clear_v_edges.add((i, j))
    return ClearanceArrangement(
        xs=tuple(xs),
        ys=tuple(ys),
        clear_cells=clear_cells,
        clear_h_edges=clear_h_edges,
        clear_v_edges=clear_v_edges,
    )
