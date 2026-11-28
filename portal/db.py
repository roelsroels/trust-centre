import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

from .models import BURGUNDY, CORAL


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATA_DIR"] / "portal.sqlite3", timeout=20, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def one(query, *args):
    return db().execute(query, args).fetchone()


def rows(query, *args):
    return db().execute(query, args).fetchall()


def run(query, *args):
    return db().execute(query, args)


@contextmanager
def write():
    connection = db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def initialize():
    db().executescript(Path(__file__).with_name("schema.sql").read_text())
    with write():
        columns = {column["name"] for column in rows("PRAGMA table_info(portal_branding)")}
        if "show_demo_team" not in columns:
            run("ALTER TABLE portal_branding ADD COLUMN show_demo_team INTEGER NOT NULL DEFAULT 0")
        columns = {column["name"] for column in rows("PRAGMA table_info(portal_accessrequest)")}
        if "handled" not in columns:
            run("ALTER TABLE portal_accessrequest ADD COLUMN handled TEXT")
        run("""CREATE UNIQUE INDEX IF NOT EXISTS trust_pending_access
               ON portal_accessrequest(user_id, document_id, tier) WHERE handled IS NULL""")
        if not one("SELECT name FROM sqlite_master WHERE name='trust_installed'"):
            run("CREATE TABLE trust_installed (version INTEGER NOT NULL)")
            run("INSERT INTO trust_installed VALUES (1)")
            for name, description in (
                ("Certificates", "Certifications and attestations."),
                ("Policies", "Security, privacy and compliance policies."),
                ("Reports", "Audit reports and evidence."),
            ):
                run("INSERT OR IGNORE INTO portal_category(name, description) VALUES (?, ?)", name, description)

        if one("SELECT version FROM trust_installed")["version"] < 2:
            run("UPDATE portal_branding SET accent=? WHERE lower(accent)='#076e65'", CORAL)
            run("UPDATE portal_branding SET ink=? WHERE lower(ink)='#132a42'", BURGUNDY)
            run("UPDATE trust_installed SET version=2")

        if one("SELECT version FROM trust_installed")["version"] < 3:
            run("UPDATE portal_branding SET font='chromatica' WHERE font='sans'")
            run("UPDATE trust_installed SET version=3")
        if one("SELECT version FROM trust_installed")["version"] < 4:
            columns = {column["name"] for column in rows("PRAGMA table_info(portal_outgoingmail)")}
            if "setup_account_id" not in columns:
                run("""ALTER TABLE portal_outgoingmail ADD COLUMN setup_account_id INTEGER
                       REFERENCES portal_account(id) ON DELETE CASCADE""")
            run("UPDATE trust_installed SET version=4")
        if one("SELECT version FROM trust_installed")["version"] < 5:
            migrate_account_request_tiers()
            run("UPDATE trust_installed SET version=5")
        if one("SELECT version FROM trust_installed")["version"] < 6:
            run("ALTER TABLE portal_account ADD COLUMN auth_generation INTEGER NOT NULL DEFAULT 0")
            run("ALTER TABLE portal_outgoingmail ADD COLUMN password_reset INTEGER NOT NULL DEFAULT 0")
            run("UPDATE trust_installed SET version=6")
        run("""INSERT OR IGNORE INTO trust_font(key,name,regular,medium,italic,medium_italic)
               VALUES ('chromatica','Chromatica','','','','')""")


def migrate_account_request_tiers():
    existing = one("SELECT name FROM sqlite_master WHERE type='table' AND name='trust_account_request'")
    if existing:
        run("ALTER TABLE trust_account_request RENAME TO trust_account_request_v4")
    run("""CREATE TABLE trust_account_request (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL COLLATE NOCASE,
            company TEXT NOT NULL,
            tier INTEGER NOT NULL CHECK (tier IN (0, 10, 20, 30)),
            source TEXT NOT NULL CHECK (source IN ('website', 'import')),
            requested TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
            account_id INTEGER REFERENCES portal_account(id) ON DELETE SET NULL
        )""")
    if existing:
        run("""INSERT INTO trust_account_request (id,email,company,tier,source,requested,status,account_id)
               SELECT id,email,company,tier,source,requested,status,account_id FROM trust_account_request_v4""")
        run("DROP TABLE trust_account_request_v4")
    run("""CREATE UNIQUE INDEX trust_pending_account_request
           ON trust_account_request(email) WHERE status='pending'""")
