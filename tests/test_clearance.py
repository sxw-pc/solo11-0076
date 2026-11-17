"""Unit tests for the exact, continuous-plane orthogonal clearance engine.

These tests exercise the arrangement-based erosion in ``topoaudit.geometry``
and the orchestration in ``topoaudit.clearance`` directly on hand-built
orthogonal polygons, so every connectivity claim is tied to an exact
construction rather than raster sampling.
"""
import unittest
from topoaudit.clearance import audit_feature_clearance, audit_polygon_part
from topoaudit.geometry import (
    clearance_cells,
    polygon_area,
    polygon_perimeter,
    provably_valid_orthogonal_polygon,
    ring_is_axis_aligned,
)

EPS = 1e-9
CLEARANCE_WIDTH = 0.4
HALF = 0.2
MIN_CORE = 25.0


def R(*points):
    """Build a closed orthogonal ring."""
    return tuple(tuple(point) for point in points) + (points[0],)


def dumbbell(neck_width):
    """Two 10x10 squares joined by a length-2, neck-width corridor."""
    y0, y1 = 5.0 - neck_width / 2.0, 5.0 + neck_width / 2.0
    outer = R(
        (0, 0), (10, 0), (10, y0), (12, y0), (12, 0), (22, 0),
        (22, 10), (12, 10), (12, y1), (10, y1), (10, 10), (0, 10),
    )
    return (outer,)


def dead_end(neck_width):
    """A 10x10 core with a thin, closed finger of the same width."""
    y0, y1 = 5.0 - neck_width / 2.0, 5.0 + neck_width / 2.0
    outer = R(
        (0, 0), (10, 0), (10, y0), (12, y0), (12, y1),
        (10, y1), (10, 10), (0, 10),
    )
    return (outer,)


def vertical_neck_with_hole(hole):
    outer = R(
        (0, 0), (10, 0), (10, 10), (6, 10), (6, 12), (10, 12),
        (10, 22), (0, 22), (0, 12), (4, 12), (4, 10), (0, 10),
    )
    return (outer, hole)


class AxisAlignedGateTest(unittest.TestCase):
    def test_axis_aligned_detection(self):
        self.assertTrue(ring_is_axis_aligned(dumbbell(0.2)[0], EPS))
        slanted = R((0, 0), (10, 0), (10, 10), (5, 15), (0, 10))
        self.assertFalse(ring_is_axis_aligned(slanted, EPS))

    def test_validity_gate_reasons(self):
        ok, reason = provably_valid_orthogonal_polygon(dumbbell(0.2), EPS)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        slanted = (R((0, 0), (10, 0), (10, 10), (5, 15), (0, 10)),)
        ok, reason = provably_valid_orthogonal_polygon(slanted, EPS)
        self.assertFalse(ok)
        self.assertEqual(reason, "non_axis_aligned_edge")
        touching_hole = (
            R((0, 0), (10, 0), (10, 10), (0, 10)),
            R((0, 3), (3, 3), (3, 7), (0, 7)),
        )
        ok, reason = provably_valid_orthogonal_polygon(touching_hole, EPS)
        self.assertFalse(ok)
        self.assertEqual(reason, "hole_not_strictly_inside_outer")
        self_touch = (
            R((0, 0), (10, 0), (10, 10), (20, 10), (20, 20),
              (10, 20), (10, 10), (0, 10)),
        )
        ok, reason = provably_valid_orthogonal_polygon(self_touch, EPS)
        self.assertFalse(ok)
        self.assertEqual(reason, "ring_self_touch_or_repeated_vertex")

    def test_clearance_cells_rejects_unsupported(self):
        slanted = (R((0, 0), (10, 0), (10, 10), (5, 15), (0, 10)),)
        self.assertIsNone(clearance_cells(slanted, HALF, EPS))


class DumbbellErosionTest(unittest.TestCase):
    def test_arrangement_derives_from_face_coordinates_only(self):
        # The grid lines are the face coordinates plus their half-width
        # offsets: no external/fixed raster resolution is introduced.
        polygon = dumbbell(0.2)
        raw_x = sorted({p[0] for ring in polygon for p in ring})
        raw_y = sorted({p[1] for ring in polygon for p in ring})
        expected_x = sorted({v for b in raw_x for v in (b, b + HALF, b - HALF)})
        expected_y = sorted({v for b in raw_y for v in (b, b + HALF, b - HALF)})
        arrangement = clearance_cells(polygon, HALF, EPS)
        self.assertEqual(arrangement.xs, tuple(expected_x))
        self.assertEqual(arrangement.ys, tuple(expected_y))

    def test_simple_square_erosion_area_is_exact(self):
        square = (R((0, 0), (10, 0), (10, 10), (0, 10)),)
        arrangement = clearance_cells(square, HALF, EPS)
        components = arrangement.connected_components()
        self.assertEqual(len(components), 1)
        # Eroding a 10x10 square by 0.2 on every side leaves 9.6 x 9.6.
        self.assertAlmostEqual(components[0].area, 9.6 * 9.6, places=6)

    def test_narrow_bridge_splits_two_cores(self):
        result = audit_polygon_part(
            "polygons[0]", dumbbell(0.2), CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "blocking_narrow_neck")
        self.assertEqual(len(result.cores), 2)
        self.assertEqual(result.disconnected_core_pairs, ((0, 1),))
        for core in result.cores:
            self.assertGreaterEqual(core.area, MIN_CORE)
        # Each surviving core is the eroded 10x10 chamber: (10-2h)^2 = 9.6^2.
        self.assertAlmostEqual(result.cores[0].area, 9.6 * 9.6, places=6)
        self.assertAlmostEqual(result.cores[1].area, 9.6 * 9.6, places=6)
        self.assertTrue(result.constrictions)
        self.assertAlmostEqual(result.constrictions[0].width, 0.2, places=6)

    def test_exact_fit_keeps_cores_connected(self):
        result = audit_polygon_part(
            "polygons[0]", dumbbell(0.4), CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(result.cores), 1)
        self.assertEqual(result.disconnected_core_pairs, ())

    def test_roomy_bridge_keeps_cores_connected(self):
        result = audit_polygon_part(
            "polygons[0]", dumbbell(0.41), CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(result.cores), 1)

    def test_global_ratio_matches_worked_example(self):
        polygon = dumbbell(0.2)
        area = polygon_area(polygon)
        perimeter = polygon_perimeter(polygon)
        self.assertAlmostEqual(area, 200.4, places=6)
        self.assertAlmostEqual(perimeter, 83.6, places=6)
        self.assertAlmostEqual(area / perimeter, 2.3971291866, places=6)
        # The global ratio stays well above the default sliver ratio of 0.1,
        # so the shape is not a sliver even though the neck blocks clearance.
        self.assertGreater(area / perimeter, 0.1)


