from django.apps import AppConfig
from django.db import transaction
from django.db.models.signals import post_delete


def remove_revision_file(sender, instance, **kwargs):
    if instance.file:
        transaction.on_commit(lambda: instance.file.delete(save=False), robust=True)


class PortalConfig(AppConfig):
    name = "portal"

    def ready(self):
        from .models import Revision

        post_delete.connect(remove_revision_file, sender=Revision)
