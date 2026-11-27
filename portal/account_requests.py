import re
import secrets

from email_validator import validate_email

from .db import now, one, run
from .models import Tier


def email_address(value):
    return validate_email(value.strip(), check_deliverability=False).normalized


def request_account(email, company, *, tier=Tier.BASIC, source="website"):
    if one("SELECT id FROM portal_account WHERE email=? COLLATE NOCASE", email):
        return False
    return bool(run("""INSERT OR IGNORE INTO trust_account_request(email,company,tier,source,requested)
                       VALUES (?,?,?,?,?)""", email, company, tier, source, now()).rowcount)


def approve_request(entry, tier):
    if one("SELECT id FROM portal_account WHERE email=? COLLATE NOCASE", entry["email"]):
        return False
    username = entry["email"]
    if not re.fullmatch(r"[\w.@+-]{1,150}", username) or one(
        "SELECT id FROM portal_account WHERE username=? COLLATE NOCASE", username
    ):
        username = "customer-" + secrets.token_hex(8)
    account_id = run("""INSERT INTO portal_account(username,email,password,tier,is_staff,is_active,is_superuser,
                        first_name,last_name,date_joined) VALUES (?,?,?,?,0,1,0,'','',?)""",
                     username, entry["email"], "!setup:" + secrets.token_urlsafe(24), tier, now()).lastrowid
    run("UPDATE trust_account_request SET status='approved',account_id=? WHERE id=?", account_id, entry["id"])
    run("""INSERT INTO portal_outgoingmail(email,subject,body,expected_status,attempts,next_attempt,setup_account_id)
           VALUES (?,'Set up your Trust Centre account','','',0,?,?)""", entry["email"], now(), account_id)
    return True
