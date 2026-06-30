"""Sync service layer (spec §4.4).

Three movements, all made *correct and boring* by the invariants in the spec:
  - **enqueue_*** (till): record local transactional writes into the append-only outbox.
  - **apply_push** (hub): idempotently apply a till's outbox aggregates, then re-fold stock.
  - **collect_master_changes** (hub) / **apply_master_changes** (till): one-way master-data replica.
"""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from teyssir.core.money import to_money
from teyssir.inventory.services import recompute_on_hand

from .models import SyncOutbox
from .serialization import dump, load


# --- Till side: populate the outbox -----------------------------------------
def _next_seq(terminal):
    last = SyncOutbox.objects.filter(origin_terminal=terminal).aggregate(m=Max("seq"))["m"] or 0
    return last + 1


@transaction.atomic
def enqueue(entity, entity_id, payload, terminal, op="UPSERT"):
    return SyncOutbox.objects.create(
        entity=entity, entity_id=str(entity_id), op=op, payload=payload,
        origin_terminal=terminal, seq=_next_seq(terminal),
    )


def enqueue_sale(sale):
    """Bundle a finalized sale aggregate (sale + lines + payments + invoice + stock
    movements) into ONE outbox entry, applied atomically at the hub (spec §4.4)."""
    from teyssir.billing.models import Invoice
    from teyssir.inventory.models import StockMovement

    objs = [sale]
    objs += list(sale.lines.all())
    objs += list(sale.payments.all())
    objs += list(Invoice.objects.filter(sale=sale))
    objs += list(StockMovement.objects.filter(ref_type="SALE", ref_id=str(sale.id)))
    return enqueue("sales.Sale", sale.id, dump(objs), sale.terminal)


def enqueue_return(ret):
    """Bundle a credit-note (AVOIR) aggregate: Return + lines + RETURN stock movements (§4.4)."""
    from teyssir.inventory.models import StockMovement

    objs = [ret]
    objs += list(ret.lines.all())
    objs += list(StockMovement.objects.filter(ref_type="RETURN", ref_id=str(ret.id)))
    return enqueue("sales.Return", ret.id, dump(objs), ret.terminal)


def enqueue_cash_session(session):
    """Sync a cash session (open/close) so the hub has consolidated cash + variance (§13.3).
    Resolves now that users replicate hub->till and `CashSession.user` is a stable UUID."""
    return enqueue("sales.CashSession", session.id, dump([session]), session.terminal)


def enqueue_quotation(quotation):
    """Bundle a quotation aggregate (Quotation + lines) for the hub (§4.4)."""
    objs = [quotation] + list(quotation.lines.all())
    return enqueue("quotations.Quotation", quotation.id, dump(objs), quotation.terminal)


def enqueue_reservation(reservation):
    """Bundle a reservation for the hub (§4.4)."""
    return enqueue("quotations.Reservation", reservation.id, dump([reservation]), reservation.terminal)


def enqueue_movements(movements, terminal):
    """Enqueue a batch of stock movements (e.g. stock-take adjustments) so the hub re-folds
    on-hand from the union of every node's movements (§4.4)."""
    movements = list(movements)
    if not movements:
        return None
    return enqueue("inventory.StockMovement", movements[0].id, dump(movements), terminal)


def enqueue_customer(customer):
    """Sync a (possibly till-quick-created) customer to the hub; deduped by UUID (§4.4)."""
    return enqueue("customers.Customer", customer.id, dump([customer]),
                   customer.origin_terminal or "")


def enqueue_account_entry(entry):
    """Sync a customer-account charge/payment so the hub keeps the consolidated balance (§M9)."""
    return enqueue("customers.AccountEntry", entry.id, dump([entry]),
                   entry.origin_terminal or "")


