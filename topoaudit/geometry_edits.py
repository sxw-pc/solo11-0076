"""In-memory line endpoint edits used by the read-only repair rehearsal.

These helpers build new :class:`Geometry` / :class:`Feature` objects; the
loaded source objects are never mutated and nothing is written to disk.
"""
from __future__ import annotations
from typing import Any, Mapping

from .model import Feature, Geometry


def move_line_endpoints(
    geometry: Geometry, moves: Mapping[tuple[str, str], tuple[float, float]]
) -> Geometry:
    """Return an in-memory geometry copy with selected line endpoints moved.

    ``moves`` is keyed by ``(part label, side)``; the source geometry itself
    is never mutated.  Only ``LineString`` / ``MultiLineString`` endpoints can
    be moved, matching the scope of the endpoint topology rules.
    """
    if geometry.kind == "LineString":
        return Geometry(
            kind=geometry.kind,
            coordinates=_replace_endpoints(geometry.coordinates, "parts[0]", moves),
            raw_type=geometry.raw_type,
        )
    if geometry.kind == "MultiLineString":
        parts = tuple(
            _replace_endpoints(line, f"parts[{index}]", moves)
            for index, line in enumerate(geometry.coordinates)
        )
        return Geometry(
            kind=geometry.kind,
            coordinates=parts,
            raw_type=geometry.raw_type,
        )
    raise ValueError(f"端点移动仅支持线几何，收到: {geometry.kind}")


def _replace_endpoints(
    line: tuple[Any, ...],
    part_label: str,
    moves: Mapping[tuple[str, str], tuple[float, float]],
) -> tuple[Any, ...]:
    points = list(line)
    start_move = moves.get((part_label, "start"))
    end_move = moves.get((part_label, "end"))
    if start_move is not None:
        points[0] = (float(start_move[0]), float(start_move[1]))
    if end_move is not None:
        points[-1] = (float(end_move[0]), float(end_move[1]))
    return tuple(points)


def apply_endpoint_moves(
    features: tuple[Feature, ...],
    moves_by_feature: Mapping[
        tuple[str, int], Mapping[tuple[str, str], tuple[float, float]]
    ],
) -> tuple[Feature, ...]:
    """Apply grouped endpoint moves on an in-memory feature tuple.

    Features without edits are returned untouched; no source object is
    mutated and nothing is written to disk.
    """
    result: list[Feature] = []
    for feature in features:
        key = (feature.feature_id, feature.source_index)
        edits = moves_by_feature.get(key)
        if not edits:
            result.append(feature)
            continue
        result.append(
            Feature(
                layer_id=feature.layer_id,
                feature_id=feature.feature_id,
                geometry=move_line_endpoints(feature.geometry, edits),
                properties=feature.properties,
                source_index=feature.source_index,
            )
        )
    return tuple(result)
