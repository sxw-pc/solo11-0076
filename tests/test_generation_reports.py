"""Same-generation proof tests for atomic upstream release manifests."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from topoaudit.cli import main
from topoaudit.config import load_config
from topoaudit.pipeline import execute
from topoaudit.loaders import load_layers
from topoaudit.ledger_verify import verify_report_and_ledger
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

class GenerationArtifactTest(unittest.TestCase):
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

    def test_every_review_artifact_agrees_on_generation_status(self):
        out = self.root / "out"
        report, paths = execute(self._config_path("gen-v1.manifest.json"), out)
        self.assertEqual(report["source_generation"]["status"], "verified")
        inputs = json.loads(paths["inputs"].read_text(encoding="utf-8"))
        self.assertEqual(inputs["source_generation"], report["source_generation"])
        self.assertEqual(report["summary"]["quality_verdict"], "releasable")
        self.assertIn("同代已证", paths["markdown"].read_text(encoding="utf-8"))
        self.assertIn("generation_id", paths["html"].read_text(encoding="utf-8"))
        self.assertIn("source_generation: verified", paths["summary"].read_text(encoding="utf-8"))
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertTrue(verification.ok, verification.issues)
        self.assertTrue(verification.same_generation_proven)
        self.assertEqual(verification.generation_id, "gen-v1")

    def test_blocked_generation_is_reported_consistently_everywhere(self):
        (self.root / "beta.json").write_bytes(self.fixture.v2["beta"])
        out = self.root / "blocked"
        report, paths = execute(self._config_path("gen-v1.manifest.json"), out)
        self.assertEqual(report["source_generation"]["status"], "failed")
        inputs = json.loads(paths["inputs"].read_text(encoding="utf-8"))
        self.assertEqual(inputs["source_generation"], report["source_generation"])
        markdown = paths["markdown"].read_text(encoding="utf-8")
        self.assertEqual(report["summary"]["quality_verdict"], "not_releasable")
        self.assertIn("同代核验失败", markdown)
        self.assertIn("不可作同代质量放行", markdown)
        html = paths["html"].read_text(encoding="utf-8")
        self.assertIn("quality_gate: blocked", html)
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertTrue(verification.ok, verification.issues)
        self.assertFalse(verification.same_generation_proven)
        self.assertEqual(verification.quality_gate, "blocked")

    def test_ledger_keeps_generation_columns_and_verify_detects_tampering(self):
        out = self.root / "ledger"
        report, paths = execute(self._config_path("gen-v1.manifest.json"), out)
        import sqlite3

        connection = sqlite3.connect(paths["ledger"])
        try:
            row = connection.execute(
                "SELECT generation_status, generation_id, quality_gate, manifest_sha256 "
                "FROM runs WHERE result_digest = ?",
                (report["result_digest"],),
            ).fetchone()
            self.assertEqual(row[0], "verified")
            self.assertEqual(row[1], "gen-v1")
            self.assertEqual(row[2], "allowed")
            self.assertEqual(
                row[3], report["source_generation"]["manifest_sha256"]
            )
            connection.execute(
                "UPDATE runs SET quality_gate = 'allowed' WHERE result_digest = ?",
                (report["result_digest"],),
            )
            connection.commit()
        finally:
            connection.close()
        # Flip the generation status in the ledger only; verify must notice.
        connection = sqlite3.connect(paths["ledger"])
        try:
            connection.execute(
                "UPDATE runs SET generation_status = 'failed' WHERE result_digest = ?",
                (report["result_digest"],),
            )
            connection.commit()
        finally:
            connection.close()
        verification = verify_report_and_ledger(paths["json"], paths["ledger"])
        self.assertFalse(verification.ok)
        self.assertTrue(any("generation" in issue for issue in verification.issues))

    def test_old_ledger_schema_is_migrated_and_generation_columns_added(self):
        import sqlite3
        from topoaudit.ledger import write_report

        ledger = self.root / "legacy.sqlite"
        connection = sqlite3.connect(ledger)
        try:
            connection.execute(
                "CREATE TABLE runs (result_digest TEXT PRIMARY KEY, "
                "schema_version TEXT NOT NULL, run_label TEXT NOT NULL, "
                "input_digest TEXT NOT NULL, config_digest TEXT NOT NULL, "
                "summary_json TEXT NOT NULL, limitations_json TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()
        out = self.root / "migrated"
        report, paths = execute(self._config_path("gen-v1.manifest.json"), out)
        # Reuse the v1 run row shape by writing the new report into the legacy DB.
        write_report(ledger, report)
        connection = sqlite3.connect(ledger)
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(runs)")
            }
            self.assertTrue(
                {"source_generation_json", "generation_status", "quality_gate",
                 "manifest_sha256", "manifest_digest"} <= columns
            )
            row = connection.execute(
                "SELECT generation_status, quality_gate FROM runs "
                "WHERE result_digest = ?",
                (report["result_digest"],),
            ).fetchone()
            self.assertEqual(row, ("verified", "allowed"))
        finally:
            connection.close()

    def test_cli_verify_states_unknown_generation_honestly(self):
        data = _config([_layer("alpha", "alpha.json"), _layer("beta", "beta.json")])
        path = self.root / "observe.config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        out = self.root / "cli-observe"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["run", "--config", str(path), "--out", str(out)]), 0)
        verify_stdout = io.StringIO()
        with contextlib.redirect_stdout(verify_stdout):
            code = main(
                [
                    "verify",
                    "--report",
                    str(out / "report.json"),
                    "--ledger",
                    str(out / "audit.sqlite"),
                ]
            )
        self.assertEqual(code, 0)
        text = verify_stdout.getvalue()
        self.assertIn("source_generation=unverified", text)
        self.assertIn("不可作同代质量放行", text)

    def test_manifest_has_correct_relative_path_check(self):
        config = load_config(self._config_path("gen-v1.manifest.json"))
        loaded = load_layers(config)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(
            [item.config.layer_id for item in loaded], ["alpha", "beta"]
        )


if __name__ == "__main__":
    unittest.main()
