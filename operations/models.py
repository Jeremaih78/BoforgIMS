"""Explicit operational responsibility. Business transactions remain in their source modules."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


KINDS = (
    ('shipment_work', 'Shipment ETA, clearance and receiving'),
    ('service_work', 'Repair and quality handoff'),
    ('stocktake_work', 'Stocktake approval'),
    ('quotation_work', 'Sent quotation follow-up'),
    ('fulfilment_work', 'Paid shop order fulfilment'),
    ('bill_work', 'Supplier bill review'),
    ('period_work', 'Fiscal close checklist'),
)


class Responsibility(models.Model):
    kind = models.CharField(max_length=32, choices=KINDS)
    source_id = models.PositiveBigIntegerField()
    shipment = models.OneToOneField('inventory.Shipment', null=True, blank=True, on_delete=models.SET_NULL)
    service_case = models.OneToOneField('inventory.ServiceCase', null=True, blank=True, on_delete=models.SET_NULL)
    stocktake = models.OneToOneField('inventory.Stocktake', null=True, blank=True, on_delete=models.SET_NULL)
    quotation = models.OneToOneField('sales.Quotation', null=True, blank=True, on_delete=models.SET_NULL)
    order = models.OneToOneField('shop.Order', null=True, blank=True, on_delete=models.SET_NULL)
    bill = models.OneToOneField('accounting.SupplierBill', null=True, blank=True, on_delete=models.SET_NULL)
    period = models.OneToOneField('accounting.FiscalPeriod', null=True, blank=True, on_delete=models.SET_NULL)
    company = models.ForeignKey('accounting.Company', on_delete=models.PROTECT)
    team = models.ForeignKey('tasker.Team', null=True, blank=True, on_delete=models.PROTECT)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    enabled = models.BooleanField(default=False)
    priority = models.CharField(max_length=10,default='HIGH',choices=(
        ('CRITICAL','Critical'),('HIGH','High'),('MEDIUM','Medium'),('LOW','Low')))
    next_review_on = models.DateField(null=True, blank=True)
    # An explicit owner and contact cadence are required before quote automation.
    contact_interval_days = models.PositiveSmallIntegerField(null=True, blank=True)
    sla_due_on = models.DateField(null=True, blank=True)
    checklist = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    quality_version = models.CharField(max_length=64, blank=True, editable=False)
    quality_accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='+')
    quality_accepted_at = models.DateTimeField(null=True, blank=True)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('kind', 'source_id'), name='operations_source_once')]
        permissions = [('manage_responsibilities', 'Assign operational responsibility in managed companies')]
        ordering = ('next_review_on', 'pk')

    def clean(self):
        from .registry import SOURCES
        spec = SOURCES.get(self.kind)
        if not spec:
            raise ValidationError('Choose a registered operational source.')
        for other in SOURCES.values():
            value = getattr(self, other.field + '_id')
            if value is not None and (other != spec or value != self.source_id):
                raise ValidationError('The canonical source identity must match.')
        if self.source_id < 1:
            raise ValidationError('Choose an existing source record.')
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            if (old.kind, old.source_id, old.company_id) != (self.kind, self.source_id, self.company_id):
                raise ValidationError('Responsibility retains its source and company. Pause it before reviewing a scope correction.')
        if self.team_id and (self.team.company_id != self.company_id or self.team.is_deleted):
            raise ValidationError('Choose a live team in the responsible company.')
        if not isinstance(self.checklist, dict) or any(type(v) is not bool for v in self.checklist.values()):
            raise ValidationError('Checklist entries must be explicit checks.')
        if self.contact_interval_days is not None and not 1 <= self.contact_interval_days <= 365:
            raise ValidationError('Choose a contact interval between 1 and 365 days.')

    def save(self, *args, **kwargs):
        self.full_clean()
        self.revision = uuid.uuid4()
        if kwargs.get('update_fields'):
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'revision', 'updated_at'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_kind_display()} #{self.source_id}'


class ResponsibilityEvent(models.Model):
    responsibility = models.ForeignKey(Responsibility, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    client_key = models.UUIDField()
    action = models.CharField(max_length=24)
    revision = models.UUIDField()
    source_version = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64,blank=True)
    details = models.JSONField(default=dict,blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('actor', 'client_key'), name='operations_request_once')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Operational audit receipts are immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Operational audit receipts are retained.')
