import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from topoaudit.config import load_config
from topoaudit.errors import ConfigError, InputError
from topoaudit.loaders import compute_input_digest, load_layers
ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "fixtures" / "demo"

def layer(path, layer_id="layer", fmt="geojson", types=None, schema=None):
    return {
        "id": layer_id,
        "path": path,
        "format": fmt,
        "geometry_types": types or ["Point", "LineString", "Polygon"],
        "id_field": "id",
        "schema": schema or {"id": {"type": "string", "required": True}},
    }

def config_data(layers):
    return {
        "schema_version": "1.0",
        "run_label": "unit-test",
        "tolerance": {"coordinate_epsilon": 0.000001, "near_miss_distance": 1},
        "crs": {
            "name": "LOCAL_XY",
            "type": "planar",
            "bounds": {"min_x": -100, "min_y": -100, "max_x": 100, "max_y": 100},
        },
        "layers": layers,
    }

def write_config(directory, data):
    path = Path(directory) / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path

class LoaderTest(unittest.TestCase):
    def test_fixture_layers_and_file_digests(self):
        config = load_config(DEMO / "config.json")
        layers = load_layers(config)
        self.assertEqual([item.config.layer_id for item in layers], ["parcels", "roads", "points", "empty"])
        self.assertEqual(len(layers[0].features), 14)
        self.assertEqual(len(layers[1].features), 14)
        self.assertEqual(layers[2].features[0].properties["elevation"], 12.5)
        self.assertEqual(layers[2].features[4].properties["label"], "")
        raw = (DEMO / "points.csv").read_bytes()
        self.assertEqual(layers[2].input_file.sha256, hashlib.sha256(raw).hexdigest())
        digest = compute_input_digest(config.config_digest, tuple(item.input_file for item in layers))
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, compute_input_digest(config.config_digest, tuple(item.input_file for item in layers)))
    def test_unsupported_geometry_is_preserved_for_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "input.geojson").write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"id": "X-1"},
                              "geometry": {"type": "GeometryCollection", "geometries": []}}],
            }), encoding="utf-8")
            cfg = write_config(tmp, config_data([layer("input.geojson", types=["LineString"])]))
            loaded = load_layers(load_config(cfg))[0].features[0]
            self.assertEqual(loaded.geometry.kind, "Unsupported")
            self.assertEqual(loaded.geometry.raw_type, "GeometryCollection")
    def test_invalid_geojson_has_structured_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "bad.geojson").write_text("{not json", encoding="utf-8")
            cfg = write_config(tmp, config_data([layer("bad.geojson")]))
            with self.assertRaises(InputError) as caught:
                load_layers(load_config(cfg))
            self.assertEqual(caught.exception.code, "TOPO-INP-005")
    def test_csv_requires_id_x_y_and_configured_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "points.csv").write_text("id,x\nA,1\n", encoding="utf-8")
            cfg = write_config(tmp, config_data([layer("points.csv", fmt="csv", types=["Point"])]))
            with self.assertRaises(InputError) as caught:
                load_layers(load_config(cfg))
            self.assertEqual(caught.exception.code, "TOPO-INP-007")
    def test_non_finite_csv_coordinate_is_loaded_for_rule_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "points.csv").write_text("id,x,y\nP-1,NaN,2\n", encoding="utf-8")
            cfg = write_config(tmp, config_data([layer("points.csv", fmt="csv", types=["Point"])]))
            feature = load_layers(load_config(cfg))[0].features[0]
            self.assertFalse(feature.geometry.coordinates[0] == feature.geometry.coordinates[0])
    def test_config_rejects_unknown_field_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = config_data([layer("missing.geojson")])
            data["surprise"] = True
            cfg = write_config(tmp, data)
            with self.assertRaises(ConfigError) as caught:
                load_config(cfg)
            self.assertEqual(caught.exception.code, "TOPO-CFG-003")
            self.assertIn("$.surprise", caught.exception.path)
    def test_config_rejects_duplicate_layers_and_unknown_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = config_data([layer("a.geojson", "same"), layer("b.geojson", "same")])
            with self.assertRaises(ConfigError) as caught:
                load_config(write_config(tmp, duplicate))
            self.assertEqual(caught.exception.code, "TOPO-CFG-008")
            bad_rule = config_data([layer("a.geojson")])
            bad_rule["layers"][0]["rules"] = {"NOT_A_RULE": {"enabled": False}}
            with self.assertRaises(ConfigError) as caught:
                load_config(write_config(tmp, bad_rule))
            self.assertEqual(caught.exception.code, "TOPO-CFG-007")
    def test_changed_input_changes_input_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            geojson = {"type": "FeatureCollection", "features": []}
            target = Path(tmp, "input.geojson")
            target.write_text(json.dumps(geojson), encoding="utf-8")
            cfg = write_config(tmp, config_data([layer("input.geojson")]))
            config = load_config(cfg)
            first = compute_input_digest(config.config_digest, tuple(item.input_file for item in load_layers(config)))
            target.write_text(json.dumps(geojson) + "\n", encoding="utf-8")
            second = compute_input_digest(config.config_digest, tuple(item.input_file for item in load_layers(config)))
            self.assertNotEqual(first, second)
if __name__ == "__main__":
    unittest.main()
