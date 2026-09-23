from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

class ProfileRegistry:
    def __init__(self,directory:Path|None=None):
        self.directory=directory
        self._profiles:dict[str,dict[str,Any]]={}
        self._hashes:dict[str,str]={}
        if directory and directory.exists():
            for path in sorted(directory.glob("*.json")):
                profile=json.loads(path.read_text(encoding="utf-8"))
                source=str(profile.get("source_key") or "").strip()
                if not source or source=="provider-key-here":
                    continue
                key=source.casefold()
                raw=json.dumps(profile,sort_keys=True,ensure_ascii=False,separators=(",",":"))
                self._profiles[key]=profile
                self._hashes[key]=hashlib.sha256(raw.encode()).hexdigest()

    def get(self,source_key:str|None) -> dict[str,Any]|None:
        return self._profiles.get((source_key or "").casefold())

    def items(self):
        for key,profile in self._profiles.items():
            yield key,profile,self._hashes[key]

def path_value(payload:Any,path:str) -> Any:
    current=payload
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current,dict):
            current=current.get(part)
        elif isinstance(current,list) and part.isdigit():
            idx=int(part)
            current=current[idx] if 0<=idx<len(current) else None
        else:
            return None
    return current

def profile_value(payload:Any,profile:dict[str,Any]|None,target:str) -> Any:
    if not profile:
        return None
    paths=(profile.get("fields") or {}).get(target) or []
    if isinstance(paths,str):
        paths=[paths]
    for path in paths:
        value=path_value(payload,str(path))
        if value not in (None,"",[],{}):
            return value
    return None
