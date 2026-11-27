import uuid

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Document, OutgoingMail, Revision, Subscriber


def url(name, *args):
    return settings.BASE_URL + reverse(name, args=args)


def mail_for(subscriber, subject, body):
    return OutgoingMail(
        email=subscriber.email, subject=subject, body=body, subscriber=subscriber,
        expected_status=subscriber.status, subscription_token=subscriber.token,
    )


@transaction.atomic
def invite(email, *, manual=False):
    subscriber, created = Subscriber.objects.get_or_create(email=email.strip().lower())
    if not created and not (manual and subscriber.status == "off"):
        return
    subscriber.status = "pending"
    subscriber.token = uuid.uuid4()
    subscriber.save()
    link = url("subscription", subscriber.token)
    mail_for(subscriber, "Confirm Trust Centre notifications",
             f"Confirm your subscription at {link}\n\nIf you did not request this, you can ignore this email.").save()


@transaction.atomic
def prune(document):
    previous = document.revisions.filter(published__isnull=False).exclude(pk=document.current_id)
    keep = list(previous.order_by("-published", "-pk").values_list("pk", flat=True)[:document.retain_previous])
    previous.exclude(pk__in=keep).delete()


@transaction.atomic
def publish(revision_id):
    revision = Revision.objects.select_related("document").get(pk=revision_id)
    document = revision.document
    if document.current_id == revision.pk:
        return False
    update = document.current_id is not None or revision.published is not None
    revision.published = timezone.now()
    revision.save(update_fields=["published"])
    document.current = revision
    document.save(update_fields=["current"])
    prune(document)
    link = url("document", document.pk)
    subject = "Updated Trust Centre document" if update else "New Trust Centre document"
    OutgoingMail.objects.bulk_create([
        mail_for(subscriber, subject, f"{document.title}\n\n{document.teaser}\n\n"
                 f"Open in the Trust Centre: {link}\n\n"
                 f"Unsubscribe: {url('subscription', subscriber.token)}")
        for subscriber in Subscriber.objects.filter(status="active").iterator()
    ], batch_size=250)
    return True
