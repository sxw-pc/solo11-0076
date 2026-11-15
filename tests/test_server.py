import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from topoaudit.errors import ServerError
from topoaudit.pipeline import execute
from topoaudit.server import create_server
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fixtures" / "demo" / "config.json"

class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.report, paths = execute(CONFIG, cls.tmp.name)
        cls.digest = cls.report["result_digest"]
        cls.server = create_server(paths["ledger"], host="127.0.0.1", port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.opener = build_opener(ProxyHandler({}))
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()
    def _get(self, path, method="GET", data=None):
        request = Request(self.base + path, method=method, data=data)
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
    def test_health_and_index(self):
        status, headers, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "read_only": True})
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        status, _, page = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("demo-local-topology", page.decode("utf-8"))
        self.assertIn(self.digest, page.decode("utf-8"))
    def test_run_page_and_rules_page(self):
        status, _, page = self._get(f"/runs/{self.digest}")
        self.assertEqual(status, 200)
        text = page.decode("utf-8")
        self.assertIn("POLYGON_OVERLAP", text)
        self.assertIn("规则说明", text)
        self.assertNotIn("<script", text.lower())
        self.assertNotIn("https://", text.lower())
        status, _, rules = self._get("/rules")
        self.assertEqual(status, 200)
        self.assertIn("EMPTY_LAYER", rules.decode("utf-8"))
    def test_findings_json_and_filters(self):
        status, headers, body = self._get(f"/runs/{self.digest}/findings.json?severity=info")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(body)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["findings"][0]["rule_id"], "NEAR_MISS_ENDPOINT")
        status, _, body = self._get(f"/runs/{self.digest}/findings.json?layer=roads&rule=DANGLING_ENDPOINT")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["count"], 3)
    def test_rejects_unknown_routes_traversal_and_filters(self):
        self.assertEqual(self._get("/runs/" + "0" * 64)[0], 404)
        self.assertEqual(self._get("/runs/not-a-digest")[0], 404)
        self.assertEqual(self._get("/runs/%2e%2e/")[0], 404)
        self.assertEqual(self._get(f"/runs/{self.digest}/findings.json?unknown=1")[0], 400)
        self.assertEqual(self._get(f"/runs/{self.digest}/findings.json?severity=nope")[0], 400)
        self.assertEqual(self._get("/healthz", method="POST", data=b"x")[0], 405)
        self.assertEqual(self._get("/healthz", method="DELETE")[0], 405)
    def test_only_loopback_can_be_bound(self):
        with self.assertRaises(ServerError):
            create_server("unused.sqlite", host="0.0.0.0", port=0)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
if __name__ == "__main__":
    unittest.main()
