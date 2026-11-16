"""Tests for the read-only endpoint repair rehearsal.

Synthetic layers are built in memory (or in isolated temp directories) and
cover every acceptance scenario: constrained edits, non-transitive neighbor
clustering, real post-repair re-audits, and the Pareto trade-off.
"""
from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from topoaudit.config import load_config
from topoaudit.errors import RepairError
from topoaudit.loaders import load_layers
from topoaudit.model import (
    AuditConfig,
    CRS,
    Bounds,
    DeclaredCRS,
    EndpointRef,
    Feature,
    FieldSpec,
    Geometry,
    InputFile,
    LayerConfig,
    LoadedLayer,
    Tolerance,
)
from topoaudit.repair import (
    CANDIDATE_BUDGET_EXCEEDED,
    DISPLACEMENT_LIMIT,
    NEW_FINDING_AFTER_REPAIR,
    NO_SNAP_POINT_WITHIN_LIMITS,
    POST_ACCIDENTAL_CONNECTION,
    POST_EXISTING_CONNECTION_BROKEN,
    POST_TARGET_NOT_CONNECTED,
    PROTECTED_ENDPOINT_PINNED,
    TARGET_NOT_NEAR_MISS,
    RepairRequest,
    parse_repair_request,
    rehearse_endpoint_repair,
)
from topoaudit.repair_report import build_rehearsal_report

EPSILON = 0.000001
NEAR_MISS = 2.0
LIMIT = 0.9


def crs() -> CRS:
    return CRS(
        name="LOCAL_XY",
        kind="planar",
        bounds=Bounds(-1000, -1000, 10000, 10000),
    )


def base_config(layers: Sequence[LayerConfig]) -> AuditConfig:
    return AuditConfig(
        path=Path("synthetic.json"),
        config_digest="test-digest",
        schema_version="1.0",
        run_label="repair-unit-test",
        tolerance=Tolerance(
            coordinate_epsilon=EPSILON, near_miss_distance=NEAR_MISS
        ),
        crs=crs(),
        layers=tuple(layers),
    )


def line_layer(features: Sequence[Feature]) -> LoadedLayer:
    config = LayerConfig(
        layer_id="lines",
        path="lines.geojson",
        resolved_path=Path("/tmp/lines.geojson"),
        source_format="geojson",
        geometry_types=("LineString", "MultiLineString"),
        id_field="id",
        schema={"id": FieldSpec("id", "string", True)},
        rules={},
        threshold_overrides={"near_miss_distance": NEAR_MISS},
        declared_crs=None,
    )
    return LoadedLayer(
        config=config,
        features=tuple(features),
        input_file=InputFile(
            layer_id="lines",
            path="lines.geojson",
            source_format="geojson",
            sha256="0" * 64,
            size_bytes=0,
        ),
    )


def line_feature(feature_id: str, coordinates: Sequence[Any], index: int) -> Feature:
    return Feature(
        layer_id="lines",
        feature_id=feature_id,
        geometry=Geometry(
            kind="LineString",
            coordinates=tuple(tuple(map(float, point)) for point in coordinates),
            raw_type="LineString",
        ),
        properties={"id": feature_id},
        source_index=index,
    )


def ref(feature_id: str, side: str, part: str = "parts[0]") -> EndpointRef:
    return EndpointRef(feature_id=feature_id, part_index=part, side=side)


def start_of(feature_id: str) -> EndpointRef:
    return ref(feature_id, "start")


def end_of(feature_id: str) -> EndpointRef:
    return ref(feature_id, "end")


def request(
    targets: Sequence[tuple[EndpointRef, EndpointRef]],
    *,
    max_displacement: float = LIMIT,
    protected: Sequence[tuple[EndpointRef, EndpointRef]] = (),
    endpoint_limits: dict[EndpointRef, float] | None = None,
    budget: int = 200,
) -> RepairRequest:
    return RepairRequest(
        layer_id="lines",
        targets=tuple(targets),
        max_displacement=max_displacement,
        endpoint_limits=endpoint_limits or {},
        protected_connections=tuple(protected),
        candidate_budget=budget,
    )


def rehearse(features: Sequence[Feature], req: RepairRequest):
    layer = line_layer(features)
    config = base_config((layer.config,))
    return rehearse_endpoint_repair(config, (layer,), req), layer, config


