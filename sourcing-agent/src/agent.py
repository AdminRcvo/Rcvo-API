from __future__ import annotations

import copy
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any

from contracts import CandidateLookup, RawGateway, RawItem, ReferenceGateway
from normalization import normalize
from profiles import ProfileRegistry
from state import AgentState

@dataclass
class AgentConfig:
    mode:str="production"
    claim_size:int=500
    lease_seconds:int=300
    max_fail_attempts:int=5
    idle_sleep_seconds:float=5.0
    worker_id:str|None=None

class SourcingAgent:
    def __init__(
        self,
        raw:RawGateway,
        reference:ReferenceGateway,
        state:AgentState,
        config:AgentConfig|None=None,
        profiles:ProfileRegistry|None=None,
    ):
        self.raw=raw
        self.reference=reference
        self.state=state
        self.config=config or AgentConfig()
        self.profiles=profiles or ProfileRegistry(None)
        for source_key,profile,sha in self.profiles.items():
            self.state.register_profile(source_key,sha,profile)
        if self.config.mode not in {"simulation","production"}:
            raise ValueError("mode must be simulation or production")
        self.worker_id=self.config.worker_id or (
            f"sourcing-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )

    def health(self) -> dict[str,Any]:
        return {
            "status":"ok",
            "worker_id":self.worker_id,
            "mode":self.config.mode,
            "raw":self.raw.health(),
            "reference":self.reference.health(),
            "state":self.state.stats(),
        }

    def _source_key(self,item:RawItem) -> str:
        if item.source_key and str(item.source_key).strip():
            return str(item.source_key).strip()
        return "raw"

    def _promotion(
        self,
        item:RawItem,
        normalized,
        lookup:CandidateLookup,
        source_key:str,
    ) -> dict[str,Any]:
        result={
            "raw_ref":{
                "source_key":source_key,
                "raw_batch_uuid":item.raw_batch_uuid or str(item.batch_id if item.batch_id is not None else "unknown"),
                "raw_record_id":item.id,
                "source_record_id":item.source_record_id,
            },
            "contact":copy.deepcopy(normalized.contact),
        }
        if normalized.email:
            result["email"]=copy.deepcopy(normalized.email)
        if normalized.phone:
            result["phone"]=copy.deepcopy(normalized.phone)
        if normalized.organization:
            result["organization"]=copy.deepcopy(normalized.organization)
        if normalized.employment:
            result["employment"]=copy.deepcopy(normalized.employment)
        if lookup.contact_rcvo_id:
            result["contact_match_rcvo_id"]=lookup.contact_rcvo_id
        if lookup.organization_rcvo_id:
            result["organization_match_rcvo_id"]=lookup.organization_rcvo_id
        if lookup.site_rcvo_id:
            result["site_match_rcvo_id"]=lookup.site_rcvo_id
        return result

    def process_batch(self) -> dict[str,Any]:
        run=self.state.start_run(self.worker_id,self.config.mode)
        counters={"claimed":0,"promoted":0,"enriched":0,"rejected":0,"failed":0}
        try:
            items=self.raw.claim(
                self.worker_id,self.config.claim_size,self.config.lease_seconds
            )
            counters["claimed"]=len(items)
            if not items:
                self.state.finish_run(run,"complete",counters,{"idle":True})
                return counters

            prepared:list[tuple[RawItem,dict[str,Any],Any,CandidateLookup,str]]=[]
            for item in items:
                try:
                    source_key=self._source_key(item)
                    normalized=normalize(item.payload,source_key,self.profiles.get(source_key))
                    lookup=self.reference.lookup(normalized.as_lookup())
                    if lookup.conflicts:
                        self.raw.reject(
                            self.worker_id,item.id,
                            "reference_conflict: "+"; ".join(lookup.conflicts)
                        )
                        counters["rejected"]+=1
                        self.state.decision(
                            run,item.id,"needs_review",source_key=source_key,
                            contact_rcvo_id=lookup.contact_rcvo_id,
                            organization_rcvo_id=lookup.organization_rcvo_id,
                            qualification_status=normalized.contact["qualification_status"],
                            vo_relevance=normalized.contact["vo_relevance"],
                            match_methods=lookup.methods,
                            reasons=normalized.reasons+lookup.conflicts,
                        )
                        continue

                    promotion=self._promotion(item,normalized,lookup,source_key)
                    prepared.append((item,promotion,normalized,lookup,source_key))
                except Exception as exc:
                    state=self.raw.fail(self.worker_id,item.id,str(exc))
                    counters["failed"]+=1
                    self.state.error(run,exc,item.id)
                    self.state.decision(run,item.id,state,reasons=[str(exc)])

            if self.config.mode=="simulation":
                for item,promotion,normalized,lookup,source_key in prepared:
                    self.raw.release(self.worker_id,item.id)
                    counters["promoted"]+=1
                    self.state.decision(
                        run,item.id,"simulated",source_key=source_key,
                        contact_rcvo_id=lookup.contact_rcvo_id,
                        organization_rcvo_id=lookup.organization_rcvo_id,
                        qualification_status=normalized.contact["qualification_status"],
                        vo_relevance=normalized.contact["vo_relevance"],
                        match_methods=lookup.methods,
                        reasons=normalized.reasons,
                        details={"promotion":promotion},
                    )
            elif prepared:
                promotions=[x[1] for x in prepared]
                result=self.reference.promote_many(promotions)
                item_results=result.get("results")
                if item_results is None:
                    failed_indexes={
                        int(e.get("index")) for e in result.get("errors",[])
                        if str(e.get("index","")).isdigit()
                    }
                    item_results=[
                        {"ok":i not in failed_indexes}
                        for i in range(1,len(prepared)+1)
                    ]
                if len(item_results)!=len(prepared):
                    raise RuntimeError("reference result count does not match submitted promotions")

                for idx,(item,promotion,normalized,lookup,source_key) in enumerate(prepared):
                    out=item_results[idx]
                    if out.get("ok",True) and not out.get("error"):
                        self.raw.ack(self.worker_id,item.id)
                        counters["promoted"]+=1
                        if out.get("status")=="enriched":
                            counters["enriched"]+=1
                        self.state.decision(
                            run,item.id,out.get("status","promoted"),
                            source_key=source_key,
                            contact_rcvo_id=out.get("contact_rcvo_id") or lookup.contact_rcvo_id,
                            organization_rcvo_id=out.get("organization_rcvo_id") or lookup.organization_rcvo_id,
                            qualification_status=normalized.contact["qualification_status"],
                            vo_relevance=normalized.contact["vo_relevance"],
                            match_methods=lookup.methods,
                            reasons=normalized.reasons,
                        )
                    else:
                        error=str(out.get("error","reference promotion failed"))
                        self.raw.fail(self.worker_id,item.id,error)
                        counters["failed"]+=1
                        self.state.error(run,error,item.id,details=out)

            status="complete" if counters["failed"]==0 else "partial"
            self.state.finish_run(run,status,counters)
            return counters
        except Exception as exc:
            self.state.error(run,exc)
            self.state.finish_run(run,"failed",counters,{"error":str(exc)})
            raise

    def run_forever(self) -> None:
        while True:
            counters=self.process_batch()
            if counters.get("claimed",0)==0:
                time.sleep(self.config.idle_sleep_seconds)
