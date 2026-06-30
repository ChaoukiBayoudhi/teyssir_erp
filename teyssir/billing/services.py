from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DocumentCounter, FiscalStampConfig, Invoice

# Doc-type segment in the fiscal number. FACTURE stays bare ("C1-202606-0001", as confirmed);
# other documents carry a code so an avoir/ticket can never collide with a facture number.
_TYPE_CODE = {"FACTURE": "", "TICKET": "T", "AVOIR": "AV"}


@transaction.atomic
def allocate_document_number(terminal, doc_type, when=None):
    """Atomically allocate the next gapless number for (terminal, YYYY, MM, doc_type).

    Returns ``(number, seq, when)`` with ``number`` formatted ``C1-YYYYMM-XXXX`` (or
    ``S1C1-YYYYMM-XXXX`` when a multi-store ``STORE_CODE`` is set, for global uniqueness) —
    4-digit zero-padded sequence that auto-widens past 9999 so gaplessness never breaks.
    Spec §4.4. Row-locked so two concurrent finalizes can never get the same seq.
    """
    when = when or timezone.now()
    counter, _ = DocumentCounter.objects.get_or_create(
        terminal=terminal, year=when.year, month=when.month, doc_type=doc_type,
        defaults={"seq": 0},
    )
    # Re-fetch under a row lock before incrementing (no-op lock on SQLite, which
    # already serialises writes; real contention is at the PostgreSQL hub).
    counter = DocumentCounter.objects.select_for_update().get(pk=counter.pk)
    counter.seq += 1
    counter.save(update_fields=["seq"])
    seg = _TYPE_CODE.get(doc_type, doc_type[:2].upper())
    mid = f"{seg}-" if seg else ""
    prefix = f"{settings.STORE_CODE}{terminal}"      # store-scoped when STORE_CODE is set
    number = f"{prefix}-{mid}{when.year}{when.month:02d}-{counter.seq:04d}"
    return number, counter.seq, when


def resolve_fiscal_stamp(doc_type):
    """Resolve the configured timbre fiscal for a doc type; 0 if none/inactive (spec §2 M2b)."""
    cfg = FiscalStampConfig.objects.filter(doc_type=doc_type, active=True).first()
    return cfg.amount if cfg else Decimal("0.000")


@transaction.atomic
def issue_invoice(sale, doc_type=Invoice.FACTURE, when=None):
    """Allocate the number and snapshot the stamp onto a new immutable invoice (spec §7.3)."""
    number, seq, when = allocate_document_number(sale.terminal, doc_type, when)
    stamp = resolve_fiscal_stamp(doc_type)
    return Invoice.objects.create(
        sale=sale,
        doc_type=doc_type,
        store_code=settings.STORE_CODE,
        terminal=sale.terminal,
        year=when.year,
        month=when.month,
        seq=seq,
        fiscal_number=number,
        timbre_amount_snapshot=stamp,
    )
