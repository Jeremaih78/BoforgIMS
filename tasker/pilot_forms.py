import uuid
from datetime import timedelta
from django import forms
from django.contrib.auth import get_user_model
from tasker.models import BaselineStudy,BaselineObservation,OutcomeMeasurement
from tasker.pilot_models import Pilot,PilotObservation,FEATURES,DEFINITIONS,METRICS
from tasker.improvement_forms import style
from tasker.organization import assignable_users_for
from tasker.services import baseline,pilots
from tasker.services.pilot_release import OPERATING_CHECKS
from tasker.services.goals import visible_outcomes_for


class CommandForm(forms.Form):
    expected_revision=forms.CharField(widget=forms.HiddenInput)
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)


class PilotForm(CommandForm):
    study=forms.ModelChoiceField(queryset=BaselineStudy.objects.none(),label='Stage 2 baseline')
    owner=forms.ModelChoiceField(queryset=get_user_model().objects.none(),required=False,help_text='The owner nominated admin. Select the registered account in this scope; no employee enrollment occurs here.')
    title=forms.CharField(max_length=180)
    is_synthetic=forms.BooleanField(required=False,label='Synthetic demonstration',help_text='A synthetic pilot can never certify productivity or grant real rollout access. This identity cannot be changed later.')
    method=forms.ChoiceField(choices=Pilot._meta.get_field('method').choices,initial='STAGGERED')
    timezone_name=forms.CharField(initial='Africa/Harare',max_length=64)
    starts_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    ends_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    weekdays=forms.TypedMultipleChoiceField(coerce=int,choices=tuple(enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'))),widget=forms.CheckboxSelectMultiple,initial=[0,1,2,3,4])
    excluded_dates=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}),help_text='One predeclared ISO holiday/excluded date per line; applies equally to both groups.')
    minimum_pairs=forms.IntegerField(min_value=4,max_value=20,initial=4,help_text='Minimum four matched blocks for exploratory paired-cluster uncertainty. This is not a power calculation.')
    minimum_days=forms.IntegerField(min_value=5,max_value=65,initial=20,label='Minimum observed days in each window')
    minimum_output=forms.IntegerField(min_value=1,initial=30,label='Minimum accepted units per group/window')
    target_percent=forms.DecimalField(min_value=70,max_value=1000,decimal_places=2,initial=70,label='Strictly greater than this uplift (%)')
    features=forms.MultipleChoiceField(choices=[(v,v.title()) for v in FEATURES],widget=forms.CheckboxSelectMultiple,initial=['core','planning','operator','coordination','reports','strategy','business','improvement','recurrence'])
    def __init__(self,*args,user,pilot=None,study=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['study'].queryset=baseline.visible_studies_for(user)
        if pilot:study=pilot.study
        self.initial['study']=study.pk if study else None
        self.initial['expected_revision']=str(pilot.revision) if pilot else 'new'
        self.fields['owner'].queryset=assignable_users_for(user,team=study.team,company=study.company) if study else get_user_model().objects.none()
        for key in DEFINITIONS:self.fields[key]=forms.CharField(required=False,max_length=3000,label=key.replace('_',' ').capitalize(),widget=forms.Textarea(attrs={'rows':2}))
        if pilot:
            for key in ('owner','title','is_synthetic','method','timezone_name','starts_on','ends_on','weekdays','minimum_pairs','minimum_days','minimum_output','target_percent','features'):
                self.initial[key]=getattr(pilot,key+'_id') if key=='owner' else getattr(pilot,key)
            self.initial['excluded_dates']='\n'.join(pilot.excluded_dates)
            self.initial.update(pilot.definitions);self.fields['study'].disabled=True;self.fields['is_synthetic'].disabled=True
        if study:self.fields['study'].disabled=True
        if not pilot:self.initial['owner']=self.fields['owner'].queryset.filter(username='admin').values_list('pk',flat=True).first()
        style(self)
    def clean(self):
        values=super().clean()
        values['definitions']={k:values.pop(k,'') for k in DEFINITIONS}
        values['excluded_dates']=[v.strip() for v in values.get('excluded_dates','').splitlines() if v.strip()]
        return values


class MemberForm(CommandForm):
    employee=forms.ModelChoiceField(queryset=get_user_model().objects.none())
    arm=forms.ChoiceField(choices=(('PILOT','Early / pilot'),('COMPARISON','Later / comparison')))
    match_key=forms.CharField(max_length=60,label='Matched block',help_text='Use the same block name for one early and one later employee with comparable role and workload.')
    rollout_on=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    def __init__(self,*args,user,pilot,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['employee'].queryset=assignable_users_for(user,team=pilot.study.team,company=pilot.study.company)
        self.initial['expected_revision']=str(pilot.revision);style(self)


class ObservationForm(CommandForm):
    employee=forms.ModelChoiceField(queryset=get_user_model().objects.none())
    phase=forms.ChoiceField(choices=(('FOLLOWUP','Follow-up business measurements'),('BASELINE','Supplement Stage 2 evidence')))
    baseline_observation=forms.ModelChoiceField(queryset=BaselineObservation.objects.none(),required=False)
    observed_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    source_key=forms.CharField(max_length=180,help_text='Unique business register/day reference. Corrections need a new reference after retraction.')
    tasker_exposed=forms.TypedChoiceField(required=False,coerce=lambda v:{'true':True,'false':False}.get(v),empty_value=None,
        choices=(('','Unknown'),('false','No Tasker exposure'),('true','Tasker used')))
    output_source=forms.ChoiceField(choices=(('REGISTER','Approved business register'),('OUTCOMES','Linked accepted goal outcomes')))
    outcomes=forms.ModelMultipleChoiceField(queryset=OutcomeMeasurement.objects.none(),required=False,help_text='Use only if output source is linked goal outcomes. Unit, owner, day and total must match.')
    evidence=forms.CharField(required=False,max_length=5000,widget=forms.Textarea(attrs={'rows':3}),help_text='Shared work evidence only. Exclude private reflections and personal notes.')
    def __init__(self,*args,user,pilot,row=None,**kwargs):
        super().__init__(*args,**kwargs)
        ids=pilot.members.values_list('employee_id',flat=True)
        if not pilots.manages(user,pilot):ids=[user.pk]
        self.fields['employee'].queryset=get_user_model().objects.filter(pk__in=ids,is_active=True)
        self.fields['baseline_observation'].queryset=baseline.visible_observations_for(user).filter(study=pilot.study,employee_id__in=ids,status='ACCEPTED')
        self.fields['outcomes'].queryset=visible_outcomes_for(user).filter(company_id=pilot.company_id,team_id=pilot.team_id,employee_id__in=ids,status='ACCEPTED',occurred_on__range=(pilot.starts_on,pilot.ends_on))
        for name in METRICS:
            count=name in ('quality_passed','quality_checked','rework_count','cycle_samples','deadline_due','deadline_met')
            self.fields[name]=(forms.IntegerField(required=False,min_value=0) if count else forms.DecimalField(required=False,min_value=0,max_digits=16,decimal_places=3))
            self.fields[name].help_text='Leave unknown measurements blank; zero is an explicit observation.'
        self.order_fields(['employee','phase','baseline_observation','observed_on','source_key','tasker_exposed','output_source','outcomes',*METRICS,'evidence','expected_revision','client_key'])
        self.initial['expected_revision']=str(row.revision) if row else 'new'
        if row:
            for f in ('employee','phase','baseline_observation','observed_on','source_key','tasker_exposed','output_source','evidence',*METRICS):
                self.initial[f]=getattr(row,f+'_id') if f in ('employee','baseline_observation') else ('true' if row.tasker_exposed else 'false') if f=='tasker_exposed' and row.tasker_exposed is not None else getattr(row,f)
            self.initial['outcomes']=list(row.outcomes.values_list('pk',flat=True))
        style(self)


class ReviewForm(CommandForm):
    reason=forms.CharField(required=False,max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)


class ControlForm(ReviewForm):
    member_id=forms.IntegerField(required=False,widget=forms.HiddenInput)


class ReleaseForm(forms.Form):
    client_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    check_key=forms.ChoiceField(choices=list(OPERATING_CHECKS.items()))
    status=forms.ChoiceField(choices=(('PENDING','Pending'),('VERIFIED','Verified'),('FAILED','Failed'),('DEFERRED','Conditional / deferred')))
    reference=forms.CharField(max_length=500)
    summary=forms.CharField(max_length=2000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)


class ValidationForm(CommandForm):
    report_digest=forms.CharField(widget=forms.HiddenInput)
    reference=forms.CharField(max_length=500,label='Independent methodology/data review reference')
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);style(self)
