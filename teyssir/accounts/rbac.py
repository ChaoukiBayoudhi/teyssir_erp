"""Role -> capability-permission mapping (spec §10.2).

Permissions are the codenames declared on `RBACAnchor.Meta.permissions`. `seed_rbac`
creates one Django Group per role and attaches these permissions.
"""

ROLE_PERMISSIONS = {
    "Administrator": ["manage_users", "configure_system", "view_audit_log"],
    "Owner": [
        "create_sale", "void_refund", "edit_product", "adjust_stock",
        "manage_purchasing", "open_close_cash", "view_financial_reports", "view_audit_log",
    ],
    "Manager": [
        "create_sale", "void_refund", "edit_product", "adjust_stock",
        "manage_purchasing", "open_close_cash", "view_financial_reports",
    ],
    "Cashier": ["create_sale", "open_close_cash"],
    "Seller": ["create_sale"],
    "InventoryManager": ["edit_product", "adjust_stock", "manage_purchasing"],
    "Accountant": ["view_financial_reports"],
    "Auditor": ["view_financial_reports", "view_audit_log"],
}
