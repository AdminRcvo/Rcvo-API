from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from contracts import CandidateLookup, RawItem

class JsonHttpClient:
    def __init__(self,base_url:str,token:str,timeout:float=30.0):
        self.base=base_url.rstrip("/")
        self.token=token
        self.timeout=timeout

    def request(self,method:str,path:str,payload:Any=None) -> Any:
        data=None if payload is None else json.dumps(
            payload,ensure_ascii=False,separators=(",",":")
        ).encode()
        req=urllib.request.Request(
            self.base+path,
            data=data,
            method=method,
            headers={
                "Authorization":"Bearer "+self.token,
                "Accept":"application/json",
                "Content-Type":"application/json",
                "User-Agent":"Rcvo-Sourcing-Agent/1.0",
            },
        )
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            body=response.read()
            return json.loads(body.decode("utf-8")) if body else {}

class HttpRawGateway:
    def __init__(self,base_url:str,token:str,timeout:float=30.0):
        self.client=JsonHttpClient(base_url,token,timeout)
    def health(self): return self.client.request("GET","/health")
    def claim(self,worker_id,limit,lease_seconds):
        data=self.client.request("POST","/v1/raw/claim",{
            "worker_id":worker_id,"limit":limit,"lease_seconds":lease_seconds
        })
        return [RawItem(**x) for x in data.get("items",[])]
    def release(self,worker_id,record_id):
        self.client.request("POST",f"/v1/raw/{record_id}/release",{"worker_id":worker_id})
    def ack(self,worker_id,record_id):
        self.client.request("POST",f"/v1/raw/{record_id}/ack",{"worker_id":worker_id})
    def fail(self,worker_id,record_id,error):
        data=self.client.request("POST",f"/v1/raw/{record_id}/fail",{
            "worker_id":worker_id,"error":error
        })
        return data.get("state","pending")
    def reject(self,worker_id,record_id,reason):
        self.client.request("POST",f"/v1/raw/{record_id}/reject",{
            "worker_id":worker_id,"reason":reason
        })

class HttpReferenceGateway:
    def __init__(self,base_url:str,token:str,timeout:float=30.0):
        self.client=JsonHttpClient(base_url,token,timeout)
    def health(self): return self.client.request("GET","/health")
    def lookup(self,normalized):
        data=self.client.request("POST","/v1/reference/lookup",normalized)
        return CandidateLookup(
            contact_rcvo_id=data.get("contact_rcvo_id"),
            organization_rcvo_id=data.get("organization_rcvo_id"),
            site_rcvo_id=data.get("site_rcvo_id"),
            methods=list(data.get("methods",[])),
            conflicts=list(data.get("conflicts",[])),
        )
    def promote_many(self,promotions):
        return self.client.request(
            "POST","/v1/reference/promotions/batch",
            {"promotions":promotions}
        )
