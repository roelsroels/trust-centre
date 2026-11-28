import csv
import io
import re
import sqlite3
import uuid

import requests
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .account_requests import approve_request, email_address, request_account
from .auth import hash_password, password_error, reset_message, revoke_sessions
from .db import now, one, rows, run, write
from .fonts import STYLES, font_choices, font_url
from .models import BRANDING, TIERS, Tier
from .services import add_revision, discard, invite, publish, prune, stash_file
from .sso import AUTH_METHODS, CALLBACK, client as oidc_client, configuration, valid_issuer
from .views import serve_pdf

manage = Blueprint("manage", __name__, url_prefix="/manage")


@manage.before_request
def staff_only():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["is_staff"] and not g.staff:
        return redirect(url_for("auth.authenticator", next=request.path))
    if not g.staff:
        abort(403)


def selected(query, identifier):
    record = one(query, identifier)
    if not record:
        abort(404)
    return record


def page(title, section, **context):
    return render_template("admin.html", heading=title, section=section, **context)


def form(title, section, fields, *, values=None, action=None, label="Save", obj=None, **context):
    return page(title, section, fields=fields, values=request.form if request.method == "POST" else values or {},
                action=action, label=label, obj=obj, **context)


def field(name, label, kind="text", **options):
    return {"name": name, "label": label, "kind": kind, **options}


@manage.get("/")
def index():
    pending = one("SELECT count(*) AS total FROM portal_accessrequest WHERE handled IS NULL")["total"]
    account_pending = one("SELECT count(*) AS total FROM trust_account_request WHERE status='pending'")["total"]
    return page("Manage Trust Centre", "overview", pending=pending, account_pending=account_pending)


@manage.get("/documents/")
def documents():
    query = request.args.get("q", "").strip()[:150]
    docs = rows("""SELECT d.*,c.name AS category FROM portal_document d JOIN portal_category c ON c.id=d.category_id
                   WHERE instr(lower(d.title || ' ' || d.teaser || ' ' || c.name),lower(?))>0 OR ?=''
                   ORDER BY c.name,d.title""", query, query)
    return page("Documents", "documents", entries=docs, search=query)


def pdf_upload():
    upload = request.files.get("file")
    if not upload or not upload.filename.lower().endswith(".pdf") or upload.stream.read(5) != b"%PDF-":
        raise ValueError("Choose a PDF file.")
    if upload.stream.seek(0, 2) > 25 * 1024 * 1024:
        raise ValueError("Choose a PDF under 25 MB.")
    upload.stream.seek(0)
    label = request.form.get("revision", "").strip()
    if not 1 <= len(label) <= 60:
        raise ValueError("Enter a version label of 1 to 60 characters.")
    return upload, label


