"""Unit tests for unified POS / catalogue product search ranking."""
from decimal import Decimal

from django.test import TestCase

from teyssir.catalog.models import Barcode, Product, TaxRate
from teyssir.catalog.search import looks_like_code, lookup_by_code, search_products


class ProductSearchServiceTests(TestCase):
    def setUp(self):
        self.tva = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.pen = Product.objects.create(
            sku="PEN-001", reference="PEN-001", name_fr="Stylo Bic bleu",
            tax_rate=self.tva, sale_price=Decimal("0.850"),
        )
        Barcode.objects.create(product=self.pen, value="6191234567890")
        self.book = Product.objects.create(
            sku="9782070612758", name_fr="الأمير الصغير / Le Petit Prince",
            name_ar="الأمير الصغير", tax_rate=self.tva, sale_price=Decimal("15.000"),
            product_type=Product.BOOK, is_book=True, isbn="9782070612758",
        )
        self.sac = Product.objects.create(
            sku="1001", reference="1001", name_fr="Sac à dos",
            tax_rate=self.tva, sale_price=Decimal("45.000"),
        )

    def test_looks_like_code(self):
        self.assertTrue(looks_like_code("1001"))
        self.assertTrue(looks_like_code("6191234567890"))
        self.assertTrue(looks_like_code("PEN-001"))
        self.assertFalse(looks_like_code("Stylo"))
        self.assertFalse(looks_like_code("Sac à dos"))
        self.assertFalse(looks_like_code(""))

    def test_name_partial_and_case(self):
        self.assertEqual(list(search_products("stylo").values_list("name_fr", flat=True)),
                         ["Stylo Bic bleu"])
        self.assertEqual(list(search_products("BIC").values_list("name_fr", flat=True)),
                         ["Stylo Bic bleu"])

    def test_arabic_and_accents(self):
        self.assertIn(self.book.id, search_products("الأمير").values_list("id", flat=True))
        self.assertIn(self.sac.id, search_products("à dos").values_list("id", flat=True))

    def test_reference_and_barcode(self):
        self.assertEqual(lookup_by_code("1001").get().id, self.sac.id)
        self.assertEqual(lookup_by_code("6191234567890").get().id, self.pen.id)
        self.assertEqual(search_products("PEN-001").get().id, self.pen.id)

    def test_rank_exact_before_contains(self):
        Product.objects.create(
            sku="X1", name_fr="Super Stylo Bic bleu Deluxe", tax_rate=self.tva,
            sale_price=Decimal("2.000"),
        )
        ids = list(search_products("Stylo Bic bleu").values_list("id", flat=True))
        self.assertEqual(ids[0], self.pen.id)
