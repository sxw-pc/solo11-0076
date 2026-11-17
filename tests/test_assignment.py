import unittest

from tests.assignment_kit import (
    AssignmentTestBase,
    base_assignment,
    square,
    zone_multipolygon,
    zone_polygon,
)


class GreedyCounterexampleTest(AssignmentTestBase):
    """P1 只能去 A、P2 可去 A/B、容量各 1：贪心选最近区会让 P1 无人接管。"""

    def test_flexible_point_does_not_orphan_restricted_point(self):
        zones = [
            zone_polygon("Z-A", [square(0, 0, 10, 10)]),
            zone_polygon("Z-B", [square(8, 0, 20, 10)]),
        ]
        # P2 离 Z-A 锚点 (5,5) 更近；贪心会把 Z-A 给 P2。
        points = [("P1", 1, 1), ("P2", 9, 1)]
        assignment = base_assignment({"Z-A": [5, 5], "Z-B": [15, 5]}, capacity=1)
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertEqual(section["status"], "ok")
        self.assertIsNone(section["blocking"])
        candidates = {}
        for item in section["candidates"]:
            candidates.setdefault(item["point_id"], set()).add(item["zone_id"])
        self.assertEqual(candidates["P1"], {"Z-A"})
        self.assertEqual(candidates["P2"], {"Z-A", "Z-B"})
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P1": "Z-A", "P2": "Z-B"}, plan["objective"])
            self.assertEqual(plan["metrics"]["assigned"], 2)
            self.assertEqual(plan["unassigned"], [])
        self.assert_plan_invariants(section, {"Z-A": 1, "Z-B": 1})


class ZoneHoleTest(AssignmentTestBase):
    def test_point_inside_hole_is_not_a_candidate(self):
        zones = [zone_polygon("Z-A", [square(0, 0, 20, 20), square(8, 8, 12, 12)])]
        points = [("P-HOLE", 10, 10), ("P-SOLID", 5, 5)]
        assignment = base_assignment({"Z-A": [2, 2]}, capacity=2)
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        self.assertEqual(section["status"], "ok")
        candidate_points = {item["point_id"] for item in section["candidates"]}
        self.assertNotIn("P-HOLE", candidate_points)
        self.assertIn("P-SOLID", candidate_points)
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P-SOLID": "Z-A"})
            reasons = {item["point_id"]: item["reason"] for item in plan["unassigned"]}
            self.assertEqual(reasons, {"P-HOLE": "no_candidate_zone"})
        self.assert_plan_invariants(section, {"Z-A": 2})


class BoundaryPolicyTest(AssignmentTestBase):
    def _run(self, policy):
        zones = [zone_polygon("Z-A", [square(0, 0, 10, 10)])]
        points = [("P-EDGE", 0, 5), ("P-IN", 5, 5)]
        assignment = base_assignment({"Z-A": [5, 5]}, capacity=2, policy=policy)
        return self.run_precheck(assignment, zones, points).assignment

    def test_boundary_review_policy_defers_point(self):
        section = self._run("review")
        self.assertEqual(section["status"], "ok")
        review = section["review_pairs"]
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["point_id"], "P-EDGE")
        self.assertEqual(review[0]["zone_id"], "Z-A")
        self.assertEqual(review[0]["relation"], "boundary")
        self.assertEqual(review[0]["component"], "polygons[0]")
        candidate_points = {item["point_id"] for item in section["candidates"]}
        self.assertNotIn("P-EDGE", candidate_points)
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P-IN": "Z-A"})
            reasons = {item["point_id"]: item["reason"] for item in plan["unassigned"]}
            self.assertEqual(reasons, {"P-EDGE": "boundary_only_requires_review"})

    def test_boundary_candidate_policy_assigns_point(self):
        section = self._run("candidate")
        boundary = [
            item for item in section["candidates"] if item["point_id"] == "P-EDGE"
        ]
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0]["relation"], "boundary")
        self.assertEqual(section["review_pairs"], [])
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P-EDGE": "Z-A", "P-IN": "Z-A"})
            self.assertEqual(plan["unassigned"], [])


class MultiPolygonTest(AssignmentTestBase):
    def test_components_are_traced_in_candidates(self):
        zones = [
            zone_multipolygon("Z-M", [[square(0, 0, 5, 5)], [square(20, 20, 25, 25)]])
        ]
        points = [("P-C0", 2, 2), ("P-C1", 22, 22), ("P-OUT", 10, 10)]
        assignment = base_assignment({"Z-M": [22, 22]}, capacity=3)
        result = self.run_precheck(assignment, zones, points)
        section = result.assignment
        components = {item["point_id"]: item["component"] for item in section["candidates"]}
        self.assertEqual(components, {"P-C0": "polygons[0]", "P-C1": "polygons[1]"})
        for plan in section["plans"]:
            mapping = {item["point_id"]: item["zone_id"] for item in plan["assignments"]}
            self.assertEqual(mapping, {"P-C0": "Z-M", "P-C1": "Z-M"})
            reasons = {item["point_id"]: item["reason"] for item in plan["unassigned"]}
            self.assertEqual(reasons, {"P-OUT": "no_candidate_zone"})
        self.assert_plan_invariants(section, {"Z-M": 3})

    def test_custom_zone_id_field_traces_feature_id(self):
        zones = [
            zone_polygon("F-001", [square(0, 0, 10, 10)], zone_code="RA-1"),
        ]
        points = [("P1", 5, 5)]
        assignment = base_assignment({"RA-1": [5, 5]}, capacity=1)
        result = self.run_precheck(
            assignment, zones, points, zone_id_field="zone_code"
        )
        section = result.assignment
        self.assertEqual(section["config"]["zone_id_field"], "zone_code")
        zone_record = section["zones"][0]
        self.assertEqual(zone_record["zone_id"], "RA-1")
        self.assertEqual(zone_record["feature_id"], "F-001")
        self.assertEqual(section["candidates"][0]["zone_id"], "RA-1")
        mapping = {
            item["point_id"]: item["zone_id"]
            for item in section["plans"][0]["assignments"]
        }
        self.assertEqual(mapping, {"P1": "RA-1"})



if __name__ == "__main__":
    unittest.main()
