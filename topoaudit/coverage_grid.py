"""Orthogonal grid algebra for the opt-in coverage audit.

Everything here is pure and deterministic: inputs are validated orthogonal
polygons, outputs are fresh axis-aligned rectangles.  No source coordinates are
ever modified.
"""
from __future__ import annotations
from typing import Optional, Sequence
from .geometry import (
    edge_lies_on_ring,
    point_in_polygon,
    polygon_bbox,
    polygon_boundary_edges,
    ring_axis_edges,
)
from .util import stable_round
Point = tuple[float, float]
Polygon = tuple[tuple[Point, ...], ...]
Box = tuple[float, float, float, float]
Edge = tuple[float, float, float, float]

def difference_pieces(
    target: Sequence[Polygon], clip: Sequence[Polygon], epsilon: float
) -> list[Polygon]:
    """Exact ``union(target) - union(clip)`` on the orthogonal arrangement grid.

    The grid is the Cartesian product of every x/y coordinate that appears in
    either the AOI or parcel boundary.  Parcel-only coordinates are needed so a
    partial parcel edge cannot silently pass through a cell interior.  Each cell is kept only when its open interior lies inside
    the target union, outside the clip union, and no input boundary passes
    through that interior.  Consequently:

    * shared parcel edges, shared corners and zero-width line gaps yield no
      positive-area cell;
    * clip polygons outside the AOI are harmless;
    * the clip is a true union, so overlaps never double-count and never offset
      a gap elsewhere.
    """
    target_edges = [edge for polygon in target for edge in polygon_boundary_edges(polygon, epsilon)]
    clip_edges = [edge for polygon in clip for edge in polygon_boundary_edges(polygon, epsilon)]
    xs = _breaks(_horizontal(target_edges, epsilon) + _horizontal(clip_edges, epsilon))
    ys = _breaks(_vertical(target_edges, epsilon) + _vertical(clip_edges, epsilon))
    if len(xs) < 2 or len(ys) < 2:
        return []
    pieces: list[Polygon] = []
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 <= epsilon:
            continue
        for x0, x1 in zip(xs, xs[1:]):
            if x1 - x0 <= epsilon:
                continue
            center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            if not any(point_in_polygon(polygon, center, epsilon) == "inside" for polygon in target):
                continue
            if any(point_in_polygon(polygon, center, epsilon) == "inside" for polygon in clip):
                continue
            pieces.append((((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)),))
    return pieces

def _vertical(edges: Sequence[Edge], epsilon: float) -> list[tuple[float, float, float]]:
    return [
        (x, y0, y1)
        for x, y0, x1, y1 in edges
        if x1 - x <= epsilon and y1 - y0 > epsilon
    ]

def _horizontal(edges: Sequence[Edge], epsilon: float) -> list[tuple[float, float, float]]:
    return [
        (y, x0, x1)
        for x0, y, x1, y1 in edges
        if y1 - y <= epsilon and x1 - x0 > epsilon
    ]

def _breaks(edges: Sequence[tuple[float, float, float]]) -> list[float]:
    values: set[float] = set()
    for _, start, end in edges:
        values.add(float(start))
        values.add(float(end))
    return sorted(values)

def components(pieces: Sequence[Polygon], epsilon: float) -> list[list[Polygon]]:
    """Group rectangles into edge-connected components in a stable order.

    Only a shared positive-length edge (or positive-area overlap) joins cells.
    Diagonally touching cells remain separate components: a point contact has
    no area and must not silently enlarge one remediation location.  Ordering is
    by component bbox, so the result is independent of parcel input order.
    """
    if not pieces:
        return []
    boxes = [polygon_bbox(piece) for piece in pieces]
    parent = list(range(len(pieces)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(pieces)):
        for right in range(left + 1, len(pieces)):
            a, b = boxes[left], boxes[right]
            if a is None or b is None:
                continue
            if _disjoint(a, b, epsilon) or not _touches(a, b, epsilon):
                continue
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[max(root_left, root_right)] = min(root_left, root_right)
    groups: dict[int, list[Polygon]] = {}
    for index, piece in enumerate(pieces):
        groups.setdefault(find(index), []).append(piece)
    ordered = [groups[key] for key in sorted(groups)]
    return sorted(ordered, key=lambda group: (group_bbox(group) or (0, 0, 0, 0)))

def _disjoint(a: Box, b: Box, epsilon: float) -> bool:
    return (
        a[2] < b[0] - epsilon
        or b[2] < a[0] - epsilon
        or a[3] < b[1] - epsilon
        or b[3] < a[1] - epsilon
    )

def _touches(a: Box, b: Box, epsilon: float) -> bool:
    """True for positive-area overlap or a shared positive-length edge.

    A shared corner alone is a measure-zero contact and does not join cells.
    """
    overlap_x = min(a[2], b[2]) - max(a[0], b[0])
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    if overlap_x > epsilon and overlap_y > epsilon:
        return True
    if abs(max(a[0], b[0]) - min(a[2], b[2])) <= epsilon and overlap_y > epsilon:
        return True
    if abs(max(a[1], b[1]) - min(a[3], b[3])) <= epsilon and overlap_x > epsilon:
        return True
    return False

def group_bbox(group: Sequence[Polygon]) -> Optional[Box]:
    box: Optional[Box] = None
    for piece in group:
        current = polygon_bbox(piece)
        if current is None:
            continue
        box = current if box is None else (
            min(box[0], current[0]),
            min(box[1], current[1]),
            max(box[2], current[2]),
            max(box[3], current[3]),
        )
    return box

def boundary_contact(
    group: Sequence[Polygon], target: Sequence[Polygon], epsilon: float
) -> bool:
    """True when a group edge lies on an AOI exterior (shell) boundary.

    AOI holes are intentionally excluded: a gap adjacent to an AOI hole is still
    an internal hole for acceptance purposes.  Parcel seams are excluded too,
    because only the AOI shell defines the outer boundary.
    """
    shells = [polygon[0] for polygon in target]
    for piece in group:
        for edge in ring_axis_edges(piece[0], epsilon):
            for shell in shells:
                if edge_lies_on_ring(edge, shell, epsilon):
                    return True
    return False

def cells_document(group: Sequence[Polygon]) -> list[dict[str, float]]:
    boxes = sorted(
        (box for box in (polygon_bbox(piece) for piece in group) if box is not None),
        key=lambda box: (box[1], box[0], box[3], box[2]),
    )
    return [
        {
            "bbox": [stable_round(value) for value in box],
            "area": stable_round(max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])),
        }
        for box in boxes
    ]
