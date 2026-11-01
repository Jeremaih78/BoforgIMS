"""Inventory replenishment review metadata; stock and purchases stay at source."""
import hashlib

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import BigIntegerField, Case, CharField, F, Func, IntegerField, Value, When
from django.db.models.functions import Cast, Concat, Substr
from django_hosts.resolvers import reverse as host_reverse

from inventory.models import Product, ReplenishmentPlan
from inventory.replenishment_policies import visible_plans_for
from inventory.services.replenishment import effective_plan_state, product_stock_version
from .collections import digest, source_id

SOURCE_APP = 'inventory'
SOURCE_TYPES = {'replenishment_plan': ReplenishmentPlan}
ACTION_KEYS = {'replenishment_plan': 'replenish_stock'}


class StockSHA256(Func):
    # PostgreSQL's built-in bytea hash avoids an undeclared pgcrypto extension.
    template = "encode(sha256(convert_to(%(expressions)s, 'UTF8')), 'hex')"
    output_field = CharField()


def source_type(value):
    if value != 'replenishment_plan':
        raise ValidationError('Choose a registered replenishment source.')
    return value


def readable_sources(user, kind):
    source_type(kind)
    return visible_plans_for(user)


def actionable_sources(user, kind):
    return readable_sources(user, kind).filter(is_enabled=True, product__is_active=True,
        product__track_inventory=True, product__quantity__gte=0, product__reserved__gte=0,
        product__reserved__lte=F('product__quantity'), product__reorder_level__gte=0,
        product__quantity__lte=Cast('product__reserved',BigIntegerField())+Cast('product__reorder_level',BigIntegerField()))


def load_source(kind, pk, *, lock=False):
    source_type(kind); source_id(pk)
    rows = ReplenishmentPlan.objects
    if lock:
        product_id = rows.filter(pk=pk).values_list('product_id', flat=True).first()
        if product_id is not None:
            list(Product.objects.select_for_update().filter(pk=product_id))
        rows = rows.select_for_update(of=('self',))
    return rows.select_related('product', 'company', 'assigned_to').filter(pk=pk).first()


def sql_fields():
    """Expressions evaluated on the source Plan table, including raw stock."""
    raw = StockSHA256(Concat(Cast('product__quantity', CharField()), Value('|'),
        Cast('product__reserved', CharField()), Value('|'), Cast('product__reorder_level', CharField()),
        Value('|'), Case(When(product__is_active=True, then=Value('1')), default=Value('0')),
        Value('|'), Case(When(product__track_inventory=True, then=Value('1')), default=Value('0'))))
    version = StockSHA256(Concat(Cast('revision', CharField()), Value('|'), raw))
    fresh = Case(When(observed_need_state='LOW', then=F('review_state')),
        default=Value('NEEDS_REVIEW'), output_field=CharField())
    return {'due': F('next_review_on'), 'rank': Case(
            When(product__quantity=F('product__reserved'), then=0), default=1, output_field=IntegerField()),
        'source_version': version, 'company': F('company_id'), 'owner': F('assigned_to_id'),
        'updated': F('updated_at'), 'title': Concat(Value('Replenish '), Substr('product__name', 1, 145)),
        'priority': Case(When(product__quantity=F('product__reserved'), then=Value('CRITICAL')),
            default=Value('HIGH'), output_field=CharField()),
        'status': Concat(Value('LOW:'), fresh),
        'state': Case(When(observed_need_state='LOW', review_state__in=(
            'REVIEWING','WAITING_DECISION','AWAITING_STOCK'), then=Value('IN_PROGRESS')),
            default=Value('OPEN'), output_field=CharField())}


def snapshot(kind, pk, *, instance=None):
    source_type(kind); source_id(pk)
    plan = instance if instance is not None else load_source(kind, pk)
    facts = {'source_app':SOURCE_APP, 'source_type':kind, 'source_record_id':pk,
        'action_key':ACTION_KEYS[kind], 'title':'Review stock replenishment', 'owner_id':None,
        'company_id':None,'status':'UNAVAILABLE','due_date':None,'priority':'MEDIUM',
        'source_status':'','source_version':'','source_updated_at':None,'reason_code':'source_missing'}
    if plan is None:
        return facts
    state = effective_plan_state(plan)
    need = state['need_state']
    progress = state['review_state']
    status, reason = 'UNAVAILABLE', 'source_review_needed'
    if not plan.is_enabled:
        reason = 'source_paused'
    elif not plan.company.is_active:
        reason = 'company_inactive'
    elif need == 'CLEAR':
        status, reason = 'COMPLETED', 'need_cleared'
    elif need == 'LOW':
        status, reason = ('OPEN' if progress == 'NEEDS_REVIEW' else 'IN_PROGRESS'), ''
        if not plan.assigned_to_id or not plan.assigned_to.is_active:
            status, reason = 'UNASSIGNED', 'responsible_owner_needed'
    elif need == 'INVALID':
        reason = 'stock_review_needed'
    elif need == 'INACTIVE':
        reason = 'product_inactive_or_untracked'
    version = hashlib.sha256(f'{plan.revision}|{product_stock_version(plan.product)}'.encode()).hexdigest()
    facts.update(owner_id=plan.assigned_to_id,company_id=plan.company_id,status=status,
        title='Replenish '+plan.product.name[:145],
        due_date=plan.next_review_on.isoformat() if plan.next_review_on else None,
        priority=('CRITICAL' if state['available'] == 0 else 'HIGH') if need == 'LOW' else 'MEDIUM',
        source_status='LOW:'+progress if need == 'LOW' else need,source_version=version,
        source_updated_at=plan.updated_at.isoformat(),reason_code=reason)
    return facts


def source_url(*, user, kind, pk):
    if not readable_sources(user, kind).filter(pk=source_id(pk)).exists():
        raise PermissionDenied
    return host_reverse('ims:inventory:replenishment_detail', kwargs={'pk':pk}, host='ims')
