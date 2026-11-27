import base64
import hashlib
import io
import re
import smtplib
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

import pyotp

from .app import create_app
from .auth import signature
from .db import initialize, now, one, rows, run
from .email_content import branded_email
from .services import send

PASSWORD = "temporary-test-password"
send_message = smtplib.SMTP.send_message


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

    def test_smtp_message_wire_format(self):
        self.app.config.update(MAIL_CONSOLE=False, SMTP_SSL=False,
                               FROM_EMAIL="Équipe conformité <trust@example.com>")
        subject = ("Révision disponible " * 15).rstrip()
        body = "Résumé : accès à la dernière version. " * 40
        with self.app.app_context(), patch("portal.services.smtplib.SMTP") as smtp:
            relay = smtp.return_value.__enter__.return_value
            relay.has_extn.return_value = False
            relay.send_message.side_effect = lambda *args, **kwargs: send_message(relay, *args, **kwargs)
            send("reader@bücher.example", subject, body)
            send("reader@bücher.example", "Document updated", "Example body")
            first, second = (call.args for call in relay.sendmail.call_args_list)

        envelope_from, recipients, wire = first[:3]
        self.assertEqual(envelope_from, "trust@example.com")
        self.assertEqual(recipients, ["reader@xn--bcher-kva.example"])
        self.assertTrue(wire.isascii())
        self.assertNotRegex(wire, rb"(?<!\r)\n|\r(?!\n)")
        headers, payload = wire.split(b"\r\n\r\n", 1)
        self.assertTrue(all(len(line) <= 78 for line in headers.split(b"\r\n")))
        self.assertTrue(all(len(line) <= 998 for line in payload.split(b"\r\n")))
        parser = BytesParser(policy=policy.default)
        message = parser.parsebytes(wire)
        self.assertFalse(message.defects)
        self.assertEqual(parsedate_to_datetime(message["Date"]).utcoffset().total_seconds(), 0)
        self.assertRegex(message["Message-ID"], r"^<[^@]+@[^>]+>$")
        self.assertNotEqual(message["Message-ID"], parser.parsebytes(second[2])["Message-ID"])
        self.assertEqual(message["From"].addresses[0].display_name, "Équipe conformité")
        self.assertEqual(message["Subject"], subject)
        self.assertEqual(message["MIME-Version"], "1.0")
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        plain, html = message.iter_parts()
        self.assertEqual(plain.get_content_type(), "text/plain")
        self.assertEqual(html.get_content_type(), "text/html")
        for part in (plain, html):
            self.assertEqual(part.get_content_charset(), "utf-8")
            self.assertEqual(part["Content-Transfer-Encoding"], "base64")
            self.assertTrue(all(len(line) <= 76 for line in part.get_payload().splitlines()))
        self.assertEqual(plain.get_content().rstrip("\r\n"), body)
        self.assertIn("Trust Centre", html.get_content())
        self.assertNotIn("<img", html.get_content())

    def test_email_uses_configured_logo_and_safe_action_links(self):
        self.app.config.update(MAIL_CONSOLE=False, SMTP_SSL=False, BASE_URL="https://trust.example.com")
        link = "https://trust.example.com/documents/1/"
        stop = "https://trust.example.com/subscription/token/"
        with self.app.app_context(), patch("portal.services.smtplib.SMTP") as smtp:
            run("UPDATE portal_branding SET logo_url=?,name=?", "https://example.com/logo.png", "Alder & Quay")
            send("reader@example.com", "New report", f"<script>alert(1)</script>\n\n"
                 f"Open in the Trust Centre: {link}\n\nUnsubscribe: {stop}")
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn('src="https://example.com/logo.png"', html)
        self.assertIn("Alder &amp; Quay", html)
        self.assertIn(f'href="{link}"', html)
        self.assertIn(f'href="{stop}"', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_email_follows_active_colours_and_fonts(self):
        with self.app.app_context():
            run("UPDATE portal_branding SET accent='#F6B67C',ink='#17234A',font='serif'")
            email = branded_email("New report", "Example body")
            self.assertIn("background:#17234A", email)
            self.assertIn("font-family:Georgia", email)
            self.assertNotIn("@font-face", email)
            run("""INSERT INTO trust_font(key,name,regular,medium,italic,medium_italic)
                   VALUES ('example','Example','https://fonts.example.com/regular.woff2',
                           'https://fonts.example.com/medium.woff2','','')""")
            run("UPDATE portal_branding SET font='example'")
            email = branded_email("New report", "Example body")
            self.assertIn("@font-face", email)
            self.assertIn("https://fonts.example.com/medium.woff2", email)
            self.assertIn("Portal Webfont", email)

    def test_smtp_negotiates_international_mailboxes(self):
        self.app.config.update(MAIL_CONSOLE=False, SMTP_SSL=False)
        with self.app.app_context(), patch("portal.services.smtplib.SMTP") as smtp:
            relay = smtp.return_value.__enter__.return_value
            relay.has_extn.side_effect = lambda name: name.lower() == "smtputf8"
            relay.send_message.side_effect = lambda *args, **kwargs: send_message(relay, *args, **kwargs)
            send("δοκιμή@example.com", "Test", "Example body")
            self.assertIn("SMTPUTF8", relay.sendmail.call_args.args[3])
            relay.has_extn.side_effect = None
            relay.has_extn.return_value = False
            with self.assertRaises(smtplib.SMTPNotSupportedError):
                send("δοκιμή@example.com", "Test", "Example body")
            self.assertEqual(relay.sendmail.call_count, 1)

    def test_smtp_rejects_invalid_envelope_addresses(self):
        self.app.config.update(MAIL_CONSOLE=False, SMTP_SSL=False, FROM_EMAIL="trust")
        with self.app.app_context(), patch("portal.services.smtplib.SMTP") as smtp:
            with self.assertRaises(ValueError):
                send("reader@example.com", "Test", "Example body")
            smtp.assert_not_called()

    def test_account_requests_and_import_require_approval_before_setup(self):
        self.assertIn(b"Request an account", self.client.get("/account/login/").data)
        path = "/account/request/"
        email = "new-customer@example.com"
        self.assertIn(b"Enter a customer or company name", self.post(path, {"email": email}).data)
        self.assertEqual(self.post(path, {"email": email, "company": "Example Company"}).status_code, 302)
        self.assertEqual(self.post(path, {"email": email.upper(), "company": "Duplicate"}).status_code, 302)
        self.assertIn("/account/login/", self.client.get("/manage/accounts/requests/").location)
        self.sign_in("admin", staff=True)
        file = b"email,company,tier\nimported@example.com,Imported Company,Pro\nreader@example.com,Existing,Expert\n"
        import_result = self.post("/manage/accounts/import/", {"file": (io.BytesIO(file), "customers.csv")})
        self.assertEqual(import_result.status_code, 302)
        review = "/manage/accounts/requests/"
        page = self.client.get(review)
        self.assertIn(b"Example Company", page.data)
        self.assertIn(b"Imported Company", page.data)
        with self.app.app_context():
            pending = rows("SELECT * FROM trust_account_request ORDER BY id")
            self.assertEqual(len(pending), 2)
            self.assertEqual(pending[1]["tier"], 20)
            self.assertIsNone(one("SELECT id FROM portal_account WHERE email=?", email))
            self.assertFalse(rows("SELECT id FROM portal_outgoingmail"))
        ids = [entry["id"] for entry in pending]
        result = self.post(review + "review/", {"selected": ids, "action": "approve"}, source=review)
        self.assertEqual(result.status_code, 302)
        with self.app.app_context():
            customer = one("SELECT * FROM portal_account WHERE email=?", email)
            self.assertTrue(customer["password"].startswith("!setup:"))
            self.assertEqual(customer["is_staff"], 0)
            self.assertEqual(len(rows("SELECT id FROM portal_outgoingmail WHERE setup_account_id IS NOT NULL")), 2)
        self.post(review + "review/", {"selected": ids, "action": "approve"}, source=review)
        self.post("/account/logout/", source="/")
        denied = self.post("/account/login/", {"username": customer["username"], "password": PASSWORD})
        self.assertIn(b"Check your username and password", denied.data)
        with patch("portal.auth.send") as send:
            result = self.app.test_cli_runner().invoke(args=["send_queue"])
            self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(send.call_count, 2)
        message = next(call.args for call in send.call_args_list if call.args[0] == email)
        self.assertIn(customer["username"], message[2])
        link = re.search(r"/account/reset/confirm/[^/]+/", message[2]).group(0)
        password = "initial-customer-password"
        self.assertEqual(self.post(link, {"password": password, "confirmation": password}).status_code, 302)
        self.assertIn(b"expired or already been used", self.client.get(link).data)
        signed_in = self.post("/account/login/", {"username": customer["username"], "password": password})
        self.assertEqual(signed_in.location, "/library/")

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
