"""Deterministic geometry normalization for duplicate detection.

Normalization is intentionally evidence-preserving: source coordinates in
``Feature`` objects are never changed.  The functions here only build a
canonical integer-grid signature for comparisons.
"""
from __future__ import annotations
import math
from typing import Any, Optional, Sequence
from .geometry import is_finite_point
from .model import Geometry
CanonicalPoint = tuple[int, int]
CanonicalLine = tuple[CanonicalPoint, ...]
CanonicalPolygon = tuple[CanonicalLine, ...]

def quantize_point(point: Sequence[Any], epsilon: float) -> CanonicalPoint:
    if not is_finite_point(point):
        raise ValueError("point is not finite")
    return (
        int(round(float(point[0]) / epsilon)),
        int(round(float(point[1]) / epsilon)),
    )

def _strip_consecutive(points: Sequence[CanonicalPoint]) -> tuple[CanonicalPoint, ...]:
    result: list[CanonicalPoint] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    return tuple(result)

def _rotate_to_min(points: tuple[CanonicalPoint, ...]) -> tuple[CanonicalPoint, ...]:
    if not points:
        return points
    minimum = min(range(len(points)), key=lambda index: points[index])
    return points[minimum:] + points[:minimum]

def _canonical_sequence(
    points: Sequence[Sequence[Any]], epsilon: float, ring_mode: bool
) -> tuple[CanonicalPoint, ...]:
    quantized = _strip_consecutive([quantize_point(point, epsilon) for point in points])
    if not quantized:
        return ()
    closed = len(quantized) >= 2 and quantized[0] == quantized[-1]
    body = list(quantized)
    if closed:
        body = body[:-1]
    if ring_mode and closed:
        body_tuple = _rotate_to_min(tuple(body))
        reversed_tuple = _rotate_to_min(tuple(reversed(body_tuple)))
        body_tuple = min(body_tuple, reversed_tuple)
        return body_tuple + (body_tuple[0],) if body_tuple else ()
    if not ring_mode:
        forward = tuple(body)
        backward = tuple(reversed(body))
        return min(forward, backward)
    return tuple(body)

def canonical_line(points: Sequence[Sequence[Any]], epsilon: float) -> CanonicalLine:
    return _canonical_sequence(points, epsilon, ring_mode=False)

def canonical_ring(ring: Sequence[Sequence[Any]], epsilon: float) -> CanonicalLine:
    return _canonical_sequence(ring, epsilon, ring_mode=True)

def canonical_polygon(
    polygon: Sequence[Sequence[Sequence[Any]]], epsilon: float
) -> Optional[CanonicalPolygon]:
    if not polygon:
        return None
    rings = [canonical_ring(ring, epsilon) for ring in polygon]
    outer = rings[0]
    holes = tuple(sorted(rings[1:]))
    return (outer,) + holes

def _canonical_raw(kind: str, coordinates: Any, epsilon: float) -> Any:
    if kind == "Point":
        return quantize_point(coordinates, epsilon)
    if kind == "LineString":
        return canonical_line(coordinates, epsilon)
    if kind == "MultiLineString":
        return tuple(sorted(canonical_line(line, epsilon) for line in coordinates))
    if kind == "Polygon":
        polygon = canonical_polygon(coordinates, epsilon)
        return polygon
    if kind == "MultiPolygon":
        polygons = [
            canonical_polygon(polygon, epsilon)
            for polygon in coordinates
        ]
        if any(value is None for value in polygons):
            return None
        return tuple(sorted(polygons))
    return None

def canonical_geometry(geometry: Geometry, epsilon: float) -> Optional[dict[str, Any]]:
    """Return a stable, JSON-serializable signature or ``None`` if unsupported."""
    if not epsilon > 0:
        raise ValueError("epsilon must be positive")
    try:
        canonical = _canonical_raw(geometry.kind, geometry.coordinates, epsilon)
    except (TypeError, ValueError, OverflowError):
        return None
    if canonical is None:
        return None
    return {"kind": geometry.kind, "coordinates": canonical}

def finite_bbox(
    coordinates: Any,
) -> Optional[tuple[float, float, float, float]]:
    """Compute a bbox from nested coordinate arrays, ignoring non-finite values."""
    xs: list[float] = []
    ys: list[float] = []
    def visit(value: Any) -> None:
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        ):
            x, y = float(value[0]), float(value[1])
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x)
                ys.append(y)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(coordinates)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
