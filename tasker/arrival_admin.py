"""Owner or current scoped readers can inspect, never rewrite, arrival history."""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q

from tasker.arrival_models import ArrivalCorrection, ArrivalDeclaration
from tasker.services.arrival import arrival_teams_for, can_read_arrival


class ArrivalReadOnlyAdmin(admin.ModelAdmin):
    actions = ()
    list_per_page = 30

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        try:
            teams = arrival_teams_for(request.user)
        except PermissionDenied:
            return queryset.none()
        declarations = ArrivalDeclaration.objects.filter(
            Q(user=request.user) | Q(teams__in=teams, teams__memberships__user_id=F("user_id"),
                teams__memberships__is_active=True, teams__memberships__is_deleted=False, user__is_active=True)).values("pk")
        lookup = "pk__in" if self.model is ArrivalDeclaration else "arrival_id__in"
        return queryset.filter(**{lookup: declarations}).distinct()

    def get_readonly_fields(self, request, obj=None):
        # A declaration can snapshot several teams. Rendering the M2M would
        # disclose other teams' names to a manager who can read only one.
        return tuple(field.name for field in self.model._meta.fields)

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        if obj is None:
            return request.user.is_active and request.user.has_perm("tasker.view_task")
        arrival = obj if self.model is ArrivalDeclaration else obj.arrival
        try:
            return can_read_arrival(request.user, arrival)
        except PermissionDenied:
            return False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ArrivalDeclaration)
class ArrivalDeclarationAdmin(ArrivalReadOnlyAdmin):
    list_display = ("id", "user", "local_date", "registered_at", "timezone_name")
    list_filter = ("local_date", "timezone_name")


@admin.register(ArrivalCorrection)
class ArrivalCorrectionAdmin(ArrivalReadOnlyAdmin):
    list_display = ("id", "arrival_id", "actor", "effective_at", "created_at")
