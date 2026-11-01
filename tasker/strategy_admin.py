"""Operational evidence only; scenario and confirmation edits use domain services."""
from django.contrib import admin

from tasker.coordination_admin import OperationalEvidenceAdmin
from tasker.models import StrategyScenario, StrategyScenarioEvent, WorkDeclaration, WorkDeclarationEvent


@admin.register(WorkDeclaration)
class WorkDeclarationAdmin(OperationalEvidenceAdmin):
    list_display = ("user", "team", "confirmed_at", "revision")


@admin.register(WorkDeclarationEvent)
class WorkDeclarationEventAdmin(OperationalEvidenceAdmin):
    list_display = ("declaration", "actor", "created_at")


@admin.register(StrategyScenario)
class StrategyScenarioAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "owner", "team", "state", "created_at", "expires_at")
    list_filter = ("state",)


@admin.register(StrategyScenarioEvent)
class StrategyScenarioEventAdmin(OperationalEvidenceAdmin):
    list_display = ("scenario", "actor", "action", "created_at")
