"""Current source facts plus explicit operational responsibility; never duplicate Tasks."""
from django.core.exceptions import PermissionDenied
from django.db.models import Case, CharField, DateField, F, IntegerField, Q, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, Least, Substr
from django_hosts.resolvers import reverse as host_reverse

from operations.models import Responsibility
from operations.policies import visible
from operations.registry import IDENTIFIERS, SOURCES, expressions, facts, source_label, spec_for
from .collections import source_id

SOURCE_APP = 'operations'
SOURCE_TYPES = {kind:Responsibility for kind in SOURCES}
ACTION_KEYS = {kind:'review_source' for kind in SOURCES}


def source_type(value):
    spec_for(value)
    return value


def readable_sources(user, kind):
    return visible(user,source_type(kind))


def with_state(rows, kind):
    """SQL status matches snapshot; raw source changes are checked before pagination."""
    spec = spec_for(kind)
    ex = expressions(kind)
    rows = rows.annotate(_native=ex['native_state'],_native_version=ex['native_version'])
    whens = [When(**{spec.field+'__isnull':True},then=Value('UNAVAILABLE')),
        When(enabled=False,then=Value('UNAVAILABLE')),When(company__is_active=False,then=Value('UNAVAILABLE'))]
    if kind == 'fulfilment_work':
        whens.append(When(~Q(order__payment__status='paid'),then=Value('UNAVAILABLE')))
    if kind == 'quotation_work':
        whens.append(When(Q(assigned_to__isnull=True)|Q(next_review_on__isnull=True)|Q(contact_interval_days__isnull=True),then=Value('UNAVAILABLE')))
    if kind == 'service_work':
        whens.append(When(_native='CLOSED',quality_version=F('_native_version'),quality_accepted_by__isnull=False,then=Value('COMPLETED')))
    if spec.done:
        if kind == 'stocktake_work':
            whens.append(When(_native='CLOSED',stocktake__approved_by__isnull=True,then=Value('UNAVAILABLE')))
        whens.append(When(_native__in=spec.done,then=Value('COMPLETED')))
    whens.append(When(~Q(_native__in=spec.active),then=Value('CANCELLED')))
    whens.append(When(Q(assigned_to__isnull=True)|Q(assigned_to__is_active=False),then=Value('UNASSIGNED')))
    if kind == 'service_work':
        whens.append(When(_native__in=('REPAIR','CLOSED'),then=Value('IN_PROGRESS')))
    return rows.annotate(_state=Case(*whens,default=Value('OPEN'),output_field=CharField()))


def actionable_sources(user, kind):
    return with_state(readable_sources(user,kind),kind).filter(_state__in=('OPEN','IN_PROGRESS','UNASSIGNED'))


def load_source(kind, pk, *, lock=False):
    source_type(kind); source_id(pk)
    spec = spec_for(kind)
    if lock:
        native_id = Responsibility.objects.filter(pk=pk,kind=kind).values_list('source_id',flat=True).first()
        if native_id:
            list(spec.cls().objects.select_for_update().filter(pk=native_id))
    rows = Responsibility.objects.filter(kind=kind)
    if lock:
        rows = rows.select_for_update(of=('self',))
    return rows.select_related('company','assigned_to','team',spec.field).filter(pk=pk).first()


def sql_fields(kind):
    spec = spec_for(kind)
    due_args = [F('next_review_on'), F('sla_due_on')]
    if spec.date_field:
        due_args.append(F(spec.field+'__'+spec.date_field))
    due = Least(*due_args,output_field=DateField())
    return {'due':due,'rank':Case(When(priority='CRITICAL',then=0),When(priority='HIGH',then=1),
            When(priority='MEDIUM',then=2),default=3,output_field=IntegerField()),
        'source_version':expressions(kind)['version'],'company':F('company_id'),'owner':F('assigned_to_id'),
        'updated':F('updated_at'),'title':Concat(Value(spec.title+' · '),
            Substr(Coalesce(F(spec.field+'__'+IDENTIFIERS[kind]),Cast('source_id',CharField())),1,120)),
        'priority':F('priority'),'status':expressions(kind)['native_state'],'state':F('_state')}


def snapshot(kind, pk, *, instance=None):
    spec = spec_for(kind); source_id(pk)
    row = instance if instance is not None else load_source(kind,pk)
    data = dict(source_app=SOURCE_APP,source_type=kind,source_record_id=pk,action_key=ACTION_KEYS[kind],
        owner_id=None,company_id=None,title=spec.title,status='UNAVAILABLE',due_date=None,priority='MEDIUM',
        source_status='',source_version='',source_updated_at=None,reason_code='source_missing')
    if row is None:
        return data
    current = facts(row)
    dates = [row.next_review_on,row.sla_due_on]
    native = getattr(row,spec.field)
    if native and spec.date_field:
        dates.append(getattr(native,spec.date_field))
    due = min((date for date in dates if date),default=None)
    data.update(owner_id=row.assigned_to_id,company_id=row.company_id,title=f'{spec.title} · {source_label(kind,native)}',
        status=current['status'],due_date=due.isoformat() if due else None,
        priority=row.priority,source_status=current['state'],source_version=current['version'],
        source_updated_at=row.updated_at.isoformat(),reason_code=current['reason'])
    return data


def source_url(*, user, kind, pk):
    if not readable_sources(user,kind).filter(pk=source_id(pk)).exists():
        raise PermissionDenied
    return host_reverse('ims:operations:detail',kwargs={'pk':pk},host='ims')
