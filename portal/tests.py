import base64
import hashlib
import io
import re
import smtplib
import tempfile
import time
import unittest
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pyotp
from joserfc import jwt
from joserfc.jwk import RSAKey

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

    def test_sso_settings_owner_only_and_existing_account_link(self):
        settings = "/manage/settings/sso/"
        login = "/account/login/"
        connection = "/account/sso/connection/"
        start = "/account/sso/start/"
        issuer = "https://identity.example.com"
        key = RSAKey.generate_key(2048, private=True, parameters={"kid": "fixture"})
        provider = {name: issuer + path for name, path in {
            "authorization_endpoint": "/authorize", "token_endpoint": "/token", "jwks_uri": "/keys",
        }.items()}
        self.app.config.update(SSO_CLIENT_SECRET="fixture-only", BASE_URL="https://trust.example.com")
        self.assertNotIn(b"Continue with", self.client.get(login).data)
        self.sign_in("admin", staff=True)
        self.assertEqual(self.client.get(settings).status_code, 403)
        self.post("/account/logout/", source="/")
        self.sign_in("owner", staff=True)
        self.assertIn(b"https://trust.example.com/account/sso/callback/", self.client.get(settings).data)
        callback_query = {}

        def token(**kwargs):
            self.assertTrue(kwargs["code_verifier"])
            now_epoch = int(time.time())
            signed = jwt.encode({"alg": "RS256", "kid": "fixture"}, {
                "sub": "reader-example-subject", "iss": issuer, "aud": "fixture-client",
                "iat": now_epoch, "exp": now_epoch + 300, "nonce": callback_query["nonce"][0],
            }, key)
            return {"id_token": signed, "access_token": "fixture-token"}

        def authorize(response):
            nonlocal callback_query
            self.assertEqual(response.status_code, 302)
            callback_query = parse_qs(urlsplit(response.location).query)
            self.assertEqual(callback_query["code_challenge_method"], ["S256"])
            self.assertEqual(callback_query["scope"], ["openid"])
            return "/account/sso/callback/?code=fixture-code&state=" + callback_query["state"][0]

        with patch("portal.sso.discovery", return_value=provider), \
                patch("portal.sso.OIDCClient.fetch_access_token", side_effect=token), \
                patch("portal.sso.OIDCClient.fetch_jwk_set", return_value={"keys": [key.as_dict(private=False)]}):
            result = self.post(settings, {"enabled": "on", "label": "Example Provider", "issuer": issuer,
                                          "client_id": "fixture-client", "token_auth": "client_secret_basic"})
            self.assertEqual(result.status_code, 302)
            self.post("/account/logout/", source="/")
            self.assertIn(b"Continue with Example Provider", self.client.get(login).data)
            unlinked = authorize(self.post(start, source=login))
            self.assertIn("/account/login/", self.client.get(unlinked).location)
            self.sign_in("reader")
            invalid = self.post(connection, {"password": "invalid", "action": "link"})
            self.assertIn(b"Enter your current Trust Centre password", invalid.data)
            link = authorize(self.post(connection, {"password": PASSWORD, "action": "link"}))
            self.assertEqual(self.client.get(link).location, connection)
            self.assertIn(b"Your account is connected", self.client.get(connection).data)
            self.post("/account/logout/", source="/")
            signed_in = authorize(self.post(start, {"next": "/library/"}, source=login))
            self.assertEqual(self.client.get(signed_in).location, "/library/")
            self.assertEqual(self.client.get("/notifications/").status_code, 200)
            self.assertEqual(self.client.get("/manage/").status_code, 403)

    def test_public_tier_and_signed_out_agreement(self):
        self.sign_in("admin", staff=True)
        identifier, revision = self.upload()
        doc_path = f"/documents/{identifier}/"
        file = f"{doc_path}files/{revision}/"
        edit = f"/manage/documents/{identifier}/"
        fields = {"title": "Audit report", "teaser": "Scope of the audit.", "category_id": self.category,
                  "minimum_tier": 0, "retain_previous": 1, "agreement": ""}
        self.assertIn(b'Public allows anyone', self.client.get(edit).data)
        self.assertEqual(self.post(edit, fields).status_code, 302)
        guest = self.app.test_client()
        self.assertEqual(guest.get(file).status_code, 404)
        publish = f"/manage/documents/{identifier}/versions/{revision}/publish/"
        self.assertEqual(self.post(publish).status_code, 302)
        self.assertIn(b">Public</span>", guest.get("/library/").data)
        with guest.get(file) as pdf:
            self.assertEqual(pdf.data, b"%PDF-1.4\nreport")
            self.assertEqual(pdf.headers["Cache-Control"], "no-store")

        account = f"/manage/accounts/{self.reader}/"
        self.assertEqual(self.post(account, {"username": "reader", "email": "reader@example.com",
                                            "tier": 0, "is_active": "on"}).status_code, 302)
        self.assertIn(b'<option value="0" selected>Public', self.client.get(account).data)
        fields["agreement"] = "Read this first."
        self.assertEqual(self.post(edit, fields).status_code, 302)
        self.assertEqual(guest.get(file).location, doc_path)
        page = guest.get(doc_path)
        csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        digest = re.search(rb'name="digest" value="([^"]+)"', page.data).group(1).decode()
        self.assertEqual(guest.post(doc_path + "agreement/", data={
            "csrf_token": csrf, "digest": digest, "agree": "yes",
        }).status_code, 302)
        with guest.get(file) as pdf:
            self.assertEqual(pdf.status_code, 200)
        self.assertEqual(self.app.test_client().get(file).location, doc_path)

        fields["agreement"] = "Revised terms."
        self.assertEqual(self.post(edit, fields).status_code, 302)
        self.assertEqual(guest.get(file).location, doc_path)
        fields.update(agreement="", minimum_tier=10)
        self.assertEqual(self.post(edit, fields).status_code, 302)
        self.assertIn("/account/login/", guest.get(file).location)
        self.post("/account/logout/", source="/")
        self.sign_in("reader")
        self.assertEqual(self.client.get(file).status_code, 403)

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

    def test_legacy_cookie_requires_a_fresh_sign_in(self):
        with self.app.app_context():
            account = one("SELECT * FROM portal_account WHERE id=?", self.admin)
            with self.client.session_transaction() as cookie:
                cookie.update(account=self.admin, stamp=signature(account), otp=None)
        response = self.client.get("/manage/documents/")
        self.assertIn("/account/login/", response.location)

    def test_sign_out_and_disabling_revoke_sessions(self):
        self.sign_in("reader")
        signed_out = self.app.test_client()
        signed_out.set_cookie("trust_session", self.client.get_cookie("trust_session").value)
        self.assertEqual(signed_out.get("/notifications/").status_code, 200)
        self.post("/account/logout/", source="/")
        self.assertEqual(signed_out.get("/notifications/").status_code, 302)

        self.sign_in("reader")
        disabled = self.app.test_client()
        disabled.set_cookie("trust_session", self.client.get_cookie("trust_session").value)
        self.post("/account/logout/", source="/")
        # Sign the customer in again in another browser so its active session can be revoked by the admin.
        original = self.client
        self.client = disabled
        self.sign_in("reader")
        self.client = original
        self.sign_in("admin", staff=True)
        edit = f"/manage/accounts/{self.reader}/"
        details = {"username": "reader", "email": "reader@example.com", "tier": 10}
        self.assertEqual(self.post(edit, details).status_code, 302)
        self.assertEqual(self.post(edit, {**details, "is_active": "on"}).status_code, 302)
        self.assertEqual(disabled.get("/notifications/").status_code, 302)

    def test_password_reset_queues_mail_and_uses_a_token_free_form(self):
        stale = self.app.test_client()
        self.sign_in("reader")
        stale.set_cookie("trust_session", self.client.get_cookie("trust_session").value)
        with patch("portal.auth.send") as send:
            known = self.post("/account/reset/", {"email": "reader@example.com"})
            unknown = self.post("/account/reset/", {"email": "unknown@example.com"})
            self.assertEqual(known.data, unknown.data)
            send.assert_not_called()
            with self.app.app_context():
                self.assertEqual(len(rows("SELECT id FROM portal_outgoingmail WHERE password_reset=1")), 2)
            worker = self.app.test_cli_runner().invoke(args=["send_queue"])
            self.assertEqual(worker.exit_code, 0, worker.output)
            self.assertEqual(send.call_count, 1)
        body = send.call_args.args[2]
        link = re.search(r"/account/reset/confirm/[^/]+/", body).group(0)
        start = self.client.get(link)
        self.assertEqual(start.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(start.location, "/account/reset/confirm/")
        password = "replacement-test-password"
        self.assertEqual(self.post(start.location, {"password": password, "confirmation": password}).status_code, 302)
        self.client.get(link)
        self.assertIn(b"expired or already been used", self.client.get(start.location).data)
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
        file = b"email,company,tier\nimported@example.com,Imported Company,Public\nreader@example.com,Existing,Expert\n"
        import_result = self.post("/manage/accounts/import/", {"file": (io.BytesIO(file), "customers.csv")})
        self.assertEqual(import_result.status_code, 302)
        review = "/manage/accounts/requests/"
        page = self.client.get(review)
        self.assertIn(b"Example Company", page.data)
        self.assertIn(b"Imported Company", page.data)
        with self.app.app_context():
            pending = rows("SELECT * FROM trust_account_request ORDER BY id")
            self.assertEqual(len(pending), 2)
            self.assertEqual(pending[1]["tier"], 0)
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
        start = self.client.get(link)
        self.assertEqual(start.location, "/account/reset/confirm/")
        self.assertEqual(self.post(start.location, {"password": password, "confirmation": password}).status_code, 302)
        self.client.get(link)
        self.assertIn(b"expired or already been used", self.client.get(start.location).data)
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
