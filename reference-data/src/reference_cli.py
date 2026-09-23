#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reference_engine import add_suppression, connect, init_db, promote, stats

ROOT=Path(__file__).resolve().parents[1]

def main(argv=None) -> int:
    p=argparse.ArgumentParser(prog="rcvo-reference")
    p.add_argument("--db",type=Path,default=ROOT/"data"/"rcvo-reference.sqlite")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
    sub.add_parser("check")

    one=sub.add_parser("promote")
    one.add_argument("--file",type=Path,required=True)

    many=sub.add_parser("promote-jsonl")
    many.add_argument("--file",type=Path,required=True)
    many.add_argument("--continue-on-error",action="store_true")

    prospect=sub.add_parser("prospectable")
    prospect.add_argument("--limit",type=int,default=100)

    sup=sub.add_parser("suppress")
    sup.add_argument("--scope-type",required=True,choices=["contact","email","organization","domain"])
    sup.add_argument("--scope-value",required=True)
    sup.add_argument("--reason",required=True,choices=["optout","hard_bounce","client_rcvo","manual_exclusion","legal_hold","invalid_address","other"])
    sup.add_argument("--permanent",action="store_true")
    sup.add_argument("--notes")

    args=p.parse_args(argv)

    if args.command=="init":
        init_db(args.db)
        print(json.dumps({"db":str(args.db),"status":"initialized"},indent=2))
        return 0
    if args.command=="stats":
        print(json.dumps(stats(args.db),indent=2))
        return 0
    if args.command=="check":
        init_db(args.db)
        with connect(args.db) as conn:
            quick=conn.execute("PRAGMA quick_check").fetchone()[0]
            fk=conn.execute("PRAGMA foreign_key_check").fetchall()
        print(json.dumps({"quick_check":quick,"foreign_key_errors":len(fk)},indent=2))
        return 0
    if args.command=="promote":
        payload=json.loads(args.file.read_text(encoding="utf-8"))
        print(json.dumps(promote(args.db,payload),ensure_ascii=False,indent=2))
        return 0
    if args.command=="promote-jsonl":
        ok=failed=0
        with args.file.open("r",encoding="utf-8-sig") as fh:
            for line_no,line in enumerate(fh,start=1):
                if not line.strip():
                    continue
                try:
                    promote(args.db,json.loads(line))
                    ok+=1
                except Exception as exc:
                    failed+=1
                    print(json.dumps({"line":line_no,"error":str(exc)},ensure_ascii=False))
                    if not args.continue_on_error:
                        raise
        print(json.dumps({"promoted":ok,"failed":failed},indent=2))
        return 0
    if args.command=="prospectable":
        init_db(args.db)
        with connect(args.db) as conn:
            rows=[dict(r) for r in conn.execute(
                "SELECT * FROM v_prospectable_contacts ORDER BY contact_id LIMIT ?",
                (args.limit,),
            )]
        print(json.dumps(rows,ensure_ascii=False,indent=2))
        return 0
    if args.command=="suppress":
        sid=add_suppression(
            args.db,args.scope_type,args.scope_value,args.reason,
            permanent=args.permanent,notes=args.notes,
        )
        print(json.dumps({"suppression_id":sid},indent=2))
        return 0
    raise AssertionError(args.command)

if __name__=="__main__":
    raise SystemExit(main())
