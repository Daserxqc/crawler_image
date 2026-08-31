from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from tax_platform.accounts.notify import run_notify_cycle
from tax_platform.store.schema import connect
import tax_platform.web.app as api_mod
from tax_platform.web.app import app


class AccountsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "accounts.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on)
            VALUES ('shanghai', '任免', 'https://example.com/n1', '2025-01-01')
            """
        )
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '关注甲', 'appoint', '货劳处', '处长',
                      '2025-01-01', '任免', 'https://example.com/n1', '关注甲任货劳处处长')
            """,
            (nid,),
        )
        self.conn.commit()
        self._orig_db = api_mod.DB_PATH
        self._orig_dev = os.environ.get("TAX_HR_DEV_LOGIN")
        os.environ["TAX_HR_DEV_LOGIN"] = "1"
        api_mod.DB_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self) -> None:
        api_mod.DB_PATH = self._orig_db
        if self._orig_dev is None:
            os.environ.pop("TAX_HR_DEV_LOGIN", None)
        else:
            os.environ["TAX_HR_DEV_LOGIN"] = self._orig_dev
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def _login(self, email: str = "watcher@example.com") -> None:
        req = self.client.post("/api/auth/request-code", json={"email": email})
        self.assertEqual(req.status_code, 200)
        code = req.json()["dev_code"]
        ver = self.client.post("/api/auth/verify", json={"email": email, "code": code})
        self.assertEqual(ver.status_code, 200)
        self.assertTrue(ver.json()["ok"])

    def test_login_watch_and_notify(self) -> None:
        me0 = self.client.get("/api/auth/me")
        self.assertFalse(me0.json()["authenticated"])

        self._login()
        me = self.client.get("/api/auth/me")
        self.assertTrue(me.json()["authenticated"])
        self.assertEqual(me.json()["user"]["email"], "watcher@example.com")
        self.assertEqual(me.json()["user"]["account"], "watcher@example.com")

        add = self.client.post(
            "/api/watches",
            json={"target_type": "person", "target_id": "shanghai:关注甲", "label": "关注甲"},
        )
        self.assertEqual(add.status_code, 200)
        self.assertEqual(add.json()["target_id"], "shanghai:关注甲")

        listed = self.client.get("/api/watches")
        self.assertEqual(listed.json()["total"], 1)

        # Seed cursor then insert a newer event that should match.
        from tax_platform.accounts.schema import connect as acc_connect

        db = acc_connect(self.db_path)
        max_id = db.execute("SELECT MAX(id) FROM appointment_events").fetchone()[0]
        user_id = db.execute("SELECT id FROM users").fetchone()[0]
        db.execute(
            """
            INSERT INTO notify_cursor (user_id, last_event_id, updated_at)
            VALUES (?, ?, '2025-01-01T00:00:00+00:00')
            ON CONFLICT(user_id) DO UPDATE SET last_event_id = excluded.last_event_id
            """,
            (user_id, max_id),
        )
        db.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on)
            VALUES ('shanghai', '新任免', 'https://example.com/n2', '2025-06-01')
            """
        )
        nid2 = db.execute("SELECT id FROM notices WHERE source_url LIKE '%n2'").fetchone()[0]
        db.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '关注甲', 'dismiss', '货劳处', '处长',
                      '2025-06-01', '免职', 'https://example.com/n2', '免去关注甲的货劳处处长职务')
            """,
            (nid2,),
        )
        db.commit()
        result = run_notify_cycle(dry_run=True, conn=db)
        self.assertEqual(result["digests_queued"], 1)
        self.assertEqual(result["events_matched"], 1)
        outbox = db.execute("SELECT status, subject FROM email_outbox").fetchone()
        self.assertIsNotNone(outbox)
        self.assertIn("新变动", outbox["subject"])
        db.close()

        out = self.client.get("/api/notify/outbox")
        self.assertGreaterEqual(len(out.json()["items"]), 1)

        pages = [
            self.client.get("/login"),
            self.client.get("/watches"),
            self.client.get("/anomalies"),
        ]
        for page in pages:
            self.assertEqual(page.status_code, 200)

    def test_gmail_and_phone_login(self) -> None:
        email = "x15162608130@gmail.com"
        req = self.client.post(
            "/api/auth/request-code",
            json={"channel": "email", "account": email},
        )
        self.assertEqual(req.status_code, 200, req.text)
        self.assertEqual(req.json()["account"], email.lower())
        code = req.json()["dev_code"]
        ver = self.client.post(
            "/api/auth/verify",
            json={"channel": "email", "account": email, "code": code},
        )
        self.assertEqual(ver.status_code, 200, ver.text)

        self.client.post("/api/auth/logout")
        phone = "13800138000"
        req2 = self.client.post(
            "/api/auth/request-code",
            json={"channel": "phone", "account": phone},
        )
        self.assertEqual(req2.status_code, 200, req2.text)
        self.assertEqual(req2.json()["phone"], phone)
        ver2 = self.client.post(
            "/api/auth/verify",
            json={"channel": "phone", "account": phone, "code": req2.json()["dev_code"]},
        )
        self.assertEqual(ver2.status_code, 200, ver2.text)
        me = self.client.get("/api/auth/me")
        self.assertTrue(me.json()["authenticated"])
        self.assertEqual(me.json()["user"]["phone"], phone)
        self.assertEqual(me.json()["user"]["channel"], "phone")

        # Anomalies requires login; unauthenticated redirects.
        self.client.post("/api/auth/logout")
        anon = self.client.get("/anomalies", follow_redirects=False)
        self.assertIn(anon.status_code, {302, 307})
