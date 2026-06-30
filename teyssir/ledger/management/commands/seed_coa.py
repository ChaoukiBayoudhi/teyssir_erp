from django.core.management.base import BaseCommand

from teyssir.ledger.services import seed_chart


class Command(BaseCommand):
    help = "Seed the chart of accounts (spec §15)."

    def handle(self, *args, **options):
        seed_chart()
        self.stdout.write(self.style.SUCCESS("Chart of accounts seeded."))
