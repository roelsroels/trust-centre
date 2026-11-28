import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

import pyotp
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from .account_requests import email_address, request_account
from .db import now, one, rows, run, write
from .services import absolute, send

auth = Blueprint("auth", __name__)


def hash_password(password):
    return generate_password_hash(password, method="scrypt")


def verify_password(encoded, password):
    if len(password) > 1024 or not encoded or encoded.startswith("!"):
        return False
    if encoded.startswith(("pbkdf2_sha256$", "pbkdf2_sha1$")):
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        digest = hashlib.pbkdf2_hmac(algorithm.removeprefix("pbkdf2_"), password.encode(),
                                     salt.encode(), int(iterations))
        return hmac.compare_digest(base64.b64encode(digest).decode(), expected)
    return check_password_hash(encoded, password)


def password_error(password):
    if not 12 <= len(password) <= 1024 or password.isdigit():
        return "Use a password of 12 to 1,024 characters that includes a letter or symbol."
    return ""


def signature(account):
    identity = ":".join(str(account[key]) for key in
                        ("id", "password", "email", "is_staff", "is_superuser", "auth_generation"))
    return hmac.new(current_app.secret_key.encode(), identity.encode(), "sha256").hexdigest()


def session_hash(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def revoke_sessions(account_id):
    run("DELETE FROM trust_session WHERE account_id=?", account_id)


def revoke_current_session():
    if sid := session.get("sid"):
        run("DELETE FROM trust_session WHERE sid_hash=?", session_hash(sid))


def load_user():
    g.user = None
    g.staff = False
    sid = session.get("sid")
    if not sid:
        if "account" in session:  # Retire sessions issued before server-side revocation.
            session.clear()
        return
    record = one("""SELECT a.*,s.stamp AS session_stamp,s.otp_id AS session_otp FROM trust_session s
                    JOIN portal_account a ON a.id=s.account_id
                    WHERE s.sid_hash=? AND s.expires>? AND a.is_active=1""",
                 session_hash(sid), int(time.time()))
    if not record or not hmac.compare_digest(signature(record), record["session_stamp"]):
        session.clear()
        return
    g.user = record
    g.staff = bool(record["is_staff"] and record["session_otp"])


def limit(action, identity):
    epoch = int(time.time())
    keys = [(f"ip:{request.remote_addr}", 60)]
    if identity:
        keys.append((f"account:{identity.casefold()}", 12))
    with write():
        run("DELETE FROM trust_auth_limit WHERE expires<=?", epoch)
        for key, maximum in keys:
            digest = hashlib.sha256(f"{action}:{key}".encode()).hexdigest()
            run("""INSERT INTO trust_auth_limit VALUES (?,1,?)
                   ON CONFLICT(key) DO UPDATE SET hits=hits+1""", digest, epoch + 600)
            if one("SELECT hits FROM trust_auth_limit WHERE key=?", digest)["hits"] > maximum:
                return False
    return True


def verify_otp(user, supplied):
    if not re.fullmatch(r"[0-9]{6,8}", supplied):
        return None
    for device in rows("SELECT * FROM otp_totp_totpdevice WHERE user_id=? AND confirmed=1", user["id"]):
        if len(supplied) != device["digits"] or not device["step"]:
            continue
        secret = base64.b32encode(bytes.fromhex(device["key"])).decode()
        generator = pyotp.TOTP(secret, digits=device["digits"], interval=device["step"])
        counter = (int(time.time()) - device["t0"]) // device["step"]
        for offset in range(-device["tolerance"], device["tolerance"] + 1):
            tick = counter + device["drift"] + offset
            if tick > device["last_t"] and hmac.compare_digest(generator.generate_otp(tick), supplied):
                run("UPDATE otp_totp_totpdevice SET last_t=?,drift=?,last_used_at=? WHERE id=?",
                    tick, tick - counter, now(), device["id"])
                return device["id"]
    return None


def destination(value, fallback):
    if (value and re.fullmatch(r"/(?!/)[^\x00-\x20\\]*", value)
            and not urlsplit(value).netloc):
        return value
    return fallback


def establish_session(account, device=None):
    epoch = int(time.time())
    sid = secrets.token_urlsafe(32)
    run("DELETE FROM trust_session WHERE expires<=?", epoch)
    revoke_current_session()
    run("INSERT INTO trust_session(sid_hash,account_id,stamp,otp_id,expires) VALUES (?,?,?,?,?)",
        session_hash(sid), account["id"], signature(account), device,
        epoch + int(current_app.permanent_session_lifetime.total_seconds()))
    session.clear()
    session["sid"] = sid
    run("UPDATE portal_account SET last_login=? WHERE id=?", now(), account["id"])


def finish_login(account, target):
    if account["is_staff"]:
        session.clear()
        session["mfa_pending"] = {
            "id": account["id"], "stamp": signature(account),
            "target": target, "expires": int(time.time()) + 300,
        }
        return redirect(url_for("auth.authenticator"))
    establish_session(account)
    return redirect(target)


@auth.get("/manage/login/")
def manage_login():
    target = destination(request.args.get("next"), "/manage/")
    return redirect(url_for("auth.login", next=target))


@auth.route("/account/login/", methods=["GET", "POST"])
def login():
    target = destination(request.values.get("next"), "/library/")
    if g.user:
        if g.user["is_staff"] and not g.staff:
            return redirect(url_for("auth.authenticator", next=target))
        return redirect(target)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not limit("login", username):
            abort(429)
        user = one("SELECT * FROM portal_account WHERE username=? AND is_active=1", username)
        # A dummy hash keeps an unknown username on the same expensive path.
        valid = verify_password(user["password"] if user else current_app.config["DUMMY_PASSWORD"], password)
        if user and valid:
            with write():
                refreshed = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", user["id"])
                if refreshed and signature(refreshed) == signature(user):
                    return finish_login(refreshed, target)
        session.pop("mfa_pending", None)
        flash("Check your username and password.")
    return render_template("sign_in.html", target=target)


@auth.route("/account/authenticator/", methods=["GET", "POST"])
def authenticator():
    target = destination(request.args.get("next"), "/manage/")
    if g.staff:
        return redirect(target)
    pending = session.get("mfa_pending")
    if g.user and g.user["is_staff"]:
        identifier, stamp = g.user["id"], signature(g.user)
    elif pending and pending["expires"] > time.time():
        identifier, stamp, target = pending["id"], pending["stamp"], pending["target"]
    else:
        session.pop("mfa_pending", None)
        return redirect(url_for("auth.login", next=target))
    account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1 AND is_staff=1", identifier)
    if not account or not hmac.compare_digest(signature(account), stamp):
        session.clear()
        return redirect(url_for("auth.login", next=target))
    if request.method == "POST":
        if not limit("authenticator", account["username"]):
            abort(429)
        with write():
            account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1 AND is_staff=1", identifier)
            if not account or not hmac.compare_digest(signature(account), stamp):
                session.clear()
                return redirect(url_for("auth.login", next=target))
            if device := verify_otp(account, request.form.get("otp", "")):
                establish_session(account, device)
                return redirect(target)
        flash("Check the current code in your authenticator and try again.")
    return render_template("authenticator.html", target=target)


@auth.route("/account/request/", methods=["GET", "POST"])
def request_new_account():
    if g.user:
        return redirect(url_for("public.library"))
    if request.method == "POST":
        address = request.form.get("email", "").strip()
        company = request.form.get("company", "").strip()
        if not limit("request-account", address):
            abort(429)
        try:
            email = email_address(address)
            if not 1 <= len(company) <= 150:
                raise ValueError("Enter a customer or company name of up to 150 characters.")
        except ValueError as error:
            flash(str(error))
        else:
            with write():
                request_account(email, company)
            flash("If your account request is approved, you’ll receive an email to set your password.")
            return redirect(url_for("auth.login"))
    return render_template("request_account.html")


@auth.post("/account/logout/")
def logout():
    revoke_current_session()
    session.clear()
    return redirect("/")


def reset_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="trust-centre-password-reset")


