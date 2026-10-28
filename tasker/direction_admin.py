"""Read-only admin inspection; management forms preserve acceptance/audit rules."""
from django.contrib import admin

from .models import (
    BaselineObservation, BaselineStudy, Goal, Initiative, KeyResult,
    OrganizationAccess, OrganizationEvent, OutcomeMeasurement, TaskContribution,
    Team, TeamMembership,
)


class DirectionAdmin(admin.ModelAdmin):
    actions = ()
    list_per_page = 30

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        from .organization import can_manage_company, visible_companies_for, visible_teams_for
        from .policies import visible_tasks_for
        from .services.goals import visible_goals_for, visible_initiatives_for, visible_key_results_for, visible_outcomes_for
        from .services.baseline import visible_studies_for, visible_observations_for
        queryset = super().get_queryset(request)
        selectors = {Team: visible_teams_for, Goal: visible_goals_for, KeyResult: visible_key_results_for,
                     Initiative: visible_initiatives_for, OutcomeMeasurement: visible_outcomes_for,
                     BaselineStudy: visible_studies_for, BaselineObservation: visible_observations_for}
        if self.model in selectors:
            return queryset.filter(pk__in=selectors[self.model](request.user).values("pk"))
        if self.model is TeamMembership:
            return queryset.filter(team__in=visible_teams_for(request.user))
        if self.model is TaskContribution:
            return queryset.filter(task__in=visible_tasks_for(request.user), initiative__in=visible_initiatives_for(request.user))
        if self.model is OrganizationAccess:
            return queryset if request.user.is_superuser else queryset.filter(user=request.user, is_active=True, is_deleted=False)
        if self.model is OrganizationEvent:
            if request.user.is_superuser and request.user.is_active:
                return queryset
            companies = [c.pk for c in visible_companies_for(request.user) if can_manage_company(request.user, c)]
            return queryset.filter(company_id__in=companies)
        return queryset.none()


for model in (Team, TeamMembership, OrganizationAccess, OrganizationEvent, Goal, KeyResult,
              Initiative, TaskContribution, OutcomeMeasurement, BaselineStudy, BaselineObservation):
    admin.site.register(model, DirectionAdmin)
