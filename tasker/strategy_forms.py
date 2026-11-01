"""Typed, scoped alternatives to moving cards on the Strategy board."""
import uuid
from datetime import date

from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseFormSet, formset_factory
from django.db.models import Q
from django.core.validators import MinValueValidator, MaxValueValidator

from accounting.models import Company
from tasker.models import Task, Team
from tasker.organization import assignable_users_for, managed_teams_for, visible_companies_for
from tasker.services.coordination import scoped_tasks_for


def _style(form):
    for field in form.fields.values():
        if not isinstance(field.widget, forms.HiddenInput):
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"


def _selected_id(value):
    value = str(getattr(value, "pk", value) or "")
    return int(value) if value.isdecimal() and len(value) <= 19 and 0 < int(value) <= 9223372036854775807 else None


def _bounded(queryset, selected=()):
    ids = [value for value in (_selected_id(value) for value in selected) if value]
    return queryset.filter(Q(pk__in=queryset.values("pk")[:200]) | Q(pk__in=ids))


class StrategyFilterForm(forms.Form):
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False, empty_label="All accessible companies")
    team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False, empty_label="All accessible teams")
    day = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "min": "1900-01-01", "max": "2200-12-31"}),
        label="Planning date", validators=[MinValueValidator(date(1900, 1, 1)), MaxValueValidator(date(2200, 12, 31))])
    view = forms.ChoiceField(choices=(("execution", "Execution"), ("dependencies", "Dependencies"), ("capacity", "Capacity")), required=False, widget=forms.HiddenInput)
    page = forms.IntegerField(required=False, min_value=1, max_value=100000, widget=forms.HiddenInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from tasker.services.strategy import strategy_teams_for
        self.fields["company"].queryset = visible_companies_for(user).order_by("name")
        self.fields["team"].queryset = strategy_teams_for(user).order_by("company__name", "name")
        _style(self)

    def clean(self):
        data = super().clean()
        if data.get("team") and data.get("company") and data["team"].company_id != data["company"].pk:
            self.add_error("team", "Choose a team belonging to the selected company, or clear the company filter.")
        return data


class WorkDeclarationForm(forms.Form):
    team = forms.ModelChoiceField(queryset=Team.objects.none(), widget=forms.HiddenInput)
    current_task = forms.ModelChoiceField(queryset=Task.objects.none(), required=False, empty_label="No current work declared")
    next_task = forms.ModelChoiceField(queryset=Task.objects.none(), required=False, empty_label="No next work declared")
    expected_revision = forms.CharField(max_length=36, widget=forms.HiddenInput, initial="none")

    def __init__(self, *args, user, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = Team.objects.filter(company__is_active=True,
            memberships__user=user, memberships__is_active=True, memberships__is_deleted=False).distinct()
        tasks = scoped_tasks_for(user).filter(owner=user, archived=False,
            status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED))
        tasks = tasks.filter(team=team).order_by("title", "pk") if team else tasks.none()
        selected = [self.data.get(self.add_prefix(key)) if self.is_bound else self.initial.get(key) for key in ("current_task", "next_task")]
        self.fields["current_task"].queryset = _bounded(tasks, selected)
        self.fields["next_task"].queryset = self.fields["current_task"].queryset
        _style(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("current_task") and cleaned.get("current_task") == cleaned.get("next_task"):
            self.add_error("next_task", "Choose different current and next work, or leave next work undeclared.")
        return cleaned


class ScenarioCreateForm(forms.Form):
    team = forms.ModelChoiceField(queryset=Team.objects.none(), label="Team to plan for")
    title = forms.CharField(max_length=140, label="Scenario name", widget=forms.TextInput(attrs={"placeholder": "For example: make room for a critical customer order"}))
    client_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = managed_teams_for(user).order_by("company__name", "name")
        _style(self)


class ScenarioChangeForm(forms.Form):
    task = forms.ModelChoiceField(queryset=Task.objects.none(), label="Work to change")
    owner = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Proposed responsible employee")
    priority = forms.ChoiceField(choices=(("", "Choose priority"), *Task.Priority.choices), label="Proposed priority")
    due_date = forms.DateField(required=False, label="Proposed deadline date", widget=forms.DateInput(attrs={"type": "date"}))
    due_time = forms.TimeField(required=False, label="Proposed deadline time", widget=forms.TimeInput(attrs={"type": "time"}))

    def __init__(self, *args, user, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        if team:
            choices = scoped_tasks_for(user).filter(team=team, archived=False,
                status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)).order_by("title", "pk")
            self.fields["task"].queryset = _bounded(choices, [self.data.get(self.add_prefix("task"))] if self.is_bound else [])
            self.fields["task"].help_text = "Up to 200 active tasks in this team. Review Work for items outside this selection."
            owners = assignable_users_for(user, team=team).order_by("first_name", "username", "pk")
            self.fields["owner"].queryset = _bounded(owners, [self.data.get(self.add_prefix("owner"))] if self.is_bound else [])
            self.fields["owner"].help_text = "Up to 200 active team members, plus your current selection. Membership is checked; skills are not inferred."
        _style(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("due_time") and not cleaned.get("due_date"):
            self.add_error("due_date", "Choose the deadline date when specifying a time.")
        return cleaned


class ScenarioChangeFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        task_ids = []
        for form in self.forms:
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("task"):
                task_ids.append(form.cleaned_data["task"].pk)
        if not task_ids:
            raise forms.ValidationError("Choose at least one task change to preview.")
        if len(task_ids) != len(set(task_ids)):
            raise forms.ValidationError("Each task can appear only once in a scenario.")


ScenarioChanges = formset_factory(ScenarioChangeForm, formset=ScenarioChangeFormSet,
    extra=1, max_num=10, validate_max=True, absolute_max=10, can_delete=True)


class ScenarioRevisionForm(forms.Form):
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)
