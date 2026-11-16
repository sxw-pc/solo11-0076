"""Source-generation policy tests: require vs observe vs no manifest."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from topoaudit.cli import main
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.errors import ConfigError, InputError
from topoaudit.loaders import load_input_set
from topoaudit.report_json import build_report
from topoaudit.report_html import render_html
from topoaudit.report_md import render_markdown
from topoaudit.util import sha256_bytes

def _collection(features):
    return {"type": "FeatureCollection", "features": features}

def _feature(feature_id, lon, lat):
    return {
        "type": "Feature",
        "properties": {"id": feature_id},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }

def _layer(layer_id, path, fmt="geojson"):
    return {
        "id": layer_id,
        "path": path,
        "format": fmt,
        "geometry_types": ["Point"],
        "id_field": "id",
        "schema": {"id": {"type": "string", "required": True}},
    }

def _config(layers, manifest=None, policy=None):
    data = {
        "schema_version": "1.0",
        "run_label": "generation-test",
        "tolerance": {"coordinate_epsilon": 0.000001, "near_miss_distance": 1},
        "crs": {
            "name": "LOCAL_XY",
            "type": "planar",
            "bounds": {"min_x": -100, "min_y": -100, "max_x": 100, "max_y": 100},
        },
        "layers": layers,
    }
    if manifest is not None or policy is not None:
        block = {}
        if policy is not None:
            block["policy"] = policy
        if manifest is not None:
            block["manifest"] = manifest
        data["source_generation"] = block
    return data

def _write_bytes(path, data):
    Path(path).write_bytes(data)
    return data

def _manifest_dict(generation_id, entries):
    return {"schema_version": "1.0", "generation_id": generation_id, "layers": entries}

class GenerationFixture:
    """Two independent complete v1/v2 layer pairs and their manifests."""

    def __init__(self, root: Path):
        self.root = root
        self.v1 = {
            "alpha": _write_bytes(
                root / "alpha.json",
                json.dumps(_collection([_feature("A-1", 1, 1)]), sort_keys=True).encode(),
            ),
            "beta": _write_bytes(
                root / "beta.json",
                json.dumps(_collection([_feature("B-1", 2, 2)]), sort_keys=True).encode(),
            ),
        }
        self.v2 = {
            "alpha": json.dumps(
                _collection([_feature("A-2", 3, 3)]), sort_keys=True
            ).encode(),
            "beta": json.dumps(
                _collection([_feature("B-2", 4, 4)]), sort_keys=True
            ).encode(),
        }
        self.manifest_v1 = self._write_manifest("gen-v1", self.v1)
        self.manifest_v2 = self._write_manifest("gen-v2", self.v2)

    def _write_manifest(self, generation_id, files):
        entries = [
            {
                "id": layer_id,
                "path": f"{layer_id}.json",
                "format": "geojson",
                "sha256": sha256_bytes(raw),
            }
            for layer_id, raw in files.items()
        ]
        return _write_bytes(
            self.root / f"{generation_id}.manifest.json",
            json.dumps(_manifest_dict(generation_id, entries), sort_keys=True).encode(),
        )

    def config(self, manifest_name, policy="observe"):
        return _config(
            [_layer("alpha", "alpha.json"), _layer("beta", "beta.json")],
            manifest=manifest_name,
            policy=policy,
        )

class GenerationPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fixture = GenerationFixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _config_path(self, manifest_name="gen-v1.manifest.json", policy="observe"):
        path = self.root / "audit.config.json"
        path.write_text(
            json.dumps(self.fixture.config(manifest_name, policy)), encoding="utf-8"
        )
        return path

    def test_require_policy_refuses_audit_without_same_generation_proof(self):
        (self.root / "beta.json").write_bytes(self.fixture.v2["beta"])
        config_path = self._config_path("gen-v1.manifest.json", policy="require")
        with self.assertRaises(InputError) as caught:
            run_audit(load_config(config_path))
        self.assertEqual(caught.exception.code, "TOPO-INP-012")

    def test_clean_data_without_manifest_still_has_no_pass_verdict(self):
        data = _config([_layer("alpha", "alpha.json"), _layer("beta", "beta.json")])
        path = self.root / "clean-observe.config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        report = build_report(run_audit(load_config(path)))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["summary"]["quality_verdict"], "not_releasable")
        self.assertIn("不能据此给出可执行的质量通过结论", render_markdown(report))
        self.assertIn("不能据此给出可执行的质量通过结论", render_html(report))

    def test_manifest_without_policy_defaults_to_require_and_refuses_mismatch(self):
        (self.root / "beta.json").write_bytes(self.fixture.v2["beta"])
        data = _config(
            [_layer("alpha", "alpha.json"), _layer("beta", "beta.json")],
            manifest="gen-v1.manifest.json",
        )
        path = self.root / "default-require.config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(InputError) as caught:
            run_audit(load_config(path))
        self.assertEqual(caught.exception.code, "TOPO-INP-012")

    def test_no_manifest_observe_is_default(self):
        data = _config([_layer("alpha", "alpha.json"), _layer("beta", "beta.json")])
        path = self.root / "default-observe.config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_input_set(load_config(path))
        self.assertEqual(loaded.source_generation.policy, "observe")
        self.assertEqual(loaded.source_generation.status, "unverified")

    def test_require_policy_needs_manifest(self):
        path = self.root / "bad.config.json"
        data = _config([_layer("alpha", "alpha.json"), _layer("beta", "beta.json")])
        data["source_generation"] = {"policy": "require"}
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        self.assertEqual(caught.exception.code, "TOPO-CFG-010")

    def test_no_manifest_observe_route_is_honest_and_never_passes(self):
        data = _config([_layer("alpha", "alpha.json"), _layer("beta", "beta.json")])
        path = self.root / "no-manifest.config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_input_set(load_config(path))
        evidence = loaded.source_generation
        self.assertEqual(evidence.status, "unverified")
        self.assertIsNone(evidence.generation_id)
        self.assertIsNone(evidence.manifest_sha256)
        self.assertEqual(evidence.quality_gate, "blocked")
        report = build_report(run_audit(load_config(path)))
        self.assertFalse(report["source_generation"]["same_generation_proven"])
        self.assertEqual(report["summary"]["quality_gate"], "blocked")
        markdown = render_markdown(report)
        self.assertIn("来源代未证、不可作同代质量放行", markdown)
        self.assertIn("来源代未证", render_html(report))


if __name__ == "__main__":
    unittest.main()
