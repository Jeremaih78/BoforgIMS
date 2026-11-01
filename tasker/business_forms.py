"""Read-only source action filters; no source identity or actor is editable here."""
from django import forms
from django.conf import settings


class BusinessActionFilterForm(forms.Form):
    state = forms.ChoiceField(label="Show", required=False, choices=(
        ("active", "Active actions"), ("closed", "Closed at source"), ("all", "All readable actions")))
    source = forms.ChoiceField(label="Source", required=False, choices=(
        ("", "All business sources"), ("collection_task", "Collection follow-ups"),
        ("promise_to_pay", "Payment promise reviews")))
    scope = forms.ChoiceField(label="Responsibility", required=False, choices=(
        ("mine", "Assigned to me"), ("visible", "All I can read")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "All business sources")]
        if getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False):
            choices += [("collection_task", "Collection follow-ups"), ("promise_to_pay", "Payment promise reviews")]
        if getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False):
            choices += [("replenishment_plan", "Stock replenishment")]
        from operations.models import KINDS
        from tasker.services.business_links import enabled
        choices += [(kind,label) for kind,label in KINDS if enabled(kind)]
        self.fields["source"].choices = choices
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select"

    def clean_state(self):
        return self.cleaned_data.get("state") or "active"

    def clean_scope(self):
        return self.cleaned_data.get("scope") or "mine"


class BusinessContributionForm(forms.Form):
    team = forms.ModelChoiceField(queryset=None,required=False,label='Attributed team')
    key_result = forms.ModelChoiceField(queryset=None,required=False,label='Operational output result')
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self,*args,user,link,**kwargs):
        from tasker.organization import visible_teams_for
        from tasker.services.goals import visible_key_results_for
        from tasker.business_outcome_models import BusinessContribution
        from tasker.services.business_outcomes import team_for
        super().__init__(*args,**kwargs)
        self.fields['team'].queryset=visible_teams_for(user).filter(company_id=link.company_id)
        self.fields['key_result'].queryset=visible_key_results_for(user).filter(goal__company_id=link.company_id)
        mapping=BusinessContribution.objects.filter(link=link).first()
        self.initial.update(team=mapping.team_id if mapping else team_for(link),
            key_result=mapping.key_result_id if mapping else None,expected_revision=link.revision)
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs['class']='form-select'
