"""Snap-anchor geometry and per-cluster option feasibility checks."""
from __future__ import annotations
import itertools
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .geometry import distance
from .model import EndpointRef, RepairConflict
from .repair_candidates import (
    DISPLACEMENT_LIMIT,
    NO_SNAP_POINT_WITHIN_LIMITS,
    POST_TARGET_NOT_CONNECTED,
    PROTECTED_ENDPOINT_PINNED,
    ClusterOption,
)
from .rule_topology import EndpointIndex
from .util import canonical_json, stable_round


def cluster_snap_options(
    cluster: frozenset[frozenset[int]],
    targets: Sequence[tuple[EndpointRef, EndpointRef]],
    index: EndpointIndex,
    record_limits: Mapping[int, float],
    pinned: frozenset[frozenset[int]],
) -> tuple[tuple[ClusterOption, ...], tuple[RepairConflict, ...]]:
    """Return feasible snap options for one cluster plus rejection records."""
    groups = sorted(cluster, key=lambda group: min(group))
    anchors: list[tuple[float, float]] = [
        index.point_at(min(group)) for group in groups
    ]
    if len(groups) > 1:
        anchors.append(
            centroid(index.point_at(min(group)) for group in groups)
        )
    target_edges = cluster_edges(cluster, targets, index, groups)
    options: dict[str, ClusterOption] = {}
    rejections: list[RepairConflict] = []
    for anchor in unique_points(anchors):
        option, reason = build_option(
            groups, anchor, index, record_limits, pinned, target_edges
        )
        if option is not None:
            options[option_key(option)] = option
        elif reason is not None:
            rejections.append(reason)
    ordered = tuple(sorted(options.values(), key=option_sort_key))
    if not ordered:
        members = tuple(
            sorted(
                (index.record_at(member).ref for group in groups for member in group),
                key=lambda ref: ref.key(),
            )
        )
        rejections.append(
            RepairConflict(
                code=NO_SNAP_POINT_WITHIN_LIMITS,
                message=(
                    "该近失配簇不存在同时满足每端点位移上限与保护连接的吸附点。"
                ),
                endpoints=members,
                details={
                    "anchor_candidates": [list(point) for point in unique_points(anchors)],
                    "rejection_count": len(rejections),
                },
            )
        )
    return ordered, tuple(rejections)


def cluster_edges(
    cluster: frozenset[frozenset[int]],
    targets: Sequence[tuple[EndpointRef, EndpointRef]],
    index: EndpointIndex,
    groups: Sequence[frozenset[int]],
) -> tuple[tuple[int, int], ...]:
    position_of: dict[frozenset[int], int] = {
        group: position for position, group in enumerate(groups)
    }
    cluster_members = frozenset(itertools.chain.from_iterable(cluster))
    edges: set[tuple[int, int]] = set()
    for first_ref, second_ref in targets:
        left = index.resolve(first_ref)
        right = index.resolve(second_ref)
        if left not in cluster_members or right not in cluster_members:
            continue
        group_left = index.group_of(left)
        group_right = index.group_of(right)
        if group_left != group_right:
            edges.add(
                tuple(sorted((position_of[group_left], position_of[group_right])))
            )
    return tuple(sorted(edges))


def build_option(
    groups: Sequence[frozenset[int]],
    anchor: tuple[float, float],
    index: EndpointIndex,
    record_limits: Mapping[int, float],
    pinned: frozenset[frozenset[int]],
    target_edges: Sequence[tuple[int, int]],
) -> tuple[Any, Any]:
    epsilon = index.tolerance.coordinate_epsilon
    destinations: dict[int, tuple[float, float]] = {}
    moved_positions: list[int] = []
    moved_records = 0
    max_displacement = 0.0
    total_displacement = 0.0
    for position, group in enumerate(groups):
        current = index.point_at(min(group))
        displacement = distance(current, anchor)
        if group in pinned and displacement > epsilon:
            return None, RepairConflict(
                code=PROTECTED_ENDPOINT_PINNED,
                message="候选吸附点要求移动受保护既有连接的端点，该锚点不可用。",
                endpoints=tuple(
                    sorted(
                        (index.record_at(member).ref for member in group),
                        key=lambda ref: ref.key(),
                    )
                ),
                details={"anchor": list(anchor), "displacement": stable_round(displacement)},
            )
        if displacement > epsilon:
            for member in group:
                limit = record_limits[member]
                if displacement > limit + epsilon:
                    return None, RepairConflict(
                        code=DISPLACEMENT_LIMIT,
                        message=(
                            "候选吸附点使某端点位移超过其允许上限，"
                            "该锚点不可用。"
                        ),
                        endpoints=(index.record_at(member).ref,),
                        details={
                            "anchor": list(anchor),
                            "displacement": stable_round(displacement),
                            "limit": stable_round(limit),
                        },
                    )
            moved_positions.append(position)
            moved_records += len(group)
            max_displacement = max(max_displacement, displacement)
            total_displacement += displacement * len(group)
        destinations[position] = anchor
    if not moved_positions:
        return None, None
    for left_position, right_position in target_edges:
        if (
            distance(destinations[left_position], destinations[right_position])
            > epsilon
        ):
            return None, RepairConflict(
                code=POST_TARGET_NOT_CONNECTED,
                message="候选吸附点未让指定近失配边连通。",
                endpoints=(),
                details={"anchor": list(anchor)},
            )
    return (
        ClusterOption(
            destinations=dict(destinations),
            moved_positions=tuple(moved_positions),
            moved_record_count=moved_records,
            max_displacement=max_displacement,
            total_displacement=total_displacement,
        ),
        None,
    )


def centroid(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    values = list(points)
    return (
        sum(point[0] for point in values) / len(values),
        sum(point[1] for point in values) / len(values),
    )


def unique_points(
    points: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Deduplicate anchors without collapsing int/float coordinate equality."""
    seen: set[tuple[Any, Any]] = set()
    result: list[tuple[float, float]] = []
    for point in points:
        key = (repr(point[0]), repr(point[1]))
        if key not in seen:
            seen.add(key)
            result.append(point)
    return result


def option_key(option: ClusterOption) -> str:
    return canonical_json(
        [
            [list(point) for point in option.destinations.values()],
            stable_round(option.max_displacement),
        ]
    )


def option_sort_key(option: ClusterOption) -> tuple[Any, ...]:
    return (
        len(option.moved_positions),
        option.moved_record_count,
        stable_round(option.max_displacement),
        stable_round(option.total_displacement),
        option_key(option),
    )
