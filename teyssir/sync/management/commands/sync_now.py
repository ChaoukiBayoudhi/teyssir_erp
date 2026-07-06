import urllib.error

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from teyssir.sync.client import sync_now


class Command(BaseCommand):
    help = "Run one sync exchange with the hub: push the outbox, then pull master data (spec §4.4)."

    def handle(self, *args, **options):
        if settings.ROLE != "till":
            raise CommandError("sync_now runs on a till node (TEYSSIR_ROLE=till).")
        if not settings.HUB_URL:
            raise CommandError("TEYSSIR_HUB_URL is not set.")
        if not settings.SYNC_KEY:
            raise CommandError("TEYSSIR_SYNC_KEY is not set.")
        try:
            result = sync_now(settings.HUB_URL, settings.SYNC_KEY)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise CommandError("Hub rejected the sync key (TEYSSIR_SYNC_KEY must match the hub).")
            raise CommandError(f"Hub returned HTTP {exc.code}. Sales are safe locally; will retry.")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            # Hub offline: not a crash — sales stay queued locally and replay next time (spec §4.3).
            raise CommandError(f"Hub unreachable ({exc}). Sales are safe locally; will retry.")
        self.stdout.write(self.style.SUCCESS(f"Synced: {result}"))
