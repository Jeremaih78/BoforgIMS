from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from core.query import csv_cell
from core.permissions import ims_permission, allowed
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounting.models import APPayment, SupplierBill
from customers.models import Customer
from inventory.models import Supplier
from sales.models import Invoice, Payment
from sales.pdf_utils import render_pdf_from_html

from .forms import CollectionTaskForm, CreditorFollowUpForm, DebtorFollowUpForm, PaymentDisputeForm, PromiseToPayForm
from .models import (
    CollectionTask,
    CreditorAccount,
    CreditorFollowUp,
    DebtorAccount,
    DebtorFollowUp,
    Notification,
    PaymentDispute,
    PromiseToPay,
)
from .services import (
    auto_create_collection_tasks,
    build_creditor_aging,
    build_debtor_aging,
    collections_dashboard_data,
    generate_creditor_payment_recommendation,
    generate_follow_up_recommendation,
    get_customer_outstanding,
    get_supplier_outstanding,
    monitor_promises,
    sync_creditor_account_summary,
    sync_debtor_account_summary,
)
from .services.messaging import send_whatsapp_reminder
from .workspace_views import dashboard,debtors_list,creditors_list,debtor_detail,creditor_detail












@login_required
@ims_permission('credit_control.view_debtorfollowup', staff=False)
def followups(request):
    debtor_followups = DebtorFollowUp.objects.select_related("customer", "invoice").order_by("-follow_up_date")
    creditor_followups = CreditorFollowUp.objects.select_related("supplier", "bill").order_by("-follow_up_date")
    return render(
        request,
        "credit_control/followups.html",
        {
            "debtor_followups": debtor_followups[:100],
            "creditor_followups": creditor_followups[:100],
        },
    )


@login_required
@ims_permission('credit_control.add_debtorfollowup', staff=False)
@transaction.atomic
def add_debtor_followup(request, customer_id: int | None = None):
    initial = {}
    if customer_id:
        customer = get_object_or_404(Customer, id=customer_id)
        initial["customer"] = customer
        initial["debtor_account"] = DebtorAccount.objects.filter(customer=customer).first()

    form = DebtorFollowUpForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        if obj.promised_amount and obj.promised_payment_date:
            PromiseToPay.objects.create(
                customer=obj.customer,
                invoice=obj.invoice,
                follow_up=obj,
                promised_amount=obj.promised_amount,
                promised_date=obj.promised_payment_date,
                created_by=request.user,
            )
        sync_debtor_account_summary(obj.customer)
        messages.success(request, "Debtor follow-up logged.")
        return redirect("ims:credit_control:debtor_detail", customer_id=obj.customer_id)
    return render(request, "credit_control/form_page.html", {"title": "Log Debtor Follow-up", "form": form})


@login_required
@ims_permission('credit_control.add_creditorfollowup', staff=False)
def add_creditor_followup(request, supplier_id: int | None = None):
    initial = {}
    if supplier_id:
        supplier = get_object_or_404(Supplier, id=supplier_id)
        initial["supplier"] = supplier
        initial["creditor_account"] = CreditorAccount.objects.filter(supplier=supplier).first()

    form = CreditorFollowUpForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        sync_creditor_account_summary(obj.supplier)
        messages.success(request, "Creditor follow-up logged.")
        return redirect("ims:credit_control:creditor_detail", supplier_id=obj.supplier_id)
    return render(request, "credit_control/form_page.html", {"title": "Log Creditor Follow-up", "form": form})


@login_required
@ims_permission('credit_control.view_promisetopay', staff=False)
def promises(request):
    from .source_policies import visible_promises_for
    qs = visible_promises_for(request.user).select_related("customer", "invoice").order_by("promised_date")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))
    return render(request, "credit_control/promises.html", {"promises": qs})


@login_required
@ims_permission('credit_control.add_promisetopay', staff=False)
def promise_create(request):
    form = PromiseToPayForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        sync_debtor_account_summary(obj.customer)
        messages.success(request, "Promise to pay saved.")
        return redirect("ims:credit_control:promises")
    return render(request, "credit_control/form_page.html", {"title": "Create Promise to Pay", "form": form})


