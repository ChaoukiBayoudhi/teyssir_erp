from django.core.management.base import BaseCommand

from teyssir.ledger.services import post_all_to_gl, trial_balance


class Command(BaseCommand):
    help = "Post sales, goods receipts and on-account payments to the GL (hub batch). Spec §15."

    def handle(self, *args, **options):
        posted = post_all_to_gl()
        tb = trial_balance()
        self.stdout.write(self.style.SUCCESS(
            f"Posted {posted}. Trial balance: debit {tb['total_debit']} "
            f"/ credit {tb['total_credit']} (balanced={tb['balanced']})"
        ))