def feasible_candidates(result) -> list:
    return [candidate for candidate in result.candidates if candidate.feasible]


def conflict_codes(result) -> set[str]:
    codes = {conflict.code for conflict in result.conflicts}
    for candidate in result.candidates:
        codes.update(conflict.code for conflict in candidate.conflicts)
    return codes


class TwoEndpointsTradeoffTest(unittest.TestCase):
    """The 0.8-gap pair: one-end move and two-end split must both survive."""

    def setUp(self):
        # A ends at (10, 0); B starts at (10.8, 0): gap 0.8, no other
        # endpoints nearby, long enough runs so no zero-length segments.
        self.features = (
            line_feature("A", [(0, 0), (5, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (16, 0), (22, 0)], 1),
        )

    def test_pareto_front_keeps_both_tradeoffs(self):
        result, _, _ = rehearse(
            self.features, request([(end_of("A"), start_of("B"))])
        )
        self.assertEqual(result.status, "feasible")
        feasible = feasible_candidates(result)
        # Three non-dominated plans: move only A, move only B (each one
        # endpoint, 0.8 displacement), or split 0.4/0.4 (two endpoints).
        self.assertEqual(len(feasible), 3)
        # Frontier must contain all three plans.
        self.assertEqual(set(result.pareto_front), {c.candidate_id for c in feasible})
        one_end = [c for c in feasible if c.moved_endpoint_count == 1]
        two_ends = [c for c in feasible if c.moved_endpoint_count == 2]
        self.assertEqual(len(one_end), 2)
        for candidate in one_end:
            self.assertAlmostEqual(candidate.max_displacement, 0.8, places=6)
        self.assertEqual(len(two_ends), 1)
        self.assertAlmostEqual(two_ends[0].max_displacement, 0.4, places=6)
        # Every move respects the per-endpoint cap.
        for candidate in feasible:
            for move in candidate.moves:
                self.assertLessEqual(move.displacement, LIMIT + EPSILON)
        # Every feasible plan truly connects at coordinate_epsilon.
        for candidate in feasible:
            changed = {
                (move.endpoint.feature_id, move.endpoint.side): move.proposed_coordinate
                for move in candidate.moves
            }
            a_point = changed.get(("A", "end"), (10.0, 0.0))
            b_point = changed.get(("B", "start"), (10.8, 0.0))
            self.assertLessEqual(
                ((a_point[0] - b_point[0]) ** 2 + (a_point[1] - b_point[1]) ** 2)
                ** 0.5,
                EPSILON,
            )

    def test_repair_removes_near_miss_without_new_findings(self):
        result, _, _ = rehearse(
            self.features, request([(end_of("A"), start_of("B"))])
        )
        for candidate in feasible_candidates(result):
            added = [
                change
                for change in candidate.finding_changes
                if change.change_type == "added"
            ]
            removed = [
                change
                for change in candidate.finding_changes
                if change.change_type == "removed"
            ]
            self.assertEqual(added, [])
            rule_ids_removed = {change.finding.rule_id for change in removed}
            self.assertIn("NEAR_MISS_ENDPOINT", rule_ids_removed)
            # The two repaired endpoints stop being dangling as well.
            self.assertIn("DANGLING_ENDPOINT", rule_ids_removed)

    def test_ordering_is_input_permutation_independent(self):
        req = request([(end_of("A"), start_of("B"))])
        first, _, _ = rehearse(self.features, req)
        reversed_features = tuple(
            replace(feature, source_index=1 - feature.source_index)
            for feature in reversed(self.features)
        )
        second, _, _ = rehearse(reversed_features, req)

        def change_view(change):
            finding = change.finding
            feature = finding.feature_id
            related = tuple(finding.related_feature_ids)
            if finding.rule_id == "NEAR_MISS_ENDPOINT":
                # The primary feature_id of an endpoint finding follows the
                # input source order; the business identity is the endpoint
                # pair, so compare on the unordered feature pair instead.
                feature = None
                related = tuple(
                    sorted(
                        {
                            finding.evidence["endpoint_a"]["feature_id"],
                            finding.evidence["endpoint_b"]["feature_id"],
                        }
                    )
                )
            return [change.change_type, finding.rule_id, feature, related]

        def business_view(result):
            return [
                {
                    "candidate_id": candidate.candidate_id,
                    "feasible": candidate.feasible,
                    "is_pareto": candidate.is_pareto,
                    "moves": [
                        [move.endpoint.key(), list(move.proposed_coordinate)]
                        for move in candidate.moves
                    ],
                    "changes": [
                        change_view(change)
                        for change in candidate.finding_changes
                    ],
                    "conflicts": [conflict.code for conflict in candidate.conflicts],
                }
                for candidate in result.candidates
            ]

        self.assertEqual(business_view(first), business_view(second))
        self.assertEqual(first.pareto_front, second.pareto_front)


class NonTransitiveNeighborTest(unittest.TestCase):
    """A near B and B near C must not merge A-C when A-C is out of range."""

    def setUp(self):
        # Gaps: A-B = 1.0, B-C = 1.0 with near_miss 1.5, while A-C = 2.0
        # exceeds near_miss.  The chain is slightly bent so the three-way
        # centroid anchor is distinct from every existing endpoint.
        self.features = (
            line_feature("A", [(0, 10), (0, 0)], 0),        # end (0,0)
            line_feature("B", [(1.0, 0), (1.0, -10)], 1),   # start (1,0)
            line_feature("C", [(2.0, 1.0), (2.0, 11)], 2),  # start (2,1)
        )
        self.layer = line_layer(self.features)
        self.config = base_config((self.layer.config,))
        self.tolerance = replace(
            self.config.tolerance, near_miss_distance=1.5
        )
        self.config = replace(self.config, tolerance=self.tolerance)

    def _rehearse(self, targets, max_displacement: float = 2.1):
        return rehearse_endpoint_repair(
            self.config,
            (self.layer,),
            request(targets, max_displacement=max_displacement),
        )

    def test_single_edge_does_not_pull_third_endpoint(self):
        result = self._rehearse([(end_of("A"), start_of("B"))])
        self.assertEqual(result.status, "feasible")
        for candidate in feasible_candidates(result):
            touched = {move.endpoint.feature_id for move in candidate.moves}
            self.assertNotIn("C", touched)
        # C remains a dangling endpoint: retained/added findings keep its name.
        candidate = feasible_candidates(result)[0]
        dangling = [
            change
            for change in candidate.finding_changes
            if change.finding.rule_id == "DANGLING_ENDPOINT"
        ]
        self.assertTrue(
            any(change.finding.feature_id == "C" for change in dangling)
        )

    def test_both_edges_form_one_explicit_cluster_without_transitive_claim(self):
        result = self._rehearse(
            [
                (end_of("A"), start_of("B")),
                (start_of("B"), start_of("C")),
            ]
        )
        self.assertEqual(result.status, "feasible")
        # A-C distance (2.0) exceeds near_miss: the only reason they share a
        # cluster is the two explicitly requested edges.
        feasible = feasible_candidates(result)
        self.assertTrue(feasible)
        for candidate in feasible:
            touched = {move.endpoint.feature_id for move in candidate.moves}
            # At least the plans that move all three exist (centroid anchor).
            self.assertTrue(touched <= {"A", "B", "C"})
        # The shared-centroid plan moves all three endpoints to the mean
        # point; no endpoint travels farther than the largest centroid gap.
        points = ((0.0, 0.0), (1.0, 0.0), (2.0, 1.0))
        centroid = (
            sum(p[0] for p in points) / 3,
            sum(p[1] for p in points) / 3,
        )
        max_centroid_gap = max(
            ((p[0] - centroid[0]) ** 2 + (p[1] - centroid[1]) ** 2) ** 0.5
            for p in points
        )
        centroid_plans = [
            candidate
            for candidate in feasible
            if candidate.moved_endpoint_count == 3
        ]
        self.assertTrue(centroid_plans)
        for candidate in centroid_plans:
            self.assertLessEqual(
                candidate.max_displacement, max_centroid_gap + EPSILON
            )

    def test_near_miss_graph_is_not_transitively_merged(self):
        # Requesting only A-B must never propose an A-C exact connection.
        result = self._rehearse([(end_of("A"), start_of("B"))])
        for candidate in result.candidates:
            for conflict in candidate.conflicts:
                self.assertNotEqual(
                    conflict.code, POST_ACCIDENTAL_CONNECTION
                )
        feasible = feasible_candidates(result)
        for candidate in feasible:
            moved = {move.endpoint.feature_id for move in candidate.moves}
            self.assertNotIn("C", moved)


class SharedEndpointConflictTest(unittest.TestCase):
    """One endpoint participates in two requested pairs: shared feasibility."""

    def setUp(self):
        # B's start is simultaneously near A's end (gap 0.8) and C's start
        # (gap 0.8), vertically arranged; a single anchor must satisfy both.
        self.features = (
            line_feature("A", [(0, 0), (10, 0)], 0),          # end (10,0)
            line_feature("B", [(10.8, 0), (10.8, -8)], 1),   # start (10.8,0)
            line_feature("C", [(10.8, 0.8), (4, 4)], 2),     # start (10.8,0.8)
        )

    def test_shared_anchor_plans_connect_both_edges(self):
        result, _, _ = rehearse(
            self.features,
            request(
                [
                    (end_of("A"), start_of("B")),
                    (start_of("B"), start_of("C")),
                ]
            ),
        )
        self.assertEqual(result.status, "feasible")
        feasible = feasible_candidates(result)
        self.assertTrue(feasible)
        for candidate in feasible:
            # Both requested edges must be exact-connected after the edit.
            destinations = {}
            for move in candidate.moves:
                destinations[
                    (move.endpoint.feature_id, move.endpoint.side)
                ] = move.proposed_coordinate
            b_point = destinations.get(("B", "start"), (10.8, 0.0))
            a_point = destinations.get(("A", "end"), (10.0, 0.0))
            c_point = destinations.get(("C", "start"), (10.8, 0.8))
            self.assertLessEqual(
                abs(a_point[0] - b_point[0])
                + abs(a_point[1] - b_point[1]),
                EPSILON * 2,
            )
            self.assertLessEqual(
                abs(c_point[0] - b_point[0])
                + abs(c_point[1] - b_point[1]),
                EPSILON * 2,
            )

    def test_tight_limit_makes_shared_snap_infeasible_with_reasons(self):
        # Neither A nor C may move more than 0.2; B pinned by a small limit
        # too: the 0.8 gaps cannot close, so no feasible plan survives.
        result, _, _ = rehearse(
            self.features,
            request(
                [
                    (end_of("A"), start_of("B")),
                    (start_of("B"), start_of("C")),
                ],
                endpoint_limits={
                    end_of("A"): 0.2,
                    start_of("B"): 0.2,
                    start_of("C"): 0.2,
                },
            ),
        )
        self.assertEqual(result.status, "infeasible")
        self.assertEqual(feasible_candidates(result), [])
        self.assertIn(NO_SNAP_POINT_WITHIN_LIMITS, conflict_codes(result))
        self.assertIn(DISPLACEMENT_LIMIT, conflict_codes(result))


class ZeroLengthAfterSnapTest(unittest.TestCase):
    """Snapping a short line's endpoint onto its other vertex is invalid."""

    def setUp(self):
        # A ends at (10, 0). B is a short two-vertex segment only 0.5 long:
        # moving B's start 0.8 onto A collapses B into a zero-length line
        # (its two vertices then sit ~0.3 apart, below coordinate_epsilon?
        # no — use a 0.5 segment so the 0.8 move leaves both at the same
        # point within epsilon only for the full-move anchor).
        self.features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (10.8, 0.5)], 1),
        )

    def setUp(self):
        # A ends at (10, 0). B is a short collinear segment only 0.4 long,
        # pointing back toward A.  Snapping B's start onto the A-B midpoint
        # (a 0.4 move) makes B's two vertices coincide within epsilon, i.e.
        # a zero-length segment; moving B's start all the way onto A keeps
        # the segment non-degenerate (its other vertex then sits 0.4 away).
        self.features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (10.4, 0)], 1),
        )

    def test_snap_onto_other_vertex_is_rejected_by_reaudit(self):
        result, _, _ = rehearse(
            self.features, request([(end_of("A"), start_of("B"))])
        )
        # The midpoint (0.4/0.4 split) anchor collapses B and must fail.
        offending = [
            candidate
            for candidate in result.candidates
            if any(
                conflict.code == NEW_FINDING_AFTER_REPAIR
                and conflict.details.get("rule_id") == "ZERO_LENGTH_SEGMENT"
                for conflict in candidate.conflicts
            )
        ]
        self.assertTrue(offending)
        for candidate in offending:
            self.assertFalse(candidate.feasible)
        # Anchors that keep B non-degenerate remain feasible and introduce
        # no geometry finding at all.
        feasible = feasible_candidates(result)
        self.assertTrue(feasible)
        for candidate in feasible:
            self.assertFalse(
                any(
                    conflict.code == NEW_FINDING_AFTER_REPAIR
                    for conflict in candidate.conflicts
                )
            )


