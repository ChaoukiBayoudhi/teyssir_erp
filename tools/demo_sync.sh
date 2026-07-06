#!/bin/bash
# End-to-end sync demo (spec §4.4): a hub + a till (C1) over real HTTP, both on SQLite.
# Till pulls the catalogue, sells offline, pushes the sale back; we verify the hub received it.
set -e
cd "$(cd "$(dirname "$0")/.." && pwd)"      # project root (portable, no hardcoded path)
PY="$(pwd)/.venv/bin/python"

HUB="env TEYSSIR_ROLE=hub TEYSSIR_DB=sqlite TEYSSIR_SQLITE_NAME=demo_hub.sqlite3 TEYSSIR_SYNC_KEY=demo-key"
TILL="env TEYSSIR_ROLE=till TEYSSIR_TERMINAL=C1 TEYSSIR_DB=sqlite TEYSSIR_SQLITE_NAME=demo_c1.sqlite3 \
      TEYSSIR_HUB_URL=http://localhost:8000 TEYSSIR_SYNC_KEY=demo-key"

echo "### clean previous demo DBs"
rm -f demo_hub.sqlite3* demo_c1.sqlite3*

echo "### HUB: migrate + seed fiscal + a product with 100 in stock"
$HUB $PY manage.py migrate --noinput >/dev/null
$HUB $PY manage.py seed_fiscal
$HUB $PY manage.py shell >/dev/null <<'PYEOF'
from decimal import Decimal
from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import TaxRate, Product, Barcode
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
# distinctive timbre on the hub (default is 1.000) to prove config sync to the till
FiscalStampConfig.objects.filter(doc_type="FACTURE").update(amount=Decimal("0.600"))
t = TaxRate.objects.get(rate_percent=Decimal("7.00"))
p = Product.objects.create(sku="PEN", name_fr="Stylo Bic", tax_rate=t, sale_price=Decimal("0.850"))
Barcode.objects.create(product=p, value="6191234567890")
apply_movement(product_id=p.id, qty=Decimal("100"), reason=StockMovement.RECEIPT)
PYEOF

echo "### start HUB server (background) on :8000"
$HUB $PY manage.py runserver 8000 --noreload >/tmp/teyssir_hub.log 2>&1 &
HUBPID=$!
trap "kill $HUBPID 2>/dev/null || true" EXIT
curl -s --retry 20 --retry-connrefused --retry-delay 1 http://localhost:8000/health/ >/dev/null
echo "    hub is up: $(curl -s http://localhost:8000/health/)"

echo "### TILL C1: migrate ONLY (no local seed — fiscal config comes from the hub via sync)"
$TILL $PY manage.py migrate --noinput >/dev/null

echo "### TILL C1: first sync -> PULL the catalogue + fiscal config from the hub"
$TILL $PY manage.py sync_now
$TILL $PY manage.py shell <<'PYEOF'
from teyssir.billing.models import FiscalStampConfig
c = FiscalStampConfig.objects.filter(doc_type="FACTURE").first()
print(f"    TILL pulled timbre fiscal from hub: {c.amount if c else 'NONE'} (expect 0.600)")
PYEOF

echo "### TILL C1: sell 3 pens OFFLINE (finalize locally)"
$TILL $PY manage.py shell <<'PYEOF'
from decimal import Decimal
from teyssir.catalog.models import Product
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale
p = Product.objects.get(sku="PEN")           # pulled from the hub
s = Sale.objects.create(terminal="C1", status="DRAFT")
SaleLine.objects.create(sale=s, product=p, qty=Decimal("3"),
                        unit_price=p.sale_price, tax_rate=Decimal("7.00"))
inv = finalize_sale(s, payment_method="CASH")
p.refresh_from_db()
print(f"    TILL sold 3 -> invoice {inv.fiscal_number}, total {s.total} DT, local stock now {p.qty_on_hand}")
PYEOF

echo "### TILL C1: second sync -> PUSH the sale to the hub"
$TILL $PY manage.py sync_now

echo "### HUB: verify it received the sale"
$HUB $PY manage.py shell <<'PYEOF'
from teyssir.billing.models import Invoice
from teyssir.sales.models import Sale, Payment
from teyssir.catalog.models import Product
for i in Invoice.objects.all():
    print(f"    HUB has invoice {i.fiscal_number}  (timbre {i.timbre_amount_snapshot})")
for s in Sale.objects.all():
    print(f"    HUB has sale total {s.total} DT, status {s.status}, terminal {s.terminal}")
print(f"    HUB payments: {[ (p.method, str(p.amount)) for p in Payment.objects.all() ]}")
print(f"    HUB stock for PEN after merge: {Product.objects.get(sku='PEN').qty_on_hand}  (100 received - 3 sold)")
PYEOF

echo "### done"
