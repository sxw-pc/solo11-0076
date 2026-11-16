import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ConfigError
from topoaudit.ledger_verify import verify_report_and_ledger
from topoaudit.pipeline import execute
from topoaudit.report_json import build_report

SHELL_10 = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE_3_7 = [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]
HOLE_4_6 = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]

def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]

def polygon_feature(fid, rings):
    return {
        "type": "Feature",
        "properties": {"fid": fid},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }

def multipolygon_feature(fid, parts):
    return {
        "type": "Feature",
        "properties": {"fid": fid},
        "geometry": {"type": "MultiPolygon", "coordinates": parts},
    }

class OverlapRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def _write_inputs(self, features, tolerance=None, rules=None, thresholds=None):
        (self.dir / "parcels.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features}),
            encoding="utf-8",
        )
        layer = {
            "id": "parcels",
            "path": "parcels.geojson",
            "format": "geojson",
            "geometry_types": ["Polygon", "MultiPolygon"],
            "id_field": "fid",
            "schema": {"fid": {"type": "string", "required": True}},
        }
        if rules:
            layer["rules"] = rules
        if thresholds:
            layer["thresholds"] = thresholds
        config = {
            "schema_version": "1.0",
            "run_label": "overlap-test",
            "tolerance": tolerance or {"coordinate_epsilon": 0.000001},
            "crs": {
                "name": "LOCAL_XY",
                "type": "planar",
                "bounds": {"min_x": -1000, "min_y": -1000, "max_x": 2000, "max_y": 2000},
            },
            "layers": [layer],
        }
        (self.dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        return self.dir / "config.json"
    def _run(self, features, **kwargs):
        config_path = self._write_inputs(features, **kwargs)
        return run_audit(load_config(config_path))
    @staticmethod
    def _overlaps(result):
        return [f for f in result.findings if f.rule_id == "POLYGON_OVERLAP"]
    @staticmethod
    def _reviews(result):
        return [f for f in result.findings if f.rule_id == "POLYGON_OVERLAP_REVIEW"]
    def test_shared_shell_different_holes_is_reported(self):
        result = self._run([
            polygon_feature("A", [SHELL_10, HOLE_3_7]),
            polygon_feature("B", [SHELL_10, HOLE_4_6]),
        ])
        overlaps = self._overlaps(result)
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(self._reviews(result), [])
        evidence = overlaps[0].evidence
        self.assertEqual(evidence["pair"], ["A", "B"])
        self.assertEqual(evidence["verdict"], "certified_overlap")
        self.assertIn(evidence["certification"], ("vertex_containment", "boundary_refinement"))
        witness = evidence["witness"]
        self.assertEqual(len(witness), 2)
        # The witness must be a locatable common interior point: inside the
        # shared shell and outside both holes.
        self.assertTrue(0 < witness[0] < 10)
        self.assertTrue(0 < witness[1] < 10)
        self.assertFalse(3 < witness[0] < 7 and 3 < witness[1] < 7)
        self.assertFalse(4 < witness[0] < 6 and 4 < witness[1] < 6)
        self.assertEqual(evidence["budget"], {"limit": 64, "consumed": 0})
    def test_shared_edge_pair_is_not_reported(self):
        result = self._run([
            polygon_feature("A", [square(0, 0, 10, 10)]),
            polygon_feature("B", [square(10, 0, 20, 10)]),
        ])
        self.assertEqual(self._overlaps(result), [])
        self.assertEqual(self._reviews(result), [])
    def test_part_inside_hole_is_not_reported(self):
        result = self._run([
            polygon_feature("A", [SHELL_10, HOLE_3_7]),
            polygon_feature("B", [square(4, 4, 5, 5)]),
        ])
        self.assertEqual(self._overlaps(result), [])
        self.assertEqual(self._reviews(result), [])
    def test_multipolygon_disjoint_parts(self):
        result = self._run([
            multipolygon_feature("M", [
                [square(0, 0, 10, 10)],
                [square(100, 0, 110, 10)],
            ]),
            polygon_feature("N", [square(5, 2, 15, 8)]),
        ])
        overlaps = self._overlaps(result)
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0].evidence["pair"], ["M", "N"])
        self.assertEqual(overlaps[0].evidence["component_indexes"], ["polygons[0]", "polygons[0]"])
        self.assertEqual(self._reviews(result), [])
    def test_near_tolerance_tangency_goes_to_review(self):
        result = self._run([
            polygon_feature("A", [square(0, 0, 10, 10)]),
            polygon_feature("B", [square(10 + 1e-8, 0, 20 + 1e-8, 10)]),
        ])
        self.assertEqual(self._overlaps(result), [])
        reviews = self._reviews(result)
        self.assertEqual(len(reviews), 1)
        evidence = reviews[0].evidence
        self.assertEqual(evidence["verdict"], "uncertain")
        self.assertEqual(evidence["reason"], "tolerance_unstable_contact")
        self.assertTrue(evidence["locations"])
        self.assertEqual(evidence["pair"], ["A", "B"])
    def test_unclear_topology_goes_to_review(self):
        tangent_hole = [[2, 2], [8, 2], [8, 10], [2, 10], [2, 2]]
        result = self._run([
            polygon_feature("A", [SHELL_10, tangent_hole]),
            polygon_feature("B", [square(5, 5, 15, 15)]),
        ])
        self.assertEqual(self._overlaps(result), [])
        reviews = self._reviews(result)
        self.assertEqual(len(reviews), 1)
        evidence = reviews[0].evidence
        self.assertEqual(evidence["verdict"], "uncertain")
        self.assertEqual(evidence["reason"], "topology_undefined")
        self.assertTrue(evidence["locations"])
        self.assertIn("issues_a", evidence["raw_evidence"])
    def test_budget_changes_pending_review_to_certified(self):
        shell = square(0, 0, 16, 16)
        hole = [[1.5, 0.4], [14.5, 0.4], [14.5, 12], [1.5, 12], [1.5, 0.4]]
        features = [
            polygon_feature("A", [shell, hole]),
            polygon_feature("B", [shell, hole]),
        ]
        low = self._run(features, tolerance={"coordinate_epsilon": 0.000001, "overlap_budget": 2})
        self.assertEqual(self._overlaps(low), [])
        low_reviews = self._reviews(low)
        self.assertEqual(len(low_reviews), 1)
        self.assertEqual(low_reviews[0].evidence["reason"], "budget_exhausted")
        self.assertEqual(low_reviews[0].evidence["budget"], {"limit": 2, "consumed": 2})
        self.assertTrue(low_reviews[0].evidence["locations"])
        high = self._run(features, tolerance={"coordinate_epsilon": 0.000001, "overlap_budget": 256})
        self.assertEqual(self._reviews(high), [])
        high_overlaps = self._overlaps(high)
        self.assertEqual(len(high_overlaps), 1)
        self.assertEqual(high_overlaps[0].evidence["certification"], "boundary_refinement")
        self.assertEqual(high_overlaps[0].evidence["budget"]["limit"], 256)
        self.assertLessEqual(high_overlaps[0].evidence["budget"]["consumed"], 256)
        layer_override = self._run(features, thresholds={"overlap_budget": 2})
        self.assertEqual(len(self._reviews(layer_override)), 1)
        self.assertEqual(self._reviews(layer_override)[0].evidence["reason"], "budget_exhausted")
    def test_invalid_budget_values_are_rejected(self):
        features = [polygon_feature("A", [square(0, 0, 10, 10)])]
        for bad in (-1, 2.5, "64"):
            config_path = self._write_inputs(
                features, tolerance={"coordinate_epsilon": 0.000001, "overlap_budget": bad}
            )
            with self.assertRaises(ConfigError):
                load_config(config_path)
    def test_rule_switches_are_independent(self):
        features = [
            polygon_feature("A", [SHELL_10, HOLE_3_7]),
            polygon_feature("B", [SHELL_10, HOLE_4_6]),
        ]
        no_overlap_rule = self._run(features, rules={"POLYGON_OVERLAP": {"enabled": False}})
        self.assertEqual(self._overlaps(no_overlap_rule), [])
        self.assertEqual(self._reviews(no_overlap_rule), [])
        tangent_features = [
            polygon_feature("A", [SHELL_10, [[2, 2], [8, 2], [8, 10], [2, 10], [2, 2]]]),
            polygon_feature("B", [square(5, 5, 15, 15)]),
        ]
        no_review_rule = self._run(
            tangent_features, rules={"POLYGON_OVERLAP_REVIEW": {"enabled": False}}
        )
        self.assertEqual(self._reviews(no_review_rule), [])
    def test_report_ledger_consistency_and_input_immutability(self):
        features = [
            polygon_feature("A", [SHELL_10, HOLE_3_7]),
            polygon_feature("B", [SHELL_10, HOLE_4_6]),
            polygon_feature("C", [square(20, 0, 30, 10)]),
            polygon_feature("D", [square(30 + 1e-8, 0, 40 + 1e-8, 10)]),
        ]
        config_path = self._write_inputs(features)
        before = hashlib.sha256((self.dir / "parcels.geojson").read_bytes()).hexdigest()
        report, paths = execute(config_path, self.dir / "out")
        after = hashlib.sha256((self.dir / "parcels.geojson").read_bytes()).hexdigest()
        self.assertEqual(before, after)
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertTrue(verification.ok, verification.issues)
        written = json.loads(paths["json"].read_text(encoding="utf-8"))
        pairs = {
            item["rule_id"]: item
            for item in written["findings"]
            if item["rule_id"] in ("POLYGON_OVERLAP", "POLYGON_OVERLAP_REVIEW")
        }
        self.assertEqual(pairs["POLYGON_OVERLAP"]["evidence"]["pair"], ["A", "B"])
        self.assertEqual(pairs["POLYGON_OVERLAP"]["evidence"]["verdict"], "certified_overlap")
        self.assertEqual(pairs["POLYGON_OVERLAP_REVIEW"]["evidence"]["pair"], ["C", "D"])
        self.assertEqual(pairs["POLYGON_OVERLAP_REVIEW"]["evidence"]["verdict"], "uncertain")
        connection = sqlite3.connect(paths["ledger"])
        try:
            rows = connection.execute(
                "SELECT rule_id, evidence_json FROM findings WHERE rule_id LIKE 'POLYGON%'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual({row[0] for row in rows}, {"POLYGON_OVERLAP", "POLYGON_OVERLAP_REVIEW"})
        for rule_id, evidence_json in rows:
            evidence = json.loads(evidence_json)
            self.assertEqual(evidence["verdict"], pairs[rule_id]["evidence"]["verdict"])
            self.assertEqual(evidence["pair"], pairs[rule_id]["evidence"]["pair"])
    def test_repeated_runs_are_deterministic(self):
        features = [
            polygon_feature("A", [SHELL_10, HOLE_3_7]),
            polygon_feature("B", [SHELL_10, HOLE_4_6]),
            polygon_feature("C", [square(20, 0, 30, 10)]),
            polygon_feature("D", [square(30 + 1e-8, 0, 40 + 1e-8, 10)]),
        ]
        first = self._run(features)
        second = self._run(features)
        self.assertEqual(
            [item.as_dict() for item in first.findings],
            [item.as_dict() for item in second.findings],
        )
        config_path = self._write_inputs(features)
        report_a = build_report(run_audit(load_config(config_path)))
        report_b = build_report(run_audit(load_config(config_path)))
        self.assertEqual(report_a["result_digest"], report_b["result_digest"])
if __name__ == "__main__":
    unittest.main()
