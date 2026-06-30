import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Per-user account (no shared logins — spec §18). `manager_pin` holds a *hashed*
    PIN used to authorise sensitive POS actions (void/refund/discount, spec §13).

    UUID primary key so a user has a *stable identity across nodes*: users are authored on
    the hub and replicated to tills (master data), which lets `Sale.created_by` /
    `CashSession.user` FKs resolve when a till pushes back to the hub (spec §4.4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    preferred_language = models.CharField(
        max_length=2, choices=[("fr", "Français"), ("ar", "العربية")], default="fr"
    )
    manager_pin = models.CharField(max_length=128, blank=True, default="")

    def __str__(self):
        return self.get_username()


class RBACAnchor(models.Model):
    """Anchor model that owns Teyssir's capability permissions (spec §10).

    No rows are ever created; it exists only to give the custom permissions a
    content type so they can be attached to role Groups by `seed_rbac`.
    """

    class Meta:
        managed = True
        default_permissions = ()
        permissions = [
            ("create_sale", "Create and finalize a sale"),
            ("void_refund", "Void or refund a sale"),
            ("edit_product", "Create/edit products and prices"),
            ("adjust_stock", "Adjust stock"),
            ("manage_purchasing", "Purchase orders and receiving"),
            ("open_close_cash", "Open/close cash session"),
            ("view_financial_reports", "View financial reports"),
            ("manage_users", "Manage users and roles"),
            ("configure_system", "Configure devices/backups"),
            ("view_audit_log", "View the audit log"),
        ]
