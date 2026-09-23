from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "reference.sql"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path,timeout=30)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn

def init_db(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))

def _clean(value: Any) -> str | None:
    if value is None:
        return None
    value=re.sub(r"\s+"," ",str(value)).strip()
    return value or None

def norm_email(value: Any) -> str | None:
    v=_clean(value)
    return v.lower() if v else None

def norm_domain(value: Any) -> str | None:
    v=_clean(value)
    if not v:
        return None
    v=v.lower()
    v=re.sub(r"^https?://","",v)
    v=v.split("/",1)[0].split(":",1)[0].strip(".")
    if v.startswith("www."):
        v=v[4:]
    return v or None

def norm_org_name(value: Any) -> str | None:
    v=_clean(value)
    if not v:
        return None
    v=v.casefold()
    v=re.sub(r"[^\w\s-]"," ",v,flags=re.UNICODE)
    v=re.sub(r"\b(sas|sasu|sa|sarl|eurl|sas|groupe|group)\b"," ",v)
    return re.sub(r"\s+"," ",v).strip() or None

def norm_city(value: Any) -> str | None:
    v=_clean(value)
    return v.title() if v else None

def norm_last_name(value: Any) -> str | None:
    v=_clean(value)
    return v.upper() if v else None

def norm_first_name(value: Any) -> str | None:
    v=_clean(value)
    if not v:
        return None
    return "-".join(part[:1].upper()+part[1:].lower() if part else "" for part in v.split("-"))

def norm_job(value: Any) -> str | None:
    v=_clean(value)
    return v.casefold() if v else None

def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20].upper()}"

def _confidence(obj: dict[str,Any] | None, default: float=0.0) -> float:
    if not obj:
        return default
    try:
        value=float(obj.get("confidence",default))
    except Exception:
        return default
    return max(0.0,min(1.0,value))

def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value,ensure_ascii=False,separators=(",",":"),default=str)

def _event(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    event_type: str,
    *,
    contact_id: int | None=None,
    organization_id: int | None=None,
    details: Any=None,
) -> None:
    conn.execute(
        """
        INSERT INTO promotion_events(
            source_key,raw_batch_uuid,raw_record_id,event_type,occurred_at,
            contact_id,organization_id,details_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            raw["source_key"],raw.get("raw_batch_uuid"),raw.get("raw_record_id"),
            event_type,utc_now(),contact_id,organization_id,_json(details),
        ),
    )

def _observe(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    target_type: str,
    target_id: int,
    field_name: str,
    observed: Any,
    normalized: Any,
    confidence: float | None,
) -> None:
    if observed is None:
        return
    conn.execute(
        """
        INSERT INTO source_observations(
            target_type,target_id,field_name,observed_value,normalized_value,
            source_key,raw_batch_uuid,raw_record_id,observed_at,confidence
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            target_type,target_id,field_name,str(observed),
            None if normalized is None else str(normalized),
            raw["source_key"],raw.get("raw_batch_uuid"),raw.get("raw_record_id"),
            utc_now(),confidence,
        ),
    )

