"""Scoped forms for the goals, teams and measurement workspace."""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import (
    BaselineObservation, BaselineStudy, Goal, Initiative, KeyResult,
    OrganizationAccess, OutcomeMeasurement, Task, Team, TeamMembership,
)
from .organization import (
    can_manage_company, can_manage_team, visible_companies_for, visible_teams_for,
)
from .policies import visible_tasks_for
from .services.goals import visible_goals_for, visible_initiatives_for, visible_key_results_for
from .services.baseline import visible_studies_for


def manageable_companies(user):
    companies = visible_companies_for(user)
    return companies.filter(pk__in=[c.pk for c in companies if can_manage_company(user, c)])


def manageable_teams(user):
    teams = visible_teams_for(user)
    return teams.filter(pk__in=[t.pk for t in teams if can_manage_team(user, t)])


def scoped_people(user):
    users = get_user_model().objects.filter(is_active=True)
    if user.is_superuser:
        return users
    member_ids = TeamMembership.objects.filter(team__in=visible_teams_for(user), is_active=True).values("user_id")
    grant_ids = OrganizationAccess.objects.filter(company__in=manageable_companies(user), is_active=True).values("user_id")
    return users.filter(Q(pk__in=member_ids) | Q(pk__in=grant_ids) | Q(pk=user.pk)).distinct()


class ScopedForm(forms.ModelForm):
    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control")
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d")
        if "company" in self.fields:
            self.fields["company"].queryset = manageable_companies(user)
        if "team" in self.fields:
            self.fields["team"].queryset = manageable_teams(user)
        if "owner" in self.fields:
            self.fields["owner"].queryset = scoped_people(user)
        for name in ("description", "evidence_policy", "evidence", "output_definition", "acceptance_criteria", "effort_definition", "quality_definition", "source_definition", "comparison_plan"):
            if name in self.fields:
                self.fields[name].widget = forms.Textarea(attrs={"class": "form-control", "rows": 3})


class TeamForm(ScopedForm):
    class Meta:
        model = Team
        fields = ("company", "name", "description")

    def clean_company(self):
        company = self.cleaned_data["company"]
        if self.instance.pk and company.pk != self.instance.company_id:
            raise forms.ValidationError("A team cannot be moved to another company. Create a new team instead.")
        return company


class MembershipForm(ScopedForm):
    class Meta:
        model = TeamMembership
        fields = ("team", "user", "role", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Roster administration requires company authority, not manager task access.
        self.fields["team"].queryset = Team.objects.filter(company__in=manageable_companies(self.user))
        self.fields["user"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            for name in ("team", "user"):
                if name in data and getattr(data[name], "pk", None) != getattr(self.instance, f"{name}_id"):
                    self.add_error(name, "Deactivate this membership and create another to preserve its history.")
        return data


class OrganizationAccessForm(ScopedForm):
    class Meta:
        model = OrganizationAccess
        fields = ("company", "user", "is_active", "can_manage", "can_view_all", "can_receive_reports")

    def clean(self):
        data = super().clean()
        if not self.user.is_superuser:
            raise forms.ValidationError("A system administrator must configure company-wide access and report recipient rights.")
        if self.instance.pk:
            for name in ("company", "user"):
                if name in data and data[name].pk != getattr(self.instance, f"{name}_id"):
                    self.add_error(name, "Deactivate this grant and create a separate grant instead.")
        return data


class GoalForm(ScopedForm):
    class Meta:
        model = Goal
        fields = ("company", "team", "title", "description", "owner", "status", "start_date", "end_date", "evidence_policy")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Team managers can author their own team's goals; company-wide goals
        # still require company authority in the save service.
        self.fields["company"].queryset = visible_companies_for(self.user)


class KeyResultForm(ScopedForm):
    class Meta:
        model = KeyResult
        fields = ("goal", "title", "owner", "unit", "baseline", "target", "direction", "aggregation", "start_date", "end_date", "evidence_policy")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["goal"].queryset = visible_goals_for(self.user)


class InitiativeForm(ScopedForm):
    class Meta:
        model = Initiative
        fields = ("goal", "key_result", "title", "description", "owner")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["goal"].queryset = visible_goals_for(self.user)
        self.fields["key_result"].queryset = visible_key_results_for(self.user)


class ContributionForm(forms.Form):
    initiative = forms.ModelChoiceField(queryset=Initiative.objects.none(), widget=forms.Select(attrs={"class": "form-select"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["initiative"].queryset = visible_initiatives_for(user)


class OutcomeForm(ScopedForm):
    class Meta:
        model = OutcomeMeasurement
        fields = ("company", "team", "key_result", "task", "employee", "occurred_on", "value", "source_type", "source_key", "evidence")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = visible_companies_for(self.user)
        self.fields["team"].queryset = visible_teams_for(self.user)
        self.fields["key_result"].queryset = visible_key_results_for(self.user)
        self.fields["task"].queryset = visible_tasks_for(self.user)
        self.fields["employee"].queryset = get_user_model().objects.filter(pk=self.user.pk)
        if self.user.is_superuser:
            self.fields["employee"].queryset = get_user_model().objects.filter(is_active=True)
        elif manageable_teams(self.user).exists() or manageable_companies(self.user).exists():
            self.fields["employee"].queryset = scoped_people(self.user)
        self.fields["employee"].initial = self.user.pk


class BaselineStudyForm(ScopedForm):
    class Meta:
        model = BaselineStudy
        fields = ("company", "team", "name", "role", "workload_type", "output_unit", "output_definition", "acceptance_criteria", "effort_definition", "quality_definition", "source_definition", "comparison_plan", "start_date", "end_date", "pilot_start_date", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = visible_companies_for(self.user)


class BaselineObservationForm(ScopedForm):
    class Meta:
        model = BaselineObservation
        fields = ("study", "employee", "observed_on", "staffed_hours", "accepted_output", "quality_passed", "rework_count", "evidence", "source_key")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["study"].queryset = visible_studies_for(self.user)
        self.fields["employee"].queryset = get_user_model().objects.filter(pk=self.user.pk)
        if self.user.is_superuser:
            self.fields["employee"].queryset = get_user_model().objects.filter(is_active=True)
        elif manageable_teams(self.user).exists() or manageable_companies(self.user).exists():
            self.fields["employee"].queryset = scoped_people(self.user)
        self.fields["employee"].initial = self.user.pk