class SelfIntersectionAfterSnapTest(unittest.TestCase):
    """A snap that bends a line into a self-intersection must be rejected."""

    def setUp(self):
        # A ends at (0, 0). B starts near it at (0.5, 0) and runs in a U
        # shape whose later segments cross near the origin once B's start is
        # pulled across them.
        self.features = (
            line_feature("A", [(-10, 0), (0, 0)], 0),
            line_feature(
                "B",
                [(0.5, 0), (4, 0), (4, 2), (0.2, 2), (0.2, -2), (4, -2)],
                1,
            ),
        )

    def test_self_intersection_plan_is_flagged(self):
        result, _, _ = rehearse(
            self.features,
            request([(end_of("A"), start_of("B"))], max_displacement=1.0),
        )
        flagged = [
            candidate
            for candidate in result.candidates
            if any(
                conflict.code == NEW_FINDING_AFTER_REPAIR
                and conflict.details.get("rule_id") == "SELF_INTERSECTION"
                for conflict in candidate.conflicts
            )
        ]
        self.assertTrue(flagged)
        for candidate in flagged:
            self.assertFalse(candidate.feasible)
            self.assertIn(
                "B",
                {move.endpoint.feature_id for move in candidate.moves},
            )
        # Every feasible plan must leave the repaired geometry free of
        # self-intersection; plans that bend B into itself are excluded.
        feasible = feasible_candidates(result)
        self.assertTrue(feasible)
        for candidate in feasible:
            self.assertFalse(
                any(
                    conflict.details.get("rule_id") == "SELF_INTERSECTION"
                    for conflict in candidate.conflicts
                )
            )
        # At least one feasible plan avoids touching B entirely.
        self.assertTrue(
            any(
                all(move.endpoint.feature_id != "B" for move in candidate.moves)
                for candidate in feasible
            )
        )


