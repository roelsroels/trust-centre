import base64
import os

import click
import pyotp
from flask import current_app
from werkzeug.datastructures import FileStorage

from .account_requests import email_address
from .auth import hash_password, password_error
from .db import initialize, now, one, run, write
from .demo.catalog import COMPANY, SAMPLES
from .models import BURGUNDY, CORAL
from .services import add_revision, discard, process_mail, stash_file


def register_commands(app):
    @app.cli.command("init_db")
    def init_db():
        initialize()

    @app.cli.command("createsuperuser")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_owner(username, email, password):
        if error := password_error(password):
            raise click.ClickException(error)
        try:
            email = email_address(email)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        if one("SELECT id FROM portal_account WHERE username=? OR email=?", username, email):
            raise click.ClickException("That username or email is already in use.")
        run("""INSERT INTO portal_account(username,email,password,tier,is_staff,is_active,is_superuser,first_name,last_name,date_joined)
               VALUES (?,?,?,30,1,1,1,'','',?)""", username, email, hash_password(password), now())
        click.echo("Owner created.")

    @app.cli.command("enable_totp")
    @click.argument("username")
    def enable_totp(username):
        with write():
            account = one("SELECT id FROM portal_account WHERE username=? AND is_staff=1 AND is_active=1", username)
            if not account:
                raise click.ClickException("No active Admin or Owner found.")
            key = os.urandom(20)
            run("DELETE FROM otp_totp_totpdevice WHERE user_id=?", account["id"])
            run("""INSERT INTO otp_totp_totpdevice(name,confirmed,key,step,t0,digits,tolerance,drift,last_t,
                   user_id,throttling_failure_count,created_at) VALUES ('Authenticator',1,?,30,0,6,1,0,-1,?,0,?)""",
                key.hex(), account["id"], now())
        totp = pyotp.TOTP(base64.b32encode(key).decode())
        click.echo("Add this private TOTP setup URI to your authenticator:")
        click.echo(totp.provisioning_uri(name=username, issuer_name="Trust Centre"))

    @app.cli.command("seed_demo")
    def seed_demo():
        source_dir = current_app.config["BASE_DIR"] / "output" / "pdf"
        if any(not (source_dir / f"{sample.slug}.pdf").is_file() for sample in SAMPLES):
            raise click.ClickException("The bundled demo PDFs are missing.")
        stored = []
        try:
            with write():
                brand = one("SELECT * FROM portal_branding ORDER BY id LIMIT 1")
                if brand and brand["show_demo_team"] and brand["name"] == COMPANY:
                    click.echo("The demo is already installed.")
                    return
                if brand or one("SELECT id FROM portal_document LIMIT 1"):
                    raise click.ClickException("Use a fresh portal database to install the demo.")
                run("""INSERT INTO portal_branding(name,introduction,team,logo_url,accent,ink,font,access_inbox,show_demo_team)
                       VALUES (?,?,?,'',?,?,'chromatica','',1)""", COMPANY,
                    "Explore how our fictional workflow platform handles security, privacy, and compliance.",
                    "Meet the people behind our security, audit, and privacy programmes.", CORAL, BURGUNDY)
                for sample in SAMPLES:
                    run("INSERT OR IGNORE INTO portal_category(name,description) VALUES (?, '')", sample.category)
                    category = one("SELECT id FROM portal_category WHERE name=?", sample.category)
                    doc = run("""INSERT INTO portal_document(title,teaser,minimum_tier,agreement,retain_previous,category_id)
                                 VALUES (?,?,?,?,3,?)""",
                              sample.title, sample.teaser, sample.tier, sample.agreement, category["id"]).lastrowid
                    with (source_dir / f"{sample.slug}.pdf").open("rb") as source:
                        stored.append(stash_file(FileStorage(source)))
                    version = next((part.removeprefix("Revision ") for part in sample.reference.split(" · ")
                                    if part.startswith("Revision ")), "1.0")
                    revision = add_revision(doc, version, stored[-1], now())
                    run("UPDATE portal_document SET current_id=? WHERE id=?", revision, doc)
        except BaseException:
            discard(stored)
            raise
        click.echo("Installed six fictional documents and the demo homepage.")

    @app.cli.command("send_queue")
    @click.option("--watch", is_flag=True)
    def send_queue(watch):
        # Either service may start first when Compose recreates the stack.
        initialize()
        process_mail(watch)
