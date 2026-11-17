"""End-to-end tests for the two orthogonal clearance usage contracts.

The same synthetic GeoJSON fixture is audited twice: once under the
``land_compactness`` contract and once under ``access_clearance``.  Both runs
emit identical geometric evidence; only the delivery gate differs.  The tests
also cover report/SQLite ledger persistence, deterministic re-runs, and
strict configuration validation.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ConfigError
from topoaudit.ledger import write_report
from topoaudit.pipeline import execute
from topoaudit.report_json import build_report
from topoaudit.rules_catalog import RULE_CATALOG, RULE_IDS

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "fixtures" / "clearance"
LAND_CONFIG = FIXTURE_DIR / "config_land.json"
ACCESS_CONFIG = FIXTURE_DIR / "config_access.json"

BLOCKING_FEATURES = ("DB-BLOCK", "HOLE-BLOCK")
OK_FEATURES = (
    "DB-EXACT", "DB-ROOMY", "DEAD-END", "HOLE-ROOMY", "PLAIN",
    "POINT-TOUCH", "SEPARATED",
)
UNKNOWN_FEATURES = ("DIAGONAL", "UNCLOSED", "SELF-TOUCH", "HOLE-TOUCH")


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.land = run_audit(load_config(LAND_CONFIG))
        cls.access = run_audit(load_config(ACCESS_CONFIG))

    def _rules_for(self, result, feature_id):
        return sorted(
            finding.rule_id
            for finding in result.findings
            if finding.feature_id == feature_id
        )

    def _evidence(self, result, feature_id):
        matches = [
            finding for finding in result.findings if finding.feature_id == feature_id
        ]
        self.assertEqual(len(matches), 1, feature_id)
        return matches[0].evidence

    def test_new_rules_are_in_catalog_with_fixed_severity(self):
        for rule_id in (
            "BLOCKING_NARROW_NECK",
            "NARROW_NECK_OBSERVATION",
            "CLEARANCE_CONNECTIVITY_OK",
            "CLEARANCE_AUDIT_UNKNOWN",
        ):
            self.assertIn(rule_id, RULE_IDS)
        self.assertEqual(RULE_CATALOG["BLOCKING_NARROW_NECK"].severity, "error")
        self.assertEqual(RULE_CATALOG["NARROW_NECK_OBSERVATION"].severity, "info")
        self.assertEqual(RULE_CATALOG["CLEARANCE_CONNECTIVITY_OK"].severity, "info")
        self.assertEqual(RULE_CATALOG["CLEARANCE_AUDIT_UNKNOWN"].severity, "warning")

    def test_contracts_diverge_only_on_delivery_gate(self):
        for feature_id in BLOCKING_FEATURES:
            self.assertEqual(
                self._rules_for(self.land, feature_id), ["NARROW_NECK_OBSERVATION"]
            )
            self.assertEqual(
                self._rules_for(self.access, feature_id), ["BLOCKING_NARROW_NECK"]
            )

    def test_land_compactness_has_no_blocking_errors(self):
        blocking = [
            finding for finding in self.land.findings
            if finding.rule_id == "BLOCKING_NARROW_NECK"
        ]
        self.assertEqual(blocking, [])

    def test_access_contract_blocks_dumbbell_and_hole(self):
        for feature_id in BLOCKING_FEATURES:
            self.assertEqual(
                self._rules_for(self.access, feature_id), ["BLOCKING_NARROW_NECK"]
            )

    def test_passing_shapes_report_ok_under_both_contracts(self):
        for feature_id in OK_FEATURES:
            self.assertEqual(
                self._rules_for(self.land, feature_id), ["CLEARANCE_CONNECTIVITY_OK"]
            )
            self.assertEqual(
                self._rules_for(self.access, feature_id), ["CLEARANCE_CONNECTIVITY_OK"]
            )

    def test_unsupported_shapes_are_unknown_under_both_contracts(self):
        for feature_id in UNKNOWN_FEATURES:
            land_rules = self._rules_for(self.land, feature_id)
            access_rules = self._rules_for(self.access, feature_id)
            self.assertIn("CLEARANCE_AUDIT_UNKNOWN", land_rules)
            self.assertIn("CLEARANCE_AUDIT_UNKNOWN", access_rules)
            self.assertNotIn("BLOCKING_NARROW_NECK", access_rules)

    def test_dumbbell_evidence_core_areas_and_ratio(self):
        evidence = self._evidence(self.access, "DB-BLOCK")
        self.assertEqual(evidence["status"], "blocking_narrow_neck")
        self.assertEqual(evidence["contract"], "access_clearance")
        self.assertEqual(evidence["clearance_width"], 0.4)
        self.assertEqual(evidence["min_core_area"], 25.0)
        part = evidence["parts"][0]
        self.assertEqual(part["part_index"], "polygons[0]")
        self.assertAlmostEqual(part["area"], 200.4, places=6)
        self.assertAlmostEqual(part["perimeter"], 83.6, places=6)
        self.assertAlmostEqual(part["area_perimeter_ratio"], 2.397129187, places=6)
        self.assertGreater(part["area_perimeter_ratio"], 0.1)
        self.assertEqual(part["core_count"], 2)
        self.assertEqual(part["disconnected_core_pairs"], [[0, 1]])
        self.assertTrue(part["originally_connected_inside_face"])
        neck = part["candidate_neck"]
        self.assertIsNotNone(neck)
        self.assertEqual(neck["core_pair"], [0, 1])
        self.assertAlmostEqual(
            neck["narrowest_constriction"]["clear_width"], 0.2, places=6
        )
        # Boundary evidence references two real face segments and a witness.
        constriction = part["constriction_evidence"][0]
        self.assertEqual(len(constriction["boundary_segment_a"]), 2)
        self.assertEqual(len(constriction["boundary_segment_b"]), 2)
        self.assertEqual(len(constriction["witness_point"]), 2)

    def test_dead_end_is_distinguished_from_bridge(self):
        dead_end = self._evidence(self.access, "DEAD-END")["parts"][0]
        bridge = self._evidence(self.access, "DB-BLOCK")["parts"][0]
        self.assertEqual(dead_end["status"], "pass")
        self.assertEqual(dead_end["core_count"], 1)
        self.assertEqual(dead_end["disconnected_core_pairs"], [])
        self.assertEqual(bridge["status"], "blocking_narrow_neck")
        self.assertEqual(bridge["core_count"], 2)
        # The dead end still surfaces the narrow constriction evidence.
        self.assertTrue(dead_end["constriction_evidence"])

    def test_exact_fit_corridor_passes(self):
        evidence = self._evidence(self.access, "DB-EXACT")
        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(evidence["parts"][0]["core_count"], 1)

    def test_multipolygon_parts_are_not_attributed_to_erosion(self):
        point_touch = self._evidence(self.access, "POINT-TOUCH")
        separated = self._evidence(self.access, "SEPARATED")
        self.assertEqual(point_touch["status"], "pass")
        self.assertEqual(len(point_touch["parts"]), 2)
        for part in point_touch["parts"]:
            self.assertEqual(part["core_count"], 1)
            self.assertEqual(part["disconnected_core_pairs"], [])
        self.assertEqual(len(separated["parts"]), 2)

    def test_unknown_evidence_carries_reason_and_ratio(self):
        evidence = self._evidence(self.access, "DIAGONAL")
        self.assertEqual(evidence["status"], "unknown")
        self.assertEqual(evidence["reason"], "one_or_more_parts_unsupported")
        self.assertEqual(evidence["parts"][0]["reason"], "non_axis_aligned_edge")
        self.assertIn("area_perimeter_ratio", evidence["parts"][0])

    def test_sliver_rule_remains_independent(self):
        # The dumbbell is not a sliver (ratio 2.397 > 0.1) under either run.
        for result in (self.land, self.access):
            slivers = [
                finding for finding in result.findings
                if finding.rule_id == "SLIVER_POLYGON"
            ]
            self.assertEqual(slivers, [])


class DeterminismAndLedgerTest(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        first = build_report(run_audit(load_config(ACCESS_CONFIG)))
        second = build_report(run_audit(load_config(ACCESS_CONFIG)))
        self.assertEqual(first, second)

    def test_report_and_sqlite_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, paths = execute(ACCESS_CONFIG, tmp)
            connection = sqlite3.connect(paths["ledger"])
            try:
                rows = connection.execute(
                    "SELECT rule_id, COUNT(*) FROM findings GROUP BY rule_id"
                ).fetchall()
            finally:
                connection.close()
            counts = dict(rows)
            self.assertEqual(counts.get("BLOCKING_NARROW_NECK"), 2)
            self.assertEqual(counts.get("CLEARANCE_CONNECTIVITY_OK"), len(OK_FEATURES))
            self.assertEqual(counts.get("CLEARANCE_AUDIT_UNKNOWN"), len(UNKNOWN_FEATURES))
            # Idempotent re-write keeps a single run.
            write_report(paths["ledger"], report)
            connection = sqlite3.connect(paths["ledger"])
            try:
                run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                catalog_count = connection.execute(
                    "SELECT COUNT(*) FROM rule_catalog"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(run_count, 1)
            self.assertEqual(catalog_count, len(RULE_IDS))


class ClearanceConfigValidationTest(unittest.TestCase):
    def _base_config(self):
        return json.loads(Path(LAND_CONFIG).read_text(encoding="utf-8"))

    def _write(self, tmp, data, name="config.json"):
        path = Path(tmp, name)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_contract_parses_into_layer_config(self):
        config = load_config(LAND_CONFIG)
        contract = config.layers[0].clearance_contract
        self.assertIsNotNone(contract)
        self.assertEqual(contract.usage_contract, "land_compactness")
        self.assertEqual(contract.clearance_width, 0.4)
        self.assertEqual(contract.min_core_area, 25.0)
        self.assertIn("clearance_contract", config.layers[0].as_dict())

    def test_rejects_unknown_contract_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._base_config()
            data["layers"][0]["clearance_contract"]["usage_contract"] = "flood_zone"
            with self.assertRaises(ConfigError) as caught:
                load_config(self._write(tmp, data))
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")

    def test_rejects_non_positive_width_and_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._base_config()
            data["layers"][0]["clearance_contract"]["clearance_width"] = 0
            with self.assertRaises(ConfigError):
                load_config(self._write(tmp, data))
            data = self._base_config()
            data["layers"][0]["clearance_contract"]["min_core_area"] = -1
            with self.assertRaises(ConfigError):
                load_config(self._write(tmp, data))

    def test_rejects_unknown_contract_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._base_config()
            data["layers"][0]["clearance_contract"]["extra"] = 1
            with self.assertRaises(ConfigError) as caught:
                load_config(self._write(tmp, data))
            self.assertEqual(caught.exception.code, "TOPO-CFG-003")

    def test_layer_without_contract_runs_no_clearance_rules(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(FIXTURE_DIR / "clearance.geojson", Path(tmp) / "clearance.geojson")
            data = self._base_config()
            del data["layers"][0]["clearance_contract"]
            config = load_config(self._write(tmp, data))
            self.assertIsNone(config.layers[0].clearance_contract)
            result = run_audit(config)
            rule_ids = {finding.rule_id for finding in result.findings}
            self.assertNotIn("BLOCKING_NARROW_NECK", rule_ids)
            self.assertNotIn("CLEARANCE_CONNECTIVITY_OK", rule_ids)
            self.assertNotIn("CLEARANCE_AUDIT_UNKNOWN", rule_ids)


if __name__ == "__main__":
    unittest.main()
