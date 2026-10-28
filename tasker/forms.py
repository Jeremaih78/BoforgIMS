from datetime import timedelta
from copy import copy

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, Q
from django.utils import timezone

from .models import DailyPlan, DailyReview, Task, TaskCategory
from .policies import visible_tasks_for
from .selectors import owned_plannable_tasks
from .services.notes import notes_version as version_for_notes
from .services.workbench import work_version as version_for_work
from .organization import assignable_users_for, validate_task_scope, visible_companies_for, visible_teams_for


class TaskForm(forms.ModelForm):
    notes_version = forms.CharField(required=False, widget=forms.HiddenInput)
    work_version = forms.RegexField(regex=r"^[0-9a-f]{64}$", required=False, widget=forms.HiddenInput)
    accept_definition = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    owner = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    estimated_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=525600,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Minutes"}),
    )
    actual_minutes = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=525600,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Minutes"}),
    )

    class Meta:
        model = Task
        fields = (
            "title",
            "owner",
            "company",
            "team",
            "description",
            "definition_of_done",
            "priority",
            "status",
            "due_date",
            "due_time",
            "progress",
            "estimated_minutes",
            "actual_minutes",
            "category",
            "parent_task",
            "colour",
            "carry_forward",
            "archived",
            "notes",
            "notes_version",
            "work_version",
            "accept_definition",
            "blocked_reason",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Task title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "definition_of_done": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "What must be true before this task can be accepted?"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "progress": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "parent_task": forms.Select(attrs={"class": "form-select"}),
            "colour": forms.TextInput(attrs={"class": "form-control", "placeholder": "#4DA3FF"}),
            "carry_forward": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "archived": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "blocked_reason": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "What is preventing progress?"}
            ),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.definition_submitted = self.is_bound and "definition_of_done" in self.data
        if not self.instance.owner_id:
            # Owner is omitted for employees, but ModelForm's model validation
            # still needs the effective owner for personal parent boundaries.
            self.instance.owner = user
        if self.is_bound and self.instance.pk:
            # Clients predating Stage 2 cannot accidentally clear a mapping.
            self.data = self.data.copy()
            for name in ("company", "team"):
                if name not in self.data:
                    self.data[name] = getattr(self.instance, f"{name}_id") or ""
            if not self.definition_submitted:
                self.data["definition_of_done"] = self.instance.definition_of_done
        self.fields["company"].queryset = visible_companies_for(user)
        self.fields["team"].queryset = visible_teams_for(user)
        for name in ("company", "team"):
            self.fields[name].widget.attrs["class"] = "form-select"
        self.fields["company"].help_text = "Leave company and team blank for personal or unmapped legacy work."
        self.fields["team"].help_text = "Sharing with a team gives its manager access to this task and its working notes."
        self.fields["notes"].strip = False
        if self.instance.pk and not self.is_bound:
            self.initial["notes_version"] = version_for_notes(self.instance.notes)
            self.initial["work_version"] = version_for_work(self.instance)
        self.fields["category"].queryset = TaskCategory.objects.filter(is_active=True)
        parent_queryset = visible_tasks_for(user).exclude(status=Task.Status.CANCELLED)
        if self.instance.pk:
            parent_queryset = parent_queryset.exclude(pk=self.instance.pk)
        self.fields["parent_task"].queryset = parent_queryset.order_by("title")

        if user.has_perm("tasker.assign_task") or user.has_perm("tasker.manage_tasker"):
            team_id = self.data.get("team") if self.is_bound else self.instance.team_id
            company_id = self.data.get("company") if self.is_bound else self.instance.company_id
            team_id = int(team_id) if str(team_id or "").isdigit() else None
            company_id = int(company_id) if str(company_id or "").isdigit() else None
            self.fields["owner"].queryset = assignable_users_for(user, team=team_id, company=company_id).order_by(
                "first_name", "last_name", "username"
            )
            self.fields["owner"].initial = self.instance.owner_id or user.pk
            if not self.is_bound and not self.initial.get("owner"):
                self.initial["owner"] = self.instance.owner_id or user.pk
        else:
            self.fields.pop("owner")

        if self.instance.pk:
            if self.instance.estimated_duration:
                self.fields["estimated_minutes"].initial = round(
                    self.instance.estimated_duration.total_seconds() / 60
                )
            if self.instance.actual_duration:
                self.fields["actual_minutes"].initial = round(
                    self.instance.actual_duration.total_seconds() / 60
                )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("due_time") and not cleaned.get("due_date"):
            self.add_error("due_date", "Choose a due date when setting a due time.")
        if not self.errors:
            candidate = copy(self.instance)
            candidate.company = cleaned.get("company")
            candidate.team = cleaned.get("team")
            candidate.owner = cleaned.get("owner") or (self.instance.owner if self.instance.owner_id else self.user)
            candidate.parent_task = cleaned.get("parent_task")
            previous = Task.all_objects.filter(pk=self.instance.pk).first() if self.instance.pk else None
            try:
                validate_task_scope(self.user, candidate, previous)
            except PermissionDenied:
                self.add_error(None, "You cannot assign or share a task outside your authorized scope.")
            except ValidationError as exc:
                for name, errors in getattr(exc, "message_dict", {"__all__": exc.messages}).items():
                    self.add_error(name if name in self.fields else None, errors)
        return cleaned

    def save(self, commit=True):
        task = super().save(commit=False)
        if "owner" in self.cleaned_data:
            task.owner = self.cleaned_data["owner"]
        elif not task.owner_id:
            task.owner = self.user
        estimate = self.cleaned_data.get("estimated_minutes")
        actual = self.cleaned_data.get("actual_minutes")
        task.estimated_duration = timedelta(minutes=estimate) if estimate else None
        task.actual_duration = timedelta(minutes=actual) if actual is not None else None
        if commit:
            task.save()
            self.save_m2m()
        return task


