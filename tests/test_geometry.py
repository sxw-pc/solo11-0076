import math
import unittest
from topoaudit.geometry import (
    area_perimeter_ratio,
    bbox_of_points,
    bbox_intersection,
    consecutive_duplicates,
    distance,
    is_finite_point,
    legacy_polygons_overlap,
    line_self_intersections,
    point_in_polygon,
    point_location,
    polygon_area,
    polygon_perimeter,
    polygons_overlap,
    ring_closed,
    ring_signed_area,
    segments_intersect,
    zero_length_segments,
)
from topoaudit.geometry_overlap import (
    VERDICT_CERTIFIED_DISJOINT,
    VERDICT_CERTIFIED_OVERLAP,
    VERDICT_UNCERTAIN,
    polygon_overlap_verdict,
    polygon_topology_issues,
)
from topoaudit.model import Geometry
from topoaudit.normalize import canonical_geometry, finite_bbox
SQUARE = (((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),)
HOLE = (
    ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),
    ((3, 3), (7, 3), (7, 7), (3, 7), (3, 3)),
)

def _square(x0, y0, x1, y1):
    return (((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)),)

_SHELL_10 = ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))
_HOLE_3_7 = ((3, 3), (7, 3), (7, 7), (3, 7), (3, 3))
_HOLE_4_6 = ((4, 4), (6, 4), (6, 6), (4, 6), (4, 4))
_HOLE_2_8 = ((2, 2), (8, 2), (8, 8), (2, 8), (2, 2))