@manage.route("/documents/new/", methods=["GET", "POST"])
@manage.route("/documents/<int:identifier>/", methods=["GET", "POST"])
def edit_document(identifier=None):
    doc = selected("SELECT * FROM portal_document WHERE id=?", identifier) if identifier else None
    categories = rows("SELECT id,name FROM portal_category ORDER BY name")
    if request.method == "POST":
        data = request.form
        stored = None
        try:
            title, teaser = data.get("title", "").strip(), data.get("teaser", "").strip()
            category, tier = data.get("category_id", type=int), data.get("minimum_tier", type=int)
            retention = data.get("retain_previous", type=int)
            if not (0 < len(title) <= 150 and 0 < len(teaser) <= 500):
                raise ValueError("Enter a title and a teaser that fits in 500 characters.")
            if tier not in TIERS or category not in {entry["id"] for entry in categories}:
                raise ValueError("Choose a category and access level.")
            if retention is None or not 0 <= retention <= 100:
                raise ValueError("Choose how many previous versions to keep, from 0 to 100.")
            agreement = data.get("agreement", "").strip()
            if len(agreement) > 20000:
                raise ValueError("Keep the agreement under 20,000 characters.")
            if not doc:
                upload, label = pdf_upload()
                stored = stash_file(upload)
            with write():
                values = (title, teaser, category, tier, retention, agreement)
                if doc:
                    run("""UPDATE portal_document SET title=?,teaser=?,category_id=?,minimum_tier=?,
                           retain_previous=?,agreement=? WHERE id=?""", *values, identifier)
                    retired = prune(identifier)
                else:
                    identifier = run("""INSERT INTO portal_document(title,teaser,category_id,minimum_tier,retain_previous,agreement)
                                        VALUES (?,?,?,?,?,?)""", *values).lastrowid
                    add_revision(identifier, label, stored)
                    retired = []
            discard(retired)
            flash("Document settings saved." if doc else "Draft uploaded. Review it below and publish when ready.")
            path = url_for("manage.edit_document", identifier=identifier)
            return redirect(path if doc else path + "#versions")
        except (ValueError, sqlite3.IntegrityError) as exc:
            if stored:
                discard([stored])
            flash(str(exc) if isinstance(exc, ValueError) else "The document could not be saved. Check its category.")
        except Exception:
            if stored:
                discard([stored])
            raise
    fields = [
        field("title", "Title", required=True, maximum=150),
        field("category_id", "Category", "select", choices=[(x["id"], x["name"]) for x in categories], required=True),
        field("teaser", "Public teaser", "textarea", maximum=500, required=True, help="Anyone can read this summary."),
        field("minimum_tier", "Minimum access", "select", choices=TIERS.items(), required=True,
              help="Public allows anyone to open the published PDF, including visitors who are signed out."),
        field("agreement", "Access agreement (optional)", "textarea", maximum=20000, help="Customers must accept these terms before opening the PDF."),
        field("retain_previous", "Previous versions to keep", "number", required=True, minimum=0, maximum=100,
              help="The current version is kept separately. Lowering this number removes older published files."),
    ]
    if not doc:
        fields += [field("revision", "Version label", maximum=60, required=True), field("file", "PDF", "file", required=True)]
    revisions = rows("SELECT * FROM portal_revision WHERE document_id=? ORDER BY created DESC,id DESC", identifier) if doc else []
    acceptances = rows("""SELECT a.*,u.username FROM portal_acceptance a LEFT JOIN portal_account u ON u.id=a.user_id
                          WHERE document_id=? ORDER BY accepted DESC""", identifier) if doc else []
    return form(doc["title"] if doc else "Add a document", "documents", fields, obj=doc,
                values=dict(doc) if doc else {"retain_previous": 3, "minimum_tier": 10, "revision": "1.0"},
                label="Save settings" if doc else "Upload draft", revisions=revisions, acceptances=acceptances)


@manage.post("/documents/<int:identifier>/upload/")
def upload_revision(identifier):
    selected("SELECT id FROM portal_document WHERE id=?", identifier)
    stored = None
    try:
        upload, label = pdf_upload()
        stored = stash_file(upload)
        add_revision(identifier, label, stored)
        flash("Draft uploaded. Publish when it’s ready.")
    except ValueError as error:
        flash(str(error))
    except Exception:
        if stored:
            discard([stored])
        raise
    return redirect(url_for("manage.edit_document", identifier=identifier) + "#versions")


@manage.get("/documents/<int:identifier>/versions/<int:version>/preview/")
def preview_revision(identifier, version):
    revision = one("""SELECT r.file,d.title FROM portal_revision r JOIN portal_document d ON d.id=r.document_id
                      WHERE r.id=? AND d.id=?""", version, identifier)
    if not revision:
        abort(404)
    return serve_pdf(revision["file"], revision["title"], inline=True)


