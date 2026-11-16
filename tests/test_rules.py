import re
import unittest
from dataclasses import replace
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.loaders import load_layers
from topoaudit.model import LoadedLayer
from topoaudit.rules import run_rules
from topoaudit.rules_catalog import RULE_IDS
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fixtures" / "demo" / "config.json"

class RuleEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG)
        cls.result = run_audit(cls.config)
        cls.by_code = {}
        for finding in cls.result.findings:
            cls.by_code.setdefault(finding.rule_id, []).append(finding)
    def test_all_rules_covered_by_fixture(self):
        self.assertEqual(set(self.by_code), set(RULE_IDS))
        self.assertEqual(len(self.result.findings), 29)
        self.assertEqual(self.result.summary["total_findings"], 29)
        self.assertEqual(self.result.summary["severity_counts"], {"error": 20, "warning": 8, "info": 1})
    def test_rule_counts(self):
        expected = {
            "SCHEMA_MISSING_FIELD": 3,
            "SCHEMA_TYPE_MISMATCH": 2,
            "DANGLING_ENDPOINT": 3,
            "EMPTY_LAYER": 1,
            "POLYGON_OVERLAP": 1,
            "NEAR_MISS_ENDPOINT": 1,
            "DUPLICATE_CONSECUTIVE_VERTEX": 1,
            "ZERO_LENGTH_SEGMENT": 1,
        }
        for code, count in expected.items():
            self.assertEqual(len(self.by_code[code]), count, code)
    def test_finding_ids_are_stable_prefixes_and_unique(self):
        ids = [finding.finding_id for finding in self.result.findings]
        self.assertEqual(len(ids), len(set(ids)))
        for finding in self.result.findings:
            self.assertRegex(finding.finding_id, re.compile(r"^[0-9a-f]{16}$"))
            self.assertIn(finding.severity, {"error", "warning", "info"})
    def test_evidence_and_related_ids(self):
        duplicate = self.by_code["DUPLICATE_FEATURE_ID"][0]
        self.assertEqual(duplicate.evidence["value"], "P-003")
        overlap = self.by_code["POLYGON_OVERLAP"][0]
        self.assertEqual(overlap.feature_id, "P-007")
        self.assertEqual(overlap.related_feature_ids, ("P-008",))
        near = self.by_code["NEAR_MISS_ENDPOINT"][0]
        self.assertEqual(near.severity, "info")
        self.assertEqual(set((near.feature_id, *near.related_feature_ids)), {"RD-008", "RD-009"})
        for dangling in self.by_code["DANGLING_ENDPOINT"]:
            self.assertEqual(dangling.related_feature_ids, (dangling.evidence["nearest_feature_id"],))
            self.assertNotEqual(dangling.feature_id, dangling.related_feature_ids[0])
        non_finite = self.by_code["COORD_NON_FINITE"][0]
        self.assertEqual(non_finite.evidence["point_count"], 1)
        self.assertIn("coordinate", non_finite.evidence["locations"][0])
    def test_rerun_is_deterministic_and_order_independent(self):
        second = run_audit(self.config)
        self.assertEqual(
            [item.as_dict() for item in self.result.findings],
            [item.as_dict() for item in second.findings],
        )
        layers = load_layers(self.config)
        reversed_layers = tuple(
            LoadedLayer(layer.config, tuple(reversed(layer.features)), layer.input_file)
            for layer in reversed(layers)
        )
        findings = run_rules(self.config, reversed_layers)
        self.assertEqual(
            [item.as_dict() for item in findings],
            [item.as_dict() for item in self.result.findings],
        )
    def test_rule_switches_are_independent(self):
        layers = load_layers(self.config)
        patched = []
        for layer in layers:
            rules = dict(layer.config.rules)
            if layer.config.layer_id == "parcels":
                rules["SLIVER_POLYGON"] = False
            if layer.config.layer_id == "roads":
                rules["LAYER_CRS_MISMATCH"] = False
                rules["NEAR_MISS_ENDPOINT"] = False
            patched.append(LoadedLayer(replace(layer.config, rules=rules), layer.features, layer.input_file))
        codes = [item.rule_id for item in run_rules(self.config, tuple(patched))]
        self.assertNotIn("SLIVER_POLYGON", codes)
        self.assertNotIn("LAYER_CRS_MISMATCH", codes)
        self.assertNotIn("NEAR_MISS_ENDPOINT", codes)
        self.assertEqual(codes.count("DANGLING_ENDPOINT"), 3)
    def test_severity_assignment(self):
        severities = {finding.rule_id: finding.severity for finding in self.result.findings}
        self.assertEqual(severities["COORD_NON_FINITE"], "error")
        self.assertEqual(severities["DUPLICATE_GEOMETRY"], "warning")
        self.assertEqual(severities["NEAR_MISS_ENDPOINT"], "info")
if __name__ == "__main__":
    unittest.main()
