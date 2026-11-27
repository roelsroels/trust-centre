import base64
import hashlib
import hmac
import re
import time
from urllib.parse import urlsplit

import pyotp
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

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
    identity = f"{account['id']}:{account['password']}:{account['email']}:{account['is_staff']}:{account['is_superuser']}"
    return hmac.new(current_app.secret_key.encode(), identity.encode(), "sha256").hexdigest()


def load_user():
    user = None
    if session.get("account"):
        candidate = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", session["account"])
        if candidate and hmac.compare_digest(signature(candidate), session.get("stamp", "")):
            user = candidate
        else:
            session.clear()
    g.user = user
    g.staff = bool(user and user["is_staff"] and session.get("otp") and one(
        "SELECT id FROM otp_totp_totpdevice WHERE user_id=? AND id=? AND confirmed=1",
        user["id"], session["otp"],
    ))


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
    session.clear()
    session.update(account=account["id"], stamp=signature(account), otp=device)
    run("UPDATE portal_account SET last_login=? WHERE id=?", now(), account["id"])


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
                    if refreshed["is_staff"]:
                        session.clear()
                        session["mfa_pending"] = {
                            "id": refreshed["id"], "stamp": signature(refreshed),
                            "target": target, "expires": int(time.time()) + 300,
                        }
                        return redirect(url_for("auth.authenticator"))
                    establish_session(refreshed)
                    return redirect(target)
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


@auth.post("/account/logout/")
def logout():
    session.clear()
    return redirect("/")


def reset_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="trust-centre-password-reset")


def reset_link(account):
    payload = reset_serializer().dumps([account["id"], signature(account)])
    return absolute(url_for("auth.reset_confirm", token=payload))


def reset_message(account):
    send(account["email"], "Reset your Trust Centre password",
         f"Choose a new password within one hour:\n\n{reset_link(account)}\n\n"
         "If you did not request this, you can ignore this email.")


@auth.route("/account/reset/", methods=["GET", "POST"])
def reset():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not limit("reset", email):
            abort(429)
        for account in rows("SELECT * FROM portal_account WHERE email=? COLLATE NOCASE AND is_active=1", email):
            if account["password"].startswith("!"):
                continue
            try:
                reset_message(account)
            except (OSError, ValueError) as error:
                current_app.logger.warning("Could not send password reset for account %s (%s)", account["id"], type(error).__name__)
        return render_template("message.html", heading="Check your email",
                               text="If an active account uses that address, you’ll receive a password reset link.")
    return render_template("reset.html", valid=True)


@auth.route("/account/reset/confirm/<token>/", methods=["GET", "POST"])
def reset_confirm(token):
    try:
        identifier, stamp = reset_serializer().loads(token, max_age=3600)
    except BadData:
        identifier, stamp = None, ""
    account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", identifier)
    valid = bool(account and hmac.compare_digest(stamp, signature(account)))
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
                changed = run("UPDATE portal_account SET password=? WHERE id=? AND password=? AND is_active=1",
                              hashed, account["id"], account["password"]).rowcount
            if changed:
                session.clear()
                flash("Password changed. Sign in with your new password.")
                return redirect(url_for("auth.login"))
            valid = False
    return render_template("reset.html", confirm=True, valid=valid)
