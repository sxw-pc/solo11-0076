"""Same-generation proof tests for atomic upstream release manifests."""
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
from topoaudit.loaders import load_input_set, load_layers
from topoaudit.pipeline import execute
from topoaudit.report_json import build_report
from topoaudit.ledger_verify import verify_report_and_ledger
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

class GenerationProofTest(unittest.TestCase):
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

    def test_verified_same_generation_binds_parsed_bytes(self):
        loaded = load_input_set(load_config(self._config_path()))
        evidence = loaded.source_generation
        self.assertEqual(evidence.status, "verified")
        self.assertEqual(evidence.generation_id, "gen-v1")
        self.assertEqual(evidence.quality_gate, "allowed")
        self.assertIs(evidence.manifest_read_consistent, True)
        for layer, raw in zip(loaded.layers, ("alpha", "beta")):
            self.assertEqual(layer.input_file.sha256, sha256_bytes(self.fixture.v1[raw]))
        verified = {item.layer_id: item for item in evidence.layer_checks}
        self.assertEqual(verified["alpha"].expected_sha256, verified["alpha"].actual_sha256)
        self.assertEqual(verified["beta"].status, "verified")

    def test_mixed_generations_cannot_pass_even_when_each_file_is_stable(self):
        # alpha from v1, beta from v2; both files are whole and stable on disk.
        (self.root / "beta.json").write_bytes(self.fixture.v2["beta"])
        config = load_config(self._config_path("gen-v1.manifest.json"))
        # Each file individually hashes stably before and after reading.
        before = {name: sha256_bytes((self.root / f"{name}.json").read_bytes())
                  for name in ("alpha", "beta")}
        loaded = load_input_set(config)
        after = {name: sha256_bytes((self.root / f"{name}.json").read_bytes())
                 for name in ("alpha", "beta")}
        self.assertEqual(before, after)
        evidence = loaded.source_generation
        self.assertEqual(evidence.status, "failed")
        self.assertEqual(evidence.quality_gate, "blocked")
        self.assertFalse(evidence.same_generation_proven if hasattr(evidence, "same_generation_proven") else False)
        self.assertTrue(all(item.reread_consistent for item in evidence.layer_checks))
        statuses = {item.layer_id: item.status for item in evidence.layer_checks}
        self.assertEqual(statuses["alpha"], "verified")
        self.assertEqual(statuses["beta"], "digest_mismatch")
        report = build_report(run_audit(config))
        self.assertEqual(report["source_generation"]["status"], "failed")
        self.assertFalse(report["source_generation"]["same_generation_proven"])
        self.assertEqual(report["summary"]["quality_gate"], "blocked")

    def test_manifest_missing_layer_is_rejected_as_same_generation(self):
        manifest = self.root / "partial.manifest.json"
        entries = [
            {
                "id": "alpha",
                "path": "alpha.json",
                "format": "geojson",
                "sha256": sha256_bytes(self.fixture.v1["alpha"]),
            }
        ]
        manifest.write_bytes(
            json.dumps(_manifest_dict("gen-partial", entries)).encode()
        )
        loaded = load_input_set(load_config(self._config_path(manifest.name)))
        self.assertEqual(loaded.source_generation.status, "failed")
        self.assertEqual(loaded.source_generation.missing_layer_ids, ("beta",))
        self.assertEqual(loaded.source_generation.quality_gate, "blocked")

    def test_manifest_extra_member_is_rejected_as_same_generation(self):
        manifest = self.root / "extra.manifest.json"
        entries = [
            {
                "id": layer_id,
                "path": f"{layer_id}.json",
                "format": "geojson",
                "sha256": sha256_bytes(raw),
            }
            for layer_id, raw in self.fixture.v1.items()
        ]
        gamma = json.dumps(
            _collection([_feature("G-1", 9, 9)]), sort_keys=True
        ).encode()
        _write_bytes(self.root / "gamma.json", gamma)
        entries.append(
            {
                "id": "gamma",
                "path": "gamma.json",
                "format": "geojson",
                "sha256": sha256_bytes(gamma),
            }
        )
        manifest.write_bytes(json.dumps(_manifest_dict("gen-extra", entries)).encode())
        loaded = load_input_set(load_config(self._config_path(manifest.name)))
        self.assertEqual(loaded.source_generation.status, "failed")
        self.assertEqual(loaded.source_generation.extra_layer_ids, ("gamma",))

    def test_wrong_digest_or_path_mismatch_is_rejected(self):
        entries = [
            {
                "id": "alpha",
                "path": "alpha.json",
                "format": "geojson",
                "sha256": "0" * 64,
            },
            {
                "id": "beta",
                "path": "elsewhere.json",
                "format": "geojson",
                "sha256": sha256_bytes(self.fixture.v1["beta"]),
            },
        ]
        manifest = self.root / "bad.manifest.json"
        manifest.write_bytes(json.dumps(_manifest_dict("gen-bad", entries)).encode())
        loaded = load_input_set(load_config(self._config_path(manifest.name)))
        statuses = {item.layer_id: item.status for item in loaded.source_generation.layer_checks}
        self.assertEqual(statuses["alpha"], "digest_mismatch")
        self.assertEqual(statuses["beta"], "path_mismatch")
        self.assertEqual(loaded.source_generation.quality_gate, "blocked")

    def test_atomic_replacement_after_parse_is_detected(self):
        config = load_config(self._config_path("gen-v1.manifest.json"))
        real_read_bytes = Path.read_bytes

        def replacing_read(path):
            data = real_read_bytes(path)
            if path.name == "beta.json" and data == self.fixture.v1["beta"]:
                # Return the parsed v1 bytes, then atomically swap in v2.
                candidate = path.with_suffix(".json.new")
                candidate.write_bytes(self.fixture.v2["beta"])
                candidate.replace(path)
            return data

        with mock.patch.object(Path, "read_bytes", replacing_read):
            loaded = load_input_set(config)
        evidence = loaded.source_generation
        checks = {item.layer_id: item for item in evidence.layer_checks}
        self.assertEqual(checks["alpha"].status, "verified")
        self.assertEqual(checks["beta"].status, "reread_mismatch")
        self.assertIs(checks["beta"].reread_consistent, False)
        self.assertEqual(evidence.status, "failed")
        self.assertEqual(evidence.quality_gate, "blocked")

    def test_manifest_replaced_between_reads_is_detected(self):
        config = load_config(self._config_path("gen-v1.manifest.json"))
        real_read_bytes = Path.read_bytes

        def replacing_read(path):
            data = real_read_bytes(path)
            if path.name == "gen-v1.manifest.json":
                candidate = path.with_suffix(".new")
                candidate.write_bytes(self.fixture.manifest_v2)
                candidate.replace(path)
            return data

        with mock.patch.object(Path, "read_bytes", replacing_read):
            loaded = load_input_set(config)
        self.assertIs(loaded.source_generation.manifest_read_consistent, False)
        self.assertEqual(loaded.source_generation.status, "failed")
        # v1 layer bytes are correct for the v1 manifest read, but the second
        # manifest read shows a version switch, so no proof is issued.
        self.assertEqual(loaded.source_generation.quality_gate, "blocked")


if __name__ == "__main__":
    unittest.main()