@login_required
@ims_permission('credit_control.view_paymentdispute', staff=False)
def disputes(request):
    qs = PaymentDispute.objects.select_related("customer", "supplier", "invoice", "bill").order_by("-opened_date")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))
    return render(request, "credit_control/disputes.html", {"disputes": qs})


@login_required
@ims_permission('credit_control.add_paymentdispute', staff=False)
def dispute_create(request):
    form = PaymentDisputeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        if obj.customer:
            sync_debtor_account_summary(obj.customer)
        if obj.supplier:
            sync_creditor_account_summary(obj.supplier)
        messages.success(request, "Dispute recorded.")
        return redirect("ims:credit_control:disputes")
    return render(request, "credit_control/form_page.html", {"title": "Open Dispute", "form": form})


@login_required
@ims_permission('credit_control.view_collectiontask', staff=False, write_permission='credit_control.change_collectiontask')
def tasks(request):
    from .source_policies import operational_admin, visible_collection_tasks_for
    if request.method == "POST" and request.POST.get("run_auto") == "1":
        if not operational_admin(request.user):
            raise PermissionDenied
        created = auto_create_collection_tasks()
        messages.success(request, f"Auto-task generation complete ({len(created)} created).")
        return redirect("ims:credit_control:tasks")

    qs = visible_collection_tasks_for(request.user).select_related("customer", "supplier", "invoice", "bill", "assigned_to")
    if request.GET.get("today") == "1":
        qs = qs.filter(due_date=timezone.now().date())
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))

    pg = Paginator(qs.order_by("due_date", "-priority"), 50).get_page(request.GET.get("page"))
    return render(request, "credit_control/tasks.html", {"tasks": pg, "can_auto_create": operational_admin(request.user)})


@login_required
@ims_permission('credit_control.add_collectiontask', staff=False)
def task_create(request):
    form = CollectionTaskForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, "Task created.")
        return redirect("ims:credit_control:tasks")
    return render(request, "credit_control/form_page.html", {"title": "Create Task", "form": form})


@login_required
@ims_permission('credit_control.view_debtoraccount', staff=False)
def aging_reports(request):
    from .workspace_views import controls
    context = {**controls(request),
        "debtor_rows": build_debtor_aging(**_aging_filters(request)),
        "creditor_rows": build_creditor_aging(**_aging_filters(request),company_id=None) if allowed(request.user,"credit_control.view_creditoraccount") else [],
    }
    return render(request, "credit_control/aging_reports.html", context)


