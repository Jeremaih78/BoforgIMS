from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
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


@login_required
def dashboard(request):
    for customer in Customer.objects.all():
        sync_debtor_account_summary(customer)
    for supplier in Supplier.objects.all():
        sync_creditor_account_summary(supplier)

    context = collections_dashboard_data()
    return render(request, "credit_control/dashboard.html", context)


@login_required
def debtors_list(request):
    for customer in Customer.objects.all():
        sync_debtor_account_summary(customer)

    qs = DebtorAccount.objects.select_related("customer").all()
    if request.GET.get("risk"):
        qs = qs.filter(risk_level=request.GET.get("risk"))
    if request.GET.get("overdue") == "1":
        qs = qs.filter(overdue_balance__gt=0)
    if request.GET.get("disputed") == "1":
        qs = qs.filter(collection_status=DebtorAccount.CollectionStatus.DISPUTED)
    if request.GET.get("follow_up_due") == "1":
        qs = qs.filter(next_follow_up_date__lte=timezone.now().date())
    if request.GET.get("promise_broken") == "1":
        qs = qs.filter(customer__promises_to_pay__status=PromiseToPay.Status.BROKEN).distinct()

    rows = []
    aging = {row["customer"].id: row for row in build_debtor_aging()}
    for acct in qs:
        age = aging.get(acct.customer_id)
        buckets = age["buckets"] if age else {}
        rows.append({"account": acct, "aging": buckets})

    return render(request, "credit_control/debtors_list.html", {"rows": rows})


@login_required
def debtor_detail(request, customer_id: int):
    customer = get_object_or_404(Customer, id=customer_id)
    account = sync_debtor_account_summary(customer)
    outstanding = get_customer_outstanding(customer)

    recommendation = generate_follow_up_recommendation(customer)
    followups = DebtorFollowUp.objects.filter(customer=customer).select_related("invoice", "created_by")
    promises = PromiseToPay.objects.filter(customer=customer).select_related("invoice")
    disputes = PaymentDispute.objects.filter(customer=customer)

    invoices = Invoice.objects.filter(customer=customer).order_by("-date")
    payments = Payment.objects.filter(invoice__customer=customer).select_related("invoice").order_by("-date")

    timeline = []
    for inv in invoices:
        timeline.append({"when": inv.date, "kind": "INVOICE", "label": inv.number, "amount": inv.total})
    for pay in payments:
        timeline.append({"when": pay.date, "kind": "PAYMENT", "label": pay.invoice.number, "amount": pay.amount})
    for fu in followups[:50]:
        timeline.append({"when": fu.follow_up_date.date(), "kind": "FOLLOWUP", "label": fu.get_follow_up_type_display(), "amount": None})
    for p in promises:
        timeline.append({"when": p.promised_date, "kind": "PROMISE", "label": p.get_status_display(), "amount": p.promised_amount})
    for d in disputes:
        timeline.append({"when": d.opened_date, "kind": "DISPUTE", "label": d.get_dispute_type_display(), "amount": None})
    timeline = sorted(timeline, key=lambda t: t["when"], reverse=True)

    return render(
        request,
        "credit_control/debtor_detail.html",
        {
            "customer": customer,
            "account": account,
            "outstanding": outstanding,
            "invoices": invoices,
            "followups": followups,
            "promises": promises,
            "disputes": disputes,
            "recommendation": recommendation,
            "timeline": timeline[:100],
        },
    )


@login_required
def creditors_list(request):
    for supplier in Supplier.objects.all():
        sync_creditor_account_summary(supplier)

    qs = CreditorAccount.objects.select_related("supplier")
    if request.GET.get("risk"):
        qs = qs.filter(risk_level=request.GET.get("risk"))
    if request.GET.get("overdue") == "1":
        qs = qs.filter(overdue_balance__gt=0)
    if request.GET.get("critical") == "1":
        qs = qs.filter(is_critical_supplier=True)

    aging = {row["supplier"].id: row for row in build_creditor_aging()}
    rows = []
    for acct in qs:
        recommendation = generate_creditor_payment_recommendation(acct.supplier)
        rows.append({
            "account": acct,
            "aging": aging.get(acct.supplier_id, {}).get("buckets", {}),
            "recommendation": recommendation,
        })

    return render(request, "credit_control/creditors_list.html", {"rows": rows})


@login_required
def creditor_detail(request, supplier_id: int):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    account = sync_creditor_account_summary(supplier)
    outstanding = get_supplier_outstanding(supplier)

    recommendation = generate_creditor_payment_recommendation(supplier)
    followups = CreditorFollowUp.objects.filter(supplier=supplier).select_related("bill", "created_by")
    disputes = PaymentDispute.objects.filter(supplier=supplier)
    bills = SupplierBill.objects.filter(supplier=supplier).order_by("-date")
    payments = APPayment.objects.filter(supplier=supplier).order_by("-date")

    stock_dependency = supplier.product_set.filter(reorder_level__gt=0, quantity__lte=3).count()

    timeline = []
    for bill in bills:
        timeline.append({"when": bill.date, "kind": "BILL", "label": bill.doc_no, "amount": bill.total})
    for pay in payments:
        timeline.append({"when": pay.date, "kind": "PAYMENT", "label": pay.payment_no, "amount": pay.amount})
    for fu in followups[:50]:
        timeline.append({"when": fu.follow_up_date.date(), "kind": "FOLLOWUP", "label": fu.get_follow_up_type_display(), "amount": None})
    for d in disputes:
        timeline.append({"when": d.opened_date, "kind": "DISPUTE", "label": d.get_dispute_type_display(), "amount": None})
    timeline = sorted(timeline, key=lambda t: t["when"], reverse=True)

    return render(
        request,
        "credit_control/creditor_detail.html",
        {
            "supplier": supplier,
            "account": account,
            "outstanding": outstanding,
            "bills": bills,
            "followups": followups,
            "disputes": disputes,
            "recommendation": recommendation,
            "stock_dependency": stock_dependency,
            "timeline": timeline[:100],
        },
    )


