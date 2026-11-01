"""Explicit scoped inputs; the model cannot choose an actor, payer or audience."""
import uuid

from django import forms

from tasker.ai_workflow_models import AIWorkRequest
from tasker.models import CaptureItem, Task
from tasker.services.reporting import report_teams_for


class AIWorkForm(forms.Form):
    feature = forms.ChoiceField(choices=AIWorkRequest.Feature.choices, initial="BRIEFING")
    task = forms.ModelChoiceField(queryset=Task.objects.none(), required=False,
        help_text="Only your own task can receive checklist changes.")
    capture = forms.ModelChoiceField(queryset=CaptureItem.objects.none(), required=False,
        help_text="Only this deliberately selected capture is included.")
    team = forms.ModelChoiceField(queryset=Task.objects.none(), required=False,
        label="Shared team facts", help_text="Team briefings exclude personal notes, captures and conversations.")
    question = forms.CharField(required=False, max_length=16000,
        label="Question or deliberately supplied meeting notes",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Meeting notes are personal input. Other questions are limited to 2,000 characters.")
    client_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from tasker.services.coordination import scoped_tasks_for
        self.fields["task"].queryset = scoped_tasks_for(user).filter(owner=user, archived=False).order_by("title")
        self.fields["capture"].queryset = CaptureItem.objects.filter(owner=user,
            visibility="PRIVATE", triage_state="INBOX").order_by("-updated_at")
        self.fields["team"].queryset = report_teams_for(user).order_by("name")
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean(self):
        data = super().clean()
        feature = data.get("feature")
        if feature == "CAPTURE" and not data.get("capture"):
            self.add_error("capture", "Choose the capture you want to understand.")
        if feature in ("EXPLAIN", "BREAKDOWN") and not data.get("task"):
            self.add_error("task", "Choose the task you want to review.")
        if feature != "CAPTURE" and data.get("capture"):
            self.add_error("capture", "Select a capture only for capture assistance.")
        if feature not in ("EXPLAIN", "BREAKDOWN", "COORDINATION_SUMMARY") and data.get("task"):
            self.add_error("task", "Choose task-specific assistance for this input.")
        if data.get("team") and feature not in ("BRIEFING", "COORDINATION_SUMMARY"):
            self.add_error("team", "Only shared briefings and coordination summaries can use a team audience.")
        if data.get("team") and data.get("task"):
            self.add_error("task", "Choose either shared team facts or a personal task.")
        if feature == "MEETING" and not data.get("question", "").strip():
            self.add_error("question", "Supply the meeting notes deliberately.")
        if feature != "MEETING" and len(data.get("question", "")) > 2000:
            self.add_error("question", "Keep this question within 2,000 characters.")
        return data


class AIRevisionForm(forms.Form):
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)
