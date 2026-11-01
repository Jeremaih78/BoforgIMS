"""Allowlisted source facts. No record bodies, customer contacts or financial amounts are projected."""
from dataclasses import dataclass

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Cast, Coalesce, Concat


@dataclass(frozen=True)
class Source:
    model: str
    field: str
    permission: str
    work_permission: str
    title: str
    status_field: str
    active: tuple
    done: tuple
    date_field: str = ''
    native_route: str = ''

    def cls(self):
        return apps.get_model(self.model)


SOURCES = {
    'shipment_work': Source('inventory.Shipment', 'shipment', 'inventory.view_shipment', 'inventory.change_shipment',
        'Shipment', 'status', ('CREATED','IN_TRANSIT','ARRIVED','CLEARED','RECEIVED'), ('CLOSED',), 'eta_date', 'ims:inventory:shipment_detail'),
    'service_work': Source('inventory.ServiceCase', 'service_case', 'inventory.manage_unit_lifecycle', 'inventory.manage_unit_lifecycle',
        'Repair and handoff', 'status', ('OPEN','REPAIR','CLOSED'), (), '', 'ims:inventory:case_detail'),
    'stocktake_work': Source('inventory.Stocktake', 'stocktake', 'inventory.view_stocktake', 'inventory.approve_stocktake',
        'Approve stocktake', 'status', ('REVIEW',), ('CLOSED',), '', 'ims:inventory:stocktake_detail'),
    'quotation_work': Source('sales.Quotation', 'quotation', 'sales.view_quotation', 'sales.change_quotation',
        'Follow up quotation', 'status', ('SENT',), ('CONVERTED',), '', 'ims:sales:quotation_edit'),
    'fulfilment_work': Source('shop.Order', 'order', 'shop.view_order', 'shop.change_order',
        'Fulfil paid order', 'status', ('paid',), ('shipped',)),
    'bill_work': Source('accounting.SupplierBill', 'bill', 'accounting.view_supplierbill', 'accounting.change_supplierbill',
        'Review supplier bill', 'status', ('DRAFT','POSTED','PARTIAL'), ('PAID',), 'due_date'),
    'period_work': Source('accounting.FiscalPeriod', 'period', 'accounting.view_fiscalperiod', 'accounting.change_fiscalperiod',
        'Fiscal close', 'is_closed', ('False',), ('True',), 'end_date'),
}

CHECKLISTS = {
    'period_work': ('Reconcile bank balances', 'Review receivables and payables', 'Review stock valuation', 'Review draft journals'),
    'service_work': ('Inspection recorded', 'Resolution checked', 'Customer handoff arranged'),
}

IDENTIFIERS = {'shipment_work':'shipment_code','service_work':'unit__unit_id','stocktake_work':'name',
    'quotation_work':'number','fulfilment_work':'number','bill_work':'doc_no','period_work':'name'}


def source_label(kind,source):
    if source is None:
        return 'Source unavailable'
    value=source
    for part in IDENTIFIERS[kind].split('__'):
        value=getattr(value,part,None)
        if value is None:
            break
    return str(value or source.pk)[:120]


def spec_for(kind):
    try:
        return SOURCES[kind]
    except (KeyError, TypeError):
        raise ValidationError('Choose a registered operational source.')


