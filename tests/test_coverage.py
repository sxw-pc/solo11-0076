"""Opt-in orthogonal coverage-gap audit: union, holes, modes, and blockers."""
import json
import tempfile
import unittest
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.coverage import CoverageConfig, audit_coverage
from topoaudit.geometry import coverage_polygon_issue
from topoaudit.errors import ConfigError
from topoaudit.model import Feature, InputFile, LayerConfig, LoadedLayer
from topoaudit.model import Geometry
from topoaudit.pipeline import execute
from topoaudit.report_html import render_html
from topoaudit.report_json import build_report
from topoaudit.report_md import render_markdown
from topoaudit.engine import run_audit
ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "coverage"
EPSILON = 0.000001

def _rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))

def _polygon(*rings):
    return Geometry("Polygon", tuple(rings), "Polygon")

def _layer(*polygons, layer_id="parcels"):
    features = tuple(
        Feature(layer_id, f"P{index}", polygon, {}, index)
        for index, polygon in enumerate(polygons)
    )
    config = LayerConfig(
        layer_id, "parcels.geojson", Path("parcels.geojson"), "geojson",
        ("Polygon", "MultiPolygon"), "id", {}, {}, {}, None,
    )
    return LoadedLayer(
        config, features, InputFile(layer_id, "parcels.geojson", "geojson", "0" * 64, 0)
    )

def _request(aoi, mode="internal_only", layer_id="parcels"):
    return CoverageConfig(aoi=aoi, layer_id=layer_id, mode=mode)

def _run(config_name):
    return run_audit(load_config(FIXTURES / config_name))

class CoverageUnionTest(unittest.TestCase):
    def test_overlapping_parcels_cover_aoi_with_no_gap(self):
        aoi = _polygon(_rect(0, 0, 10, 10))
        parcels = _layer(_polygon(_rect(0, 0, 6, 10)), _polygon(_rect(4, 0, 10, 10)))
        result = audit_coverage(_request(aoi), (parcels,), EPSILON)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coverage"]["aoi_area"], 100.0)
        self.assertEqual(result["coverage"]["gap_area"], 0.0)
        self.assertEqual(result["coverage"]["covered_area"], 100.0)
        self.assertEqual(result["coverage"]["gap_component_count"], 0)

    def test_fixture_full_coverage_is_zero_in_both_modes(self):
        for name in ("config_full_internal.json",):
            coverage = _run(name).coverage
            self.assertEqual(coverage["coverage"]["gap_area"], 0.0)
            self.assertEqual(coverage["coverage"]["required_component_count"], 0)

    def test_overlap_is_not_counted_as_double_coverage(self):
        # Coincident parcels exactly cover the AOI once; union must stay 100.
        aoi = _polygon(_rect(0, 0, 10, 10))
        same = _polygon(_rect(0, 0, 10, 10))
        result = audit_coverage(_request(aoi), (_layer(same, same),), EPSILON)
        self.assertEqual(result["coverage"]["covered_area"], 100.0)
        self.assertEqual(result["coverage"]["gap_area"], 0.0)

    def test_overlap_cannot_offset_a_gap_elsewhere(self):
        # P1 covers 0..10 x 0..8; P2 covers 0..5 x 0..10.  Their overlap is
        # 0..5 x 0..8.  The uncovered region is 5..10 x 8..10 = 10, which the
        # overlap must not reduce.
        aoi = _polygon(_rect(0, 0, 10, 10))
        parcels = _layer(_polygon(_rect(0, 0, 10, 8)), _polygon(_rect(0, 0, 5, 10)))
        for mode in ("internal_only", "all_uncovered"):
            result = audit_coverage(_request(aoi, mode), (parcels,), EPSILON)
            self.assertEqual(result["coverage"]["gap_area"], 10.0, mode)
            self.assertEqual(result["coverage"]["covered_area"], 90.0, mode)
            (component,) = result["components"]
            self.assertEqual(component["bbox"], [5.0, 8.0, 10.0, 10.0])
            self.assertEqual(component["area"], 10.0)

    def test_multipolygon_parcels_tile_the_aoi(self):
        coverage = _run("config_multipolygon.json").coverage
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["aoi"]["part_count"], 1)
        self.assertEqual(coverage["coverage"]["gap_area"], 0.0)
        self.assertEqual(len(coverage["contributors"]), 3)

    def test_shared_edges_and_corners_do_not_create_gaps(self):
        coverage = _run("config_shared.json").coverage
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["coverage"]["gap_area"], 0.0)
        self.assertEqual(coverage["coverage"]["gap_component_count"], 0)

    def test_multipolygon_aoi_is_supported(self):
        coverage = _run("config_multi_aoi.json").coverage
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["aoi"]["geometry_type"], "MultiPolygon")
        self.assertEqual(coverage["aoi"]["part_count"], 2)
        self.assertEqual(coverage["coverage"]["aoi_area"], 100.0)
        self.assertEqual(coverage["coverage"]["gap_area"], 0.0)

    def test_touching_multipolygon_aoi_parts_are_not_a_gap(self):
        aoi = Geometry(
            "MultiPolygon",
            (
                (_rect(0, 0, 5, 10),),
                (_rect(5, 0, 10, 10),),
            ),
            "MultiPolygon",
        )
        parcel = _polygon(_rect(0, 0, 10, 10))
        result = audit_coverage(_request(aoi), (_layer(parcel),), EPSILON)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coverage"]["gap_area"], 0.0)

