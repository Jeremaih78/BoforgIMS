import uuid
from django import forms
from .models import CollectionTask
from .source_policies import promise_assignees, source_assignees, source_manager


class SourceRevisionForm(forms.Form):
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)
    client_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class CollectionWorkForm(SourceRevisionForm):
    assigned_to = forms.ModelChoiceField(queryset=None, required=False, label="Responsible employee")
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type":"date"}))
    priority = forms.ChoiceField(choices=CollectionTask.Priority.choices)

    def __init__(self, *args, user, item, **kwargs):
        from django.contrib.auth import get_user_model
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = (source_assignees(user,item.company_id,
            permission="credit_control.view_collectiontask") if source_manager(user,item.company_id)
            else get_user_model().objects.filter(pk=item.assigned_to_id))
        self.fields["assigned_to"].disabled = bool(item.promise_to_pay_id) or not source_manager(user,item.company_id)
        if item.promise_to_pay_id:
            self.fields["assigned_to"].help_text = "Responsibility follows the linked payment promise. Change it on the promise page."
        _style(self)


class PromiseAssignmentForm(SourceRevisionForm):
    assigned_to = forms.ModelChoiceField(queryset=None, required=False, label="Responsible employee")

    def __init__(self, *args, user, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = promise_assignees(user,item,company_id=item.company_id)
        _style(self)


def _style(form):
    for field in form.fields.values():
        if not isinstance(field.widget,forms.HiddenInput):
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget,forms.Select) else "form-control"
