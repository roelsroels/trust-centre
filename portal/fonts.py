import re
from urllib.parse import urlsplit

from .db import rows

BUILTIN = {"chromatica": "Chromatica", "sans": "System sans", "serif": "System serif", "mono": "System mono"}
STYLES = (
    ("regular", "Regular", 400, "normal"),
    ("medium", "Medium", 500, "normal"),
    ("italic", "Regular italic / oblique", 400, "italic"),
    ("medium_italic", "Medium italic / oblique", 500, "italic"),
)
CHROMATICA_LOCALS = ("Chromatica Regular", "Chromatica Medium", "Chromatica Regular Oblique", "Chromatica Medium Oblique")


def font_url(value):
    if not value:
        return ""
    if len(value) > 2000 or not re.fullmatch(r"https://[A-Za-z0-9._~:/?\[\]@!$&*+,;=%#-]+", value):
        raise ValueError("Use a direct HTTPS URL to a WOFF2 file.")
    url = urlsplit(value)
    if not url.hostname or url.username is not None or not url.path.lower().endswith(".woff2"):
        raise ValueError("Use a direct HTTPS URL to a WOFF2 file.")
    return value


def font_choices():
    return list(BUILTIN.items()) + [
        (entry["key"], entry["name"]) for entry in rows("SELECT key,name FROM trust_font WHERE key<>'chromatica' ORDER BY name")
    ]


def faces(key):
    if key in {"sans", "serif", "mono"}:
        return []
    entries = rows("SELECT * FROM trust_font WHERE key=?", key)
    entry = entries[0] if entries else {}
    result = []
    for index, (field, _, weight, style) in enumerate(STYLES):
        try:
            url = font_url(entry.get(field, "") if isinstance(entry, dict) else entry[field])
        except ValueError:
            url = ""
        local = CHROMATICA_LOCALS[index] if key == "chromatica" else ""
        if url or local:
            result.append({"url": url, "local": local, "weight": weight, "style": style})
    return result