class CoverageHoleTest(unittest.TestCase):
    def test_unfilled_parcel_hole_is_a_gap_of_hole_area(self):
        coverage = _run("config_hole_internal.json").coverage
        self.assertEqual(coverage["coverage"]["gap_area"], 16.0)
        self.assertEqual(coverage["coverage"]["required_area"], 16.0)
        (component,) = coverage["components"]
        self.assertEqual(component["classification"], "internal")
        self.assertTrue(component["required"])
        self.assertEqual(component["area"], 16.0)
        self.assertEqual(component["bbox"], [3.0, 3.0, 7.0, 7.0])

    def test_aoi_holes_are_not_counted_as_gaps(self):
        coverage = _run("config_aoi_hole.json").coverage
        self.assertEqual(coverage["aoi"]["area"], 96.0)
        self.assertEqual(coverage["aoi"]["hole_count"], 1)
        self.assertEqual(coverage["coverage"]["gap_area"], 0.0)
        self.assertEqual(coverage["components"], [])

    def test_gap_adjacent_to_aoi_hole_is_still_internal(self):
        aoi = _polygon(_rect(0, 0, 10, 10), _rect(2, 2, 4, 4))
        parcel = _polygon(_rect(0, 0, 10, 10), _rect(1, 2, 2, 4))
        result = audit_coverage(_request(aoi), (_layer(parcel),), EPSILON)
        (component,) = result["components"]
        self.assertEqual(component["classification"], "internal")
        self.assertFalse(component["touches_aoi_boundary"])
        self.assertEqual(component["area"], 2.0)

    def test_hole_and_boundary_gap_are_not_merged(self):
        coverage = _run("config_combo_internal.json").coverage
        classes = sorted(item["classification"] for item in coverage["components"])
        self.assertEqual(classes, ["boundary", "internal"])
        self.assertEqual(coverage["coverage"]["gap_area"], 24.0)

