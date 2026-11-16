"""Coverage report/ledger evidence agreement and deterministic reruns."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from topoaudit.ledger_query import get_coverage, get_coverage_sections
from topoaudit.pipeline import execute
from topoaudit.report_json import compute_result_digest

from tests.coverage_helpers import DEMO_CONFIG, HOLED, UNCLOSED

class ReportLedgerDeterminismTest(unittest.TestCase):
    def test_findings_report_and_ledger_evidence_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            report, paths = execute(DEMO_CONFIG, out)
            section = report["coverage"][0]
            self.assertEqual(section["rule_id"], "routes-in-permit-zones")
            self.assertEqual(
                section["status_counts"],
                {"covered": 4, "partial": 2, "uncovered": 1, "undetermined": 1, "empty": 0},
            )
            line_records = {item["feature_id"]: item for item in section["lines"]}
            coverage_findings = {
                item["feature_id"]: item
                for item in report["findings"]
                if item["rule_id"] == "LINE_COVERAGE"
            }
            for feature_id, finding in coverage_findings.items():
                evidence = finding["evidence"]
                record = line_records[feature_id]
                self.assertEqual(evidence["status"], record["status"])
                self.assertEqual(evidence["uncovered_length"], record["uncovered_length"])
                self.assertEqual(evidence["covered_length"], record["covered_length"])
                self.assertEqual(
                    evidence["undetermined_length"], record["undetermined_length"]
                )
                for slice_evidence in evidence["uncovered_slices"]:
                    self.assertIn(slice_evidence["kind"], {
                        "exterior", "tolerance_exterior", "undetermined",
                        "boundary_outer", "boundary_hole",
                    })
            # Ledger rows are byte-identical to report records.
            ledger_records = {
                item["feature_id"]: item
                for item in get_coverage(paths["ledger"], report["result_digest"])
            }
            self.assertEqual(set(ledger_records), set(line_records))
            for feature_id in line_records:
                self.assertEqual(ledger_records[feature_id], line_records[feature_id])
            sections = get_coverage_sections(paths["ledger"], report["result_digest"])
            self.assertEqual(sections[0], section)
            # Exposed raw coordinates: every uncovered slice has start/end.
            for record in section["lines"]:
                for item in record["uncovered_slices"]:
                    self.assertEqual(len(item["start"]), 2)
                    self.assertEqual(len(item["end"]), 2)
            self.assertEqual(compute_result_digest(report), report["result_digest"])

    def test_repeated_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a"
            second = Path(tmp) / "b"
            report_a, _ = execute(DEMO_CONFIG, first)
            report_b, _ = execute(DEMO_CONFIG, second)
            self.assertEqual(report_a, report_b)
            self.assertEqual(report_a["result_digest"], report_b["result_digest"])
            self.assertEqual(
                (first / "report.json").read_bytes(),
                (second / "report.json").read_bytes(),
            )
            connection = sqlite3.connect(first / "audit.sqlite")
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM coverage_lines").fetchone()[0],
                    8,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM coverage_rules").fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_coverage_finding_ids_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_a, _ = execute(DEMO_CONFIG, Path(tmp) / "a")
            report_b, _ = execute(DEMO_CONFIG, Path(tmp) / "b")
            ids_a = [
                item["finding_id"]
                for item in report_a["findings"]
                if item["rule_id"] == "LINE_COVERAGE"
            ]
            ids_b = [
                item["finding_id"]
                for item in report_b["findings"]
                if item["rule_id"] == "LINE_COVERAGE"
            ]
            self.assertEqual(ids_a, ids_b)
            self.assertEqual(len(ids_a), len(set(ids_a)))



class LedgerTamperTest(unittest.TestCase):
    def test_verify_detects_coverage_ledger_tampering(self):
        from topoaudit.ledger_verify import verify_report_and_ledger

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            report, paths = execute(DEMO_CONFIG, out)
            digest = report["result_digest"]
            connection = sqlite3.connect(paths["ledger"])
            try:
                connection.execute(
                    "UPDATE coverage_lines SET status = 'covered' "
                    "WHERE result_digest = ? AND feature_id = 'RT-001'",
                    (digest,),
                )
                connection.commit()
            finally:
                connection.close()
            result = verify_report_and_ledger(paths["json"], paths["ledger"])
            self.assertFalse(result.ok)
            self.assertTrue(
                any("coverage" in issue for issue in result.issues), result.issues
            )


class GeometryPrimitiveTest(unittest.TestCase):
    def test_polygon_validity_reasons(self):
        from topoaudit.geometry import polygon_validity

        valid, reason = polygon_validity(
            tuple(tuple(map(tuple, ring)) for ring in HOLED), 1e-9
        )
        self.assertTrue(valid)
        self.assertEqual(reason, ())
        valid, issues = polygon_validity(
            tuple(tuple(map(tuple, ring)) for ring in UNCLOSED), 1e-9
        )
        self.assertFalse(valid)
        self.assertEqual(issues, ("rings[0]:ring_not_closed",))

    def test_union_seam_is_interior(self):
        from topoaudit.geometry import union_point_location

        polygons = (
            (((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)),),
            (((10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0), (10.0, 0.0)),),
        )
        location, _ = union_point_location(polygons, (10.0, 5.0), 1e-9, 1e-6)
        self.assertEqual(location, "interior")
        location, _ = union_point_location(polygons, (0.0, 5.0), 1e-9, 1e-6)
        self.assertEqual(location, "boundary_outer")

    def test_split_segment_is_stable(self):
        from topoaudit.geometry import split_segment

        rings = tuple(tuple(map(tuple, ring)) for ring in HOLED)
        params = split_segment((-1.0, 5.0), (11.0, 5.0), rings, 1e-9)
        self.assertEqual(params[0], 0.0)
        self.assertEqual(params[-1], 1.0)
        self.assertEqual(params, sorted(set(params)))


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
