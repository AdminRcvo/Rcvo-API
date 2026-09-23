import gzip
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from http_source import run_http_connector
from ingest_runtime import connect_fast, ingest_file
from raw_queue import ack_record, claim_records, fail_record, queue_stats
from rcvo_data import init_databases

class ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = int(parse_qs(urlparse(self.path).query).get("page", ["1"])[0])
        pages = {
            1: [{"id": "a"}, {"id": "b"}],
            2: [{"id": "c"}],
            3: [],
        }
        body = json.dumps({"data": {"contacts": pages.get(page, [])}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, fmt, *args):
        return

class AdvancedIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.raw, _ = init_databases(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jsonl_dead_letter_and_idempotent_artifact(self):
        path = self.root / "contacts.jsonl"
        path.write_text('{"id":1}\nnot-json\n{"id":2}\n', encoding="utf-8")
        first = ingest_file(self.data, path, source_key="jsonl-test")
        second = ingest_file(self.data, path, source_key="jsonl-test")
        self.assertEqual(first["rows"], 2)
        self.assertEqual(first["rejected"], 1)
        self.assertTrue(second["skipped"])
        with connect_fast(self.raw) as conn:
            failures = conn.execute("SELECT count(*) FROM raw_ingest_failures").fetchone()[0]
            artifacts = conn.execute("SELECT count(*) FROM raw_artifacts").fetchone()[0]
        self.assertEqual(failures, 1)
        self.assertEqual(artifacts, 1)

    def test_gzip_semicolon_csv(self):
        path = self.root / "export.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write("email;entreprise;ville\na@x.fr;Garage X;Dax\nb@y.fr;Garage Y;Pau\n")
        result = ingest_file(self.data, path, source_key="gzip-csv")
        self.assertEqual(result["rows"], 2)

    def test_xlsx(self):
        from openpyxl import Workbook
        path = self.root / "contacts.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["email", "entreprise", "ville"])
        ws.append(["a@x.fr", "Garage X", "Dax"])
        ws.append(["b@y.fr", "Garage Y", "Pau"])
        wb.save(path)
        result = ingest_file(self.data, path, source_key="xlsx-test")
        self.assertEqual(result["rows"], 2)

    def test_queue_leases_ack_and_terminal_error(self):
        path = self.root / "queue.jsonl"
        path.write_text('{"id":1}\n{"id":2}\n{"id":3}\n', encoding="utf-8")
        ingest_file(self.data, path, source_key="queue-test")
        claimed = claim_records(self.raw, "worker-A", limit=2, lease_seconds=60)
        self.assertEqual(len(claimed), 2)
        ack_record(self.raw, claimed[0]["id"], "worker-A")
        state = fail_record(self.raw, claimed[1]["id"], "worker-A", "boom", max_attempts=1)
        self.assertEqual(state, "error")
        stats = queue_stats(self.raw)
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["pending"], 1)

    def test_http_page_pagination_and_checkpoint(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {
                "source_key": "http-test",
                "source_name": "HTTP Test",
                "connector_key": "contacts",
                "url": f"http://127.0.0.1:{server.server_port}/contacts",
                "method": "GET",
                "response": {"format": "json", "records_path": "data.contacts"},
                "pagination": {
                    "type": "page",
                    "param": "page",
                    "start": 1,
                    "page_size_param": "per_page",
                    "page_size": 2,
                    "stop_on_short_page": True
                },
                "retry": {"max_attempts": 2, "backoff_seconds": 0.01}
            }
            result = run_http_connector(self.data, config, restart=True)
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["requests"], 2)
            self.assertEqual(result["checkpoint"]["page"], 3)
            with connect_fast(self.raw) as conn:
                artifacts = conn.execute("SELECT count(*) FROM raw_artifacts").fetchone()[0]
                fetches = conn.execute("SELECT count(*) FROM raw_fetch_runs WHERE status='complete'").fetchone()[0]
            self.assertEqual(artifacts, 2)
            self.assertEqual(fetches, 1)
        finally:
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    unittest.main()
