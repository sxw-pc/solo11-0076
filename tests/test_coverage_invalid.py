"""Invalid polygon sources, CRS conflicts, and coverage config validation."""
import json
import tempfile
import unittest
from pathlib import Path

from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ConfigError

from tests.coverage_helpers import (
    BOWTIE,
    HOLED,
    UNCLOSED,
    _line_feature,
    _polygon_feature,
    _write,
    build_case,
    rule,
    run_coverage,
)

class InvalidPolygonTest(unittest.TestCase):
    def _run(self, polygons, line_coords):
        with tempfile.TemporaryDirectory() as tmp:
            return run_coverage(
                Path(tmp),
                [_line_feature("L", line_coords)],
                polygons,
                rule(),
            )

    def test_self_intersecting_face_near_line_is_undetermined_not_covered(self):
        section, records, findings = self._run(
            [_polygon_feature("BAD", BOWTIE)],
            [[32, 5], [38, 5]],
        )
        record = records["L"]
        self.assertEqual(record["status"], "undetermined")
        self.assertGreater(record["undetermined_length"], 0.0)
        self.assertEqual(record["covered_length"], 0.0)
        self.assertIn("invalid_polygon_source", record["reasons"])
        invalid = record["invalid_polygon_sources"]
        self.assertEqual(invalid[0]["feature_id"], "BAD")
        self.assertIn("rings[0]:ring_self_intersection", invalid[0]["reasons"])
        self.assertIn("L", findings)
        self.assertEqual(findings["L"].evidence["status"], "undetermined")

    def test_unclosed_ring_is_undetermined(self):
        section, records, findings = self._run(
            [_polygon_feature("BAD", UNCLOSED)],
            [[52, 5], [58, 5]],
        )
        self.assertEqual(records["L"]["status"], "undetermined")
        reasons = records["L"]["invalid_polygon_sources"][0]["reasons"]
        self.assertIn("rings[0]:ring_not_closed", reasons)

    def test_degenerate_ring_is_undetermined(self):
        degenerate = [[[30, 0], [40, 0], [30, 0]]]
        section, records, findings = self._run(
            [_polygon_feature("BAD", degenerate)],
            [[31, 0], [38, 0]],
        )
        self.assertEqual(records["L"]["status"], "undetermined")
        reasons = records["L"]["invalid_polygon_sources"][0]["reasons"]
        self.assertIn("rings[0]:ring_degenerate", reasons)

    def test_invalid_face_does_not_poison_independent_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [
                    _line_feature("INSIDE-GOOD", [[1, 5], [9, 5]]),
                    _line_feature("NEAR-BAD", [[31, 5], [39, 5]]),
                ],
                [
                    _polygon_feature("GOOD", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]),
                    _polygon_feature("BAD", BOWTIE),
                ],
                rule(),
            )
            self.assertEqual(records["INSIDE-GOOD"]["status"], "covered")
            self.assertEqual(records["NEAR-BAD"]["status"], "undetermined")
            self.assertNotIn("INSIDE-GOOD", findings)
            self.assertIn("NEAR-BAD", findings)

    def test_polygon_layer_crs_mismatch_makes_whole_rule_undetermined(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[1, 5], [9, 5]])],
                [_polygon_feature("P", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
                rule(),
                polygons_crs={"name": "LOCAL_FEET", "type": "planar"},
            )
            self.assertEqual(section["polygon_layer_crs_reason"], "declared_crs_mismatch")
            self.assertEqual(records["L"]["status"], "undetermined")
            self.assertEqual(records["L"]["covered_length"], 0.0)
            self.assertEqual(records["L"]["undetermined_length"], 8.0)
            self.assertIn("L", findings)
            evidence = findings["L"].evidence
            self.assertEqual(evidence["polygon_layer_crs_reason"], "declared_crs_mismatch")

    def test_covered_line_with_mistrusted_polygon_crs_is_undetermined(self):
        """A line that would be covered must not be 'covered' under CRS conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[1, 5], [9, 5]])],
                [_polygon_feature("P", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
                rule(),
                polygons_crs={"name": "LOCAL_FEET", "type": "planar"},
            )
            record = records["L"]
            self.assertEqual(record["status"], "undetermined")
            self.assertEqual(record["reasons"], ["declared_crs_mismatch"])
            self.assertEqual(record["lengths"]["undetermined"], 8.0)
            self.assertEqual(record["lengths"]["interior"], 0.0)
            for item in record["uncovered_slices"]:
                self.assertEqual(item["kind"], "undetermined")
                self.assertEqual(item["reason"], "declared_crs_mismatch")
            self.assertNotIn("invalid_polygon_source", record["reasons"])

    def test_far_invalid_face_does_not_taint_covered_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[1, 5], [9, 5]])],
                [
                    _polygon_feature("P", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]),
                    _polygon_feature("BAD", BOWTIE),
                ],
                rule(),
            )
            self.assertEqual(records["L"]["status"], "covered")
            self.assertEqual(records["L"]["invalid_polygon_sources"], [])

    def test_tolerance_band_does_not_mask_real_gap(self):
        """A 0.4 gap is real uncovered length under boundary_allowed."""
        with tempfile.TemporaryDirectory() as tmp:
            _, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[-0.4, 5], [5, 5]])],
                [_polygon_feature("P", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
                rule("boundary_allowed", 0.5),
            )
            record = records["L"]
            self.assertEqual(record["status"], "partial")
            self.assertAlmostEqual(record["uncovered_length"], 0.4)
            self.assertAlmostEqual(record["lengths"]["tolerance_exterior"], 0.4)

    def test_invalid_face_does_not_drive_coverage_status_without_reason(self):
        """geometry.py validity must drive rules.py status, not just a warning."""
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[32, 5], [38, 5]])],
                [_polygon_feature("BAD", BOWTIE)],
                rule(),
            )
            self.assertNotEqual(records["L"]["status"], "covered")
            self.assertEqual(records["L"]["covered_length"], 0.0)
            self.assertTrue(records["L"]["reasons"])
            self.assertNotEqual(findings["L"].evidence["status"], "covered")



class ConfigValidationTest(unittest.TestCase):
    def test_boundary_mode_is_mandatory_and_restricted(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = {
                "id": "cov",
                "line_layer": "lines",
                "polygon_layer": "polys",
                "tolerance": 1.0,
            }
            with self.assertRaises(ConfigError) as caught:
                build_case(Path(tmp), [], [], [spec])
            self.assertEqual(caught.exception.code, "TOPO-CFG-004")
            spec["boundary_mode"] = "lenient"
            with self.assertRaises(ConfigError) as caught:
                build_case(Path(tmp), [], [], [spec])
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")
            spec["boundary_mode"] = "strict_interior"
            spec["tolerance"] = -1
            with self.assertRaises(ConfigError) as caught:
                build_case(Path(tmp), [], [], [spec])
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")

    def test_unknown_layer_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = {
                "id": "cov",
                "line_layer": "lines",
                "polygon_layer": "missing",
                "boundary_mode": "strict_interior",
                "tolerance": 1.0,
            }
            with self.assertRaises(ConfigError) as caught:
                build_case(Path(tmp), [], [], [spec])
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")

    def test_same_layer_for_both_sides_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = {
                "id": "cov",
                "line_layer": "lines",
                "polygon_layer": "lines",
                "boundary_mode": "strict_interior",
                "tolerance": 1.0,
            }
            with self.assertRaises(ConfigError) as caught:
                build_case(Path(tmp), [], [], [spec])
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")

    def test_coverage_rule_disabled_by_layer_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp_path := Path(tmp) / "lines.geojson", [_line_feature("L", [[1, 5], [9, 5]])])
            _write(Path(tmp) / "polys.geojson", [_polygon_feature("P", HOLED)])
            data = {
                "schema_version": "1.0",
                "run_label": "coverage-unit",
                "tolerance": {"coordinate_epsilon": 0.000001},
                "crs": {
                    "name": "LOCAL_XY",
                    "type": "planar",
                    "bounds": {"min_x": -100, "min_y": -100, "max_x": 200, "max_y": 100},
                },
                "layers": [
                    {
                        "id": "lines",
                        "path": "lines.geojson",
                        "format": "geojson",
                        "geometry_types": ["LineString"],
                        "id_field": "id",
                        "schema": {"id": {"type": "string"}},
                        "rules": {"LINE_COVERAGE": {"enabled": False}},
                    },
                    {
                        "id": "polys",
                        "path": "polys.geojson",
                        "format": "geojson",
                        "geometry_types": ["Polygon"],
                        "id_field": "id",
                        "schema": {"id": {"type": "string"}},
                    },
                ],
                "coverage_rules": rule(),
            }
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            result = run_audit(load_config(config_path))
            self.assertEqual(result.coverage[0]["enabled"], False)
            self.assertEqual(result.coverage[0]["lines"], [])
            self.assertFalse(
                any(f.rule_id == "LINE_COVERAGE" for f in result.findings)
            )




if __name__ == "__main__":
    unittest.main()
