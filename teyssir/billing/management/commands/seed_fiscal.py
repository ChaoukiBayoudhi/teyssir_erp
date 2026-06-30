from decimal import Decimal

from django.core.management.base import BaseCommand

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import TaxRate

# Tunisia TVA rates (spec §15; source: compta.tn). 7% covers books/manuels/journaux/
# fournitures scolaires — the bulk of the catalogue.
TVA_RATES = [
    ("TVA 7%", Decimal("7.00"), True),
    ("TVA 13%", Decimal("13.00"), False),
    ("TVA 19%", Decimal("19.00"), False),
    ("Exonéré 0%", Decimal("0.00"), False),
]

# Timbre fiscal (spec §2 M2b): default 1.000 DT on factures, none on tickets.
STAMPS = [
    ("FACTURE", Decimal("1.000")),
    ("TICKET", Decimal("0.000")),
    ("AVOIR", Decimal("0.000")),
]


class Command(BaseCommand):
    help = "Seed Tunisian TVA rates and the configurable timbre fiscal (spec §15/§2)."

    def handle(self, *args, **options):
        for name, rate, is_default in TVA_RATES:
            TaxRate.objects.get_or_create(
                name=name, defaults={"rate_percent": rate, "is_default": is_default}
            )
        for doc_type, amount in STAMPS:
            FiscalStampConfig.objects.get_or_create(
                doc_type=doc_type, defaults={"amount": amount, "active": True}
            )
        self.stdout.write(self.style.SUCCESS("Fiscal config seeded (TVA 7/13/19/0 + timbre)."))