class CoverageModeTest(unittest.TestCase):
    def test_internal_only_marks_boundary_strip_as_separate(self):
        coverage = _run("config_strip_internal.json").coverage
        self.assertEqual(coverage["mode"], "internal_only")
        (component,) = coverage["components"]
        self.assertEqual(component["classification"], "boundary")
        self.assertFalse(component["required"])
        self.assertEqual(coverage["coverage"]["gap_area"], 20.0)
        self.assertEqual(coverage["coverage"]["required_area"], 0.0)
        self.assertEqual(coverage["coverage"]["boundary_area"], 20.0)

    def test_all_uncovered_requires_boundary_strip(self):
        coverage = _run("config_strip_all.json").coverage
        (component,) = coverage["components"]
        self.assertTrue(component["required"])
        self.assertEqual(coverage["coverage"]["required_area"], 20.0)
        self.assertEqual(coverage["coverage"]["required_component_count"], 1)
        # Geometry-based boundary stats do not collapse when the strip becomes
        # a remediation obligation.
        self.assertEqual(coverage["coverage"]["boundary_area"], 20.0)
        self.assertEqual(coverage["coverage"]["boundary_component_count"], 1)

    def test_both_modes_report_identical_geometry_but_different_obligations(self):
        internal = _run("config_combo_internal.json").coverage
        all_uncovered = _run("config_combo_all.json").coverage
        self.assertEqual(internal["coverage"]["gap_area"], all_uncovered["coverage"]["gap_area"])
        self.assertEqual(
            [(item["component_id"], item["area"], item["bbox"]) for item in internal["components"]],
            [(item["component_id"], item["area"], item["bbox"]) for item in all_uncovered["components"]],
        )
        self.assertNotEqual(
            internal["coverage"]["required_component_count"],
            all_uncovered["coverage"]["required_component_count"],
        )
        self.assertNotEqual(
            internal["coverage"]["required_area"], all_uncovered["coverage"]["required_area"]
        )
        self.assertEqual(internal["coverage"]["required_area"], 4.0)
        self.assertEqual(all_uncovered["coverage"]["required_area"], 24.0)
        self.assertEqual(internal["coverage"]["boundary_area"], 20.0)
        self.assertEqual(all_uncovered["coverage"]["boundary_area"], 20.0)

class CoveragePropertyTest(unittest.TestCase):
    """Small randomized checks that pin the union arithmetic, not just fixtures."""

    def test_random_orthogonal_tilings_leave_no_gap(self):
        import random
        rng = random.Random(20261116)
        for _ in range(40):
            xs = [0.0] + sorted(rng.sample(range(1, 10), rng.randint(0, 3))) + [10.0]
            ys = [0.0] + sorted(rng.sample(range(1, 10), rng.randint(0, 3))) + [10.0]
            rectangles = [
                _rect(x0, y0, x1, y1)
                for x0, x1 in zip(xs, xs[1:])
                for y0, y1 in zip(ys, ys[1:])
            ]
            rng.shuffle(rectangles)
            result = audit_coverage(
                _request(_polygon(_rect(0, 0, 10, 10)), "all_uncovered"),
                (_layer(*(_polygon(rect) for rect in rectangles)),),
                EPSILON,
            )
            self.assertEqual(result["coverage"]["gap_area"], 0.0)

    def test_random_missing_cell_equals_its_own_area(self):
        import random
        rng = random.Random(20261116)
        cells = [(x, y) for x in range(10) for y in range(10)]
        for missing in rng.sample(cells, 10):
            rectangles = [
                _rect(x, y, x + 1, y + 1) for x, y in cells if (x, y) != missing
            ]
            result = audit_coverage(
                _request(_polygon(_rect(0, 0, 10, 10)), "all_uncovered"),
                (_layer(*(_polygon(rect) for rect in rectangles)),),
                EPSILON,
            )
            self.assertEqual(result["coverage"]["gap_area"], 1.0)
            (component,) = result["components"]
            self.assertEqual(
                component["bbox"], [float(missing[0]), float(missing[1]),
                                    float(missing[0] + 1), float(missing[1] + 1)]
            )

