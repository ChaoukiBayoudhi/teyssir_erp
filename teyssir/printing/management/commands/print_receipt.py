from django.core.management.base import BaseCommand, CommandError

from teyssir.printing.devices import send
from teyssir.printing.receipt import render_sale_receipt, render_text
from teyssir.sales.models import Sale


class Command(BaseCommand):
    help = "Render a sale's receipt and send it to the printer (TEYSSIR_PRINTER), or preview it."

    def add_arguments(self, parser):
        parser.add_argument("sale_id")
        parser.add_argument("--preview", action="store_true",
                            help="Print the plain-text preview to stdout instead of the printer.")

    def handle(self, *args, **options):
        try:
            sale = Sale.objects.get(pk=options["sale_id"])
        except Sale.DoesNotExist:
            raise CommandError("sale not found")
        if options["preview"]:
            self.stdout.write(render_text(sale))
            return
        n = send(render_sale_receipt(sale))
        self.stdout.write(self.style.SUCCESS(f"sent {n} bytes to the printer"))
