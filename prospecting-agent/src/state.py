from __future__ import annotations
import json,sqlite3,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Any

SCHEMA="""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS agent_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
INSERT INTO agent_meta(key,value) VALUES ('schema_version','1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS mailboxes(
    mailbox_id TEXT PRIMARY KEY,
    address TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
      CHECK(status IN ('active','paused','disabled','dns_invalid','health_paused')),
    pause_reason TEXT,
    activated_at TEXT NOT NULL,
    last_sent_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox(
    id INTEGER PRIMARY KEY,
    outbox_uuid TEXT NOT NULL UNIQUE,
    campaign_rcvo_id TEXT NOT NULL,
    campaign_contact_id INTEGER NOT NULL,
    contact_rcvo_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    to_email TEXT NOT NULL,
    recipient_domain TEXT NOT NULL,
    mailbox_id TEXT NOT NULL REFERENCES mailboxes(mailbox_id),
    message_key TEXT NOT NULL,
    message_version INTEGER NOT NULL,
    message_id TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('prepared','sent','synced','failed','uncertain')),
    prepared_at TEXT NOT NULL,
    sent_at TEXT,
    synced_at TEXT,
    provider_message_id TEXT,
    error TEXT,
    next_eligible_at TEXT,
    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
    UNIQUE(campaign_rcvo_id,contact_rcvo_id,step_index)
);
CREATE INDEX IF NOT EXISTS idx_outbox_sync ON outbox(state,sent_at);
CREATE INDEX IF NOT EXISTS idx_outbox_mailbox_sent ON outbox(mailbox_id,sent_at);
CREATE INDEX IF NOT EXISTS idx_outbox_domain_sent ON outbox(recipient_domain,sent_at);

CREATE TABLE IF NOT EXISTS feedback_events(
    id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    message_id TEXT,
    mailbox_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT,
    synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_mailbox ON feedback_events(mailbox_id,occurred_at,event_type);

CREATE TABLE IF NOT EXISTS local_suppressions(
    id INTEGER PRIMARY KEY,
    contact_rcvo_id TEXT NOT NULL,
    campaign_rcvo_id TEXT,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    synced_at TEXT,
    UNIQUE(contact_rcvo_id,reason)
);

CREATE TABLE IF NOT EXISTS control_state(
    id INTEGER PRIMARY KEY CHECK(id=1),
    outbound_state TEXT NOT NULL DEFAULT 'off'
      CHECK(outbound_state IN ('off','paused','on')),
    mode TEXT NOT NULL DEFAULT 'production'
      CHECK(mode IN ('simulation','pilot','production')),
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO control_state(id,outbound_state,mode,updated_at)
VALUES (1,'off','production',strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE IF NOT EXISTS agent_runs(
    id INTEGER PRIMARY KEY,
    run_uuid TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    claimed INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    synced INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    details_json TEXT
);
"""

def now()->str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

