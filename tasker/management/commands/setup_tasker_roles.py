"""Provision the standard Tasker groups with deterministic permissions."""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError


ROLE_PERMISSIONS = {
    "Tasker Employee": {
        "add_task", "change_task", "delete_task", "view_task",
        "view_taskcategory",
        "add_dailyplan", "change_dailyplan", "view_dailyplan",
        "add_dailyreview", "change_dailyreview", "view_dailyreview",
        "add_taskcomment", "view_taskcomment",
        "add_taskchecklistitem", "change_taskchecklistitem", "view_taskchecklistitem",
        "add_tasktimeentry", "view_tasktimeentry",
        "add_taskattachment", "change_taskattachment", "view_taskattachment",
        "view_taskactivity",
        "use_ai",
    },
    "Tasker Supervisor": {
        "add_task", "change_task", "delete_task", "view_task", "assign_task", "view_all_tasks",
        "view_taskcategory",
        "add_dailyplan", "change_dailyplan", "view_dailyplan",
        "add_dailyreview", "change_dailyreview", "view_dailyreview",
        "add_taskcomment", "view_taskcomment",
        "add_taskchecklistitem", "change_taskchecklistitem", "view_taskchecklistitem",
        "add_tasktimeentry", "view_tasktimeentry",
        "add_taskattachment", "change_taskattachment", "view_taskattachment",
        "view_taskactivity",
        "use_ai",
    },
}


class Command(BaseCommand):
    help = "Create or refresh Employee, Supervisor, Manager, and Administrator Tasker roles."

    def handle(self, *args, **options):
        available = {
            permission.codename: permission
            for permission in Permission.objects.filter(content_type__app_label="tasker")
        }
        if not available:
            raise CommandError("Tasker permissions are unavailable. Run migrations first.")

        all_permissions = set(available)
        roles = {
            **ROLE_PERMISSIONS,
            "Tasker Manager": all_permissions,
            "Tasker Administrator": all_permissions,
        }
        for role_name, codenames in roles.items():
            missing = codenames - all_permissions
            if missing:
                raise CommandError(f"Unknown Tasker permissions for {role_name}: {sorted(missing)}")
            group, created = Group.objects.get_or_create(name=role_name)
            group.permissions.set(available[codename] for codename in sorted(codenames))
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} {role_name} ({len(codenames)} permissions)"))
