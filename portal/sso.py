import hmac
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import requests
from authlib.integrations.base_client import OAuthError
from authlib.integrations.flask_client import FlaskIntegration, FlaskOAuth2App
from authlib.integrations.requests_client import OAuth2Session
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from joserfc.errors import JoseError

from .auth import destination, finish_login, limit, signature, verify_password
from .db import one, run, write
from .services import absolute

sso = Blueprint("sso", __name__, url_prefix="/account/sso")
DEFAULT_CONFIG = {"enabled": False, "label": "Single sign-on", "issuer": "", "client_id": "",
                  "token_auth": "client_secret_basic"}
AUTH_METHODS = (("client_secret_basic", "HTTP Basic"), ("client_secret_post", "POST body"))
CALLBACK = "/account/sso/callback/"
FLOW_TTL = 300


def configuration():
    return one("SELECT * FROM trust_sso_config WHERE id=1") or DEFAULT_CONFIG


def available(config):
    return bool(config["enabled"] and current_app.config["SSO_CLIENT_SECRET"])


def https_url(value):
    if not value or any(c.isspace() or c in "\\<>\"" for c in value):
        return False
    try:
        parts = urlsplit(value)
        return bool(parts.scheme == "https" and parts.hostname and parts.username is None and not parts.fragment)
    except ValueError:
        return False


def valid_issuer(value):
    return https_url(value) and not urlsplit(value).query