class ExistingConnectionProtectionTest(unittest.TestCase):
    """Protected exact connections cannot be broken; pinned groups hold."""

    def setUp(self):
        # B.start and C.start already coincide exactly (an existing
        # connection). A.end is 0.8 away from that shared junction.
        self.features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (10.8, -8)], 1),
            line_feature("C", [(10.8, 0), (16, 4)], 2),
        )
        self.junction_pair = (start_of("B"), start_of("C"))

    def test_protection_pins_shared_group(self):
        result, _, _ = rehearse(
            self.features,
            request(
                [(end_of("A"), start_of("B"))],
                protected=[self.junction_pair],
            ),
        )
        self.assertEqual(result.status, "feasible")
        for candidate in feasible_candidates(result):
            moved = {move.endpoint.feature_id for move in candidate.moves}
            # The pinned B/C junction cannot move; only A travels the 0.8.
            self.assertNotIn("B", moved)
            self.assertNotIn("C", moved)
            self.assertEqual(moved, {"A"})

    def test_unprotected_shared_group_moves_together_never_breaks(self):
        result, _, _ = rehearse(
            self.features, request([(end_of("A"), start_of("B"))])
        )
        self.assertEqual(result.status, "feasible")
        for candidate in feasible_candidates(result):
            moved_features = {
                move.endpoint.feature_id for move in candidate.moves
            }
            if "B" in moved_features:
                self.assertIn("C", moved_features)
            destinations = {
                move.endpoint.feature_id: move.proposed_coordinate
                for move in candidate.moves
            }
            if "B" in destinations:
                self.assertEqual(destinations["B"], destinations["C"])
            self.assertFalse(
                any(
                    conflict.code == POST_EXISTING_CONNECTION_BROKEN
                    for conflict in candidate.conflicts
                )
            )

    def test_protection_reference_must_be_existing_connection(self):
        layer = line_layer(self.features)
        config = base_config((layer.config,))
        with self.assertRaises(RepairError):
            rehearse_endpoint_repair(
                config,
                (layer,),
                request(
                    [(end_of("A"), start_of("B"))],
                    protected=[(end_of("A"), start_of("B"))],
                ),
            )

    def test_limit_conflict_reports_pin_violation(self):
        # Forbid A from moving enough and protect the junction: no anchor
        # remains, producing structured displacement/pin conflicts.
        result, _, _ = rehearse(
            self.features,
            request(
                [(end_of("A"), start_of("B"))],
                max_displacement=0.2,
                protected=[self.junction_pair],
            ),
        )
        self.assertEqual(result.status, "infeasible")
        codes = conflict_codes(result)
        self.assertTrue(
            DISPLACEMENT_LIMIT in codes
            or NO_SNAP_POINT_WITHIN_LIMITS in codes
        )


