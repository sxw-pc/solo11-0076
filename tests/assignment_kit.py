"""Shared synthetic fixtures for the assignment-precheck tests."""
import json
import tempfile
import unittest
from pathlib import Path

from topoaudit.config import load_config
from topoaudit.engine import run_audit


def square(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]


def zone_polygon(zone_id, rings, **properties):
    return {
        "type": "Feature",
        "properties": {"zone_id": zone_id, **properties},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }


def zone_multipolygon(zone_id, polygons):
    return {
        "type": "Feature",
        "properties": {"zone_id": zone_id},
        "geometry": {"type": "MultiPolygon", "coordinates": polygons},
    }


def base_assignment(anchors, capacity=1, policy="review", **extra):
    spec = {
        "point_layer": "facilities",
        "zone_layer": "zones",
        "max_facilities_per_zone": capacity,
        "boundary_policy": policy,
        "anchors": anchors,
    }
    spec.update(extra)
    return spec


class AssignmentTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_zones(self, features):
        (self.dir / "zones.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features}),
            encoding="utf-8",
        )

    def write_points(self, rows):
        lines = ["id,x,y"] + [f"{pid},{x},{y}" for pid, x, y in rows]
        (self.dir / "points.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_config(self, assignment, declared_crs=None, zone_id_field=None):
        zone_layer = {
            "id": "zones",
            "path": "zones.geojson",
            "format": "geojson",
            "geometry_types": ["Polygon", "MultiPolygon"],
            "id_field": "zone_id",
            "schema": {"zone_id": {"type": "string", "required": True}},
        }
        if declared_crs is not None:
            zone_layer["declared_crs"] = declared_crs
        spec = dict(assignment)
        if zone_id_field is not None:
            spec["zone_id_field"] = zone_id_field
        config = {
            "schema_version": "1.0",
            "run_label": "assignment-test",
            "tolerance": {"coordinate_epsilon": 0.000001},
            "crs": {
                "name": "LOCAL_XY",
                "type": "planar",
                "bounds": {"min_x": -1000, "min_y": -1000, "max_x": 1000, "max_y": 1000},
            },
            "layers": [
                {
                    "id": "facilities",
                    "path": "points.csv",
                    "format": "csv",
                    "geometry_types": ["Point"],
                    "id_field": "id",
                    "schema": {"id": {"type": "string", "required": True}},
                },
                zone_layer,
            ],
            "assignment": spec,
        }
        (self.dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        return self.dir / "config.json"

    def run_precheck(self, assignment, zones, points, **config_kwargs):
        self.write_zones(zones)
        self.write_points(points)
        config = load_config(self.write_config(assignment, **config_kwargs))
        return run_audit(config)

    def assert_plan_invariants(self, section, capacities):
        """每点最多一个区、每区不超容量、每点都有归属或原因。"""
        for plan in section["plans"]:
            seen = set()
            for item in plan["assignments"]:
                self.assertNotIn(item["point_id"], seen, plan["objective"])
                seen.add(item["point_id"])
            for zone_id, load in plan["metrics"]["zone_loads"].items():
                self.assertLessEqual(load, capacities[zone_id], plan["objective"])
            accounted = len(plan["assignments"]) + len(plan["unassigned"])
            self.assertEqual(accounted, len(section["points"]), plan["objective"])


def tradeoff_fixture():
    """重叠区 + 容量充足：两条目标产生不同但均合规的方案。"""
    zones = [
        zone_polygon("Z-A", [square(0, 0, 10, 10)]),
        zone_polygon("Z-B", [square(5, 0, 15, 10)]),
    ]
    points = [("P1", 6.5, 1), ("P2", 7, 1), ("P3", 7.5, 1), ("P4", 8, 1)]
    assignment = base_assignment({"Z-A": [6, 1], "Z-B": [14, 1]}, capacity=4)
    return zones, points, assignment
