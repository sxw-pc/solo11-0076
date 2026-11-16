import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from topoaudit.cli import main
from topoaudit.demo import DEMO_DIR
ROOT = Path(__file__).resolve().parent.parent
CONFIG = DEMO_DIR / "config.json"

def fixture_hashes():
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DEMO_DIR.iterdir())
        if path.is_file()
    }

class CliDemoTest(unittest.TestCase):
    def test_demo_end_to_end_and_inputs_are_unchanged(self):
        before = fixture_hashes()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo"
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["demo", "--out", str(out)])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertIn("fixture 预期计数核对通过", stdout.getvalue())
            for name in ("report.json", "report.md", "report.html", "audit.sqlite", "inputs.json", "summary.txt"):
                self.assertTrue((out / name).is_file(), name)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["total_findings"], 25)
            self.assertEqual(report["summary"]["total_features"], 35)
            self.assertEqual(len(report["findings"]), 25)
            self.assertEqual({item["rule_id"] for item in report["findings"]}, set(report["rules"][i]["rule_id"] for i in range(20)))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["verify", "--report", str(out / "report.json"), "--ledger", str(out / "audit.sqlite")]), 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["query", "--ledger", str(out / "audit.sqlite")]), 0)
        self.assertEqual(before, fixture_hashes())
    def test_demo_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["demo", "--out", str(first)]), 0)
                self.assertEqual(main(["demo", "--out", str(second)]), 0)
                self.assertEqual(main(["demo", "--out", str(first)]), 0)
            report_a = json.loads((first / "report.json").read_text(encoding="utf-8"))
            report_b = json.loads((second / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report_a["result_digest"], report_b["result_digest"])
            connection = sqlite3.connect(first / "audit.sqlite")
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 25)
            finally:
                connection.close()
    def test_demo_refuses_output_inside_fixtures(self):
        before = fixture_hashes()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = main(["demo", "--out", str(DEMO_DIR)])
        self.assertEqual(code, 2)
        self.assertIn("TOPO-DEMO-001", stderr.getvalue())
        self.assertEqual(before, fixture_hashes())
    def test_verify_tampered_report_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["run", "--config", str(CONFIG), "--out", str(out)]), 0)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            report["result_digest"] = "0" * 64
            tampered = Path(tmp) / "tampered.json"
            tampered.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["verify", "--report", str(tampered), "--ledger", str(out / "audit.sqlite")]), 1)
    def test_no_command_returns_usage_error(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 2)
if __name__ == "__main__":
    unittest.main()
