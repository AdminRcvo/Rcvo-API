from __future__ import annotations
import json,urllib.request
from typing import Any

class ReferenceHttpGateway:
    def __init__(self,base_url:str,token:str,timeout:float=30):
        self.base=base_url.rstrip("/")
        self.token=token
        self.timeout=timeout

    def _request(self,path:str,payload:dict[str,Any]|None=None,method:str="POST"):
        data=None if payload is None else json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
        req=urllib.request.Request(
            self.base+path,data=data,method=method,
            headers={
                "Authorization":"Bearer "+self.token,
                "Accept":"application/json",
                "Content-Type":"application/json",
                "User-Agent":"Rcvo-Prospecting-Agent/1.0",
            }
        )
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            body=response.read()
            return json.loads(body.decode()) if body else {}

    def health(self):
        req=urllib.request.Request(self.base+"/health",method="GET")
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            return json.loads(response.read().decode())

    def upsert_campaign(self,payload): return self._request("/v1/prospecting/campaigns/upsert",payload)
    def register_message(self,payload): return self._request("/v1/prospecting/messages/register",payload)
    def enroll(self,campaign_rcvo_id,limit):
        return self._request("/v1/prospecting/enroll",{"campaign_rcvo_id":campaign_rcvo_id,"limit":limit})
    def claim(self,campaign_rcvo_id,worker_id,limit,lease_seconds):
        data=self._request("/v1/prospecting/claim",{
            "campaign_rcvo_id":campaign_rcvo_id,"worker_id":worker_id,
            "limit":limit,"lease_seconds":lease_seconds,
        })
        return data.get("contacts",[])
    def release(self,campaign_contact_id,worker_id,*,next_eligible_at=None,error=False):
        self._request("/v1/prospecting/release",{
            "campaign_contact_id":campaign_contact_id,"worker_id":worker_id,
            "next_eligible_at":next_eligible_at,"error":error,
        })
    def events(self,events): return self._request("/v1/prospecting/events/batch",{"events":events})
    def suppress(self,payload): return self._request("/v1/prospecting/suppress",payload)
