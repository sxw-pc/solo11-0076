"""Explicit-edge clustering and bounded repair candidate enumeration.

Near-miss adjacency is never treated as transitive: a cluster only contains
the exact-connection endpoint groups joined by *requested* edges.  Each
cluster contributes a fixed, stably ordered set of snap anchors (see
:mod:`repair_anchors`), and the cross-cluster product is truncated to an
explicit candidate budget.
"""
from __future__ import annotations
import itertools
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .model import EndpointRef
from .rule_topology import EndpointIndex
from .util import canonical_json, stable_round

# Structured, stable conflict vocabularies shared with the verifier.
DISPLACEMENT_LIMIT = "DISPLACEMENT_LIMIT"
PROTECTED_ENDPOINT_PINNED = "PROTECTED_ENDPOINT_PINNED"
POST_TARGET_NOT_CONNECTED = "POST_TARGET_NOT_CONNECTED"
NO_SNAP_POINT_WITHIN_LIMITS = "NO_SNAP_POINT_WITHIN_LIMITS"


@dataclass(frozen=True)
class ClusterOption:
    """One way of snapping one cluster: group position -> destination point."""

    destinations: Mapping[int, tuple[float, float]]
    moved_positions: tuple[int, ...]
    moved_record_count: int
    max_displacement: float
    total_displacement: float


@dataclass(frozen=True)
class Plan:
    """One complete combination of per-cluster snap options."""

    cluster_options: tuple[ClusterOption, ...]
    moved_endpoint_count: int
    max_displacement: float
    total_displacement: float


def build_clusters(
    targets: Sequence[tuple[EndpointRef, EndpointRef]], index: EndpointIndex
) -> tuple[frozenset[frozenset[int]], ...]:
    """Connected components of exact-connection groups over requested edges.

    Only groups touched by a requested edge participate; near-miss adjacency
    is never treated as transitive.
    """
    parent: dict[frozenset[int], frozenset[int]] = {}

    def find(node: frozenset[int]) -> frozenset[int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: frozenset[int], b: frozenset[int]) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            chosen = root_a if min(root_a) < min(root_b) else root_b
            parent[root_b if chosen is root_a else root_a] = chosen

    involved: set[frozenset[int]] = set()
    for first_ref, second_ref in targets:
        group_left = index.group_of(index.resolve(first_ref))
        group_right = index.group_of(index.resolve(second_ref))
        involved.add(group_left)
        involved.add(group_right)
        union(group_left, group_right)

    merged: dict[frozenset[int], set[frozenset[int]]] = {}
    for group in involved:
        merged.setdefault(find(group), set()).add(group)
    return tuple(
        frozenset(groups)
        for groups in sorted(
            merged.values(),
            key=lambda members: min(min(group) for group in members),
        )
    )


def enumerate_plans(
    cluster_options: Sequence[Sequence[ClusterOption]], budget: int
) -> tuple[tuple[Plan, ...], list[str]]:
    """Stable, budget-bounded cross product of per-cluster options."""
    if any(not options for options in cluster_options):
        return [], []
    plans: list[Plan] = []
    review_reasons: list[str] = []
    truncated = False
    for combination in itertools.product(*cluster_options):
        if len(plans) >= budget:
            truncated = True
            break
        plans.append(_compose_plan(combination))
    if truncated:
        review_reasons.append(
            f"候选组合数超过预算 {budget}，仅枚举前 {budget} 个稳定排序方案；"
            "未宣称无解或全局最优，剩余候选需人工复核。"
        )
    plans.sort(
        key=lambda plan: (
            plan.moved_endpoint_count,
            stable_round(plan.max_displacement),
            stable_round(plan.total_displacement),
            plan_signature(plan),
        )
    )
    return tuple(plans), review_reasons


def _compose_plan(options: Sequence[ClusterOption]) -> Plan:
    max_displacement = 0.0
    total_displacement = 0.0
    moved_endpoint_count = 0
    for option in options:
        max_displacement = max(max_displacement, option.max_displacement)
        total_displacement += option.total_displacement
        moved_endpoint_count += option.moved_record_count
    return Plan(
        cluster_options=tuple(options),
        moved_endpoint_count=moved_endpoint_count,
        max_displacement=max_displacement,
        total_displacement=total_displacement,
    )


def plan_signature(plan: Plan) -> str:
    payload = [
        {
            str(position): list(point)
            for position, point in sorted(option.destinations.items())
        }
        for option in plan.cluster_options
    ]
    return canonical_json(payload)


def pareto_front(candidates: Sequence[Any]) -> tuple[str, ...]:
    """Non-dominated plans on (moved endpoint count, max displacement)."""
    feasible = [candidate for candidate in candidates if candidate.feasible]
    frontier: list[Any] = []
    for candidate in feasible:
        dominated = any(
            other is not candidate
            and other.moved_endpoint_count <= candidate.moved_endpoint_count
            and other.max_displacement <= candidate.max_displacement
            and (
                other.moved_endpoint_count < candidate.moved_endpoint_count
                or other.max_displacement < candidate.max_displacement
            )
            for other in feasible
        )
        if not dominated:
            frontier.append(candidate)
    frontier.sort(
        key=lambda candidate: (
            candidate.moved_endpoint_count,
            stable_round(candidate.max_displacement),
            candidate.candidate_id,
        )
    )
    return tuple(candidate.candidate_id for candidate in frontier)
