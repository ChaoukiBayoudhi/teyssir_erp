import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from teyssir.billing.models import DocumentCounter, FiscalStampConfig
from teyssir.billing.services import allocate_document_number, resolve_fiscal_stamp


def _june_2026():
    return timezone.make_aware(datetime.datetime(2026, 6, 15, 12, 0))


class NumberingTests(TestCase):
    def test_format_and_gapless_increment(self):
        when = _june_2026()
        n1, s1, _ = allocate_document_number("C1", "FACTURE", when)
        n2, s2, _ = allocate_document_number("C1", "FACTURE", when)
        self.assertEqual(n1, "C1-202606-0001")
        self.assertEqual(n2, "C1-202606-0002")
        self.assertEqual((s1, s2), (1, 2))

    def test_per_terminal_isolation(self):
        when = _june_2026()
        allocate_document_number("C1", "FACTURE", when)
        n_c2, s_c2, _ = allocate_document_number("C2", "FACTURE", when)
        self.assertEqual(n_c2, "C2-202606-0001")  # C2 has its own series
        self.assertEqual(s_c2, 1)

    def test_monthly_reset(self):
        june = _june_2026()
        july = timezone.make_aware(datetime.datetime(2026, 7, 1, 9, 0))
        allocate_document_number("C1", "FACTURE", june)
        n_july, s_july, _ = allocate_document_number("C1", "FACTURE", july)
        self.assertEqual(n_july, "C1-202607-0001")  # reset for the new month
        self.assertEqual(s_july, 1)
        # the counters are distinct rows
        self.assertEqual(DocumentCounter.objects.count(), 2)


class StoreScopedNumberingTests(TestCase):
    """Phase 6: with a STORE_CODE set, numbers are globally unique across stores; default unchanged."""

    @override_settings(STORE_CODE="S1")
    def test_store_prefix_when_set(self):
        n, _, _ = allocate_document_number("C1", "FACTURE", _june_2026())
        self.assertEqual(n, "S1C1-202606-0001")

    @override_settings(STORE_CODE="S2")
    def test_avoir_segment_keeps_store_prefix(self):
        n, _, _ = allocate_document_number("C1", "AVOIR", _june_2026())
        self.assertEqual(n, "S2C1-AV-202606-0001")

    def test_default_single_store_is_unchanged(self):
        n, _, _ = allocate_document_number("C1", "FACTURE", _june_2026())
        self.assertEqual(n, "C1-202606-0001")   # STORE_CODE="" -> backward compatible


class StampTests(TestCase):
    def test_resolve_configured_stamp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.assertEqual(resolve_fiscal_stamp("FACTURE"), Decimal("1.000"))

    def test_unknown_doc_type_is_zero(self):
        self.assertEqual(resolve_fiscal_stamp("TICKET"), Decimal("0.000"))
