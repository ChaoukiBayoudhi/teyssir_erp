import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teyssir.customers.models import Customer
from teyssir.customers.services import balance, charge_account, post_payment, statement
from teyssir.sync.models import SyncOutbox
from teyssir.sync.services import apply_push

User = get_user_model()


class AccountLedgerTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="École Ibn Khaldoun", credit_limit=Decimal("1000.000"),
        )

    def test_charge_payment_balance_and_statement(self):
        charge_account(self.customer, Decimal("100.000"), "SALE", "a")
        charge_account(self.customer, Decimal("50.000"), "SALE", "b")
        self.assertEqual(balance(self.customer), Decimal("150.000"))

        post_payment(self.customer, Decimal("120.000"))
        self.assertEqual(balance(self.customer), Decimal("30.000"))

        st = statement(self.customer)
        self.assertEqual(st["balance"], "30.000")
        self.assertEqual(len(st["entries"]), 3)
        self.assertEqual(st["entries"][-1]["balance"], "30.000")   # running balance

    def test_customer_detail_includes_balance(self):
        charge_account(self.customer, Decimal("80.000"), "SALE", "x")
        user = User.objects.create_user("u", password="pw-strong-123")
        api = APIClient()
        api.force_authenticate(user)
        r = api.get(f"/api/v1/customers/{self.customer.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["balance"], "80.000")

    def test_payment_endpoint_and_statement(self):
        charge_account(self.customer, Decimal("80.000"), "SALE", "c")
        user = User.objects.create_user("cashier", password="pw-strong-123")
        api = APIClient()
        api.force_authenticate(user)

        r = api.post(f"/api/v1/customers/{self.customer.id}/payment/", {"amount": "30.000"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["balance"], "50.000")

        r = api.get(f"/api/v1/customers/{self.customer.id}/statement/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["balance"], "50.000")

    def test_rejects_negative_or_zero_payment(self):
        from teyssir.customers.services import AccountAmountError
        with self.assertRaises(AccountAmountError):
            post_payment(self.customer, Decimal("-10.000"))
        with self.assertRaises(AccountAmountError):
            post_payment(self.customer, Decimal("0"))
        user = User.objects.create_user("c2", password="pw-strong-123")
        api = APIClient()
        api.force_authenticate(user)
        r = api.post(f"/api/v1/customers/{self.customer.id}/payment/", {"amount": "-2222"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_rejects_payment_exceeding_balance(self):
        """Root cause of Solde -2222: règlement larger than owed (or on zero balance)."""
        from teyssir.customers.services import AccountAmountError
        # balance 0 — any payment would go negative
        with self.assertRaises(AccountAmountError):
            post_payment(self.customer, Decimal("2222.000"))
        charge_account(self.customer, Decimal("50.000"), "SALE", "z")
        with self.assertRaises(AccountAmountError):
            post_payment(self.customer, Decimal("50.001"))
        post_payment(self.customer, Decimal("50.000"))  # exact OK
        self.assertEqual(balance(self.customer), Decimal("0.000"))


class CustomerSyncTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="École X", origin_terminal="C1")

    def test_charge_enqueues_account_entry(self):
        charge_account(self.customer, Decimal("100.000"), "SALE", "x")
        entry = SyncOutbox.objects.get(entity="customers.AccountEntry")
        self.assertEqual(json.loads(entry.payload)[0]["model"], "customers.accountentry")

    def test_api_create_enqueues_customer_and_round_trips_to_hub(self):
        user = User.objects.create_user("cashier", password="pw-strong-123")
        api = APIClient()
        api.force_authenticate(user)
        r = api.post("/api/v1/customers/", {"name": "École Y"}, format="json")
        self.assertEqual(r.status_code, 201)
        cid = r.json()["id"]

        entry = SyncOutbox.objects.get(entity="customers.Customer")
        Customer.objects.filter(pk=cid).delete()                 # hub doesn't have it yet
        apply_push([{"id": str(entry.id), "seq": entry.seq, "payload": entry.payload}])
        self.assertEqual(Customer.objects.filter(pk=cid).count(), 1)
