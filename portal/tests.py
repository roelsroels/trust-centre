import base64
import hashlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyotp

from .app import create_app
from .auth import signature
from .db import initialize, now, one, rows, run

PASSWORD = "temporary-test-password"


class PortalFlows(unittest.TestCase):
    def setUp(self):
        data = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(TESTING=True, LOCAL=True, DATA_DIR=Path(data), SECRET_KEY="fixture-key",
                              SESSION_COOKIE_SECURE=False, TRUSTED_HOSTS=["localhost"])
        with self.app.app_context():
            initialize()
            self.admin = self.account("admin", staff=True)
            self.owner = self.account("owner", staff=True, owner=True)
            self.reader = self.account("reader")
            self.category = one("SELECT id FROM portal_category WHERE name='Reports'")["id"]
            run("""INSERT INTO portal_branding(name,introduction,team,logo_url,accent,ink,font,access_inbox,show_demo_team)
                   VALUES ('Trust Centre','','','','#076e65','#132a42','sans','compliance@example.com',0)""")
        self.client = self.app.test_client()

    def account(self, username, staff=False, owner=False):
        salt = "test-salt"
        legacy = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt.encode(), 4000)
        encoded = f"pbkdf2_sha256$4000${salt}${base64.b64encode(legacy).decode()}"
        identifier = run("""INSERT INTO portal_account(username,email,password,tier,is_staff,is_active,is_superuser,
                            first_name,last_name,date_joined) VALUES (?,?,?,10,?,1,?,'','',?)""",
                         username, f"{username}@example.com", encoded, staff, owner, now()).lastrowid
        if staff:
            key = hashlib.sha256(username.encode()).digest()[:20]
            run("""INSERT INTO otp_totp_totpdevice(name,confirmed,key,step,t0,digits,tolerance,drift,last_t,user_id,
                   throttling_failure_count) VALUES ('Authenticator',1,?,30,0,6,1,0,-1,?,0)""", key.hex(), identifier)
        return identifier

    def post(self, path, data=None, source=None):
        form_page = self.client.get(source or path)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', form_page.data)
        self.assertIsNotNone(match, f"No CSRF field at {source or path}: {form_page.status_code}")
        return self.client.post(path, data={**(data or {}), "csrf_token": match.group(1).decode()})

    def sign_in(self, username, staff=False, target=None):
        path = "/account/login/"
        if target:
            path += f"?next={target}"
        fields = {"username": username, "password": PASSWORD}
        result = self.post(path, fields)
        self.assertEqual(result.status_code, 302, result.data[:2600])
        if staff:
            self.assertTrue(result.location.startswith("/account/authenticator/"))
            challenge = self.client.get(result.location)
            self.assertNotIn(b'name="password"', challenge.data)
            self.assertIn("/account/login/", self.client.get("/manage/").location)
            key = hashlib.sha256(username.encode()).digest()[:20]
            code = pyotp.TOTP(base64.b32encode(key).decode()).now()
            result = self.post(result.location, {"otp": code})
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result.location, target or "/library/")
        return result

    def upload(self):
        response = self.post("/manage/documents/new/", {
            "title": "Audit report", "teaser": "Scope of the audit.", "category_id": self.category,
            "minimum_tier": 30, "retain_previous": 1, "agreement": "Keep this private.", "revision": "1.0",
            "file": (io.BytesIO(b"%PDF-1.4\nreport"), "report.pdf"),
        })
        self.assertEqual(response.status_code, 302, response.data[:2600])
        with self.app.app_context():
            doc = one("SELECT * FROM portal_document")
            revision = one("SELECT * FROM portal_revision")
        return doc["id"], revision["id"]

    def test_upload_publish_agreement_and_access_request(self):
        self.sign_in("admin", staff=True)
        self.assertIn(b"Access requests", self.client.get("/manage/").data)
        doc, rev = self.upload()
        path = f"/documents/{doc}/"
        download = f"{path}files/{rev}/"
        self.assertEqual(self.client.get(path).status_code, 404)
        preview = f"/manage/documents/{doc}/versions/{rev}/preview/"
        with self.client.get(preview) as pdf:
            self.assertEqual(pdf.data, b"%PDF-1.4\nreport")
        publish = f"/manage/documents/{doc}/versions/{rev}/publish/"
        self.assertEqual(self.post(publish).status_code, 302)
        self.post("/account/logout/", source="/")
        self.sign_in("reader")
        self.assertEqual(self.client.get(preview).status_code, 403)
        self.assertIn(b"Expert required", self.client.get(path).data)
        self.assertEqual(self.client.get(download).status_code, 403)
        for _ in range(2):
            self.assertEqual(self.post(path + "request/", source=path).status_code, 302)
        with self.app.app_context():
            self.assertEqual(len(rows("SELECT * FROM portal_outgoingmail")), 1)
            run("UPDATE portal_account SET tier=30 WHERE id=?", self.reader)
        self.assertEqual(self.client.get(download).status_code, 403)
        response = self.client.get(path)
        digest = re.search(rb'name="digest" value="([^"]+)"', response.data).group(1).decode()
        self.assertEqual(self.post(path + "agreement/", {"digest": digest, "agree": "yes"}, source=path).status_code, 302)
        with self.client.get(download) as pdf:
            self.assertEqual(pdf.data, b"%PDF-1.4\nreport")
            self.assertEqual(pdf.headers["Cache-Control"], "no-store")
        with self.app.app_context():
            run("UPDATE portal_document SET agreement='Changed' WHERE id=?", doc)
        self.assertEqual(self.client.get(download).status_code, 403)

    def test_staff_roles_mfa_and_csrf(self):
        self.sign_in("admin", staff=True)
        self.assertEqual(self.client.get("/manage/").status_code, 200)
        self.assertEqual(self.client.get(f"/manage/accounts/{self.owner}/").status_code, 404)
        self.assertEqual(self.client.get(f"/manage/accounts/{self.admin}/").status_code, 404)
        account = f"/manage/accounts/{self.reader}/"
        self.assertEqual(self.client.post(account).status_code, 400)
        self.assertNotIn(b'name="is_staff"', self.client.get(account).data)
        data = {"username": "reader", "email": "reader@example.com", "tier": 20, "is_active": "on", "is_staff": "on"}
        self.assertEqual(self.post(account, data).status_code, 302)
        with self.app.app_context():
            self.assertEqual(one("SELECT is_staff,tier FROM portal_account WHERE id=?", self.reader)["is_staff"], 0)
        self.post("/account/logout/", source="/")
        self.sign_in("owner", staff=True, target="/manage/")
        self.assertIn(b'name="is_staff"', self.client.get(account).data)
        self.assertEqual(self.post(account, data).status_code, 302)
        with self.app.app_context():
            self.assertEqual(one("SELECT is_staff FROM portal_account WHERE id=?", self.reader)["is_staff"], 1)

    def test_existing_admin_session_only_asks_for_authenticator(self):
        with self.app.app_context():
            account = one("SELECT * FROM portal_account WHERE id=?", self.admin)
            with self.client.session_transaction() as cookie:
                cookie.update(account=self.admin, stamp=signature(account), otp=None)
        response = self.client.get("/manage/documents/")
        self.assertIn("/account/authenticator/", response.location)
        self.assertNotIn(b'name="password"', self.client.get(response.location).data)
        key = hashlib.sha256(b"admin").digest()[:20]
        code = pyotp.TOTP(base64.b32encode(key).decode()).now()
        result = self.post(response.location, {"otp": code})
        self.assertEqual(result.location, "/manage/documents/")
        self.assertEqual(self.client.get(result.location).status_code, 200)

    def test_password_reset_uses_smtp_and_invalidates_link_and_session(self):
        stale = self.app.test_client()
        with self.client.get("/account/login/") as login:
            self.assertIn(b"Username", login.data)
        self.sign_in("reader")
        stale.set_cookie("trust_session", self.client.get_cookie("trust_session").value)
        with patch("portal.auth.send") as send:
            result = self.post("/account/reset/", {"email": "reader@example.com"})
            self.assertIn(b"Check your email", result.data)
            self.assertEqual(send.call_count, 1)
        body = send.call_args.args[2]
        link = re.search(r"/account/reset/confirm/[^/]+/", body).group(0)
        password = "replacement-test-password"
        self.assertEqual(self.post(link, {"password": password, "confirmation": password}).status_code, 302)
        self.assertIn(b"expired or already been used", self.client.get(link).data)
        self.assertEqual(stale.get("/notifications/").status_code, 302)
        response = self.post("/account/login/", {"username": "reader", "password": password})
        self.assertEqual(response.status_code, 302)

    def test_csv_confirmation_and_publish_notifications(self):
        self.sign_in("admin", staff=True)
        self.assertEqual(self.post("/manage/subscribers/import/", {
            "file": (io.BytesIO(b"email\nreader@example.com\nREADER@example.com\n"), "customers.csv"),
        }).status_code, 302)
        with self.app.app_context():
            subscriber = one("SELECT * FROM portal_subscriber")
            message = one("SELECT * FROM portal_outgoingmail")
            self.assertEqual(subscriber["status"], "pending")
        link = re.search(r"/subscription/[^/]+/", message["body"]).group(0)
        self.assertEqual(self.post(link, {"action": "confirm"}).status_code, 302)
        doc, rev = self.upload()
        self.assertEqual(self.post(f"/manage/documents/{doc}/versions/{rev}/publish/").status_code, 302)
        with self.app.app_context():
            self.assertEqual(len(rows("SELECT * FROM portal_outgoingmail")), 2)
        self.assertEqual(self.post(link, {"action": "off"}).status_code, 302)
        with patch("portal.services.send") as send:
            runner = self.app.test_cli_runner()
            result = runner.invoke(args=["send_queue"])
            self.assertEqual(result.exit_code, 0, result.output)
            send.assert_not_called()