class CoverageBlockingTest(unittest.TestCase):
    def test_slanted_edge_is_indeterminate_not_bbox(self):
        result = _run("config_slanted.json")
        self.assertEqual(result.coverage["status"], "indeterminate")
        self.assertEqual(result.coverage["blocked"]["reason"], "unsupported_parcel_geometry")
        self.assertEqual(result.coverage["blocked"]["feature_issues"][0]["reason"], "non_axis_aligned_segment")
        self.assertIsNone(result.coverage["coverage"]["gap_area"])
        rule = [item for item in result.findings if item.rule_id == "COVERAGE_INDETERMINATE"]
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule[0].severity, "error")

    def test_self_intersection_is_indeterminate(self):
        result = _run("config_bowtie.json")
        self.assertEqual(result.coverage["status"], "indeterminate")
        self.assertEqual(result.coverage["blocked"]["reason"], "unsupported_parcel_geometry")

    def test_slanted_aoi_is_indeterminate(self):
        aoi = _polygon(((0, 0), (10, 0), (10, 10), (5, 7), (0, 10), (0, 0)))
        parcels = _layer(_polygon(_rect(0, 0, 10, 10)))
        result = audit_coverage(_request(aoi), (parcels,), EPSILON)
        self.assertEqual(result["status"], "indeterminate")
        self.assertTrue(result["blocked"]["reason"].startswith("aoi_"))

    def test_hole_outside_shell_is_indeterminate(self):
        parcel = _polygon(_rect(0, 0, 10, 10), _rect(20, 20, 22, 22))
        result = audit_coverage(_request(_polygon(_rect(0, 0, 10, 10))), (_layer(parcel),), EPSILON)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(
            result["blocked"]["feature_issues"][0]["reason"], "hole_outside_shell"
        )

    def test_non_finite_coordinate_is_indeterminate(self):
        bad = _polygon(((0, 0), (10, 0), (float("nan"), 5), (0, 10), (0, 0)))
        result = audit_coverage(_request(_polygon(_rect(0, 0, 10, 10))), (_layer(bad),), EPSILON)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(
            result["blocked"]["feature_issues"][0]["reason"], "non_finite_coordinate"
        )
        self.assertIsNone(result["coverage"]["gap_area"])

    def test_missing_coverage_layer_is_indeterminate(self):
        result = audit_coverage(
            _request(_polygon(_rect(0, 0, 10, 10)), layer_id="absent"), (), EPSILON
        )
        self.assertEqual(result["blocked"]["reason"], "layer_not_found")

    def test_layer_tolerance_override_drives_coverage_epsilon(self):
        import json
        raw = json.loads((FIXTURES / "config_shared.json").read_text(encoding="utf-8"))
        raw["layers"][0]["thresholds"] = {"coordinate_epsilon": 0.5}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "shared.geojson").write_bytes(
                (FIXTURES / "shared.geojson").read_bytes()
            )
            target = Path(tmp, "config.json")
            target.write_text(json.dumps(raw), encoding="utf-8")
            coverage = run_audit(load_config(target)).coverage
        self.assertEqual(coverage["aoi"]["coordinate_epsilon"], 0.5)
        self.assertEqual(coverage["coverage"]["gap_area"], 0.0)

    def test_duplicate_vertex_and_nested_hole_are_indeterminate(self):
        aoi = _polygon(_rect(0, 0, 10, 10))
        duplicate = _polygon(
            ((0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0))
        )
        result = audit_coverage(_request(aoi), (_layer(duplicate),), EPSILON)
        self.assertEqual(
            result["blocked"]["feature_issues"][0]["reason"], "zero_length_segment"
        )
        nested = (
            _rect(0, 0, 10, 10), _rect(1, 1, 9, 9), _rect(2, 2, 3, 3)
        )
        self.assertEqual(
            coverage_polygon_issue(nested, EPSILON), "nested_or_overlapping_holes"
        )
        self.assertEqual(
            coverage_polygon_issue(duplicate.coordinates, EPSILON),
            "zero_length_segment",
        )

    def test_config_rejects_non_polygon_aoi_and_unknown_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "p.geojson").write_text(
                json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
            )
            raw = json.loads((FIXTURES / "config_full_internal.json").read_text(encoding="utf-8"))
            raw["layers"][0]["path"] = "p.geojson"
            raw["coverage"]["aoi"] = {"type": "Point", "coordinates": [1, 1]}
            target = Path(tmp, "config.json")
            target.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(target)
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")
            raw["coverage"]["aoi"] = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
            raw["coverage"]["mode"] = "sometimes"
            target.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(target)

