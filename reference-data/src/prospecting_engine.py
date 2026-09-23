from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reference_engine import connect, init_db

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def _parse_dt(value:str|None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)

def _campaign_row(conn:sqlite3.Connection,rcvo_id:str):
    row=conn.execute("SELECT * FROM campaigns WHERE rcvo_id=?",(rcvo_id,)).fetchone()
    if not row:
        raise ValueError(f"campaign not found: {rcvo_id}")
    return row

def upsert_campaign(path:Path,payload:dict[str,Any]) -> dict[str,Any]:
    init_db(path)
    rcvo_id=str(payload.get("rcvo_id") or f"CMP-{uuid.uuid4().hex[:20].upper()}")
    name=str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("campaign name required")
    now=utc_now()
    status=str(payload.get("status") or "draft")
    snapshot=payload.get("eligibility_snapshot")
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO campaigns(
                rcvo_id,name,campaign_kind,status,starts_at,ends_at,
                eligibility_snapshot_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(rcvo_id) DO UPDATE SET
                name=excluded.name,
                campaign_kind=excluded.campaign_kind,
                status=excluded.status,
                starts_at=excluded.starts_at,
                ends_at=excluded.ends_at,
                eligibility_snapshot_json=excluded.eligibility_snapshot_json,
                updated_at=excluded.updated_at
            """,
            (
                rcvo_id,name,str(payload.get("campaign_kind") or "email"),status,
                payload.get("starts_at"),payload.get("ends_at"),
                json.dumps(snapshot,ensure_ascii=False,separators=(",",":")) if snapshot is not None else None,
                now,now,
            ),
        )
        row=_campaign_row(conn,rcvo_id)
        return dict(row)

def register_message_version(path:Path,payload:dict[str,Any]) -> dict[str,Any]:
    init_db(path)
    key=str(payload.get("message_key") or "").strip()
    version=int(payload.get("version") or 1)
    subject=str(payload.get("subject_template") or "")
    text=str(payload.get("text_template") or "")
    html=payload.get("html_template")
    video=payload.get("video_url")
    if not key or not subject or not text:
        raise ValueError("message_key, subject_template and text_template required")
    canonical=json.dumps(
        {"key":key,"version":version,"subject":subject,"text":text,"html":html,"video":video},
        ensure_ascii=False,sort_keys=True,separators=(",",":")
    )
    sha=hashlib.sha256(canonical.encode()).hexdigest()
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO prospecting_message_versions(
                message_key,version,subject_template,text_template,html_template,
                video_url,content_sha256,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(message_key,version) DO UPDATE SET
                subject_template=excluded.subject_template,
                text_template=excluded.text_template,
                html_template=excluded.html_template,
                video_url=excluded.video_url,
                content_sha256=excluded.content_sha256
            """,
            (key,version,subject,text,html,video,sha,utc_now()),
        )
        row=conn.execute(
            "SELECT * FROM prospecting_message_versions WHERE message_key=? AND version=?",
            (key,version),
        ).fetchone()
        return dict(row)

def eligible_contacts(path:Path,limit:int=1000) -> list[dict[str,Any]]:
    init_db(path)
    limit=max(1,min(int(limit),5000))
    with connect(path) as conn:
        return [
            dict(r) for r in conn.execute(
                """
                SELECT * FROM v_prospectable_contacts
                ORDER BY contact_id
                LIMIT ?
                """,
                (limit,),
            )
        ]