def expressions(kind):
    """One SQL/Python fact path, including raw bulk changes that bypass signals."""
    from tasker.business_adapters.replenishment import StockSHA256
    spec = spec_for(kind)
    prefix = spec.field + '__'
    state = Cast(prefix + spec.status_field, CharField())
    if kind == 'period_work':
        state = Case(When(period__is_closed=True, then=Value('True')), default=Value('False'), output_field=CharField())
    date = Cast(prefix + spec.date_field, CharField()) if spec.date_field else Value('')
    proof = Value('')
    if kind == 'fulfilment_work':
        proof = Concat(Coalesce(F('order__payment__status'), Value('missing')),Value('|'),
            Cast('order__updated_at',CharField()),Value('|'),Cast('order__total',CharField()),Value('|'),
            Coalesce(Cast('order__status_changed_at',CharField()),Value('')))
    elif kind == 'service_work':
        proof = Concat(Coalesce(Cast('service_case__inspection', CharField()), Value('')), Value('|'),
            Coalesce(Cast('service_case__resolution', CharField()), Value('')), Value('|'),
            Coalesce(Cast('service_case__replacement_id', CharField()), Value('')), Value('|'),
            Coalesce(Cast('service_case__technician_id', CharField()), Value('')), Value('|'),
            Coalesce(Cast('service_case__closed_at', CharField()), Value('')),Value('|'),
            Cast('service_case__unit_id',CharField()))
    elif kind == 'stocktake_work':
        proof = Concat(Coalesce(Cast('stocktake__approved_by_id', CharField()), Value('')),Value('|'),
            Coalesce(Cast('stocktake__closed_at',CharField()),Value('')))
    elif kind == 'bill_work':
        proof = Concat(Cast('bill__company_id',CharField()),Value('|'),Cast('bill__date',CharField()),
            Value('|'),Cast('bill__total',CharField()),Value('|'),Cast('bill__currency_id',CharField()),Value('|'),
            Coalesce(Cast('bill__status_changed_at',CharField()),Value('')))
    elif kind == 'period_work':
        proof = Concat(Cast('period__company_id',CharField()),Value('|'),Cast('period__start_date',CharField()),
            Value('|'),Coalesce(Cast('period__locked_at',CharField()),Value('')))
    elif kind == 'shipment_work':
        proof = Concat(Cast('shipment__updated_at',CharField()),Value('|'),
            Coalesce(Cast('shipment__closed_at',CharField()),Value('')))
    elif kind == 'quotation_work':
        proof = Concat(Cast('quotation__date',CharField()),Value('|'),Cast('quotation__customer_id',CharField()))
    native = StockSHA256(Concat(Coalesce(state, Value('missing')), Value('|'), Coalesce(date,Value('')), Value('|'), proof))
    version = StockSHA256(Concat(Cast('revision', CharField()), Value('|'), native))
    return {'native_state': state, 'native_version': native, 'version': version}


def facts(row):
    from .models import Responsibility
    spec = spec_for(row.kind)
    current = Responsibility.objects.filter(pk=row.pk).annotate(**{
        '_'+key: value for key,value in expressions(row.kind).items()}).values(
        '_native_state','_native_version','_version').first()
    if not current:
        return {'status':'UNAVAILABLE','reason':'source_missing','native_version':'','version':'','state':''}
    state = current['_native_state']
    source = getattr(row, spec.field)
    reason, status = '', 'OPEN'
    if source is None:
        status, reason = 'UNAVAILABLE', 'source_missing'
    elif getattr(source, 'company_id', row.company_id) != row.company_id:
        status, reason = 'UNAVAILABLE', 'source_company_changed'
    elif not row.enabled or not row.company.is_active:
        status, reason = 'UNAVAILABLE', 'source_paused'
    elif row.kind == 'fulfilment_work' and getattr(getattr(source, 'payment', None), 'status', None) != 'paid':
        status, reason = 'UNAVAILABLE', 'payment_not_verified'
    elif row.kind == 'quotation_work' and not (row.assigned_to_id and row.next_review_on and row.contact_interval_days):
        status, reason = 'UNAVAILABLE', 'contact_policy_needed'
    elif row.kind == 'service_work' and state == 'CLOSED':
        if row.quality_version == current['_native_version'] and row.quality_accepted_by_id:
            status = 'COMPLETED'
        else:
            status, reason = 'IN_PROGRESS', 'quality_acceptance_needed'
    elif state in spec.done:
        # Source approvals are evidence, never the checklist's checkboxes.
        if row.kind == 'stocktake_work' and not source.approved_by_id:
            status, reason = 'UNAVAILABLE', 'approval_evidence_needed'
        else:
            status = 'COMPLETED'
    elif state not in spec.active:
        status, reason = 'CANCELLED', 'source_not_actionable'
    elif row.kind == 'service_work' and state == 'REPAIR':
        status = 'IN_PROGRESS'
    if status in ('OPEN','IN_PROGRESS') and (not row.assigned_to_id or not row.assigned_to.is_active):
        status, reason = 'UNASSIGNED', 'responsible_owner_needed'
    return dict(status=status, reason=reason, native_version=current['_native_version'], version=current['_version'], state=state or '')
