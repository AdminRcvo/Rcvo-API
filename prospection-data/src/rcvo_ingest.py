#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from http_source import load_connector_config, run_http_connector
from ingest_runtime import ingest_directory, ingest_file
from raw_queue import queue_stats
from rcvo_data import init_databases

def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", required=True)
    p.add_argument("--source-name")
    p.add_argument("--source-kind", default="provider_export")
    p.add_argument("--provider-url")
    p.add_argument("--format", default="auto")
    p.add_argument("--delimiter")
    p.add_argument("--records-path")
    p.add_argument("--xml-record-tag")
    p.add_argument("--sheet")
    p.add_argument("--batch-size", type=int, default=25000)
    p.add_argument("--id-field")
    p.add_argument("--hash-payload", action="store_true")
    p.add_argument("--force", action="store_true")

def common_kwargs(args) -> dict:
    return {
        "source_name": args.source_name,
        "source_kind": args.source_kind,
        "provider_url": args.provider_url,
        "fmt": args.format,
        "delimiter": args.delimiter,
        "records_path": args.records_path,
        "xml_record_tag": args.xml_record_tag,
        "sheet": args.sheet,
        "batch_size": args.batch_size,
        "id_field": args.id_field,
        "hash_payload": args.hash_payload,
    }

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rcvo-ingest")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("queue-stats")

    fp = sub.add_parser("file")
    add_common(fp)
    fp.add_argument("--file", type=Path, required=True)

    dp = sub.add_parser("dir")
    add_common(dp)
    dp.add_argument("--directory", type=Path, required=True)
    dp.add_argument("--no-recursive", action="store_true")

    hp = sub.add_parser("http")
    hp.add_argument("--config", type=Path, required=True)
    hp.add_argument("--restart", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init":
        raw, ref = init_databases(args.data_dir)
        print(json.dumps({"raw": str(raw), "reference": str(ref)}, indent=2))
        return 0

    if args.command == "queue-stats":
        raw, _ = init_databases(args.data_dir)
        print(json.dumps(queue_stats(raw), indent=2))
        return 0

    if args.command == "file":
        result = ingest_file(
            args.data_dir,
            args.file,
            source_key=args.source,
            force=args.force,
            **common_kwargs(args),
        )
    elif args.command == "dir":
        result = ingest_directory(
            args.data_dir,
            args.directory,
            source_key=args.source,
            recursive=not args.no_recursive,
            force=args.force,
            **common_kwargs(args),
        )
    elif args.command == "http":
        result = run_http_connector(
            args.data_dir,
            load_connector_config(args.config),
            restart=args.restart,
        )
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
