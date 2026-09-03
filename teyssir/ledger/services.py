"""Double-entry general ledger (spec §15, Phase 5).

The GL is a **hub-side derivation**: `post_sales_to_gl()` runs on the consolidated hub and posts a
balanced journal entry per finalized sale (idempotent by reference), so tills never touch the GL.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from teyssir.core.money import to_money

from .models import Account, JournalEntry, JournalLine

# Tunisian PCG-flavoured chart of accounts.
CHART = [
    ("531", "Caisse", Account.ASSET),
    ("532", "Banque", Account.ASSET),
    ("411", "Clients", Account.ASSET),
    ("401", "Fournisseurs", Account.LIABILITY),
    ("370", "Stocks de marchandises", Account.ASSET),
    ("700", "Ventes de marchandises", Account.REVENUE),
    ("4366", "TVA déductible", Account.ASSET),
    ("4367", "TVA collectée", Account.LIABILITY),
    ("4471", "Droit de timbre", Account.LIABILITY),
    ("607", "Coût des marchandises vendues", Account.EXPENSE),
]
PAYMENT_ACCOUNT = {"CASH": "531", "CARD": "532", "ACCOUNT": "411", "VOUCHER": "531"}


class UnbalancedJournal(Exception):
    pass


def seed_chart():
    for code, name, type_ in CHART:
        Account.objects.get_or_create(code=code, defaults={"name": name, "type": type_})


@transaction.atomic
def post_journal(*, date, memo, lines, ref_type="", ref_id=""):
    """`lines` = [(account_code, debit, credit)]. Validates Σdebit == Σcredit; idempotent by ref."""
    if ref_type and ref_id:
        existing = JournalEntry.objects.filter(ref_type=ref_type, ref_id=str(ref_id)).first()
        if existing:
            return existing
    total_d = sum((to_money(d) for _, d, _ in lines), Decimal("0.000"))
    total_c = sum((to_money(c) for _, _, c in lines), Decimal("0.000"))
    if total_d != total_c:
        raise UnbalancedJournal(f"debits {total_d} != credits {total_c}")

    accounts = {a.code: a for a in Account.objects.all()}
    entry = JournalEntry.objects.create(date=date, memo=memo, ref_type=ref_type, ref_id=str(ref_id))
    for code, d, c in lines:
        JournalLine.objects.create(
            entry=entry, account=accounts[code], debit=to_money(d), credit=to_money(c)
        )
    return entry


def post_sale_to_gl(sale):
    """Post one finalized sale: Dr tender (Cash/Bank/Clients) ; Cr Sales, TVA, Timbre ;
    plus Dr COGS / Cr Inventory at cost. Spec §15."""
    from teyssir.inventory.models import StockMovement

    lines = []
    for p in sale.payments.all():
        lines.append((PAYMENT_ACCOUNT.get(p.method, "531"), p.amount, 0))
    if sale.subtotal:
        lines.append(("700", 0, sale.subtotal))
    if sale.tax_total:
        lines.append(("4367", 0, sale.tax_total))
    if sale.timbre_amount_snapshot:
        lines.append(("4471", 0, sale.timbre_amount_snapshot))

    cogs = Decimal("0.000")
    for mv in StockMovement.objects.filter(reason="SALE", ref_id=str(sale.id)):
        cogs += (-mv.qty) * mv.unit_cost
    cogs = to_money(cogs)
    if cogs:
        lines.append(("607", cogs, 0))   # Dr COGS
        lines.append(("370", 0, cogs))   # Cr Inventory

    invoice = getattr(sale, "invoice", None)
    ref = invoice.fiscal_number if invoice else str(sale.id)
    return post_journal(date=sale.created_at.date(), memo=f"Vente {ref}",
                        lines=lines, ref_type="SALE", ref_id=sale.id)


def post_return_to_gl(ret):
    """Reverse a credit note (AVOIR): Dr Sales/TVA/Timbre ; Cr tender ;
    plus Dr Inventory / Cr COGS for restored stock. Spec §15."""
    from teyssir.inventory.models import StockMovement

    lines = []
    if ret.subtotal:
        lines.append(("700", ret.subtotal, 0))          # reverse revenue
    if ret.tax_total:
        lines.append(("4367", ret.tax_total, 0))         # reverse TVA collectée
    if ret.timbre_amount_snapshot:
        lines.append(("4471", ret.timbre_amount_snapshot, 0))
    tender = PAYMENT_ACCOUNT.get(ret.refund_method, "531")
    if ret.total:
        lines.append((tender, 0, ret.total))            # Cr cash/bank/AR (money out)

    cogs = Decimal("0.000")
    for mv in StockMovement.objects.filter(reason="RETURN", ref_id=str(ret.id)):
        cogs += mv.qty * mv.unit_cost
    cogs = to_money(cogs)
    if cogs:
        lines.append(("370", cogs, 0))                   # Dr Inventory (stock back)
        lines.append(("607", 0, cogs))                   # Cr COGS

    return post_journal(date=ret.created_at.date(), memo=f"Avoir {ret.number or ret.id}",
                        lines=lines, ref_type="RETURN", ref_id=ret.id)


def post_returns_to_gl():
    """Hub batch: post every return not yet in the GL (idempotent)."""
    from teyssir.sales.models import Return

    return _post_batch("RETURN", Return.objects.all(), post_return_to_gl)


def post_sales_to_gl():
    """Hub batch: post every finalized sale not yet in the GL (idempotent).

    Sales without a matching tender (Σ payments == total) are skipped — posting them
    would raise UnbalancedJournal; they surface as exceptions for the accountant to fix.
    """
    from teyssir.sales.models import Sale

    posted = 0
    already = set(
        JournalEntry.objects.filter(ref_type="SALE").values_list("ref_id", flat=True)
    )
    for sale in Sale.objects.filter(status=Sale.FINALIZED).prefetch_related("payments"):
        if str(sale.id) in already:
            continue
        tender = sum((p.amount for p in sale.payments.all()), Decimal("0.000"))
        if to_money(tender) != to_money(sale.total):
            continue  # incomplete tender — do not poison the GL
        post_sale_to_gl(sale)
        posted += 1
    return posted


def post_goods_receipt_to_gl(gr):
    """Inventory in at cost: Dr Stocks / Cr Fournisseurs (AP). VAT-deductible from a separate
    purchase invoice is a documented extension."""
    from django.db.models import F, Sum

    cost = to_money(gr.lines.aggregate(s=Sum(F("qty") * F("unit_cost")))["s"] or 0)
    if not cost:
        return None
    return post_journal(date=gr.created_at.date(), memo=f"Réception {gr.id}",
                        lines=[("370", cost, 0), ("401", 0, cost)],
                        ref_type="RECEIPT", ref_id=gr.id)


def post_account_payment_to_gl(entry):
    """A customer payment-on-account: Dr Caisse / Cr Clients (AR reduced). The original charge
    was already booked to AR by the sale."""
    return post_journal(date=entry.created_at.date(), memo="Règlement client",
                        lines=[("531", entry.amount, 0), ("411", 0, entry.amount)],
                        ref_type="ACCT_PAYMENT", ref_id=entry.id)


def post_purchase_invoice_to_gl(inv):
    """Record deductible TVA on a supplier invoice: Dr TVA déductible / Cr Fournisseurs.
    The goods value is booked by the goods receipt (Dr Stocks / Cr Fournisseurs), so we post the
    VAT portion only — no double count. Completes the TVA picture for the monthly declaration."""
    if not inv.tva_total:
        return None
    return post_journal(
        date=(inv.invoice_date or inv.created_at.date()),
        memo=f"TVA déductible {inv.supplier_number}",
        lines=[("4366", inv.tva_total, 0), ("401", 0, inv.tva_total)],
        ref_type="PURCHASE_INVOICE", ref_id=inv.id,
    )


def _post_batch(ref_type, queryset, poster):
    """Post each not-yet-posted object via `poster`; count only entries actually created."""
    already = set(JournalEntry.objects.filter(ref_type=ref_type).values_list("ref_id", flat=True))
    posted = 0
    for obj in queryset:
        if str(obj.id) not in already and poster(obj):
            posted += 1
    return posted


def post_all_to_gl():
    """Hub batch: post sales, returns, goods receipts, purchase-invoice VAT and payments."""
    from teyssir.customers.models import AccountEntry
    from teyssir.purchasing.models import GoodsReceipt, PurchaseInvoice

    return {
        "sales": post_sales_to_gl(),
        "returns": post_returns_to_gl(),
        "receipts": _post_batch("RECEIPT", GoodsReceipt.objects.all(), post_goods_receipt_to_gl),
        "purchase_invoices": _post_batch(
            "PURCHASE_INVOICE", PurchaseInvoice.objects.all(), post_purchase_invoice_to_gl),
        "payments": _post_batch(
            "ACCT_PAYMENT", AccountEntry.objects.filter(entry_type=AccountEntry.PAYMENT),
            post_account_payment_to_gl),
    }


def vat_declaration(date_from, date_to):
    """Monthly TVA declaration from the GL: collected (4367) − deductible (4366) = net payable.
    Negative net = VAT credit carried forward. Spec §15."""
    def balance(code, normal):
        agg = JournalLine.objects.filter(
            account__code=code, entry__date__gte=date_from, entry__date__lte=date_to,
        ).aggregate(d=Sum("debit"), c=Sum("credit"))
        d, c = to_money(agg["d"] or 0), to_money(agg["c"] or 0)
        return to_money(c - d) if normal == "C" else to_money(d - c)

    collected = balance("4367", "C")
    deductible = balance("4366", "D")
    return {
        "from": str(date_from), "to": str(date_to),
        "tva_collected": str(collected),
        "tva_deductible": str(deductible),
        "net_payable": str(to_money(collected - deductible)),
    }


def financial_statements():
    """Income statement + balance sheet derived from the GL. Equity = net income (no capital
    accounts yet); the balance sheet balances whenever the trial balance does (spec §15)."""
    revenue = expense = assets = liabilities = Decimal("0.000")
    for acc in Account.objects.all():
        agg = JournalLine.objects.filter(account=acc).aggregate(d=Sum("debit"), c=Sum("credit"))
        d = to_money(agg["d"] or 0)
        c = to_money(agg["c"] or 0)
        if acc.type == Account.REVENUE:
            revenue += c - d
        elif acc.type == Account.EXPENSE:
            expense += d - c
        elif acc.type == Account.ASSET:
            assets += d - c
        elif acc.type == Account.LIABILITY:
            liabilities += c - d
    revenue, expense = to_money(revenue), to_money(expense)
    assets, liabilities = to_money(assets), to_money(liabilities)
    net_income = to_money(revenue - expense)
    return {
        "income_statement": {
            "revenue": str(revenue), "expenses": str(expense), "net_income": str(net_income),
        },
        "balance_sheet": {
            "assets": str(assets), "liabilities": str(liabilities), "equity": str(net_income),
            "balanced": assets == to_money(liabilities + net_income),
        },
    }


def trial_balance():
    """Per-account debit/credit totals + grand totals (must be equal)."""
    rows = []
    total_d = Decimal("0.000")
    total_c = Decimal("0.000")
    for acc in Account.objects.all():
        agg = JournalLine.objects.filter(account=acc).aggregate(d=Sum("debit"), c=Sum("credit"))
        d = to_money(agg["d"] or 0)
        c = to_money(agg["c"] or 0)
        if d == 0 and c == 0:
            continue
        rows.append({"code": acc.code, "name": acc.name, "type": acc.type,
                     "debit": str(d), "credit": str(c)})
        total_d += d
        total_c += c
    return {
        "rows": rows,
        "total_debit": str(to_money(total_d)),
        "total_credit": str(to_money(total_c)),
        "balanced": total_d == total_c,
    }
