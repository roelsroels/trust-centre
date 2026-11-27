import uuid
from pathlib import Path

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, RegexValidator, URLValidator
from django.db import models


class Tier(models.IntegerChoices):
    BASIC = 10, "Basic"
    PRO = 20, "Pro"
    EXPERT = 30, "Expert"


class Account(AbstractUser):
    # Django staff and superuser flags represent Admin and Owner respectively.
    email = models.EmailField(unique=True)
    tier = models.PositiveSmallIntegerField(choices=Tier, default=Tier.BASIC)
    REQUIRED_FIELDS = ["email"]


color = RegexValidator(r"^#[0-9a-fA-F]{6}\Z", "Enter a six-digit hex color.")


class Branding(models.Model):
    name = models.CharField(max_length=100, default="Trust Centre")
    introduction = models.TextField(default="Explore our security and compliance documents.")
    team = models.TextField(default="Our compliance team maintains the reports, certificates and policies in this portal.")
    logo_url = models.URLField(blank=True, validators=[URLValidator(schemes=["https"])], help_text="Optional HTTPS URL for a company logo.")
    accent = models.CharField(max_length=7, default="#076e65", validators=[color])
    ink = models.CharField(max_length=7, default="#132a42", validators=[color])
    font = models.CharField(max_length=10, choices=[("sans", "System sans"), ("serif", "System serif"), ("mono", "System mono")], default="sans")
    access_inbox = models.EmailField(blank=True, help_text="Where access requests are sent.")

    class Meta:
        verbose_name_plural = "Portal settings"

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Document(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    teaser = models.TextField(max_length=500)
    minimum_tier = models.PositiveSmallIntegerField(choices=Tier)
    agreement = models.TextField(blank=True, help_text="Leave blank when no agreement is required.")
    retain_previous = models.PositiveSmallIntegerField(default=3, validators=[MaxValueValidator(100)])
    current = models.ForeignKey("Revision", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return self.title


def pdf_path(instance, filename):
    return f"documents/{uuid.uuid4().hex}.pdf"


def validate_pdf(upload):
    signature = upload.read(5)
    upload.seek(0)
    if Path(upload.name).suffix.lower() != ".pdf" or signature != b"%PDF-":
        raise ValidationError("Upload a PDF file.")
    if upload.size > 25 * 1024 * 1024:
        raise ValidationError("PDF files must be 25 MB or smaller.")


class Revision(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="revisions")
    label = models.CharField(max_length=60)
    file = models.FileField(upload_to=pdf_path, validators=[validate_pdf])
    created = models.DateTimeField(auto_now_add=True)
    published = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-pk"]

    def __str__(self):
        return f"{self.document} · {self.label}"


class Acceptance(models.Model):
    user = models.ForeignKey(Account, null=True, on_delete=models.SET_NULL)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64)
    text = models.TextField()
    accepted = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "document", "digest"], name="unique_acceptance")]


class Subscriber(models.Model):
    email = models.EmailField(unique=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=[
        ("pending", "Pending"), ("active", "Subscribed"), ("off", "Unsubscribed"),
    ], default="pending")
    changed = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.email


class OutgoingMail(models.Model):
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    subscriber = models.ForeignKey(Subscriber, null=True, blank=True, on_delete=models.CASCADE)
    expected_status = models.CharField(max_length=12, blank=True)
    subscription_token = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt = models.DateTimeField(auto_now_add=True)


class AccessRequest(models.Model):
    user = models.ForeignKey(Account, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    tier = models.PositiveSmallIntegerField(choices=Tier)
    requested = models.DateTimeField(auto_now_add=True)
    handled = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["user", "document", "tier"], condition=models.Q(handled__isnull=True),
            name="unique_pending_access_request",
        )]