def reset_link(account):
    payload = reset_serializer().dumps([account["id"], signature(account)])
    return absolute(f"/account/reset/confirm/{payload}/")


def reset_message(account):
    if account["password"].startswith("!setup:"):
        send(account["email"], "Set up your Trust Centre account",
             f"Your Trust Centre account has been approved. Your username is {account['username']}.\n\n"
             f"Choose your password within one hour:\n\n{reset_link(account)}\n\n"
             "If you did not request an account, you can ignore this email.")
        return
    send(account["email"], "Reset your Trust Centre password",
         f"Choose a new password within one hour:\n\n{reset_link(account)}\n\n"
         "If you did not request this, you can ignore this email.")


@auth.route("/account/reset/", methods=["GET", "POST"])
def reset():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not limit("reset", email):
            abort(429)
        if 1 <= len(email) <= 254:
            run("""INSERT INTO portal_outgoingmail(email,subject,body,expected_status,attempts,next_attempt,password_reset)
                   VALUES (?,'','','',0,?,1)""", email, now())
        return render_template("message.html", heading="Check your email",
                               text="If an active account uses that address, you’ll receive a password reset link.")
    return render_template("reset.html", valid=True)


@auth.get("/account/reset/confirm/<token>/")
def reset_begin(token):
    session.pop("reset_pending", None)
    try:
        (identifier, stamp), issued = reset_serializer().loads(token, max_age=3600, return_timestamp=True)
    except BadData:
        return redirect(url_for("auth.reset_confirm"))
    account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", identifier)
    if account and hmac.compare_digest(stamp, signature(account)):
        session["reset_pending"] = {"id": identifier, "stamp": stamp, "expires": int(issued.timestamp()) + 3600}
    return redirect(url_for("auth.reset_confirm"))


@auth.route("/account/reset/confirm/", methods=["GET", "POST"])
def reset_confirm():
    pending = session.get("reset_pending")
    account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", pending["id"]) if pending else None
    valid = bool(account and pending["expires"] > time.time()
                 and hmac.compare_digest(pending["stamp"], signature(account)))
    if valid and request.method == "POST":
        password = request.form.get("password", "")
        error = password_error(password)
        if request.form.get("confirmation") != password:
            error = "The passwords must match."
        if error:
            flash(error)
        else:
            hashed = hash_password(password)
            with write():
                current = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", account["id"])
                if current and pending["expires"] > time.time() and hmac.compare_digest(
                    pending["stamp"], signature(current)
                ):
                    run("UPDATE portal_account SET password=? WHERE id=?", hashed, account["id"])
                    revoke_sessions(account["id"])
                    revoke_current_session()
                    session.clear()
                    flash("Password changed. Sign in with your new password.")
                    return redirect(url_for("auth.login"))
            valid = False
    if not valid:
        session.pop("reset_pending", None)
    return render_template("reset.html", confirm=True, valid=valid)
