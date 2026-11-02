"""Live collections screens; GET requests never refresh cached account records."""
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render,get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from core.query import date_filter
from core.permissions import ims_permission,allowed
from accounting.models import Currency
from customers.models import Customer
from inventory.models import Supplier,Product
from .models import DebtorAccount,CreditorAccount,PromiseToPay,DebtorFollowUp,CreditorFollowUp
from .services.aging import build_debtor_aging,build_creditor_aging
from .services.balances import get_customer_outstanding,get_supplier_outstanding

ZERO=Decimal('0.00')


def controls(request):
    codes=sorted(set(Currency.objects.filter(company=None).values_list('code',flat=True))|set(Product.objects.values_list('currency',flat=True)))
    code=request.GET.get('currency') or getattr(settings,'BASE_CURRENCY_CODE','USD')
    if code not in codes: code=getattr(settings,'BASE_CURRENCY_CODE','USD')
    day=min(date_filter(request.GET.get('as_of'),'balance date') or timezone.localdate(),timezone.localdate())
    return {'currency':code,'currency_codes':codes,'as_of':day,'q':request.GET.get('q','').strip()[:120],'overdue_only':request.GET.get('overdue')=='1','querystring':request.GET.urlencode(),
        'can_receive':allowed(request.user,'sales.add_payment',staff=True),'can_pay':allowed(request.user,'accounting.add_appayment'),
        'can_debtor_followup':allowed(request.user,'credit_control.add_debtorfollowup'),'can_creditor_followup':allowed(request.user,'credit_control.add_creditorfollowup')}


def queue(context,side):
    rows=build_debtor_aging(context['as_of'],context['currency']) if side=='customer' else build_creditor_aging(context['as_of'],context['currency'],company_id=None)
    for row in rows:
        row['party']=row[side]
        row['oldest_days']=max((item.days_overdue for item in row['items']),default=0)
        row['document_count']=len(row['items'])
        row['missing_due']=sum(item.due_date is None for item in row['items'])
        row['over90']=row['buckets']['91_120']+row['buckets']['120_plus']
    return rows


def decorate(rows,side):
    for row in rows:
        days=row['oldest_days']
        row['priority']='Escalate' if days>90 or row.get('broken_promises',0) else 'Follow up' if days>0 else 'On schedule'
        row['suggestion']='Review disputed amount before collection.' if row['disputes'] else 'Review the broken payment promise and agree a new action.' if row.get('broken_promises',0) else 'Contact the customer about the oldest overdue invoice.' if days and side=='customer' else 'Arrange payment for the oldest overdue bill.' if days else 'Confirm a due date.' if row['missing_due'] else 'No overdue balance.'
    return sorted(rows,key=lambda r:(r['oldest_days'],r.get('broken_promises',0),r['total_outstanding']),reverse=True)


@login_required
@ims_permission('credit_control.view_debtoraccount',staff=False)
def dashboard(request):
    context=controls(request)
    debtors=decorate(queue(context,'customer'),'customer')
    creditors=decorate(queue(context,'supplier'),'supplier') if allowed(request.user,'credit_control.view_creditoraccount') else []
    from .source_policies import visible_promises_for,visible_collection_tasks_for
    context.update(debtors=debtors[:6],creditors=creditors[:6],receivable=sum((r['total_outstanding'] for r in debtors),ZERO),receivable_overdue=sum((r['overdue_amount'] for r in debtors),ZERO),payable=sum((r['total_outstanding'] for r in creditors),ZERO),payable_overdue=sum((r['overdue_amount'] for r in creditors),ZERO),debtor_count=len(debtors),creditor_count=len(creditors),
        promises_due=visible_promises_for(request.user).filter(status=PromiseToPay.Status.OPEN,promised_date__lte=context['as_of']).count(),tasks_due=visible_collection_tasks_for(request.user).filter(due_date__lte=context['as_of']).exclude(status__in=['DONE','CANCELLED']).count())
    return render(request,'credit_control/workspace.html',context)


def _list(request,side):
    context=controls(request);rows=decorate(queue(context,side),side)
    if context['q']:
        term=context['q'].lower();rows=[r for r in rows if term in f"{r['party'].name} {getattr(r['party'],'phone','')} {getattr(r['party'],'email','')}".lower() or any(term in item.number.lower() for item in r['items'])]
    if context['overdue_only']: rows=[r for r in rows if r['overdue_amount']>0]
    context.update(side=side,rows=Paginator(rows,25).get_page(request.GET.get('page')))
    return render(request,'credit_control/workspace_queue.html',context)


@login_required
@ims_permission('credit_control.view_debtoraccount',staff=False)
def debtors_list(request):return _list(request,'customer')


@login_required
@ims_permission('credit_control.view_creditoraccount',staff=False)
def creditors_list(request):return _list(request,'supplier')


def _detail(request,side,pk):
    context=controls(request)
    party=get_object_or_404(Customer if side=='customer' else Supplier,pk=pk)
    data=get_customer_outstanding(party,context['as_of'],context['currency']) if side=='customer' else get_supplier_outstanding(party,context['as_of'],context['currency'],company_id=None)
    followups=(DebtorFollowUp.objects.filter(customer=party) if side=='customer' else CreditorFollowUp.objects.filter(supplier=party)).select_related('created_by').order_by('-follow_up_date')[:20]
    context.update(side=side,party=party,outstanding=data,followups=followups)
    return render(request,'credit_control/workspace_party.html',context)


@login_required
@ims_permission('credit_control.view_debtoraccount',staff=False)
def debtor_detail(request,customer_id):return _detail(request,'customer',customer_id)


@login_required
@ims_permission('credit_control.view_creditoraccount',staff=False)
def creditor_detail(request,supplier_id):return _detail(request,'supplier',supplier_id)
