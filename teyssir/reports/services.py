"""Management-accounting reports (spec §15). No double-entry GL — operational P&L + analytics.

COGS uses the cost snapshotted onto each SALE stock-movement at finalize time (weighted-average
cost at the moment of sale), so it is correct even as costs change later.
"""
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate, TruncHour, TruncWeek

from teyssir.core.money import to_money
from teyssir.inventory.models import StockMovement
from teyssir.sales.models import Payment, Sale, SaleLine


def _money(v):
    return str(to_money(v or 0))


def _bucket_grain(date_from, date_to):
    """Daily buckets for short ranges; weekly when the window spans > 45 days."""
    days = (date_to - date_from).days
    if days > 45:
        return "week", TruncWeek("created_at")
    return "day", TruncDate("created_at")


def sales_report(date_from, date_to, store=None, payment_method=None,
                 product_type=None, terminal=None):
    """Operational P&L + analytics for [date_from, date_to].

    Optional filters (all additive — omit for legacy behaviour):
      store, payment_method, product_type (book|furniture), terminal
    """
    sales = Sale.objects.filter(
        status=Sale.FINALIZED,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    if store is not None and store != "":
        sales = sales.filter(invoice__store_code=store)
    if terminal:
        sales = sales.filter(terminal=terminal)
    if payment_method:
        sales = sales.filter(payments__method=payment_method).distinct()
    if product_type:
        sales = sales.filter(lines__product__product_type=product_type).distinct()

    lines = SaleLine.objects.filter(sale__in=sales)
    if product_type:
        lines = lines.filter(product__product_type=product_type)

    # When slicing by product type, sale.total includes other lines — use line maths.
    line_scoped = bool(product_type)

    if line_scoped:
        line_agg = lines.aggregate(subtotal=Sum("line_total"))
        subtotal = line_agg["subtotal"] or Decimal("0.000")
        # Approximate tax from line bases × rate (line_total is HT in this ERP).
        tax_total = Decimal("0.000")
        for row in lines.values("tax_rate").annotate(base=Sum("line_total")):
            rate = row["tax_rate"] or Decimal("0")
            base = row["base"] or Decimal("0")
            tax_total += to_money(base * rate / Decimal("100"))
        timbre = Decimal("0.000")  # timbre is per-invoice, not attributable to a product slice
        total = to_money(subtotal + tax_total)
        sales_count = sales.count()
    else:
        agg = sales.aggregate(
            subtotal=Sum("subtotal"), tax=Sum("tax_total"),
            timbre=Sum("timbre_amount_snapshot"), total=Sum("total"),
        )
        subtotal = agg["subtotal"] or Decimal("0.000")
        tax_total = agg["tax"] or Decimal("0")
        timbre = agg["timbre"] or Decimal("0")
        total = agg["total"] or Decimal("0")
        sales_count = sales.count()

    sale_ids = [str(i) for i in sales.values_list("id", flat=True)]
    cogs_qs = StockMovement.objects.filter(reason="SALE", ref_id__in=sale_ids)
    if product_type:
        cogs_qs = cogs_qs.filter(product__product_type=product_type)
    cogs_raw = cogs_qs.aggregate(c=Sum(F("qty") * F("unit_cost")))["c"] or Decimal("0")
    cogs = to_money(-cogs_raw)                       # SALE qty is negative
    gross_profit = to_money(subtotal - cogs)
    margin = to_money(gross_profit / subtotal * 100) if subtotal else Decimal("0.000")

    best = (
        lines.values("product__sku", "product__name_fr")
        .annotate(qty=Sum("qty"), revenue=Sum("line_total"))
        .order_by("-qty")[:10]
    )
    tva = (
        lines.values("tax_rate").annotate(base=Sum("line_total")).order_by("tax_rate")
    )
    tva_rows = []
    for r in tva:
        base = to_money(r["base"] or 0)
        rate = r["tax_rate"] or Decimal("0")
        tax_amt = to_money(base * rate / Decimal("100"))
        tva_rows.append({
            "rate": str(rate),
            "base": str(base),
            "tax": str(tax_amt),
        })

    pay = (
        Payment.objects.filter(sale__in=sales)
        .values("method").annotate(amount=Sum("amount")).order_by("method")
    )
    if payment_method:
        pay = pay.filter(method=payment_method)

    category_mix = (
        lines.values("product__product_type")
        .annotate(qty=Sum("qty"), revenue=Sum("line_total"))
        .order_by("product__product_type")
    )

    grain, trunc = _bucket_grain(date_from, date_to)
    if line_scoped:
        # Line revenue (HT) by sale date
        series_qs = (
            lines.annotate(bucket=TruncDate("sale__created_at"))
            .values("bucket")
            .annotate(revenue=Sum("line_total"), count=Count("sale", distinct=True))
            .order_by("bucket")
        )
        if grain == "week":
            series_qs = (
                lines.annotate(bucket=TruncWeek("sale__created_at"))
                .values("bucket")
                .annotate(revenue=Sum("line_total"), count=Count("sale", distinct=True))
                .order_by("bucket")
            )
        series = [
            {
                "date": (row["bucket"].date() if hasattr(row["bucket"], "date") else row["bucket"]).isoformat()
                if row["bucket"] else None,
                "revenue": _money(row["revenue"]),
                "sales_count": row["count"] or 0,
            }
            for row in series_qs if row["bucket"]
        ]
    else:
        series_qs = (
            sales.annotate(bucket=trunc)
            .values("bucket")
            .annotate(
                revenue=Sum("total"),
                subtotal=Sum("subtotal"),
                count=Count("id"),
            )
            .order_by("bucket")
        )
        series = []
        for row in series_qs:
            if not row["bucket"]:
                continue
            b = row["bucket"]
            d = b.date() if hasattr(b, "date") else b
            series.append({
                "date": d.isoformat(),
                "revenue": _money(row["revenue"]),
                "revenue_ex_tax": _money(row["subtotal"]),
                "sales_count": row["count"] or 0,
            })

    # Margin trend (daily HT − COGS) when not product-sliced and range ≤ 45 days
    margin_trend = []
    if not line_scoped and grain == "day" and sale_ids:
        day_rev = {
            (r["bucket"].date() if hasattr(r["bucket"], "date") else r["bucket"]): r["subtotal"] or Decimal("0")
            for r in sales.annotate(bucket=TruncDate("created_at"))
            .values("bucket").annotate(subtotal=Sum("subtotal"))
            if r["bucket"]
        }
        day_cogs = {}
        for r in (
            StockMovement.objects.filter(reason="SALE", ref_id__in=sale_ids)
            .annotate(bucket=TruncDate("created_at"))
            .values("bucket")
            .annotate(c=Sum(F("qty") * F("unit_cost")))
        ):
            if not r["bucket"]:
                continue
            d = r["bucket"].date() if hasattr(r["bucket"], "date") else r["bucket"]
            day_cogs[d] = to_money(-(r["c"] or 0))
        for d in sorted(set(day_rev) | set(day_cogs)):
            rev = day_rev.get(d, Decimal("0"))
            cg = day_cogs.get(d, Decimal("0"))
            gp = to_money(rev - cg)
            margin_trend.append({
                "date": d.isoformat(),
                "gross_profit": str(gp),
                "margin_pct": str(to_money(gp / rev * 100) if rev else Decimal("0.000")),
            })

    hourly = []
    if date_from == date_to:
        hourly_qs = (
            sales.annotate(hour=TruncHour("created_at"))
            .values("hour")
            .annotate(revenue=Sum("total"), count=Count("id"))
            .order_by("hour")
        )
        for row in hourly_qs:
            if not row["hour"]:
                continue
            hourly.append({
                "hour": row["hour"].strftime("%H:00"),
                "revenue": _money(row["revenue"]),
                "sales_count": row["count"] or 0,
            })

    # Filter option hints (period-scoped, unfiltered by payment/type so UI can switch)
    base_period = Sale.objects.filter(
        status=Sale.FINALIZED,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    terminals = sorted({t for t in base_period.values_list("terminal", flat=True) if t})
    stores = sorted({
        (s or "") for s in base_period.values_list("invoice__store_code", flat=True)
    })
    # Only expose store filter when multi-store data exists
    multi_store = len([s for s in stores if s]) > 0 and len(stores) > 1

    return {
        "from": str(date_from),
        "to": str(date_to),
        "bucket": grain,
        "sales_count": sales_count,
        "revenue_ex_tax": str(to_money(subtotal)),
        "tax_total": str(to_money(tax_total)),
        "timbre_total": str(to_money(timbre)),
        "revenue_inc_tax": str(to_money(total)),
        "cogs": str(cogs),
        "gross_profit": str(gross_profit),
        "margin_pct": str(margin),
        "best_sellers": [
            {"sku": b["product__sku"], "name": b["product__name_fr"],
             "qty": str(int(b["qty"] or 0)),
             "revenue": str(to_money(b["revenue"]))}
            for b in best
        ],
        "tva_by_rate": tva_rows,
        "payment_mix": [
            {"method": p["method"], "amount": str(to_money(p["amount"]))} for p in pay
        ],
        "category_mix": [
            {
                "product_type": c["product__product_type"] or "furniture",
                "qty": str(int(c["qty"] or 0)),
                "revenue": str(to_money(c["revenue"])),
            }
            for c in category_mix
        ],
        "series": series,
        "margin_trend": margin_trend,
        "hourly": hourly,
        "filters_applied": {
            "store": store or "",
            "payment_method": payment_method or "",
            "product_type": product_type or "",
            "terminal": terminal or "",
        },
        "filter_options": {
            "payment_methods": [m for m, _ in Payment.METHODS],
            "product_types": ["book", "furniture"],
            "terminals": terminals,
            "stores": stores if multi_store else [],
        },
    }


def consolidated_sales_by_store(date_from, date_to):
    """Phase 6: roll up finalized sales by store (Invoice.store_code) for a cloud hub holding data
    from several stores. Empty store_code = the standalone/single store. Returns per-store lines
    plus a chain-wide grand total (spec §15, multi-store)."""
    sales = Sale.objects.filter(
        status=Sale.FINALIZED,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    rows = (
        sales.values("invoice__store_code")
        .annotate(count=Count("id"), ex=Sum("subtotal"), tax=Sum("tax_total"), inc=Sum("total"))
        .order_by("invoice__store_code")
    )
    stores = [
        {
            "store_code": r["invoice__store_code"] or "",
            "sales_count": r["count"],
            "revenue_ex_tax": str(to_money(r["ex"] or 0)),
            "tax_total": str(to_money(r["tax"] or 0)),
            "revenue_inc_tax": str(to_money(r["inc"] or 0)),
        }
        for r in rows
    ]
    g = sales.aggregate(c=Count("id"), ex=Sum("subtotal"), tax=Sum("tax_total"), inc=Sum("total"))
    return {
        "from": str(date_from),
        "to": str(date_to),
        "stores": stores,
        "grand_total": {
            "sales_count": g["c"] or 0,
            "revenue_ex_tax": str(to_money(g["ex"] or 0)),
            "tax_total": str(to_money(g["tax"] or 0)),
            "revenue_inc_tax": str(to_money(g["inc"] or 0)),
        },
    }
