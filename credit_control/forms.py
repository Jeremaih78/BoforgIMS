from django import forms
from django.utils import timezone

from .models import CollectionTask, CreditorFollowUp, DebtorFollowUp, PaymentDispute, PromiseToPay


class FollowUpCapture:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
            if isinstance(field.widget,forms.Textarea):field.widget.attrs['rows']=3
        self.fields['follow_up_date'].initial=timezone.localtime
        self.fields['follow_up_date'].widget.format='%Y-%m-%dT%H:%M'
        self.fields['follow_up_type'].initial='CALL'
        party_name='customer' if 'customer' in self.fields else 'supplier'
        doc_name='invoice' if party_name=='customer' else 'bill'
        account_name='debtor_account' if party_name=='customer' else 'creditor_account'
        selected=self.initial.get(party_name)
        if selected:
            self.fields[party_name].queryset=self.fields[party_name].queryset.filter(pk=getattr(selected,'pk',selected))
            self.fields[doc_name].queryset=self.fields[doc_name].queryset.filter(**{party_name:selected})
        self.fields[account_name].disabled=True
        self.fields[account_name].widget=forms.HiddenInput()

    def clean(self):
        data=super().clean()
        party_name='customer' if 'customer' in self.fields else 'supplier'
        doc_name='invoice' if party_name=='customer' else 'bill'
        doc=data.get(doc_name);party=data.get(party_name)
        if doc and party and getattr(doc,party_name+'_id')!=party.pk:
            self.add_error(doc_name,'This document belongs to a different account.')
        amount=data.get('promised_amount');day=data.get('promised_payment_date')
        if amount is not None and amount<=0:self.add_error('promised_amount','Enter a positive promised amount.')
        if bool(amount)!=bool(day):self.add_error('promised_amount','Enter both a promised amount and a payment date.')
        return data


class DebtorFollowUpForm(FollowUpCapture,forms.ModelForm):
    class Meta:
        model = DebtorFollowUp
        fields = [
            "customer",
            "invoice",
            "debtor_account",
            "follow_up_date",
            "follow_up_type",
            "direction",
            "subject",
            "message_summary",
            "full_message",
            "outcome",
            "next_action_date",
            "next_action_type",
            "promised_amount",
            "promised_payment_date",
            "attachment",
            "status",
        ]
        widgets = {
            "follow_up_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "next_action_date": forms.DateInput(attrs={"type": "date"}),
            "promised_payment_date": forms.DateInput(attrs={"type": "date"}),
        }


class CreditorFollowUpForm(FollowUpCapture,forms.ModelForm):
    class Meta:
        model = CreditorFollowUp
        fields = [
            "supplier",
            "bill",
            "creditor_account",
            "follow_up_date",
            "follow_up_type",
            "subject",
            "message_summary",
            "full_message",
            "outcome",
            "next_action_date",
            "promised_payment_amount",
            "promised_payment_date",
            "status",
        ]
        widgets = {
            "follow_up_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "next_action_date": forms.DateInput(attrs={"type": "date"}),
            "promised_payment_date": forms.DateInput(attrs={"type": "date"}),
        }


class PromiseToPayForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            _source_creation_fields(self, user, "credit_control.view_promisetopay")

    class Meta:
        model = PromiseToPay
        fields = [
            "company",
            "customer",
            "invoice",
            "follow_up",
            "assigned_to",
            "promised_amount",
            "promised_date",
            "status",
            "actual_paid_amount",
            "actual_paid_date",
            "notes",
        ]
        widgets = {
            "promised_date": forms.DateInput(attrs={"type": "date"}),
            "actual_paid_date": forms.DateInput(attrs={"type": "date"}),
        }


class PaymentDisputeForm(forms.ModelForm):
    class Meta:
        model = PaymentDispute
        fields = [
            "dispute_type",
            "customer",
            "supplier",
            "invoice",
            "bill",
            "opened_date",
            "status",
            "description",
            "resolution_notes",
            "resolved_date",
            "owner",
        ]
        widgets = {
            "opened_date": forms.DateInput(attrs={"type": "date"}),
            "resolved_date": forms.DateInput(attrs={"type": "date"}),
        }


class CollectionTaskForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            _source_creation_fields(self, user, "credit_control.view_collectiontask")

    def clean(self):
        data = super().clean()
        promise = data.get("promise_to_pay")
        if promise:
            if data.get("task_type") != "CHECK_PROMISE":
                self.add_error("promise_to_pay", "A promise reference belongs to a Check Promise task.")
            for field in ("company", "customer", "invoice"):
                if getattr(promise, field+"_id") != getattr(data.get(field), "pk", None):
                    self.add_error(field, "Use the same company, customer and invoice as the selected payment promise.")
            if data.get("supplier") or data.get("bill"):
                self.add_error("promise_to_pay", "A debtor payment promise cannot reference a supplier bill.")
            if getattr(data.get("assigned_to"), "pk", None) != promise.assigned_to_id:
                self.add_error("assigned_to", "Use the responsible employee from the selected payment promise.")
        return data

    class Meta:
        model = CollectionTask
        fields = [
            "company",
            "task_type",
            "customer",
            "supplier",
            "invoice",
            "bill",
            "promise_to_pay",
            "assigned_to",
            "due_date",
            "priority",
            "status",
            "notes",
        ]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


def _source_creation_fields(form, user, permission):
    from accounting.models import Company
    from django.contrib.auth import get_user_model
    from .source_policies import promise_assignees, source_company_ids, source_assignees, source_manager, visible_promises_for
    from core.permissions import allowed
    if not {"company", "assigned_to"}.issubset(form.fields):
        return
    form.fields["company"].queryset = Company.objects.filter(pk__in=source_company_ids(user))
    company_id = form.data.get(form.add_prefix("company")) if form.is_bound else getattr(form.instance, "company_id", None)
    company_id = int(company_id) if str(company_id or "").isdecimal() and len(str(company_id)) <= 19 else None
    if source_manager(user, company_id):
        form.fields["assigned_to"].queryset = (promise_assignees(user,form.instance,company_id=company_id)
            if isinstance(form.instance,PromiseToPay) else source_assignees(user,company_id,permission=permission))
    else:
        form.fields["assigned_to"].queryset = get_user_model().objects.filter(pk=user.pk, is_active=True) if allowed(user, permission, staff=False) else get_user_model().objects.none()
        form.fields["assigned_to"].required = True
    if "promise_to_pay" in form.fields:
        form.fields["promise_to_pay"].queryset = visible_promises_for(user)
