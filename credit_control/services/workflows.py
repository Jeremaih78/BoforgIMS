from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import APPayment
from customers.models import Customer
from inventory.models import Supplier
from sales.models import Invoice, Payment

from credit_control.models import (
    AuditEvent,
    CollectionTask,
    CreditorAccount,
    DebtorAccount,
    DebtorFollowUp,
    Notification,
    PaymentDispute,
    PromiseToPay,
    ReminderRule,
)
from .aging import build_creditor_aging, build_debtor_aging
from .balances import (
    get_customer_outstanding,
    get_supplier_outstanding,
    invoice_total,
    supplier_total_outstanding,
)
from .recommendations import calculate_creditor_priority, calculate_debtor_risk


ZERO = Decimal("0.00")


def _risk_to_level(score: int) -> str:
    if score >= 75:
        return DebtorAccount.RiskLevel.CRITICAL
    if score >= 50:
        return DebtorAccount.RiskLevel.HIGH
    if score >= 25:
        return DebtorAccount.RiskLevel.MEDIUM
    return DebtorAccount.RiskLevel.LOW


def _creditor_risk_to_level(score: int) -> str:
    if score >= 80:
        return CreditorAccount.RiskLevel.CRITICAL
    if score >= 55:
        return CreditorAccount.RiskLevel.HIGH
    if score >= 30:
        return CreditorAccount.RiskLevel.MEDIUM
    return CreditorAccount.RiskLevel.LOW


def log_audit(event_type: str, obj, action: str, user=None, metadata=None):
    AuditEvent.objects.create(
        company=getattr(obj, "company", None),
        event_type=event_type,
        model_name=obj.__class__.__name__,
        object_id=obj.pk,
        action=action,
        user=user,
        metadata=metadata or {},
    )


def sync_debtor_account_summary(customer: Customer):
    data = get_customer_outstanding(customer)
    acct, _ = DebtorAccount.objects.get_or_create(customer=customer)

    invoices = Invoice.objects.filter(customer=customer)
    total_invoiced = ZERO
    for inv in invoices:
        total_invoiced += invoice_total(inv)
    total_paid = Payment.objects.filter(invoice__customer=customer).aggregate(total=Sum("amount")).get("total") or ZERO
    risk = calculate_debtor_risk(customer)

    collection_status = DebtorAccount.CollectionStatus.CURRENT
    if PaymentDispute.objects.filter(customer=customer, status__in=[PaymentDispute.Status.OPEN, PaymentDispute.Status.UNDER_REVIEW]).exists():
        collection_status = DebtorAccount.CollectionStatus.DISPUTED
    elif PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.OPEN).exists():
        collection_status = DebtorAccount.CollectionStatus.PROMISE_TO_PAY
    elif data["overdue"] > 0:
        collection_status = DebtorAccount.CollectionStatus.OVERDUE

    next_follow_up = DebtorFollowUp.objects.filter(customer=customer, status=DebtorFollowUp.Status.OPEN, next_action_date__isnull=False).order_by("next_action_date").values_list("next_action_date", flat=True).first()
    last_follow_up = DebtorFollowUp.objects.filter(customer=customer).order_by("-follow_up_date").values_list("follow_up_date", flat=True).first()
    last_payment = Payment.objects.filter(invoice__customer=customer).order_by("-date").values_list("date", flat=True).first()

    acct.total_invoiced = total_invoiced
    acct.total_paid = Decimal(total_paid).quantize(Decimal("0.01"))
    acct.total_outstanding = data["outstanding"]
    acct.current_balance = data["outstanding"] - data["overdue"]
    acct.overdue_balance = data["overdue"]
    acct.last_payment_date = last_payment
    acct.last_follow_up_date = last_follow_up.date() if isinstance(last_follow_up, datetime) else last_follow_up
    acct.next_follow_up_date = next_follow_up
    acct.risk_level = _risk_to_level(risk["score"])
    acct.collection_status = collection_status
    acct.save()
    return acct


