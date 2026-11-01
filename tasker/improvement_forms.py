import uuid
from datetime import time,timedelta
from django import forms
from django.contrib.auth import get_user_model
from tasker.models import Team
from tasker.organization import assignable_users_for,managed_teams_for
from tasker.improvement_models import RecurringWorkflow
from tasker.services.improvement import templates_for
from tasker.services.work_dates import work_date


def style(form):
    for field in form.fields.values():
        if field.widget.is_hidden:continue
        field.widget.attrs['class']='im-checks' if isinstance(field.widget,forms.CheckboxSelectMultiple) else (
            'form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control')


class CommandForm(forms.Form):
    expected_revision=forms.CharField(widget=forms.HiddenInput)
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)


class SOPForm(CommandForm):
    title=forms.CharField(max_length=255)
    team=forms.ModelChoiceField(queryset=Team.objects.none(),required=False,label='Sharing scope',empty_label='Personal SOP',
        help_text='A team SOP is visible to that team. Existing execution records retain their published version.')
    instructions=forms.CharField(required=False,max_length=10000,widget=forms.Textarea(attrs={'rows':4}))
    definition_of_done=forms.CharField(max_length=5000,widget=forms.Textarea(attrs={'rows':3}),label='Definition of done')
    steps=forms.CharField(widget=forms.Textarea(attrs={'rows':5}),help_text='One checklist step per line; up to 50 steps.')

    def __init__(self,*args,user,template=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['team'].queryset=managed_teams_for(user)
        self.initial['expected_revision']=str(template.revision) if template else 'new'
        if template:
            version=template.versions.get(number=template.latest_version)
            self.initial.update(title=version.title,team=template.team_id,instructions=version.instructions,
                definition_of_done=version.definition_of_done,steps='\n'.join(version.steps))
            self.fields['team'].disabled=True
        style(self)

    def clean_steps(self):
        rows=[row.strip() for row in self.cleaned_data['steps'].splitlines() if row.strip()]
        if not 1<=len(rows)<=50 or any(len(row)>255 for row in rows):raise forms.ValidationError('Use 1–50 steps, each at most 255 characters.')
        return rows


class SOPRunForm(CommandForm):
    due_date=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)


class RoutineForm(CommandForm):
    template=forms.ModelChoiceField(queryset=None,label='SOP')
    owner=forms.ModelChoiceField(queryset=get_user_model().objects.none(),label='Responsible employee')
    frequency=forms.ChoiceField(choices=(('DAILY','Every calendar day'),('WEEKLY','Selected weekdays'),('MONTHLY','Monthly; clamp to month end')))
    interval=forms.IntegerField(min_value=1,max_value=366,initial=1,label='Repeat every',help_text='Days, weeks or months, according to the selected frequency.')
    weekdays=forms.TypedMultipleChoiceField(coerce=int,required=False,choices=tuple(enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))),widget=forms.CheckboxSelectMultiple)
    timezone_name=forms.CharField(max_length=64,initial='Africa/Harare',label='Timezone')
    local_time=forms.TimeField(initial=time(9),widget=forms.TimeInput(attrs={'type':'time'}),label='Release time',help_text='Ambiguous or non-existent DST times are skipped with a recorded calendar exception.')
    start_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Calendar starts')
    end_on=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Calendar ends')
    missed_policy=forms.ChoiceField(choices=(('LATEST','Latest due occurrence only'),('SKIP','Skip past days'),('ALL','Catch up all; at most 20 per job')))
    overlap_policy=forms.ChoiceField(choices=(('SKIP_OPEN','Skip while earlier work is still open'),('ALLOW','Allow overlapping tasks')))
    paused=forms.BooleanField(required=False,initial=True,label='Keep paused')
    rebase=forms.BooleanField(required=False,label='Rebase the future calendar if the rule changes',help_text='Previously created tasks and execution evidence are preserved. The prior pending calendar is retained in the change audit.')

    def __init__(self,*args,user,template,row=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['template'].queryset=templates_for(user)
        self.fields['template'].disabled=True
        self.initial.update(template=template.pk,expected_revision=str(row.revision) if row else 'new',
            start_on=work_date(user)+timedelta(days=1),weekdays=[0,1,2,3,4],owner=user.pk)
        self.fields['owner'].queryset=(assignable_users_for(user,team=template.team,company=template.team.company)
            if template.team_id else get_user_model().objects.filter(pk=user.pk))
        if row:
            for name in ('frequency','interval','weekdays','timezone_name','local_time','start_on','end_on','missed_policy','overlap_policy','paused'):
                self.initial[name]=getattr(row,name)
            self.initial['owner']=row.owner_id
        style(self)


class ExceptionForm(CommandForm):
    scheduled_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Skip date')
    reason=forms.CharField(max_length=500,label='Shared exception reason')
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)


