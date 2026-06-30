"""Management-accounting reports (spec §15). No double-entry GL — operational P&L + analytics.

COGS uses the cost snapshotted onto each SALE stock-movement at finalize time (weighted-average
cost at the moment of sale), so it is correct even as costs change later.
"""
from decimal import Decimal

from django.db.models import F, Sum

from teyssir.core.money import to_money
from teyssir.inventory.models import StockMovement
from teyssir.sales.models import Payment, Sale, SaleLine


def sales_report(date_from, date_to):
    sales = Sale.objects.filter(
        status=Sale.FINALIZED,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    agg = sales.aggregate(
        subtotal=Sum("subtotal"), tax=Sum("tax_total"),
        timbre=Sum("timbre_amount_snapshot"), total=Sum("total"),
    )
    subtotal = agg["subtotal"] or Decimal("0.000")

    sale_ids = [str(i) for i in sales.values_list("id", flat=True)]
    cogs_raw = (
        StockMovement.objects.filter(reason="SALE", ref_id__in=sale_ids)
        .aggregate(c=Sum(F("qty") * F("unit_cost")))["c"]
        or Decimal("0")
    )
    cogs = to_money(-cogs_raw)                       # SALE qty is negative
    gross_profit = to_money(subtotal - cogs)
    margin = to_money(gross_profit / subtotal * 100) if subtotal else Decimal("0.000")

    best = (
        SaleLine.objects.filter(sale__in=sales)
        .values("product__sku", "product__name_fr")
        .annotate(qty=Sum("qty"), revenue=Sum("line_total"))
        .order_by("-qty")[:5]
    )
    tva = (
        SaleLine.objects.filter(sale__in=sales)
        .values("tax_rate").annotate(base=Sum("line_total")).order_by("tax_rate")
    )
    pay = (
        Payment.objects.filter(sale__in=sales)
        .values("method").annotate(amount=Sum("amount")).order_by("method")
    )

    return {
        "from": str(date_from),
        "to": str(date_to),
        "sales_count": sales.count(),
        "revenue_ex_tax": str(to_money(subtotal)),
        "tax_total": str(to_money(agg["tax"] or 0)),
        "timbre_total": str(to_money(agg["timbre"] or 0)),
        "revenue_inc_tax": str(to_money(agg["total"] or 0)),
        "cogs": str(cogs),
        "gross_profit": str(gross_profit),
        "margin_pct": str(margin),
        "best_sellers": [
            {"sku": b["product__sku"], "name": b["product__name_fr"],
             "qty": str(Decimal(b["qty"]).quantize(Decimal("0.001"))),
             "revenue": str(to_money(b["revenue"]))}
            for b in best
        ],
        "tva_by_rate": [
            {"rate": str(r["tax_rate"]), "base": str(to_money(r["base"]))} for r in tva
        ],
        "payment_mix": [
            {"method": p["method"], "amount": str(to_money(p["amount"]))} for p in pay
        ],
    }
