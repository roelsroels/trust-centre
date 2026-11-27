from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import Account, Branding, Category, Document, OutgoingMail, Revision, Subscriber, Tier


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PortalFlows(TestCase):
    def setUp(self):
        private = TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=private.name, SECURE_SSL_REDIRECT=False))
        self.password = "private-test-password-24"
        self.owner = Account.objects.create_superuser("owner", "owner@example.com", self.password)
        self.admin = Account.objects.create_user("admin", "admin@example.com", self.password, is_staff=True)
        self.customer = Account.objects.create_user("reader", "reader@example.com", self.password)
        self.category = Category.objects.get(name="Reports")
        Branding.objects.create(access_inbox="compliance@example.com")

    def sign_in_admin(self, user):
        self.client.logout()
        device = TOTPDevice.objects.create(user=user)
        response = self.client.post(reverse("manage:login"), {
            "username": user.username, "password": self.password,
            "otp_device": device.persistent_id, "otp_token": str(TOTP(device.bin_key).token()).zfill(6),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[DEVICE_ID_SESSION_KEY], device.persistent_id)

    def test_document_admin_upload_access_and_revision(self):
        self.sign_in_admin(self.admin)
        response = self.client.post(reverse("manage:portal_document_add"), {
            "category": self.category.pk, "title": "Audit pack", "teaser": "Scope of the audit.",
            "minimum_tier": Tier.PRO, "agreement": "Keep this confidential.", "retain_previous": 1,
            "revisions-TOTAL_FORMS": 1, "revisions-INITIAL_FORMS": 0,
            "revisions-0-label": "1.0",
            "revisions-0-file": SimpleUploadedFile("report.pdf", b"%PDF-1.4\nfirst", "application/pdf"),
        })
        self.assertEqual(response.status_code, 302, response.content[:3000])
        document = Document.objects.get()
        revision = document.revisions.get()
        page = reverse("document", args=[document.pk])
        download = reverse("download", args=[document.pk, revision.pk])
        self.assertEqual(self.client.get(page).status_code, 404)
        self.assertContains(self.client.get(reverse("manage:portal_revision_change", args=[revision.pk])), "Publish or restore")
        self.assertEqual(self.client.post(reverse("manage:revision_publish", args=[revision.pk])).status_code, 302)

        self.client.force_login(self.customer)
        self.assertContains(self.client.get(page), "Scope of the audit.")
        self.assertNotContains(self.client.get(page), "Keep this confidential.")
        self.assertEqual(self.client.get(download).status_code, 403)
        for _ in range(2):
            self.client.post(reverse("request_access", args=[document.pk]))
        self.assertEqual(OutgoingMail.objects.filter(email="compliance@example.com").count(), 1)

        self.customer.tier = Tier.PRO
        self.customer.save()
        self.assertEqual(self.client.get(download).status_code, 403)
        response = self.client.get(page)
        self.assertContains(response, "Keep this confidential.")
        digest = response.context["digest"]
        self.client.post(reverse("accept", args=[document.pk]), {"agree": "yes", "digest": digest})
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4\nfirst")
        document.agreement = "Revised terms."
        document.save(update_fields=["agreement"])
        self.client.post(reverse("accept", args=[document.pk]), {"agree": "yes", "digest": digest})
        self.assertEqual(self.client.get(download).status_code, 403)

        self.sign_in_admin(self.admin)
        previous = revision
        for label in ("2.0", "3.0"):
            revision = Revision.objects.create(document=document, label=label, file=SimpleUploadedFile("report.pdf", b"%PDF-1.4"))
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("manage:revision_publish", args=[revision.pk]))
        self.assertFalse(Revision.objects.filter(pk=previous.pk).exists())
        self.assertFalse(previous.file.storage.exists(previous.file.name))
        retained = document.revisions.exclude(pk=revision.pk).get()
        self.client.post(reverse("manage:revision_publish", args=[retained.pk]))
        document.refresh_from_db()
        self.assertEqual(document.current, retained)

    def test_admin_cannot_promote_customers_or_modify_admins(self):
        self.sign_in_admin(self.admin)
        account_page = reverse("manage:portal_account_change", args=[self.customer.pk])
        self.assertNotContains(self.client.get(account_page), 'name="is_staff"')
        self.assertEqual(self.client.post(account_page, {
            "username": self.customer.username, "email": self.customer.email, "tier": Tier.EXPERT,
            "is_active": "on", "is_staff": "on", "is_superuser": "on",
        }).status_code, 302)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_staff)
        self.assertFalse(self.customer.is_superuser)
        self.assertEqual(self.customer.tier, Tier.EXPERT)
        for target in (self.admin, self.owner):
            route = reverse("manage:auth_user_password_change", args=[target.pk])
            self.assertIn(self.client.get(route).status_code, (302, 403, 404))
        self.sign_in_admin(self.owner)
        self.assertContains(self.client.get(account_page), 'name="is_staff"')

    def test_confirmed_subscribers_receive_publish_mail_only(self):
        self.sign_in_admin(self.admin)
        response = self.client.post(reverse("manage:subscriber_import"), {
            "file": SimpleUploadedFile("customers.csv", b"email\nlist@example.com\nLIST@example.com\n"),
        })
        self.assertEqual(response.status_code, 302)
        imported = Subscriber.objects.get()
        self.assertEqual(imported.status, "pending")

        self.client.force_login(self.customer)
        self.client.post(reverse("notifications"))
        subscriber = Subscriber.objects.get(email=self.customer.email)
        link = reverse("subscription", args=[subscriber.token])
        self.client.get(link)
        subscriber.refresh_from_db()
        self.assertEqual(subscriber.status, "pending")
        call_command("send_queue")
        self.assertEqual(len(mail.outbox), 2)
        self.client.post(link, {"action": "confirm"})

        self.sign_in_admin(self.admin)
        document = Document.objects.create(category=self.category, title="Audit", teaser="Public summary", minimum_tier=Tier.EXPERT)
        revision = Revision.objects.create(document=document, label="1", file=SimpleUploadedFile("report.pdf", b"%PDF-1.4"))
        self.client.post(reverse("manage:revision_publish", args=[revision.pk]))
        call_command("send_queue")
        self.assertEqual(mail.outbox[-1].to, [self.customer.email])
        self.assertIn(link, mail.outbox[-1].body)
        self.assertIn("Public summary", mail.outbox[-1].body)
        self.assertEqual(len(mail.outbox), 3)
        self.client.post(link, {"action": "off"})
        revision = Revision.objects.create(document=document, label="2", file=SimpleUploadedFile("report.pdf", b"%PDF-1.4"))
        self.client.post(reverse("manage:revision_publish", args=[revision.pk]))
        self.assertFalse(OutgoingMail.objects.exists())