class AccidentalConnectionTest(unittest.TestCase):
    """A third, unspecified near neighbor must not get silently joined."""

    def setUp(self):
        # A.end at (10,0), B.start at (10.8,0); D.start at (10.4, 5) is far
        # enough vertically. Instead place D.start at (10.4, 0.0000005) so a
        # midpoint snap lands within epsilon of D as well only for some
        # anchors; construct D exactly on the A-B midpoint line.
        self.features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (20, 0)], 1),
            # D's start coincides with the A-B midpoint: any split anchor
            # there would accidentally connect D.
            line_feature("D", [(10.4, 0), (10.4, 8)], 2),
        )

    def test_midpoint_anchor_accidental_join_is_rejected(self):
        result, _, _ = rehearse(
            self.features, request([(end_of("A"), start_of("B"))])
        )
        # At least one candidate (the 0.4/0.4 split to the midpoint) creates
        # an accidental exact connection with D and is rejected.
        accidental = [
            candidate
            for candidate in result.candidates
            if any(
                conflict.code == POST_ACCIDENTAL_CONNECTION
                for conflict in candidate.conflicts
            )
        ]
        self.assertTrue(accidental)
        # Feasible plans avoid the midpoint anchor entirely.
        for candidate in feasible_candidates(result):
            for move in candidate.moves:
                if move.endpoint.feature_id in {"A", "B"}:
                    self.assertNotAlmostEqual(
                        move.proposed_coordinate[0], 10.4, places=6
                    )


class InfeasibleAndValidationTest(unittest.TestCase):
    def test_target_too_far_is_not_a_near_miss(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(20, 0), (30, 0)], 1),
        )
        result, _, _ = rehearse(
            features, request([(end_of("A"), start_of("B"))])
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn(TARGET_NOT_NEAR_MISS, conflict_codes(result))
        self.assertEqual(result.candidates, ())

    def test_already_connected_target_is_rejected(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10, 0), (20, 0)], 1),
        )
        result, _, _ = rehearse(
            features, request([(end_of("A"), start_of("B"))])
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn(TARGET_NOT_NEAR_MISS, conflict_codes(result))

    def test_unknown_endpoint_raises(self):
        features = (line_feature("A", [(0, 0), (10, 0)], 0),)
        layer = line_layer(features)
        config = base_config((layer.config,))
        with self.assertRaises(RepairError):
            rehearse_endpoint_repair(
                config,
                (layer,),
                request([(end_of("A"), start_of("GHOST"))]),
            )

    def test_displacement_cap_blocks_all_candidates(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (20, 0)], 1),
        )
        result, _, _ = rehearse(
            features, request([(end_of("A"), start_of("B"))], max_displacement=0.3)
        )
        self.assertEqual(result.status, "infeasible")
        self.assertEqual(feasible_candidates(result), [])
        self.assertIn(NO_SNAP_POINT_WITHIN_LIMITS, conflict_codes(result))

    def test_no_pseudo_actionable_advice_when_infeasible(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (20, 0)], 1),
        )
        result, _, _ = rehearse(
            features, request([(end_of("A"), start_of("B"))], max_displacement=0.1)
        )
        for candidate in result.candidates:
            self.assertFalse(candidate.feasible)
            self.assertTrue(candidate.conflicts)
        self.assertTrue(result.conflicts)


class BudgetTest(unittest.TestCase):
    def test_budget_truncation_marks_review_without_claiming_optimum(self):
        # Three independent pairs -> 2 options each => 8 combinations; cap
        # the budget below that and assert review_required is raised while
        # the returned plans remain a stable, fully reviewed prefix.
        features = (
            line_feature("A1", [(0, 0), (10, 0)], 0),
            line_feature("A2", [(10.8, 0), (20, 0)], 1),
            line_feature("B1", [(0, 10), (10, 10)], 2),
            line_feature("B2", [(10.8, 10), (20, 10)], 3),
            line_feature("C1", [(0, 20), (10, 20)], 4),
            line_feature("C2", [(10.8, 20), (20, 20)], 5),
        )
        targets = [
            (end_of("A1"), start_of("A2")),
            (end_of("B1"), start_of("B2")),
            (end_of("C1"), start_of("C2")),
        ]
        result, _, _ = rehearse(features, request(targets, budget=3))
        self.assertTrue(result.review_required)
        self.assertLessEqual(len(result.candidates), 3)
        self.assertIn(CANDIDATE_BUDGET_EXCEEDED, conflict_codes(result))
        # Even under truncation, every returned candidate was really audited.
        for candidate in result.candidates:
            self.assertTrue(candidate.finding_changes)

    def test_small_set_lists_complete_frontier(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (20, 0)], 1),
        )
        result, _, _ = rehearse(
            features,
            request([(end_of("A"), start_of("B"))], budget=200),
        )
        self.assertFalse(result.review_required)
        self.assertEqual(
            len(result.pareto_front), len(feasible_candidates(result))
        )