@login_required
@ims_permission('credit_control.view_debtoraccount', staff=False)
def report_export_csv(request, report_name: str):
    _report_access(request,report_name)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{report_name}.csv"'
    raw_writer=csv.writer(response)
    class SafeWriter:
        def writerow(self,row):raw_writer.writerow([csv_cell(cell) for cell in row])
    writer=SafeWriter()

    if report_name == "ar_aging":
        writer.writerow(["Customer", "Outstanding", "Overdue", "Current", "1-30", "31-60", "61-90", "91-120", "120+"])
        for row in build_debtor_aging(**_aging_filters(request)):
            b = row["buckets"]
            writer.writerow([
                row["customer"].name,
                row["total_outstanding"],
                row["overdue_amount"],
                b["current"],
                b["1_30"],
                b["31_60"],
                b["61_90"],
                b["91_120"],
                b["120_plus"],
            ])
    elif report_name == "ap_aging":
        writer.writerow(["Supplier", "Outstanding", "Overdue", "Current", "1-30", "31-60", "61-90", "91-120", "120+"])
        for row in build_creditor_aging(**_aging_filters(request),company_id=None):
            b = row["buckets"]
            writer.writerow([
                row["supplier"].name,
                row["total_outstanding"],
                row["overdue_amount"],
                b["current"],
                b["1_30"],
                b["31_60"],
                b["61_90"],
                b["91_120"],
                b["120_plus"],
            ])
    elif report_name == "promises":
        writer.writerow(["Customer", "Invoice", "Promised Amount", "Promised Date", "Status", "Actual Paid"])
        for p in PromiseToPay.objects.select_related("customer", "invoice"):
            writer.writerow([p.customer.name, p.invoice.number if p.invoice else "", p.promised_amount, p.promised_date, p.status, p.actual_paid_amount])
    elif report_name == "broken_promises":
        writer.writerow(["Customer", "Invoice", "Promised Amount", "Promised Date", "Status"])
        for p in PromiseToPay.objects.filter(status=PromiseToPay.Status.BROKEN).select_related("customer", "invoice"):
            writer.writerow([p.customer.name, p.invoice.number if p.invoice else "", p.promised_amount, p.promised_date, p.status])
    elif report_name == "disputed_invoices":
        writer.writerow(["Type", "Customer", "Supplier", "Invoice", "Bill", "Status", "Opened"])
        for d in PaymentDispute.objects.select_related("customer", "supplier", "invoice", "bill"):
            writer.writerow([
                d.dispute_type,
                d.customer.name if d.customer else "",
                d.supplier.name if d.supplier else "",
                d.invoice.number if d.invoice else "",
                d.bill.doc_no if d.bill else "",
                d.status,
                d.opened_date,
            ])
    elif report_name == "top_debtors":
        writer.writerow(["Customer", "Outstanding", "Overdue", "Risk"])
        for a in sorted(_live_accounts(request),key=lambda a:(a.overdue_balance,a.total_outstanding),reverse=True)[:50]:
            writer.writerow([a.customer.name, a.total_outstanding, a.overdue_balance, a.risk_level])
    elif report_name == "debtor_risk":
        writer.writerow(["Customer", "Risk", "Outstanding", "Overdue", "Status"])
        for a in sorted(_live_accounts(request),key=lambda a:(a.risk_level,a.overdue_balance),reverse=True):
            writer.writerow([a.customer.name, a.risk_level, a.total_outstanding, a.overdue_balance, a.collection_status])
    elif report_name == "upcoming_payables":
        writer.writerow(["Supplier", "Bill", "Due Date", "Total", "Status"])
        for b in SupplierBill.objects.filter(due_date__isnull=False).order_by("due_date"):
            writer.writerow([b.supplier.name, b.doc_no, b.due_date, b.total, b.status])
    elif report_name == "critical_supplier_exposure":
        writer.writerow(["Supplier", "Outstanding", "Overdue", "Payment Status"])
        for c in (a for a in _live_accounts(request,creditor=True) if a.is_critical_supplier):
            writer.writerow([c.supplier.name, c.total_outstanding, c.overdue_balance, c.payment_status])
    elif report_name == "creditor_negotiation":
        writer.writerow(["Supplier", "Follow-up Date", "Type", "Outcome", "Next Action Date"])
        for f in CreditorFollowUp.objects.select_related("supplier"):
            writer.writerow([f.supplier.name, f.follow_up_date, f.follow_up_type, f.outcome, f.next_action_date])
    elif report_name == "collections_activity":
        writer.writerow(["Customer", "Follow-up Date", "Type", "Outcome", "Status"])
        for f in DebtorFollowUp.objects.select_related("customer"):
            writer.writerow([f.customer.name, f.follow_up_date, f.follow_up_type, f.outcome, f.status])
    elif report_name == "cash_control":
        data = collections_dashboard_data()
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Expected Collections", data["total_receivables"]])
        writer.writerow(["Planned Payments", data["total_payables"]])
        writer.writerow(["Overdue Receivables", data["overdue_receivables"]])
        writer.writerow(["Overdue Payables", data["overdue_payables"]])
        writer.writerow(["Net Cash Pressure", data["net_cash_pressure"]])
    elif report_name == "net_due_by_week":
        data = collections_dashboard_data()
        writer.writerow(["Week", "Expected Collection", "Planned Payment", "Net"])
        for i in range(4):
            wk = timezone.now().date() + timedelta(days=i * 7)
            expected = PromiseToPay.objects.filter(promised_date__gte=wk, promised_date__lt=wk + timedelta(days=7)).aggregate(total=Sum("promised_amount"))["total"] or Decimal("0")
            planned = CollectionTask.objects.filter(due_date__gte=wk, due_date__lt=wk + timedelta(days=7), task_type=CollectionTask.TaskType.FOLLOW_UP_CREDITOR).count()
            writer.writerow([f"Week {i + 1}", expected, planned, expected - Decimal(planned)])
    else:
        writer.writerow(["Report not implemented", report_name])

    return response


