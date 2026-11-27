from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from portal.models import Account


class Command(BaseCommand):
    help = "Provision or reset an authenticator for an Admin or Owner."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = Account.objects.get(username=options["username"], is_staff=True, is_active=True)
        except Account.DoesNotExist as exc:
            raise CommandError("No active Admin or Owner found.") from exc
        TOTPDevice.objects.filter(user=user).delete()
        device = TOTPDevice.objects.create(user=user, name="Authenticator")
        self.stdout.write("Add this private TOTP setup URI to your authenticator:")
        self.stdout.write(device.config_url)
