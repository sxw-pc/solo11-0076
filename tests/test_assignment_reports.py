import sqlite3
import unittest

from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ConfigError
from topoaudit.ledger_query import get_assignment
from topoaudit.ledger_verify import verify_report_and_ledger
from topoaudit.pipeline import execute
from topoaudit.report_json import compute_result_digest
from topoaudit.util import canonical_json
from tests.assignment_kit import (
    AssignmentTestBase,
    base_assignment,
    square,
    tradeoff_fixture,
    zone_polygon,
)


class ReportLedgerTest(AssignmentTestBase):
    def test_reports_and_ledger_carry_assignment(self):
        zones, points, assignment = tradeoff_fixture()
        self.write_zones(zones)
        self.write_points(points)
        config_path = self.write_config(assignment)
        report, paths = execute(config_path, self.dir / "out")
        self.assertIn("assignment", report)
        section = report["assignment"]
        self.assertEqual(section["status"], "ok")
        self.assertEqual(report["summary"]["assignment"]["status"], "ok")
        self.assertEqual(compute_result_digest(report), report["result_digest"])
        markdown = paths["markdown"].read_text(encoding="utf-8")
        self.assertIn("责任区分配预检", markdown)
        self.assertIn("min_total_distance", markdown)
        self.assertIn("balance_loads", markdown)
        html_text = paths["html"].read_text(encoding="utf-8")
        self.assertIn("责任区分配预检", html_text)
        self.assertIn("balance_loads", html_text)
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertTrue(verification.ok, verification.issues)
        stored = get_assignment(paths["ledger"], report["result_digest"])
        self.assertIsNotNone(stored)
        self.assertEqual(
            canonical_json({k: v for k, v in stored.items() if k != "plan_metrics"}),
            canonical_json(section),
        )
        self.assertEqual(len(stored["plan_metrics"]), 2)
        connection = sqlite3.connect(paths["ledger"])
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM assignment_plans WHERE result_digest = ?",
                (report["result_digest"],),
            ).fetchone()
            self.assertEqual(row[0], 2)
            row = connection.execute(
                "SELECT status FROM assignment_details WHERE result_digest = ?",
                (report["result_digest"],),
            ).fetchone()
            self.assertEqual(row[0], "ok")
        finally:
            connection.close()

    def test_blocking_is_visible_in_reports_and_ledger(self):
        zones = [
            zone_polygon("Z-A", [square(0, 0, 10, 10)]),
            zone_polygon("Z-B", [square(20, 0, 30, 10)]),
        ]
        points = [("P1", 1, 1), ("P2", 2, 2), ("P3", 25, 5)]
        assignment = base_assignment({"Z-A": [5, 5], "Z-B": [25, 5]}, capacity=1)
        self.write_zones(zones)
        self.write_points(points)
        config_path = self.write_config(assignment)
        report, paths = execute(config_path, self.dir / "out")
        blocking = report["assignment"]["blocking"]
        self.assertEqual(blocking["deficit"], 1)
        self.assertEqual(report["summary"]["assignment"]["blocking_deficit"], 1)
        markdown = paths["markdown"].read_text(encoding="utf-8")
        self.assertIn("无法全量分配的阻断证据", markdown)
        self.assertIn("capacity_blocked", markdown)
        html_text = paths["html"].read_text(encoding="utf-8")
        self.assertIn("无法全量分配的阻断证据", html_text)
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertTrue(verification.ok, verification.issues)
        stored = get_assignment(paths["ledger"], report["result_digest"])
        self.assertEqual(stored["blocking"]["deficient_points"], ["P1", "P2"])


class AssignmentConfigTest(AssignmentTestBase):
    def _prepare(self, assignment):
        self.write_zones([zone_polygon("Z-A", [square(0, 0, 10, 10)])])
        self.write_points([("P1", 5, 5)])
        return self.write_config(assignment)

    def test_unknown_assignment_key_rejected(self):
        path = self._prepare(base_assignment({"Z-A": [5, 5]}, bogus=1))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-003", str(ctx.exception))

    def test_invalid_boundary_policy_rejected(self):
        path = self._prepare(base_assignment({"Z-A": [5, 5]}, policy="sometimes"))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-006", str(ctx.exception))

    def test_zero_capacity_rejected(self):
        path = self._prepare(base_assignment({"Z-A": [5, 5]}, capacity=0))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-006", str(ctx.exception))

    def test_empty_anchors_rejected(self):
        path = self._prepare(base_assignment({}, capacity=1))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-006", str(ctx.exception))

    def test_missing_point_layer_rejected(self):
        spec = base_assignment({"Z-A": [5, 5]})
        spec["point_layer"] = "nope"
        path = self._prepare(spec)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-010", str(ctx.exception))

    def test_missing_zone_layer_rejected(self):
        spec = base_assignment({"Z-A": [5, 5]})
        spec["zone_layer"] = "nope"
        path = self._prepare(spec)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("TOPO-CFG-010", str(ctx.exception))

    def test_unknown_anchor_zone_blocks_precheck(self):
        path = self._prepare(base_assignment({"Z-A": [5, 5], "Z-GHOST": [1, 1]}))
        result = run_audit(load_config(path))
        self.assertEqual(result.assignment["status"], "blocked")
        codes = {issue["code"] for issue in result.assignment["issues"]}
        self.assertIn("UNKNOWN_ZONE_REFERENCE", codes)

    def test_duplicate_point_ids_block_precheck(self):
        self.write_zones([zone_polygon("Z-A", [square(0, 0, 10, 10)])])
        self.write_points([("P1", 1, 1), ("P1", 2, 2)])
        config = load_config(self.write_config(base_assignment({"Z-A": [5, 5]})))
        result = run_audit(config)
        self.assertEqual(result.assignment["status"], "blocked")
        codes = {issue["code"] for issue in result.assignment["issues"]}
        self.assertIn("DUPLICATE_POINT_ID", codes)
        self.assertIn(
            "DUPLICATE_FEATURE_ID", {finding.rule_id for finding in result.findings}
        )



if __name__ == "__main__":
    unittest.main()
