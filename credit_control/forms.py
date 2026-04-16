from django import forms

from .models import CollectionTask, CreditorFollowUp, DebtorFollowUp, PaymentDispute, PromiseToPay


class DebtorFollowUpForm(forms.ModelForm):
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


class CreditorFollowUpForm(forms.ModelForm):
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
    class Meta:
        model = PromiseToPay
        fields = [
            "customer",
            "invoice",
            "follow_up",
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
    class Meta:
        model = CollectionTask
        fields = [
            "task_type",
            "customer",
            "supplier",
            "invoice",
            "bill",
            "assigned_to",
            "due_date",
            "priority",
            "status",
            "notes",
        ]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }
