import unittest

from topoaudit.assignment import run_assignment_precheck
from topoaudit.config import load_config
from topoaudit.loaders import load_layers
from topoaudit.model import LoadedLayer
from topoaudit.util import canonical_json
from tests.assignment_kit import (
    AssignmentTestBase,
    base_assignment,
    square,
    tradeoff_fixture,
    zone_polygon,
)


class InfeasibleWitnessTest(AssignmentTestBase):
    def test_capacity_shortage_has_reviewable_witness(self):
        zones = [
            zone_polygon("Z-A", [square(0, 0, 10, 10)]),
            zone_polygon("Z-B", [square(20, 0, 30, 10)]),
        ]
        points = [("P1", 1, 1), ("P2", 2, 2), ("P3", 25, 5)]
        assignment = base_assignment({"Z-A": [5, 5], "Z-B": [25, 5]}, capacity=1)
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertEqual(section["status"], "ok")
        blocking = section["blocking"]
        self.assertIsNotNone(blocking)
        self.assertEqual(blocking["reason"], "insufficient_capacity_or_coverage")
        self.assertEqual(blocking["deficient_points"], ["P1", "P2"])
        self.assertEqual(
            blocking["candidate_zones"], [{"zone_id": "Z-A", "capacity": 1}]
        )
        self.assertEqual(blocking["demand"], 2)
        self.assertEqual(blocking["total_capacity"], 1)
        self.assertEqual(blocking["deficit"], 1)
        blocking_reasons = {
            item["point_id"]: item["reason"] for item in blocking["unassigned_points"]
        }
        self.assertEqual(blocking_reasons, {"P1": "capacity_blocked"})
        for plan in section["plans"]:
            self.assertEqual(plan["metrics"]["assigned"], 2)
            self.assertEqual(len(plan["unassigned"]), 1)
            self.assertEqual(plan["unassigned"][0]["reason"], "capacity_blocked")
        nearest = [
            plan for plan in section["plans"]
            if plan["objective"] == "min_total_distance"
        ][0]
        mapping = {item["point_id"]: item["zone_id"] for item in nearest["assignments"]}
        self.assertEqual(mapping, {"P2": "Z-A", "P3": "Z-B"})
        self.assertEqual(nearest["unassigned"][0]["point_id"], "P1")
        self.assert_plan_invariants(section, {"Z-A": 1, "Z-B": 1})

    def test_zone_capacity_override(self):
        zones = [
            zone_polygon("Z-A", [square(0, 0, 10, 10)]),
            zone_polygon("Z-B", [square(20, 0, 30, 10)]),
        ]
        points = [("P1", 25, 5), ("P2", 26, 5)]
        assignment = base_assignment(
            {"Z-A": [5, 5], "Z-B": [25, 5]},
            capacity=1,
            zone_capacities={"Z-B": 2},
        )
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertIsNone(section["blocking"])
        capacities = {zone["zone_id"]: zone["capacity"] for zone in section["zones"]}
        self.assertEqual(capacities, {"Z-A": 1, "Z-B": 2})
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P1": "Z-B", "P2": "Z-B"})
        self.assert_plan_invariants(section, {"Z-A": 1, "Z-B": 2})


class ObjectiveTradeoffTest(AssignmentTestBase):
    def test_distance_and_balance_plans_are_both_kept(self):
        zones, points, assignment = tradeoff_fixture()
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertEqual(section["status"], "ok")
        self.assertIsNone(section["blocking"])
        plans = {plan["objective"]: plan for plan in section["plans"]}
        self.assertEqual(set(plans), {"min_total_distance", "balance_loads"})
        nearest = plans["min_total_distance"]
        balanced = plans["balance_loads"]
        # 锚点总距离优先：四个点全部交给更近的 Z-A。
        self.assertEqual(nearest["metrics"]["zone_loads"], {"Z-A": 4, "Z-B": 0})
        self.assertEqual(nearest["metrics"]["total_distance"], 5.0)
        self.assertEqual(nearest["metrics"]["load_sum_squares"], 16)
        # 负载均衡优先：2/2 分摊，出勤总距离变长。
        self.assertEqual(balanced["metrics"]["zone_loads"], {"Z-A": 2, "Z-B": 2})
        self.assertEqual(balanced["metrics"]["total_distance"], 14.0)
        self.assertEqual(balanced["metrics"]["load_sum_squares"], 8)
        balanced_map = {
            item["point_id"]: item["zone_id"] for item in balanced["assignments"]
        }
        self.assertEqual(
            balanced_map, {"P1": "Z-A", "P2": "Z-A", "P3": "Z-B", "P4": "Z-B"}
        )
        # 两条方案互不支配，且都守住唯一归属与容量。
        self.assertLess(
            nearest["metrics"]["total_distance"],
            balanced["metrics"]["total_distance"],
        )
        self.assertLess(
            balanced["metrics"]["load_sum_squares"],
            nearest["metrics"]["load_sum_squares"],
        )
        for plan in plans.values():
            self.assertTrue(plan["non_dominated"])
            self.assertEqual(plan["metrics"]["assigned"], 4)
            self.assertEqual(plan["unassigned"], [])
        self.assert_plan_invariants(section, {"Z-A": 4, "Z-B": 4})


