# Teyssir POS — front-end (React PWA)

Vite + React 18 + MUI (Material 3, library-green) + react-i18next (FR/AR with runtime RTL).
Talks to the Django/DRF backend over `/api/v1` (token auth). Spec: `../docs/ARCHITECTURE.md` §5/§11/§13.

## Develop (two terminals)

```bash
# 1) backend (from teyssir_erp/)
TEYSSIR_ROLE=till .venv/bin/python manage.py runserver        # http://localhost:8000

# 2) front-end (from teyssir_erp/frontend/)
npm install
npm run dev                                                   # http://localhost:5173
```
`vite.config.js` proxies `/api` and `/health` to `:8000`, so the browser stays same-origin
(no CORS). In production, Caddy serves the built `dist/` and proxies `/api` to the local node.

## Build

```bash
npm run build      # -> dist/ (installable PWA: manifest + service worker)
npm run preview    # serve the build locally
```

## What's implemented (Phase-0 POS slice)

- **Login** (token auth) and **POS** screen.
- Product **search** + **barcode lookup**, cart with quantities, live totals
  (per-rate TVA + timbre + total, displayed 2-dp).
- **Checkout** → calls `/api/v1/pos/checkout`, which finalizes a real sale on the node
  (stock movement, `C1-YYYYMM-XXXX` number, timbre snapshot) and returns the invoice number.
- **FR/AR** toggle with full **RTL** mirroring (emotion + stylis-plugin-rtl, MUI `direction`).
- Terminal selector (C1/C2/C3).

## Demo data

```bash
# from teyssir_erp/, after migrate + seed_fiscal:
.venv/bin/python manage.py createsuperuser           # a login
.venv/bin/python manage.py shell -c "from teyssir.catalog.models import *; from teyssir.inventory.services import apply_movement; from decimal import Decimal; t=TaxRate.objects.get(rate_percent=Decimal('7.00')); p=Product.objects.create(sku='PEN', name_fr='Stylo Bic', tax_rate=t, sale_price=Decimal('0.850')); Barcode.objects.create(product=p, value='6191234567890'); apply_movement(product_id=p.id, qty=Decimal('100'), reason='RECEIPT')"
```
