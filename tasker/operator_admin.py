"""Read-only owner-scoped policy and recommendation evidence."""
from django.contrib import admin

from .operator_models import OperatorOverride, OperatorPolicy, OperatorSnapshot, OperatorState
from .workbench_admin import OwnerWorkAdmin


@admin.register(OperatorPolicy)
class OperatorPolicyAdmin(OwnerWorkAdmin):
    list_display = ("owner", "revision", "updated_at")


@admin.register(OperatorState)
class OperatorStateAdmin(OwnerWorkAdmin):
    list_display = ("owner", "reason", "updated_at")


@admin.register(OperatorSnapshot)
class OperatorSnapshotAdmin(OwnerWorkAdmin):
    list_display = ("id", "owner", "created_at", "expires_at")


@admin.register(OperatorOverride)
class OperatorOverrideAdmin(OwnerWorkAdmin):
    list_display = ("owner", "action", "created_at")
