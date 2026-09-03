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

ADMIN_USER = "admin"
ADMIN_PASS = "TaxHR-Admin-ChangeMe"


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
        api_mod.DB_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self) -> None:
        api_mod.DB_PATH = self._orig_db
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def _login_admin(self) -> None:
        ver = self.client.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        self.assertEqual(ver.status_code, 200, ver.text)
        self.assertTrue(ver.json()["ok"])

    def _login(self, username: str = "watcher", password: str = "pass1234") -> None:
        self._login_admin()
        created = self.client.post(
            "/api/admin/users",
            json={"username": username, "default_password": password, "auto": False},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.client.post("/api/auth/logout")
        ver = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(ver.status_code, 200, ver.text)
        self.assertTrue(ver.json()["ok"])

    def test_login_watch_and_notify(self) -> None:
        me0 = self.client.get("/api/auth/me")
        self.assertFalse(me0.json()["authenticated"])

        self._login()
        me = self.client.get("/api/auth/me")
        self.assertTrue(me.json()["authenticated"])
        user = me.json()["user"]
        self.assertEqual(user["username"], "watcher")
        self.assertEqual(user["login_method"], "账号密码")
        self.assertTrue(user["must_change_password"])
        self.assertEqual(user["display_name"], "watcher")
        self.assertNotIn("email", user)
        self.assertNotIn("phone", user)

        add = self.client.post(
            "/api/watches",
            json={"target_type": "person", "target_id": "shanghai:关注甲", "label": "关注甲"},
        )
        self.assertEqual(add.status_code, 200)
        self.assertEqual(add.json()["target_id"], "shanghai:关注甲")

        listed = self.client.get("/api/watches")
        self.assertEqual(listed.json()["total"], 1)

        from tax_platform.accounts.schema import connect as acc_connect

        db = acc_connect(self.db_path)
        max_id = db.execute("SELECT MAX(id) FROM appointment_events").fetchone()[0]
        user_id = db.execute(
            "SELECT id FROM users WHERE username = ?", ("watcher",)
        ).fetchone()[0]
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
        db.close()

        out = self.client.get("/api/notify/outbox")
        self.assertEqual(out.status_code, 200)
        self.assertGreaterEqual(len(out.json()["items"]), 1)

        redirect = self.client.get("/watches", follow_redirects=False)
        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(redirect.headers.get("location"), "/account#watches")

        for path in ("/login", "/account", "/invite", "/admin/users"):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_invite_and_change_password(self) -> None:
        self._login_admin()
        created = self.client.post(
            "/api/admin/users",
            json={"username": "guest01", "default_password": "initpass", "auto": False},
        )
        self.assertEqual(created.status_code, 200)
        token = created.json()["invite_token"]
        inv = self.client.get("/api/auth/invite", params={"token": token})
        self.assertEqual(inv.status_code, 200)
        self.assertEqual(inv.json()["username"], "guest01")
        self.assertEqual(inv.json()["default_password"], "initpass")

        self.client.post("/api/auth/logout")
        login = self.client.post(
            "/api/auth/login",
            json={"username": "guest01", "password": "initpass"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["user"]["must_change_password"])

        changed = self.client.post(
            "/api/auth/change-password",
            json={"new_password": "newpass1"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["user"]["must_change_password"])

        nick = self.client.patch("/api/auth/profile", json={"nickname": "测试昵称"})
        self.assertEqual(nick.status_code, 200)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.json()["user"]["nickname"], "测试昵称")

    def test_otp_endpoints_disabled(self) -> None:
        req = self.client.post("/api/auth/request-code", json={"email": "a@b.com"})
        self.assertEqual(req.status_code, 400)

    def test_one_click_auto_user(self) -> None:
        self._login_admin()
        created = self.client.post("/api/admin/users", json={"auto": True})
        self.assertEqual(created.status_code, 200, created.text)
        data = created.json()
        self.assertTrue(data["username"].startswith("taxuser"))
        nick = data["nickname"] or ""
        self.assertTrue(nick.startswith("\u7a0e\u52a1\u7528\u6237"))  # 税务用户
        self.assertRegex(nick, r"^\u7a0e\u52a1\u7528\u6237\d{4}$")
        self.assertEqual(len(data["default_password"]), 12)
        letters = sum(c.isalpha() for c in data["default_password"])
        digits = sum(c.isdigit() for c in data["default_password"])
        self.assertEqual(letters, 6)
        self.assertEqual(digits, 6)
