"""Small explicit forms for operator control; guidance never submits itself."""
from django import forms


WEIGHT_LABELS = {
    "goal": "Company goal connection",
    "deadline": "Deadline risk",
    "urgency": "Agreed priority",
    "unlock": "Work this task unlocks",
    "age": "Time waiting for attention",
    "effort": "Fit of the next work slice",
    "context": "Continuity with recent work",
}


class OperatorPolicyForm(forms.Form):
    expected_revision = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in WEIGHT_LABELS.items():
            self.fields[key] = forms.IntegerField(label=label, min_value=1 if key == "age" else 0,
                max_value=100, widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}))

    def weights(self):
        return {key: self.cleaned_data[key] for key in WEIGHT_LABELS}


class OperatorActionForm(forms.Form):
    snapshot_id = forms.UUIDField(widget=forms.HiddenInput)
    task_id = forms.IntegerField(min_value=1, max_value=9223372036854775807, widget=forms.HiddenInput)
    reason = forms.CharField(required=False, max_length=1000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))
    choose_another = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def clean_reason(self):
        value = self.cleaned_data["reason"].strip()
        if "\x00" in value:
            raise forms.ValidationError("Use a reason without null characters.")
        return value