def sync_creditor_account_summary(supplier: Supplier):
    data = get_supplier_outstanding(supplier)
    acct, _ = CreditorAccount.objects.get_or_create(supplier=supplier)

    total_billed = sum(Decimal(b.total or 0) for b in supplier.supplierbill_set.all())
    total_paid = APPayment.objects.filter(supplier=supplier).aggregate(total=Sum("amount")).get("total") or ZERO
    priority = calculate_creditor_priority(supplier)

    payment_status = CreditorAccount.PaymentStatus.CURRENT
    if PaymentDispute.objects.filter(supplier=supplier, status__in=[PaymentDispute.Status.OPEN, PaymentDispute.Status.UNDER_REVIEW]).exists():
        payment_status = CreditorAccount.PaymentStatus.DISPUTED
    elif data["overdue"] > 0 and acct.is_critical_supplier:
        payment_status = CreditorAccount.PaymentStatus.CRITICAL_SUPPLIER
    elif data["overdue"] > 0:
        payment_status = CreditorAccount.PaymentStatus.OVERDUE
    elif data["outstanding"] > 0:
        payment_status = CreditorAccount.PaymentStatus.DUE_SOON

    last_follow_up = supplier.creditor_follow_ups.order_by("-follow_up_date").values_list("follow_up_date", flat=True).first()
    next_follow_up = supplier.creditor_follow_ups.filter(status="OPEN", next_action_date__isnull=False).order_by("next_action_date").values_list("next_action_date", flat=True).first()
    last_payment = APPayment.objects.filter(supplier=supplier).order_by("-date").values_list("date", flat=True).first()

    acct.total_billed = total_billed
    acct.total_paid = Decimal(total_paid).quantize(Decimal("0.01"))
    acct.total_outstanding = data["outstanding"]
    acct.current_balance = data["outstanding"] - data["overdue"]
    acct.overdue_balance = data["overdue"]
    acct.last_payment_date = last_payment
    acct.last_follow_up_date = last_follow_up.date() if isinstance(last_follow_up, datetime) else last_follow_up
    acct.next_follow_up_date = next_follow_up
    acct.risk_level = _creditor_risk_to_level(priority["score"])
    acct.payment_status = payment_status
    acct.save()
    return acct


def _ensure_task(**kwargs):
    defaults = {
        "priority": kwargs.pop("priority", CollectionTask.Priority.MEDIUM),
        "status": CollectionTask.Status.PENDING,
        "notes": kwargs.pop("notes", ""),
        "company": kwargs.pop("company", None),
    }
    task, created = CollectionTask.objects.get_or_create(
        task_type=kwargs["task_type"],
        customer=kwargs.get("customer"),
        supplier=kwargs.get("supplier"),
        invoice=kwargs.get("invoice"),
        bill=kwargs.get("bill"),
        due_date=kwargs["due_date"],
        defaults=defaults,
    )
    return task, created


def _default_debtor_rules() -> list[tuple[int, str, str]]:
    return [
        (-3, CollectionTask.TaskType.SEND_REMINDER, CollectionTask.Priority.LOW),
        (0, CollectionTask.TaskType.SEND_REMINDER, CollectionTask.Priority.MEDIUM),
        (3, CollectionTask.TaskType.FOLLOW_UP_DEBTOR, CollectionTask.Priority.HIGH),
        (7, CollectionTask.TaskType.FOLLOW_UP_DEBTOR, CollectionTask.Priority.HIGH),
        (14, CollectionTask.TaskType.ESCALATION_REVIEW, CollectionTask.Priority.URGENT),
        (30, CollectionTask.TaskType.ESCALATION_REVIEW, CollectionTask.Priority.URGENT),
    ]


def _default_creditor_rules() -> list[tuple[int, str, str]]:
    return [
        (-3, CollectionTask.TaskType.SEND_REMINDER, CollectionTask.Priority.MEDIUM),
        (0, CollectionTask.TaskType.FOLLOW_UP_CREDITOR, CollectionTask.Priority.MEDIUM),
        (7, CollectionTask.TaskType.FOLLOW_UP_CREDITOR, CollectionTask.Priority.HIGH),
    ]


