#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ingest_runtime import ingest_bytes

class Handler(BaseHTTPRequestHandler):
    server_version = "RcvoRawReceiver/1.0"

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/ingest":
            self._json(404, {"error": "not_found"})
            return
        expected = os.getenv(self.server.token_env)
        auth = self.headers.get("Authorization", "")
        if not expected or auth != "Bearer " + expected:
            self._json(401, {"error": "unauthorized"})
            return
        source = self.headers.get("X-Rcvo-Source")
        if not source:
            self._json(400, {"error": "X-Rcvo-Source required"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > self.server.max_body_bytes:
            self._json(413, {"error": "invalid_body_size"})
            return
        data = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        fmt = self.headers.get("X-Rcvo-Format") or {
            "application/json": "json",
            "application/x-ndjson": "jsonl",
            "application/jsonl": "jsonl",
            "text/csv": "csv",
            "text/tab-separated-values": "tsv",
            "application/xml": "xml",
            "text/xml": "xml",
        }.get(content_type, "jsonl")
        ext = {
            "json": ".json", "jsonl": ".jsonl", "csv": ".csv",
            "tsv": ".tsv", "xml": ".xml",
        }.get(fmt, ".bin")
        try:
            result = ingest_bytes(
                self.server.data_dir,
                data,
                source_key=source,
                original_filename="webhook" + ext,
                origin_uri="webhook:" + self.client_address[0],
                source_kind="webhook",
                acquisition_mode="webhook",
                fmt=fmt,
                records_path=self.headers.get("X-Rcvo-Records-Path"),
                batch_size=self.server.batch_size,
            )
            self._json(202, result)
        except Exception as exc:
            self._json(500, {"error": "ingest_failed", "message": str(exc)})

    def log_message(self, fmt, *args):
        return

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--token-env", default="RCVO_RAW_INGEST_TOKEN")
    p.add_argument("--max-body-mb", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=25000)
    args = p.parse_args()

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.data_dir = args.data_dir
    server.token_env = args.token_env
    server.max_body_bytes = args.max_body_mb * 1024 * 1024
    server.batch_size = args.batch_size
    server.serve_forever()

if __name__ == "__main__":
    main()
