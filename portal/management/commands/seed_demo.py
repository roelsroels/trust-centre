from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from portal.demo.catalog import COMPANY, SAMPLES
from portal.models import Branding, Category, Document, Revision


class Command(BaseCommand):
    help = "Install fictional sample documents and branding in a fresh Trust Centre."

    def handle(self, *args, **options):
        samples_dir = settings.BASE_DIR / "output" / "pdf"
        if any(not (samples_dir / f"{sample.slug}.pdf").is_file() for sample in SAMPLES):
            raise CommandError("The bundled demo PDFs are missing.")

        stored_files = []
        try:
            with transaction.atomic():
                brand = Branding.objects.first()
                if brand and brand.show_demo_team and brand.name == COMPANY:
                    self.stdout.write("The demo is already installed.")
                    return
                if brand or Document.objects.exists():
                    raise CommandError("Use a fresh portal database to install the demo.")
                Branding.objects.create(
                    name=COMPANY,
                    introduction="Explore how our fictional workflow platform handles security, privacy, and compliance.",
                    team="Meet the people behind our security, audit, and privacy programmes.",
                    show_demo_team=True,
                )
                for sample in SAMPLES:
                    category, _ = Category.objects.get_or_create(name=sample.category)
                    document = Document.objects.create(
                        category=category, title=sample.title, teaser=sample.teaser,
                        minimum_tier=sample.tier, agreement=sample.agreement,
                    )
                    version = next(
                        (part.removeprefix("Revision ") for part in sample.reference.split(" · ")
                         if part.startswith("Revision ")),
                        "1.0",
                    )
                    revision = Revision(document=document, label=version, published=timezone.now())
                    with (samples_dir / f"{sample.slug}.pdf").open("rb") as source:
                        revision.file.save(f"{sample.slug}.pdf", File(source), save=False)
                    stored_files.append((revision.file.storage, revision.file.name))
                    revision.save()
                    document.current = revision
                    document.save(update_fields=["current"])
        except Exception:
            for storage, name in stored_files:
                storage.delete(name)
            raise
        self.stdout.write(self.style.SUCCESS("Installed six fictional documents and the demo homepage."))
