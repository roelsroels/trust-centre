from django.db import migrations


def default_categories(apps, schema_editor):
    category = apps.get_model("portal", "Category")
    for name, description in (
        ("Reports", "Audit reports and evidence."),
        ("Certificates", "Certifications and attestations."),
        ("Policies", "Security, privacy and compliance policies."),
    ):
        category.objects.get_or_create(name=name, defaults={"description": description})


class Migration(migrations.Migration):
    dependencies = [("portal", "0001_initial")]
    operations = [migrations.RunPython(default_categories, migrations.RunPython.noop)]
