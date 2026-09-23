#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from reference_engine import connect, init_db, norm_domain, norm_email, promote_many
from prospecting_engine import (
    claim_due, eligible_contacts, enroll_contacts, enroll_eligible,
    record_events, register_message_version, release_claim, suppress, upsert_campaign
)

class Server(ThreadingHTTPServer):
    db:Path
    token_env:str
    prospecting_token_env:str

def exact_lookup(db:Path,payload:dict) -> dict:
    source_key=str(payload.get("source_key") or "").strip()
    email=norm_email(payload.get("email"))
    contact_ext=str(payload.get("contact_external_id") or "").strip() or None
    org_ext=str(payload.get("organization_external_id") or "").strip() or None
    domain=norm_domain(payload.get("organization_domain"))

    contact_candidates={}
    organization_candidates={}
    methods=[]

    with connect(db) as conn:
        if contact_ext and source_key:
            row=conn.execute(
                """
                SELECT c.rcvo_id FROM external_identities x
                JOIN contacts c ON c.id=x.contact_id
                WHERE x.source_key=? AND x.entity_type='contact' AND x.external_id=?
                """,
                (source_key,contact_ext),
            ).fetchone()
            if row:
                contact_candidates["external_id"]=row["rcvo_id"]
                methods.append("contact_external_id")

        if email:
            row=conn.execute(
                """
                SELECT c.rcvo_id FROM contact_emails e
                JOIN contacts c ON c.id=e.contact_id
                WHERE e.email_norm=?
                """,
                (email,),
            ).fetchone()
            if row:
                contact_candidates["exact_email"]=row["rcvo_id"]
                methods.append("exact_email")

        if org_ext and source_key:
            row=conn.execute(
                """
                SELECT o.rcvo_id FROM external_identities x
                JOIN organizations o ON o.id=x.organization_id
                WHERE x.source_key=? AND x.entity_type='organization' AND x.external_id=?
                """,
                (source_key,org_ext),
            ).fetchone()
            if row:
                organization_candidates["external_id"]=row["rcvo_id"]
                methods.append("organization_external_id")

        if domain:
            row=conn.execute(
                """
                SELECT o.rcvo_id FROM organization_domains d
                JOIN organizations o ON o.id=d.organization_id
                WHERE d.domain_norm=?
                """,
                (domain,),
            ).fetchone()
            if row:
                organization_candidates["exact_domain"]=row["rcvo_id"]
                methods.append("exact_domain")

    conflicts=[]
    cset=set(contact_candidates.values())
    oset=set(organization_candidates.values())
    if len(cset)>1:
        conflicts.append("strong contact keys resolve to different contacts")
    if len(oset)>1:
        conflicts.append("strong organization keys resolve to different organizations")
    return {
        "contact_rcvo_id":next(iter(cset)) if len(cset)==1 else None,
        "organization_rcvo_id":next(iter(oset)) if len(oset)==1 else None,
        "site_rcvo_id":None,
        "methods":methods,
        "conflicts":conflicts,
    }

class Handler(BaseHTTPRequestHandler):
    server_version="RcvoReferenceSourcingAPI/1.0"

    def log_message(self,fmt,*args):
        return

    def _write(self,status:int,payload:dict):
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self,scope="sourcing"):
        env_name=self.server.token_env if scope=="sourcing" else self.server.prospecting_token_env
        expected=os.getenv(env_name)
        return bool(expected and self.headers.get("Authorization")=="Bearer "+expected)

    def _body(self,max_bytes:int=20*1024*1024):
        length=int(self.headers.get("Content-Length","0"))
        if length<=0:
            return {}
        if length>max_bytes:
            raise ValueError("request body too large")
        value=json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value,dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self):
        if self.path=="/health":
            try:
                with connect(self.server.db) as conn:
                    check=conn.execute("PRAGMA quick_check").fetchone()[0]
                self._write(200,{"status":"ok" if check=="ok" else "degraded","quick_check":check})
            except Exception as exc:
                self._write(503,{"status":"error","error":str(exc)})
            return
        self._write(404,{"error":"not_found"})

    def do_POST(self):
        prospecting=self.path.startswith("/v1/prospecting/")
        if not self._auth("prospecting" if prospecting else "sourcing"):
            self._write(401,{"error":"unauthorized"})
            return
        try:
            body=self._body()
            if self.path=="/v1/reference/lookup":
                self._write(200,exact_lookup(self.server.db,body))
                return
            if self.path=="/v1/reference/promotions/batch":
                promotions=body.get("promotions")
                if not isinstance(promotions,list):
                    raise ValueError("promotions array required")
                if len(promotions)>5000:
                    raise ValueError("too many promotions in one request")
                result=promote_many(
                    self.server.db,promotions,batch_size=500,continue_on_error=True
                )
                self._write(200,result)
                return

            if self.path=="/v1/prospecting/campaigns/upsert":
                self._write(200,upsert_campaign(self.server.db,body))
                return
            if self.path=="/v1/prospecting/messages/register":
                self._write(200,register_message_version(self.server.db,body))
                return
            if self.path=="/v1/prospecting/eligible":
                self._write(200,{"contacts":eligible_contacts(self.server.db,body.get("limit",1000))})
                return
            if self.path=="/v1/prospecting/enroll":
                campaign=str(body.get("campaign_rcvo_id") or "")
                ids=body.get("contact_rcvo_ids")
                if ids is None:
                    self._write(200,enroll_eligible(self.server.db,campaign,body.get("limit",5000)))
                else:
                    if not isinstance(ids,list):
                        raise ValueError("contact_rcvo_ids must be an array")
                    self._write(200,enroll_contacts(self.server.db,campaign,[str(x) for x in ids]))
                return
            if self.path=="/v1/prospecting/claim":
                self._write(200,{"contacts":claim_due(
                    self.server.db,
                    str(body.get("campaign_rcvo_id") or ""),
                    str(body.get("worker_id") or ""),
                    body.get("limit",200),
                    body.get("lease_seconds",300),
                )})
                return
            if self.path=="/v1/prospecting/release":
                release_claim(
                    self.server.db,
                    int(body["campaign_contact_id"]),
                    str(body.get("worker_id") or ""),
                    next_eligible_at=body.get("next_eligible_at"),
                    error=bool(body.get("error",False)),
                )
                self._write(200,{"released":True})
                return
            if self.path=="/v1/prospecting/events/batch":
                events=body.get("events")
                if not isinstance(events,list):
                    raise ValueError("events array required")
                self._write(200,record_events(self.server.db,events))
                return
            if self.path=="/v1/prospecting/suppress":
                self._write(200,suppress(self.server.db,body))
                return

            self._write(404,{"error":"not_found"})
        except Exception as exc:
            self._write(400,{"error":"bad_request","message":str(exc)})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=ROOT/"data"/"rcvo-reference.sqlite")
    p.add_argument("--bind",default="127.0.0.1")
    p.add_argument("--port",type=int,default=8092)
    p.add_argument("--token-env",default="RCVO_REFERENCE_SOURCING_TOKEN")
    p.add_argument("--prospecting-token-env",default="RCVO_REFERENCE_PROSPECTION_TOKEN")
    args=p.parse_args()

    init_db(args.db)
    server=Server((args.bind,args.port),Handler)
    server.db=args.db
    server.token_env=args.token_env
    server.prospecting_token_env=args.prospecting_token_env
    server.serve_forever()

if __name__=="__main__":
    main()
