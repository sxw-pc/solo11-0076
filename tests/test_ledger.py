import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from topoaudit.ledger import write_report
from topoaudit.ledger_query import (
    get_findings,
    get_input_files,
    get_rule_catalog,
    get_run,
    list_runs,
    run_exists,
)
from topoaudit.ledger_verify import verify_report_and_ledger
from topoaudit.pipeline import execute
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fixtures" / "demo" / "config.json"

class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.report, self.paths = execute(CONFIG, self.out)
        self.ledger = self.paths["ledger"]
        self.digest = self.report["result_digest"]
    def tearDown(self):
        self.tmp.cleanup()
    def _counts(self):
        connection = sqlite3.connect(self.ledger)
        try:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("runs", "layers", "findings", "input_files", "rule_catalog")
            }
        finally:
            connection.close()
    def test_schema_and_rows(self):
        self.assertEqual(
            self._counts(),
            {"runs": 1, "layers": 6, "findings": 29, "input_files": 6, "rule_catalog": 20},
        )
        self.assertEqual(get_run(self.ledger, self.digest)["run_label"], "demo-local-topology")
        self.assertEqual(len(get_input_files(self.ledger, self.digest)), 6)
        self.assertEqual(len(get_rule_catalog(self.ledger)), 20)
        self.assertTrue(run_exists(self.ledger, self.digest))
        self.assertFalse(run_exists(self.ledger, "0" * 64))
    def test_idempotent_write_by_result_digest(self):
        before = self._counts()
        write_report(self.ledger, self.report)
        write_report(self.ledger, self.report)
        self.assertEqual(before, self._counts())
        self.assertEqual(len(list_runs(self.ledger)), 1)
    def test_filters_and_queries(self):
        infos = get_findings(self.ledger, self.digest, severity="info")
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0]["rule_id"], "NEAR_MISS_ENDPOINT")
        roads = get_findings(self.ledger, self.digest, layer_id="roads")
        self.assertEqual(len(roads), 11)
        duplicates = get_findings(self.ledger, self.digest, rule_id="DUPLICATE_FEATURE_ID")
        self.assertEqual(duplicates[0]["evidence"]["value"], "P-003")
        self.assertEqual(get_findings(self.ledger, self.digest, severity="error", layer_id="points"), get_findings(self.ledger, self.digest, severity="error", layer_id="points"))
        self.assertEqual(get_findings(self.ledger, "0" * 64), [])
    def test_verify_success(self):
        result = verify_report_and_ledger(self.paths["json"], self.ledger)
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.finding_count, 29)
        self.assertEqual(result.layer_count, 6)
    def test_verify_detects_report_tampering(self):
        tampered = json.loads(self.paths["json"].read_text(encoding="utf-8"))
        tampered["summary"]["total_findings"] = 999
        target = self.out / "tampered.json"
        target.write_text(json.dumps(tampered), encoding="utf-8")
        result = verify_report_and_ledger(target, self.ledger)
        self.assertFalse(result.ok)
        self.assertTrue(any("result_digest" in issue for issue in result.issues))
    def test_verify_detects_ledger_summary_tampering(self):
        connection = sqlite3.connect(self.ledger)
        try:
            connection.execute("UPDATE runs SET summary_json = ? WHERE result_digest = ?", ("{}", self.digest))
            connection.commit()
        finally:
            connection.close()
        result = verify_report_and_ledger(self.paths["json"], self.ledger)
        self.assertFalse(result.ok)
        self.assertTrue(any("summary" in issue for issue in result.issues))
if __name__ == "__main__":
    unittest.main()
