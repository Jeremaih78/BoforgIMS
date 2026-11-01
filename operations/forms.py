import uuid
from django import forms
from accounting.models import Company
from inventory.replenishment_policies import company_ids
from tasker.models import Team
from .models import KINDS
from .policies import can_manage, can_setup_company, eligible
from .registry import CHECKLISTS, SOURCES, source_label


class ConfigureForm(forms.Form):
    source = forms.ModelChoiceField(queryset=Company.objects.none(),label='Source record')
    team = forms.ModelChoiceField(queryset=Team.objects.none(),required=False,label='Responsible team')
    assigned_to = forms.ModelChoiceField(queryset=Company.objects.none(),required=False,label='Responsible employee')
    enabled = forms.BooleanField(required=False,label='Enable this responsibility')
    priority = forms.ChoiceField(choices=(('CRITICAL','Critical'),('HIGH','High'),('MEDIUM','Medium'),('LOW','Low')),initial='HIGH')
    next_review_on = forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Next follow-up')
    contact_interval_days = forms.IntegerField(required=False,min_value=1,max_value=365,label='Quotation contact interval (days)')
    sla_due_on = forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Repair SLA deadline')
    expected_revision = forms.CharField(widget=forms.HiddenInput)
    client_key = forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)

    def __init__(self,*args,user,kind,company_id,row=None,**kwargs):
        super().__init__(*args,**kwargs)
        spec = SOURCES[kind]
        rows = spec.cls().objects.all().order_by('-pk')
        if kind in ('bill_work','period_work'):
            rows = rows.filter(company_id=company_id)
        # Records already bound to another company never leak into this chooser.
        from .models import Responsibility
        rows = rows.exclude(pk__in=Responsibility.objects.filter(kind=kind).exclude(company_id=company_id).values('source_id'))
        if not can_setup_company(user,kind,company_id):
            rows = rows.none()
        self.fields['source'].queryset=rows
        self.fields['source'].label_from_instance=lambda source: source_label(kind,source)
        self.fields['team'].queryset=Team.objects.filter(company_id=company_id,is_deleted=False).order_by('name')
        if not can_manage(user,kind,company_id):
            from tasker.organization import managed_teams_for
            self.fields['team'].queryset=self.fields['team'].queryset.filter(pk__in=managed_teams_for(user))
        self.fields['assigned_to'].queryset=eligible(user,kind,company_id)
        if row:
            self.fields['source'].disabled=True
            self.initial.update(source=row.source_id,team=row.team_id,assigned_to=row.assigned_to_id,
                enabled=row.enabled,priority=row.priority,next_review_on=row.next_review_on,contact_interval_days=row.contact_interval_days,
                sla_due_on=row.sla_due_on,expected_revision=str(row.revision))
        else:
            self.initial['expected_revision']='new'
        if kind != 'quotation_work':
            self.fields.pop('contact_interval_days')
        if kind != 'service_work':
            self.fields.pop('sla_due_on')
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs['class']='form-check-input' if isinstance(field,forms.BooleanField) else 'form-control'


class ReviewForm(forms.Form):
    expected_revision = forms.UUIDField(widget=forms.HiddenInput)
    expected_source_version = forms.CharField(widget=forms.HiddenInput)
    client_key = forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    next_review_on = forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Next follow-up')
    notes = forms.CharField(required=False,max_length=5000,widget=forms.Textarea(attrs={'rows':3}),
        help_text='Source-only notes. Excluded from Tasker reminders and AI context.')

    def __init__(self,*args,row,manager=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial.update(expected_revision=row.revision,expected_source_version=row.current['version'],
            next_review_on=row.next_review_on,notes=row.notes)
        for index,label in enumerate(CHECKLISTS.get(row.kind,())):
            self.fields[f'check_{index}']=forms.BooleanField(required=False,label=label,
                initial=row.checklist.get(label,False))
        if row.kind == 'service_work' and manager:
            self.fields['accept_quality']=forms.BooleanField(required=False,label='Accept repair quality and handoff')
        if row.kind == 'quotation_work':
            self.fields['contact_done']=forms.BooleanField(required=False,label='Contact completed; schedule the next follow-up using the agreed interval')
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs['class']='form-check-input' if isinstance(field,forms.BooleanField) else 'form-control'
