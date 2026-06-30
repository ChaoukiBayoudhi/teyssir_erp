from decimal import Decimal

from django.test import SimpleTestCase

from teyssir.core import money


class MoneyTests(SimpleTestCase):
    def test_store_scale_is_millime(self):
        self.assertEqual(money.to_money("0.850"), Decimal("0.850"))
        # half-up at the 3rd decimal
        self.assertEqual(money.to_money("1.2505"), Decimal("1.251"))

    def test_display_two_dp_half_up(self):
        self.assertEqual(money.display(Decimal("1.250")), "1.25")
        self.assertEqual(money.display(Decimal("1.255")), "1.26")

    def test_no_float_artifacts(self):
        # 0.1 + 0.2 == 0.30000000000000004 as a float; must still be exactly 0.300
        self.assertEqual(money.to_money(0.1 + 0.2), Decimal("0.300"))

    def test_tva_rates(self):
        self.assertEqual(money.line_tax("10.000", 7), Decimal("0.700"))
        self.assertEqual(money.line_tax("10.000", 19), Decimal("1.900"))
        self.assertEqual(money.line_tax("10.000", 0), Decimal("0.000"))