def discovery(issuer):
    response = requests.get(
        issuer.rstrip("/") + "/.well-known/openid-configuration",
        timeout=8, allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError("OIDC discovery did not return HTTP 200.")
    metadata = response.json()
    if not isinstance(metadata, dict) or metadata.get("issuer") != issuer:
        raise ValueError("The issuer must match the discovery document exactly.")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(metadata.get(key), str) or not https_url(metadata[key]):
            raise ValueError("OIDC discovery must advertise HTTPS authorization, token, and keys endpoints.")
    return metadata


class OIDCSession(OAuth2Session):
    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


class OIDCClient(FlaskOAuth2App):
    client_cls = OIDCSession


def client(config):
    metadata = discovery(config["issuer"])
    algorithms = metadata.get("id_token_signing_alg_values_supported", ["RS256"])
    if not isinstance(algorithms, list):
        raise ValueError("Invalid OIDC signing algorithm list.")
    supported = [alg for alg in algorithms if alg in {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                                                      "ES256", "ES384", "ES512", "EdDSA"}]
    if not supported:
        raise ValueError("The provider must support signed ID tokens with asymmetric keys.")
    return OIDCClient(
        FlaskIntegration("trust_oidc"), name="trust_oidc",
        client_id=config["client_id"], client_secret=current_app.config["SSO_CLIENT_SECRET"],
        client_kwargs={"scope": "openid", "code_challenge_method": "S256", "default_timeout": 8,
                       "token_endpoint_auth_method": config["token_auth"]},
        issuer=config["issuer"], authorization_endpoint=metadata["authorization_endpoint"],
        token_endpoint=metadata["token_endpoint"], jwks_uri=metadata["jwks_uri"],
        id_token_signing_alg_values_supported=supported,
    )


def provider_key(config):
    return [config["issuer"], config["client_id"], config["token_auth"]]


def discard_intent():
    intent = session.pop("sso_intent", None)
    if intent:
        FlaskIntegration("trust_oidc").clear_state_data(session, intent["state"])
    return intent


def begin(config, target, account=None):
    discard_intent()
    state = secrets.token_urlsafe(24)
    try:
        provider = client(config)
        response = provider.authorize_redirect(absolute(CALLBACK), state=state)
    except (OAuthError, requests.RequestException, RuntimeError, ValueError) as error:
        current_app.logger.warning("Could not start SSO (%s)", type(error).__name__)
        flash("Single sign-on is unavailable. Check the provider configuration or sign in with your password.")
        return redirect(url_for("sso.connection") if account else url_for("auth.login", next=target))
    session["sso_intent"] = {
        "state": state, "provider": provider_key(config), "target": target, "expires": int(time.time()) + FLOW_TTL,
        "account": account["id"] if account else None, "stamp": signature(account) if account else None,
    }
    return response


@sso.post("/start/")
def start():
    config = configuration()
    if not available(config):
        abort(404)
    target = destination(request.form.get("next"), "/library/")
    if g.user:
        return redirect(target)
    if not limit("sso-start", ""):
        abort(429)
    return begin(config, target)


@sso.route("/connection/", methods=["GET", "POST"])
def connection():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["is_staff"] and not g.staff:
        return redirect(url_for("auth.authenticator", next=request.path))
    config = configuration()
    if not available(config):
        abort(404)
    linked = one("SELECT account_id FROM trust_sso_identity WHERE issuer=? AND account_id=?",
                 config["issuer"], g.user["id"])
    if request.method == "POST":
        if not limit("sso-link", g.user["username"]):
            abort(429)
        if not verify_password(g.user["password"], request.form.get("password", "")):
            flash("Enter your current Trust Centre password to continue.")
        elif request.form.get("action") == "unlink":
            discard_intent()
            run("DELETE FROM trust_sso_identity WHERE issuer=? AND account_id=?", config["issuer"], g.user["id"])
            flash("Single sign-on connection removed.")
            return redirect(url_for("sso.connection"))
        elif request.form.get("action") == "link" and not linked:
            return begin(config, url_for("sso.connection"), g.user)
    return render_template("sso_connection.html", provider=config["label"], linked=bool(linked))


@sso.get("/callback/")
def callback():
    config = configuration()
    intent = session.get("sso_intent")
    if (not available(config) or not intent or intent["expires"] <= time.time()
            or intent["provider"] != provider_key(config)
            or not hmac.compare_digest(intent["state"], request.args.get("state", ""))):
        discard_intent()
        flash("This sign-in attempt expired or could not be verified. Start again.")
        return redirect(url_for("auth.login"))
    session.pop("sso_intent", None)
    try:
        token = client(config).authorize_access_token(
            claims_options={"iss": {"essential": True, "value": config["issuer"]}}, leeway=60)
        claims = token.get("userinfo") or {}
        subject = claims.get("sub")
        if claims.get("iss") != config["issuer"] or not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise ValueError("Invalid OIDC identity")
    except (OAuthError, JoseError, requests.RequestException, RuntimeError, ValueError) as error:
        current_app.logger.warning("Could not verify SSO callback (%s)", type(error).__name__)
        flash("The identity provider could not verify your sign-in. Try again or use your password.")
        return redirect(url_for("sso.connection") if intent["account"] else url_for("auth.login"))
    finally:
        FlaskIntegration("trust_oidc").clear_state_data(session, intent["state"])

    with write():
        if intent["account"]:
            account = one("SELECT * FROM portal_account WHERE id=? AND is_active=1", intent["account"])
            if (not g.user or not account or g.user["id"] != account["id"]
                    or not hmac.compare_digest(signature(account), intent["stamp"])
                    or (account["is_staff"] and not g.staff)):
                abort(403)
            try:
                run("INSERT INTO trust_sso_identity(issuer,subject,account_id) VALUES (?,?,?)",
                    config["issuer"], subject, account["id"])
            except sqlite3.IntegrityError:
                flash("That single sign-on identity or account is already connected.")
            else:
                flash("Single sign-on is connected to your Trust Centre account.")
            return redirect(url_for("sso.connection"))

        account = one("""SELECT a.* FROM trust_sso_identity i JOIN portal_account a ON a.id=i.account_id
                         WHERE i.issuer=? AND i.subject=? AND a.is_active=1""", config["issuer"], subject)
        if not account:
            flash("Sign in with your Trust Centre password first, then connect single sign-on from your account menu.")
            return redirect(url_for("auth.login", next=intent["target"]))
        return finish_login(account, intent["target"])