class PreflightBlockingTest(unittest.TestCase):
    def test_invalid_geometry_input_is_not_auto_repaired(self):
        bad = line_feature("BAD", [(0, 0), (1, 1), (0, 1), (1, 0)], 0)
        good = line_feature("GOOD", [(2.5, 0), (8, 0)], 1)
        layer = line_layer((bad, good))
        config = base_config((layer.config,))
        result = rehearse_endpoint_repair(
            config,
            (layer,),
            request([(start_of("BAD"), start_of("GOOD"))]),
        )
        self.assertEqual(result.status, "rejected")
        self.assertTrue(
            any(code.startswith("PREFLIGHT_") for code in conflict_codes(result))
        )
        self.assertEqual(result.candidates, ())

    def test_crs_declaration_conflict_blocks_rehearsal(self):
        features = (
            line_feature("A", [(0, 0), (10, 0)], 0),
            line_feature("B", [(10.8, 0), (20, 0)], 1),
        )
        layer = line_layer(features)
        conflicting = replace(
            layer.config,
            declared_crs=DeclaredCRS(name="OTHER_GRID", kind="planar"),
        )
        blocked_layer = LoadedLayer(
            config=conflicting,
            features=layer.features,
            input_file=layer.input_file,
        )
        config = base_config((conflicting,))
        result = rehearse_endpoint_repair(
            config,
            (blocked_layer,),
            request([(end_of("A"), start_of("B"))]),
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn("PREFLIGHT_LAYER_CRS_MISMATCH", conflict_codes(result))


class RequestParsingTest(unittest.TestCase):
    def test_parse_valid_request(self):
        raw = {
            "layer_id": "lines",
            "max_displacement": 0.9,
            "targets": [
                [
                    {"feature_id": "A", "part_index": "parts[0]", "side": "end"},
                    {"feature_id": "B", "part_index": "parts[0]", "side": "start"},
                ]
            ],
            "protected_connections": [
                [
                    {"feature_id": "B", "part_index": "parts[0]", "side": "start"},
                    {"feature_id": "C", "part_index": "parts[0]", "side": "start"},
                ]
            ],
            "endpoint_limits": [
                {
                    "endpoint": {
                        "feature_id": "A",
                        "part_index": "parts[0]",
                        "side": "end",
                    },
                    "max_displacement": 0.4,
                }
            ],
            "candidate_budget": 50,
        }
        parsed = parse_repair_request(raw)
        self.assertEqual(parsed.layer_id, "lines")
        self.assertEqual(parsed.max_displacement, 0.9)
        self.assertEqual(len(parsed.targets), 1)
        self.assertEqual(len(parsed.protected_connections), 1)
        self.assertEqual(parsed.candidate_budget, 50)
        self.assertEqual(
            parsed.effective_limit(end_of("A")), 0.4
        )
        self.assertEqual(parsed.effective_limit(start_of("B")), 0.9)

    def test_parse_rejects_unknown_fields_and_bad_values(self):
        with self.assertRaises(RepairError):
            parse_repair_request({"layer_id": "x", "max_displacement": 1, "targets": []})
        with self.assertRaises(RepairError):
            parse_repair_request(
                {
                    "layer_id": "x",
                    "max_displacement": -1,
                    "targets": [
                        [
                            {"feature_id": "A", "part_index": "parts[0]", "side": "end"},
                            {"feature_id": "B", "part_index": "parts[0]", "side": "end"},
                        ]
                    ],
                }
            )
        with self.assertRaises(RepairError):
            parse_repair_request(
                {
                    "layer_id": "x",
                    "max_displacement": 1,
                    "targets": [
                        [
                            {"feature_id": "A", "part_index": "parts[0]", "side": "middle"},
                            {"feature_id": "B", "part_index": "parts[0]", "side": "end"},
                        ]
                    ],
                }
            )


class SourceBytesUntouchedTest(unittest.TestCase):
    def test_rehearsal_never_writes_sources(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": "A"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[0, 0], [10, 0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"id": "B"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[10.8, 0], [20, 0]],
                    },
                },
            ],
        }
        config_doc = {
            "schema_version": "1.0",
            "run_label": "byte-guard-test",
            "tolerance": {
                "coordinate_epsilon": EPSILON,
                "near_miss_distance": NEAR_MISS,
            },
            "crs": {
                "name": "LOCAL_XY",
                "type": "planar",
                "bounds": {
                    "min_x": -100,
                    "min_y": -100,
                    "max_x": 1000,
                    "max_y": 1000,
                },
            },
            "layers": [
                {
                    "id": "lines",
                    "path": "lines.geojson",
                    "format": "geojson",
                    "geometry_types": ["LineString", "MultiLineString"],
                    "id_field": "id",
                    "schema": {"id": {"type": "string", "required": True}},
                }
            ],
        }
        request_doc = {
            "layer_id": "lines",
            "max_displacement": 0.9,
            "targets": [
                [
                    {"feature_id": "A", "part_index": "parts[0]", "side": "end"},
                    {"feature_id": "B", "part_index": "parts[0]", "side": "start"},
                ]
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lines.geojson").write_text(
                json.dumps(geojson), encoding="utf-8"
            )
            (root / "config.json").write_text(
                json.dumps(config_doc), encoding="utf-8"
            )
            (root / "request.json").write_text(
                json.dumps(request_doc), encoding="utf-8"
            )
            source_hash = hashlib.sha256(
                (root / "lines.geojson").read_bytes()
            ).hexdigest()
            config = load_config(root / "config.json")
            layers = load_layers(config)
            before_features = [
                json.dumps(feature.geometry.coordinates)
                for feature in layers[0].features
            ]
            parsed = parse_repair_request(request_doc)
            result = rehearse_endpoint_repair(config, layers, parsed)
            self.assertEqual(result.status, "feasible")
            # Loaded objects are immutable views; re-reading yields same bytes.
            self.assertEqual(
                hashlib.sha256((root / "lines.geojson").read_bytes()).hexdigest(),
                source_hash,
            )
            reloaded = load_layers(config)[0].features
            self.assertEqual(
                [
                    json.dumps(feature.geometry.coordinates)
                    for feature in reloaded
                ],
                before_features,
            )
            report = build_rehearsal_report(
                result,
                run_label=config.run_label,
                input_files=tuple(
                    sorted(
                        (layer.input_file for layer in layers),
                        key=lambda item: item.layer_id,
                    )
                ),
            )
            self.assertTrue(report["read_only"])
            self.assertEqual(report["write_back"], "never")
            json.dumps(report)  # fully JSON-serializable


if __name__ == "__main__":
    unittest.main()
