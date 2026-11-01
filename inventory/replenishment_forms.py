"""Versioned source forms for explicit procurement responsibility and review."""
import uuid

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from inventory.models import Product
from inventory.replenishment_models import ReplenishmentPlan
from inventory.replenishment_policies import eligible_assignees, visible_companies_for


class SourceForm(forms.Form):
    expected_revision = forms.CharField(max_length=36, widget=forms.HiddenInput)
    client_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        supported = {self.add_prefix(name) for name in self.fields} | {"csrfmiddlewaretoken"}
        if self.is_bound and set(self.data) - supported:
            raise forms.ValidationError("This review accepts only the work fields shown in the form.")
        return values

    def clean_next_review_on(self):
        value = self.cleaned_data.get("next_review_on")
        if value and not 1900 <= value.year <= 2200:
            raise forms.ValidationError("Choose a next-review date from 1900 to 2200.")
        return value


class ResponsibilityForm(SourceForm):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    company = forms.ModelChoiceField(queryset=None, label="Responsible company")
    assigned_to = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False,
        label="Procurement owner", help_text="Choose an authorized employee, or leave explicitly unassigned for manager review.")
    is_enabled = forms.BooleanField(required=False, label="Enable replenishment review",
        help_text="Watch this product's reservation-adjusted availability against its reorder level.")
    next_review_on = forms.DateField(required=False, label="Next review",
        widget=forms.DateInput(attrs={"type":"date"}))
    notes = forms.CharField(required=False, max_length=10000, strip=False,
        label="Standing procurement notes", widget=forms.Textarea(attrs={"rows":4}),
        help_text="Visible at the authorized Inventory source; excluded from Tasker and its reminders.")

    def __init__(self, *args, user, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = visible_companies_for(user, manage=True)
        self.fields["company"].widget = forms.HiddenInput()
        if item is not None:
            self.fields["product"].queryset = Product.objects.filter(pk=item.product_id)
            self.fields["product"].disabled = True
        else:
            available = Q(replenishment_plan__isnull=True)
            # A successful new mapping may be retried after the response was
            # lost. Let the source service check its receipt/current authority
            # instead of rejecting its now-mapped product before that check.
            submitted = self.data.get(self.add_prefix("product")) if self.is_bound else None
            if str(submitted or "").isdecimal() and len(str(submitted)) <= 19:
                submitted = int(submitted)
                if 0 < submitted <= 9223372036854775807:
                    available |= Q(pk=submitted)
            self.fields["product"].queryset = Product.objects.filter(available).order_by("name", "pk")
        company_value = self.data.get(self.add_prefix("company")) if self.is_bound else self.initial.get("company")
        company_value = getattr(company_value,"pk",company_value)
        company_id = int(company_value) if str(company_value or "").isdecimal() and len(str(company_value)) <= 19 else None
        self.fields["assigned_to"].queryset = eligible_assignees(user,company_id) if company_id else get_user_model().objects.none()
        _style(self)


class CompanyChoiceForm(forms.Form):
    company = forms.ModelChoiceField(queryset=None,label="Responsible company")

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["company"].queryset = visible_companies_for(user,manage=True)
        _style(self)


class ReviewForm(SourceForm):
    expected_stock_version = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    review_state = forms.ChoiceField(choices=ReplenishmentPlan.ReviewState.choices, label="Next step")
    next_review_on = forms.DateField(required=False, label="Next review",
        widget=forms.DateInput(attrs={"type":"date"}))
    notes = forms.CharField(required=False, max_length=10000, strip=False,
        label="Standing procurement notes", widget=forms.Textarea(attrs={"rows":4}),
        help_text="Keep supplier follow-ups and decision context here. These notes are not copied to Tasker.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget,forms.HiddenInput):
            continue
        if isinstance(field.widget,forms.CheckboxInput):
            field.widget.attrs["class"] = "form-check-input"
        else:
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget,forms.Select) else "form-control"
