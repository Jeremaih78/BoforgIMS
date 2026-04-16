from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


ROLE_PERMISSIONS = {
    "Finance Manager": [
        "credit_control_finance_manager",
        "view_debtoraccount",
        "change_debtoraccount",
        "view_creditoraccount",
        "change_creditoraccount",
        "add_collectiontask",
        "change_collectiontask",
        "view_collectiontask",
    ],
    "Accountant": [
        "credit_control_accountant",
        "view_debtoraccount",
        "change_debtoraccount",
        "view_creditoraccount",
        "change_creditoraccount",
        "add_promisetopay",
        "change_promisetopay",
        "view_promisetopay",
        "add_paymentdispute",
        "change_paymentdispute",
        "view_paymentdispute",
    ],
    "Collections Officer": [
        "credit_control_collections_officer",
        "add_debtorfollowup",
        "change_debtorfollowup",
        "view_debtorfollowup",
        "add_collectiontask",
        "change_collectiontask",
        "view_collectiontask",
        "add_promisetopay",
        "change_promisetopay",
        "view_promisetopay",
    ],
    "Sales": [
        "credit_control_sales",
        "view_debtoraccount",
        "view_debtorfollowup",
        "view_promisetopay",
    ],
    "Procurement": [
        "credit_control_procurement",
        "view_creditoraccount",
        "view_creditorfollowup",
        "view_collectiontask",
    ],
    "Viewer": [
        "credit_control_viewer",
        "view_debtoraccount",
        "view_creditoraccount",
        "view_collectiontask",
    ],
}


class Command(BaseCommand):
    help = "Create/update credit control role groups and permissions"

    def handle(self, *args, **options):
        for role, codes in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=role)
            perms = Permission.objects.filter(codename__in=codes)
            group.permissions.set(perms)
            self.stdout.write(self.style.SUCCESS(f"Configured role: {role} ({perms.count()} perms)"))
