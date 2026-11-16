"""Deterministic, layer-scoped index of every auditable line endpoint.

Records are ordered by ``(source_index, part label, side)`` so that every
derived structure (near pairs, exact-connection groups) is independent of
dictionary insertion order and stable across input permutations.  The index
is shared by the topology rules and the read-only repair rehearsal.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

from .geometry import distance, is_finite_point, line_components, points_close
from .model import EndpointRef, Feature, LayerConfig, Tolerance


@dataclass(frozen=True)
class EndpointRecord:
    """One auditable line endpoint with its stable business identity."""

    ref: EndpointRef
    source_index: int
    point: tuple[float, float]

    def legacy_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.ref.feature_id,
            "source_index": self.source_index,
            "part": self.ref.part_index,
            "side": self.ref.side,
            "point": self.point,
        }


class EndpointIndex:
    """Read-only endpoint records plus cached near/exact connection views."""

    def __init__(
        self, records: tuple[EndpointRecord, ...], tolerance: Tolerance
    ) -> None:
        self._records = records
        self._tolerance = tolerance
        self._by_key: dict[tuple[str, str, str], int] = {
            record.ref.key(): index for index, record in enumerate(records)
        }
        self._exact_groups: Optional[tuple[frozenset[int], ...]] = None
        self._near_pairs: Optional[list[tuple[int, int, float]]] = None

    @property
    def records(self) -> tuple[EndpointRecord, ...]:
        return self._records

    @property
    def tolerance(self) -> Tolerance:
        return self._tolerance

    def __len__(self) -> int:
        return len(self._records)

    def record_at(self, index: int) -> EndpointRecord:
        return self._records[index]

    def point_at(self, index: int) -> tuple[float, float]:
        return self._records[index].point

    def resolve(self, ref: EndpointRef) -> int:
        try:
            return self._by_key[ref.key()]
        except KeyError:
            raise KeyError(
                f"端点不存在或不是线端点: {ref.feature_id}/{ref.part_index}/{ref.side}"
            ) from None

    def gap(self, left: int, right: int) -> float:
        return distance(self._records[left].point, self._records[right].point)

    def are_exact_connected(self, left: int, right: int) -> bool:
        if left == right:
            return True
        return self.gap(left, right) <= self._tolerance.coordinate_epsilon

    def near_pairs(self) -> list[tuple[int, int, float]]:
        """Endpoint pairs with epsilon < distance <= near_miss_distance."""
        if self._near_pairs is not None:
            return list(self._near_pairs)
        pairs: list[tuple[int, int, float]] = []
        tolerance = self._tolerance
        for left in range(len(self._records)):
            first = self._records[left]
            for right in range(left + 1, len(self._records)):
                second = self._records[right]
                if first.source_index == second.source_index:
                    continue
                gap = self.gap(left, right)
                if tolerance.coordinate_epsilon < gap <= tolerance.near_miss_distance:
                    pairs.append((left, right, gap))
        self._near_pairs = pairs
        return list(pairs)

    def exact_connection_groups(self) -> tuple[frozenset[int], ...]:
        """Union-find groups of endpoints already within coordinate_epsilon."""
        if self._exact_groups is not None:
            return self._exact_groups
        parent = list(range(len(self._records)))

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        tolerance = self._tolerance
        for left in range(len(self._records)):
            first = self._records[left]
            for right in range(left + 1, len(self._records)):
                second = self._records[right]
                if first.source_index == second.source_index:
                    continue
                if self.gap(left, right) <= tolerance.coordinate_epsilon:
                    root_left, root_right = find(left), find(right)
                    if root_left != root_right:
                        parent[max(root_left, root_right)] = min(root_left, root_right)
        grouped: dict[int, list[int]] = {}
        for index in range(len(self._records)):
            grouped.setdefault(find(index), []).append(index)
        self._exact_groups = tuple(
            frozenset(members) for members in sorted(grouped.values(), key=min)
        )
        return self._exact_groups

    def group_of(self, index: int) -> frozenset[int]:
        for group in self.exact_connection_groups():
            if index in group:
                return group
        return frozenset({index})


def build_endpoint_index(
    features: tuple[Feature, ...], layer: LayerConfig, tolerance: Tolerance
) -> EndpointIndex:
    """Build the deterministic endpoint index used by topology checks."""
    records: list[EndpointRecord] = []
    for feature in sorted(features, key=lambda item: item.source_index):
        if feature.geometry.kind not in layer.geometry_types:
            continue
        for label, line in line_components(feature.geometry):
            if len(line) < 2 or not all(is_finite_point(point) for point in line):
                continue
            if points_close(line[0], line[-1], tolerance.coordinate_epsilon):
                continue
            for side, point in (("start", line[0]), ("end", line[-1])):
                records.append(
                    EndpointRecord(
                        ref=EndpointRef(
                            feature_id=feature.feature_id,
                            part_index=label,
                            side=side,
                        ),
                        source_index=feature.source_index,
                        point=(float(point[0]), float(point[1])),
                    )
                )
    return EndpointIndex(tuple(records), tolerance)
