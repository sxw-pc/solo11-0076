"""Fault-injection and recovery drills for the atomic publish pipeline.

Synthetic A/B audit inputs are built in independent temporary directories,
interrupted at each designated cut point via ``TOPOAUDIT_FAULT_AT`` (hard
``os._exit`` crash in a real subprocess), then recovered and cross-checked
with ``python -m topoaudit verify --report <当前report.json> --ledger
<当前audit.sqlite>``.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from topoaudit import publish
from topoaudit.config import load_config
from topoaudit.engine import run_audit
from topoaudit.ledger_query import get_publish_log, list_runs, run_exists
from topoaudit.pipeline import execute
from topoaudit.report_json import build_report

ROOT = Path(__file__).resolve().parent.parent
FAULT_ENV = "TOPOAUDIT_FAULT_AT"


def make_input_set(directory: Path, label: str, points) -> Path:
    """Create a self-contained synthetic audit input set (config + GeoJSON)."""
    directory.mkdir(parents=True, exist_ok=True)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [x, y]},
            "properties": {"id": pid},
        }
        for pid, x, y in points
    ]
    (directory / "pts.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    config = {
        "schema_version": "1.0",
        "run_label": label,
        "tolerance": {},
        "crs": {
            "name": "LOCAL_XY",
            "type": "planar",
            "bounds": {"min_x": 0, "min_y": 0, "max_x": 1000, "max_y": 1000},
        },
        "layers": [
            {
                "id": "sites",
                "path": "pts.geojson",
                "format": "geojson",
                "geometry_types": ["Point"],
                "id_field": "id",
                "schema": {"id": {"type": "string", "required": True}},
            }
        ],
    }
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return directory / "config.json"


def digest_of(config_path: Path) -> str:
    return build_report(run_audit(load_config(config_path)))["result_digest"]


class PublishRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.config_a = make_input_set(
            base / "inputs_a", "audit-A", [("A-1", 10, 10), ("A-2", 20, 20)]
        )
        self.config_b = make_input_set(
            base / "inputs_b",
            "audit-B",
            [("B-1", 11, 11), ("B-2", 21, 21), ("B-3", 31, 31)],
        )
        self.out = base / "out"
        self.digest_a = digest_of(self.config_a)
        self.digest_b = digest_of(self.config_b)
        self.assertNotEqual(self.digest_a, self.digest_b)

    def tearDown(self):
        publish.set_fault_hook(None)
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def cli(self, *args, fault_at=None, extra_env=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env.pop(FAULT_ENV, None)
        env.pop("TOPOAUDIT_RECOVERY_POLICY", None)
        if fault_at is not None:
            env[FAULT_ENV] = fault_at
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "topoaudit", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )

    def run_ok(self, config):
        result = self.cli("run", "--config", str(config), "--out", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def run_crash(self, config, fault_at):
        result = self.cli(
            "run", "--config", str(config), "--out", str(self.out), fault_at=fault_at
        )
        self.assertEqual(result.returncode, 99, result.stderr)
        return result

    def recover(self, *extra):
        result = self.cli("recover", "--out", str(self.out), *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def verify_current(self):
        """The acceptance command against the currently visible result."""
        return self.cli(
            "verify",
            "--report",
            str(self.out / "report.json"),
            "--ledger",
            str(self.out / "audit.sqlite"),
        )

    def verify_version(self, digest):
        return self.cli(
            "verify",
            "--report",
            str(self.out / "versions" / digest / "report.json"),
            "--ledger",
            str(self.out / "audit.sqlite"),
        )

    def current_digest(self):
        report = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        return report["result_digest"]

    def visible_digests(self):
        """Digest identity carried by every externally visible artifact."""
        found = set()
        report = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        found.add(report["result_digest"])
        inputs = json.loads((self.out / "inputs.json").read_text(encoding="utf-8"))
        found.add(inputs["result_digest"])
        for name in ("report.md", "report.html", "summary.txt"):
            text = (self.out / name).read_text(encoding="utf-8")
            for digest in (self.digest_a, self.digest_b):
                if digest in text:
                    found.add(digest)
        return found

    def ledger_counts(self):
        connection = sqlite3.connect(self.out / "audit.sqlite")
        try:
            return {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("runs", "layers", "findings", "input_files")
            }
        finally:
            connection.close()

    def assert_visible_is_single_version(self, digest):
        self.assertEqual(self.current_digest(), digest)
        self.assertEqual(self.visible_digests(), {digest})
        result = self.verify_current()
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_version_files_match_manifest(self, digest):
        directory = self.out / "versions" / digest
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["result_digest"], digest)
        self.assertEqual(sorted(manifest["files"]), sorted(publish.REPORT_FILE_NAMES))
        for name, meta in manifest["files"].items():
            data = (directory / name).read_bytes()
            import hashlib

            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"], name)
            self.assertEqual(len(data), meta["size_bytes"], name)

    # -- cut point 1: partial report files written -------------------------

    def test_cut1_partial_reports_keep_previous_version(self):
        self.run_ok(self.config_a)
        self.run_crash(self.config_b, publish.FAULT_STAGING_PARTIAL)

        staging = self.out / ".staging" / self.digest_b
        self.assertTrue(staging.is_dir())
        self.assertFalse((staging / "manifest.json").exists())
        # A is still the complete visible result right after the crash.
        self.assert_visible_is_single_version(self.digest_a)
        self.assertFalse(run_exists(self.out / "audit.sqlite", self.digest_b))

        recovered = self.recover()
        self.assertIn("rollback", recovered.stdout)
        self.assertIn(self.digest_b, recovered.stdout)
        self.assertFalse(staging.exists(), "rolled-back staging must be removed")
        self.assert_visible_is_single_version(self.digest_a)

        events = [
            row for row in get_publish_log(self.out / "audit.sqlite", self.digest_b)
        ]
        self.assertEqual([row["event"] for row in events], ["recovery_rollback"])
        self.assertEqual(events[0]["detail"]["reason"], "staging_incomplete")

        # Re-running the same input now publishes B cleanly.
        self.run_ok(self.config_b)
        self.assert_visible_is_single_version(self.digest_b)
        self.assertEqual(self.verify_version(self.digest_a).returncode, 0)
        self.assert_version_files_match_manifest(self.digest_b)

    # -- cut point 2: all reports written, ledger not committed ------------

    def test_cut2_reports_complete_ledger_pending_keeps_previous(self):
        self.run_ok(self.config_a)
        self.run_crash(self.config_b, publish.FAULT_STAGING_COMPLETE)

        staging = self.out / ".staging" / self.digest_b
        self.assertTrue((staging / "manifest.json").is_file())
        for name in publish.REPORT_FILE_NAMES:
            self.assertTrue((staging / name).is_file(), name)
        self.assert_visible_is_single_version(self.digest_a)
        self.assertFalse(
            run_exists(self.out / "audit.sqlite", self.digest_b),
            "ledger rows for B must not exist before the commit cut point",
        )

        recovered = self.recover()
        self.assertIn("rollback", recovered.stdout)
        self.assertFalse(staging.exists())
        self.assert_visible_is_single_version(self.digest_a)
        self.assertFalse(run_exists(self.out / "audit.sqlite", self.digest_b))

        events = [
            row for row in get_publish_log(self.out / "audit.sqlite", self.digest_b)
        ]
        self.assertEqual([row["event"] for row in events], ["recovery_rollback"])
        self.assertEqual(events[0]["detail"]["reason"], "ledger_not_committed")

        self.run_ok(self.config_b)
        self.assert_visible_is_single_version(self.digest_b)
        self.assertEqual(self.verify_version(self.digest_a).returncode, 0)

    # -- cut point 3: ledger committed, current pointer not switched -------

    def test_cut3_rollforward_publishes_committed_session(self):
        self.run_ok(self.config_a)
        self.run_crash(self.config_b, publish.FAULT_LEDGER_COMMITTED)

        # The ledger commit went through, but B must not be visible yet.
        self.assertTrue(run_exists(self.out / "audit.sqlite", self.digest_b))
        self.assert_visible_is_single_version(self.digest_a)

        recovered = self.recover()
        self.assertIn("rollforward", recovered.stdout)
        self.assertIn(self.digest_b, recovered.stdout)
        self.assert_visible_is_single_version(self.digest_b)
        self.assert_version_files_match_manifest(self.digest_b)

        events = [
            row for row in get_publish_log(self.out / "audit.sqlite", self.digest_b)
        ]
        published = [row for row in events if row["event"] == "published"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["detail"]["via"], "recovery_rollforward")
        self.assertEqual(published[0]["detail"]["previous_current"], self.digest_a)

        # The previously published version stays reviewable.
        self.assertEqual(self.verify_version(self.digest_a).returncode, 0)
        self.assert_version_files_match_manifest(self.digest_a)

    def test_cut3_rollback_keeps_previous_and_is_traceable(self):
        self.run_ok(self.config_a)
        self.run_crash(self.config_b, publish.FAULT_LEDGER_COMMITTED)
        self.assert_visible_is_single_version(self.digest_a)

        recovered = self.recover("--policy", "rollback")
        self.assertIn("rollback", recovered.stdout)
        self.assert_visible_is_single_version(self.digest_a)
        self.assertFalse((self.out / ".staging" / self.digest_b).exists())
        self.assertFalse((self.out / "versions" / self.digest_b).exists())

        events = [
            row for row in get_publish_log(self.out / "audit.sqlite", self.digest_b)
        ]
        self.assertEqual(
            [row["event"] for row in events], ["ledger_committed", "recovery_rollback"]
        )
        self.assertEqual(events[-1]["detail"]["reason"], "policy")
        self.assertEqual(events[-1]["detail"]["policy"], "rollback")
        self.assertEqual(events[-1]["detail"]["preserved_current"], self.digest_a)

        # B's ledger rows remain as append-only evidence, but B is not the
        # current result; re-running B publishes it without duplicating rows.
        self.assertTrue(run_exists(self.out / "audit.sqlite", self.digest_b))
        before = self.ledger_counts()
        self.run_ok(self.config_b)
        self.assert_visible_is_single_version(self.digest_b)
        self.assertEqual(self.ledger_counts(), before)
        self.assertEqual(self.verify_version(self.digest_a).returncode, 0)

    def test_cut3_crash_after_verified_event_recovers(self):
        self.run_ok(self.config_a)
        self.run_crash(self.config_b, publish.FAULT_PUBLISH_VERIFIED)
        self.assert_visible_is_single_version(self.digest_a)
        self.recover()
        self.assert_visible_is_single_version(self.digest_b)
        self.assertEqual(self.verify_version(self.digest_a).returncode, 0)

    # -- idempotency / no mixed versions ------------------------------------

    def test_rerun_same_input_never_produces_mixed_versions(self):
        self.run_ok(self.config_a)
        self.run_ok(self.config_b)
        counts = self.ledger_counts()
        self.assertEqual(counts["runs"], 2)
        log_len = len(get_publish_log(self.out / "audit.sqlite"))

        # Re-running the current input is a strict no-op.
        self.run_ok(self.config_b)
        self.assertEqual(self.ledger_counts(), counts)
        self.assertEqual(len(get_publish_log(self.out / "audit.sqlite")), log_len)
        self.assert_visible_is_single_version(self.digest_b)

        # Switching back to A reuses the content-addressed version and never
        # duplicates ledger rows.
        manifest_a = json.loads(
            (self.out / "versions" / self.digest_a / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.run_ok(self.config_a)
        self.assertEqual(self.ledger_counts(), counts)
        self.assert_visible_is_single_version(self.digest_a)
        manifest_a_after = json.loads(
            (self.out / "versions" / self.digest_a / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest_a, manifest_a_after)

        self.run_ok(self.config_b)
        self.assertEqual(self.ledger_counts(), counts)
        self.assert_visible_is_single_version(self.digest_b)
        runs = list_runs(self.out / "audit.sqlite")
        self.assertEqual(
            sorted(row["result_digest"] for row in runs),
            sorted([self.digest_a, self.digest_b]),
        )

    def test_visible_set_is_consistent_at_every_cut_point(self):
        self.run_ok(self.config_a)
        for point in (
            publish.FAULT_STAGING_PARTIAL,
            publish.FAULT_STAGING_COMPLETE,
            publish.FAULT_LEDGER_COMMITTED,
            publish.FAULT_PUBLISH_VERIFIED,
        ):
            self.run_crash(self.config_b, point)
            # At no observable moment may the visible set mix A and B.
            self.assert_visible_is_single_version(self.digest_a)
            self.recover()
            self.run_ok(self.config_b)
            self.assert_visible_is_single_version(self.digest_b)
            self.run_ok(self.config_a)
            self.assert_visible_is_single_version(self.digest_a)

    # -- in-process recovery details ----------------------------------------

    def test_tampered_staging_rolls_back_rollforward_attempt(self):
        class Boom(Exception):
            pass

        execute(self.config_a, self.out)

        def hook(point):
            if point == publish.FAULT_LEDGER_COMMITTED:
                raise Boom(point)

        publish.set_fault_hook(hook)
        try:
            with self.assertRaises(Boom):
                execute(self.config_b, self.out)
        finally:
            publish.set_fault_hook(None)
        staging = self.out / ".staging" / self.digest_b
        with open(staging / "report.md", "a", encoding="utf-8") as handle:
            handle.write("tampered\n")

        actions = publish.recover_pending(self.out)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "rollback")
        self.assertEqual(actions[0].reason, "verification_failed")
        self.assertFalse(staging.exists())
        self.assert_visible_is_single_version(self.digest_a)
        events = [
            row for row in get_publish_log(self.out / "audit.sqlite", self.digest_b)
        ]
        self.assertEqual(events[-1]["event"], "recovery_rollback")
        self.assertEqual(events[-1]["detail"]["reason"], "verification_failed")
        self.assertTrue(events[-1]["detail"]["issues"])

    def test_republish_same_input_is_noop_in_process(self):
        execute(self.config_a, self.out)
        log_before = get_publish_log(self.out / "audit.sqlite")
        execute(self.config_a, self.out)
        self.assertEqual(get_publish_log(self.out / "audit.sqlite"), log_before)
        self.assertFalse((self.out / ".staging").exists())
        self.assert_visible_is_single_version(self.digest_a)

    def test_legacy_flat_layout_is_adopted_without_loss(self):
        legacy = Path(self.tmp.name) / "legacy_out"
        full = Path(self.tmp.name) / "full_out"
        execute(self.config_a, full)
        legacy.mkdir()
        for name in (*publish.REPORT_FILE_NAMES, "audit.sqlite"):
            shutil.copy2(full / name, legacy / name)
        original = {
            name: (legacy / name).read_bytes() for name in publish.REPORT_FILE_NAMES
        }

        execute(self.config_b, legacy)
        self.assertEqual(
            json.loads((legacy / "report.json").read_text(encoding="utf-8"))[
                "result_digest"
            ],
            self.digest_b,
        )
        adopted = legacy / "versions" / self.digest_a
        self.assertTrue(adopted.is_dir())
        for name in publish.REPORT_FILE_NAMES:
            self.assertEqual((adopted / name).read_bytes(), original[name], name)
        result = self.cli(
            "verify",
            "--report",
            str(adopted / "report.json"),
            "--ledger",
            str(legacy / "audit.sqlite"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [
            row
            for row in get_publish_log(legacy / "audit.sqlite", self.digest_a)
            if row["event"] == "published"
        ]
        self.assertTrue(any(row["detail"]["via"] == "legacy_adopted" for row in events))

    def test_recover_cli_without_pending_or_missing_dir(self):
        self.out.mkdir()
        result = self.recover()
        self.assertIn("没有待恢复", result.stdout)
        missing = self.cli("recover", "--out", str(self.out / "does-not-exist"))
        self.assertEqual(missing.returncode, 2)

    def test_fault_hook_points_are_stable(self):
        self.assertEqual(
            set(publish.FAULT_POINTS),
            {
                "staging.partial",
                "staging.complete",
                "ledger.committed",
                "publish.verified",
            },
        )


if __name__ == "__main__":
    unittest.main()
