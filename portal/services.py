import smtplib
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.errors import NonASCIILocalPartDefect
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from urllib.parse import urlsplit

from flask import current_app

from .db import now, one, rows, run, write


def absolute(path):
    return current_app.config["BASE_URL"] + path


def private_file(name):
    root = (current_app.config["DATA_DIR"] / "private").resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid private file path")
    return path


def stash_file(file):
    name = f"documents/{uuid.uuid4().hex}.pdf"
    path = private_file(name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with path.open("xb") as target:
            file.save(target)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return name


def add_revision(document, label, name, published=None):
    return run("INSERT INTO portal_revision (document_id, label, file, created, published) VALUES (?,?,?,?,?)",
               document, label, name, now(), published).lastrowid


def discard(files):
    for name in files:
        try:
            private_file(name).unlink(missing_ok=True)
        except OSError:
            current_app.logger.warning("Could not remove a retired PDF", exc_info=True)


def enqueue(email, subject, body, subscriber=None):
    run("""INSERT INTO portal_outgoingmail
           (email, subject, body, subscriber_id, expected_status, subscription_token, attempts, next_attempt)
           VALUES (?,?,?,?,?,?,0,?)""",
        email, subject, body, subscriber["id"] if subscriber else None,
        subscriber["status"] if subscriber else "", subscriber["token"] if subscriber else None, now())


def invite(email, manual=False):
    email = email.strip().lower()
    subscriber = one("SELECT * FROM portal_subscriber WHERE email=?", email)
    if subscriber and not (manual and subscriber["status"] == "off"):
        return False
    token = uuid.uuid4().hex
    run("""INSERT INTO portal_subscriber(email, token, status, changed) VALUES (?,?,'pending',?)
           ON CONFLICT(email) DO UPDATE SET token=excluded.token, status='pending', changed=excluded.changed""",
        email, token, now())
    subscriber = one("SELECT * FROM portal_subscriber WHERE email=?", email)
    link = absolute(f"/subscription/{uuid.UUID(hex=token)}/")
    enqueue(email, "Confirm Trust Centre notifications",
            f"Confirm your subscription at {link}\n\nIf you did not request this, you can ignore this email.", subscriber)
    return True


def prune(document_id):
    doc = one("SELECT current_id,retain_previous FROM portal_document WHERE id=?", document_id)
    old = rows("""SELECT id,file FROM portal_revision WHERE document_id=? AND id IS NOT ?
                  AND published IS NOT NULL ORDER BY published DESC,id DESC LIMIT -1 OFFSET ?""",
               document_id, doc["current_id"], doc["retain_previous"])
    for rev in old:
        run("DELETE FROM portal_revision WHERE id=?", rev["id"])
    return [rev["file"] for rev in old]


def publish(doc, revision):
    if doc["current_id"] == revision["id"]:
        return []
    update = doc["current_id"] is not None or revision["published"] is not None
    run("UPDATE portal_revision SET published=? WHERE id=?", now(), revision["id"])
    run("UPDATE portal_document SET current_id=? WHERE id=?", revision["id"], doc["id"])
    removed = prune(doc["id"])
    link = absolute(f"/documents/{doc['id']}/")
    subject = "Updated Trust Centre document" if update else "New Trust Centre document"
    for subscriber in rows("SELECT * FROM portal_subscriber WHERE status='active'"):
        stop = absolute(f"/subscription/{uuid.UUID(hex=subscriber['token'])}/")
        enqueue(subscriber["email"], subject, f"{doc['title']}\n\n{doc['teaser']}\n\n"
                f"Open in the Trust Centre: {link}\n\nUnsubscribe: {stop}", subscriber)
    return removed


def send(email, subject, body):
    config = current_app.config
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = config["FROM_EMAIL"]
    message["To"] = email
    for field in ("From", "To"):
        header = message[field]
        if len(header.addresses) != 1 or any(
            not isinstance(defect, NonASCIILocalPartDefect) for defect in header.defects
        ):
            raise ValueError(f"{field} must contain a single valid mailbox.")
        address = header.addresses[0]
        if not address.username or not address.domain:
            raise ValueError(f"{field} must contain a complete email address.")
        # International domain names can use their ASCII representation over SMTP.
        message.replace_header(field, Address(
            display_name=address.display_name, username=address.username, domain=address.domain.encode("idna").decode(),
        ))
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    host = urlsplit(config["BASE_URL"]).hostname.encode("idna").decode()
    message["Message-ID"] = make_msgid(domain=host)
    message.set_content(body, charset="utf-8", cte="base64")
    if config["MAIL_CONSOLE"]:
        print(message, flush=True)
        return
    tls = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL if config["SMTP_SSL"] else smtplib.SMTP
    options = {"context": tls} if config["SMTP_SSL"] else {}
    with smtp(config["SMTP_HOST"], config["SMTP_PORT"], timeout=15, **options) as connection:
        if config["SMTP_TLS"]:
            connection.starttls(context=tls)
        if config["SMTP_USER"] or config["SMTP_PASSWORD"]:
            connection.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        connection.send_message(
            message, from_addr=message["From"].addresses[0].addr_spec,
            to_addrs=[message["To"].addresses[0].addr_spec],
        )


def process_mail(watch):
    while True:
        for mail in rows("SELECT * FROM portal_outgoingmail WHERE next_attempt<=? ORDER BY next_attempt,id LIMIT 50", now()):
            if mail["subscriber_id"]:
                sub = one("SELECT * FROM portal_subscriber WHERE id=?", mail["subscriber_id"])
                if not sub or sub["token"] != mail["subscription_token"] or sub["status"] != mail["expected_status"]:
                    run("DELETE FROM portal_outgoingmail WHERE id=?", mail["id"])
                    continue
            try:
                send(mail["email"], mail["subject"], mail["body"])
            except (OSError, ValueError) as error:
                attempts = mail["attempts"] + 1
                next_try = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=min(60, 2 ** min(attempts, 6)))
                run("UPDATE portal_outgoingmail SET attempts=?,next_attempt=? WHERE id=?",
                    attempts, next_try.isoformat(sep=" "), mail["id"])
                current_app.logger.warning("Mail %s failed (%s); queued for retry", mail["id"], type(error).__name__)
            else:
                run("DELETE FROM portal_outgoingmail WHERE id=?", mail["id"])
        if not watch:
            return
        time.sleep(5)
