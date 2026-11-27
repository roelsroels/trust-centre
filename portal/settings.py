import os
from pathlib import Path
from urllib.parse import urlsplit


BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL = os.getenv("DEBUG") == "1"
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
origin = urlsplit(BASE_URL)
if not origin.hostname or origin.path or (not LOCAL and origin.scheme != "https"):
    raise RuntimeError("Set BASE_URL to the public HTTPS origin.")
SECRET_KEY = os.getenv("SECRET_KEY") or ("local-development-only" if LOCAL else "")
if not SECRET_KEY:
    raise RuntimeError("Set SECRET_KEY before starting the Trust Centre.")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
TRUSTED_HOSTS = [origin.hostname, *os.getenv("EXTRA_HOSTS", "").split()]
SESSION_COOKIE_NAME = "trust_session"
SESSION_COOKIE_SECURE = not LOCAL
SESSION_COOKIE_SAMESITE = "Lax"
MAX_CONTENT_LENGTH = 27 * 1024 * 1024
MAX_FORM_MEMORY_SIZE = 512 * 1024

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SSL = os.getenv("SMTP_SSL") == "1"
SMTP_TLS = os.getenv("SMTP_TLS", "1") == "1" and not SMTP_SSL
FROM_EMAIL = os.getenv("FROM_EMAIL", "trust@example.com")
MAIL_CONSOLE = os.getenv("MAIL_CONSOLE") == "1"
