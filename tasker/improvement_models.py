"""Versioned routines and improvement evidence. Private reflections never become team facts."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .models import AuditedSoftDeleteModel
from .planning_models import validate_zone


class FrozenImprovement(models.Model):
    class Meta:
        abstract=True

    def save(self,*args,**kwargs):
        if not self._state.adding:
            raise ValidationError('Execution evidence is immutable. Append a new revision.')
        self.full_clean()
        return super().save(*args,**kwargs)

    def delete(self,*args,**kwargs):
        raise ValidationError('Execution evidence is retained.')


class SOPTemplate(AuditedSoftDeleteModel):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_sops')
    team=models.ForeignKey('tasker.Team',null=True,blank=True,on_delete=models.PROTECT,related_name='sops')
    title=models.CharField(max_length=255)
    latest_version=models.PositiveIntegerField(default=0)
    archived=models.BooleanField(default=False)
    revision=models.UUIDField(default=uuid.uuid4,editable=False)

    class Meta:
        ordering=('title','pk')

    def __str__(self):return self.title


class SOPVersion(FrozenImprovement):
    template=models.ForeignKey(SOPTemplate,on_delete=models.PROTECT,related_name='versions')
    number=models.PositiveIntegerField()
    title=models.CharField(max_length=255)
    instructions=models.TextField(blank=True)
    definition_of_done=models.TextField()
    steps=models.JSONField(default=list)
    published_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    published_at=models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        ordering=('-number',)
        constraints=[models.UniqueConstraint(fields=('template','number'),name='tasker_sop_version_once')]

    def clean(self):
        if not self.definition_of_done.strip() or len(self.definition_of_done)>5000 or len(self.instructions)>10000:
            raise ValidationError('Define an acceptance condition; keep instructions within 10,000 characters.')
        if not isinstance(self.steps,list) or not 1<=len(self.steps)<=50 or any(
                type(s) is not str or not s.strip() or len(s)>255 for s in self.steps):
            raise ValidationError('Provide one to fifty clear checklist steps, each at most 255 characters.')


class RecurringWorkflow(AuditedSoftDeleteModel):
    template=models.ForeignKey(SOPTemplate,on_delete=models.PROTECT,related_name='routines')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_routines')
    company=models.ForeignKey('accounting.Company',null=True,blank=True,on_delete=models.PROTECT)
    team=models.ForeignKey('tasker.Team',null=True,blank=True,on_delete=models.PROTECT)
    frequency=models.CharField(max_length=8,choices=(('DAILY','Daily'),('WEEKLY','Weekly'),('MONTHLY','Monthly')))
    interval=models.PositiveSmallIntegerField(default=1,validators=[MinValueValidator(1),MaxValueValidator(366)])
    weekdays=models.JSONField(default=list,blank=True)
    timezone_name=models.CharField(max_length=64,validators=[validate_zone])
    local_time=models.TimeField()
    start_on=models.DateField()
    end_on=models.DateField(null=True,blank=True)
    next_on=models.DateField(null=True,blank=True)
    missed_policy=models.CharField(max_length=8,choices=(('LATEST','Latest only'),('SKIP','Skip past days'),('ALL','Bounded catch-up')))
    overlap_policy=models.CharField(max_length=12,default='SKIP_OPEN',choices=(('SKIP_OPEN','Skip while prior work is open'),('ALLOW','Allow overlapping tasks')))
    paused=models.BooleanField(default=True)
    needs_review=models.BooleanField(default=False,editable=False)
    revision=models.UUIDField(default=uuid.uuid4,editable=False)

    class Meta:
        ordering=('next_on','pk')
        indexes=[models.Index(fields=('paused','next_on'),name='tasker_routine_due_idx')]

    def clean(self):
        validate_zone(self.timezone_name)
        if not isinstance(self.weekdays,list) or any(type(d) is not int or d not in range(7) for d in self.weekdays) or len(set(self.weekdays))!=len(self.weekdays):
            raise ValidationError('Choose distinct weekdays.')
        if self.frequency=='WEEKLY' and not self.weekdays:
            raise ValidationError('Choose at least one weekday.')
        if self.end_on and self.end_on<self.start_on:
            raise ValidationError('The end date must follow the start date.')
        if self.template_id and (self.team_id!=self.template.team_id or (
                not self.team_id and self.owner_id!=self.template.owner_id)):
            raise ValidationError('Routine execution must preserve the template’s private or team scope.')
        if self.team_id and self.team.company_id!=self.company_id:
            raise ValidationError('Choose the team’s company.')

    def __str__(self):return f'{self.template.title} · {self.get_frequency_display()}'


class RoutineException(FrozenImprovement):
    routine=models.ForeignKey(RecurringWorkflow,on_delete=models.PROTECT,related_name='exceptions')
    scheduled_on=models.DateField()
    reason=models.CharField(max_length=500)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        constraints=[models.UniqueConstraint(fields=('routine','scheduled_on'),name='tasker_routine_exception_once')]


class RoutineOccurrence(FrozenImprovement):
    routine=models.ForeignKey(RecurringWorkflow,on_delete=models.PROTECT,related_name='occurrences')
    scheduled_on=models.DateField()
    scheduled_at=models.DateTimeField(null=True,blank=True)
    rule_revision=models.UUIDField()
    rule_snapshot=models.JSONField(default=dict)
    version=models.ForeignKey(SOPVersion,null=True,blank=True,on_delete=models.PROTECT)
    task=models.OneToOneField('tasker.Task',null=True,blank=True,on_delete=models.PROTECT,related_name='routine_occurrence')
    status=models.CharField(max_length=8,choices=(('CREATED','Task created'),('SKIPPED','Skipped')))
    reason_code=models.CharField(max_length=64,blank=True)
    created_at=models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        ordering=('-scheduled_on','-pk')
        constraints=[models.UniqueConstraint(fields=('routine','scheduled_on'),name='tasker_routine_occurrence_once'),
            models.CheckConstraint(condition=Q(status='CREATED',task__isnull=False,version__isnull=False)|Q(status='SKIPPED',task__isnull=True),name='tasker_routine_task_evidence')]


class SOPExecution(FrozenImprovement):
    version=models.ForeignKey(SOPVersion,on_delete=models.PROTECT,related_name='executions')
    task=models.OneToOneField('tasker.Task',on_delete=models.PROTECT,related_name='sop_execution')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    client_key=models.UUIDField()
    created_at=models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        constraints=[models.UniqueConstraint(fields=('actor','client_key'),name='tasker_sop_execution_once')]


class ImprovementEvent(FrozenImprovement):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    client_key=models.UUIDField(null=True,blank=True)
    request_digest=models.CharField(max_length=64,blank=True)
    entity_type=models.CharField(max_length=32)
    entity_id=models.CharField(max_length=64)
    action=models.CharField(max_length=32)
    details=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        constraints=[models.UniqueConstraint(fields=('actor','client_key'),condition=Q(client_key__isnull=False),name='tasker_improvement_request_once')]


class CarryDecision(FrozenImprovement):
    task=models.ForeignKey('tasker.Task',on_delete=models.PROTECT,related_name='carry_decisions')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    action=models.CharField(max_length=12,choices=(('CLARIFY','Clarify'),('SPLIT','Split'),('DELEGATE','Delegate'),('RESCHEDULE','Reschedule'),('STOP','Stop')))
    reason=models.CharField(max_length=1000)
    prior_version=models.CharField(max_length=64)
    result_task=models.ForeignKey('tasker.Task',null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(default=timezone.now,editable=False)


class WeeklyReview(FrozenImprovement):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_weekly_reviews')
    team=models.ForeignKey('tasker.Team',null=True,blank=True,on_delete=models.PROTECT)
    week_start=models.DateField()
    note=models.TextField(blank=True)
    references=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now,editable=False)


class Experiment(AuditedSoftDeleteModel):
    source_capture=models.OneToOneField('tasker.CaptureItem',on_delete=models.PROTECT,related_name='experiment')
    task=models.OneToOneField('tasker.Task',on_delete=models.PROTECT,related_name='experiment')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='tasker_experiments')
    team=models.ForeignKey('tasker.Team',null=True,blank=True,on_delete=models.PROTECT)
    company=models.ForeignKey('accounting.Company',null=True,blank=True,on_delete=models.PROTECT)
    title=models.CharField(max_length=255)
    expected_benefit=models.CharField(max_length=1000)
    unit=models.CharField(max_length=60)
    baseline=models.DecimalField(max_digits=20,decimal_places=4)
    target=models.DecimalField(max_digits=20,decimal_places=4)
    direction=models.CharField(max_length=6,choices=(('HIGHER','Higher'),('LOWER','Lower')))
    review_on=models.DateField()
    revision=models.UUIDField(default=uuid.uuid4,editable=False)

    def clean(self):
        from decimal import Decimal
        self.baseline=Decimal(str(self.baseline));self.target=Decimal(str(self.target))
        if not self.baseline.is_finite() or not self.target.is_finite() or (
                self.target<=self.baseline if self.direction=='HIGHER' else self.target>=self.baseline):
            raise ValidationError('Define a finite baseline and a target in the chosen direction.')


class ExperimentResult(FrozenImprovement):
    origin=models.OneToOneField('self',null=True,blank=True,on_delete=models.PROTECT,related_name='acceptance')
    experiment=models.ForeignKey(Experiment,on_delete=models.PROTECT,related_name='results')
    value=models.DecimalField(max_digits=20,decimal_places=4)
    evidence=models.TextField()
    task_version=models.CharField(max_length=64)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    accepted_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    accepted_at=models.DateTimeField(null=True,blank=True)
    outcome=models.ForeignKey('tasker.OutcomeMeasurement',null=True,blank=True,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now,editable=False)

    def clean(self):
        from decimal import Decimal
        if not Decimal(str(self.value)).is_finite() or not self.evidence.strip() or len(self.evidence)>5000:
            raise ValidationError('Record a finite observation and evidence of up to 5,000 characters.')
        if bool(self.accepted_at)!=bool(self.accepted_by_id):raise ValidationError('Acceptance requires its actor and timestamp.')


class DayClosure(models.Model):
    plan=models.OneToOneField('tasker.DailyPlan',on_delete=models.PROTECT,related_name='closure')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    closed=models.BooleanField(default=False)
    revision=models.UUIDField(default=uuid.uuid4,editable=False)
    closed_at=models.DateTimeField(null=True,blank=True)
    updated_at=models.DateTimeField(auto_now=True)


class DayRevision(FrozenImprovement):
    closure=models.ForeignKey(DayClosure,on_delete=models.PROTECT,related_name='revisions')
    action=models.CharField(max_length=16)
    private_reason=models.CharField(max_length=1000,blank=True)
    review_digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(default=timezone.now,editable=False)
