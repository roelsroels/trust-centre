import re
from urllib.parse import urlsplit

from flask import current_app

from .db import one
from .fonts import faces
from .models import BRANDING, button_text

SYSTEM_FONTS = {
    "sans": "system-ui, Arial, Helvetica, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "ui-monospace, 'Courier New', monospace",
}
WEBFONT_STACK = "'Portal Webfont', 'Century Gothic', 'Avenir Next', system-ui, Arial, sans-serif"

LINK_LABELS = {
    "Confirm your subscription at ": "Confirm subscription",
    "Open in the Trust Centre: ": "Open in the Trust Centre",
    "Unsubscribe: ": "Unsubscribe",
}


def email_blocks(body):
    base = current_app.config["BASE_URL"] + "/"
    for paragraph in re.split(r"\n\s*\n", body.strip()):
        for label_prefix, label in LINK_LABELS.items():
            if paragraph.startswith(label_prefix):
                address = paragraph.removeprefix(label_prefix)
                break
        else:
            address = paragraph
            path = address.removeprefix(base)
            if path.startswith("account/reset/confirm/"):
                label = "Reset password"
            elif path.startswith("documents/"):
                label = "View document"
            else:
                label = ""
        if label and address.startswith(base) and not any(c.isspace() for c in address):
            yield {"url": address, "label": label, "secondary": label == "Unsubscribe"}
        else:
            yield {"lines": paragraph.splitlines()}


def branded_email(subject, body):
    brand = one("SELECT * FROM portal_branding ORDER BY id LIMIT 1") or BRANDING
    accent = brand["accent"] if re.fullmatch(r"#[0-9a-fA-F]{6}", brand["accent"]) else BRANDING["accent"]
    ink = brand["ink"] if re.fullmatch(r"#[0-9a-fA-F]{6}", brand["ink"]) else BRANDING["ink"]
    try:
        logo = urlsplit(brand["logo_url"])
        logo_url = brand["logo_url"] if logo.scheme == "https" and logo.hostname and not logo.username else ""
    except ValueError:
        logo_url = ""
    font = brand["font"]
    template = current_app.jinja_env.get_template("email.html")
    return template.render(
        brand=brand, logo_url=logo_url, accent=accent, ink=ink, on_accent=button_text(accent),
        on_ink=button_text(ink), font_faces=faces(font), font_family=SYSTEM_FONTS.get(font, WEBFONT_STACK),
        chromatica=font == "chromatica",
        subject=subject, blocks=list(email_blocks(body)), home=current_app.config["BASE_URL"],
    )