@login_required
@ims_permission('credit_control.view_debtoraccount', staff=False)
def report_export_pdf(request, report_name: str):
    _report_access(request,report_name)
    from .workspace_views import controls
    context = {**controls(request),
        "report_name": report_name,
        "debtor_rows": build_debtor_aging(**_aging_filters(request)),
        "creditor_rows": build_creditor_aging(**_aging_filters(request),company_id=None) if allowed(request.user,"credit_control.view_creditoraccount") else [],
        "promises": PromiseToPay.objects.select_related("customer", "invoice").all(),
        "disputes": PaymentDispute.objects.select_related("customer", "supplier", "invoice", "bill").all(),
        "debtor_accounts": _live_accounts(request),
        "creditor_accounts": _live_accounts(request,creditor=True) if allowed(request.user,"credit_control.view_creditoraccount") else [],
        "dashboard": collections_dashboard_data(),
    }
    html = render_to_string("credit_control/reports/generic_report_pdf.html", context)
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{report_name}.pdf"'
    return resp


@login_required
@ims_permission('credit_control.add_debtorfollowup', staff=False)
@require_POST
def send_debtor_whatsapp(request, customer_id: int, invoice_id: int | None = None):
    customer = get_object_or_404(Customer, id=customer_id)
    invoice = None
    if invoice_id:
        invoice = get_object_or_404(Invoice, id=invoice_id, customer=customer)
    send_whatsapp_reminder(customer=customer, invoice=invoice, created_by=request.user)
    sync_debtor_account_summary(customer)
    messages.success(request, "WhatsApp reminder queued (stub) and logged as follow-up.")
    return redirect("ims:credit_control:debtor_detail", customer_id=customer.id)


@login_required
@ims_permission('credit_control.view_debtoraccount', staff=False)
def statement_customer(request, customer_id: int):
    customer = get_object_or_404(Customer, id=customer_id)
    invoices = Invoice.objects.filter(customer=customer).order_by("date")
    payments = Payment.objects.filter(invoice__customer=customer).order_by("date")
    return render(request, "credit_control/customer_statement.html", {"customer": customer, "invoices": invoices, "payments": payments})


@login_required
@ims_permission('credit_control.view_notification', staff=False, write_permission='credit_control.change_notification')
def notifications(request):
    qs = Notification.objects.filter(user=request.user).order_by("-created_at")
    if request.method == "POST" and request.POST.get("mark_all") == "1":
        qs.filter(is_read=False).update(is_read=True, read_at=timezone.now())
        messages.success(request, "Notifications marked as read.")
        return redirect("ims:credit_control:notifications")
    return render(request, "credit_control/notifications.html", {"notifications": qs[:200]})


def _aging_filters(request):
    from .workspace_views import controls
    data=controls(request)
    return {"as_of_date":data["as_of"],"currency":data["currency"]}


def _report_access(request,report_name):
    if report_name in {'ap_aging','upcoming_payables','critical_supplier_exposure','creditor_negotiation'} and not allowed(request.user,'credit_control.view_creditoraccount'):
        raise PermissionDenied


def _live_accounts(request,creditor=False):
    from .services.balances import customer_balance_map,supplier_balance_map
    filters=_aging_filters(request)
    data=supplier_balance_map(filters['as_of_date'],filters['currency'],company_id=None) if creditor else customer_balance_map(filters['as_of_date'],filters['currency'])
    model=CreditorAccount if creditor else DebtorAccount
    party='supplier' if creditor else 'customer'
    existing={getattr(item,party+'_id'):item for item in model.objects.filter(**{party+'_id__in':list(data)}).select_related(party)}
    result=[]
    for key,row in data.items():
        account=existing.get(key) or model(**{party:row[party]})
        account.total_outstanding=row['outstanding'];account.overdue_balance=row['overdue'];account.current_balance=row['outstanding']-row['overdue']
        result.append(account)
    return result
