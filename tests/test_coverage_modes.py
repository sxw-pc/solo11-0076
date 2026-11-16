"""Whole-line and boundary-mode coverage tests."""
import tempfile
import unittest
from pathlib import Path

from tests.coverage_helpers import (
    ADJACENT,
    HOLED,
    _line_feature,
    _polygon_feature,
    rule,
    run_coverage,
)

class WholeLineCoverageTest(unittest.TestCase):
    def test_endpoints_inside_but_middle_outside_is_not_covered(self):
        """The five acceptance criteria start here: endpoints are not enough."""
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[1, 5], [9, 5]])],
                [_polygon_feature("P", [[[3, 0], [7, 0], [7, 10], [3, 10], [3, 0]]])],
                rule(),
            )
            record = records["L"]
            self.assertEqual(record["status"], "partial")
            self.assertAlmostEqual(record["uncovered_length"], 4.0)
            self.assertIn("L", findings)
            slices = findings["L"].evidence["uncovered_slices"]
            self.assertEqual(slices[0]["start"], [1.0, 5.0])
            self.assertEqual(slices[-1]["end"], [9.0, 5.0])

    def test_hole_crossing_reports_inside_hole_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[-1, 5], [11, 5]])],
                [_polygon_feature("P", HOLED)],
                rule(),
            )
            record = records["L"]
            self.assertEqual(record["status"], "partial")
            self.assertAlmostEqual(record["lengths"]["boundary_hole"], 0.0)
            # 2 units outside on the left + 2 units through the hole.
            self.assertAlmostEqual(record["uncovered_length"], 4.0)
            self.assertAlmostEqual(record["covered_length"], 8.0)
            hole_slices = [
                item for item in findings["L"].evidence["uncovered_slices"]
                if item["kind"] == "exterior" and item["start"] == [4.0, 5.0]
            ]
            self.assertEqual(len(hole_slices), 1)
            self.assertEqual(hole_slices[0]["end"], [6.0, 5.0])

    def test_line_across_adjacent_polygons_is_continuously_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[2, 2], [18, 2]])],
                [
                    _polygon_feature("P1", HOLED),
                    _polygon_feature("P2", ADJACENT),
                ],
                rule(),
            )
            record = records["L"]
            self.assertEqual(record["status"], "covered")
            self.assertAlmostEqual(record["total_length"], 16.0)
            self.assertAlmostEqual(record["covered_length"], 16.0)
            self.assertNotIn("L", findings)

    def test_multiline_and_multiline_components_are_all_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [
                    _line_feature(
                        "M",
                        [[[1, 5], [9, 5]], [[1, 20], [9, 20]]],
                        kind="MultiLineString",
                    )
                ],
                [_polygon_feature("P", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
                rule(),
            )
            record = records["M"]
            self.assertEqual(record["status"], "partial")
            self.assertAlmostEqual(record["covered_length"], 8.0)
            self.assertAlmostEqual(record["uncovered_length"], 8.0)
            parts = {item["part"] for item in record["uncovered_slices"]}
            self.assertEqual(parts, {"parts[1]"})

    def test_overlapping_polygons_do_not_double_count_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[2, 5], [8, 5]])],
                [
                    _polygon_feature("P1", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]),
                    _polygon_feature("P2", [[[5, 0], [15, 0], [15, 10], [5, 10], [5, 0]]]),
                ],
                rule(),
            )
            record = records["L"]
            self.assertEqual(record["status"], "covered")
            self.assertAlmostEqual(record["total_length"], 6.0)
            self.assertAlmostEqual(record["covered_length"], 6.0)
            for entry in record["segments"][0]["slices"]:
                self.assertLessEqual(entry["length"], 6.0)
            self.assertAlmostEqual(
                sum(entry["length"] for entry in record["segments"][0]["slices"]),
                6.0,
            )



class UnionSemanticsTest(unittest.TestCase):
    def test_hole_in_one_polygon_is_filled_by_another(self):
        """A hole in one face is covered where another face fills it."""
        full = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
        holed = [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[5, 3], [5, 7]])],
                [_polygon_feature("P1", full), _polygon_feature("P2", holed)],
                rule(),
            )
            record = records["L"]
            self.assertEqual(record["status"], "covered")
            self.assertAlmostEqual(record["covered_length"], 4.0)
            self.assertNotIn("L", findings)

    def test_line_along_hole_edge_is_boundary_not_interior(self):
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp),
                [_line_feature("L", [[4, 4], [6, 4]])],
                [_polygon_feature("P", HOLED)],
                rule("strict_interior", 0.5),
            )
            self.assertEqual(records["L"]["status"], "uncovered")
            self.assertAlmostEqual(records["L"]["lengths"]["boundary_hole"], 2.0)
            self.assertEqual(records["L"]["lengths"]["interior"], 0.0)


class BoundaryModeTest(unittest.TestCase):
    def _runs(self):
        lines = [
            _line_feature("OUTER", [[0, 2], [0, 8]]),
            _line_feature("HOLE", [[4, 4], [6, 4]]),
            _line_feature("NEAR", [[-0.4, 5], [0, 5]]),
        ]
        polygons = [
            _polygon_feature(
                "P",
                [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                 [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]],
            )
        ]
        return lines, polygons

    def test_strict_interior_rejects_boundary_length(self):
        lines, polygons = self._runs()
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp), lines, polygons, rule("strict_interior", 0.5)
            )
            self.assertEqual(records["OUTER"]["status"], "uncovered")
            self.assertAlmostEqual(records["OUTER"]["lengths"]["boundary_outer"], 6.0)
            self.assertEqual(records["HOLE"]["status"], "uncovered")
            self.assertAlmostEqual(records["HOLE"]["lengths"]["boundary_hole"], 2.0)
            self.assertEqual(records["NEAR"]["status"], "uncovered")
            self.assertAlmostEqual(records["NEAR"]["lengths"]["tolerance_exterior"], 0.4)
            self.assertIn("OUTER", findings)
            self.assertIn("HOLE", findings)

    def test_boundary_allowed_accepts_only_true_ring_length(self):
        lines, polygons = self._runs()
        with tempfile.TemporaryDirectory() as tmp:
            section, records, findings = run_coverage(
                Path(tmp), lines, polygons, rule("boundary_allowed", 0.5)
            )
            self.assertEqual(records["OUTER"]["status"], "covered")
            self.assertEqual(records["HOLE"]["status"], "covered")
            # The near-outside line was not absorbed by the tolerance.
            self.assertEqual(records["NEAR"]["status"], "uncovered")
            self.assertAlmostEqual(records["NEAR"]["uncovered_length"], 0.4)
            self.assertIn("NEAR", findings)
            self.assertEqual(section["boundary_mode"], "boundary_allowed")

    def test_same_edge_line_changes_verdict_between_modes(self):
        lines, polygons = self._runs()
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            _, strict_records, _ = run_coverage(
                Path(tmp_a), lines, polygons, rule("strict_interior", 0.5)
            )
            _, allow_records, _ = run_coverage(
                Path(tmp_b), lines, polygons, rule("boundary_allowed", 0.5)
            )
            self.assertEqual(strict_records["OUTER"]["status"], "uncovered")
            self.assertEqual(allow_records["OUTER"]["status"], "covered")
            self.assertAlmostEqual(
                strict_records["OUTER"]["lengths"]["boundary_outer"],
                allow_records["OUTER"]["lengths"]["boundary_outer"],
            )




if __name__ == "__main__":
    unittest.main()