@manage.route("/documents/<int:identifier>/versions/<int:version>/publish/", methods=["GET", "POST"])
def publish_revision(identifier, version):
    if request.method == "POST":
        with write():
            doc = selected("SELECT * FROM portal_document WHERE id=?", identifier)
            rev = one("SELECT * FROM portal_revision WHERE id=? AND document_id=?", version, identifier)
            if not rev:
                abort(404)
            retired = publish(doc, rev)
        discard(retired)
        flash("This version is now live.")
        return redirect(url_for("manage.edit_document", identifier=identifier) + "#versions")
    doc = selected("SELECT * FROM portal_document WHERE id=?", identifier)
    rev = one("SELECT * FROM portal_revision WHERE id=? AND document_id=?", version, identifier)
    if not rev:
        abort(404)
    return page("Publish version", "confirm", text=f"Make version {rev['label']} of {doc['title']} live. "
                "Confirmed subscribers will receive an email if the live version changes.", label="Publish version",
                back=url_for("manage.edit_document", identifier=identifier))


@manage.route("/documents/<int:identifier>/delete/", methods=["GET", "POST"])
def delete_document(identifier):
    doc = selected("SELECT * FROM portal_document WHERE id=?", identifier)
    if request.method == "POST":
        with write():
            revisions = rows("SELECT file FROM portal_revision WHERE document_id=?", identifier)
            run("UPDATE portal_document SET current_id=NULL WHERE id=?", identifier)
            run("DELETE FROM portal_revision WHERE document_id=?", identifier)
            run("DELETE FROM portal_acceptance WHERE document_id=?", identifier)
            run("DELETE FROM portal_accessrequest WHERE document_id=?", identifier)
            run("DELETE FROM portal_document WHERE id=?", identifier)
        discard([item["file"] for item in revisions])
        flash("Document deleted.")
        return redirect(url_for("manage.documents"))
    return page("Delete document", "confirm", text=f"Permanently delete {doc['title']} and all its versions?",
                label="Delete document", back=url_for("manage.edit_document", identifier=identifier))


@manage.get("/categories/")
def categories():
    return page("Categories", "categories", entries=rows("SELECT * FROM portal_category ORDER BY name"))


@manage.route("/categories/new/", methods=["GET", "POST"])
@manage.route("/categories/<int:identifier>/", methods=["GET", "POST"])
def edit_category(identifier=None):
    category = selected("SELECT * FROM portal_category WHERE id=?", identifier) if identifier else None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            if not 1 <= len(name) <= 100 or len(description) > 200:
                raise ValueError("Enter a name and keep the description under 200 characters.")
            if category:
                run("UPDATE portal_category SET name=?,description=? WHERE id=?", name, description, identifier)
            else:
                run("INSERT INTO portal_category (name,description) VALUES (?,?)", name, description)
            flash("Category saved.")
            return redirect(url_for("manage.categories"))
        except (ValueError, sqlite3.IntegrityError):
            flash("Use a unique category name and a description under 200 characters.")
    return form("Edit category" if category else "Add a category", "category_form", [
        field("name", "Name", required=True, maximum=100), field("description", "Description", maximum=200),
    ], values=dict(category) if category else {}, obj=category)


@manage.route("/categories/<int:identifier>/delete/", methods=["GET", "POST"])
def delete_category(identifier):
    category = selected("SELECT * FROM portal_category WHERE id=?", identifier)
    if request.method == "POST":
        try:
            run("DELETE FROM portal_category WHERE id=?", identifier)
            flash("Category deleted.")
        except sqlite3.IntegrityError:
            flash("Move the documents in this category before deleting it.")
        return redirect(url_for("manage.categories"))
    return page("Delete category", "confirm", text=f"Delete {category['name']}?", label="Delete category",
                back=url_for("manage.edit_category", identifier=identifier))


def manageable_account(identifier):
    account = one("""SELECT * FROM portal_account WHERE id=? AND is_superuser=0
                     AND (is_staff=0 OR ?=1)""", identifier, g.user["is_superuser"])
    if not account:
        abort(404)
    return account


@manage.get("/accounts/")
def accounts():
    query = request.args.get("q", "").strip()[:150]
    return page("Accounts", "accounts", search=query, entries=rows("""
        SELECT a.*, COALESCE(r.company,'') AS company FROM portal_account a
        LEFT JOIN trust_account_request r ON r.account_id=a.id
        WHERE a.is_superuser=0 AND (a.is_staff=0 OR ?=1)
        AND (instr(lower(a.username || ' ' || a.email || ' ' || COALESCE(r.company,'')),lower(?))>0 OR ?='')
        ORDER BY a.username
    """, g.user["is_superuser"], query, query))


