import math
import unittest
from topoaudit.geometry import (
    area_perimeter_ratio,
    bbox_of_points,
    bbox_intersection,
    consecutive_duplicates,
    distance,
    is_finite_point,
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
from topoaudit.model import Geometry
from topoaudit.normalize import canonical_geometry, finite_bbox
SQUARE = (((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),)
HOLE = (
    ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),
    ((3, 3), (7, 3), (7, 7), (3, 7), (3, 3)),
)

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
if __name__ == "__main__":
    unittest.main()
