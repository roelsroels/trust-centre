import secrets
from datetime import datetime, timedelta

from flask import Flask, g, get_flashed_messages, redirect, render_template, request
from flask_wtf import CSRFProtect
from werkzeug.exceptions import SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix

from . import settings
from .admin import manage
from .auth import auth, hash_password, load_user
from .cli import register_commands
from .db import close_db
from .fonts import BUILTIN, faces
from .models import TIERS, Tier, button_text
from .sso import available, configuration, sso
from .views import branding, public


def create_app(**overrides):
    app = Flask(__name__)
    app.config.from_object(settings)
    app.config.update(overrides)
    app.config["DATA_DIR"].mkdir(mode=0o700, parents=True, exist_ok=True)
    app.config["DUMMY_PASSWORD"] = hash_password(secrets.token_hex(20))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

    @app.errorhandler(SecurityError)
    def untrusted_host(error):
        return "Invalid host.", 400

    @app.before_request
    def secure_request():
        if isinstance(request.routing_exception, SecurityError):
            raise request.routing_exception
        if not app.config["LOCAL"] and not request.is_secure:
            return redirect(app.config["BASE_URL"] + request.full_path.rstrip("?"), code=301)
        if request.headers.get("X-Real-IP"):
            request.environ["REMOTE_ADDR"] = request.headers["X-Real-IP"]

    CSRFProtect(app)

    @app.before_request
    def authenticate_request():
        if request.endpoint != "static":
            load_user()

    @app.context_processor
    def shared():
        brand = branding()
        font_faces = faces(brand["font"])
        oidc = configuration()
        font_class = brand["font"] if brand["font"] in BUILTIN else "web"
        return {"brand": brand, "font_faces": font_faces, "font_class": font_class, "button_text": button_text(brand["accent"]), "user": g.get("user"), "staff": g.get("staff"),
                "sso_enabled": available(oidc), "sso_label": oidc["label"],
                "tiers": TIERS, "public_tier": Tier.PUBLIC, "messages": get_flashed_messages()}

    @app.template_filter("shortdate")
    def shortdate(value):
        if not value:
            return ""
        return datetime.fromisoformat(value).strftime("%d %b %Y").lstrip("0")

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "no-referrer" if request.endpoint in {"auth.reset_begin", "sso.callback"} else "same-origin"
        )
        if not app.config["LOCAL"] and request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=86400"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    for code, heading, text in (
        (400, "Request could not be verified", "Reload the page and try again."),
        (403, "Access denied", "Your account does not have permission to open this page."),
        (404, "Page not found", "The page or document is unavailable."),
        (413, "File too large", "Choose a smaller file."),
        (429, "Too many attempts", "Try again in a few minutes."),
    ):
        def error_page(error, heading=heading, text=text, code=code):
            return render_template("message.html", heading=heading, text=text), code

        app.register_error_handler(code, error_page)
    app.teardown_appcontext(close_db)
    app.register_blueprint(public)
    app.register_blueprint(auth)
    app.register_blueprint(sso)
    app.register_blueprint(manage)
    register_commands(app)
    return app