@manage.get("/accounts/requests/")
def account_requests():
    pending = rows("SELECT * FROM trust_account_request WHERE status='pending' ORDER BY requested,id")
    return page("Account requests", "account_requests", entries=pending)


@manage.route("/accounts/import/", methods=["GET", "POST"])
def import_accounts():
    if request.method == "POST":
        try:
            upload = request.files.get("file")
            if not upload:
                raise ValueError("Choose a CSV file.")
            content = upload.read(256 * 1024 + 1)
            if len(content) > 256 * 1024:
                raise ValueError("Use a CSV smaller than 256 KB.")
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
            if not reader.fieldnames or "email" not in reader.fieldnames:
                raise ValueError("The CSV needs a column called email.")
            records = []
            tiers = {name.lower(): tier for tier, name in TIERS.items()}
            for row in reader:
                if not any(value for key, value in row.items() if key is not None):
                    continue
                email = email_address(row.get("email") or "")
                company = (row.get("company") or "").strip()
                tier_name = (row.get("tier") or "").strip().lower()
                if len(company) > 150 or (tier_name and tier_name not in tiers):
                    raise ValueError(f"Row {reader.line_num}: use a company name up to 150 characters and Public, Basic, Pro, or Expert for tier.")
                records.append((email, company, tiers.get(tier_name, Tier.BASIC)))
            if not records:
                raise ValueError("The CSV contains no accounts.")
            with write():
                created = sum(request_account(email, company, tier=tier, source="import")
                              for email, company, tier in records)
            flash(f"Added {created} account request(s) for review. Skipped {len(records) - created} existing or duplicate entries.")
            return redirect(url_for("manage.account_requests"))
        except (ValueError, csv.Error) as error:
            flash(str(error))
    return form("Import accounts", "account_import", [field("file", "CSV file", "file", required=True,
                help="Use a UTF-8 CSV with an email column. Optional columns are company and tier. The default tier is Basic.")],
                label="Import for review")


@manage.post("/accounts/requests/review/")
def review_accounts():
    selection = {int(value) for value in request.form.getlist("selected") if re.fullmatch(r"[0-9]{1,18}", value)}
    action = request.form.get("action")
    if action not in {"approve", "decline"} or not selection:
        flash("Select the accounts you want to review.")
        return redirect(url_for("manage.account_requests"))
    changed = 0
    with write():
        for identifier in sorted(selection):
            entry = one("SELECT * FROM trust_account_request WHERE id=? AND status='pending'", identifier)
            if not entry:
                continue
            if action == "decline":
                run("UPDATE trust_account_request SET status='declined' WHERE id=?", identifier)
                changed += 1
            else:
                tier = request.form.get(f"tier_{identifier}", entry["tier"], type=int)
                if tier not in TIERS:
                    abort(400)
                changed += approve_request(entry, tier)
    if action == "approve":
        flash(f"Approved {changed} account(s). Password setup emails are queued. Requests for existing accounts were skipped.")
    else:
        flash(f"Declined {changed} account request(s).")
    return redirect(url_for("manage.account_requests"))