class TaskCommentForm(forms.Form):
    body = forms.CharField(
        max_length=5000,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "Add a comment…"}
        ),
    )


class ChecklistItemForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Add a checklist item", "autocomplete": "off"}
        ),
    )


class TimeEntryForm(forms.Form):
    started_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
        input_formats=("%Y-%m-%dT%H:%M",),
    )
    duration_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Minutes"}),
    )
    notes = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["started_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")


class TaskProgressForm(forms.Form):
    work_version = forms.RegexField(regex=r"^[0-9a-f]{64}$", required=False, widget=forms.HiddenInput)
    accept_definition = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    progress = forms.IntegerField(
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100}),
    )


class QuickAddTaskForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "What needs to be done?",
                "autocomplete": "off",
                "autofocus": True,
            }
        ),
    )
    priority = forms.ChoiceField(
        choices=Task.Priority.choices,
        initial=Task.Priority.MEDIUM,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    due_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
    )
    estimated_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=1440,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 30", "inputmode": "numeric"}
        ),
    )
    category = forms.ModelChoiceField(
        queryset=TaskCategory.objects.none(),
        required=False,
        empty_label="No category",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = TaskCategory.objects.filter(is_active=True).order_by(
            "sort_order", "name"
        )
        for name, suffix in (
            ("title", "Title"), ("priority", "Priority"), ("due_time", "Time"),
            ("estimated_minutes", "Estimate"), ("category", "Category"),
        ):
            field_id = f"globalTask{suffix}"
            self.fields[name].widget.attrs.update({
                "id": field_id, "aria-describedby": f"{field_id}Errors",
            })

    def estimated_duration(self):
        minutes = self.cleaned_data.get("estimated_minutes")
        return timedelta(minutes=minutes) if minutes else None


class QuickNotesForm(forms.Form):
    quick_notes = forms.CharField(
        required=False,
        max_length=5000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control tasker-notes-input",
                "rows": 5,
                "placeholder": "Capture a thought, reminder, or follow-up…",
                "aria-label": "Quick notes",
            }
        ),
    )


