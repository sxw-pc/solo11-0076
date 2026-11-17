import json
import tempfile
import unittest
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ReportError
from topoaudit.pipeline import execute
from topoaudit.report_html import render_html
from topoaudit.report_json import build_report, compute_result_digest
from topoaudit.report_md import render_markdown
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fixtures" / "demo" / "config.json"

class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG)
        cls.result = run_audit(cls.config)
        cls.report = build_report(cls.result)
    def test_json_report_shape_and_digest(self):
        for key in (
            "schema_version", "run_label", "input_digest", "result_digest",
            "summary", "layers", "rules", "findings", "input_files", "limitations",
        ):
            self.assertIn(key, self.report)
        self.assertEqual(len(self.report["rules"]), 19)
        self.assertEqual(len(self.report["findings"]), 24)
        self.assertEqual(len(self.report["result_digest"]), 64)
        self.assertEqual(compute_result_digest(self.report), self.report["result_digest"])
        changed = dict(self.report)
        changed["run_label"] = "changed"
        self.assertNotEqual(compute_result_digest(changed), self.report["result_digest"])
    def test_repeated_build_is_identical(self):
        again = build_report(run_audit(load_config(CONFIG)))
        self.assertEqual(again, self.report)
        self.assertEqual(render_markdown(again), render_markdown(self.report))
        self.assertEqual(render_html(again), render_html(self.report))
    def test_all_three_reports_and_input_summary_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, paths = execute(CONFIG, tmp)
            for key in ("json", "markdown", "html", "ledger", "inputs", "summary"):
                self.assertTrue(paths[key].is_file(), key)
            written = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(written["result_digest"], report["result_digest"])
            inputs = json.loads(paths["inputs"].read_text(encoding="utf-8"))
            self.assertEqual(inputs["input_digest"], report["input_digest"])
            self.assertEqual(len(inputs["input_files"]), 4)
            markdown = paths["markdown"].read_text(encoding="utf-8")
            self.assertIn("POLYGON_OVERLAP", markdown)
            self.assertIn(report["result_digest"], markdown)
    def test_html_is_self_contained_and_escaped(self):
        html = render_html(self.report)
        self.assertIn("<style>", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http://", html.lower())
        self.assertNotIn("https://", html.lower())
        self.assertNotIn("<link", html.lower())
        self.assertNotIn("<img", html.lower())
        self.assertIn("Content-Security-Policy", html)
        malicious = dict(self.report)
        malicious["run_label"] = "<script>alert(1)</script>&"
        escaped = render_html(malicious)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;&amp;", escaped)
        self.assertNotIn("<script>alert(1)</script>", escaped)
    def test_output_collision_with_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "report.json")
            original = json.dumps({"type": "FeatureCollection", "features": []})
            source.write_text(original, encoding="utf-8")
            config_path = Path(tmp, "config.json")
            config_path.write_text(json.dumps({
                "schema_version": "1.0", "run_label": "collision", "tolerance": {},
                "crs": {"name": "LOCAL_XY", "type": "planar",
                        "bounds": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1}},
                "layers": [{"id": "l", "path": "report.json", "format": "geojson",
                            "geometry_types": ["Point"], "id_field": "id", "schema": {}}],
            }), encoding="utf-8")
            with self.assertRaises(ReportError) as caught:
                execute(config_path, tmp)
            self.assertEqual(caught.exception.code, "TOPO-RPT-004")
            self.assertEqual(source.read_text(encoding="utf-8"), original)
    def test_markdown_contains_core_sections(self):
        markdown = render_markdown(self.report)
        for text in ("## 运行摘要", "## 图层统计", "## 规则统计", "## Findings", "## 限制说明"):
            self.assertIn(text, markdown)
        self.assertNotIn("http://", markdown)
        self.assertNotIn("https://", markdown)
if __name__ == "__main__":
    unittest.main()
