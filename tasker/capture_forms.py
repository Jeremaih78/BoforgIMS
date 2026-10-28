"""Explicit inputs for private capture and deliberate promotion."""
import uuid

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from .models import CaptureItem
from .capture_models import normalize_tags
from .organization import visible_teams_for
from .services.goals import visible_goals_for, _can_manage


def promotion_goals_for(user):
    goals = visible_goals_for(user)
    if not getattr(settings, "TASKER_DIRECTION_ENABLED", False) or not user.has_perm("tasker.add_initiative"):
        return goals.none()
    return goals.filter(pk__in=[goal.pk for goal in goals if _can_manage(user, goal, "add_initiative")])


class CaptureFields(forms.Form):
    kind = forms.ChoiceField(choices=CaptureItem.Kind.choices, initial="NOTE", label="Capture type")
    title = forms.CharField(max_length=255, required=False, label="Title (optional)")
    content = forms.CharField(max_length=100000, strip=False, widget=forms.Textarea(attrs={"rows": 10}), label="Capture text")
    url = forms.URLField(max_length=2048, required=False, label="Link URL (optional)")
    tags = forms.CharField(max_length=1000, required=False, help_text="Separate tags with commas.")
    pinned = forms.BooleanField(required=False, label="Pin this capture")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean_tags(self):
        try:
            return normalize_tags(self.cleaned_data["tags"])
        except ValidationError as error:
            raise ValidationError(error.messages)

    def clean_url(self):
        value = self.cleaned_data["url"]
        if value and not value.lower().startswith(("https://", "http://")):
            raise ValidationError("Use an http or https link.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == "LINK" and not cleaned.get("url") and "url" not in self.errors:
            candidate = cleaned.get("content", "").strip()
            if candidate.lower().startswith(("http://", "https://")):
                try:
                    cleaned["url"] = self.fields["url"].clean(candidate)
                except ValidationError:
                    self.add_error("url", "Enter a valid http or https URL for this link.")
            else:
                self.add_error("url", "Enter a URL for this link.")
        return cleaned


class CaptureCreateForm(CaptureFields):
    client_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("client_key", uuid.uuid4())


class CaptureEditForm(CaptureFields):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class CapturePromotionForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    target = forms.ChoiceField(choices=(), label="Create as")
    title = forms.CharField(max_length=255)
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Task due date (optional)")
    team = forms.ModelChoiceField(queryset=None, required=False, label="Task team (optional)", help_text="Leave blank for personal work. Choose a team only when you intend to share the resulting task.")
    goal = forms.ModelChoiceField(queryset=None, required=False, label="Initiative goal", help_text="Required for an initiative; you must be authorized to manage this goal.")
    confirm_copy = forms.BooleanField(label="Copy this capture’s current text into the new work record")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = visible_teams_for(user)
        self.fields["goal"].queryset = promotion_goals_for(user)
        choices = [("task", "Task")] if user.has_perm("tasker.add_task") else []
        if self.fields["goal"].queryset.exists():
            choices.append(("initiative", "Goal initiative"))
        self.fields["target"].choices = choices
        if not self.is_bound and choices and self.initial.get("target") not in dict(choices):
            self.initial["target"] = choices[0][0]
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("target") == "initiative":
            if not cleaned.get("goal"):
                self.add_error("goal", "Choose a goal for this initiative.")
            if cleaned.get("team") or cleaned.get("due_date"):
                self.add_error(None, "Task team/date settings apply to task promotion. An initiative uses its chosen goal’s scope.")
        elif cleaned.get("goal"):
            self.add_error("goal", "A goal is used when promoting to an initiative. Leave it blank for a task.")
        return cleaned