class DailyPlanForm(forms.ModelForm):
    plan_version = forms.CharField(required=False,max_length=64,widget=forms.HiddenInput)
    top_priorities = forms.ModelMultipleChoiceField(
        queryset=Task.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "planner-check-input"}),
    )
    planned_tasks = forms.ModelMultipleChoiceField(
        queryset=Task.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "planner-check-input"}),
    )

    class Meta:
        model = DailyPlan
        fields = ("focus_area", "motivational_quote", "top_priorities", "planned_tasks", "quick_notes")
        widgets = {
            "focus_area": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "What deserves your best attention today?"}
            ),
            "motivational_quote": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "A thought to guide the day"}
            ),
            "quick_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "Notes, reminders, calls, and follow-ups…",
                }
            ),
        }

    def __init__(self, *args, user, plan_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            from .services.planner import plan_version
            self.initial["plan_version"] = plan_version(self.instance)
        plan_date = plan_date or timezone.localdate()
        tasks = owned_plannable_tasks(user=user, plan=self.instance)
        self.fields["top_priorities"].queryset = tasks
        self.fields["planned_tasks"].queryset = tasks

        if not self.is_bound and not self.instance.pk:
            if not self.initial.get("motivational_quote"):
                self.initial["motivational_quote"] = (
                    "Success is the sum of small efforts, repeated day in and day out."
                )
            if not self.initial.get("planned_tasks"):
                self.initial["planned_tasks"] = list(
                    tasks.filter(due_date=plan_date).values_list("pk", flat=True)
                )

    def clean_top_priorities(self):
        priorities = self.cleaned_data["top_priorities"]
        if priorities.count() > 4:
            raise forms.ValidationError("Choose no more than four top priorities.")
        return priorities


class DailyReviewForm(forms.ModelForm):
    ENERGY_CHOICES = (
        (1, "1 · Very low"),
        (2, "2 · Low"),
        (3, "3 · Steady"),
        (4, "4 · High"),
        (5, "5 · Excellent"),
    )
    PRODUCTIVE_TIME_CHOICES = (
        ("", "Choose a time"),
        ("Early morning", "Early morning"),
        ("Late morning", "Late morning"),
        ("Early afternoon", "Early afternoon"),
        ("Late afternoon", "Late afternoon"),
        ("Evening", "Evening"),
    )
    energy_level = forms.TypedChoiceField(
        choices=ENERGY_CHOICES,
        coerce=int,
        widget=forms.RadioSelect(attrs={"class": "review-energy-input"}),
    )
    most_productive_time = forms.ChoiceField(
        choices=PRODUCTIVE_TIME_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    carry_over_tasks = forms.ModelMultipleChoiceField(
        queryset=Task.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "planner-check-input"}),
    )

    class Meta:
        model = DailyReview
        fields = (
            "accomplishments",
            "energy_level",
            "most_productive_time",
            "biggest_distraction",
            "lessons_learned",
            "carry_over_tasks",
            "tomorrow_focus",
        )
        widgets = {
            "accomplishments": forms.Textarea(
                attrs={"class": "form-control", "rows": 4, "placeholder": "What did you finish or move forward?"}
            ),
            "biggest_distraction": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "What pulled your attention away?"}
            ),
            "lessons_learned": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "What will you repeat or change?"}
            ),
            "tomorrow_focus": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "What should lead tomorrow?"}
            ),
        }

    def __init__(self, *args, user, plan, **kwargs):
        super().__init__(*args, **kwargs)
        candidate_tasks = (
            Task.objects.filter(owner=user, archived=False)
            .exclude(status__in=(Task.Status.COMPLETED, Task.Status.CANCELLED))
            .filter(Q(daily_plans=plan) | Q(due_date__lte=plan.plan_date) | Q(carry_forward=True))
            .distinct()
            .order_by(F("due_date").asc(nulls_last=True), "title")
        )
        self.fields["carry_over_tasks"].queryset = candidate_tasks
