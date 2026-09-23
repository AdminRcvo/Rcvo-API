from __future__ import annotations
import hashlib,json,os,socket,uuid
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any

from deliverability import dns_preflight,mailbox_gate
from inbound import scan_mailbox
from mailer import build_message,content_hash,safe_format,transport_for
from tokens import sign
from state import State

@dataclass
class RuntimeConfig:
    campaign:dict[str,Any]
    mailboxes:list[dict[str,Any]]
    identity:dict[str,Any]
    public_base_url:str
    tracking_secret:str
    claim_size:int=100
    lease_seconds:int=300
    enroll_limit:int=5000
    worker_id:str|None=None
    enforce_send_window:bool=True

class ProspectingAgent:
    def __init__(self,reference,state:State,config:RuntimeConfig):
        self.reference=reference
        self.state=state
        self.config=config
        self.worker_id=config.worker_id or f"prospecting-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.mailbox_config={m["mailbox_id"]:m for m in config.mailboxes}
        self.state.register_mailboxes(config.mailboxes)

    @property
    def campaign_id(self):
        return str(self.config.campaign["rcvo_id"])

    def bootstrap(self):
        production=any(m.get("transport","smtp")!="dry_run" for m in self.config.mailboxes)
        if production and not self.config.public_base_url.lower().startswith("https://"):
            raise ValueError("production public_base_url must use https")
        postal=str(self.config.identity.get("postal_address") or "").strip()
        if production and (not postal or "renseigner" in postal.casefold()):
            raise ValueError("complete Rcvo sender identity/postal address before production")
        campaign=dict(self.config.campaign)
        self.reference.upsert_campaign({
            "rcvo_id":campaign["rcvo_id"],
            "name":campaign["name"],
            "status":campaign.get("status","running"),
            "campaign_kind":"email",
            "starts_at":campaign.get("starts_at"),
            "ends_at":campaign.get("ends_at"),
            "eligibility_snapshot":campaign.get("eligibility"),
        })
        for step in campaign.get("sequence",[]):
            self.reference.register_message({
                "message_key":step["message_key"],
                "version":int(step.get("version",1)),
                "subject_template":step["subject"],
                "text_template":step["text"],
                "html_template":step.get("html"),
                "video_url":step.get("video_url"),
            })
        self._dns_preflight()
        return self.reference.enroll(self.campaign_id,self.config.enroll_limit)

    def _dns_preflight(self):
        for mailbox in self.config.mailboxes:
            if mailbox.get("transport","smtp")!="smtp":
                continue
            if not mailbox.get("require_dns_auth",True):
                continue
            domain=mailbox["address"].rsplit("@",1)[1].lower()
            result=dns_preflight(domain,mailbox.get("dkim_selector"))
            if not result["ok"]:
                self.state.pause_mailbox(mailbox["mailbox_id"],json.dumps(result,separators=(",",":")),"dns_invalid")

    def _next_time(self,step:dict[str,Any],last:bool):
        if last:
            days=float(self.config.campaign.get("post_campaign_cooldown_days",180))
            return (datetime.now(timezone.utc)+timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        hours=float(step.get("next_delay_hours",24*7))
        return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec="milliseconds").replace("+00:00","Z")

    def _available_mailbox_count(self):
        count=0
        for row in self.state.mailbox_rows():
            cfg=self.mailbox_config.get(row["mailbox_id"])
            if not cfg: continue
            hourly,daily,_,last=self.state.sent_counts(row["mailbox_id"],None)
            health=self.state.health_counts(row["mailbox_id"])
            gate=mailbox_gate(
                row,cfg,health,hourly,daily,0,last,
                require_window=self.config.enforce_send_window,
            )
            if gate.allowed: count+=1
        return count

    def _select_mailbox(self,recipient_domain:str):
        candidates=[]
        for row in self.state.mailbox_rows():
            cfg=self.mailbox_config.get(row["mailbox_id"])
            if not cfg: continue
            hourly,daily,domain_hourly,last=self.state.sent_counts(row["mailbox_id"],recipient_domain)
            health=self.state.health_counts(row["mailbox_id"])
            gate=mailbox_gate(
                row,cfg,health,hourly,daily,domain_hourly,last,
                require_window=self.config.enforce_send_window,
            )
            if not gate.allowed:
                if gate.reason.startswith(("spam_complaint","hard_bounce_rate","soft_bounce_rate")):
                    self.state.pause_mailbox(row["mailbox_id"],gate.reason)
                continue
            candidates.append((daily,hourly,last or "",row["mailbox_id"],cfg))
        if not candidates: return None
        candidates.sort(key=lambda x:(x[0],x[1],x[2],x[3]))
        return candidates[0][4]

    def _urls(self,contact:dict[str,Any],step:dict[str,Any]):
        base=self.config.public_base_url.rstrip("/")
        unsub=sign({
            "kind":"unsubscribe","contact_rcvo_id":contact["rcvo_id"],
            "campaign_rcvo_id":self.campaign_id
        },self.config.tracking_secret)
        urls={"unsubscribe_url":f"{base}/unsubscribe/{unsub}"}
        if step.get("video_url"):
            token=sign({
                "kind":"video","contact_rcvo_id":contact["rcvo_id"],
                "campaign_rcvo_id":self.campaign_id,
                "target":step["video_url"],
                "message_key":step["message_key"],
            },self.config.tracking_secret)
            urls["video_url"]=f"{base}/video/{token}"
        else:
            urls["video_url"]=""
        return urls

    def _render(self,contact,step,urls):
        data={
            **contact,
            "first_name":contact.get("first_name") or "",
            "last_name":contact.get("last_name") or "",
            "company":contact.get("organization_name") or "",
            **urls,
        }
        subject=safe_format(step["subject"],data)
        text=safe_format(step["text"],data)
        html=safe_format(step["html"],data) if step.get("html") else None
        if urls.get("video_url") and "{video_url}" not in step["text"]:
            text=text.rstrip()+"\n\nVoir la vidéo Rcvo : "+urls["video_url"]
            if html:
                html=html.rstrip()+'<p><a href="'+urls["video_url"]+'">Voir la vidéo Rcvo</a></p>'
        return subject,text,html

    def sync_unsynced(self):
        synced=0
        for row in self.state.unsynced():
            event_type="email_sent" if int(row["step_index"])==0 else "followup_sent"
            result=self.reference.events([{
                "event_type":event_type,
                "event_key":"send:"+row["message_id"],
                "contact_rcvo_id":row["contact_rcvo_id"],
                "campaign_rcvo_id":row["campaign_rcvo_id"],
                "occurred_at":row["sent_at"],
                "sender_mailbox":self.mailbox_config[row["mailbox_id"]]["address"],
                "message_key":f'{row["message_key"]}:v{row["message_version"]}',
                "provider_message_id":row["provider_message_id"] or row["message_id"],
                "result":"sent",
                "next_eligible_at":row["next_eligible_at"],
                "completed":bool(row["completed"]),
                "metadata":{"content_sha256":row["content_sha256"],"step_index":row["step_index"]},
            }])
            if result.get("failed",0)==0:
                self.state.mark_synced(row["outbox_uuid"])
                synced+=1
        return synced

    def poll_inboxes(self):
        processed=0
        for mailbox in self.config.mailboxes:
            if not mailbox.get("imap_enabled",False):
                continue
            try:
                result=scan_mailbox(mailbox,self.handle_feedback,limit=int(mailbox.get("imap_scan_limit",100)))
                processed+=int(result.get("processed",0))
            except Exception as exc:
                if mailbox.get("pause_on_inbound_failure",True):
                    self.state.pause_mailbox(mailbox["mailbox_id"],"inbound_monitor_failure:"+str(exc))
        return processed

    def process_once(self):
        counters={"claimed":0,"sent":0,"synced":0,"skipped":0,"failed":0,"uncertain":0,"simulated":0}
        counters["synced"]+=self.sync_suppressions()
        counters["synced"]+=self.sync_feedback()
        counters["synced"]+=self.sync_unsynced()
        self.poll_inboxes()
        counters["synced"]+=self.sync_suppressions()
        counters["synced"]+=self.sync_feedback()

        control=self.state.control()
        if control["outbound_state"]!="on":
            return counters
        if control["mode"]=="simulation":
            return counters

        capacity=self._available_mailbox_count()
        if capacity<=0:
            return counters
        claim_limit=min(self.config.claim_size,capacity)
        if control["mode"]=="pilot":
            claim_limit=min(claim_limit,5)
        contacts=self.reference.claim(
            self.campaign_id,self.worker_id,claim_limit,
            self.config.lease_seconds
        )
        counters["claimed"]=len(contacts)
        sequence=self.config.campaign.get("sequence",[])
        for contact in contacts:
            ccid=int(contact["campaign_contact_id"])
            step_index=int(contact.get("step_index",0))
            if step_index>=len(sequence):
                self.reference.release(ccid,self.worker_id,error=True)
                counters["failed"]+=1
                continue
            step=sequence[step_index]
            email=str(contact["primary_email"]).lower()
            recipient_domain=email.rsplit("@",1)[1]
            mailbox=self._select_mailbox(recipient_domain)
            if mailbox is None:
                retry=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec="milliseconds").replace("+00:00","Z")
                self.reference.release(ccid,self.worker_id,next_eligible_at=retry)
                counters["skipped"]+=1
                continue

            urls=self._urls(contact,step)
            subject,text,html=self._render(contact,step,urls)
            last=step_index==len(sequence)-1
            next_at=self._next_time(step,last)
            sender_domain=mailbox["address"].rsplit("@",1)[1]
            stable=uuid.uuid5(uuid.NAMESPACE_URL,f"{self.campaign_id}|{contact['rcvo_id']}|{step_index}")
            message_id=f"<{stable.hex}@{sender_domain}>"
            sha=content_hash(subject,text,html)

            row=self.state.prepare(
                campaign_rcvo_id=self.campaign_id,
                campaign_contact_id=ccid,
                contact_rcvo_id=contact["rcvo_id"],
                step_index=step_index,
                to_email=email,
                recipient_domain=recipient_domain,
                mailbox_id=mailbox["mailbox_id"],
                message_key=step["message_key"],
                message_version=int(step.get("version",1)),
                message_id=message_id,
                subject=subject,
                content_sha256=sha,
                next_eligible_at=next_at,
                completed=last,
            )
            if row["state"]=="synced":
                counters["skipped"]+=1
                continue
            if row["state"]=="sent":
                counters["synced"]+=self.sync_unsynced()
                continue
            if row["state"]=="uncertain":
                self.reference.release(ccid,self.worker_id,error=True)
                counters["uncertain"]+=1
                continue

            msg=build_message(
                mailbox=mailbox,to_email=email,subject=subject,text_body=text,html_body=html,
                unsubscribe_url=urls["unsubscribe_url"],message_id=message_id,
                identity_name=self.config.identity["name"],
                identity_address=self.config.identity.get("postal_address",""),
            )
            try:
                result=transport_for(mailbox).send(msg,mailbox)
            except (TimeoutError,ConnectionError,OSError) as exc:
                self.state.mark_failed(row["outbox_uuid"],str(exc),uncertain=True)
                self.reference.release(ccid,self.worker_id,error=True)
                counters["uncertain"]+=1
                continue
            except Exception as exc:
                self.state.mark_failed(row["outbox_uuid"],str(exc),uncertain=False)
                retry=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(timespec="milliseconds").replace("+00:00","Z")
                self.reference.release(ccid,self.worker_id,next_eligible_at=retry)
                counters["failed"]+=1
                continue

            self.state.mark_sent(row["outbox_uuid"],result.get("provider_message_id"))
            counters["sent"]+=1
            try:
                counters["synced"]+=self.sync_unsynced()
            except Exception:
                # The message is already accepted by the transport.
                # Keep it as sent/unsynced and never resend it.
                pass
        return counters

    def sync_suppressions(self):
        synced=0
        for row in self.state.unsynced_suppressions():
            self.reference.suppress({
                "contact_rcvo_id":row["contact_rcvo_id"],
                "reason":row["reason"],
                "source":"prospecting_agent_local_suppression",
                "occurred_at":row["occurred_at"],
            })
            self.state.mark_suppression_synced(row["id"])
            synced+=1
        return synced

    def request_unsubscribe(self,contact_rcvo_id:str,campaign_rcvo_id:str|None):
        self.state.add_local_suppression(contact_rcvo_id,campaign_rcvo_id,"optout")
        try:
            self.sync_suppressions()
        except Exception:
            pass
        return {"accepted":True}

    def sync_feedback(self):
        synced=0
        for event in self.state.unsynced_feedback():
            payload=json.loads(event["payload_json"] or "{}")
            row=self.state.outbox_by_message(event["message_id"]) if event["message_id"] else None
            if not row:
                continue
            ref_event={
                "event_type":event["event_type"],
                "event_key":event["event_key"],
                "contact_rcvo_id":row["contact_rcvo_id"],
                "campaign_rcvo_id":row["campaign_rcvo_id"],
                "occurred_at":payload.get("occurred_at") or event["occurred_at"],
                "sender_mailbox":self.mailbox_config[row["mailbox_id"]]["address"],
                "message_key":row["message_key"],
                "provider_message_id":row["provider_message_id"] or row["message_id"],
                "result":payload.get("result") or event["event_type"],
                "next_eligible_at":payload.get("next_eligible_at"),
                "source":"mail_provider",
                "metadata":payload,
            }
            result=self.reference.events([ref_event])
            if result.get("failed",0)==0:
                self.state.mark_feedback_synced(event["event_key"])
                synced+=1
        return synced

    def handle_feedback(self,event:dict[str,Any]):
        event_type=str(event.get("event_type") or "")
        allowed={"email_delivered","soft_bounce","bounce","spam_complaint","reply_received","delivery_deferred","delivery_rejected"}
        if event_type not in allowed:
            raise ValueError("unsupported feedback event")
        message_id=str(event.get("message_id") or event.get("provider_message_id") or "")
        row=self.state.outbox_by_message(message_id)
        if not row:
            raise ValueError("unknown message")
        event_key=str(event.get("event_key") or f"feedback:{event_type}:{uuid.uuid4()}")
        self.state.feedback(event_key,message_id,row["mailbox_id"],event_type,event)
        if event_type in {"spam_complaint","bounce"}:
            reason="optout" if event_type=="spam_complaint" else "hard_bounce"
            self.state.add_local_suppression(row["contact_rcvo_id"],row["campaign_rcvo_id"],reason)
        if event_type=="spam_complaint":
            self.state.pause_mailbox(row["mailbox_id"],"spam_complaint_detected")
        try:
            self.sync_suppressions()
            return self.sync_feedback()
        except Exception:
            return {"accepted":True,"pending_sync":True}