# --- Hub side: apply pushed transactions ------------------------------------
@transaction.atomic
def apply_push(entries):
    """Apply a batch of outbox entries from a till (spec §4.4).

    Idempotent: rows are upserted by UUID; stock is re-derived as a *fold* over the
    movement ledger (not incrementally), so re-delivery is a no-op and concurrent
    offline sales merge by union. Post-merge negative on-hand is surfaced as a
    manager reconciliation warning, never an error.
    """
    applied = []
    affected_products = set()
    for entry in sorted(entries, key=lambda e: e["seq"]):
        with transaction.atomic():
            for dobj in load(entry["payload"]):
                dobj.save()
                if dobj.object.__class__.__name__ == "StockMovement":
                    affected_products.add(dobj.object.product_id)
        applied.append({"id": entry.get("id"), "seq": entry["seq"]})

    warnings = []
    for pid in affected_products:
        on_hand = recompute_on_hand(pid)
        if on_hand < 0:
            warnings.append({"product_id": str(pid), "on_hand": str(on_hand)})
    return {"applied": applied, "reconciliation_warnings": warnings}


# --- Hub side: serve master-data changes; Till side: apply them -------------
MASTER_MODELS_ORDERED = ("TaxRate", "Category", "Product", "Barcode")


def collect_master_changes(since=None):
    """Return hub-authoritative master-data rows changed since `since` (a datetime), in
    FK-dependency order, plus a small **config snapshot** (e.g. the fiscal stamp), plus a
    fresh server-side cursor (spec §4.4). Config tables are tiny, so we ship them whole and
    upsert by natural key on the till — no per-row change-tracking needed."""
    from teyssir.billing.models import FiscalStampConfig
    from teyssir.catalog.models import (
        Barcode, Book, BookContributor, Category, Contributor, Product, TaxRate,
    )

    from django.contrib.auth.models import Group

    from teyssir.accounts.models import User

    now = timezone.now()
    models_by_name = {"TaxRate": TaxRate, "Category": Category, "Product": Product, "Barcode": Barcode}
    objs = []
    for name in MASTER_MODELS_ORDERED:
        qs = models_by_name[name].objects.all()
        if since:
            qs = qs.filter(updated_at__gt=since)
        objs += list(qs.order_by("created_at"))

    # Book bibliographic profile: Contributor -> Book (FK Product, already above) -> BookContributor.
    # Cover images replicate separately (media replication, docs/BOOK-OCR-ARCHITECTURE §5).
    for Model in (Contributor, Book, BookContributor):
        qs = Model.objects.all()
        if since:
            qs = qs.filter(updated_at__gt=since)
        objs += list(qs.order_by("created_at"))

    # Identity (hub-authored, small N -> shipped whole): groups before users (User.groups m2m),
    # so tills can authenticate + enforce RBAC offline and created_by/cash_session.user FKs resolve
    # when a till pushes back to the hub (§4.4). Password hashes ride along (raw save preserves them).
    # Assumes identical releases across nodes so Permission/ContentType pks are stable.
    objs += list(Group.objects.all().order_by("id"))
    objs += list(User.objects.all().order_by("date_joined"))

    config = {
        "fiscal_stamps": [
            {"doc_type": c.doc_type, "amount": str(c.amount), "active": c.active}
            for c in FiscalStampConfig.objects.all()
        ],
    }
    return {"records": dump(objs), "config": config, "cursor": now.isoformat()}


@transaction.atomic
def apply_master_changes(records_json, config=None):
    """Apply a master-data replica update on a till (hub-authoritative, last-write-wins).
    Records are upserted by UUID; the config snapshot is upserted by natural key (§4.4)."""
    from teyssir.billing.models import FiscalStampConfig

    count = 0
    for dobj in load(records_json):
        dobj.save()
        count += 1
    if config:
        for c in config.get("fiscal_stamps", []):
            FiscalStampConfig.objects.update_or_create(
                doc_type=c["doc_type"],
                defaults={"amount": to_money(c["amount"]), "active": c.get("active", True)},
            )
    return count