def _rules_for(audience: str):
    custom = ReminderRule.objects.filter(audience=audience, is_active=True).order_by("offset_days")
    if custom.exists():
        return [(r.offset_days, r.task_type, r.priority) for r in custom]
    if audience == ReminderRule.Audience.DEBTOR:
        return _default_debtor_rules()
    return _default_creditor_rules()


def auto_create_collection_tasks(as_of_date: date | None = None):
    today = as_of_date or timezone.now().date()
    created_ids = []

    for invoice in Invoice.objects.all().select_related("customer"):
        if not invoice.due_date:
            continue
        outstanding = get_customer_outstanding(invoice.customer, as_of_date=today)
        inv_item = next((i for i in outstanding["items"] if i.obj_id == invoice.id), None)
        if not inv_item or inv_item.outstanding <= ZERO:
            continue
        for offset, task_type, priority in _rules_for(ReminderRule.Audience.DEBTOR):
            target_date = invoice.due_date + timedelta(days=offset)
            if target_date != today:
                continue
            task, created = _ensure_task(
                task_type=task_type,
                customer=invoice.customer,
                invoice=invoice,
                due_date=today,
                priority=priority,
                notes=f"Auto-created for invoice {invoice.number} at offset {offset} day(s).",
            )
            if created:
                created_ids.append(task.id)

    for supplier in Supplier.objects.all():
        supplier_data = get_supplier_outstanding(supplier, as_of_date=today)
        for item in supplier_data["items"]:
            bill = supplier.supplierbill_set.filter(id=item.obj_id).first()
            if not bill or not bill.due_date:
                continue
            for offset, task_type, priority in _rules_for(ReminderRule.Audience.CREDITOR):
                target_date = bill.due_date + timedelta(days=offset)
                if target_date != today:
                    continue
                acct = CreditorAccount.objects.filter(supplier=supplier).first()
                pr = priority
                if acct and acct.is_critical_supplier and offset >= 0:
                    pr = CollectionTask.Priority.URGENT
                task, created = _ensure_task(
                    task_type=task_type,
                    supplier=supplier,
                    bill=bill,
                    due_date=today,
                    priority=pr,
                    notes=f"Auto-created for bill {bill.doc_no} at offset {offset} day(s).",
                )
                if created:
                    created_ids.append(task.id)

    for promise in PromiseToPay.objects.filter(status=PromiseToPay.Status.OPEN, promised_date=today):
        task, created = _ensure_task(
            task_type=CollectionTask.TaskType.CHECK_PROMISE,
            customer=promise.customer,
            invoice=promise.invoice,
            due_date=today,
            priority=CollectionTask.Priority.HIGH,
            notes=f"Promise due today: {promise.promised_amount}",
        )
        if created:
            created_ids.append(task.id)

    return created_ids


def monitor_promises(as_of_date: date | None = None):
    today = as_of_date or timezone.now().date()
    broken = []
    due_today = []
    likely_broken = []

    for promise in PromiseToPay.objects.filter(status=PromiseToPay.Status.OPEN):
        if promise.promised_date == today:
            due_today.append(promise)
        if promise.promised_date < today and promise.actual_paid_amount < promise.promised_amount:
            promise.status = PromiseToPay.Status.BROKEN
            promise.save(update_fields=["status", "updated_at"])
            broken.append(promise)
        elif promise.promised_date <= today + timedelta(days=1):
            past_broken = PromiseToPay.objects.filter(customer=promise.customer, status=PromiseToPay.Status.BROKEN).count()
            if past_broken >= 2:
                likely_broken.append(promise)

    return {
        "due_today": due_today,
        "broken": broken,
        "likely_broken": likely_broken,
    }


def refresh_credit_control_snapshots():
    for customer in Customer.objects.all():
        sync_debtor_account_summary(customer)
    for supplier in Supplier.objects.all():
        sync_creditor_account_summary(supplier)
    return {
        "debtors": DebtorAccount.objects.count(),
        "creditors": CreditorAccount.objects.count(),
    }


