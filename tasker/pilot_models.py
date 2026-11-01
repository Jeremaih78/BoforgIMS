"""Prospective pilot protocols and accepted business measurements, never activity scores."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator,MaxValueValidator
from django.db import models
from django.db.models import Q,F
from django.utils import timezone
from .models import AuditedSoftDeleteModel
from .planning_models import validate_zone


FEATURES=('core','planning','operator','coordination','reports','ai','strategy','business','improvement','recurrence')
METRICS=('staffed_hours','accepted_output','quality_passed','quality_checked','rework_count','cycle_minutes',
    'cycle_samples','blocked_hours','deadline_due','deadline_met','overtime_hours','admin_hours')
DEFINITIONS=('output_source','staffing_source','quality_source','timing_source','workload_adjustment',
    'spillover_policy','missing_data_policy','independent_review','parallel_trends_review')


class Pilot(AuditedSoftDeleteModel):
    study=models.ForeignKey('tasker.BaselineStudy',on_delete=models.PROTECT,related_name='pilots')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='owned_tasker_pilots')
    title=models.CharField(max_length=180)
    is_synthetic=models.BooleanField(default=False)
    method=models.CharField(max_length=16,default='STAGGERED',choices=(('STAGGERED','Staggered matched rollout'),('MATCHED','Matched comparison'),('BEFORE_AFTER','Exploratory before / after')))
    timezone_name=models.CharField(max_length=64,default='Africa/Harare',validators=[validate_zone])
    starts_on=models.DateField()
    ends_on=models.DateField()
    weekdays=models.JSONField(default=list)
    excluded_dates=models.JSONField(default=list,blank=True)
    definitions=models.JSONField(default=dict)
    minimum_pairs=models.PositiveSmallIntegerField(default=4,validators=[MinValueValidator(4),MaxValueValidator(20)])
    minimum_days=models.PositiveSmallIntegerField(default=20,validators=[MinValueValidator(5),MaxValueValidator(65)])
    minimum_output=models.PositiveIntegerField(default=30,validators=[MinValueValidator(1)])
    target_percent=models.DecimalField(max_digits=7,decimal_places=2,default=Decimal('70'),validators=[MinValueValidator(70),MaxValueValidator(1000)])
    features=models.JSONField(default=list)
    state=models.CharField(max_length=12,default='DRAFT',choices=(('DRAFT','Draft protocol'),('APPROVED','Protocol approved'),('PAUSED','Paused'),('CLOSED','Closed')))
    revision=models.UUIDField(default=uuid.uuid4,editable=False)
    approved_at=models.DateTimeField(null=True,blank=True,editable=False)
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    protocol_digest=models.CharField(max_length=64,blank=True,editable=False)
    rollout_authorized_at=models.DateTimeField(null=True,blank=True,editable=False)
    rollout_authorized_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')

    class Meta:
        constraints=[models.CheckConstraint(condition=Q(ends_on__gte=F('starts_on')),name='tasker_pilot_date_order'),
            models.CheckConstraint(condition=Q(target_percent__gte=70),name='tasker_pilot_target_floor')]

    @property
    def company_id(self):return self.study.company_id
    @property
    def team_id(self):return self.study.team_id
    def __str__(self):return self.title
    def clean(self):
        validate_zone(self.timezone_name)
        if self.ends_on and self.starts_on and not 0<=(self.ends_on-self.starts_on).days<90:raise ValidationError('Use a follow-up window of at most 90 calendar days.')
        if self.study_id:
            if not self.study.start_date or not self.study.end_date or (self.study.end_date-self.study.start_date).days>=90:
                raise ValidationError('Choose a baseline with a defined window of at most 90 calendar days.')
            if self.starts_on and self.starts_on<=self.study.end_date:raise ValidationError('Follow-up must begin after the baseline ends.')
        if not isinstance(self.weekdays,list) or not self.weekdays or any(type(v) is not int or v not in range(7) for v in self.weekdays) or len(set(self.weekdays))!=len(self.weekdays):raise ValidationError('Choose distinct observation weekdays.')
        if not isinstance(self.excluded_dates,list) or len(self.excluded_dates)>90 or any(type(v) is not str for v in self.excluded_dates):raise ValidationError('Use a bounded list of excluded ISO dates.')
        from datetime import date
        try:
            for value in self.excluded_dates:date.fromisoformat(value)
        except ValueError:raise ValidationError('Use ISO dates for calendar exclusions.')
        if not isinstance(self.features,list) or len(set(self.features))!=len(self.features) or any(v not in FEATURES for v in self.features):raise ValidationError('Choose allowlisted pilot features.')
        if not isinstance(self.definitions,dict) or set(self.definitions)-set(DEFINITIONS) or any(type(v) is not str or len(v)>3000 for v in self.definitions.values()):raise ValidationError('Use the defined protocol fields, each within 3,000 characters.')
        if self.pk:
            old=type(self).all_objects.get(pk=self.pk)
            if self.is_synthetic!=old.is_synthetic or self.study_id!=old.study_id:raise ValidationError('A pilot retains its baseline and synthetic/real identity.')
            if old.approved_at and any(getattr(self,n)!=getattr(old,n) for n in ('owner_id','title','method','timezone_name','starts_on','ends_on','weekdays','excluded_dates','definitions','minimum_pairs','minimum_days','minimum_output','target_percent','features','approved_at','approved_by_id','protocol_digest')):
                raise ValidationError('An approved protocol is frozen. Create a new pilot for a changed protocol.')


class PilotMember(models.Model):
    pilot=models.ForeignKey(Pilot,on_delete=models.PROTECT,related_name='members')
    employee=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_pilot_memberships')
    arm=models.CharField(max_length=12,choices=(('PILOT','Early / pilot group'),('COMPARISON','Later / comparison group')))
    match_key=models.CharField(max_length=60,help_text='One early and one later employee per matched block, within the same role and workload.')
    rollout_on=models.DateField(null=True,blank=True)
    withdrawn_at=models.DateTimeField(null=True,blank=True,editable=False)
    withdrawal_reason=models.CharField(max_length=500,blank=True)
    access_enabled=models.BooleanField(default=False)

    class Meta:
        constraints=[models.UniqueConstraint(fields=('pilot','employee'),name='tasker_pilot_member_once'),
            models.UniqueConstraint(fields=('pilot','arm','match_key'),name='tasker_pilot_match_arm_once')]
    def __str__(self):return f'Pilot member {self.pk}'
    def clean(self):
        if self.pk:
            old=type(self).objects.get(pk=self.pk)
            if old.pilot.approved_at and any(getattr(self,n)!=getattr(old,n) for n in ('pilot_id','employee_id','arm','match_key','rollout_on')):raise ValidationError('The predeclared cohort is frozen; withdrawals retain the original denominator.')


class PilotObservation(AuditedSoftDeleteModel):
    pilot=models.ForeignKey(Pilot,on_delete=models.PROTECT,related_name='observations')
    employee=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_pilot_observations')
    phase=models.CharField(max_length=10,choices=(('BASELINE','Baseline supplement'),('FOLLOWUP','Follow-up')))
    baseline_observation=models.ForeignKey('tasker.BaselineObservation',null=True,blank=True,on_delete=models.PROTECT)
    observed_on=models.DateField()
    source_key=models.CharField(max_length=180)
    staffed_hours=models.DecimalField(max_digits=7,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0),MaxValueValidator(24)])
    accepted_output=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0)])
    quality_passed=models.PositiveIntegerField(null=True,blank=True)
    quality_checked=models.PositiveIntegerField(null=True,blank=True)
    rework_count=models.PositiveIntegerField(null=True,blank=True)
    cycle_minutes=models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0)])
    cycle_samples=models.PositiveIntegerField(null=True,blank=True)
    blocked_hours=models.DecimalField(max_digits=7,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0),MaxValueValidator(24)])
    deadline_due=models.PositiveIntegerField(null=True,blank=True)
    deadline_met=models.PositiveIntegerField(null=True,blank=True)
    overtime_hours=models.DecimalField(max_digits=7,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0),MaxValueValidator(24)])
    admin_hours=models.DecimalField(max_digits=7,decimal_places=3,null=True,blank=True,validators=[MinValueValidator(0),MaxValueValidator(24)])
    tasker_exposed=models.BooleanField(null=True,blank=True)
    output_source=models.CharField(max_length=12,default='REGISTER',choices=(('REGISTER','Approved business register'),('OUTCOMES','Linked accepted goal outcomes')))
    outcomes=models.ManyToManyField('tasker.OutcomeMeasurement',blank=True)
    evidence=models.TextField(blank=True,max_length=5000)
    status=models.CharField(max_length=10,default='DRAFT',choices=(('DRAFT','Awaiting review'),('ACCEPTED','Accepted'),('RETRACTED','Retracted')))
    revision=models.UUIDField(default=uuid.uuid4,editable=False)
    accepted_at=models.DateTimeField(null=True,blank=True,editable=False)
    accepted_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')

    class Meta:
        constraints=[models.UniqueConstraint(fields=('pilot','source_key'),name='tasker_pilot_source_once'),
            models.UniqueConstraint(fields=('pilot','employee','observed_on','phase'),condition=~Q(status='RETRACTED'),name='tasker_pilot_day_once'),
            models.CheckConstraint(condition=Q(staffed_hours__isnull=True)|Q(staffed_hours__gte=0,staffed_hours__lte=24),name='tasker_pilot_hours_range'),
            models.CheckConstraint(condition=Q(accepted_output__isnull=True)|Q(accepted_output__gte=0),name='tasker_pilot_output_positive'),
            models.CheckConstraint(condition=~Q(status='ACCEPTED')|Q(accepted_at__isnull=False,accepted_by__isnull=False),name='tasker_pilot_acceptance_proof')]
        indexes=[models.Index(fields=('pilot','status','phase'),name='tasker_pilot_measure_idx')]
    def __str__(self):return f'Pilot observation {self.pk}'
    def clean(self):
        for name in METRICS:
            value=getattr(self,name)
            if value is not None and not Decimal(value).is_finite():raise ValidationError('Measurements must be finite or explicitly unknown.')
        if self.phase=='BASELINE':
            if not self.baseline_observation_id:raise ValidationError('Link the canonical Stage 2 baseline observation.')
            if any(getattr(self,n) is not None for n in ('staffed_hours','accepted_output','quality_passed','rework_count')):raise ValidationError('Baseline core values come from Stage 2 and cannot be overridden here.')
            base=self.baseline_observation
            if (base.study_id,base.employee_id,base.observed_on)!=(self.pilot.study_id,self.employee_id,self.observed_on):raise ValidationError('The baseline reference must match the study, employee and day.')
            hours,passed=base.staffed_hours,base.quality_passed
        else:
            if self.baseline_observation_id:raise ValidationError('Follow-up data cannot be relabelled as baseline.')
            hours,passed=self.staffed_hours,self.quality_passed
        if self.quality_checked is not None and passed is not None and passed>self.quality_checked:raise ValidationError('Passed checks cannot exceed checks performed.')
        if self.deadline_due is not None and self.deadline_met is not None and self.deadline_met>self.deadline_due:raise ValidationError('Met deadlines cannot exceed due deadlines.')
        if hours is not None and any(getattr(self,n) is not None and getattr(self,n)>hours for n in ('blocked_hours','overtime_hours','admin_hours')):raise ValidationError('Recorded effort components cannot exceed staffed hours.')
        if self.cycle_samples==0 and self.cycle_minutes not in (None,0):raise ValidationError('Cycle duration requires measured cycle samples.')
        if self.pk:
            old=type(self).all_objects.get(pk=self.pk)
            if old.status!='DRAFT' and any(getattr(self,n)!=getattr(old,n) for n in (*METRICS,'pilot_id','employee_id','phase','baseline_observation_id','observed_on','source_key','evidence','tasker_exposed','output_source','accepted_at','accepted_by_id')):raise ValidationError('Accepted evidence is immutable; retract and record a correction.')


class PilotEvent(models.Model):
    pilot=models.ForeignKey(Pilot,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    client_key=models.UUIDField()
    action=models.CharField(max_length=32)
    request_digest=models.CharField(max_length=64)
    details=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now,editable=False)
    class Meta:
        constraints=[models.UniqueConstraint(fields=('actor','client_key'),name='tasker_pilot_request_once')]
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('Pilot audit evidence is immutable.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('Pilot audit evidence is retained.')


class PilotReleaseEvidence(models.Model):
    pilot=models.ForeignKey(Pilot,on_delete=models.PROTECT,related_name='release_evidence')
    check_key=models.CharField(max_length=60)
    status=models.CharField(max_length=12,choices=(('VERIFIED','Verified'),('FAILED','Failed'),('PENDING','Pending'),('DEFERRED','Conditional / deferred')))
    reference=models.CharField(max_length=500)
    summary=models.CharField(max_length=2000,blank=True)
    source_digest=models.CharField(max_length=64)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now,editable=False)
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('Append a new release check instead of editing evidence.')
        self.full_clean();return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('Release checks are retained.')


class PilotValidation(models.Model):
    pilot=models.ForeignKey(Pilot,on_delete=models.PROTECT,related_name='validations')
    report_digest=models.CharField(max_length=64)
    protocol_digest=models.CharField(max_length=64)
    reference=models.CharField(max_length=500)
    reviewer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now,editable=False)
    class Meta:
        constraints=[models.UniqueConstraint(fields=('pilot','report_digest'),name='tasker_pilot_validation_once')]
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('Validation receipts are immutable.')
        self.full_clean();return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('Validation receipts are retained.')
