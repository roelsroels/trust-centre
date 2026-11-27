import hashlib
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from .models import Acceptance, AccessRequest, Branding, Category, Document, OutgoingMail, Revision, Subscriber
from .services import invite, url


def branding(request):
    return {"brand": Branding.objects.first() or Branding()}


@require_safe
def home(request):
    return render(request, "home.html", {"categories": Category.objects.all()})


def allowed(user, document):
    return user.is_authenticated and (
        user.tier >= document.minimum_tier or (user.is_staff and user.is_verified())
    )


@never_cache
@require_safe
def library(request):
    categories = Category.objects.all()
    category = None
    documents = Document.objects.filter(current__isnull=False).select_related("category")
    if request.GET.get("category"):
        category = get_object_or_404(categories, name=request.GET["category"])
        documents = documents.filter(category=category)
    return render(request, "library.html", {
        "categories": categories, "category": category,
        "rows": [(doc, allowed(request.user, doc)) for doc in documents.order_by("category__name", "title")],
    })


def agreement_digest(document):
    return hashlib.sha256(document.agreement.encode()).hexdigest()


def accepted(user, document):
    return not document.agreement or Acceptance.objects.filter(
        user=user, document=document, digest=agreement_digest(document),
    ).exists()


def published_document(pk):
    return get_object_or_404(Document.objects.select_related("current", "category"), pk=pk, current__isnull=False)


@never_cache
@require_safe
def document_page(request, pk):
    document = published_document(pk)
    access = allowed(request.user, document)
    has_agreement = access and accepted(request.user, document)
    return render(request, "document.html", {
        "item": document, "access": access, "accepted": has_agreement,
        "digest": agreement_digest(document) if access else "",
        "versions": list(document.revisions.filter(published__isnull=False).exclude(pk=document.current_id))
        if has_agreement else [],
    })


@never_cache
@login_required
@require_POST
@transaction.atomic
def accept_agreement(request, pk):
    document = published_document(pk)
    if not allowed(request.user, document):
        return HttpResponseForbidden()
    digest = agreement_digest(document)
    if document.agreement and request.POST.get("agree") == "yes" and request.POST.get("digest") == digest:
        Acceptance.objects.get_or_create(
            user=request.user, document=document, digest=digest, defaults={"text": document.agreement},
        )
    else:
        messages.error(request, "Review and confirm the current agreement.")
    return redirect("document", pk)


@never_cache
@login_required
@require_safe
def download(request, pk, revision):
    document = published_document(pk)
    if not allowed(request.user, document) or not accepted(request.user, document):
        return HttpResponseForbidden()
    item = get_object_or_404(Revision, pk=revision, document=document, published__isnull=False)
    try:
        pdf = item.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    response = FileResponse(pdf, as_attachment=request.GET.get("view") != "1",
                            filename=f"{document.title}.pdf", content_type="application/pdf")
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@never_cache
@login_required
@require_POST
@transaction.atomic
def request_access(request, pk):
    document = published_document(pk)
    if allowed(request.user, document):
        return redirect("document", pk)
    brand = branding(request)["brand"]
    if not brand.access_inbox:
        messages.error(request, "Access requests are currently unavailable.")
    else:
        _, created = AccessRequest.objects.get_or_create(
            user=request.user, document=document, tier=document.minimum_tier, handled=None,
        )
        if created:
            OutgoingMail.objects.create(
                email=brand.access_inbox, subject="Trust Centre access request",
                body=f"{request.user.get_username()} ({request.user.email}) requested "
                     f"{document.get_minimum_tier_display()} access for {document.title}.\n\n{url('document', pk)}",
            )
        messages.success(request, "Your access request has been submitted.")
    return redirect("document", pk)


@never_cache
@login_required
def notifications(request):
    email = request.user.email.strip().lower()
    subscriber = Subscriber.objects.filter(email=email).first()
    if request.method == "POST":
        invite(email, manual=True)
        messages.success(request, "If confirmation is needed, we’ll send a link to your email.")
        return redirect("notifications")
    return render(request, "notifications.html", {"subscriber": subscriber})


@never_cache
@transaction.atomic
def subscription(request, token):
    subscriber = get_object_or_404(Subscriber, token=token)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm" and subscriber.status == "pending":
            subscriber.status = "active"
            subscriber.save()
            messages.success(request, "You’re subscribed to document updates.")
        elif action == "off":
            subscriber.status = "off"
            subscriber.token = uuid.uuid4()
            subscriber.save()
            messages.success(request, "You’ve unsubscribed from document updates.")
        return redirect("home")
    return render(request, "subscription.html", {"subscriber": subscriber})