def _link_external(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    entity_type: str,
    external_id: Any,
    *,
    contact_id: int | None=None,
    organization_id: int | None=None,
    site_id: int | None=None,
    email_id: int | None=None,
    employment_id: int | None=None,
) -> None:
    ext=_clean(external_id)
    if not ext:
        return
    now=utc_now()
    row=conn.execute(
        """
        SELECT id,contact_id,organization_id,site_id,email_id,employment_id
        FROM external_identities
        WHERE source_key=? AND entity_type=? AND external_id=?
        """,
        (raw["source_key"],entity_type,ext),
    ).fetchone()
    target=(contact_id,organization_id,site_id,email_id,employment_id)
    if row:
        existing=(row["contact_id"],row["organization_id"],row["site_id"],row["email_id"],row["employment_id"])
        if any(a is not None and b is not None and a!=b for a,b in zip(existing,target)):
            raise ValueError(f"external identity conflict: {entity_type}:{ext}")
        conn.execute(
            "UPDATE external_identities SET last_seen_at=? WHERE id=?",
            (now,row["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO external_identities(
            source_key,entity_type,external_id,contact_id,organization_id,site_id,
            email_id,employment_id,first_seen_at,last_seen_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            raw["source_key"],entity_type,ext,contact_id,organization_id,site_id,
            email_id,employment_id,now,now,
        ),
    )
    _event(
        conn,raw,"external_identity_linked",
        contact_id=contact_id,organization_id=organization_id,
        details={"entity_type":entity_type,"external_id":ext},
    )

def _find_external(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    entity_type: str,
    external_id: Any,
) -> sqlite3.Row | None:
    ext=_clean(external_id)
    if not ext:
        return None
    return conn.execute(
        """
        SELECT * FROM external_identities
        WHERE source_key=? AND entity_type=? AND external_id=?
        """,
        (raw["source_key"],entity_type,ext),
    ).fetchone()

def _organization(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
) -> tuple[int | None,str | None,bool]:
    org=payload.get("organization") or {}
    explicit=_clean(payload.get("organization_match_rcvo_id"))
    domain=norm_domain(org.get("domain") or org.get("website_domain"))
    external_id=org.get("external_id")
    row=None
    method=None

    if explicit:
        row=conn.execute("SELECT * FROM organizations WHERE rcvo_id=?",(explicit,)).fetchone()
        if not row:
            raise ValueError(f"organization_match_rcvo_id not found: {explicit}")
        method="explicit_rcvo_id"
    if row is None and external_id:
        ext=_find_external(conn,raw,"organization",external_id)
        if ext and ext["organization_id"]:
            row=conn.execute("SELECT * FROM organizations WHERE id=?",(ext["organization_id"],)).fetchone()
            method="external_id"
    if row is None and domain:
        row=conn.execute(
            """
            SELECT o.* FROM organizations o
            JOIN organization_domains d ON d.organization_id=o.id
            WHERE d.domain_norm=?
            """,
            (domain,),
        ).fetchone()
        if row:
            method="exact_domain"

    now=utc_now()
    conf=_confidence(org)
    display=_clean(org.get("display_name") or org.get("name") or org.get("legal_name"))
    normalized=norm_org_name(display)
    city=norm_city(org.get("city"))
    created=False

    if row is None and not any([display,domain,external_id,city]):
        return None,None,False

    if row is None:
        rcvo_id=new_id("ENT")
        cur=conn.execute(
            """
            INSERT INTO organizations(
                rcvo_id,legal_name,display_name,normalized_name,registration_number,
                city,postal_code,country_code,sector_hint,vo_relevance,status,
                confidence,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rcvo_id,_clean(org.get("legal_name")),display,normalized,
                _clean(org.get("registration_number") or org.get("siren")),
                city,_clean(org.get("postal_code")),(_clean(org.get("country_code")) or "FR").upper(),
                _clean(org.get("sector_hint")),org.get("vo_relevance","unknown"),"active",
                conf,now,now,
            ),
        )
        org_id=int(cur.lastrowid)
        created=True
        method="new"
        _event(conn,raw,"organization_created",organization_id=org_id,details={"rcvo_id":rcvo_id})
    else:
        org_id=int(row["id"])
        rcvo_id=row["rcvo_id"]
        overrides=set(payload.get("organization_override_fields") or [])
        updates: dict[str,Any]={}
        candidates={
            "legal_name":_clean(org.get("legal_name")),
            "display_name":display,
            "normalized_name":normalized,
            "registration_number":_clean(org.get("registration_number") or org.get("siren")),
            "city":city,
            "postal_code":_clean(org.get("postal_code")),
            "country_code":(_clean(org.get("country_code")) or None),
            "sector_hint":_clean(org.get("sector_hint")),
            "vo_relevance":org.get("vo_relevance"),
        }
        for field,value in candidates.items():
            if value is None:
                continue
            if row[field] is None or field in overrides:
                updates[field]=value.upper() if field=="country_code" else value
        if conf>float(row["confidence"] or 0):
            updates["confidence"]=conf
        if updates:
            updates["updated_at"]=now
            sets=",".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE organizations SET {sets} WHERE id=?",(*updates.values(),org_id))
            _event(conn,raw,"organization_enriched",organization_id=org_id,details={"fields":list(updates)})
        _event(conn,raw,"organization_matched",organization_id=org_id,details={"method":method})

    if display:
        conn.execute(
            """
            INSERT INTO organization_aliases(
                organization_id,alias_raw,alias_norm,source_key,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(organization_id,alias_norm) DO UPDATE SET
                alias_raw=excluded.alias_raw,last_seen_at=excluded.last_seen_at
            """,
            (org_id,display,normalized or display.casefold(),raw["source_key"],now,now),
        )
    if domain:
        existing=conn.execute("SELECT organization_id FROM organization_domains WHERE domain_norm=?",(domain,)).fetchone()
        if existing and int(existing["organization_id"])!=org_id:
            raise ValueError(f"domain already belongs to another organization: {domain}")
        wants_primary=org.get("is_primary_domain")
        current_primary=conn.execute(
            "SELECT id FROM organization_domains WHERE organization_id=? AND is_primary=1",
            (org_id,),
        ).fetchone()
        if wants_primary is True:
            conn.execute("UPDATE organization_domains SET is_primary=0 WHERE organization_id=?",(org_id,))
            primary_domain=1
        elif wants_primary is False:
            primary_domain=0
        else:
            primary_domain=1 if current_primary is None else 0
        conn.execute(
            """
            INSERT INTO organization_domains(
                organization_id,domain_raw,domain_norm,is_primary,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(domain_norm) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                is_primary=CASE WHEN excluded.is_primary=1 THEN 1 ELSE organization_domains.is_primary END
            """,
            (org_id,str(org.get("domain") or org.get("website_domain")),domain,primary_domain,now,now),
        )
    _link_external(conn,raw,"organization",external_id,organization_id=org_id)

    for field,observed,normalized_value in [
        ("display_name",display,normalized),("domain",org.get("domain") or org.get("website_domain"),domain),
        ("city",org.get("city"),city),("postal_code",org.get("postal_code"),_clean(org.get("postal_code"))),
        ("vo_relevance",org.get("vo_relevance"),org.get("vo_relevance")),
    ]:
        _observe(conn,raw,"organization",org_id,field,observed,normalized_value,conf)
    return org_id,rcvo_id,created

def _site(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
    organization_id: int | None,
) -> tuple[int | None,str | None]:
    site=payload.get("site") or {}
    if not site or organization_id is None:
        return None,None
    explicit=_clean(payload.get("site_match_rcvo_id"))
    row=None
    if explicit:
        row=conn.execute("SELECT * FROM organization_sites WHERE rcvo_id=?",(explicit,)).fetchone()
        if not row:
            raise ValueError(f"site_match_rcvo_id not found: {explicit}")
        if int(row["organization_id"])!=organization_id:
            raise ValueError("site does not belong to matched organization")
    external_id=site.get("external_id")
    if row is None and external_id:
        ext=_find_external(conn,raw,"site",external_id)
        if ext and ext["site_id"]:
            row=conn.execute("SELECT * FROM organization_sites WHERE id=?",(ext["site_id"],)).fetchone()
    city=norm_city(site.get("city"))
    postal=_clean(site.get("postal_code"))
    name=_clean(site.get("site_name") or site.get("name"))
    if row is None and (city or postal or name):
        row=conn.execute(
            """
            SELECT * FROM organization_sites
            WHERE organization_id=?
              AND COALESCE(postal_code,'')=COALESCE(?,'')
              AND COALESCE(city,'')=COALESCE(?,'')
              AND COALESCE(site_name,'')=COALESCE(?,'')
            LIMIT 1
            """,
            (organization_id,postal,city,name),
        ).fetchone()
    now=utc_now()
    conf=_confidence(site)
    if row is None and not any([city,postal,name,external_id,site.get("address_line1")]):
        return None,None
    if row is None:
        rcvo_id=new_id("SITE")
        cur=conn.execute(
            """
            INSERT INTO organization_sites(
                rcvo_id,organization_id,site_name,address_line1,address_line2,
                postal_code,city,country_code,website_url,status,confidence,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rcvo_id,organization_id,name,_clean(site.get("address_line1")),
                _clean(site.get("address_line2")),postal,city,
                (_clean(site.get("country_code")) or "FR").upper(),
                _clean(site.get("website_url")),"active",conf,now,now,
            ),
        )
        site_id=int(cur.lastrowid)
    else:
        site_id=int(row["id"]); rcvo_id=row["rcvo_id"]
        overrides=set(payload.get("site_override_fields") or [])
        updates={}
        candidates={
            "site_name":name,"address_line1":_clean(site.get("address_line1")),
            "address_line2":_clean(site.get("address_line2")),"postal_code":postal,
            "city":city,"website_url":_clean(site.get("website_url")),
        }
        for field,value in candidates.items():
            if value is not None and (row[field] is None or field in overrides):
                updates[field]=value
        if conf>float(row["confidence"] or 0):
            updates["confidence"]=conf
        if updates:
            updates["updated_at"]=now
            conn.execute(
                f"UPDATE organization_sites SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(),site_id),
            )
    _link_external(conn,raw,"site",external_id,organization_id=organization_id,site_id=site_id)
    for field,observed,normalized_value in [
        ("site_name",name,name),("city",site.get("city"),city),
        ("postal_code",site.get("postal_code"),postal),
    ]:
        _observe(conn,raw,"site",site_id,field,observed,normalized_value,conf)
    return site_id,rcvo_id

def _find_contact(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
    email_norm: str | None,
) -> tuple[sqlite3.Row | None,str]:
    explicit=_clean(payload.get("contact_match_rcvo_id"))
    if explicit:
        row=conn.execute("SELECT * FROM contacts WHERE rcvo_id=?",(explicit,)).fetchone()
        if not row:
            raise ValueError(f"contact_match_rcvo_id not found: {explicit}")
        if email_norm:
            owner=conn.execute(
                "SELECT contact_id FROM contact_emails WHERE email_norm=?",(email_norm,)
            ).fetchone()
            if owner and int(owner["contact_id"])!=int(row["id"]):
                raise ValueError("explicit contact conflicts with existing email owner")
        return row,"explicit_rcvo_id"
    contact=payload.get("contact") or {}
    if contact.get("external_id"):
        ext=_find_external(conn,raw,"contact",contact.get("external_id"))
        if ext and ext["contact_id"]:
            row=conn.execute("SELECT * FROM contacts WHERE id=?",(ext["contact_id"],)).fetchone()
            if row:
                return row,"external_id"
    if email_norm:
        row=conn.execute(
            """
            SELECT c.* FROM contacts c
            JOIN contact_emails e ON e.contact_id=c.id
            WHERE e.email_norm=?
            """,
            (email_norm,),
        ).fetchone()
        if row:
            return row,"exact_email"
    return None,"new"

def _contact(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
) -> tuple[int,str,bool]:
    contact=payload.get("contact") or {}
    email=payload.get("email") or {}
    email_norm=norm_email(email.get("value") or email.get("email"))
    row,method=_find_contact(conn,raw,payload,email_norm)
    now=utc_now()
    conf=_confidence(contact)
    last=norm_last_name(contact.get("last_name") or contact.get("nom"))
    first=norm_first_name(contact.get("first_name") or contact.get("prenom"))
    city=norm_city(contact.get("city") or contact.get("ville"))
    display=_clean(contact.get("display_name")) or _clean(" ".join(x for x in [first,last] if x))
    created=False

    if row is None:
        rcvo_id=new_id("CNT")
        cur=conn.execute(
            """
            INSERT INTO contacts(
                rcvo_id,last_name,first_name,display_name,city,qualification_status,
                vo_relevance,status,confidence,notes,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rcvo_id,last,first,display,city,contact.get("qualification_status","to_enrich"),
                contact.get("vo_relevance","unknown"),"active",conf,_clean(contact.get("notes")),now,now,
            ),
        )
        contact_id=int(cur.lastrowid)
        conn.execute(
            "INSERT INTO prospecting_state(contact_id,status,total_emails_sent,updated_at) VALUES (?, 'never_contacted',0,?)",
            (contact_id,now),
        )
        created=True
        _event(conn,raw,"contact_created",contact_id=contact_id,details={"rcvo_id":rcvo_id})
        conn.execute(
            """
            INSERT INTO match_decisions(
                entity_type,incoming_key,candidate_rcvo_id,decision,method,score,
                source_key,raw_batch_uuid,raw_record_id,decided_at
            ) VALUES ('contact',?,?,'new','new',?,?,?,?,?)
            """,
            (email_norm,rcvo_id,conf,raw["source_key"],raw.get("raw_batch_uuid"),raw.get("raw_record_id"),now),
        )
    else:
        contact_id=int(row["id"]); rcvo_id=row["rcvo_id"]
        overrides=set(payload.get("contact_override_fields") or [])
        updates={}
        candidates={
            "last_name":last,"first_name":first,"display_name":display,"city":city,
            "qualification_status":contact.get("qualification_status"),
            "vo_relevance":contact.get("vo_relevance"),"notes":_clean(contact.get("notes")),
        }
        for field,value in candidates.items():
            if value is not None and (row[field] is None or field in overrides):
                updates[field]=value
        if conf>float(row["confidence"] or 0):
            updates["confidence"]=conf
        if updates:
            updates["updated_at"]=now
            conn.execute(
                f"UPDATE contacts SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(),contact_id),
            )
            _event(conn,raw,"contact_enriched",contact_id=contact_id,details={"fields":list(updates)})
        _event(conn,raw,"contact_matched",contact_id=contact_id,details={"method":method})
        conn.execute(
            """
            INSERT INTO match_decisions(
                entity_type,incoming_key,candidate_rcvo_id,decision,method,score,
                source_key,raw_batch_uuid,raw_record_id,decided_at
            ) VALUES ('contact',?,?,'matched',?,?,?,?,?,?)
            """,
            (email_norm,rcvo_id,method,conf,raw["source_key"],raw.get("raw_batch_uuid"),raw.get("raw_record_id"),now),
        )

    _link_external(conn,raw,"contact",contact.get("external_id"),contact_id=contact_id)
    for field,observed,normalized_value in [
        ("last_name",contact.get("last_name") or contact.get("nom"),last),
        ("first_name",contact.get("first_name") or contact.get("prenom"),first),
        ("city",contact.get("city") or contact.get("ville"),city),
        ("qualification_status",contact.get("qualification_status"),contact.get("qualification_status")),
        ("vo_relevance",contact.get("vo_relevance"),contact.get("vo_relevance")),
    ]:
        _observe(conn,raw,"contact",contact_id,field,observed,normalized_value,conf)
    return contact_id,rcvo_id,created

def _email(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
    contact_id: int,
) -> int | None:
    email=payload.get("email") or {}
    value=_clean(email.get("value") or email.get("email"))
    normalized=norm_email(value)
    if not normalized:
        return None
    now=utc_now()
    conf=_confidence(email)
    row=conn.execute("SELECT * FROM contact_emails WHERE email_norm=?",(normalized,)).fetchone()
    if row and int(row["contact_id"])!=contact_id:
        raise ValueError("email belongs to another contact")
    if row:
        email_id=int(row["id"])
        updates={"last_seen_at":now}
        status=email.get("deliverability_status")
        if status and (row["deliverability_status"]=="unknown" or email.get("override_status")):
            updates["deliverability_status"]=status
        if conf>float(row["ownership_confidence"] or 0):
            updates["ownership_confidence"]=conf
        if email.get("verified_at"):
            updates["verified_at"]=_clean(email.get("verified_at"))
        conn.execute(
            f"UPDATE contact_emails SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
            (*updates.values(),email_id),
        )
        _event(conn,raw,"email_reused",contact_id=contact_id,details={"email":normalized})
    else:
        requested_primary=email.get("is_primary")
        current_primary=conn.execute(
            "SELECT id FROM contact_emails WHERE contact_id=? AND is_primary=1",
            (contact_id,),
        ).fetchone()
        if requested_primary is True:
            primary=1
        elif requested_primary is False:
            primary=0
        else:
            primary=1 if current_primary is None else 0
        if primary:
            conn.execute("UPDATE contact_emails SET is_primary=0 WHERE contact_id=?",(contact_id,))
        cur=conn.execute(
            """
            INSERT INTO contact_emails(
                contact_id,email_raw,email_norm,email_kind,deliverability_status,
                ownership_confidence,is_primary,first_seen_at,last_seen_at,verified_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                contact_id,value,normalized,email.get("kind","unknown"),
                email.get("deliverability_status","unknown"),conf,primary,now,now,
                _clean(email.get("verified_at")),
            ),
        )
        email_id=int(cur.lastrowid)
        _event(conn,raw,"email_added",contact_id=contact_id,details={"email":normalized})
    _link_external(conn,raw,"email",email.get("external_id"),contact_id=contact_id,email_id=email_id)
    for field,observed,normalized_value in [
        ("email",value,normalized),
        ("deliverability_status",email.get("deliverability_status"),email.get("deliverability_status")),
        ("kind",email.get("kind"),email.get("kind")),
    ]:
        _observe(conn,raw,"email",email_id,field,observed,normalized_value,conf)
    return email_id

def _phone(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
    contact_id: int,
) -> int | None:
    phone=payload.get("phone") or {}
    raw_value=_clean(phone.get("value") or phone.get("phone"))
    if not raw_value:
        return None
    normalized=re.sub(r"[^0-9+]","",raw_value)
    now=utc_now(); conf=_confidence(phone)
    row=conn.execute(
        "SELECT * FROM contact_phones WHERE contact_id=? AND phone_norm=?",
        (contact_id,normalized),
    ).fetchone()
    if row:
        phone_id=int(row["id"])
        conn.execute(
            "UPDATE contact_phones SET last_seen_at=?,ownership_confidence=max(ownership_confidence,?) WHERE id=?",
            (now,conf,phone_id),
        )
    else:
        primary=1 if phone.get("is_primary",False) else 0
        if primary:
            conn.execute("UPDATE contact_phones SET is_primary=0 WHERE contact_id=?",(contact_id,))
        cur=conn.execute(
            """
            INSERT INTO contact_phones(
                contact_id,phone_raw,phone_norm,phone_kind,ownership_confidence,
                is_primary,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (contact_id,raw_value,normalized,phone.get("kind","unknown"),conf,primary,now,now),
        )
        phone_id=int(cur.lastrowid)
    _observe(conn,raw,"phone",phone_id,"phone",raw_value,normalized,conf)
    return phone_id

def _employment(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    payload: dict[str,Any],
    contact_id: int,
    organization_id: int | None,
    site_id: int | None,
) -> int | None:
    emp=payload.get("employment") or {}
    if not emp and organization_id is None:
        return None
    now=utc_now(); conf=_confidence(emp)
    title=_clean(emp.get("job_title"))
    title_norm=norm_job(title)
    role=_clean(emp.get("job_role"))
    current=1 if emp.get("is_current",True) else 0

    row=conn.execute(
        """
        SELECT * FROM contact_employments
        WHERE contact_id=?
          AND COALESCE(organization_id,0)=COALESCE(?,0)
          AND COALESCE(site_id,0)=COALESCE(?,0)
          AND COALESCE(job_title_norm,'')=COALESCE(?,'')
          AND is_current=?
        ORDER BY id DESC LIMIT 1
        """,
        (contact_id,organization_id,site_id,title_norm,current),
    ).fetchone()
    if row:
        emp_id=int(row["id"])
        conn.execute(
            """
            UPDATE contact_employments
            SET last_seen_at=?,confidence=max(confidence,?),
                job_role=COALESCE(job_role,?),department=COALESCE(department,?)
            WHERE id=?
            """,
            (now,conf,role,_clean(emp.get("department")),emp_id),
        )
    else:
        if current and emp.get("close_other_current",False):
            others=conn.execute(
                "SELECT id FROM contact_employments WHERE contact_id=? AND is_current=1",
                (contact_id,),
            ).fetchall()
            for old in others:
                conn.execute(
                    """
                    UPDATE contact_employments
                    SET is_current=0,valid_to=COALESCE(valid_to,?),last_seen_at=?
                    WHERE id=?
                    """,
                    (_clean(emp.get("valid_from")) or now,now,old["id"]),
                )
                _event(conn,raw,"employment_closed",contact_id=contact_id,organization_id=organization_id,details={"employment_id":old["id"]})
        cur=conn.execute(
            """
            INSERT INTO contact_employments(
                contact_id,organization_id,site_id,job_title,job_title_norm,job_role,
                department,is_current,confidence,valid_from,valid_to,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                contact_id,organization_id,site_id,title,title_norm,role,_clean(emp.get("department")),
                current,conf,_clean(emp.get("valid_from")),_clean(emp.get("valid_to")),now,now,
            ),
        )
        emp_id=int(cur.lastrowid)
        _event(conn,raw,"employment_added",contact_id=contact_id,organization_id=organization_id,details={"employment_id":emp_id})
    _link_external(conn,raw,"employment",emp.get("external_id"),contact_id=contact_id,organization_id=organization_id,site_id=site_id,employment_id=emp_id)
    for field,observed,normalized_value in [
        ("job_title",title,title_norm),("job_role",role,role),
        ("organization_id",organization_id,organization_id),("is_current",current,current),
    ]:
        _observe(conn,raw,"employment",emp_id,field,observed,normalized_value,conf)
    return emp_id

def _prepare_raw(payload: dict[str,Any]) -> dict[str,Any]:
    raw=dict(payload.get("raw_ref") or {})
    required=["source_key","raw_batch_uuid","raw_record_id"]
    missing=[x for x in required if raw.get(x) in (None,"")]
    if missing:
        raise ValueError("raw_ref missing: "+", ".join(missing))
    raw["raw_record_id"]=int(raw["raw_record_id"])
    return raw

def _promotion_rejection(
    conn: sqlite3.Connection,
    raw: dict[str,Any],
    error: Exception | str,
) -> None:
    conn.execute(
        """
        INSERT INTO promotion_events(
            source_key,raw_batch_uuid,raw_record_id,event_type,occurred_at,details_json
        ) VALUES (?,?,?,?,?,?)
        """,
        (
            raw.get("source_key","unknown"),raw.get("raw_batch_uuid"),
            raw.get("raw_record_id"),"promotion_rejected",utc_now(),
            _json({"error":str(error)}),
        ),
    )

def _promote_on_conn(
    conn: sqlite3.Connection,
    payload: dict[str,Any],
) -> dict[str,Any]:
    raw=_prepare_raw(payload)
    receipt=conn.execute(
        """
        SELECT r.*,c.rcvo_id AS contact_rcvo_id,o.rcvo_id AS organization_rcvo_id
        FROM promotion_receipts r
        LEFT JOIN contacts c ON c.id=r.contact_id
        LEFT JOIN organizations o ON o.id=r.organization_id
        WHERE r.source_key=? AND r.raw_batch_uuid=? AND r.raw_record_id=?
        """,
        (raw["source_key"],raw["raw_batch_uuid"],raw["raw_record_id"]),
    ).fetchone()
    if receipt:
        return {
            "idempotent":True,
            "status":receipt["result_status"],
            "contact_rcvo_id":receipt["contact_rcvo_id"],
            "organization_rcvo_id":receipt["organization_rcvo_id"],
        }

    _event(conn,raw,"promotion_started",details={"source_record_id":raw.get("source_record_id")})
    org_id,org_rcvo,org_created=_organization(conn,raw,payload)
    site_id,site_rcvo=_site(conn,raw,payload,org_id)
    contact_id,contact_rcvo,contact_created=_contact(conn,raw,payload)
    _email(conn,raw,payload,contact_id)
    _phone(conn,raw,payload,contact_id)
    _employment(conn,raw,payload,contact_id,org_id,site_id)

    status="created" if (contact_created or org_created) else "enriched"
    conn.execute(
        """
        INSERT INTO promotion_receipts(
            source_key,raw_batch_uuid,raw_record_id,source_record_id,promoted_at,
            result_status,contact_id,organization_id,details_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            raw["source_key"],raw["raw_batch_uuid"],raw["raw_record_id"],
            _clean(raw.get("source_record_id")),utc_now(),status,contact_id,org_id,
            _json({"site_rcvo_id":site_rcvo}),
        ),
    )
    _event(
        conn,raw,"promotion_completed",
        contact_id=contact_id,organization_id=org_id,details={"status":status}
    )
    return {
        "idempotent":False,
        "status":status,
        "contact_rcvo_id":contact_rcvo,
        "organization_rcvo_id":org_rcvo,
        "site_rcvo_id":site_rcvo,
    }

def promote(path: Path,payload: dict[str,Any]) -> dict[str,Any]:
    init_db(path)
    raw=None
    conn=connect(path)
    try:
        raw=_prepare_raw(payload)
        conn.execute("BEGIN IMMEDIATE")
        result=_promote_on_conn(conn,payload)
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        if raw is None:
            try:
                raw=_prepare_raw(payload)
            except Exception:
                raw={"source_key":"unknown","raw_batch_uuid":None,"raw_record_id":None}
        try:
            conn.execute("BEGIN IMMEDIATE")
            _promotion_rejection(conn,raw,exc)
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        conn.close()

def promote_many(
    path: Path,
    payloads,
    *,
    batch_size: int=500,
    continue_on_error: bool=True,
) -> dict[str,Any]:
    if batch_size<1:
        raise ValueError("batch_size must be >= 1")
    init_db(path)
    conn=connect(path)
    total=created=enriched=idempotent=failed=0
    errors=[]
    results=[]
    in_batch=0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for index,payload in enumerate(payloads,start=1):
            total+=1
            conn.execute("SAVEPOINT rcvo_item")
            raw=None
            try:
                raw=_prepare_raw(payload)
                result=_promote_on_conn(conn,payload)
                conn.execute("RELEASE SAVEPOINT rcvo_item")
                results.append({"ok":True,**result})
                if result.get("idempotent"):
                    idempotent+=1
                elif result.get("status")=="created":
                    created+=1
                else:
                    enriched+=1
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT rcvo_item")
                conn.execute("RELEASE SAVEPOINT rcvo_item")
                failed+=1
                if raw is None:
                    try:
                        raw=_prepare_raw(payload)
                    except Exception:
                        raw={"source_key":"unknown","raw_batch_uuid":None,"raw_record_id":None}
                try:
                    _promotion_rejection(conn,raw,exc)
                except Exception:
                    pass
                errors.append({"index":index,"error":str(exc)})
                results.append({"ok":False,"error":str(exc)})
                if not continue_on_error:
                    conn.rollback()
                    raise
            in_batch+=1
            if in_batch>=batch_size:
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                in_batch=0
        conn.commit()
        return {
            "total":total,
            "created":created,
            "enriched":enriched,
            "idempotent":idempotent,
            "failed":failed,
            "errors":errors[:100],
            "results":results,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

def add_suppression(
    path: Path,
    scope_type: str,
    scope_value: str,
    reason: str,
    *,
    permanent: bool=False,
    source: str="manual",
    notes: str | None=None,
) -> int:
    init_db(path)
    value=_clean(scope_value)
    if not value:
        raise ValueError("scope_value required")
    if scope_type in {"email","domain"}:
        value=value.lower()
    now=utc_now()
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO suppressions(
                scope_type,scope_value,reason,active,permanent,starts_at,source,notes
            ) VALUES (?,?,?,1,?,?,?,?)
            ON CONFLICT(scope_type,scope_value,reason) DO UPDATE SET
                active=1,permanent=excluded.permanent,starts_at=excluded.starts_at,
                ends_at=NULL,source=excluded.source,notes=excluded.notes
            """,
            (scope_type,value,reason,1 if permanent else 0,now,source,notes),
        )
        row=conn.execute(
            "SELECT id FROM suppressions WHERE scope_type=? AND scope_value=? AND reason=?",
            (scope_type,value,reason),
        ).fetchone()
        sid=int(row["id"])
        conn.execute(
            "INSERT INTO suppression_events(suppression_id,event_type,occurred_at,actor,details_json) VALUES (?, 'added', ?, ?, ?)",
            (sid,now,source,_json({"reason":reason})),
        )
        return sid

def record_prospecting_event(
    path: Path,
    contact_rcvo_id: str,
    event_type: str,
    occurred_at: str,
    *,
    campaign_rcvo_id: str | None=None,
    sender_mailbox: str | None=None,
    message_key: str | None=None,
    provider_message_id: str | None=None,
    result: str | None=None,
    metadata: Any=None,
    next_eligible_at: str | None=None,
) -> int:
    init_db(path)
    with connect(path) as conn:
        contact=conn.execute("SELECT id FROM contacts WHERE rcvo_id=?",(contact_rcvo_id,)).fetchone()
        if not contact:
            raise ValueError("contact not found")
        campaign_id=None
        if campaign_rcvo_id:
            row=conn.execute("SELECT id FROM campaigns WHERE rcvo_id=?",(campaign_rcvo_id,)).fetchone()
            if not row:
                raise ValueError("campaign not found")
            campaign_id=int(row["id"])
        cur=conn.execute(
            """
            INSERT INTO prospecting_events(
                contact_id,campaign_id,event_type,occurred_at,sender_mailbox,message_key,
                provider_message_id,result,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                contact["id"],campaign_id,event_type,occurred_at,sender_mailbox,message_key,
                provider_message_id,result,_json(metadata),
            ),
        )
        if event_type in {"email_sent","followup_sent"}:
            conn.execute(
                """
                UPDATE prospecting_state
                SET status='contacted',
                    total_emails_sent=total_emails_sent+1,
                    first_contact_at=COALESCE(first_contact_at,?),
                    last_contact_at=?,
                    next_eligible_at=?,
                    last_campaign_id=COALESCE(?,last_campaign_id),
                    updated_at=?
                WHERE contact_id=?
                """,
                (occurred_at,occurred_at,next_eligible_at,campaign_id,utc_now(),contact["id"]),
            )
        elif event_type=="reply_received":
            conn.execute(
                "UPDATE prospecting_state SET status='responded',updated_at=? WHERE contact_id=?",
                (utc_now(),contact["id"]),
            )
        elif event_type in {"optout","bounce"}:
            reason="optout" if event_type=="optout" else "hard_bounce"
            email=conn.execute(
                "SELECT email_norm FROM contact_emails WHERE contact_id=? AND is_primary=1",
                (contact["id"],),
            ).fetchone()
            if email:
                conn.execute(
                    """
                    INSERT INTO suppressions(
                        scope_type,scope_value,reason,active,permanent,starts_at,source
                    ) VALUES ('email',?,?,1,1,?,'prospecting_event')
                    ON CONFLICT(scope_type,scope_value,reason) DO UPDATE SET
                        active=1,permanent=1,starts_at=excluded.starts_at,ends_at=NULL
                    """,
                    (email["email_norm"],reason,occurred_at),
                )
            conn.execute(
                "UPDATE prospecting_state SET status='suppressed',updated_at=? WHERE contact_id=?",
                (utc_now(),contact["id"]),
            )
        return int(cur.lastrowid)

def stats(path: Path) -> dict[str,int]:
    init_db(path)
    with connect(path) as conn:
        tables=["organizations","organization_sites","contacts","contact_emails","contact_employments","source_observations","promotion_receipts","prospecting_events","suppressions"]
        return {t:int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in tables}