class CarryForm(forms.Form):
    expected_version=forms.CharField(widget=forms.HiddenInput)
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    action=forms.ChoiceField(choices=(('CLARIFY','Clarify the outcome'),('SPLIT','Split a smaller next step'),('DELEGATE','Delegate work'),('RESCHEDULE','Reschedule deliberately'),('STOP','Stop work that no longer matters')))
    reason=forms.CharField(max_length=1000,widget=forms.Textarea(attrs={'rows':2}),label='Shared reason',help_text='This explanation is recorded with the task’s shared history.')
    title=forms.CharField(max_length=255,required=False,label='Smaller task title')
    definition_of_done=forms.CharField(max_length=5000,required=False,widget=forms.Textarea(attrs={'rows':3}),label='Clear acceptance condition')
    due_date=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    assignee=forms.ModelChoiceField(queryset=get_user_model().objects.none(),required=False,label='Delegate to')
    def __init__(self,*args,user,task,**kwargs):
        super().__init__(*args,**kwargs)
        from tasker.services.workbench import work_version
        self.initial['expected_version']=work_version(task)
        self.fields['assignee'].queryset=assignable_users_for(user,team=task.team,company=task.company)
        if not user.has_perm('tasker.assign_task') or not self.fields['assignee'].queryset.exclude(pk=task.owner_id).exists():
            self.fields['action'].choices=[item for item in self.fields['action'].choices if item[0]!='DELEGATE']
            self.fields.pop('assignee')
        style(self)


class WeeklyForm(forms.Form):
    week_start=forms.DateField(widget=forms.HiddenInput)
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    note=forms.CharField(required=False,max_length=5000,widget=forms.Textarea(attrs={'rows':4}))
    def __init__(self,*args,shared=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['note'].label='Shared team review note' if shared else 'Private weekly reflection'
        style(self)


class ExperimentForm(forms.Form):
    version=forms.IntegerField(widget=forms.HiddenInput)
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    title=forms.CharField(max_length=255)
    owner=forms.ModelChoiceField(queryset=get_user_model().objects.none(),label='Experiment owner')
    team=forms.ModelChoiceField(queryset=Team.objects.none(),required=False,empty_label='Personal experiment',label='Experiment scope')
    expected_benefit=forms.CharField(max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    unit=forms.CharField(max_length=60,label='Measurement unit',help_text='For example minutes per setup, accepted outputs or defects per batch.')
    baseline=forms.DecimalField(max_digits=20,decimal_places=4)
    target=forms.DecimalField(max_digits=20,decimal_places=4)
    direction=forms.ChoiceField(choices=(('HIGHER','Higher is better'),('LOWER','Lower is better')))
    review_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}),label='Review date')
    def __init__(self,*args,user,capture,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial.update(version=capture.version,title=capture.title,owner=user.pk,review_on=work_date(user)+timedelta(days=7))
        self.fields['team'].queryset=Team.objects.filter(pk=capture.shared_team_id) if capture.visibility=='TEAM' else Team.objects.none()
        self.fields['owner'].queryset=assignable_users_for(user,team=capture.shared_team,company=capture.shared_team.company) if capture.shared_team_id else get_user_model().objects.filter(pk=user.pk)
        style(self)


class ResultForm(CommandForm):
    value=forms.DecimalField(max_digits=20,decimal_places=4,label='Observed value')
    evidence=forms.CharField(max_length=5000,widget=forms.Textarea(attrs={'rows':3}),help_text='Use verifiable evidence. A team experiment shares this observation with its current team.')
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)


class ExperimentOwnerForm(CommandForm):
    expected_task_version=forms.CharField(widget=forms.HiddenInput)


class DayActionForm(forms.Form):
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    expected_plan_version=forms.CharField(widget=forms.HiddenInput)
    expected_review_version=forms.CharField(widget=forms.HiddenInput)
    expected_revision=forms.CharField(widget=forms.HiddenInput)
    stop_focus=forms.BooleanField(required=False,label='Stop my current Focus when closing')
    reason=forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={'rows':2}),label='Private reason for reopening')
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)
