from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from teyssir.accounts.rbac import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Create the role Groups and attach capability permissions (spec §10)."

    def handle(self, *args, **options):
        for role, codenames in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=role)
            perms = list(Permission.objects.filter(codename__in=codenames))
            missing = set(codenames) - {p.codename for p in perms}
            if missing:
                self.stderr.write(
                    self.style.WARNING(f"{role}: unknown permissions {sorted(missing)}")
                )
            group.permissions.set(perms)
            self.stdout.write(f"  {role}: {group.permissions.count()} permissions")
        self.stdout.write(self.style.SUCCESS("RBAC seeded."))