@manage.route("/accounts/new/", methods=["GET", "POST"])
@manage.route("/accounts/<int:identifier>/", methods=["GET", "POST"])
def edit_account(identifier=None):
    account = manageable_account(identifier) if identifier else None
    if request.method == "POST":
        data = request.form
        username, tier = data.get("username", "").strip(), data.get("tier", type=int)
        try:
            email = email_address(data.get("email", ""))
            if not re.fullmatch(r"[\w.@+-]{1,150}", username) or tier not in TIERS:
                raise ValueError("Use a valid username and select a customer access level.")
            staff = int(data.get("is_staff") == "on" and g.user["is_superuser"])
            active = int(data.get("is_active") == "on")
            if account:
                with write():
                    latest = manageable_account(identifier)
                    run("""UPDATE portal_account SET username=?,email=?,tier=?,is_active=?,is_staff=?
                           WHERE id=?""", username, email, tier, active, staff, identifier)
                    if (latest["is_staff"], latest["is_active"], latest["email"]) != (staff, active, email):
                        run("UPDATE portal_account SET auth_generation=auth_generation+1 WHERE id=?", identifier)
                        revoke_sessions(identifier)
                    if latest["is_staff"] and not staff:
                        run("DELETE FROM otp_totp_totpdevice WHERE user_id=?", identifier)
            else:
                password = data.get("password", "")
                if error := password_error(password):
                    raise ValueError(error)
                identifier = run("""INSERT INTO portal_account(username,email,password,tier,is_staff,is_active,
                                   is_superuser,first_name,last_name,date_joined) VALUES (?,?,?,?,?,?,0,'','',?)""",
                                 username, email, hash_password(password), tier, staff, active, now()).lastrowid
            flash("Account saved.")
            return redirect(url_for("manage.edit_account", identifier=identifier))
        except sqlite3.IntegrityError:
            flash("That username or email address is already in use.")
        except ValueError as error:
            flash(str(error))
    fields = [
        field("username", "Username", required=True, maximum=150),
        field("email", "Email address", "email", required=True, maximum=254),
        field("tier", "Customer access level", "select", choices=TIERS.items(), required=True),
        field("is_active", "Account enabled", "checkbox"),
    ]
    if g.user["is_superuser"]:
        fields.append(field("is_staff", "Administrator", "checkbox", help="An administrator needs an authenticator before opening Manage."))
    if not account:
        fields.append(field("password", "Temporary password", "password", required=True, minimum=12, maximum=1024))
    return form("Edit account" if account else "Add an account", "account_form", fields, obj=account,
                values=dict(account) if account else {"is_active": True, "tier": 10})


@manage.post("/accounts/<int:identifier>/reset/")
def reset_account(identifier):
    account = manageable_account(identifier)
    if not account["is_active"]:
        flash("Enable the account before sending a reset email.")
    else:
        try:
            reset_message(account)
        except (OSError, ValueError):
            flash("Could not send the reset email. Check the SMTP settings.")
        else:
            flash("Password reset email sent.")
    return redirect(url_for("manage.edit_account", identifier=identifier))


@manage.route("/accounts/<int:identifier>/delete/", methods=["GET", "POST"])
def delete_account(identifier):
    account = manageable_account(identifier)
    if request.method == "POST":
        with write():
            manageable_account(identifier)
            run("UPDATE portal_acceptance SET user_id=NULL WHERE user_id=?", identifier)
            run("DELETE FROM portal_accessrequest WHERE user_id=?", identifier)
            run("DELETE FROM otp_totp_totpdevice WHERE user_id=?", identifier)
            for table, column in (("django_admin_log", "user_id"), ("portal_account_groups", "account_id"),
                                  ("portal_account_user_permissions", "account_id")):
                if one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", table):
                    run(f"DELETE FROM {table} WHERE {column}=?", identifier)
            run("DELETE FROM trust_account_request WHERE account_id=?", identifier)
            run("DELETE FROM portal_account WHERE id=?", identifier)
        flash("Account deleted.")
        return redirect(url_for("manage.accounts"))
    return page("Delete account", "confirm", text=f"Permanently delete {account['username']}?",
                label="Delete account", back=url_for("manage.edit_account", identifier=identifier))


@manage.get("/requests/")
def requests():
    return page("Access requests", "requests", entries=rows("""
        SELECT r.*,a.username,a.email,d.title FROM portal_accessrequest r
        JOIN portal_account a ON a.id=r.user_id JOIN portal_document d ON d.id=r.document_id
        ORDER BY r.handled IS NOT NULL,r.requested DESC
    """))


@manage.post("/requests/<int:identifier>/handle/")
def handle_request(identifier):
    run("UPDATE portal_accessrequest SET handled=? WHERE id=? AND handled IS NULL", now(), identifier)
    flash("Request marked as handled.")
    return redirect(url_for("manage.requests"))


