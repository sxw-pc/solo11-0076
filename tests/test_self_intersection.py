"""SELF_INTERSECTION usage contracts and segment-pair evidence tests."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from topoaudit.config import load_config
from topoaudit.errors import ConfigError
from topoaudit.findings import FindingCollector
from topoaudit.geometry_core import (
    classify_segment_pair,
    line_segment_contacts,
)
from topoaudit.model import (
    AuditConfig,
    Bounds,
    CRS,
    Feature,
    Geometry,
    LayerConfig,
    Tolerance,
)
from topoaudit.pipeline import execute
from topoaudit.rule_geometry import check_geometry
from topoaudit.util import canonical_json

EPS = 1e-9


def make_layer(usage="boundary_curve", rules=None):
    return LayerConfig(
        layer_id="lines",
        path="lines.geojson",
        resolved_path=Path("lines.geojson"),
        source_format="geojson",
        geometry_types=("LineString", "MultiLineString"),
        id_field="id",
        schema={},
        rules=rules or {},
        threshold_overrides={},
        declared_crs=None,
        line_usage=usage,
    )


def make_config(epsilon=EPS):
    return AuditConfig(
        path=Path("config.json"),
        config_digest="0" * 64,
        schema_version="1.0",
        run_label="unit",
        tolerance=Tolerance(coordinate_epsilon=epsilon),
        crs=CRS("LOCAL_XY", "planar", Bounds(-1e6, -1e6, 1e6, 1e6)),
        layers=(),
    )


def run_geometry(geometry, usage="boundary_curve", epsilon=EPS, rules=None):
    feature = Feature("lines", "F-1", geometry, {}, 0)
    collector = FindingCollector()
    check_geometry(
        feature, make_layer(usage, rules), make_config(epsilon),
        Tolerance(coordinate_epsilon=epsilon), collector,
    )
    return collector.findings()


def self_intersection_findings(findings):
    return [item for item in findings if item.rule_id == "SELF_INTERSECTION"]


def line(coords):
    return Geometry("LineString", tuple(tuple(p) for p in coords), "LineString")


def multiline(parts):
    return Geometry(
        "MultiLineString",
        tuple(tuple(tuple(p) for p in part) for part in parts),
        "MultiLineString",
    )


class SegmentPairClassificationTest(unittest.TestCase):
    """geometry_core 层：段对类别与证据。"""

    def test_adjacent_retrace_is_collinear_overlap(self):
        # 最小反例：第二段反向重走第一段的 [1,2] 区间。
        contacts = line_segment_contacts(((0, 0), (2, 0), (1, 0)), False, EPS)
        self.assertEqual(len(contacts), 1)
        contact = contacts[0]
        self.assertEqual(contact["segment_indexes"], (0, 1))
        self.assertEqual(contact["relationship"], "adjacent")
        self.assertEqual(contact["category"], "collinear_overlap")
        self.assertEqual(contact["detail"]["overlap"], [[1, 0], [2, 0]])
        self.assertEqual(contact["detail"]["overlap_length"], 1.0)

    def test_normal_polyline_only_shares_legal_endpoints(self):
        contacts = line_segment_contacts(((0, 0), (2, 0), (4, 0), (4, 2)), False, EPS)
        self.assertTrue(contacts)
        for contact in contacts:
            self.assertEqual(contact["category"], "shared_endpoint")
            self.assertEqual(contact["relationship"], "adjacent")

    def test_closed_ring_closure_is_not_a_retrace(self):
        square = ((0, 0), (2, 0), (2, 2), (0, 2), (0, 0))
        contacts = line_segment_contacts(square, True, EPS)
        closure = [c for c in contacts if c["relationship"] == "closure"]
        self.assertEqual(len(closure), 1)
        self.assertEqual(closure[0]["category"], "shared_endpoint")
        self.assertEqual(closure[0]["segment_indexes"], (0, 3))
        for contact in contacts:
            self.assertEqual(contact["category"], "shared_endpoint")

    def test_closed_ring_closure_overlap_is_a_retrace(self):
        # 首尾段除共有端点外还在 [0,2] 上共线重叠。
        contacts = line_segment_contacts(
            ((0, 0), (4, 0), (4, 2), (2, 0), (0, 0)), True, EPS
        )
        closure = [c for c in contacts if c["relationship"] == "closure"]
        self.assertEqual(len(closure), 1)
        self.assertEqual(closure[0]["category"], "collinear_overlap")
        self.assertEqual(closure[0]["detail"]["overlap_length"], 2.0)

    def test_zero_length_segment_does_not_hide_other_pairs(self):
        # 零长度段自身归 ZERO_LENGTH_SEGMENT；它两侧的实质重走仍必须报出。
        contacts = line_segment_contacts(((0, 0), (4, 0), (4, 0), (2, 0)), False, EPS)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["segment_indexes"], (0, 2))
        self.assertEqual(contacts[0]["relationship"], "degenerate_adjacent")
        self.assertEqual(contacts[0]["category"], "collinear_overlap")

    def test_zero_length_between_plain_segments_stays_legal(self):
        contacts = line_segment_contacts(((0, 0), (2, 0), (2, 0), (4, 0)), False, EPS)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["relationship"], "degenerate_adjacent")
        self.assertEqual(contacts[0]["category"], "shared_endpoint")

    def test_point_touch_and_proper_crossing(self):
        category, detail = classify_segment_pair((0, 0), (4, 0), (2, 0), (2, 2), EPS)
        self.assertEqual(category, "point_touch")
        self.assertEqual(detail["point"], [2, 0])
        category, detail = classify_segment_pair((0, 0), (4, 0), (2, -1), (2, 1), EPS)
        self.assertEqual(category, "proper_crossing")
        self.assertEqual(detail["point"], [2.0, 0.0])

    def test_epsilon_boundary_overlap_is_undecidable(self):
        # 重叠 5e-7 小于 coordinate_epsilon=1e-6：点触与正长度重叠无法稳定区分。
        category, detail = classify_segment_pair(
            (0, 0), (4, 0), (3.9999995, 0), (6, 0), 1e-6
        )
        self.assertEqual(category, "undecidable")
        self.assertIn("reason", detail)
        self.assertEqual(detail["overlap_length"], 5e-7)
        # 明确超过容差的共线重叠仍是 collinear_overlap。
        category, _ = classify_segment_pair((0, 0), (4, 0), (3.0, 0), (6, 0), 1e-6)
        self.assertEqual(category, "collinear_overlap")
        # 恰好共享端点（重叠为 0）是合法 shared_endpoint，不可误判。
        category, _ = classify_segment_pair((0, 0), (4, 0), (4, 0), (6, 0), 1e-6)
        self.assertEqual(category, "shared_endpoint")


class UsageContractTest(unittest.TestCase):
    """rule 层：boundary_curve 与 route_walk 两种用途合同。"""

    def test_three_point_backtrack_reported_in_both_usages(self):
        geometry = line([(0, 0), (2, 0), (1, 0)])
        for usage in ("boundary_curve", "route_walk"):
            findings = self_intersection_findings(run_geometry(geometry, usage))
            self.assertEqual(len(findings), 1, usage)
            evidence = findings[0].evidence
            self.assertEqual(evidence["usage"], usage)
            self.assertEqual(evidence["violation_count"], 1)
            pair = evidence["pairs"][0]
            self.assertEqual(pair["parts"], ["parts[0]", "parts[0]"])
            self.assertEqual(pair["segment_indexes"], [0, 1])
            self.assertEqual(pair["relationship"], "adjacent")
            self.assertEqual(pair["category"], "collinear_overlap")
            self.assertTrue(pair["violation"])

    def test_normal_polyline_passes_both_usages(self):
        geometry = line([(0, 0), (2, 0), (4, 0), (4, 2), (2, 2)])
        for usage in ("boundary_curve", "route_walk"):
            self.assertEqual(
                self_intersection_findings(run_geometry(geometry, usage)), [], usage
            )

    def test_closed_loop_passes_both_usages(self):
        geometry = line([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        for usage in ("boundary_curve", "route_walk"):
            self.assertEqual(
                self_intersection_findings(run_geometry(geometry, usage)), [], usage
            )

    def test_non_adjacent_vertex_revisit_differs_by_usage(self):
        # 同一折线在顶点 (0,0) 处非相邻复访，无重走。
        geometry = line([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0), (-1, 0)])
        boundary = self_intersection_findings(run_geometry(geometry, "boundary_curve"))
        self.assertEqual(len(boundary), 1)
        evidence = boundary[0].evidence
        self.assertEqual(evidence["usage"], "boundary_curve")
        categories = {
            (pair["category"], pair["relationship"], pair["violation"])
            for pair in evidence["pairs"]
        }
        self.assertIn(("shared_endpoint", "non_adjacent", True), categories)
        route = self_intersection_findings(run_geometry(geometry, "route_walk"))
        self.assertEqual(route, [])

    def test_point_touch_differs_by_usage(self):
        # T 型点触：一段端点落在另一段内部。
        geometry = line([(0, 0), (4, 0), (4, 2), (2, 0)])
        boundary = self_intersection_findings(run_geometry(geometry, "boundary_curve"))
        self.assertEqual(len(boundary), 1)
        pair = boundary[0].evidence["pairs"][0]
        self.assertEqual(pair["category"], "point_touch")
        self.assertTrue(pair["violation"])
        route = self_intersection_findings(run_geometry(geometry, "route_walk"))
        self.assertEqual(route, [])

    def test_proper_crossing_reported_in_both_usages(self):
        geometry = line([(0, 0), (4, 4), (4, 0), (0, 4)])
        for usage in ("boundary_curve", "route_walk"):
            findings = self_intersection_findings(run_geometry(geometry, usage))
            self.assertEqual(len(findings), 1, usage)
            pair = findings[0].evidence["pairs"][0]
            self.assertEqual(pair["category"], "proper_crossing")
            self.assertEqual(pair["relationship"], "non_adjacent")
            self.assertTrue(pair["violation"])

    def test_undecidable_contact_never_passes_silently(self):
        # 跨 part 共线重叠 5e-7 < coordinate_epsilon=1e-6：不可判定时两种用途都必须报。
        geometry = multiline([[(0, 0), (4, 0)], [(3.9999995, 0), (6, 0)]])
        for usage in ("boundary_curve", "route_walk"):
            findings = self_intersection_findings(
                run_geometry(geometry, usage, epsilon=1e-6)
            )
            self.assertEqual(len(findings), 1, usage)
            pair = findings[0].evidence["pairs"][0]
            self.assertEqual(pair["category"], "undecidable")
            self.assertTrue(pair["violation"])
            self.assertIn("reason", pair["detail"])

    def test_evidence_counts_and_allowed_shared_endpoints(self):
        geometry = line([(0, 0), (2, 0), (1, 0)])
        findings = self_intersection_findings(run_geometry(geometry))
        evidence = findings[0].evidence
        self.assertEqual(evidence["pair_count"], 1)
        self.assertEqual(evidence["violation_count"], 1)
        self.assertEqual(evidence["allowed_shared_endpoint_count"], 0)
        self.assertEqual(evidence["coordinate_epsilon"], EPS)


class CrossPartTest(unittest.TestCase):
    """同一 MultiLineString 不同 part 的段对检查与定位。"""

    def test_cross_part_endpoint_join_is_legal(self):
        geometry = multiline([[(0, 0), (2, 0)], [(2, 0), (2, 2)]])
        for usage in ("boundary_curve", "route_walk"):
            self.assertEqual(
                self_intersection_findings(run_geometry(geometry, usage)), [], usage
            )

    def test_cross_part_point_touch(self):
        geometry = multiline([[(0, 0), (4, 0)], [(2, 0), (2, 2)]])
        boundary = self_intersection_findings(run_geometry(geometry, "boundary_curve"))
        self.assertEqual(len(boundary), 1)
        pair = boundary[0].evidence["pairs"][0]
        self.assertEqual(pair["parts"], ["parts[0]", "parts[1]"])
        self.assertEqual(pair["relationship"], "cross_part")
        self.assertEqual(pair["category"], "point_touch")
        route = self_intersection_findings(run_geometry(geometry, "route_walk"))
        self.assertEqual(route, [])

    def test_cross_part_proper_crossing(self):
        geometry = multiline([[(0, 0), (4, 0)], [(2, -1), (2, 1)]])
        for usage in ("boundary_curve", "route_walk"):
            findings = self_intersection_findings(run_geometry(geometry, usage))
            self.assertEqual(len(findings), 1, usage)
            pair = findings[0].evidence["pairs"][0]
            self.assertEqual(pair["parts"], ["parts[0]", "parts[1]"])
            self.assertEqual(pair["segment_indexes"], [0, 0])
            self.assertEqual(pair["relationship"], "cross_part")
            self.assertEqual(pair["category"], "proper_crossing")

    def test_cross_part_retrace(self):
        geometry = multiline([[(0, 0), (4, 0)], [(3, 0), (1, 0)]])
        for usage in ("boundary_curve", "route_walk"):
            findings = self_intersection_findings(run_geometry(geometry, usage))
            self.assertEqual(len(findings), 1, usage)
            pair = findings[0].evidence["pairs"][0]
            self.assertEqual(pair["relationship"], "cross_part")
            self.assertEqual(pair["category"], "collinear_overlap")
            self.assertEqual(pair["detail"]["overlap"], [[1, 0], [3, 0]])

    def test_cross_part_evidence_is_not_attributed_to_one_part(self):
        geometry = multiline([[(0, 0), (4, 0)], [(2, -1), (2, 1)]])
        findings = self_intersection_findings(run_geometry(geometry))
        self.assertEqual(len(findings), 1)
        evidence = findings[0].evidence
        pair = evidence["pairs"][0]
        # 跨 part 接点必须同时定位两个 part，而不是归入某个 part 内部。
        self.assertEqual(len(set(pair["parts"])), 2)
        self.assertNotIn("location", pair)


class ZeroLengthAndRuleInteractionTest(unittest.TestCase):
    def test_zero_length_segment_rule_preserved(self):
        geometry = line([(0, 0), (2, 0), (2, 0), (4, 0)])
        findings = run_geometry(geometry)
        by_rule = {}
        for finding in findings:
            by_rule.setdefault(finding.rule_id, []).append(finding)
        self.assertIn("ZERO_LENGTH_SEGMENT", by_rule)
        self.assertIn("DUPLICATE_CONSECUTIVE_VERTEX", by_rule)
        self.assertNotIn("SELF_INTERSECTION", by_rule)

    def test_zero_length_does_not_swallow_substantive_overlap(self):
        geometry = line([(0, 0), (4, 0), (4, 0), (2, 0)])
        findings = run_geometry(geometry)
        by_rule = {}
        for finding in findings:
            by_rule.setdefault(finding.rule_id, []).append(finding)
        self.assertIn("ZERO_LENGTH_SEGMENT", by_rule)
        self.assertEqual(len(by_rule.get("SELF_INTERSECTION", [])), 1)
        pair = by_rule["SELF_INTERSECTION"][0].evidence["pairs"][0]
        self.assertEqual(pair["category"], "collinear_overlap")
        self.assertEqual(pair["relationship"], "degenerate_adjacent")


class ConfigSchemaTest(unittest.TestCase):
    def _write_config(self, tmp, layer_extra=None, layers=None):
        layer = {
            "id": "lines",
            "path": "lines.geojson",
            "format": "geojson",
            "geometry_types": ["LineString", "MultiLineString"],
            "id_field": "id",
            "schema": {},
        }
        layer.update(layer_extra or {})
        data = {
            "schema_version": "1.0",
            "run_label": "usage-test",
            "tolerance": {"coordinate_epsilon": 0.000001},
            "crs": {
                "name": "LOCAL_XY",
                "type": "planar",
                "bounds": {"min_x": -100, "min_y": -100, "max_x": 100, "max_y": 100},
            },
            "layers": layers if layers is not None else [layer],
        }
        Path(tmp, "lines.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []}),
            encoding="utf-8",
        )
        path = Path(tmp, "config.json")
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_line_usage_defaults_to_boundary_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(self._write_config(tmp))
            self.assertEqual(config.layers[0].line_usage, "boundary_curve")

    def test_line_usage_explicit_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(self._write_config(tmp, {"line_usage": "route_walk"}))
            self.assertEqual(config.layers[0].line_usage, "route_walk")

    def test_line_usage_rejects_unknown_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, {"line_usage": "anything"})
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
            self.assertEqual(caught.exception.code, "TOPO-CFG-006")
            self.assertIn("line_usage", caught.exception.path)

    def test_line_usage_rejects_non_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_config(tmp, {"line_usage": True})
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
            self.assertEqual(caught.exception.code, "TOPO-CFG-004")


class ReportLedgerConsistencyTest(unittest.TestCase):
    """finding、段对证据与报告/台账一致。"""

    def _run_pipeline(self, tmp, usage):
        features = [
            {
                "type": "Feature",
                "properties": {"id": "L-1"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0, 0], [2, 0], [1, 0]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "L-2"},
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[10, 0], [14, 0]], [[12, -1], [12, 1]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "L-3"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[20, 0], [22, 0], [24, 0]],
                },
            },
        ]
        Path(tmp, "lines.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features}),
            encoding="utf-8",
        )
        config = {
            "schema_version": "1.0",
            "run_label": "self-intersection-ledger",
            "tolerance": {"coordinate_epsilon": 0.000001},
            "crs": {
                "name": "LOCAL_XY",
                "type": "planar",
                "bounds": {"min_x": -100, "min_y": -100, "max_x": 100, "max_y": 100},
            },
            "layers": [
                {
                    "id": "lines",
                    "path": "lines.geojson",
                    "format": "geojson",
                    "geometry_types": ["LineString", "MultiLineString"],
                    "id_field": "id",
                    "schema": {"id": {"type": "string", "required": True}},
                    "line_usage": usage,
                }
            ],
        }
        config_path = Path(tmp, "config.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return execute(config_path, Path(tmp, "out"))

    def test_report_and_ledger_carry_usage_and_pair_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, paths = self._run_pipeline(tmp, "boundary_curve")
            findings = [
                item for item in report["findings"] if item["rule_id"] == "SELF_INTERSECTION"
            ]
            self.assertEqual(len(findings), 2)
            by_feature = {item["feature_id"]: item for item in findings}
            backtrack = by_feature["L-1"]["evidence"]
            self.assertEqual(backtrack["usage"], "boundary_curve")
            self.assertEqual(backtrack["pairs"][0]["category"], "collinear_overlap")
            crossing = by_feature["L-2"]["evidence"]
            self.assertEqual(crossing["pairs"][0]["relationship"], "cross_part")
            self.assertEqual(crossing["pairs"][0]["parts"], ["parts[0]", "parts[1]"])
            # 报告 JSON 落盘内容与内存报告一致。
            written = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(written["findings"], report["findings"])
            # SQLite 台账中的证据与报告逐项一致。
            connection = sqlite3.connect(paths["ledger"])
            try:
                rows = connection.execute(
                    "SELECT finding_id, evidence_json FROM findings "
                    "WHERE rule_id = 'SELF_INTERSECTION' ORDER BY finding_id"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(len(rows), 2)
            ledger_evidence = {row[0]: json.loads(row[1]) for row in rows}
            for item in findings:
                self.assertEqual(
                    ledger_evidence[item["finding_id"]],
                    json.loads(canonical_json(item["evidence"])),
                )
            # Markdown 报告明示用途与类别。
            markdown = paths["markdown"].read_text(encoding="utf-8")
            self.assertIn("boundary_curve", markdown)
            self.assertIn("collinear_overlap", markdown)
            self.assertIn("proper_crossing", markdown)

    def test_same_route_walk_feature_only_fails_on_real_violations(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self._run_pipeline(tmp, "route_walk")
            findings = [
                item for item in report["findings"] if item["rule_id"] == "SELF_INTERSECTION"
            ]
            # L-1 重走与 L-2 穿越在 route_walk 下仍然阻断；L-3 正常折线不报。
            self.assertEqual(
                sorted(item["feature_id"] for item in findings), ["L-1", "L-2"]
            )
            for item in findings:
                self.assertEqual(item["evidence"]["usage"], "route_walk")


if __name__ == "__main__":
    unittest.main()