@login_required
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
def promises(request):
    qs = PromiseToPay.objects.select_related("customer", "invoice").order_by("promised_date")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))
    return render(request, "credit_control/promises.html", {"promises": qs})


@login_required
def promise_create(request):
    form = PromiseToPayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        sync_debtor_account_summary(obj.customer)
        messages.success(request, "Promise to pay saved.")
        return redirect("ims:credit_control:promises")
    return render(request, "credit_control/form_page.html", {"title": "Create Promise to Pay", "form": form})


@login_required
def disputes(request):
    qs = PaymentDispute.objects.select_related("customer", "supplier", "invoice", "bill").order_by("-opened_date")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))
    return render(request, "credit_control/disputes.html", {"disputes": qs})


@login_required
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
def tasks(request):
    if request.GET.get("run_auto") == "1":
        created = auto_create_collection_tasks()
        messages.success(request, f"Auto-task generation complete ({len(created)} created).")
        return redirect("ims:credit_control:tasks")

    qs = CollectionTask.objects.select_related("customer", "supplier", "invoice", "bill", "assigned_to")
    if request.GET.get("today") == "1":
        qs = qs.filter(due_date=timezone.now().date())
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET.get("status"))

    pg = Paginator(qs.order_by("due_date", "-priority"), 50).get_page(request.GET.get("page"))
    return render(request, "credit_control/tasks.html", {"tasks": pg})


@login_required
def task_create(request):
    form = CollectionTaskForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, "Task created.")
        return redirect("ims:credit_control:tasks")
    return render(request, "credit_control/form_page.html", {"title": "Create Task", "form": form})


@login_required
def aging_reports(request):
    context = {
        "debtor_rows": build_debtor_aging(),
        "creditor_rows": build_creditor_aging(),
    }
    return render(request, "credit_control/aging_reports.html", context)


@login_required
def report_export_csv(request, report_name: str):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{report_name}.csv"'
    writer = csv.writer(response)

    if report_name == "ar_aging":
        writer.writerow(["Customer", "Outstanding", "Overdue", "Current", "1-30", "31-60", "61-90", "91-120", "120+"])
        for row in build_debtor_aging():
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
        for row in build_creditor_aging():
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
        for a in DebtorAccount.objects.order_by("-overdue_balance", "-total_outstanding")[:50]:
            writer.writerow([a.customer.name, a.total_outstanding, a.overdue_balance, a.risk_level])
    elif report_name == "debtor_risk":
        writer.writerow(["Customer", "Risk", "Outstanding", "Overdue", "Status"])
        for a in DebtorAccount.objects.order_by("-risk_level", "-overdue_balance"):
            writer.writerow([a.customer.name, a.risk_level, a.total_outstanding, a.overdue_balance, a.collection_status])
    elif report_name == "upcoming_payables":
        writer.writerow(["Supplier", "Bill", "Due Date", "Total", "Status"])
        for b in SupplierBill.objects.filter(due_date__isnull=False).order_by("due_date"):
            writer.writerow([b.supplier.name, b.doc_no, b.due_date, b.total, b.status])
    elif report_name == "critical_supplier_exposure":
        writer.writerow(["Supplier", "Outstanding", "Overdue", "Payment Status"])
        for c in CreditorAccount.objects.filter(is_critical_supplier=True):
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
def report_export_pdf(request, report_name: str):
    context = {
        "report_name": report_name,
        "debtor_rows": build_debtor_aging(),
        "creditor_rows": build_creditor_aging(),
        "promises": PromiseToPay.objects.select_related("customer", "invoice").all(),
        "disputes": PaymentDispute.objects.select_related("customer", "supplier", "invoice", "bill").all(),
        "debtor_accounts": DebtorAccount.objects.select_related("customer").all(),
        "creditor_accounts": CreditorAccount.objects.select_related("supplier").all(),
        "dashboard": collections_dashboard_data(),
    }
    html = render_to_string("credit_control/reports/generic_report_pdf.html", context)
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{report_name}.pdf"'
    return resp


@login_required
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
def statement_customer(request, customer_id: int):
    customer = get_object_or_404(Customer, id=customer_id)
    invoices = Invoice.objects.filter(customer=customer).order_by("date")
    payments = Payment.objects.filter(invoice__customer=customer).order_by("date")
    return render(request, "credit_control/customer_statement.html", {"customer": customer, "invoices": invoices, "payments": payments})


@login_required
def notifications(request):
    qs = Notification.objects.filter(user=request.user).order_by("-created_at")
    if request.GET.get("mark_all") == "1":
        qs.filter(is_read=False).update(is_read=True, read_at=timezone.now())
        messages.success(request, "Notifications marked as read.")
        return redirect("ims:credit_control:notifications")
    return render(request, "credit_control/notifications.html", {"notifications": qs[:200]})
