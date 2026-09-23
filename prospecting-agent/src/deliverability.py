from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from typing import Any

try:
    import dns.resolver
except Exception:
    dns=None

@dataclass
class Gate:
    allowed:bool
    reason:str="ok"

def _txt(name:str,resolver=None)->list[str]:
    r=resolver or (dns.resolver.Resolver() if dns else None)
    if r is None:
        raise RuntimeError("dnspython is required for DNS preflight")
    values=[]
    for ans in r.resolve(name,"TXT"):
        if hasattr(ans,"strings"):
            values.append(b"".join(ans.strings).decode(errors="replace"))
        else:
            values.append(str(ans).strip('"'))
    return values

def dns_preflight(domain:str,selector:str|None,resolver=None)->dict[str,Any]:
    result={"domain":domain,"spf":False,"dkim":False,"dmarc":False,"errors":[]}
    try:
        result["spf"]=any(v.lower().startswith("v=spf1") for v in _txt(domain,resolver))
    except Exception as exc:
        result["errors"].append("spf:"+str(exc))
    if selector:
        try:
            result["dkim"]=any("v=dkim1" in v.lower() or "p=" in v.lower() for v in _txt(f"{selector}._domainkey.{domain}",resolver))
        except Exception as exc:
            result["errors"].append("dkim:"+str(exc))
    try:
        result["dmarc"]=any(v.lower().startswith("v=dmarc1") for v in _txt(f"_dmarc.{domain}",resolver))
    except Exception as exc:
        result["errors"].append("dmarc:"+str(exc))
    result["ok"]=bool(result["spf"] and result["dkim"] and result["dmarc"])
    return result

def _parse_hhmm(value:str)->tuple[int,int]:
    h,m=value.split(":",1)
    return int(h),int(m)

def in_send_window(config:dict[str,Any],now_utc:datetime|None=None)->bool:
    tz=ZoneInfo(str(config.get("timezone") or "Europe/Paris"))
    now=(now_utc or datetime.now(timezone.utc)).astimezone(tz)
    weekdays=config.get("weekdays",[0,1,2,3,4])
    if now.weekday() not in weekdays:
        return False
    start_h,start_m=_parse_hhmm(str(config.get("window_start") or "08:30"))
    end_h,end_m=_parse_hhmm(str(config.get("window_end") or "18:00"))
    current=now.hour*60+now.minute
    return start_h*60+start_m <= current < end_h*60+end_m

def health_gate(config:dict[str,Any],counts:dict[str,int])->Gate:
    sent=int(counts.get("sent",0))
    complaints=int(counts.get("spam_complaint",0))
    hard_bounces=int(counts.get("bounce",0))
    soft_bounces=int(counts.get("soft_bounce",0))

    if complaints>0 and bool(config.get("pause_on_any_complaint",True)):
        return Gate(False,"spam_complaint_detected")

    min_sample=int(config.get("health_min_sample",50))
    if sent>=min_sample:
        hard_rate=hard_bounces/max(sent,1)
        soft_rate=soft_bounces/max(sent,1)
        if hard_rate>=float(config.get("hard_bounce_pause_rate",0.02)):
            return Gate(False,f"hard_bounce_rate={hard_rate:.4f}")
        if soft_rate>=float(config.get("soft_bounce_pause_rate",0.08)):
            return Gate(False,f"soft_bounce_rate={soft_rate:.4f}")
    return Gate(True)

def effective_daily_cap(config:dict[str,Any],activated_at:str|None)->int:
    base=int(config.get("daily_cap",200))
    ramp=config.get("warmup_daily_caps") or []
    if not ramp or not activated_at:
        return base
    activated=datetime.fromisoformat(activated_at.replace("Z","+00:00"))
    days=max(0,(datetime.now(timezone.utc)-activated).days)
    return min(base,int(ramp[min(days,len(ramp)-1)]))

def capacity_gate(config:dict[str,Any],hourly:int,daily:int,domain_hourly:int,last_sent_at:str|None,activated_at:str|None=None)->Gate:
    if hourly>=int(config.get("hourly_cap",30)):
        return Gate(False,"hourly_cap")
    if daily>=effective_daily_cap(config,activated_at):
        return Gate(False,"daily_cap")
    if domain_hourly>=int(config.get("recipient_domain_hourly_cap",8)):
        return Gate(False,"recipient_domain_hourly_cap")
    minimum=float(config.get("min_interval_seconds",90))
    if last_sent_at:
        last=datetime.fromisoformat(last_sent_at.replace("Z","+00:00"))
        elapsed=(datetime.now(timezone.utc)-last).total_seconds()
        if elapsed<minimum:
            return Gate(False,"min_interval")
    return Gate(True)

def mailbox_gate(state_row:dict[str,Any],config:dict[str,Any],counts:dict[str,int],hourly:int,daily:int,domain_hourly:int,last_sent_at:str|None,*,require_window:bool=True)->Gate:
    if state_row.get("status")!="active":
        return Gate(False,"mailbox_"+str(state_row.get("status")))
    if require_window and not in_send_window(config):
        return Gate(False,"outside_send_window")
    health=health_gate(config,counts)
    if not health.allowed:
        return health
    return capacity_gate(config,hourly,daily,domain_hourly,last_sent_at,state_row.get("activated_at"))
