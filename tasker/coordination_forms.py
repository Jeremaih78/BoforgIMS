"""Explicit coordination choices, with employee-local response times."""
import uuid
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError

from tasker.planning_models import WorkPolicy
from tasker.planning_forms import local_datetime
from tasker.services.workbench import work_version


class CoordinationForm(forms.Form):
    kind = forms.ChoiceField(choices=(("BLOCKER", "Request help with a blocker"), ("HANDOFF", "Request a handoff")))
    resolver = forms.ModelChoiceField(queryset=None, label="Who needs to act?")
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="Context and reason")
    requested_action = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 2}), label="What response do you need?")
    expected_response_at = forms.CharField(label="Response expected by", widget=forms.TextInput(attrs={"type": "datetime-local"}))
    escalation_policy = forms.ChoiceField(choices=(("NONE", "No automatic escalation"), ("UNACKNOWLEDGED", "Escalate to an authorized manager if not acknowledged")))
    client_key = forms.UUIDField(widget=forms.HiddenInput)
    expected_task_version = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, user, task, **kwargs):
        from django.contrib.auth import get_user_model
        from tasker.services.coordination import resolver_candidates
        super().__init__(*args, **kwargs)
        self.fields["resolver"].queryset = resolver_candidates(task=task, user=user) if task else get_user_model().objects.none()
        policy = WorkPolicy.objects.filter(owner=user).first()
        self.zone = ZoneInfo(policy.timezone_name if policy else "Africa/Harare")
        self.fields["expected_response_at"].help_text = f"Local time in {self.zone.key}."
        self.initial.setdefault("client_key", uuid.uuid4())
        self.initial.setdefault("expected_task_version", work_version(task) if task else "")
        self.initial.setdefault("escalation_policy", "NONE")
        if task and task.blocked_reason:
            self.initial.setdefault("reason", task.blocked_reason)
        _style(self)

    def clean_expected_response_at(self):
        return local_datetime(self.cleaned_data["expected_response_at"], self.zone)


class CoordinationActionForm(forms.Form):
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class NotificationPreferenceForm(forms.Form):
    timezone_name = forms.CharField(max_length=64, label="Timezone")
    quiet_enabled = forms.BooleanField(required=False, label="Use quiet hours")
    quiet_start = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}), label="Quiet hours start")
    quiet_end = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}), label="Quiet hours end")
    respect_work_hours = forms.BooleanField(required=False, label="Hold alerts outside my configured work hours")
    suppress_during_focus = forms.BooleanField(required=False, label="Hold routine alerts during Focus")
    digest_mode = forms.ChoiceField(choices=(("IMMEDIATE", "Individual alerts when available"), ("HOURLY", "Group routine alerts in an hourly digest")), label="Routine alerts")
    expected_revision = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class ReportPolicyForm(forms.Form):
    timezone_name = forms.CharField(max_length=64, label="Reporting timezone")
    cutoff_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}), label="Daily cutoff")
    consolidation = forms.ChoiceField(choices=(("UNCONFIGURED", "Choose a consolidation policy"), ("PER_SUBMISSION", "A revision after each End day submission"), ("AT_CUTOFF", "Consolidate at the daily cutoff")))
    correction_policy = forms.ChoiceField(choices=(("UNCONFIGURED", "Choose a correction policy"), ("APPEND_REVISION", "Append a revision after shared facts change")))
    missing_policy = forms.ChoiceField(choices=(("UNCONFIGURED", "Choose a missing-submission policy"), ("SHOW_MISSING", "Show employees with no submission explicitly")))
    weekdays = forms.TypedMultipleChoiceField(coerce=int, choices=tuple(enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))), widget=forms.CheckboxSelectMultiple, label="Reporting days")
    email_mode = forms.ChoiceField(choices=(("UNCONFIGURED", "Hold email preparation"), ("TEST", "Use the test email adapter")))
    retention_days = forms.IntegerField(required=False, min_value=1, max_value=3650, label="Retention policy in days", help_text="Leave unconfigured until the company approves its retention policy.")
    expected_revision = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class ReportDateForm(forms.Form):
    report_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Report date")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.HiddenInput):
            continue
        if isinstance(field.widget, forms.CheckboxSelectMultiple):
            field.widget.attrs["class"] = "coordination-checks"
        elif isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs["class"] = "form-check-input"
        elif isinstance(field.widget, forms.Select):
            field.widget.attrs["class"] = "form-select"
        else:
            field.widget.attrs["class"] = "form-control"
