from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase


class RBACSeedTests(TestCase):
    def test_seed_creates_roles_with_permissions(self):
        call_command("seed_rbac", verbosity=0)
        self.assertEqual(Group.objects.count(), 8)
        cashier = Group.objects.get(name="Cashier")
        codes = set(cashier.permissions.values_list("codename", flat=True))
        self.assertIn("create_sale", codes)
        self.assertNotIn("view_financial_reports", codes)  # least privilege

        auditor = Group.objects.get(name="Auditor")
        acodes = set(auditor.permissions.values_list("codename", flat=True))
        self.assertIn("view_audit_log", acodes)
        self.assertNotIn("create_sale", acodes)  # read-only role
