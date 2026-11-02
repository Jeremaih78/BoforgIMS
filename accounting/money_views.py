from datetime import timedelta
from decimal import Decimal
import csv
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from core.permissions import ims_permission,allowed
from core.query import date_filter,id_filter,csv_cell
from .models import JournalEntry,Expense,APPayment,MoneyMovement,Currency,SupplierBill
from .forms import MovementForm,MoneyAccountForm,SupplierBillForm,SupplierPaymentForm,ReceiptForm
from .services.money import record_movement,create_money_account,record_supplier_bill,record_supplier_payment
from .services.workspace import money_workspace


def controls(request):
    end=date_filter(request.GET.get('to'),'end date') or timezone.localdate()
    start=date_filter(request.GET.get('from'),'start date') or end.replace(day=1)
    if start>end: start,end=end,start
    return dict(start=start,end=end,account_id=id_filter(request.GET.get('account'),'account'),currency=request.GET.get('currency',''),query=request.GET.get('q','').strip()[:120],kind=request.GET.get('kind',''))


def permissions(user):
    return {f'can_{action}':allowed(user,perm,staff=action=='receive') for action,perm in [('expense','accounting.add_expense'),('receive','sales.add_payment'),('pay','accounting.add_appayment'),('transfer','accounting.add_moneymovement'),('account','accounting.add_bankaccount'),('bill','accounting.add_supplierbill')]}


@login_required
@ims_permission('accounting.view_journalentry',staff=False)
def home(request):
    filters=controls(request)
    data=money_workspace(**filters)
    page=Paginator(data.pop('entries'),30).get_page(request.GET.get('page'))
    return render(request,'accounting/money_home.html',{**data,**filters,**permissions(request.user),'page':page,'currencies':Currency.objects.filter(company=None).order_by('code'),'querystring':request.GET.urlencode()})


@login_required
@ims_permission('accounting.view_journalentry',staff=False)
def activity_export(request):
    data=money_workspace(**controls(request))
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="money-activity.csv"'
    writer=csv.writer(response);writer.writerow(['Date','Reference','Type','Details','Currency','In','Out'])
    for row in data['entries'].iterator():
        memo=row.memo or ''
        if memo.lstrip().startswith(('=','+','-','@')): memo="'"+memo
        writer.writerow([row.date,row.number,row.get_source_display(),memo,row.currency.code,row.money_in,row.money_out])
    return response


@login_required
@ims_permission('accounting.view_journalentry',staff=False)
def entry_detail(request,pk):
    entry=get_object_or_404(JournalEntry.objects.select_related('currency','created_by'),pk=pk,company=None,is_posted=True)
    expense=Expense.objects.filter(pk=entry.source_id,company=None).first() if entry.source=='EXPENSE' else None
    return render(request,'accounting/money_entry.html',{'entry':entry,'lines':entry.lines.select_related('account'),'expense':expense})


def _capture(request,form_class,title,description,save,initial=None):
    form=form_class(request.POST or None,initial=initial or {})
    if request.method=='POST' and form.is_valid():
        try:
            record=save(form.cleaned_data)
        except (ValidationError,ValueError) as exc:
            form.add_error(None,exc if isinstance(exc,ValidationError) else str(exc))
        else:
            messages.success(request,f'{title} saved.')
            if request.POST.get('save_action')=='another':return redirect(request.path)
            if not allowed(request.user,'accounting.view_journalentry'):
                from sales.models import Payment
                if isinstance(record,Payment):
                    return redirect('ims:sales:invoice_edit',pk=record.invoice_id)
                return redirect(request.path)
            return redirect('ims:accounting:accounting_dashboard')
    submit_label={'Move money':'Save movement','Add money account':'Add account','Record supplier bill':'Save bill','Pay supplier':'Save payment','Receive customer payment':'Save receipt'}[title]
    return render(request,'accounting/money_form.html',{'form':form,'page_title':title,'description':description,'submit_label':submit_label})


@login_required
@ims_permission('accounting.add_moneymovement',staff=False)
def movement(request):
    return _capture(request,MovementForm,'Move money','Transfer between accounts in the same currency, or record owner funding and withdrawals.',lambda data:record_movement(user=request.user,**data),{'kind':'TRANSFER'})


@login_required
@ims_permission('accounting.add_bankaccount',staff=False)
def account_create(request):
    return _capture(request,MoneyAccountForm,'Add money account','Give each bank account, cash drawer or mobile wallet its own balance. Enter an opening balance only when it has been verified.',lambda data:create_money_account(user=request.user,**data))


@login_required
@ims_permission('accounting.add_supplierbill',staff=False)
def bill_create(request):
    return _capture(request,SupplierBillForm,'Record supplier bill','Record an unpaid expense now and pay it later. Amount is the full bill total, including any recoverable tax.',lambda data:record_supplier_bill(user=request.user,**data))


@login_required
@ims_permission('accounting.add_appayment',staff=False)
def supplier_pay(request):
    initial={}
    selected=request.GET.get('bill','')
    if selected.isdigit():
        bill=get_object_or_404(SupplierBill,pk=selected,company=None)
        from credit_control.services.balances import bill_outstanding_balance
        initial={'bill':bill.pk,'amount':bill_outstanding_balance(bill)}
    def save(data):
        bill=data.pop('bill')
        return record_supplier_payment(user=request.user,bill_id=bill.pk,**data)
    return _capture(request,SupplierPaymentForm,'Pay supplier','Choose a bill and the account you paid from. Partial payments reduce only that bill. The bill’s book exchange rate is retained.',save,initial)


@login_required
@ims_permission('sales.add_payment',staff=True)
def receive(request):
    from sales.models import Invoice
    from sales.services.payments import record_payment
    initial={}
    selected=request.GET.get('invoice','')
    if selected.isdigit():
        invoice=get_object_or_404(Invoice,pk=selected)
        from sales.services import StockService
        initial={'invoice':invoice.pk,'amount':max(Decimal('0'),invoice.total-StockService.amount_paid(invoice))}
    def save(data):
        invoice=data.pop('invoice')
        return record_payment(invoice_id=invoice.pk,user=request.user,**data)
    return _capture(request,ReceiptForm,'Receive customer payment','Select an invoice and where the money arrived. Stock, invoice status and the ledger update together.',save,initial)
