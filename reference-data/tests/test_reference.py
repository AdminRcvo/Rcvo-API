import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from reference_engine import (
    add_suppression, connect, init_db, promote, record_prospecting_event, stats
)

def payload(
    raw_id:int,
    email:str|None="jean.dupont@garage-x.fr",
    *,
    source="apollo",
    batch="batch-a",
    contact=None,
    organization=None,
    employment=None,
    **extra,
):
    data={
        "raw_ref":{
            "source_key":source,
            "raw_batch_uuid":batch,
            "raw_record_id":raw_id,
            "source_record_id":f"{source}-{raw_id}",
        },
        "contact":contact or {
            "qualification_status":"usable",
            "vo_relevance":"confirmed",
            "confidence":0.8,
        },
    }
    if email:
        data["email"]={
            "value":email,
            "kind":"personal_business",
            "deliverability_status":"valid",
            "confidence":0.95,
        }
    if organization is not None:
        data["organization"]=organization
    if employment is not None:
        data["employment"]=employment
    data.update(extra)
    return data

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"rcvo-reference.sqlite"
        init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sparse_contact_is_allowed_and_prospectable(self):
        result=promote(self.db,payload(1))
        self.assertEqual(result["status"],"created")
        with connect(self.db) as conn:
            row=conn.execute(
                "SELECT * FROM v_prospectable_contacts WHERE rcvo_id=?",
                (result["contact_rcvo_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["last_name"])
        self.assertIsNone(row["first_name"])

    def test_same_email_enriches_instead_of_duplicate(self):
        first=promote(self.db,payload(1))
        second=promote(
            self.db,
            payload(
                2,
                contact={
                    "last_name":"Dupont",
                    "first_name":"jean",
                    "city":"BORDEAUX",
                    "qualification_status":"qualified",
                    "vo_relevance":"confirmed",
                    "confidence":0.92,
                },
                organization={
                    "display_name":"Garage X SAS",
                    "domain":"https://www.garage-x.fr/",
                    "city":"Bordeaux",
                    "vo_relevance":"confirmed",
                    "confidence":0.9,
                },
                employment={
                    "job_title":"Responsable VO",
                    "job_role":"responsable_vo",
                    "is_current":True,
                    "confidence":0.9,
                },
            ),
        )
        self.assertEqual(first["contact_rcvo_id"],second["contact_rcvo_id"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],1)
            row=conn.execute("SELECT * FROM v_contact_master").fetchone()
            self.assertEqual(row["last_name"],"DUPONT")
            self.assertEqual(row["first_name"],"Jean")
            self.assertEqual(row["organization_domain"],"garage-x.fr")
            self.assertEqual(row["job_role"],"responsable_vo")

    def test_same_domain_reuses_organization_for_second_contact(self):
        a=promote(
            self.db,
            payload(
                1,
                email="a@garage-x.fr",
                organization={"display_name":"Garage X","domain":"garage-x.fr","city":"Dax","confidence":0.8},
            ),
        )
        b=promote(
            self.db,
            payload(
                2,
                email="b@garage-x.fr",
                organization={"display_name":"Garage X DAX","domain":"WWW.GARAGE-X.FR","city":"Dax","confidence":0.9},
            ),
        )
        self.assertEqual(a["organization_rcvo_id"],b["organization_rcvo_id"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM organizations").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],2)

    def test_promotion_receipt_is_idempotent(self):
        data=payload(10)
        first=promote(self.db,data)
        second=promote(self.db,data)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM promotion_receipts").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],1)

    def test_external_id_resolves_contact_without_email(self):
        first=promote(
            self.db,
            payload(
                1,
                email=None,
                contact={
                    "external_id":"apollo-c-42",
                    "last_name":"MARTIN",
                    "qualification_status":"to_enrich",
                    "vo_relevance":"likely",
                    "confidence":0.7,
                },
            ),
        )
        second=promote(
            self.db,
            payload(
                2,
                email=None,
                contact={
                    "external_id":"apollo-c-42",
                    "first_name":"Paul",
                    "qualification_status":"usable",
                    "vo_relevance":"confirmed",
                    "confidence":0.9,
                },
            ),
        )
        self.assertEqual(first["contact_rcvo_id"],second["contact_rcvo_id"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM contacts").fetchone()[0],1)

    def test_explicit_match_can_attach_second_source(self):
        first=promote(
            self.db,
            payload(
                1,
                email="contact@dealer.fr",
                source="apollo",
                contact={"last_name":"DURAND","qualification_status":"usable","vo_relevance":"confirmed","confidence":0.8},
            ),
        )
        second=promote(
            self.db,
            payload(
                2,
                email=None,
                source="hunter",
                batch="batch-h",
                contact={"external_id":"hunter-123","first_name":"Luc","confidence":0.9},
                contact_match_rcvo_id=first["contact_rcvo_id"],
            ),
        )
        self.assertEqual(first["contact_rcvo_id"],second["contact_rcvo_id"])
        with connect(self.db) as conn:
            ext=conn.execute(
                "SELECT count(*) FROM external_identities WHERE source_key='hunter' AND external_id='hunter-123'"
            ).fetchone()[0]
        self.assertEqual(ext,1)

    def test_job_change_preserves_history(self):
        first=promote(
            self.db,
            payload(
                1,
                organization={"display_name":"Garage A","domain":"garage-a.fr","confidence":0.9},
                employment={"job_title":"Vendeur VO","job_role":"vendeur_vo","is_current":True,"confidence":0.9},
            ),
        )
        second_payload=payload(
            2,
            organization={"display_name":"Garage B","domain":"garage-b.fr","confidence":0.9},
            employment={
                "job_title":"Responsable VO","job_role":"responsable_vo",
                "is_current":True,"close_other_current":True,"valid_from":"2026-09-01",
                "confidence":0.95,
            },
        )
        second_payload["contact_match_rcvo_id"]=first["contact_rcvo_id"]
        second=promote(self.db,second_payload)
        self.assertEqual(first["contact_rcvo_id"],second["contact_rcvo_id"])
        with connect(self.db) as conn:
            rows=conn.execute(
                "SELECT job_role,is_current,valid_to FROM contact_employments ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["is_current"],0)
        self.assertEqual(rows[0]["valid_to"],"2026-09-01")
        self.assertEqual(rows[1]["job_role"],"responsable_vo")
        self.assertEqual(rows[1]["is_current"],1)

    def test_suppression_hides_contact(self):
        result=promote(self.db,payload(1))
        with connect(self.db) as conn:
            before=conn.execute("SELECT count(*) FROM v_prospectable_contacts").fetchone()[0]
        self.assertEqual(before,1)
        add_suppression(
            self.db,"email","JEAN.DUPONT@GARAGE-X.FR","optout",permanent=True
        )
        with connect(self.db) as conn:
            after=conn.execute("SELECT count(*) FROM v_prospectable_contacts").fetchone()[0]
        self.assertEqual(after,0)

    def test_prospecting_history_updates_state_and_is_immutable(self):
        result=promote(self.db,payload(1))
        record_prospecting_event(
            self.db,result["contact_rcvo_id"],"email_sent","2026-10-01T10:00:00.000Z",
            sender_mailbox="campagne@rcvo.example",
            message_key="launch-v1",
            next_eligible_at="2026-11-01T10:00:00.000Z",
        )
        with connect(self.db) as conn:
            state=conn.execute(
                "SELECT * FROM prospecting_state WHERE contact_id=(SELECT id FROM contacts WHERE rcvo_id=?)",
                (result["contact_rcvo_id"],),
            ).fetchone()
            event_id=conn.execute("SELECT id FROM prospecting_events").fetchone()[0]
            self.assertEqual(state["total_emails_sent"],1)
            self.assertEqual(state["next_eligible_at"],"2026-11-01T10:00:00.000Z")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM prospecting_events WHERE id=?",(event_id,))

    def test_observations_and_match_decisions_are_immutable(self):
        promote(
            self.db,
            payload(
                1,
                contact={"last_name":"DUPONT","qualification_status":"usable","vo_relevance":"confirmed","confidence":0.9},
            ),
        )
        with connect(self.db) as conn:
            obs=conn.execute("SELECT id FROM source_observations LIMIT 1").fetchone()[0]
            decision=conn.execute("SELECT id FROM match_decisions LIMIT 1").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE source_observations SET field_name='x' WHERE id=?",(obs,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM match_decisions WHERE id=?",(decision,))

    def test_integrity_and_stats(self):
        promote(self.db,payload(1))
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertEqual(len(conn.execute("PRAGMA foreign_key_check").fetchall()),0)
        s=stats(self.db)
        self.assertEqual(s["contacts"],1)
        self.assertEqual(s["contact_emails"],1)

if __name__=="__main__":
    unittest.main()
