from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reference_engine import connect, init_db, norm_email, utc_now

def _parse(ts:str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z","+00:00"))

def _iso(dt:datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def upsert_campaign(db:Path,payload:dict[str,Any]) -> dict[str,Any]:
    init_db(db)
    now=utc_now()
    rcvo_id=str(payload.get("rcvo_id") or f"CMP-{uuid.uuid4().hex[:20].upper()}")
    name=str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("campaign name required")
    status=str(payload.get("status") or "draft")
    steps=payload.get("steps") or []
    if not isinstance(steps,list) or not steps:
        raise ValueError("campaign steps required")

    with connect(db) as conn:
        row=conn.execute("SELECT id FROM campaigns WHERE rcvo_id=?",(rcvo_id,)).fetchone()
        if row:
            campaign_id=int(row["id"])
            conn.execute(
                """
                UPDATE campaigns SET name=?,status=?,starts_at=?,ends_at=?,
                    eligibility_snapshot_json=?,updated_at=?
                WHERE id=?
                """,
                (
                    name,status,payload.get("starts_at"),payload.get("ends_at"),
                    json.dumps(payload.get("eligibility") or {},ensure_ascii=False,separators=(",",":")),
                    now,campaign_id,
                ),
            )
        else:
            cur=conn.execute(
                """
                INSERT INTO campaigns(
                    rcvo_id,name,campaign_kind,status,starts_at,ends_at,
                    eligibility_snapshot_json,created_at,updated_at
                ) VALUES (?,?,'email',?,?,?,?,?,?)
                """,
                (
                    rcvo_id,name,status,payload.get("starts_at"),payload.get("ends_at"),
                    json.dumps(payload.get("eligibility") or {},ensure_ascii=False,separators=(",",":")),
                    now,now,
                ),
            )
            campaign_id=int(cur.lastrowid)

        for idx,step in enumerate(steps,start=1):
            number=int(step.get("step_number") or idx)
            delay=int(step.get("delay_hours") or 0)
            message_key=str(step.get("message_key") or f"step-{number}").strip()
            subject=str(step.get("subject") or "").strip()
            text=str(step.get("text") or "").strip()
            if not subject or not text:
                raise ValueError(f"subject/text required for step {number}")
            conn.execute(
                """
                INSERT INTO campaign_steps(
                    campaign_id,step_number,delay_hours,message_key,subject_template,
                    text_template,html_template,video_url,active,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(campaign_id,step_number) DO UPDATE SET
                    delay_hours=excluded.delay_hours,
                    message_key=excluded.message_key,
                    subject_template=excluded.subject_template,
                    text_template=excluded.text_template,
                    html_template=excluded.html_template,
                    video_url=excluded.video_url,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (
                    campaign_id,number,delay,message_key,subject,text,
                    step.get("html"),step.get("video_url"),now,now,
                ),
            )
        return {"campaign_id":campaign_id,"rcvo_id":rcvo_id}

def enroll_prospectable(db:Path,campaign_rcvo_id:str,limit:int=10000) -> dict[str,int]:
    init_db(db)
    limit=max(1,min(int(limit),50000))
    now=utc_now()
    with connect(db) as conn:
        campaign=conn.execute(
            "SELECT id,status FROM campaigns WHERE rcvo_id=?",(campaign_rcvo_id,)
        ).fetchone()
        if not campaign:
            raise ValueError("campaign not found")
        if campaign["status"] not in ("scheduled","running"):
            raise ValueError("campaign must be scheduled or running")
        first_step=conn.execute(
            """
            SELECT id,delay_hours FROM campaign_steps
            WHERE campaign_id=? AND active=1
            ORDER BY step_number LIMIT 1
            """,
            (campaign["id"],),
        ).fetchone()
        if not first_step:
            raise ValueError("campaign has no active step")

        rows=conn.execute(
            """
            SELECT contact_id FROM v_prospectable_contacts v
            WHERE NOT EXISTS (
                SELECT 1 FROM campaign_contacts cc
                WHERE cc.campaign_id=? AND cc.contact_id=v.contact_id
            )
            ORDER BY contact_id
            LIMIT ?
            """,
            (campaign["id"],limit),
        ).fetchall()

        enrolled=0
        due=_iso(_parse(now)+timedelta(hours=int(first_step["delay_hours"])))
        for row in rows:
            cur=conn.execute(
                """
                INSERT OR IGNORE INTO campaign_contacts(
                    campaign_id,contact_id,enrolled_at,status,next_eligible_at,last_event_at
                ) VALUES (?, ?, ?, 'eligible', ?, ?)
                """,
                (campaign["id"],row["contact_id"],now,due,now),
            )
            if cur.rowcount:
                cc_id=int(cur.lastrowid)
                conn.execute(
                    """
                    INSERT INTO prospecting_dispatches(
                        dispatch_uuid,campaign_contact_id,step_id,due_at,status,
                        attempts,created_at,updated_at
                    ) VALUES (?,?,?,?,'pending',0,?,?)
                    """,
                    (str(uuid.uuid4()),cc_id,first_step["id"],due,now,now),
                )
                conn.execute(
                    """
                    INSERT INTO prospecting_events(
                        contact_id,campaign_id,event_type,channel,occurred_at,result
                    ) VALUES (?,?,'enrolled','email',?,'eligible')
                    """,
                    (row["contact_id"],campaign["id"],now),
                )
                enrolled+=1
        return {"enrolled":enrolled}

def _eligible_dispatch_sql() -> str:
    return """
        SELECT
            d.id AS dispatch_id,d.dispatch_uuid,d.due_at,d.attempts,
            c.rcvo_id AS contact_rcvo_id,c.first_name,c.last_name,c.display_name,
            e.email_norm AS email,
            o.rcvo_id AS organization_rcvo_id,o.display_name AS organization_name,
            cc.id AS campaign_contact_id,ca.rcvo_id AS campaign_rcvo_id,
            ca.name AS campaign_name,cs.id AS step_id,cs.step_number,cs.delay_hours,
            cs.message_key,cs.subject_template,cs.text_template,cs.html_template,cs.video_url
        FROM prospecting_dispatches d
        JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
        JOIN campaigns ca ON ca.id=cc.campaign_id
        JOIN campaign_steps cs ON cs.id=d.step_id
        JOIN contacts c ON c.id=cc.contact_id
        JOIN contact_emails e ON e.contact_id=c.id AND e.is_primary=1
        LEFT JOIN contact_employments ce ON ce.id=(
            SELECT ce2.id FROM contact_employments ce2
            WHERE ce2.contact_id=c.id AND ce2.is_current=1
            ORDER BY ce2.confidence DESC,ce2.last_seen_at DESC,ce2.id DESC LIMIT 1
        )
        LEFT JOIN organizations o ON o.id=ce.organization_id
        WHERE d.status IN ('pending','deferred','leased')
          AND d.due_at <= ?
          AND (d.lease_until IS NULL OR d.lease_until <= ? OR d.status<>'leased')
          AND ca.status='running'
          AND c.status='active'
          AND c.qualification_status IN ('usable','qualified')
          AND c.vo_relevance IN ('likely','confirmed')
          AND e.deliverability_status NOT IN ('invalid','bounced','unsubscribed')
          AND COALESCE((SELECT status FROM prospecting_state ps WHERE ps.contact_id=c.id),'never_contacted')
              NOT IN ('suppressed','customer','responded')
          AND NOT EXISTS (
              SELECT 1 FROM suppressions s
              WHERE s.active=1 AND (
                  (s.scope_type='contact' AND s.scope_value=c.rcvo_id)
                  OR (s.scope_type='email' AND lower(s.scope_value)=e.email_norm)
                  OR (s.scope_type='organization' AND o.rcvo_id IS NOT NULL AND s.scope_value=o.rcvo_id)
              )
          )
        ORDER BY d.due_at,d.id
        LIMIT ?
    """

def claim_dispatches(
    db:Path,worker_id:str,limit:int=100,lease_seconds:int=300
) -> list[dict[str,Any]]:
    init_db(db)
    limit=max(1,min(int(limit),1000))
    lease_seconds=max(30,min(int(lease_seconds),3600))
    now=utc_now()
    lease_until=_iso(_parse(now)+timedelta(seconds=lease_seconds))
    conn=connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows=conn.execute(_eligible_dispatch_sql(),(now,now,limit)).fetchall()
        ids=[int(r["dispatch_id"]) for r in rows]
        for dispatch_id in ids:
            conn.execute(
                """
                UPDATE prospecting_dispatches
                SET status='leased',lease_worker=?,lease_until=?,attempts=attempts+1,updated_at=?
                WHERE id=?
                """,
                (worker_id,lease_until,now,dispatch_id),
            )
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def return_dispatch(db:Path,dispatch_id:int,worker_id:str) -> None:
    now=utc_now()
    with connect(db) as conn:
        row=conn.execute(
            "SELECT lease_worker FROM prospecting_dispatches WHERE id=?",
            (dispatch_id,),
        ).fetchone()
        if not row or row["lease_worker"]!=worker_id:
            raise RuntimeError("dispatch lease not owned")
        conn.execute(
            """
            UPDATE prospecting_dispatches
            SET status='pending',lease_worker=NULL,lease_until=NULL,
                attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,
                updated_at=?
            WHERE id=?
            """,
            (now,dispatch_id),
        )

def release_dispatch(
    db:Path,dispatch_id:int,worker_id:str,reason:str,defer_seconds:int=300
) -> None:
    now=utc_now()
    due=_iso(_parse(now)+timedelta(seconds=max(30,int(defer_seconds))))
    with connect(db) as conn:
        row=conn.execute(
            "SELECT lease_worker,campaign_contact_id FROM prospecting_dispatches WHERE id=?",
            (dispatch_id,),
        ).fetchone()
        if not row or row["lease_worker"]!=worker_id:
            raise RuntimeError("dispatch lease not owned")
        conn.execute(
            """
            UPDATE prospecting_dispatches
            SET status='deferred',due_at=?,lease_worker=NULL,lease_until=NULL,
                last_error=?,updated_at=?
            WHERE id=?
            """,
            (due,reason[:4000],now,dispatch_id),
        )
        contact=conn.execute(
            "SELECT contact_id,campaign_id FROM campaign_contacts WHERE id=?",
            (row["campaign_contact_id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO prospecting_events(
                contact_id,campaign_id,event_type,channel,occurred_at,result,metadata_json
            ) VALUES (?,?,'send_deferred','email',?,?,?)
            """,
            (contact["contact_id"],contact["campaign_id"],now,reason[:1000],
             json.dumps({"dispatch_id":dispatch_id},separators=(",",":"))),
        )

def _schedule_next_step(conn:sqlite3.Connection,cc_id:int,current_step_id:int,sent_at:str) -> bool:
    current=conn.execute(
        """
        SELECT campaign_id,step_number FROM campaign_steps WHERE id=?
        """,(current_step_id,)
    ).fetchone()
    nxt=conn.execute(
        """
        SELECT id,delay_hours FROM campaign_steps
        WHERE campaign_id=? AND active=1 AND step_number>?
        ORDER BY step_number LIMIT 1
        """,
        (current["campaign_id"],current["step_number"]),
    ).fetchone()
    if not nxt:
        return False
    due=_iso(_parse(sent_at)+timedelta(hours=int(nxt["delay_hours"])))
    now=utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO prospecting_dispatches(
            dispatch_uuid,campaign_contact_id,step_id,due_at,status,attempts,created_at,updated_at
        ) VALUES (?,?,?,?,'pending',0,?,?)
        """,
        (str(uuid.uuid4()),cc_id,nxt["id"],due,now,now),
    )
    conn.execute(
        """
        UPDATE campaign_contacts SET status='queued',next_eligible_at=?,last_event_at=?
        WHERE id=?
        """,
        (due,now,cc_id),
    )
    return True

def mark_sent(
    db:Path,dispatch_id:int,worker_id:str,sender_mailbox:str,
    provider_message_id:str|None,sent_at:str|None=None
) -> dict[str,Any]:
    sent_at=sent_at or utc_now()
    with connect(db) as conn:
        row=conn.execute(
            """
            SELECT d.*,cc.contact_id,cc.campaign_id
            FROM prospecting_dispatches d
            JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            WHERE d.id=?
            """,
            (dispatch_id,),
        ).fetchone()
        if not row or row["lease_worker"]!=worker_id:
            raise RuntimeError("dispatch lease not owned")
        conn.execute(
            """
            UPDATE prospecting_dispatches
            SET status='sent',lease_worker=NULL,lease_until=NULL,sender_mailbox=?,
                provider_message_id=?,sent_at=?,updated_at=?,last_error=NULL
            WHERE id=?
            """,
            (sender_mailbox,provider_message_id,sent_at,utc_now(),dispatch_id),
        )
        event_type="email_sent"
        step=conn.execute(
            "SELECT step_number,message_key FROM campaign_steps WHERE id=?",(row["step_id"],)
        ).fetchone()
        if int(step["step_number"])>1:
            event_type="followup_sent"
        conn.execute(
            """
            INSERT INTO prospecting_events(
                contact_id,campaign_id,event_type,channel,occurred_at,sender_mailbox,
                message_key,provider_message_id,result
            ) VALUES (?,?,?,'email',?,?,?,?, 'sent')
            """,
            (
                row["contact_id"],row["campaign_id"],event_type,sent_at,sender_mailbox,
                step["message_key"],provider_message_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO prospecting_state(
                contact_id,status,total_emails_sent,first_contact_at,last_contact_at,
                last_campaign_id,updated_at
            ) VALUES (?,'contacted',1,?,?,?,?)
            ON CONFLICT(contact_id) DO UPDATE SET
                status='contacted',
                total_emails_sent=prospecting_state.total_emails_sent+1,
                first_contact_at=COALESCE(prospecting_state.first_contact_at,excluded.first_contact_at),
                last_contact_at=excluded.last_contact_at,
                last_campaign_id=excluded.last_campaign_id,
                updated_at=excluded.updated_at
            """,
            (row["contact_id"],sent_at,sent_at,row["campaign_id"],utc_now()),
        )
        scheduled=_schedule_next_step(conn,row["campaign_contact_id"],row["step_id"],sent_at)
        if not scheduled:
            conn.execute(
                "UPDATE campaign_contacts SET status='completed',next_eligible_at=NULL,last_event_at=? WHERE id=?",
                (utc_now(),row["campaign_contact_id"]),
            )
        return {"next_step_scheduled":scheduled}

def record_feedback(
    db:Path,contact_rcvo_id:str,event_type:str,occurred_at:str|None=None,
    campaign_rcvo_id:str|None=None,provider_message_id:str|None=None,
    email:str|None=None,details:dict[str,Any]|None=None
) -> None:
    occurred_at=occurred_at or utc_now()
    allowed={
        "email_delivered","email_opened","link_clicked","video_clicked","reply_received",
        "bounce","complaint","optout","unsubscribe","send_failed","manual_note"
    }
    if event_type not in allowed:
        raise ValueError("unsupported feedback event")
    with connect(db) as conn:
        contact=conn.execute(
            "SELECT id FROM contacts WHERE rcvo_id=?",(contact_rcvo_id,)
        ).fetchone()
        if not contact:
            raise ValueError("contact not found")
        campaign_id=None
        if campaign_rcvo_id:
            row=conn.execute(
                "SELECT id FROM campaigns WHERE rcvo_id=?",(campaign_rcvo_id,)
            ).fetchone()
            if row: campaign_id=int(row["id"])
        conn.execute(
            """
            INSERT INTO prospecting_events(
                contact_id,campaign_id,event_type,channel,occurred_at,provider_message_id,
                result,metadata_json
            ) VALUES (?,?,?,'email',?,?,?,?)
            """,
            (
                contact["id"],campaign_id,event_type,occurred_at,provider_message_id,
                event_type,json.dumps(details or {},ensure_ascii=False,separators=(",",":")),
            ),
        )

        stop=False
        reason=None
        if event_type in ("optout","unsubscribe","complaint"):
            stop=True
            reason="optout" if event_type in ("optout","unsubscribe") else "other"
        elif event_type=="bounce" and (details or {}).get("hard",True):
            stop=True
            reason="hard_bounce"

        if stop:
            target=norm_email(email)
            if not target:
                erow=conn.execute(
                    "SELECT email_norm FROM contact_emails WHERE contact_id=? AND is_primary=1",
                    (contact["id"],),
                ).fetchone()
                target=erow["email_norm"] if erow else None
            if target:
                conn.execute(
                    """
                    INSERT INTO suppressions(
                        scope_type,scope_value,reason,active,permanent,starts_at,source,notes
                    ) VALUES ('email',?,?,1,1,?,'prospecting_agent',?)
                    ON CONFLICT(scope_type,scope_value,reason) DO UPDATE SET
                        active=1,permanent=1,starts_at=excluded.starts_at,ends_at=NULL,
                        source=excluded.source,notes=excluded.notes
                    """,
                    (target,reason,occurred_at,event_type),
                )
                sid=conn.execute(
                    "SELECT id FROM suppressions WHERE scope_type='email' AND scope_value=? AND reason=?",
                    (target,reason),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO suppression_events(
                        suppression_id,event_type,occurred_at,actor,details_json
                    ) VALUES (?,'added',?,'prospecting_agent',?)
                    """,
                    (sid["id"],occurred_at,json.dumps(details or {},separators=(",",":"))),
                )
            conn.execute(
                """
                UPDATE prospecting_state SET status='suppressed',updated_at=?
                WHERE contact_id=?
                """,
                (utc_now(),contact["id"]),
            )
            conn.execute(
                """
                UPDATE prospecting_dispatches
                SET status='cancelled',lease_worker=NULL,lease_until=NULL,
                    updated_at=?,last_error=?
                WHERE campaign_contact_id IN (
                    SELECT id FROM campaign_contacts WHERE contact_id=?
                ) AND status IN ('pending','deferred','leased')
                """,
                (utc_now(),event_type,contact["id"]),
            )
        elif event_type=="reply_received":
            conn.execute(
                """
                UPDATE prospecting_state SET status='responded',updated_at=?
                WHERE contact_id=?
                """,
                (utc_now(),contact["id"]),
            )
            conn.execute(
                """
                UPDATE prospecting_dispatches
                SET status='cancelled',lease_worker=NULL,lease_until=NULL,
                    updated_at=?,last_error='reply_received'
                WHERE campaign_contact_id IN (
                    SELECT id FROM campaign_contacts WHERE contact_id=?
                ) AND status IN ('pending','deferred','leased')
                """,
                (utc_now(),contact["id"]),
            )

def metrics(db:Path) -> dict[str,Any]:
    init_db(db)
    with connect(db) as conn:
        return {
            "campaigns":int(conn.execute("SELECT count(*) FROM campaigns").fetchone()[0]),
            "campaign_contacts":int(conn.execute("SELECT count(*) FROM campaign_contacts").fetchone()[0]),
            "dispatches":{
                row[0]:int(row[1]) for row in conn.execute(
                    "SELECT status,count(*) FROM prospecting_dispatches GROUP BY status"
                )
            },
            "events":int(conn.execute("SELECT count(*) FROM prospecting_events").fetchone()[0]),
            "suppressions":int(conn.execute("SELECT count(*) FROM suppressions WHERE active=1").fetchone()[0]),
        }