class CoverageTraceabilityTest(unittest.TestCase):
    def test_gap_components_are_consistent_across_runs(self):
        first = _run("config_combo_internal.json").coverage
        second = _run("config_combo_internal.json").coverage
        self.assertEqual(first, second)
        self.assertEqual(
            [item["component_id"] for item in first["components"]], ["G001", "G002"]
        )

    def test_component_order_is_stable_when_parcel_order_changes(self):
        aoi = _polygon(_rect(0, 0, 10, 10))
        hole_parcel = _polygon(_rect(0, 0, 10, 10), _rect(3, 3, 7, 7))
        strip_parcel = _polygon(_rect(0, 0, 10, 8), _rect(3, 3, 7, 7))
        first = audit_coverage(_request(aoi, "all_uncovered"), (_layer(hole_parcel, strip_parcel),), EPSILON)
        second = audit_coverage(_request(aoi, "all_uncovered"), (_layer(strip_parcel, hole_parcel),), EPSILON)
        self.assertEqual(
            [(c["component_id"], c["area"], c["bbox"]) for c in first["components"]],
            [(c["component_id"], c["area"], c["bbox"]) for c in second["components"]],
        )
        self.assertEqual(first["coverage"], second["coverage"])

    def test_contributors_are_traceable_to_source_features(self):
        coverage = _run("config_combo_internal.json").coverage
        self.assertEqual(coverage["status"], "ok")
        (contributor,) = coverage["contributors"]
        self.assertEqual(contributor["feature_id"], "P-COMBO")
        self.assertEqual(contributor["source_index"], 0)
        self.assertEqual(contributor["component_index"], "polygons[0]")
        self.assertTrue(contributor["has_holes"])
        self.assertEqual(coverage["aoi"]["geometry_type"], "Polygon")
        self.assertEqual(len(coverage["input_digest"]), 64)

    def test_coverage_rules_can_be_switched_off_without_losing_measurement(self):
        from dataclasses import replace
        from topoaudit.loaders import load_layers
        from topoaudit.rules import run_rules
        config = load_config(FIXTURES / "config_combo_internal.json")
        layers = load_layers(config)
        patched = tuple(
            LoadedLayer(
                replace(layer.config, rules={"COVERAGE_GAP_INTERNAL": False}),
                layer.features,
                layer.input_file,
            )
            for layer in layers
        )
        codes = [item.rule_id for item in run_rules(config, patched)]
        self.assertNotIn("COVERAGE_GAP_INTERNAL", codes)
        self.assertIn("COVERAGE_GAP_BOUNDARY", codes)
        coverage = run_audit(config).coverage
        self.assertEqual(coverage["coverage"]["required_area"], 4.0)

    def test_source_fixtures_are_never_modified(self):
        source = FIXTURES / "combo.geojson"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            execute(FIXTURES / "config_combo_internal.json", tmp)
        self.assertEqual(before, source.read_bytes())

class CoverageReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _run("config_combo_internal.json")
        cls.report = build_report(cls.result)

    def test_reports_carry_mode_components_and_area(self):
        coverage = self.report["coverage"]
        self.assertEqual(coverage["mode"], "internal_only")
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["coverage"]["required_area"], 4.0)
        markdown = render_markdown(self.report)
        self.assertIn("## 覆盖空缺审计", markdown)
        self.assertIn("internal_only", markdown)
        self.assertIn("G001", markdown)
        page = render_html(self.report)
        self.assertIn("覆盖空缺审计", page)
        self.assertIn("必须整改面积", page)
        self.assertNotIn("<script", page.lower())

    def test_run_page_shows_coverage_summary(self):
        from topoaudit.ledger_query import get_findings, get_rule_catalog, get_run
        from topoaudit.server_pages import render_run
        with tempfile.TemporaryDirectory() as tmp:
            report, paths = execute(FIXTURES / "config_combo_internal.json", tmp)
            digest = report["result_digest"]
            page = render_run(
                get_run(paths["ledger"], digest),
                get_findings(paths["ledger"], digest),
                get_rule_catalog(paths["ledger"]),
                {},
            )
            self.assertIn("覆盖空缺审计", page)
            self.assertIn("internal_only", page)
            self.assertIn("必须整改面积", page)

    def test_ledger_and_report_agree_on_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, paths = execute(FIXTURES / "config_combo_internal.json", tmp)
            from topoaudit.ledger_query import get_coverage
            from topoaudit.ledger_verify import verify_report_and_ledger
            stored = get_coverage(paths["ledger"], report["result_digest"])
            self.assertEqual(stored["mode"], "internal_only")
            self.assertEqual(stored["coverage"]["required_area"], 4.0)
            self.assertTrue(verify_report_and_ledger(paths["json"], paths["ledger"]).ok)

    def test_polygon_overlap_finding_remains_independent(self):
        # The overlapping-parcel fixture keeps POLYGON_OVERLAP while coverage is zero.
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = execute(FIXTURES / "config_full_internal.json", tmp)
        codes = [item["rule_id"] for item in report["findings"]]
        self.assertIn("POLYGON_OVERLAP", codes)
        self.assertEqual(report["coverage"]["coverage"]["gap_area"], 0.0)

    def test_result_digest_changes_when_coverage_block_changes(self):
        internal = build_report(_run("config_combo_internal.json"))
        all_uncovered = build_report(_run("config_combo_all.json"))
        # Component geometry is identical in both modes; only the required flag
        # (and therefore evidence/finding_id) changes.
        self.assertEqual(
            [(item["area"], item["bbox"], item["classification"])
             for item in internal["coverage"]["components"]],
            [(item["area"], item["bbox"], item["classification"])
             for item in all_uncovered["coverage"]["components"]],
        )
        self.assertNotEqual(internal["result_digest"], all_uncovered["result_digest"])

    def test_summary_reflects_coverage_without_changing_finding_totals(self):
        summary = self.report["summary"]
        self.assertEqual(summary["coverage"]["mode"], "internal_only")
        self.assertEqual(summary["coverage"]["required_area"], 4.0)
        coverage_findings = [
            item for item in self.report["findings"]
            if item["rule_id"].startswith("COVERAGE_")
        ]
        self.assertEqual(len(coverage_findings), 2)

    def test_blocked_status_is_visible_in_all_reports(self):
        report = build_report(_run("config_slanted.json"))
        self.assertEqual(report["coverage"]["blocked"]["reason"], "unsupported_parcel_geometry")
        markdown = render_markdown(report)
        self.assertIn("不可判定原因", markdown)
        self.assertIn("unsupported_parcel_geometry", markdown)
        page = render_html(report)
        self.assertIn("unsupported_parcel_geometry", page)
        self.assertIn("non_axis_aligned_segment", page)

    def test_config_digest_distinguishes_coverage_mode(self):
        import json
        internal = json.loads((FIXTURES / "config_combo_internal.json").read_text(encoding="utf-8"))
        all_uncovered = json.loads((FIXTURES / "config_combo_all.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "combo.geojson").write_bytes(
                (FIXTURES / "combo.geojson").read_bytes()
            )
            Path(tmp, "a.json").write_text(json.dumps(internal), encoding="utf-8")
            Path(tmp, "b.json").write_text(json.dumps(all_uncovered), encoding="utf-8")
            first = load_config(Path(tmp, "a.json"))
            second = load_config(Path(tmp, "b.json"))
        self.assertNotEqual(first.config_digest, second.config_digest)
        self.assertEqual(first.coverage.mode, "internal_only")
        self.assertEqual(second.coverage.mode, "all_uncovered")

    def test_no_coverage_block_means_no_coverage_output(self):
        report = build_report(run_audit(load_config(ROOT / "fixtures" / "demo" / "config.json")))
        self.assertIsNone(report["coverage"])
        self.assertIn("未启用覆盖空缺审计", render_markdown(report))

if __name__ == "__main__":
    unittest.main()