@manage.get("/subscribers/")
def subscribers():
    query = request.args.get("q", "").strip()[:150]
    return page("Email subscribers", "subscribers", search=query, entries=rows("""
        SELECT * FROM portal_subscriber WHERE instr(lower(email),lower(?))>0 OR ?='' ORDER BY email
    """, query, query))


@manage.route("/subscribers/import/", methods=["GET", "POST"])
def import_subscribers():
    if request.method == "POST":
        try:
            upload = request.files.get("file")
            if not upload:
                raise ValueError("Choose a CSV file.")
            content = upload.read(256 * 1024 + 1)
            if len(content) > 256 * 1024:
                raise ValueError("Use a CSV smaller than 256 KB.")
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
            if not reader.fieldnames or "email" not in reader.fieldnames:
                raise ValueError("The CSV needs a column called email.")
            emails = {email_address(row["email"].strip()).lower() for row in reader if (row.get("email") or "").strip()}
            if not emails:
                raise ValueError("The CSV contains no email addresses.")
            with write():
                count = sum(invite(email) for email in sorted(emails))
            flash(f"Queued {count} confirmation email(s).")
            return redirect(url_for("manage.subscribers"))
        except (ValueError, csv.Error) as error:
            flash(str(error))
    return form("Import subscribers", "subscriber_form", [field("file", "CSV file", "file", required=True,
                help="Use a UTF-8 CSV with an email column. New recipients receive an opt-in link.")], label="Import addresses")