def enroll_contacts(
    path:Path,campaign_rcvo_id:str,contact_rcvo_ids:list[str]
) -> dict[str,int]:
    init_db(path)
    now=utc_now()
    enrolled=skipped=0
    with connect(path) as conn:
        campaign=_campaign_row(conn,campaign_rcvo_id)
        for rcvo_id in contact_rcvo_ids[:10000]:
            contact=conn.execute(
                """
                SELECT p.contact_id
                FROM v_prospectable_contacts p
                WHERE p.rcvo_id=?
                """,
                (rcvo_id,),
            ).fetchone()
            if not contact:
                skipped+=1
                continue
            cur=conn.execute(
                """
                INSERT OR IGNORE INTO campaign_contacts(
                    campaign_id,contact_id,enrolled_at,status,next_eligible_at,last_event_at,
                    step_index,send_attempts
                ) VALUES (?,?,?,'eligible',?,NULL,0,0)
                """,
                (campaign["id"],contact["contact_id"],now,now),
            )
            if cur.rowcount:
                enrolled+=1
                conn.execute(
                    """
                    INSERT INTO prospecting_events(
                        contact_id,campaign_id,event_type,occurred_at,event_key,result
                    ) VALUES (?,?,'enrolled',?,?,?)
                    """,
                    (
                        contact["contact_id"],campaign["id"],now,
                        f"enroll:{campaign_rcvo_id}:{rcvo_id}","enrolled",
                    ),
                )
            else:
                skipped+=1
        return {"enrolled":enrolled,"skipped":skipped}

def enroll_eligible(path:Path,campaign_rcvo_id:str,limit:int=5000) -> dict[str,int]:
    init_db(path)
    limit=max(1,min(int(limit),10000))
    with connect(path) as conn:
        campaign=_campaign_row(conn,campaign_rcvo_id)
        rows=conn.execute(
            """
            SELECT p.rcvo_id
            FROM v_prospectable_contacts p
            WHERE NOT EXISTS (
                SELECT 1 FROM campaign_contacts cc
                WHERE cc.campaign_id=? AND cc.contact_id=p.contact_id
            )
            ORDER BY p.contact_id
            LIMIT ?
            """,
            (campaign["id"],limit),
        ).fetchall()
    return enroll_contacts(path,campaign_rcvo_id,[r["rcvo_id"] for r in rows])

