import csv
import io

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django_otp.admin import OTPAdminSite

from .models import Acceptance, AccessRequest, Account, Branding, Category, Document, Revision, Subscriber
from .services import invite, prune, publish


class Console(OTPAdminSite):
    site_header = "Trust Centre administration"
    site_title = "Trust Centre"
    index_title = "Manage the Trust Centre"

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_verified()


console = Console(name="manage")


class StaffModelAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return console.has_permission(request)

    def has_view_permission(self, request, obj=None):
        return console.has_permission(request)

    def has_change_permission(self, request, obj=None):
        return console.has_permission(request)

    def has_add_permission(self, request):
        return console.has_permission(request)

    def has_delete_permission(self, request, obj=None):
        return console.has_permission(request)


@admin.register(Account, site=console)
class Accounts(UserAdmin, StaffModelAdmin):
    list_display = ("username", "email", "tier", "is_staff", "is_active")
    list_filter = ("tier", "is_staff", "is_active")
    search_fields = ("username", "email")
    actions = None
    filter_horizontal = ()
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Customer account", {"fields": ("email", "tier", "is_active")}),
        ("Administration", {"fields": ("is_staff",)}),
    )
    add_fieldsets = ((None, {"fields": ("username", "email", "tier", "password1", "password2")}),)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj and not request.user.is_superuser:
            return fieldsets[:-1]
        return fieldsets

    def get_queryset(self, request):
        queryset = super().get_queryset(request).filter(is_superuser=False)
        return queryset if request.user.is_superuser else queryset.filter(is_staff=False)

    def has_view_permission(self, request, obj=None):
        return console.has_permission(request) and (
            obj is None or (not obj.is_superuser and (request.user.is_superuser or not obj.is_staff))
        )

    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


@admin.register(Branding, site=console)
class Settings(StaffModelAdmin):
    def has_add_permission(self, request):
        return super().has_add_permission(request) and not Branding.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class UploadInline(admin.TabularInline):
    model = Revision
    fields = ("label", "file")
    extra = 1
    max_num = 1
    can_delete = False
    show_change_link = False

    def get_queryset(self, request):
        return super().get_queryset(request).none()

    def has_add_permission(self, request, obj=None):
        return console.has_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return console.has_permission(request)


@admin.register(Document, site=console)
class Documents(StaffModelAdmin):
    list_display = ("title", "category", "minimum_tier", "current")
    list_filter = ("category", "minimum_tier")
    search_fields = ("title", "teaser")
    readonly_fields = ("current", "revision_list")
    inlines = (UploadInline,)

    @admin.display(description="Drafts and version history")
    def revision_list(self, obj):
        from django.utils.html import format_html_join

        if not obj.pk:
            return "Save the document to review and publish uploaded versions."
        return format_html_join(" · ", '<a href="{}">{}</a>', (
            (reverse("manage:portal_revision_change", args=[revision.pk]), revision.label)
            for revision in obj.revisions.all()
        )) or "Upload a PDF below."

    def save_model(self, request, obj, form, change):
        if change:
            if form.changed_data:
                obj.save(update_fields=form.changed_data)
            if "retain_previous" in form.changed_data:
                prune(Document.objects.get(pk=obj.pk))
        else:
            super().save_model(request, obj, form, change)


@admin.register(Revision, site=console)
class Revisions(StaffModelAdmin):
    list_display = ("label", "document", "published", "created")
    list_filter = ("document",)
    readonly_fields = ("document", "label", "created", "published")
    fields = readonly_fields
    actions = ("publish_version",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Publish or restore the selected revision", permissions=["view"])
    def publish_version(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one revision.", messages.ERROR)
            return
        changed = publish(queryset.get().pk)
        self.message_user(request, "Revision published." if changed else "That revision is already live.")

    def get_urls(self):
        return [
            path("<int:pk>/publish/", self.admin_site.admin_view(self.publish_view), name="revision_publish"),
        ] + super().get_urls()

    def publish_view(self, request, pk):
        from django.shortcuts import get_object_or_404

        revision = get_object_or_404(Revision, pk=pk)
        if request.method == "POST":
            publish(pk)
            self.message_user(request, "Revision is live.")
            return redirect("manage:portal_document_change", revision.document_id)
        return render(request, "publish.html", {
            **self.admin_site.each_context(request), "revision": revision,
            "title": "Publish or restore a revision",
        })

    change_form_template = "revision_admin.html"


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV file with an email column")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 256 * 1024:
            raise ValidationError("Use a CSV file smaller than 256 KB.")
        try:
            reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")), strict=True)
            if not reader.fieldnames or "email" not in reader.fieldnames:
                raise ValidationError("The CSV needs a column named email.")
            addresses = set()
            for row in reader:
                email = (row["email"] or "").strip().lower()
                if not email:
                    continue
                try:
                    validate_email(email)
                    if len(email) > 254:
                        raise ValidationError("Email is too long.")
                except ValidationError as exc:
                    raise ValidationError(f"Invalid email on row {reader.line_num}.") from exc
                addresses.add(email)
            if not addresses:
                raise ValidationError("The CSV contains no email addresses.")
            return addresses
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValidationError("Upload a valid UTF-8 CSV file.") from exc


class ReadOnly(StaffModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscriber, site=console)
class Subscribers(ReadOnly):
    list_display = ("email", "status", "changed")
    fields = list_display
    list_filter = ("status",)
    search_fields = ("email",)
    change_list_template = "subscriber_admin.html"

    def get_urls(self):
        return [
            path("import/", self.admin_site.admin_view(self.import_csv), name="subscriber_import"),
        ] + super().get_urls()

    def import_csv(self, request):
        form = ImportForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                for email in sorted(form.cleaned_data["file"]):
                    invite(email)
            self.message_user(request, "Confirmation emails queued for new addresses.")
            return redirect("manage:portal_subscriber_changelist")
        return render(request, "import.html", {**self.admin_site.each_context(request), "form": form, "title": "Import subscribers"})


@admin.register(Acceptance, site=console)
class Acceptances(ReadOnly):
    list_display = ("user", "document", "accepted")


@admin.register(AccessRequest, site=console)
class Requests(ReadOnly):
    list_display = ("user", "document", "tier", "requested", "handled")
    list_filter = ("handled", "tier")
    actions = ("mark_handled",)

    @admin.action(description="Mark selected requests as handled", permissions=["view"])
    def mark_handled(self, request, queryset):
        count = queryset.filter(handled__isnull=True).update(handled=timezone.now())
        self.message_user(request, f"Requests marked as handled: {count}.")


console.register(Category, StaffModelAdmin)
