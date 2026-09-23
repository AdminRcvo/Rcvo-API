from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts import CandidateLookup, RawItem

class MemoryRawGateway:
    def __init__(self,items:list[RawItem]):
        self.items={x.id:x for x in items}
        self.state={x.id:"pending" for x in items}
        self.errors={}
    def health(self): return {"status":"ok"}
    def claim(self,worker_id,limit,lease_seconds):
        ids=[i for i,s in self.state.items() if s=="pending"][:limit]
        for i in ids: self.state[i]="claimed"
        return [self.items[i] for i in ids]
    def release(self,worker_id,record_id): self.state[record_id]="pending"
    def ack(self,worker_id,record_id): self.state[record_id]="processed"
    def fail(self,worker_id,record_id,error):
        self.state[record_id]="pending"; self.errors[record_id]=error; return "pending"
    def reject(self,worker_id,record_id,reason):
        self.state[record_id]="rejected"; self.errors[record_id]=reason

class MemoryReferenceGateway:
    def __init__(self):
        self.contacts_by_email={}
        self.org_by_domain={}
        self.promotions=[]
        self.counter=0
    def health(self): return {"status":"ok"}
    def lookup(self,normalized):
        return CandidateLookup(
            contact_rcvo_id=self.contacts_by_email.get(normalized.get("email")),
            organization_rcvo_id=self.org_by_domain.get(normalized.get("organization_domain")),
            methods=[
                x for x in (
                    "exact_email" if normalized.get("email") in self.contacts_by_email else None,
                    "exact_domain" if normalized.get("organization_domain") in self.org_by_domain else None,
                ) if x
            ],
        )
    def promote_many(self,promotions):
        results=[]
        for p in promotions:
            self.counter+=1
            cid=p.get("contact_match_rcvo_id") or f"CNT-{self.counter}"
            oid=p.get("organization_match_rcvo_id")
            email=(p.get("email") or {}).get("value")
            domain=(p.get("organization") or {}).get("domain")
            if email: self.contacts_by_email[email]=cid
            if domain:
                oid=oid or f"ENT-{self.counter}"
                self.org_by_domain[domain]=oid
            self.promotions.append(p)
            results.append({
                "ok":True,
                "status":"enriched" if p.get("contact_match_rcvo_id") else "created",
                "contact_rcvo_id":cid,
                "organization_rcvo_id":oid,
            })
        return {"results":results}
