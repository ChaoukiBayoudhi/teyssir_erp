"""Render a finalized sale into an 80 mm ESC/POS receipt (spec §13.5).

Arabic on thermal printers needs a printer-side codepage (or image printing); the Latin/
numeric receipt below is the default. `render_text` gives a human-readable preview used by tests.
"""
from collections import defaultdict

from django.conf import settings

from teyssir.core.money import display, line_tax, to_money

from .escpos import Escpos


def _rate(rate):
    """Render a TVA rate without trailing zeros: 7.00 -> '7', 7.50 -> '7.5'."""
    if rate == rate.to_integral_value():
        return str(int(rate))
    return f"{rate.normalize():f}".rstrip("0").rstrip(".")


def _receipt_model(sale, store_name="Teyssir Library"):
    """Build the receipt view-model. TVA uses ``line_tax`` (millime HALF_UP) so printed
    breakdown matches booked ``sale.tax_total`` — never a raw float multiply."""
    invoice = getattr(sale, "invoice", None)
    by_rate = defaultdict(lambda: [to_money(0), to_money(0)])  # rate -> [base, tax]
    lines = list(sale.lines.select_related("product").all())
    for line in lines:
        base = line.line_total
        rate = line.tax_rate
        tax = line_tax(base, rate)
        by_rate[rate][0] = to_money(by_rate[rate][0] + base)
        by_rate[rate][1] = to_money(by_rate[rate][1] + tax)
    payments = list(sale.payments.all())
    return {
        "store": store_name,
        "matricule_fiscal": getattr(settings, "STORE_MATRICULE_FISCAL", ""),
        "number": invoice.fiscal_number if invoice else "",
        "terminal": sale.terminal,
        "lines": [
            (line.product.name_fr, line.qty, line.unit_price, line.discount, line.line_total)
            for line in lines
        ],
        "discount": sale.discount,
        "by_rate": sorted(by_rate.items()),
        "subtotal": sale.subtotal,
        "tax_total": sale.tax_total,
        "timbre": sale.timbre_amount_snapshot,
        "total": sale.total,
        "payments": [(p.method, p.amount) for p in payments],
    }


def render_sale_receipt(sale, store_name="Teyssir Library", *, duplicate=False, kick=True):
    """Return the ESC/POS byte stream for a sale's receipt.

    ``duplicate=True`` marks a reprint (DUPLICATA) without creating a new sale.
    ``kick=False`` skips the cash-drawer pulse (preferred on reprints).
    """
    m = _receipt_model(sale, store_name)
    p = Escpos()
    p.align("center").bold(True).size(2, 2).line(m["store"]).size(1, 1).bold(False)
    if duplicate:
        p.bold(True).line("*** DUPLICATA ***").bold(False)
    if m["matricule_fiscal"]:
        p.line(f"MF: {m['matricule_fiscal']}")
    p.feed().align("left")
    p.line(f"Facture: {m['number']}")
    p.line(f"Caisse:  {m['terminal']}")
    p.rule()
    for name, qty, unit, disc, total in m["lines"]:
        p.line(name[: p.width])
        left = f"  {qty} x {display(unit)}"
        if disc:
            left += f" -{display(disc)}"
        p.row(left, f"{display(total)} DT")
    p.rule()
    p.row("Sous-total HT", f"{display(m['subtotal'])} DT")
    for rate, (base, tax) in m["by_rate"]:
        p.row(f"TVA {_rate(rate)}% (base {display(base)})", f"{display(tax)} DT")
    p.row("Timbre fiscal", f"{display(m['timbre'])} DT")
    p.bold(True).size(1, 2).row("TOTAL TTC", f"{display(m['total'])} DT").size(1, 1).bold(False)
    for method, amount in m["payments"]:
        p.row(method, f"{display(amount)} DT")
    p.feed().align("center").line("Merci de votre visite").feed(3).cut()
    if kick:
        p.kick()
    return p.bytes()


def render_text(sale, store_name="Teyssir Library", width=42, *, duplicate=False):
    """Plain-text preview of the same receipt (no control bytes) — handy for tests/UI."""
    m = _receipt_model(sale, store_name)
    out = [m["store"].center(width), ""]
    if duplicate:
        out.append("*** DUPLICATA ***".center(width))
    if m["matricule_fiscal"]:
        out.append(f"MF: {m['matricule_fiscal']}")
    out += [f"Facture: {m['number']}", f"Caisse:  {m['terminal']}", "-" * width]

    def row(left, right):
        gap = max(1, width - len(left) - len(right))
        return left + " " * gap + right

    for name, qty, unit, disc, total in m["lines"]:
        out.append(name[:width])
        left = f"  {qty} x {display(unit)}"
        if disc:
            left += f" -{display(disc)}"
        out.append(row(left, f"{display(total)} DT"))
    out.append("-" * width)
    out.append(row("Sous-total HT", f"{display(m['subtotal'])} DT"))
    for rate, (base, tax) in m["by_rate"]:
        out.append(row(f"TVA {_rate(rate)}%", f"{display(tax)} DT"))
    out.append(row("Timbre fiscal", f"{display(m['timbre'])} DT"))
    out.append(row("TOTAL TTC", f"{display(m['total'])} DT"))
    for method, amount in m["payments"]:
        out.append(row(method, f"{display(amount)} DT"))
    out += ["", "Merci de votre visite".center(width)]
    return "\n".join(out)
