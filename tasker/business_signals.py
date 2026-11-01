"""Content-free source events; reconciliation stays in the shared outbox."""
from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from credit_control.models import CollectionTask, PromiseToPay
from inventory.models import Product, ReplenishmentPlan


def _enqueue(kind, pk):
    if not getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False):
        return
    from tasker.services.business_links import enqueue_business_source
    enqueue_business_source(source_type=kind, source_id=pk)


@receiver(pre_save, sender=CollectionTask)
def remember_promise_binding(sender, instance, raw=False, **kwargs):
    if not raw and instance.pk and getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False):
        instance._business_previous_promise_id = sender.objects.filter(pk=instance.pk).values_list("promise_to_pay_id", flat=True).first()


@receiver(post_save, sender=CollectionTask)
def collection_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _enqueue("collection_task", instance.pk)
    for pk in {getattr(instance, "promise_to_pay_id", None), getattr(instance, "_business_previous_promise_id", None)} - {None}:
        _enqueue("promise_to_pay", pk)


@receiver(post_delete, sender=CollectionTask)
def collection_deleted(sender, instance, **kwargs):
    _enqueue("collection_task", instance.pk)
    if instance.promise_to_pay_id:
        _enqueue("promise_to_pay", instance.promise_to_pay_id)


@receiver(post_save, sender=PromiseToPay)
def promise_changed(sender, instance, raw=False, **kwargs):
    if raw or not getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False):
        return
    _enqueue("promise_to_pay", instance.pk)
    for pk in CollectionTask.objects.filter(promise_to_pay=instance).values_list("pk", flat=True)[:100]:
        _enqueue("collection_task", pk)


@receiver(pre_delete, sender=PromiseToPay)
def remember_promise_tasks(sender, instance, **kwargs):
    if getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False):
        instance._business_collection_ids = list(CollectionTask.objects.filter(promise_to_pay=instance).values_list("pk", flat=True)[:100])


@receiver(post_delete, sender=PromiseToPay)
def promise_deleted(sender, instance, **kwargs):
    _enqueue("promise_to_pay", instance.pk)
    for pk in getattr(instance, "_business_collection_ids", ()):
        _enqueue("collection_task", pk)


def _enqueue_replenishment(pk):
    if getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False):
        from tasker.services.business_links import enqueue_business_source
        enqueue_business_source("replenishment_plan", pk)


@receiver(post_save, sender=ReplenishmentPlan)
def replenishment_changed(sender, instance, raw=False, **kwargs):
    if not raw and not getattr(instance, "_replenishment_syncing", False):
        _enqueue_replenishment(instance.pk)


@receiver(post_delete, sender=ReplenishmentPlan)
def replenishment_deleted(sender, instance, **kwargs):
    _enqueue_replenishment(instance.pk)


@receiver(post_save, sender=Product)
def replenishment_product_changed(sender, instance, raw=False, **kwargs):
    if raw or not getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False):
        return
    pk = ReplenishmentPlan.objects.filter(product_id=instance.pk).values_list("pk", flat=True).first()
    if pk is not None:
        _enqueue_replenishment(pk)
