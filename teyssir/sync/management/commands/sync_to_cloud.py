from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from teyssir.sync.client import sync_to_cloud


class Command(BaseCommand):
    help = "Phase 6: forward this store hub's transactions up to the cloud hub (spec §4.4, recursive)."

    def handle(self, *args, **options):
        if settings.ROLE != "hub":
            raise CommandError("sync_to_cloud runs on a store hub (TEYSSIR_ROLE=hub).")
        if not settings.CLOUD_HUB_URL:
            raise CommandError("TEYSSIR_CLOUD_HUB_URL is not set.")
        result = sync_to_cloud()
        self.stdout.write(self.style.SUCCESS(f"Forwarded to cloud: {result}"))
