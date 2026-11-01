from django.db import transaction
from django.db.models.signals import post_delete, post_save

from .models import Responsibility
from .registry import SOURCES


def queue(pk, kind):
    def committed():
        from tasker.services.business_links import enqueue_business_source
        enqueue_business_source(kind,pk)
    transaction.on_commit(committed)


def responsibility_saved(sender, instance, **kwargs):
    queue(instance.pk,instance.kind)


post_save.connect(responsibility_saved,sender=Responsibility,dispatch_uid='operations.responsibility')


def native_changed(sender, instance, **kwargs):
    from tasker.services.business_links import enabled
    for kind,spec in SOURCES.items():
        if spec.cls() == sender and enabled(kind):
            for pk in Responsibility.objects.filter(kind=kind,source_id=instance.pk).values_list('pk',flat=True):
                queue(pk,kind)
    if sender._meta.label_lower == 'shop.payment' and enabled('fulfilment_work'):
        for pk in Responsibility.objects.filter(kind='fulfilment_work',source_id=instance.order_id).values_list('pk',flat=True):
            queue(pk,'fulfilment_work')


for spec in SOURCES.values():
    for signal in (post_save,post_delete):
        signal.connect(native_changed,sender=spec.cls(),dispatch_uid=f'operations.{spec.model}.{signal is post_save}')
from shop.models import Payment
post_save.connect(native_changed,sender=Payment,dispatch_uid='operations.payment')
post_delete.connect(native_changed,sender=Payment,dispatch_uid='operations.payment.delete')