def collections_dashboard_data(as_of_date: date | None = None) -> dict:
    today = as_of_date or timezone.now().date()
    debtor_rows = build_debtor_aging(today)
    creditor_rows = build_creditor_aging(today)

    total_receivables = sum((row["total_outstanding"] for row in debtor_rows), ZERO)
    overdue_receivables = sum((row["overdue_amount"] for row in debtor_rows), ZERO)
    total_payables = sum((row["total_outstanding"] for row in creditor_rows), ZERO)
    overdue_payables = sum((row["overdue_amount"] for row in creditor_rows), ZERO)

    promised_this_week = PromiseToPay.objects.filter(
        promised_date__gte=today,
        promised_date__lte=today + timedelta(days=7),
    ).aggregate(total=Sum("promised_amount")).get("total") or ZERO

    broken_promises_month = PromiseToPay.objects.filter(
        status=PromiseToPay.Status.BROKEN,
        updated_at__year=today.year,
        updated_at__month=today.month,
    ).count()

    top_debtors = sorted(debtor_rows, key=lambda r: (r["overdue_amount"], r["total_outstanding"]), reverse=True)[:10]
    top_creditors = sorted(creditor_rows, key=lambda r: (r["overdue_amount"], r["total_outstanding"]), reverse=True)[:10]

    urgent_tasks = CollectionTask.objects.filter(due_date=today, status__in=[CollectionTask.Status.PENDING, CollectionTask.Status.IN_PROGRESS]).order_by("-priority", "id")[:20]

    return {
        "date": today,
        "total_receivables": total_receivables,
        "overdue_receivables": overdue_receivables,
        "total_payables": total_payables,
        "overdue_payables": overdue_payables,
        "net_cash_pressure": overdue_payables - overdue_receivables,
        "amount_promised_this_week": promised_this_week,
        "broken_promises_this_month": broken_promises_month,
        "top_debtors": top_debtors,
        "top_creditors": top_creditors,
        "urgent_tasks": urgent_tasks,
        "debtor_aging_rows": debtor_rows,
        "creditor_aging_rows": creditor_rows,
    }


def send_in_app_notification(title: str, message: str, users=None, level=Notification.Level.INFO, company=None):
    user_qs = users or get_user_model().objects.filter(is_active=True)
    rows = []
    for user in user_qs:
        rows.append(Notification(user=user, title=title, message=message, level=level, company=company))
    Notification.objects.bulk_create(rows)


def push_alerts(as_of_date: date | None = None):
    today = as_of_date or timezone.now().date()
    monitor = monitor_promises(today)
    if monitor["broken"]:
        send_in_app_notification(
            title="Broken promise detected",
            message=f"{len(monitor['broken'])} promise(s) turned broken on {today}.",
            level=Notification.Level.DANGER,
        )
    high_risk = DebtorAccount.objects.filter(risk_level__in=[DebtorAccount.RiskLevel.HIGH, DebtorAccount.RiskLevel.CRITICAL]).count()
    if high_risk:
        send_in_app_notification(
            title="High-risk debtors",
            message=f"{high_risk} debtor account(s) are currently high risk.",
            level=Notification.Level.WARNING,
        )

    critical_overdue = CreditorAccount.objects.filter(
        is_critical_supplier=True,
        overdue_balance__gt=0,
    ).count()
    if critical_overdue:
        send_in_app_notification(
            title="Critical supplier overdue",
            message=f"{critical_overdue} critical supplier(s) are overdue.",
            level=Notification.Level.DANGER,
        )

    stale_disputes = PaymentDispute.objects.filter(
        status__in=[PaymentDispute.Status.OPEN, PaymentDispute.Status.UNDER_REVIEW],
        opened_date__lte=today - timedelta(days=14),
    ).count()
    if stale_disputes:
        send_in_app_notification(
            title="Disputes unresolved",
            message=f"{stale_disputes} dispute(s) have been unresolved for over 14 days.",
            level=Notification.Level.WARNING,
        )