class CrsConflictTest(AssignmentTestBase):
    def test_crs_conflict_blocks_assignment_but_keeps_findings(self):
        zones = [zone_polygon("Z-A", [square(0, 0, 10, 10)])]
        points = [("P1", 5, 5)]
        assignment = base_assignment({"Z-A": [5, 5]}, capacity=1)
        result = self.run_precheck(
            assignment,
            zones,
            points,
            declared_crs={"name": "OTHER_CRS", "type": "planar"},
        )
        section = result.assignment
        self.assertEqual(section["status"], "blocked")
        codes = {issue["code"] for issue in section["issues"]}
        self.assertIn("LAYER_CRS_CONFLICT", codes)
        self.assertEqual(section["plans"], [])
        self.assertEqual(section["candidates"], [])
        # 单层几何 findings 不被覆盖：CRS 不一致仍然按既有规则报告。
        self.assertIn("LAYER_CRS_MISMATCH", {f.rule_id for f in result.findings})


class ExclusionTest(AssignmentTestBase):
    def test_invalid_zone_and_nonfinite_point_are_loudly_excluded(self):
        unclosed = [[0, 0], [10, 0], [10, 10], [0, 10]]
        zones = [
            zone_polygon("Z-BAD", [unclosed]),
            zone_polygon("Z-OK", [square(20, 20, 30, 30)]),
        ]
        points = [("P-IN-BAD", 5, 5), ("P-NAN", "nan", 5), ("P-IN-OK", 25, 25)]
        assignment = base_assignment({"Z-BAD": [5, 5], "Z-OK": [25, 25]}, capacity=2)
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertEqual(section["status"], "ok")
        zones_by_id = {zone["zone_id"]: zone for zone in section["zones"]}
        self.assertEqual(zones_by_id["Z-BAD"]["status"], "excluded")
        self.assertEqual(zones_by_id["Z-BAD"]["exclusion_reason"], "invalid_polygon")
        self.assertEqual(zones_by_id["Z-OK"]["status"], "valid")
        points_by_id = {point["point_id"]: point for point in section["points"]}
        self.assertEqual(points_by_id["P-NAN"]["status"], "excluded")
        self.assertEqual(
            points_by_id["P-NAN"]["exclusion_reason"], "non_finite_coordinate"
        )
        for item in section["candidates"]:
            self.assertNotEqual(item["zone_id"], "Z-BAD")
            self.assertNotEqual(item["point_id"], "P-NAN")
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P-IN-OK": "Z-OK"})
            reasons = {item["point_id"]: item["reason"] for item in plan["unassigned"]}
            self.assertEqual(reasons["P-IN-BAD"], "no_candidate_zone")
            self.assertEqual(reasons["P-NAN"], "non_finite_coordinate")
        # 既有单层几何 findings 仍然产生。
        rule_ids = {finding.rule_id for finding in result.findings}
        self.assertIn("RING_NOT_CLOSED", rule_ids)
        self.assertIn("COORD_NON_FINITE", rule_ids)
        self.assert_plan_invariants(section, {"Z-OK": 2})


class OrderIndependenceTest(AssignmentTestBase):
    def test_feature_order_does_not_change_candidates_or_results(self):
        zones = [
            zone_polygon("Z-A", [square(0, 0, 10, 10)]),
            zone_polygon("Z-B", [square(5, 0, 15, 10)]),
        ]
        points = [("P1", 6.5, 1), ("P2", 7, 1), ("P3", 7.5, 1), ("P4", 8, 1)]
        assignment = base_assignment({"Z-A": [6, 1], "Z-B": [14, 1]}, capacity=4)
        self.write_zones(zones)
        self.write_points(points)
        config = load_config(self.write_config(assignment))
        layers = load_layers(config)
        first = run_assignment_precheck(config, layers)
        reversed_layers = tuple(
            LoadedLayer(layer.config, tuple(reversed(layer.features)), layer.input_file)
            for layer in reversed(layers)
        )
        second = run_assignment_precheck(config, reversed_layers)
        for key in ("status", "issues", "candidates", "review_pairs", "plans", "blocking"):
            self.assertEqual(first[key], second[key], key)

        def strip_indexes(records):
            return [
                {key: value for key, value in record.items() if key != "source_index"}
                for record in records
            ]

        self.assertEqual(strip_indexes(first["zones"]), strip_indexes(second["zones"]))
        self.assertEqual(strip_indexes(first["points"]), strip_indexes(second["points"]))
        # 重复运行结果完全一致。
        again = run_assignment_precheck(config, layers)
        self.assertEqual(canonical_json(first), canonical_json(again))



if __name__ == "__main__":
    unittest.main()