class State:
    def __init__(self,path:Path):
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c: c.executescript(SCHEMA)
    def connect(self):
        c=sqlite3.connect(self.path,timeout=20)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def register_mailboxes(self,mailboxes:list[dict[str,Any]]):
        t=now()
        with self.connect() as c:
            for m in mailboxes:
                c.execute(
                    """
                    INSERT INTO mailboxes(mailbox_id,address,config_json,status,activated_at,updated_at)
                    VALUES (?,?,?,'active',?,?)
                    ON CONFLICT(mailbox_id) DO UPDATE SET
                      address=excluded.address,config_json=excluded.config_json,updated_at=excluded.updated_at
                    """,
                    (m["mailbox_id"],m["address"].lower(),json.dumps(m,ensure_ascii=False,separators=(",",":")),m.get("activated_at") or t,t)
                )

    def mailbox_rows(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM mailboxes ORDER BY mailbox_id")]

    def pause_mailbox(self,mailbox_id:str,reason:str,status:str="health_paused"):
        with self.connect() as c:
            c.execute("UPDATE mailboxes SET status=?,pause_reason=?,updated_at=? WHERE mailbox_id=?",(status,reason,now(),mailbox_id))

    def sent_counts(self,mailbox_id:str,recipient_domain:str|None=None):
        current=datetime.now(timezone.utc)
        hour=(current-timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        day=(current-timedelta(hours=24)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        with self.connect() as c:
            hourly=c.execute("SELECT count(*) FROM outbox WHERE mailbox_id=? AND state IN ('sent','synced') AND sent_at>=?",(mailbox_id,hour)).fetchone()[0]
            daily=c.execute("SELECT count(*) FROM outbox WHERE mailbox_id=? AND state IN ('sent','synced') AND sent_at>=?",(mailbox_id,day)).fetchone()[0]
            domain=0
            if recipient_domain:
                domain=c.execute("SELECT count(*) FROM outbox WHERE mailbox_id=? AND recipient_domain=? AND state IN ('sent','synced') AND sent_at>=?",(mailbox_id,recipient_domain,hour)).fetchone()[0]
            last=c.execute("SELECT last_sent_at FROM mailboxes WHERE mailbox_id=?",(mailbox_id,)).fetchone()
        return int(hourly),int(daily),int(domain),(last["last_sent_at"] if last else None)

    def health_counts(self,mailbox_id:str,days:int=7):
        since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        with self.connect() as c:
            sent=c.execute("SELECT count(*) FROM outbox WHERE mailbox_id=? AND state IN ('sent','synced') AND sent_at>=?",(mailbox_id,since)).fetchone()[0]
            feedback=dict(c.execute("SELECT event_type,count(*) FROM feedback_events WHERE mailbox_id=? AND occurred_at>=? GROUP BY event_type",(mailbox_id,since)).fetchall())
        return {"sent":int(sent),**{k:int(v) for k,v in feedback.items()}}

    def prepare(self,**kw):
        with self.connect() as c:
            row=c.execute("SELECT * FROM outbox WHERE campaign_rcvo_id=? AND contact_rcvo_id=? AND step_index=?",(kw["campaign_rcvo_id"],kw["contact_rcvo_id"],kw["step_index"])).fetchone()
            if row: return dict(row)
            outbox_uuid=str(uuid.uuid4())
            c.execute(
                """
                INSERT INTO outbox(
                  outbox_uuid,campaign_rcvo_id,campaign_contact_id,contact_rcvo_id,step_index,
                  to_email,recipient_domain,mailbox_id,message_key,message_version,message_id,
                  subject,content_sha256,state,prepared_at,next_eligible_at,completed
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,?)
                """,
                (outbox_uuid,kw["campaign_rcvo_id"],kw["campaign_contact_id"],kw["contact_rcvo_id"],kw["step_index"],kw["to_email"],kw["recipient_domain"],kw["mailbox_id"],kw["message_key"],kw["message_version"],kw["message_id"],kw["subject"],kw["content_sha256"],now(),kw.get("next_eligible_at"),1 if kw.get("completed") else 0)
            )
            return dict(c.execute("SELECT * FROM outbox WHERE outbox_uuid=?",(outbox_uuid,)).fetchone())

    def mark_sent(self,outbox_uuid:str,provider_message_id:str|None=None):
        t=now()
        with self.connect() as c:
            row=c.execute("SELECT mailbox_id FROM outbox WHERE outbox_uuid=?",(outbox_uuid,)).fetchone()
            c.execute("UPDATE outbox SET state='sent',sent_at=?,provider_message_id=?,error=NULL WHERE outbox_uuid=?",(t,provider_message_id,outbox_uuid))
            if row: c.execute("UPDATE mailboxes SET last_sent_at=?,updated_at=? WHERE mailbox_id=?",(t,t,row["mailbox_id"]))

    def mark_failed(self,outbox_uuid:str,error:str,uncertain:bool=False):
        with self.connect() as c:
            c.execute("UPDATE outbox SET state=?,error=? WHERE outbox_uuid=?",("uncertain" if uncertain else "failed",error[:4000],outbox_uuid))

    def unsynced(self,limit:int=1000):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbox WHERE state='sent' ORDER BY id LIMIT ?",(limit,))]

    def mark_synced(self,outbox_uuid:str):
        with self.connect() as c:
            c.execute("UPDATE outbox SET state='synced',synced_at=? WHERE outbox_uuid=?",(now(),outbox_uuid))

    def feedback(self,event_key:str,message_id:str|None,mailbox_id:str|None,event_type:str,payload:Any):
        with self.connect() as c:
            c.execute(
                """
                INSERT OR IGNORE INTO feedback_events(event_key,message_id,mailbox_id,event_type,occurred_at,payload_json)
                VALUES (?,?,?,?,?,?)
                """,
                (event_key,message_id,mailbox_id,event_type,now(),json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str))
            )

    def unsynced_feedback(self,limit:int=1000):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM feedback_events WHERE synced_at IS NULL ORDER BY id LIMIT ?",
                (limit,)
            )]

    def mark_feedback_synced(self,event_key:str):
        with self.connect() as c:
            c.execute("UPDATE feedback_events SET synced_at=? WHERE event_key=?",(now(),event_key))

    def add_local_suppression(self,contact_rcvo_id:str,campaign_rcvo_id:str|None,reason:str):
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO local_suppressions(contact_rcvo_id,campaign_rcvo_id,reason,occurred_at)
                VALUES (?,?,?,?)
                ON CONFLICT(contact_rcvo_id,reason) DO UPDATE SET
                    campaign_rcvo_id=excluded.campaign_rcvo_id,
                    occurred_at=excluded.occurred_at,
                    synced_at=NULL
                """,
                (contact_rcvo_id,campaign_rcvo_id,reason,now())
            )

    def unsynced_suppressions(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM local_suppressions WHERE synced_at IS NULL ORDER BY id"
            )]

    def mark_suppression_synced(self,row_id:int):
        with self.connect() as c:
            c.execute("UPDATE local_suppressions SET synced_at=? WHERE id=?",(now(),row_id))

    def outbox_by_message(self,message_id:str):
        with self.connect() as c:
            row=c.execute("SELECT * FROM outbox WHERE message_id=? OR provider_message_id=?",(message_id,message_id)).fetchone()
            return dict(row) if row else None


    def control(self):
        with self.connect() as c:
            row=c.execute("SELECT outbound_state,mode,updated_at FROM control_state WHERE id=1").fetchone()
            return dict(row)

    def set_control(self,outbound_state:str|None=None,mode:str|None=None):
        if outbound_state is not None and outbound_state not in ("off","paused","on"):
            raise ValueError("invalid outbound_state")
        if mode is not None and mode not in ("simulation","pilot","production"):
            raise ValueError("invalid mode")
        current=self.control()
        new_state=outbound_state or current["outbound_state"]
        new_mode=mode or current["mode"]
        with self.connect() as c:
            c.execute(
                "UPDATE control_state SET outbound_state=?,mode=?,updated_at=? WHERE id=1",
                (new_state,new_mode,now())
            )
        return self.control()

    def admin_snapshot(self):
        control=self.control()
        rows=[]
        for row in self.mailbox_rows():
            hourly,daily,_,last=self.sent_counts(row["mailbox_id"],None)
            health=self.health_counts(row["mailbox_id"])
            cfg=json.loads(row["config_json"])
            rows.append({
                "mailbox_id":row["mailbox_id"],
                "address":row["address"],
                "status":row["status"],
                "pause_reason":row["pause_reason"],
                "last_sent_at":last,
                "sent_last_hour":hourly,
                "sent_last_24h":daily,
                "health_7d":health,
                "daily_cap":int(cfg.get("daily_cap",0)),
                "hourly_cap":int(cfg.get("hourly_cap",0)),
            })
        return {
            "control":control,
            "local":self.stats(),
            "mailboxes":rows,
        }

    def stats(self):
        with self.connect() as c:
            return {
              "mailboxes":c.execute("SELECT count(*) FROM mailboxes").fetchone()[0],
              "outbox_prepared":c.execute("SELECT count(*) FROM outbox WHERE state='prepared'").fetchone()[0],
              "outbox_sent_unsynced":c.execute("SELECT count(*) FROM outbox WHERE state='sent'").fetchone()[0],
              "outbox_synced":c.execute("SELECT count(*) FROM outbox WHERE state='synced'").fetchone()[0],
              "outbox_uncertain":c.execute("SELECT count(*) FROM outbox WHERE state='uncertain'").fetchone()[0],
              "feedback_unsynced":c.execute("SELECT count(*) FROM feedback_events WHERE synced_at IS NULL").fetchone()[0],
              "suppressions_unsynced":c.execute("SELECT count(*) FROM local_suppressions WHERE synced_at IS NULL").fetchone()[0],
            }
