"""Local planning input; timezone interpretation is explicit and unambiguous."""
from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError

from tasker.models import Availability
from tasker.planning_models import validate_zone


def local_datetime(value, zone):
    try:
        naive = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid local date and time.") from None
    if naive.tzinfo is not None:
        raise ValidationError("Use a local time without a timezone suffix; the displayed timezone applies.")
    candidates = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            if not any(item.utcoffset() == aware.utcoffset() for item in candidates):
                candidates.append(aware)
    if len(candidates) != 1:
        raise ValidationError("This local time is ambiguous or does not exist during a clock change. Choose another time.")
    return candidates[0]


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")


class WorkPolicyForm(StyledForm):
    timezone_name = forms.CharField(max_length=64, validators=[validate_zone], label="Work timezone")
    weekdays = forms.TypedMultipleChoiceField(choices=tuple(enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))), coerce=int, required=False, widget=forms.CheckboxSelectMultiple)
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type":"time"}), label="Work starts")
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type":"time"}), label="Work ends", help_text="An end earlier than the start means an overnight shift.")
    buffer_minutes = forms.IntegerField(min_value=0, max_value=1440, label="Interruption buffer (minutes)", help_text="Choose an explicit allowance for interruptions; enter 0 to choose no buffer.")
    expected_version = forms.UUIDField(required=False, widget=forms.HiddenInput)


class IntervalForm(StyledForm):
    starts_at = forms.CharField(max_length=32, widget=forms.DateTimeInput(attrs={"type":"datetime-local"}), label="Planned start")
    ends_at = forms.CharField(max_length=32, widget=forms.DateTimeInput(attrs={"type":"datetime-local"}), label="Planned end")
    expected_version = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, zone, **kwargs):
        self.zone = zone
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        for field in ("starts_at", "ends_at"):
            if cleaned.get(field):
                try:
                    cleaned[field] = local_datetime(cleaned[field], self.zone)
                except ValidationError as error:
                    self.add_error(field, error)
        if not self.errors and cleaned["ends_at"] <= cleaned["starts_at"]:
            self.add_error("ends_at", "End must be after start.")
        return cleaned


class AvailabilityForm(IntervalForm):
    kind = forms.ChoiceField(choices=Availability.Kind.choices)
    private_note = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows":2}), help_text="Private to you; managers receive capacity totals, not this text.")


class ScheduleForm(IntervalForm):
    task = forms.ModelChoiceField(queryset=None)

    def __init__(self, *args, tasks, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].queryset = tasks
        self.order_fields(("task", "starts_at", "ends_at", "expected_version"))


class EffortForm(StyledForm):
    remaining_minutes = forms.IntegerField(min_value=0, max_value=525600, required=False, label="Remaining effort (minutes)", help_text="Your current estimate from now. Blank means unknown; zero does not complete the task.")
    reason = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows":2}), label="Re-estimation context")
    expected_version = forms.CharField(max_length=128, required=False, widget=forms.HiddenInput)
