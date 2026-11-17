"""Deterministic capacitated flow core for the assignment precheck.

The solver is a successive-shortest-augmenting-paths min-cost max-flow
with lexicographic ``(primary, secondary)`` tuple costs.  Adjacency is
scanned in insertion order and relaxation only accepts strictly cheaper
paths, so identical inputs always produce identical flows regardless of
the order in which the network was populated.
"""
from __future__ import annotations
from typing import Any, Optional

OBJECTIVE_DISTANCE = "min_total_distance"
OBJECTIVE_BALANCE = "balance_loads"
OBJECTIVES = (OBJECTIVE_DISTANCE, OBJECTIVE_BALANCE)
FEASIBILITY = "feasibility"


def flow_edges(
    candidates: list[dict[str, Any]],
    eligible_points: list[dict[str, Any]],
    valid_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map candidate pairs onto node indices for the flow network."""
    point_index = {record["point_id"]: i + 1 for i, record in enumerate(eligible_points)}
    zone_index = {
        record["zone_id"]: len(eligible_points) + 1 + i
        for i, record in enumerate(valid_zones)
    }
    edges = []
    for candidate in candidates:
        edges.append(
            {
                "point_node": point_index[candidate["point_id"]],
                "zone_node": zone_index[candidate["zone_id"]],
                "distance": candidate["distance"],
            }
        )
    edges.sort(key=lambda item: (item["point_node"], item["zone_node"]))
    return edges


class FlowNetwork:
    """Directed residual network with lexicographic (primary, secondary) costs."""

    def __init__(self, node_count: int) -> None:
        self.graph: list[list[list[Any]]] = [[] for _ in range(node_count)]

    def add_edge(
        self, tail: int, head: int, capacity: int, cost: tuple[float, float]
    ) -> list[Any]:
        forward = [head, len(self.graph[head]), capacity, cost]
        backward = [tail, len(self.graph[tail]), 0, (-cost[0], -cost[1])]
        self.graph[tail].append(forward)
        self.graph[head].append(backward)
        return forward


def add_assignment_arcs(
    network: FlowNetwork,
    eligible_points: list[dict[str, Any]],
    valid_zones: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    source: int,
    sink: int,
    objective: str,
) -> None:
    """Populate a network for one objective (or the plain feasibility run).

    ``min_total_distance`` prices point→zone arcs at the anchor distance;
    ``balance_loads`` prices the k-th unit of zone capacity at 2k-1 so the
    primary cost of a flow equals the sum of squared zone loads, with the
    anchor distance kept as the secondary cost.
    """
    zero = (0.0, 0.0)
    for index in range(len(eligible_points)):
        network.add_edge(source, index + 1, 1, zero)
    for edge in edges:
        if objective == OBJECTIVE_DISTANCE:
            cost = (edge["distance"], 0.0)
        elif objective == OBJECTIVE_BALANCE:
            cost = (0.0, edge["distance"])
        else:
            cost = zero
        edge["ref"] = network.add_edge(
            edge["point_node"], edge["zone_node"], 1, cost
        )
    for offset, zone in enumerate(valid_zones):
        zone_node = len(eligible_points) + 1 + offset
        capacity = zone["capacity"]
        if objective == OBJECTIVE_BALANCE:
            for unit in range(capacity):
                network.add_edge(zone_node, sink, 1, (float(2 * unit + 1), 0.0))
        else:
            network.add_edge(zone_node, sink, capacity, zero)


def min_cost_max_flow(
    network: FlowNetwork, source: int, sink: int
) -> tuple[int, tuple[float, float]]:
    """Successive shortest augmenting paths with lexicographic tuple costs."""
    flow = 0
    cost = (0.0, 0.0)
    while True:
        dist, parent = _shortest_path(network.graph, source, sink)
        if dist[sink] is None:
            return flow, cost
        amount: Optional[int] = None
        node = sink
        while node != source:
            prev, edge_index = parent[node]
            residual = network.graph[prev][edge_index][2]
            amount = residual if amount is None else min(amount, residual)
            node = prev
        node = sink
        while node != source:
            prev, edge_index = parent[node]
            edge = network.graph[prev][edge_index]
            edge[2] -= amount
            network.graph[node][edge[1]][2] += amount
            cost = (cost[0] + edge[3][0] * amount, cost[1] + edge[3][1] * amount)
            node = prev
        flow += amount


def _shortest_path(
    graph: list[list[list[Any]]], source: int, sink: int
) -> tuple[list[Optional[tuple[float, float]]], list[Any]]:
    dist: list[Optional[tuple[float, float]]] = [None] * len(graph)
    parent: list[Any] = [None] * len(graph)
    dist[source] = (0.0, 0.0)
    in_queue = [False] * len(graph)
    queue = [source]
    in_queue[source] = True
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        in_queue[node] = False
        base = dist[node]
        for edge_index, edge in enumerate(graph[node]):
            if edge[2] <= 0:
                continue
            target = edge[0]
            candidate = (base[0] + edge[3][0], base[1] + edge[3][1])
            if dist[target] is None or candidate < dist[target]:
                dist[target] = candidate
                parent[target] = (node, edge_index)
                if not in_queue[target]:
                    queue.append(target)
                    in_queue[target] = True
    return dist, parent


def reachable_from(graph: list[list[list[Any]]], source: int) -> list[bool]:
    """Residual-reachable set from ``source`` (the minimal min-cut side)."""
    seen = [False] * len(graph)
    stack = [source]
    seen[source] = True
    while stack:
        node = stack.pop()
        for edge in graph[node]:
            if edge[2] > 0 and not seen[edge[0]]:
                seen[edge[0]] = True
                stack.append(edge[0])
    return seen
