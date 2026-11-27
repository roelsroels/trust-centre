import time
from datetime import timedelta

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import OperationalError
from django.utils import timezone

from portal.models import OutgoingMail


class Command(BaseCommand):
    help = "Deliver pending mail. Run a single instance per installation."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        while True:
            try:
                self.deliver()
            except OperationalError:
                if not options["watch"]:
                    raise
                self.stderr.write("Mail queue database unavailable. Will retry.")
            if not options["watch"]:
                return
            time.sleep(5)

    def deliver(self):
        queue = OutgoingMail.objects.filter(next_attempt__lte=timezone.now()).select_related("subscriber")
        for item in list(queue.order_by("next_attempt", "pk")[:50]):
            if item.subscriber and (item.subscriber.status != item.expected_status
                                    or item.subscriber.token != item.subscription_token):
                item.delete()
                continue
            try:
                if send_mail(item.subject, item.body, None, [item.email]) != 1:
                    raise OSError("Mail provider did not accept the message.")
            except OSError:
                item.attempts += 1
                item.next_attempt = timezone.now() + timedelta(minutes=min(60, 2 ** min(item.attempts, 6)))
                item.save(update_fields=["attempts", "next_attempt"])
                self.stderr.write(f"Mail {item.pk} failed. Scheduled for retry.")
            else:
                item.delete()
