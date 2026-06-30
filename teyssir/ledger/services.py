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


def post_sales_to_gl():
    """Hub batch: post every finalized sale not yet in the GL (idempotent)."""
    from teyssir.sales.models import Sale

    posted = 0
    already = set(
        JournalEntry.objects.filter(ref_type="SALE").values_list("ref_id", flat=True)
    )
    for sale in Sale.objects.filter(status=Sale.FINALIZED):
        if str(sale.id) not in already:
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


def post_all_to_gl():
    """Hub batch: post sales, goods receipts and on-account payments (all idempotent)."""
    from teyssir.customers.models import AccountEntry
    from teyssir.purchasing.models import GoodsReceipt

    sales = post_sales_to_gl()

    rcv = 0
    posted_rcv = set(JournalEntry.objects.filter(ref_type="RECEIPT").values_list("ref_id", flat=True))
    for gr in GoodsReceipt.objects.all():
        if str(gr.id) not in posted_rcv and post_goods_receipt_to_gl(gr):
            rcv += 1

    pay = 0
    posted_pay = set(JournalEntry.objects.filter(ref_type="ACCT_PAYMENT").values_list("ref_id", flat=True))
    for e in AccountEntry.objects.filter(entry_type=AccountEntry.PAYMENT):
        if str(e.id) not in posted_pay:
            post_account_payment_to_gl(e)
            pay += 1

    return {"sales": sales, "receipts": rcv, "payments": pay}


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
