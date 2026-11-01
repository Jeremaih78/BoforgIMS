"""Keep accepted source associations immutable, including reverse M2M operations."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from .pilot_models import PilotObservation


@receiver(m2m_changed,sender=PilotObservation.outcomes.through)
def frozen_pilot_sources(sender,instance,action,reverse,pk_set,**kwargs):
    if action not in ('pre_add','pre_remove','pre_clear'):return
    with transaction.atomic():
        rows=PilotObservation.objects.select_for_update()
        if reverse:
            rows=rows.filter(pk__in=pk_set) if pk_set is not None else rows.filter(outcomes=instance)
        else:rows=rows.filter(pk=instance.pk)
        if any(row.status!='DRAFT' for row in rows):
            raise ValidationError('Accepted pilot sources are retained. Retract and enter a correction.')
