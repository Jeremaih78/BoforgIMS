from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import F, Q
from django.utils import timezone

from .models import DailyPlan, DailyReview, Task, TaskCategory
from .policies import visible_tasks_for
from .selectors import owned_plannable_tasks


class TaskForm(forms.ModelForm):
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
            "description",
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
            "blocked_reason",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Task title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
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
        self.fields["category"].queryset = TaskCategory.objects.filter(is_active=True)
        parent_queryset = visible_tasks_for(user).exclude(status=Task.Status.CANCELLED)
        if self.instance.pk:
            parent_queryset = parent_queryset.exclude(pk=self.instance.pk)
        self.fields["parent_task"].queryset = parent_queryset.order_by("title")

        if user.has_perm("tasker.assign_task") or user.has_perm("tasker.manage_tasker"):
            self.fields["owner"].queryset = get_user_model().objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
            self.fields["owner"].initial = self.instance.owner_id or user.pk
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
