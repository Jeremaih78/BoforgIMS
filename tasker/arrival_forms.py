"""Deliberate self-correction input, separate from the immutable original."""
import uuid
from zoneinfo import ZoneInfo

from django import forms


class ArrivalCorrectionForm(forms.Form):
    declared_time = forms.TimeField(label="Correct declared arrival time", input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(format="%H:%M:%S", attrs={"type": "time", "step": "1", "class": "form-control"}))
    reason = forms.CharField(label="Reason for correction", max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control", "maxlength": 1000}))
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    client_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, arrival, **kwargs):
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("declared_time", arrival.effective_at.astimezone(ZoneInfo(arrival.timezone_name)).time().replace(microsecond=0))
        initial.setdefault("expected_revision", arrival.revision)
        initial.setdefault("client_key", uuid.uuid4())
        super().__init__(*args, **kwargs)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Explain why the declared time needs correction.")
        return reason