def claim_due(
    path:Path,campaign_rcvo_id:str,worker_id:str,limit:int=200,lease_seconds:int=300
) -> list[dict[str,Any]]:
    init_db(path)
    if not worker_id:
        raise ValueError("worker_id required")
    limit=max(1,min(int(limit),1000))
    lease_seconds=max(30,min(int(lease_seconds),3600))
    now_dt=datetime.now(timezone.utc)
    now=now_dt.isoformat(timespec="milliseconds").replace("+00:00","Z")
    until=(now_dt+timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds").replace("+00:00","Z")
    conn=connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        campaign=_campaign_row(conn,campaign_rcvo_id)
        if campaign["status"] not in ("running","scheduled"):
            conn.rollback()
            return []
        if campaign["starts_at"] and campaign["starts_at"]>now:
            conn.rollback()
            return []
        if campaign["ends_at"] and campaign["ends_at"]<=now:
            conn.rollback()
            return []
        rows=conn.execute(
            """
            SELECT
                cc.id AS campaign_contact_id,
                cc.step_index,
                cc.send_attempts,
                p.*
            FROM campaign_contacts cc
            JOIN v_prospectable_contacts p ON p.contact_id=cc.contact_id
            WHERE cc.campaign_id=?
              AND cc.status IN ('eligible','queued')
              AND (cc.next_eligible_at IS NULL OR cc.next_eligible_at<=?)
              AND (cc.lease_until IS NULL OR cc.lease_until<=?)
            ORDER BY COALESCE(cc.next_eligible_at,cc.enrolled_at),cc.id
            LIMIT ?
            """,
            (campaign["id"],now,now,limit),
        ).fetchall()
        ids=[r["campaign_contact_id"] for r in rows]
        for cid in ids:
            conn.execute(
                """
                UPDATE campaign_contacts
                SET status='queued',claimed_by=?,claimed_at=?,lease_until=?,
                    send_attempts=send_attempts+1
                WHERE id=?
                """,
                (worker_id,now,until,cid),
            )
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def release_claim(
    path:Path,campaign_contact_id:int,worker_id:str,
    *,next_eligible_at:str|None=None,error:bool=False
) -> None:
    init_db(path)
    with connect(path) as conn:
        row=conn.execute(
            "SELECT claimed_by FROM campaign_contacts WHERE id=?",
            (campaign_contact_id,),
        ).fetchone()
        if not row or row["claimed_by"]!=worker_id:
            raise RuntimeError("campaign contact lease is not owned by this worker")
        conn.execute(
            """
            UPDATE campaign_contacts
            SET status=?,claimed_by=NULL,claimed_at=NULL,lease_until=NULL,
                next_eligible_at=COALESCE(?,next_eligible_at)
            WHERE id=?
            """,
            ("error" if error else "eligible",next_eligible_at,campaign_contact_id),
        )

def _scope_suppression(
    conn:sqlite3.Connection,scope_type:str,scope_value:str,reason:str,source:str,occurred_at:str
):
    scope_value=scope_value.lower() if scope_type in ("email","domain") else scope_value
    conn.execute(
        """
        INSERT INTO suppressions(
            scope_type,scope_value,reason,active,permanent,starts_at,source
        ) VALUES (?,?,?,1,1,?,?)
        ON CONFLICT(scope_type,scope_value,reason) DO UPDATE SET
            active=1,permanent=1,starts_at=excluded.starts_at,ends_at=NULL,source=excluded.source
        """,
        (scope_type,scope_value,reason,occurred_at,source),
    )
    sup=conn.execute(
        "SELECT id FROM suppressions WHERE scope_type=? AND scope_value=? AND reason=?",
        (scope_type,scope_value,reason),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO suppression_events(suppression_id,event_type,occurred_at,actor,details_json)
        VALUES (?,'added',?,?,?)
        """,
        (sup["id"],occurred_at,source,json.dumps({"reason":reason},separators=(",",":"))),
    )

def record_events(path:Path,events:list[dict[str,Any]]) -> dict[str,int]:
    init_db(path)
    inserted=duplicate=failed=0
    conn=connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for e in events[:5000]:
            try:
                event_type=str(e["event_type"])
                event_key=str(e.get("event_key") or "").strip() or None
                contact_rcvo_id=str(e.get("contact_rcvo_id") or "").strip()
                campaign_rcvo_id=str(e.get("campaign_rcvo_id") or "").strip() or None
                occurred_at=str(e.get("occurred_at") or utc_now())
                contact=conn.execute(
                    "SELECT id FROM contacts WHERE rcvo_id=?",(contact_rcvo_id,)
                ).fetchone()
                if not contact:
                    raise ValueError(f"contact not found: {contact_rcvo_id}")
                campaign_id=None
                if campaign_rcvo_id:
                    campaign_id=_campaign_row(conn,campaign_rcvo_id)["id"]
                if event_key:
                    exists=conn.execute(
                        "SELECT id FROM prospecting_events WHERE event_key=?",(event_key,)
                    ).fetchone()
                    if exists:
                        duplicate+=1
                        continue

                email=conn.execute(
                    "SELECT email_norm FROM contact_emails WHERE contact_id=? AND is_primary=1",
                    (contact["id"],),
                ).fetchone()
                metadata=e.get("metadata")
                conn.execute(
                    """
                    INSERT INTO prospecting_events(
                        contact_id,campaign_id,event_type,channel,occurred_at,
                        sender_mailbox,message_key,provider_message_id,event_key,result,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        contact["id"],campaign_id,event_type,str(e.get("channel") or "email"),
                        occurred_at,e.get("sender_mailbox"),e.get("message_key"),
                        e.get("provider_message_id"),event_key,e.get("result"),
                        json.dumps(metadata,ensure_ascii=False,separators=(",",":"),default=str)
                            if metadata is not None else None,
                    ),
                )

                cc=None
                if campaign_id:
                    cc=conn.execute(
                        """
                        SELECT id,step_index FROM campaign_contacts
                        WHERE campaign_id=? AND contact_id=?
                        """,
                        (campaign_id,contact["id"]),
                    ).fetchone()

                if event_type in ("email_sent","followup_sent"):
                    next_at=e.get("next_eligible_at")
                    completed=bool(e.get("completed"))
                    conn.execute(
                        """
                        UPDATE prospecting_state
                        SET status=?,
                            total_emails_sent=total_emails_sent+1,
                            first_contact_at=COALESCE(first_contact_at,?),
                            last_contact_at=?,
                            next_eligible_at=?,
                            last_campaign_id=COALESCE(?,last_campaign_id),
                            updated_at=?
                        WHERE contact_id=?
                        """,
                        (
                            "contacted",occurred_at,occurred_at,next_at,campaign_id,
                            utc_now(),contact["id"],
                        ),
                    )
                    if cc:
                        conn.execute(
                            """
                            UPDATE campaign_contacts
                            SET status=?,next_eligible_at=?,last_event_at=?,
                                step_index=step_index+1,
                                claimed_by=NULL,claimed_at=NULL,lease_until=NULL
                            WHERE id=?
                            """,
                            ("completed" if completed else "eligible",next_at,occurred_at,cc["id"]),
                        )
                elif event_type=="reply_received":
                    conn.execute(
                        "UPDATE prospecting_state SET status='responded',updated_at=? WHERE contact_id=?",
                        (utc_now(),contact["id"]),
                    )
                    if cc:
                        conn.execute(
                            """
                            UPDATE campaign_contacts
                            SET status='responded',last_event_at=?,
                                claimed_by=NULL,claimed_at=NULL,lease_until=NULL
                            WHERE id=?
                            """,
                            (occurred_at,cc["id"]),
                        )
                elif event_type in ("optout","unsubscribe_requested","spam_complaint","bounce"):
                    reason={
                        "optout":"optout",
                        "unsubscribe_requested":"optout",
                        "spam_complaint":"optout",
                        "bounce":"hard_bounce",
                    }[event_type]
                    if email:
                        _scope_suppression(
                            conn,"email",email["email_norm"],reason,
                            str(e.get("source") or "prospecting_event"),occurred_at
                        )
                        if event_type=="bounce":
                            conn.execute(
                                "UPDATE contact_emails SET deliverability_status='bounced' WHERE contact_id=? AND is_primary=1",
                                (contact["id"],),
                            )
                        elif event_type in ("optout","unsubscribe_requested","spam_complaint"):
                            conn.execute(
                                "UPDATE contact_emails SET deliverability_status='unsubscribed' WHERE contact_id=? AND is_primary=1",
                                (contact["id"],),
                            )
                    conn.execute(
                        "UPDATE prospecting_state SET status='suppressed',next_eligible_at=NULL,updated_at=? WHERE contact_id=?",
                        (utc_now(),contact["id"]),
                    )
                    if cc:
                        conn.execute(
                            """
                            UPDATE campaign_contacts SET status='suppressed',last_event_at=?,
                                claimed_by=NULL,claimed_at=NULL,lease_until=NULL
                            WHERE id=?
                            """,
                            (occurred_at,cc["id"]),
                        )
                elif event_type=="soft_bounce":
                    next_at=e.get("next_eligible_at")
                    conn.execute(
                        "UPDATE contact_emails SET deliverability_status='risky' WHERE contact_id=? AND is_primary=1 AND deliverability_status='unknown'",
                        (contact["id"],),
                    )
                    if cc:
                        conn.execute(
                            """
                            UPDATE campaign_contacts SET status='eligible',next_eligible_at=?,
                                last_event_at=?,claimed_by=NULL,claimed_at=NULL,lease_until=NULL
                            WHERE id=?
                            """,
                            (next_at,occurred_at,cc["id"]),
                        )

                inserted+=1
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed: prospecting_events.event_key" in str(exc):
                    duplicate+=1
                else:
                    failed+=1
            except Exception:
                failed+=1
        conn.commit()
        return {"inserted":inserted,"duplicate":duplicate,"failed":failed}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def suppress(path:Path,payload:dict[str,Any]) -> dict[str,Any]:
    init_db(path)
    contact_rcvo_id=str(payload.get("contact_rcvo_id") or "").strip()
    email_value=str(payload.get("email") or "").strip().lower()
    reason=str(payload.get("reason") or "optout")
    source=str(payload.get("source") or "prospecting_agent")
    if not contact_rcvo_id and not email_value:
        raise ValueError("contact_rcvo_id or email required")
    now=str(payload.get("occurred_at") or utc_now())
    with connect(path) as conn:
        contact=None
        if contact_rcvo_id:
            contact=conn.execute("SELECT id FROM contacts WHERE rcvo_id=?",(contact_rcvo_id,)).fetchone()
            if not contact:
                raise ValueError("contact not found")
            email=conn.execute(
                "SELECT email_norm FROM contact_emails WHERE contact_id=? AND is_primary=1",
                (contact["id"],),
            ).fetchone()
            if email:
                email_value=email["email_norm"]
        if not email_value:
            raise ValueError("no email available for suppression")
        _scope_suppression(conn,"email",email_value,reason,source,now)
        if contact:
            conn.execute(
                "UPDATE prospecting_state SET status='suppressed',next_eligible_at=NULL,updated_at=? WHERE contact_id=?",
                (utc_now(),contact["id"]),
            )
            conn.execute(
                "UPDATE contact_emails SET deliverability_status='unsubscribed' WHERE contact_id=? AND email_norm=?",
                (contact["id"],email_value),
            )
            conn.execute(
                "UPDATE campaign_contacts SET status='suppressed',claimed_by=NULL,claimed_at=NULL,lease_until=NULL WHERE contact_id=?",
                (contact["id"],),
            )
        return {"suppressed":True,"email":email_value}


def dashboard_metrics(path:Path) -> dict[str,Any]:
    init_db(path)
    with connect(path) as conn:
        event_counts={
            row["event_type"]:int(row["n"])
            for row in conn.execute(
                "SELECT event_type,count(*) AS n FROM prospecting_events GROUP BY event_type"
            )
        }
        campaign_counts={
            row["status"]:int(row["n"])
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM campaigns GROUP BY status"
            )
        }
        total_contacts=int(conn.execute("SELECT count(*) FROM contacts").fetchone()[0])
        active_contacts=int(conn.execute(
            "SELECT count(*) FROM contacts WHERE status='active'"
        ).fetchone()[0])
        prospectable=int(conn.execute(
            "SELECT count(*) FROM v_prospectable_contacts"
        ).fetchone()[0])
        suppressions=int(conn.execute(
            "SELECT count(*) FROM suppressions WHERE active=1"
        ).fetchone()[0])
        enrolled=int(conn.execute(
            "SELECT count(*) FROM campaign_contacts"
        ).fetchone()[0])
        emails_sent=event_counts.get("email_sent",0)+event_counts.get("followup_sent",0)
        return {
            "contacts":{
                "total":total_contacts,
                "active":active_contacts,
                "prospectable":prospectable,
                "suppressed":suppressions,
                "campaign_enrollments":enrolled,
            },
            "prospecting":{
                "emails_sent":emails_sent,
                "initial_emails_sent":event_counts.get("email_sent",0),
                "followups_sent":event_counts.get("followup_sent",0),
                "video_views":event_counts.get("video_viewed",0),
                "replies":event_counts.get("reply_received",0),
                "delivered":event_counts.get("email_delivered",0),
                "soft_bounces":event_counts.get("soft_bounce",0),
                "hard_bounces":event_counts.get("bounce",0),
                "spam_complaints":event_counts.get("spam_complaint",0),
                "unsubscribes":event_counts.get("unsubscribe_requested",0)+event_counts.get("optout",0),
                "delivery_deferred":event_counts.get("delivery_deferred",0),
                "delivery_rejected":event_counts.get("delivery_rejected",0),
                "events_total":sum(event_counts.values()),
            },
            "campaigns":campaign_counts,
        }
