"""Render a finalized sale into an 80 mm ESC/POS receipt (spec §13.5).

Arabic on thermal printers needs a printer-side codepage (or image printing); the Latin/
numeric receipt below is the default. `render_text` gives a human-readable preview used by tests.
"""
from collections import defaultdict

from django.conf import settings

from teyssir.core.money import display

from .escpos import Escpos


def _rate(rate):
    """Render a TVA rate without trailing zeros: 7.00 -> '7', 7.50 -> '7.5'."""
    if rate == rate.to_integral_value():
        return str(int(rate))
    return f"{rate.normalize():f}".rstrip("0").rstrip(".")


def _receipt_model(sale, store_name="Teyssir Library"):
    invoice = getattr(sale, "invoice", None)
    by_rate = defaultdict(lambda: [0, 0])  # rate -> [base, tax]
    for line in sale.lines.select_related("product").all():
        base = line.line_total
        rate = line.tax_rate
        tax = (base * rate / 100)
        by_rate[rate][0] += base
        by_rate[rate][1] += tax
    return {
        "store": store_name,
        "matricule_fiscal": getattr(settings, "STORE_MATRICULE_FISCAL", ""),
        "number": invoice.fiscal_number if invoice else "",
        "terminal": sale.terminal,
        "lines": [
            (line.product.name_fr, line.qty, line.unit_price, line.line_total)
            for line in sale.lines.select_related("product").all()
        ],
        "by_rate": sorted(by_rate.items()),
        "subtotal": sale.subtotal,
        "tax_total": sale.tax_total,
        "timbre": sale.timbre_amount_snapshot,
        "total": sale.total,
        "payments": [(p.method, p.amount) for p in sale.payments.all()],
    }


def render_sale_receipt(sale, store_name="Teyssir Library"):
    """Return the ESC/POS byte stream for a sale's receipt."""
    m = _receipt_model(sale, store_name)
    p = Escpos()
    p.align("center").bold(True).size(2, 2).line(m["store"]).size(1, 1).bold(False)
    if m["matricule_fiscal"]:
        p.line(f"MF: {m['matricule_fiscal']}")
    p.feed().align("left")
    p.line(f"Facture: {m['number']}")
    p.line(f"Caisse:  {m['terminal']}")
    p.rule()
    for name, qty, unit, total in m["lines"]:
        p.line(name[: p.width])
        p.row(f"  {qty} x {display(unit)}", f"{display(total)} DT")
    p.rule()
    p.row("Sous-total", f"{display(m['subtotal'])} DT")
    for rate, (base, tax) in m["by_rate"]:
        p.row(f"TVA {_rate(rate)}%", f"{display(tax)} DT")
    p.row("Timbre fiscal", f"{display(m['timbre'])} DT")
    p.bold(True).size(1, 2).row("TOTAL", f"{display(m['total'])} DT").size(1, 1).bold(False)
    for method, amount in m["payments"]:
        p.row(method, f"{display(amount)} DT")
    p.feed().align("center").line("Merci de votre visite").feed(3).cut().kick()
    return p.bytes()


def render_text(sale, store_name="Teyssir Library", width=42):
    """Plain-text preview of the same receipt (no control bytes) — handy for tests/UI."""
    m = _receipt_model(sale, store_name)
    out = [m["store"].center(width), ""]
    if m["matricule_fiscal"]:
        out.append(f"MF: {m['matricule_fiscal']}")
    out += [f"Facture: {m['number']}", f"Caisse:  {m['terminal']}", "-" * width]

    def row(left, right):
        gap = max(1, width - len(left) - len(right))
        return left + " " * gap + right

    for name, qty, unit, total in m["lines"]:
        out.append(name[:width])
        out.append(row(f"  {qty} x {display(unit)}", f"{display(total)} DT"))
    out.append("-" * width)
    out.append(row("Sous-total", f"{display(m['subtotal'])} DT"))
    for rate, (base, tax) in m["by_rate"]:
        out.append(row(f"TVA {_rate(rate)}%", f"{display(tax)} DT"))
    out.append(row("Timbre fiscal", f"{display(m['timbre'])} DT"))
    out.append(row("TOTAL", f"{display(m['total'])} DT"))
    for method, amount in m["payments"]:
        out.append(row(method, f"{display(amount)} DT"))
    out += ["", "Merci de votre visite".center(width)]
    return "\n".join(out)