@manage.route("/settings/", methods=["GET", "POST"])
def settings():
    existing = one("SELECT * FROM portal_branding ORDER BY id LIMIT 1")
    if request.method == "POST":
        data = request.form
        values = {key: data.get(key, "").strip() for key in BRANDING}
        values["show_demo_team"] = int(data.get("show_demo_team") == "on")
        try:
            if values["access_inbox"]:
                values["access_inbox"] = email_address(values["access_inbox"])
            if not 1 <= len(values["name"]) <= 100:
                raise ValueError("Enter a site name up to 100 characters long.")
            if values["logo_url"]:
                logo = urlsplit(values["logo_url"])
                if logo.scheme != "https" or not logo.hostname or len(values["logo_url"]) > 200:
                    raise ValueError("Enter a valid HTTPS logo URL up to 200 characters long.")
            if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", values[key]) for key in ("accent", "ink")):
                raise ValueError("Enter six-digit hex colours.")
            if values["font"] not in dict(font_choices()):
                raise ValueError("Choose a font.")
            with write():
                current = one("SELECT id FROM portal_branding ORDER BY id LIMIT 1")
                if current:
                    run("UPDATE portal_branding SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                        *values.values(), current["id"])
                else:
                    run("INSERT INTO portal_branding (" + ",".join(values) + ") VALUES (" +
                        ",".join("?" for _ in values) + ")", *values.values())
            flash("Portal settings saved.")
            return redirect(url_for("manage.settings"))
        except ValueError as error:
            flash(str(error))
    return form("Portal settings", "settings", [
        field("name", "Site name", required=True, maximum=100), field("introduction", "Homepage introduction", "textarea"),
        field("team", "Compliance team description", "textarea"), field("show_demo_team", "Show fictional team portraits", "checkbox"),
        field("logo_url", "Logo URL", "url", maximum=200), field("accent", "Accent colour", "color"),
        field("ink", "Text colour", "color"), field("font", "Font", "select",
              choices=font_choices()),
        field("access_inbox", "Email for access requests", "email", maximum=254),
    ], values=dict(existing) if existing else BRANDING)


@manage.get("/settings/fonts/")
def fonts():
    return page("Webfonts", "fonts", entries=rows("SELECT * FROM trust_font ORDER BY key<>'chromatica',name"))


@manage.route("/settings/fonts/new/", methods=["GET", "POST"])
@manage.route("/settings/fonts/<key>/", methods=["GET", "POST"])
def edit_font(key=None):
    entry = selected("SELECT * FROM trust_font WHERE key=?", key) if key else None
    chromatica = key == "chromatica"
    if request.method == "POST":
        try:
            name = "Chromatica" if chromatica else request.form.get("name", "").strip()
            if not 1 <= len(name) <= 80:
                raise ValueError("Enter a font family name up to 80 characters long.")
            sources = [font_url(request.form.get(field_name, "").strip()) for field_name, *_ in STYLES]
            if not chromatica and not sources[0]:
                raise ValueError("Add a Regular WOFF2 URL for this family.")
            if entry:
                run("""UPDATE trust_font SET name=?,regular=?,medium=?,italic=?,medium_italic=? WHERE key=?""",
                    name, *sources, key)
            else:
                run("INSERT INTO trust_font(key,name,regular,medium,italic,medium_italic) VALUES (?,?,?,?,?,?)",
                    uuid.uuid4().hex, name, *sources)
            flash("Webfont saved. Select the family in Portal settings to use it.")
            return redirect(url_for("manage.fonts"))
        except ValueError as error:
            flash(str(error))
        except sqlite3.IntegrityError:
            flash("A font family with that name already exists.")
    fields = [] if chromatica else [field("name", "Font family name", required=True, maximum=80)]
    fields += [field(field_name, label + " WOFF2 URL", "url", maximum=2000,
                     required=field_name == "regular" and not chromatica)
               for field_name, label, *_ in STYLES]
    return form("Edit webfont" if entry else "Add a webfont", "font_form", fields,
                values=dict(entry) if entry else {}, label="Save webfont")


@manage.route("/settings/sso/", methods=["GET", "POST"])
def sso_settings():
    if not g.user["is_superuser"]:
        abort(403)
    existing = configuration()
    secret_set = bool(current_app.config["SSO_CLIENT_SECRET"])
    callback = current_app.config["BASE_URL"] + CALLBACK
    if request.method == "POST":
        values = {
            "enabled": int(request.form.get("enabled") == "on"),
            "label": request.form.get("label", "").strip(),
            "issuer": request.form.get("issuer", "").strip(),
            "client_id": request.form.get("client_id", "").strip(),
            "token_auth": request.form.get("token_auth", ""),
        }
        try:
            if not 1 <= len(values["label"]) <= 60:
                raise ValueError("Enter a provider name of up to 60 characters.")
            if values["issuer"] and (len(values["issuer"]) > 500 or not valid_issuer(values["issuer"])):
                raise ValueError("Enter the provider’s HTTPS OpenID Connect issuer URL.")
            if values["token_auth"] not in dict(AUTH_METHODS) or len(values["client_id"]) > 250:
                raise ValueError("Check the client ID and token authentication method.")
            if values["enabled"]:
                if not (values["issuer"] and values["client_id"] and secret_set and callback.startswith("https://")):
                    raise ValueError("Set the HTTPS site URL, issuer, client ID and SSO_CLIENT_SECRET before enabling.")
                oidc_client(values)
            run("""INSERT INTO trust_sso_config(id,enabled,label,issuer,client_id,token_auth) VALUES (1,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,label=excluded.label,
                       issuer=excluded.issuer,client_id=excluded.client_id,token_auth=excluded.token_auth""",
                *values.values())
            flash("Single sign-on settings saved.")
            return redirect(url_for("manage.sso_settings"))
        except requests.RequestException:
            flash("Could not reach the provider’s discovery URL over HTTPS.")
        except ValueError as error:
            flash(str(error))
    return form("Single sign-on settings", "sso_settings", [
        field("enabled", "Enable single sign-on", "checkbox"),
        field("label", "Provider display name", maximum=60, required=True),
        field("issuer", "OpenID Connect issuer URL", "url", maximum=500),
        field("client_id", "Client ID", maximum=250),
        field("token_auth", "Token endpoint authentication", "select", choices=AUTH_METHODS, required=True),
    ], values=dict(existing), callback=callback, secret_set=secret_set)
