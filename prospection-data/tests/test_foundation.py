import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rcvo_data import init_databases, ingest_iterable, connect

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.raw_path, self.ref_path = init_databases(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_raw_accepts_arbitrary_and_duplicate_records(self):
        rows = [
            {"email": "a@example.fr", "company": "Garage A", "x": "1"},
            {"email": "a@example.fr", "company": "Garage A", "x": "1"},
            {"unknown_field": "kept", "nested": {"ok": True}},
        ]
        result = ingest_iterable(
            self.raw_path,
            rows,
            source_key="test-source",
            import_format="jsonl",
            batch_size=2,
        )
        self.assertEqual(result["rows"], 3)
        with connect(self.raw_path) as conn:
            count = conn.execute("SELECT count(*) FROM raw_records").fetchone()[0]
            payloads = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM raw_records ORDER BY id")]
        self.assertEqual(count, 3)
        self.assertEqual(payloads[0], rows[0])
        self.assertEqual(payloads[1], rows[1])
        self.assertEqual(payloads[2], rows[2])

    def test_reference_allows_incomplete_but_usable_contact(self):
        with connect(self.ref_path) as conn:
            conn.execute(
                "INSERT INTO contacts(rcvo_id, qualification_status, vo_relevance) VALUES ('CNT-TEST-1', 'usable', 'likely')"
            )
            contact_id = conn.execute("SELECT id FROM contacts WHERE rcvo_id='CNT-TEST-1'").fetchone()[0]
            conn.execute(
                "INSERT INTO contact_emails(contact_id,email_raw,email_norm,deliverability_status) VALUES (?, 'AUTO@GARAGE.FR', 'auto@garage.fr', 'unknown')",
                (contact_id,),
            )
            row = conn.execute(
                "SELECT contact_rcvo_id,email FROM v_prospectable_contacts WHERE contact_id=?",
                (contact_id,),
            ).fetchone()
        self.assertEqual(row, ("CNT-TEST-1", "auto@garage.fr"))

    def test_suppression_removes_contact_from_prospectable_view(self):
        with connect(self.ref_path) as conn:
            conn.execute(
                "INSERT INTO contacts(rcvo_id, qualification_status, vo_relevance) VALUES ('CNT-TEST-2', 'qualified', 'confirmed')"
            )
            contact_id = conn.execute("SELECT id FROM contacts WHERE rcvo_id='CNT-TEST-2'").fetchone()[0]
            conn.execute(
                "INSERT INTO contact_emails(contact_id,email_raw,email_norm,deliverability_status) VALUES (?, 'person@dealer.fr', 'person@dealer.fr', 'valid')",
                (contact_id,),
            )
            before = conn.execute(
                "SELECT count(*) FROM v_prospectable_contacts WHERE contact_id=?",
                (contact_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO suppressions(scope_type,scope_value,reason,permanent) VALUES ('email','person@dealer.fr','optout',1)"
            )
            after = conn.execute(
                "SELECT count(*) FROM v_prospectable_contacts WHERE contact_id=?",
                (contact_id,),
            ).fetchone()[0]
        self.assertEqual(before, 1)
        self.assertEqual(after, 0)

    def test_prospecting_history_is_append_only(self):
        with connect(self.ref_path) as conn:
            conn.execute(
                "INSERT INTO contacts(rcvo_id, qualification_status, vo_relevance) VALUES ('CNT-TEST-3', 'usable', 'likely')"
            )
            contact_id = conn.execute("SELECT id FROM contacts WHERE rcvo_id='CNT-TEST-3'").fetchone()[0]
            conn.execute(
                "INSERT INTO prospecting_events(contact_id,event_type,occurred_at) VALUES (?, 'email_sent', '2026-10-01T10:00:00.000Z')",
                (contact_id,),
            )
            event_id = conn.execute("SELECT id FROM prospecting_events").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE prospecting_events SET event_type='manual_note' WHERE id=?", (event_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM prospecting_events WHERE id=?", (event_id,))

    def test_same_email_cannot_be_sent_to_duplicate_contact_records(self):
        with connect(self.ref_path) as conn:
            conn.execute("INSERT INTO contacts(rcvo_id) VALUES ('CNT-A')")
            conn.execute("INSERT INTO contacts(rcvo_id) VALUES ('CNT-B')")
            a = conn.execute("SELECT id FROM contacts WHERE rcvo_id='CNT-A'").fetchone()[0]
            b = conn.execute("SELECT id FROM contacts WHERE rcvo_id='CNT-B'").fetchone()[0]
            conn.execute(
                "INSERT INTO contact_emails(contact_id,email_raw,email_norm) VALUES (?,?,?)",
                (a, "same@garage.fr", "same@garage.fr"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO contact_emails(contact_id,email_raw,email_norm) VALUES (?,?,?)",
                    (b, "SAME@GARAGE.FR", "same@garage.fr"),
                )

if __name__ == "__main__":
    unittest.main()