class DeadEndVersusBridgeTest(unittest.TestCase):
    def test_same_width_dead_end_is_not_blocking(self):
        bridge = audit_polygon_part(
            "polygons[0]", dumbbell(0.2), CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        finger = audit_polygon_part(
            "polygons[0]", dead_end(0.2), CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(bridge.status, "blocking_narrow_neck")
        self.assertEqual(len(bridge.cores), 2)
        # The dead end leaves one core; its narrow finger is at most a
        # sub-core fragment, never a second disconnected core.
        self.assertEqual(finger.status, "pass")
        self.assertEqual(len(finger.cores), 1)
        self.assertEqual(finger.disconnected_core_pairs, ())
        # Both still record the 0.2 constriction as spatial evidence.
        self.assertTrue(finger.constrictions)
        self.assertAlmostEqual(finger.constrictions[0].width, 0.2, places=6)


class HoleErosionTest(unittest.TestCase):
    def test_hole_invading_neck_blocks(self):
        hole = R((4.15, 10.6), (5.85, 10.6), (5.85, 11.4), (4.15, 11.4))
        polygon = vertical_neck_with_hole(hole)
        result = audit_polygon_part(
            "polygons[0]", polygon, CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "blocking_narrow_neck")
        self.assertEqual(len(result.cores), 2)
        self.assertTrue(
            any(item.ring_indexes == (0, 1) for item in result.constrictions)
        )

    def test_roomy_hole_passes(self):
        hole = R((4.5, 10.6), (5.5, 10.6), (5.5, 11.4), (4.5, 11.4))
        polygon = vertical_neck_with_hole(hole)
        result = audit_polygon_part(
            "polygons[0]", polygon, CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(result.cores), 1)

    def test_hole_edges_and_outer_edges_both_constrain(self):
        # A hole that leaves margins above the requirement must not block,
        # proving hole boundaries erode exactly like outer boundaries.
        hole = R((4.5, 10.6), (5.5, 10.6), (5.5, 11.4), (4.5, 11.4))
        polygon = vertical_neck_with_hole(hole)
        arrangement = clearance_cells(polygon, HALF, EPS)
        self.assertIsNotNone(arrangement)
        components = arrangement.connected_components()
        self.assertEqual(len(components), 1)


class UnknownInputTest(unittest.TestCase):
    def test_slanted_face_is_unknown(self):
        slanted = (R((0, 0), (10, 0), (10, 10), (5, 15), (0, 10)),)
        result = audit_polygon_part(
            "polygons[0]", slanted, CLEARANCE_WIDTH, MIN_CORE, EPS
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.reason, "non_axis_aligned_edge")

    def test_clearance_below_epsilon_is_unknown(self):
        audit = audit_feature_clearance(
            polygons=[("polygons[0]", dumbbell(0.2))],
            clearance_width=5e-10,
            min_core_area=MIN_CORE,
            usage_contract="access_clearance",
            epsilon=1e-9,
        )
        self.assertEqual(audit.status, "unknown")
        self.assertEqual(audit.reason, "clearance_width_not_above_coordinate_epsilon")

    def test_separated_multipolygon_parts_are_independent(self):
        sq1 = (R((0, 0), (10, 0), (10, 10), (0, 10)),)
        sq3 = (R((30, 0), (40, 0), (40, 10), (30, 10)),)
        audit = audit_feature_clearance(
            polygons=[("polygons[0]", sq1), ("polygons[1]", sq3)],
            clearance_width=CLEARANCE_WIDTH,
            min_core_area=MIN_CORE,
            usage_contract="access_clearance",
            epsilon=EPS,
        )
        # Pre-existing separation is never attributed to clearance erosion.
        self.assertEqual(audit.status, "pass")
        self.assertEqual(len(audit.parts), 2)
        for part in audit.parts:
            self.assertEqual(part.status, "pass")
            self.assertEqual(len(part.cores), 1)


if __name__ == "__main__":
    unittest.main()
