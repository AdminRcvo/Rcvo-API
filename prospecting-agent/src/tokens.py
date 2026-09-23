from __future__ import annotations
import base64,hashlib,hmac,json,time
from typing import Any

def _b64(data:bytes)->str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _unb64(value:str)->bytes:
    return base64.urlsafe_b64decode(value+"="*((4-len(value)%4)%4))

def sign(payload:dict[str,Any],secret:str,ttl_seconds:int=31536000)->str:
    body=dict(payload)
    body["exp"]=int(time.time())+ttl_seconds
    raw=json.dumps(body,ensure_ascii=False,separators=(",",":"),sort_keys=True).encode()
    sig=hmac.new(secret.encode(),raw,hashlib.sha256).digest()
    return _b64(raw)+"."+_b64(sig)

def verify(token:str,secret:str)->dict[str,Any]:
    raw_s,sig_s=token.split(".",1)
    raw=_unb64(raw_s)
    expected=hmac.new(secret.encode(),raw,hashlib.sha256).digest()
    if not hmac.compare_digest(expected,_unb64(sig_s)):
        raise ValueError("invalid token signature")
    payload=json.loads(raw)
    if int(payload.get("exp",0))<int(time.time()):
        raise ValueError("expired token")
    return payload