class GeometryTest(unittest.TestCase):
    def test_finite_and_distance(self):
        self.assertTrue(is_finite_point((1, 2.5)))
        self.assertFalse(is_finite_point((1, math.nan)))
        self.assertFalse(is_finite_point((True, 1)))
        self.assertEqual(distance((0, 0), (3, 4)), 5.0)
    def test_intersection_predicates(self):
        self.assertTrue(segments_intersect((0, 0), (2, 2), (0, 2), (2, 0)))
        self.assertTrue(segments_intersect((0, 0), (2, 0), (2, 0), (4, 0)))
        self.assertFalse(segments_intersect((0, 0), (1, 0), (0, 1), (1, 1)))
        self.assertTrue(segments_intersect((0, 0), (2, 0), (1, 0), (3, 0)))
    def test_ring_and_segment_checks(self):
        ring = ((0, 0), (2, 0), (2, 2), (0, 2), (0, 0))
        self.assertTrue(ring_closed(ring, 0))
        self.assertFalse(ring_closed(ring[:-1], 0))
        self.assertEqual(consecutive_duplicates(((0, 0), (0, 0), (1, 1)), 0), [1])
        self.assertEqual(zero_length_segments(((0, 0), (0, 0), (1, 1)), 0), [0])
    def test_self_intersection(self):
        bow = ((0, 0), (2, 2), (0, 2), (2, 0))
        self.assertTrue(line_self_intersections(bow, False, 0))
        square = ((0, 0), (2, 0), (2, 2), (0, 2), (0, 0))
        self.assertEqual(line_self_intersections(square, True, 0), [])
    def test_point_in_polygon_and_holes(self):
        self.assertEqual(point_location(SQUARE[0], (5, 5)), "inside")
        self.assertEqual(point_location(SQUARE[0], (0, 5)), "boundary")
        self.assertEqual(point_location(SQUARE[0], (15, 5)), "outside")
        self.assertEqual(point_in_polygon(SQUARE, (1, 1)), "inside")
        self.assertEqual(point_in_polygon(HOLE, (1, 1)), "inside")
        self.assertEqual(point_in_polygon(HOLE, (5, 5)), "outside")
        self.assertEqual(point_in_polygon(HOLE, (3, 5)), "boundary")
    def test_area_perimeter_bbox(self):
        self.assertEqual(abs(ring_signed_area(SQUARE[0])), 100.0)
        self.assertEqual(polygon_area(SQUARE), 100.0)
        self.assertEqual(polygon_perimeter(SQUARE), 40.0)
        self.assertEqual(area_perimeter_ratio(10, 100), 0.1)
        self.assertEqual(bbox_of_points([(1, 2), (3, 0)]), (1.0, 0.0, 3.0, 2.0))
        self.assertEqual(bbox_intersection((0, 0, 2, 2), (1, 1, 3, 3)), [1, 1, 2, 2])
        self.assertIsNone(bbox_intersection((0, 0, 1, 1), (2, 2, 3, 3)))
    def test_polygon_overlap(self):
        crossing = ((((5, 5), (15, 5), (15, 15), (5, 15), (5, 5))),)
        touching = ((((10, 0), (20, 0), (20, 10), (10, 10), (10, 0))),)
        contained = ((((2, 2), (4, 2), (4, 4), (2, 4), (2, 2))),)
        disjoint = ((((20, 20), (30, 20), (30, 30), (20, 30), (20, 20))),)
        self.assertTrue(polygons_overlap(SQUARE, crossing))
        self.assertFalse(polygons_overlap(SQUARE, touching))
        self.assertTrue(polygons_overlap(SQUARE, contained))
        self.assertFalse(polygons_overlap(SQUARE, disjoint))
        self.assertTrue(polygons_overlap(SQUARE, SQUARE))
    def test_finite_bbox_ignores_nan(self):
        self.assertEqual(finite_bbox(((0, 0), (math.nan, 5), (3, 4))), (0.0, 0.0, 3.0, 4.0))
    def test_canonical_geometry(self):
        line = Geometry("LineString", ((0, 0), (5, 5), (10, 0)), "LineString")
        reverse = Geometry("LineString", ((10, 0), (5, 5), (0, 0)), "LineString")
        self.assertEqual(canonical_geometry(line, 1e-6), canonical_geometry(reverse, 1e-6))
        ring_one = (((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),)
        ring_two = (((10, 10), (0, 10), (0, 0), (10, 0), (10, 10)),)
        self.assertEqual(
            canonical_geometry(Geometry("Polygon", ring_one, "Polygon"), 1e-6),
            canonical_geometry(Geometry("Polygon", ring_two, "Polygon"), 1e-6),
        )
        near = Geometry("Point", (1.0000004, 2), "Point")
        exact = Geometry("Point", (1, 2), "Point")
        self.assertEqual(canonical_geometry(near, 1e-6), canonical_geometry(exact, 1e-6))
        unsupported = Geometry("Unsupported", (), None)
        self.assertIsNone(canonical_geometry(unsupported, 1e-6))

class OverlapVerdictTest(unittest.TestCase):
    """Three-valued certified overlap verdicts with evidence."""
    def test_shared_shell_different_holes_counterexample(self):
        # A and B share the 0..10 square shell; A's hole is 3..7, B's is 4..6.
        # They share the wide annular interior, but every shell vertex lies on
        # the other shell and the shell centroid falls inside both holes.
        a = (_SHELL_10, _HOLE_3_7)
        b = (_SHELL_10, _HOLE_4_6)
        self.assertFalse(legacy_polygons_overlap(a, b, 1e-9))  # documented old miss
        verdict = polygon_overlap_verdict(a, b, 1e-9, 64)
        self.assertEqual(verdict.verdict, VERDICT_CERTIFIED_OVERLAP)
        self.assertIsNotNone(verdict.witness)
        self.assertEqual(point_in_polygon(a, verdict.witness, 1e-9), "inside")
        self.assertEqual(point_in_polygon(b, verdict.witness, 1e-9), "inside")
        self.assertTrue(polygons_overlap(a, b, 1e-9))
        reverse = polygon_overlap_verdict(b, a, 1e-9, 64)
        self.assertEqual(reverse.verdict, VERDICT_CERTIFIED_OVERLAP)
    def test_hole_contains_hole_both_directions(self):
        smaller_hole = (_SHELL_10, _HOLE_3_7)
        larger_hole = (_SHELL_10, _HOLE_2_8)
        for a, b in ((smaller_hole, larger_hole), (larger_hole, smaller_hole)):
            verdict = polygon_overlap_verdict(a, b, 1e-9, 64)
            self.assertEqual(verdict.verdict, VERDICT_CERTIFIED_OVERLAP)
            self.assertIsNotNone(verdict.witness)
    def test_identical_holed_polygons_overlap(self):
        holed = (_SHELL_10, _HOLE_3_7)
        verdict = polygon_overlap_verdict(holed, holed, 1e-9, 64)
        self.assertEqual(verdict.verdict, VERDICT_CERTIFIED_OVERLAP)
        self.assertEqual(verdict.reason, "boundary_refinement")
        self.assertEqual(point_in_polygon(holed, verdict.witness, 1e-9), "inside")
    def test_part_inside_hole_is_certified_disjoint(self):
        holed = (_SHELL_10, _HOLE_3_7)
        inside_hole = _square(4, 4, 5, 5)
        verdict = polygon_overlap_verdict(holed, inside_hole, 1e-9, 64)
        self.assertEqual(verdict.verdict, VERDICT_CERTIFIED_DISJOINT)
        self.assertFalse(polygons_overlap(holed, inside_hole, 1e-9))
        covering_hole = _square(2, 2, 8, 8)
        verdict = polygon_overlap_verdict(holed, covering_hole, 1e-9, 64)
        self.assertEqual(verdict.verdict, VERDICT_CERTIFIED_OVERLAP)
    def test_shared_edge_and_point_touch_are_disjoint(self):
        edge = polygon_overlap_verdict(_square(0, 0, 10, 10), _square(10, 0, 20, 10), 1e-9, 64)
        self.assertEqual(edge.verdict, VERDICT_CERTIFIED_DISJOINT)
        self.assertEqual(edge.reason, "boundary_touch_only")
        corner = polygon_overlap_verdict(_square(0, 0, 10, 10), _square(10, 10, 20, 20), 1e-9, 64)
        self.assertEqual(corner.verdict, VERDICT_CERTIFIED_DISJOINT)
        self.assertEqual(corner.reason, "boundary_touch_only")
        apart = polygon_overlap_verdict(_square(0, 0, 10, 10), _square(30, 0, 40, 10), 1e-9, 64)
        self.assertEqual(apart.verdict, VERDICT_CERTIFIED_DISJOINT)
        self.assertEqual(apart.reason, "bbox_disjoint")
    def test_near_tolerance_tangency_is_uncertain(self):
        epsilon = 1e-6
        near = polygon_overlap_verdict(
            _square(0, 0, 10, 10), _square(10 + 1e-8, 0, 20 + 1e-8, 10), epsilon, 64
        )
        self.assertEqual(near.verdict, VERDICT_UNCERTAIN)
        self.assertEqual(near.reason, "tolerance_unstable_contact")
        self.assertTrue(near.details["locations"])
        self.assertLessEqual(near.details["min_distance"], epsilon)
        clear = polygon_overlap_verdict(
            _square(0, 0, 10, 10), _square(10.001, 0, 20.001, 10), epsilon, 64
        )
        self.assertEqual(clear.verdict, VERDICT_CERTIFIED_DISJOINT)
    def test_unclear_topology_is_uncertain_not_false(self):
        partner = _square(1, 1, 5, 3)
        bowtie = (((0, 0), (6, 4), (0, 4), (6, 0), (2, -2), (0, 0)),)
        hole_outside = (_SHELL_10, ((20, 20), (30, 20), (30, 30), (20, 30), (20, 20)))
        hole_tangent = (_SHELL_10, ((2, 2), (8, 2), (8, 10), (2, 10), (2, 2)))
        nested_holes = (
            _SHELL_10,
            _HOLE_2_8,
            ((3, 3), (5, 3), (5, 5), (3, 5), (3, 3)),
        )
        for polygon in (bowtie, hole_outside, hole_tangent, nested_holes):
            self.assertTrue(polygon_topology_issues(polygon, 1e-9))
            verdict = polygon_overlap_verdict(polygon, partner, 1e-9, 64)
            self.assertEqual(verdict.verdict, VERDICT_UNCERTAIN)
            self.assertEqual(verdict.reason, "topology_undefined")
            self.assertTrue(verdict.details["locations"])
        far = polygon_overlap_verdict(hole_tangent, _square(50, 50, 60, 60), 1e-9, 64)
        self.assertEqual(far.verdict, VERDICT_CERTIFIED_DISJOINT)
        self.assertEqual(far.reason, "bbox_disjoint")
    def test_budget_controls_refinement_route(self):
        # Identical valid polygons whose shared hole blocks dyadic witness
        # candidates: refinement must subdivide the shared boundary several
        # times before a common interior point is found.
        shell = ((0, 0), (16, 0), (16, 16), (0, 16), (0, 0))
        hole = ((1.5, 0.4), (14.5, 0.4), (14.5, 12), (1.5, 12), (1.5, 0.4))
        polygon = (shell, hole)
        self.assertEqual(polygon_topology_issues(polygon, 1e-9), [])
        low = polygon_overlap_verdict(polygon, polygon, 1e-9, 2)
        self.assertEqual(low.verdict, VERDICT_UNCERTAIN)
        self.assertEqual(low.reason, "budget_exhausted")
        self.assertEqual(low.budget_limit, 2)
        self.assertLessEqual(low.budget_consumed, 2)
        self.assertTrue(low.details["locations"])
        high = polygon_overlap_verdict(polygon, polygon, 1e-9, 256)
        self.assertEqual(high.verdict, VERDICT_CERTIFIED_OVERLAP)
        self.assertEqual(high.reason, "boundary_refinement")
        self.assertLessEqual(high.budget_consumed, 256)
        self.assertEqual(point_in_polygon(polygon, high.witness, 1e-9), "inside")
    def test_boolean_wrapper_matches_certified_verdicts(self):
        crossing = _square(5, 5, 15, 15)
        touching = _square(10, 0, 20, 10)
        contained = _square(2, 2, 4, 4)
        disjoint = _square(20, 20, 30, 30)
        self.assertTrue(polygons_overlap(SQUARE, crossing))
        self.assertFalse(polygons_overlap(SQUARE, touching))
        self.assertTrue(polygons_overlap(SQUARE, contained))
        self.assertFalse(polygons_overlap(SQUARE, disjoint))
        self.assertTrue(polygons_overlap(SQUARE, SQUARE))
if __name__ == "__main__":
    unittest.main()
