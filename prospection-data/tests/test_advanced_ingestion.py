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
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        cursor = payload.get("cursor")
        if cursor is None:
            contacts = [{"id": "p1"}, {"id": "p2"}]
            next_cursor = "cursor-2"
        elif cursor == "cursor-2":
            contacts = [{"id": "p3"}]
            next_cursor = None
        else:
            contacts = []
            next_cursor = None
        body = json.dumps({"results": contacts, "meta": {"next_cursor": next_cursor}}).encode()
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

    def test_zip_and_xml_formats(self):
        archive = self.root / "mixed.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("contacts.csv", "email;ville\na@x.fr;Dax\n")
            z.writestr("contacts.jsonl", '{"email":"b@y.fr","ville":"Pau"}\n')
        result = ingest_file(self.data, archive, source_key="zip-test")
        self.assertEqual(result["rows"], 2)

        xml = self.root / "contacts.xml"
        xml.write_text(
            "<contacts><contact><email>a@x.fr</email><ville>Dax</ville></contact>"
            "<contact><email>b@y.fr</email><ville>Pau</ville></contact></contacts>",
            encoding="utf-8",
        )
        result = ingest_file(
            self.data, xml, source_key="xml-test", xml_record_tag="contact"
        )
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

    def test_http_post_cursor_and_bearer_env(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old = os.environ.get("RCVO_TEST_TOKEN")
        os.environ["RCVO_TEST_TOKEN"] = "test-token"
        try:
            config = {
                "source_key": "http-post-test",
                "connector_key": "cursor",
                "url": f"http://127.0.0.1:{server.server_port}/search",
                "method": "POST",
                "auth": {"type": "bearer_env", "env": "RCVO_TEST_TOKEN"},
                "json_body": {"market": "vo"},
                "response": {"format": "json", "records_path": "results"},
                "pagination": {
                    "type": "cursor",
                    "target": "json_body",
                    "cursor_param": "cursor",
                    "next_cursor_path": "meta.next_cursor"
                },
                "retry": {"max_attempts": 2, "backoff_seconds": 0.01}
            }
            result = run_http_connector(self.data, config, restart=True)
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["requests"], 2)
            self.assertTrue(result["checkpoint"]["complete"])
        finally:
            if old is None:
                os.environ.pop("RCVO_TEST_TOKEN", None)
            else:
                os.environ["RCVO_TEST_TOKEN"] = old
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    unittest.main()
