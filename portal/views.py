import hashlib
import uuid

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, session, url_for

from .db import now, one, rows, run, write
from .demo.team import TEAM
from .models import BRANDING, TIERS, Tier
from .services import absolute, enqueue, invite, private_file

public = Blueprint("public", __name__)


def branding():
    return one("SELECT * FROM portal_branding ORDER BY id LIMIT 1") or BRANDING


def document(pk):
    item = one("""SELECT d.*,c.name AS category FROM portal_document d JOIN portal_category c ON c.id=d.category_id
                  WHERE d.id=? AND d.current_id IS NOT NULL""", pk)
    if not item:
        abort(404)
    return item


def allowed(doc):
    return doc["minimum_tier"] == Tier.PUBLIC or bool(
        g.user and (g.staff or g.user["tier"] >= doc["minimum_tier"])
    )


def digest(doc):
    return hashlib.sha256(doc["agreement"].encode()).hexdigest()


def accepted(doc):
    if not doc["agreement"]:
        return True
    signature = digest(doc)
    if not g.user:
        return session.get("public_agreements", {}).get(str(doc["id"])) == signature
    return one("SELECT id FROM portal_acceptance WHERE user_id=? AND document_id=? AND digest=?",
               g.user["id"], doc["id"], signature) is not None


@public.get("/")
def home():
    return render_template("home.html", categories=rows("SELECT * FROM portal_category ORDER BY name"),
                           demo_team=TEAM if branding()["show_demo_team"] else ())


@public.get("/library/")
def library():
    name = request.args.get("category")
    categories = rows("SELECT * FROM portal_category ORDER BY name")
    if name and not any(category["name"] == name for category in categories):
        abort(404)
    docs = rows("""SELECT d.*,c.name AS category FROM portal_document d JOIN portal_category c ON c.id=d.category_id
                   WHERE d.current_id IS NOT NULL AND (? IS NULL OR c.name=?) ORDER BY c.name,d.title""", name, name)
    return render_template("library.html", categories=categories, category=name,
                           rows=[(doc, allowed(doc)) for doc in docs])


@public.get("/documents/<int:pk>/")
def document_page(pk):
    doc = document(pk)
    access = allowed(doc)
    agreed = access and bool(accepted(doc))
    current = one("SELECT * FROM portal_revision WHERE id=?", doc["current_id"]) if agreed else None
    versions = rows("""SELECT * FROM portal_revision WHERE document_id=? AND published IS NOT NULL AND id<>?
                       ORDER BY published DESC,id DESC""", pk, doc["current_id"]) if agreed else []
    return render_template("document.html", item=doc, current=current, versions=versions,
                           access=access, accepted=agreed, digest=digest(doc) if access else "")


@public.post("/documents/<int:pk>/agreement/")
def accept(pk):
    with write():
        doc = document(pk)
        if not allowed(doc):
            abort(403)
        signature = digest(doc)
        if doc["agreement"] and request.form.get("agree") == "yes" and request.form.get("digest") == signature:
            if g.user:
                run("""INSERT OR IGNORE INTO portal_acceptance(user_id,document_id,digest,text,accepted)
                       VALUES (?,?,?,?,?)""", g.user["id"], pk, signature, doc["agreement"], now())
            else:
                agreements = session.get("public_agreements", {})
                agreements.pop(str(pk), None)
                agreements[str(pk)] = signature
                # Keep the signed session cookie bounded when many documents use agreements.
                session["public_agreements"] = dict(list(agreements.items())[-16:])
        else:
            flash("Review and confirm the current agreement.")
    return redirect(url_for("public.document_page", pk=pk))


@public.get("/documents/<int:pk>/files/<int:revision>/")
def download(pk, revision):
    doc = document(pk)
    if not allowed(doc):
        if not g.user:
            return redirect(url_for("auth.login", next=request.path))
        abort(403)
    if not accepted(doc):
        if not g.user:
            return redirect(url_for("public.document_page", pk=pk))
        abort(403)
    item = one("SELECT * FROM portal_revision WHERE id=? AND document_id=? AND published IS NOT NULL", revision, pk)
    if not item:
        abort(404)
    return serve_pdf(item["file"], doc["title"], inline=request.args.get("view") == "1")


def serve_pdf(name, title, *, inline):
    path = private_file(name)
    try:
        response = send_file(path, mimetype="application/pdf", download_name=title + ".pdf",
                             as_attachment=not inline, etag=False)
    except FileNotFoundError:
        abort(404)
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@public.post("/documents/<int:pk>/request/")
def request_access(pk):
    if not g.user:
        abort(403)
    with write():
        doc = document(pk)
        if allowed(doc):
            return redirect(url_for("public.document_page", pk=pk))
        inbox = branding()["access_inbox"]
        if not inbox:
            flash("Access requests are currently unavailable.")
        else:
            created = run("""INSERT OR IGNORE INTO portal_accessrequest(user_id,document_id,tier,requested,handled)
                             VALUES (?,?,?,?,NULL)""", g.user["id"], pk, doc["minimum_tier"], now()).rowcount
            if created:
                enqueue(inbox, "Trust Centre access request",
                        f"{g.user['username']} ({g.user['email']}) requested {TIERS[doc['minimum_tier']]} access "
                        f"for {doc['title']}.\n\n{absolute(url_for('public.document_page', pk=pk))}")
            flash("Your access request has been submitted.")
    return redirect(url_for("public.document_page", pk=pk))


@public.route("/notifications/", methods=["GET", "POST"])
def notifications():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    email = g.user["email"].strip().lower()
    if request.method == "POST":
        with write():
            invite(email, manual=True)
        flash("If confirmation is needed, we’ll send a link to your email.")
        return redirect(url_for("public.notifications"))
    subscriber = one("SELECT * FROM portal_subscriber WHERE email=?", email)
    return render_template("notifications.html", subscriber=subscriber)


@public.route("/subscription/<uuid:token>/", methods=["GET", "POST"])
def subscription(token):
    subscriber = one("SELECT * FROM portal_subscriber WHERE token=?", token.hex)
    if not subscriber:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "confirm":
            changed = run("UPDATE portal_subscriber SET status='active',changed=? WHERE token=? AND status='pending'",
                          now(), token.hex).rowcount
            if changed:
                flash("You’re subscribed to document updates.")
        elif action == "off":
            changed = run("UPDATE portal_subscriber SET status='off',token=?,changed=? WHERE token=?",
                          uuid.uuid4().hex, now(), token.hex).rowcount
            if changed:
                flash("You’ve unsubscribed from document updates.")
        return redirect("/")
    return render_template("subscription.html", subscriber=subscriber)
