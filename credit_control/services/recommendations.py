from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count

from accounting.models import SupplierBill
from customers.models import Customer
from inventory.models import Product, Supplier
from sales.models import Invoice

from credit_control.models import CreditorAccount, DebtorAccount, PromiseToPay
from .balances import get_customer_outstanding, get_supplier_outstanding


def _risk_bucket(score: int) -> str:
    if score >= 75:
        return "Critical"
    if score >= 50:
        return "High risk"
    if score >= 25:
        return "Watchlist"
    return "Good payer"


def calculate_debtor_risk(customer: Customer) -> dict:
    data = get_customer_outstanding(customer)
    overdue_invoices = sum(1 for item in data["items"] if item.days_overdue > 0)
    broken_promises = PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.BROKEN).count()
    open_promises = PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.OPEN).count()
    disputes = customer.payment_disputes.filter(status__in=["OPEN", "UNDER_REVIEW"]).count()

    score = 0
    reasons = []
    if data["overdue"] > 0:
        score += min(int(data["overdue"] / Decimal("100")), 25)
        reasons.append("Customer has overdue balance")
    if overdue_invoices:
        score += min(overdue_invoices * 8, 20)
        reasons.append(f"{overdue_invoices} overdue invoices")
    if broken_promises:
        score += min(broken_promises * 12, 30)
        reasons.append(f"{broken_promises} broken promise(s)")
    if disputes:
        score += min(disputes * 10, 20)
        reasons.append("Open payment dispute(s)")
    if open_promises:
        score += min(open_promises * 4, 10)

    score = min(score, 100)
    return {
        "score": score,
        "risk_class": _risk_bucket(score),
        "reasons": reasons,
    }


def calculate_creditor_priority(supplier: Supplier) -> dict:
    data = get_supplier_outstanding(supplier)
    overdue_days = max([item.days_overdue for item in data["items"]], default=0)
    overdue_value = data["overdue"]
    upcoming_deliveries = Product.objects.filter(supplier=supplier, reorder_level__gt=0, quantity__lte=3).count()
    account = CreditorAccount.objects.filter(supplier=supplier).first()

    score = 0
    reasons = []
    if overdue_days > 0:
        score += min(overdue_days // 2, 35)
        reasons.append(f"Overdue by {overdue_days} day(s)")
    if overdue_value > 0:
        score += min(int(overdue_value / Decimal("150")), 30)
        reasons.append("Supplier has overdue balance")
    if account and account.is_critical_supplier:
        score += 30
        reasons.append("Critical supplier")
    if upcoming_deliveries:
        score += min(upcoming_deliveries * 5, 20)
        reasons.append("Stock dependency risk")

    urgency = "LOW"
    if score >= 80:
        urgency = "URGENT"
    elif score >= 55:
        urgency = "HIGH"
    elif score >= 30:
        urgency = "MEDIUM"

    return {
        "score": min(score, 100),
        "urgency": urgency,
        "reasons": reasons,
    }


def suggest_debtor_message(customer: Customer, invoice: Invoice | None = None, tone: str = "professional") -> dict:
    risk = calculate_debtor_risk(customer)
    data = get_customer_outstanding(customer)
    amount = data["outstanding"]
    today = date.today()

    if risk["risk_class"] == "Critical":
        recommended_tone = "final_notice"
        action = "Escalate to manager and request immediate part-payment"
    elif risk["risk_class"] == "High risk":
        recommended_tone = "firm"
        action = "Request committed payment date and amount"
    elif data["overdue"] > 0:
        recommended_tone = "professional"
        action = "Send overdue reminder and ask for settlement plan"
    else:
        recommended_tone = tone
        action = "Send friendly due reminder"

    inv_number = invoice.number if invoice else "your outstanding invoices"
    message = (
        f"Hi {customer.name}, this is a reminder regarding {inv_number}. "
        f"Outstanding amount is {amount}. Please advise payment date or part-payment by {today + timedelta(days=2)}."
    )

    return {
        "recommended_action": action,
        "recommended_message": message,
        "recommended_tone": recommended_tone,
        "urgency": "HIGH" if data["overdue"] > 0 else "MEDIUM",
        "reasons": risk["reasons"],
        "best_time_to_follow_up": "09:00-11:00 local time",
    }


def suggest_next_collection_action(customer: Customer) -> dict:
    data = get_customer_outstanding(customer)
    risk = calculate_debtor_risk(customer)
    if data["overdue"] <= 0:
        return {
            "recommended_action": "Send pre-due reminder",
            "recommended_message": "Friendly reminder before due date",
            "urgency": "LOW",
            "reasons": ["No overdue amount"],
        }
    if risk["risk_class"] in {"Critical", "High risk"}:
        return {
            "recommended_action": "Escalate and request part-payment today",
            "recommended_message": "Firm overdue reminder with escalation notice",
            "urgency": "URGENT",
            "reasons": risk["reasons"],
        }
    return {
        "recommended_action": "Call customer and secure promise-to-pay",
        "recommended_message": "Professional reminder and payment date confirmation",
        "urgency": "HIGH",
        "reasons": risk["reasons"] or ["Overdue balance"],
    }


def suggest_creditor_negotiation_message(supplier: Supplier, bill: SupplierBill | None = None) -> dict:
    priority = calculate_creditor_priority(supplier)
    bill_no = bill.doc_no if bill else "outstanding supplier bills"
    message = (
        f"Dear {supplier.name}, we acknowledge {bill_no}. "
        "Please confirm if a short payment extension can be granted while we align this week's cash plan."
    )
    return {
        "recommended_action": "Negotiate payment window",
        "recommended_message": message,
        "urgency": priority["urgency"],
        "reasons": priority["reasons"],
    }


def summarize_customer_payment_behavior(customer: Customer) -> dict:
    risk = calculate_debtor_risk(customer)
    promise_summary = {
        "open": PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.OPEN).count(),
        "broken": PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.BROKEN).count(),
        "kept": PromiseToPay.objects.filter(customer=customer, status=PromiseToPay.Status.KEPT).count(),
    }
    return {
        "recommended_action": "Review debtor pattern",
        "recommended_message": "Risk summary generated",
        "urgency": "HIGH" if risk["risk_class"] in {"High risk", "Critical"} else "MEDIUM",
        "reasons": risk["reasons"],
        "summary": {
            "risk_score": risk["score"],
            "risk_class": risk["risk_class"],
            "promises": promise_summary,
        },
    }


def summarize_supplier_payment_pressure(supplier: Supplier) -> dict:
    priority = calculate_creditor_priority(supplier)
    data = get_supplier_outstanding(supplier)
    return {
        "recommended_action": "Optimize payment sequencing",
        "recommended_message": "Supplier pressure summary generated",
        "urgency": priority["urgency"],
        "reasons": priority["reasons"],
        "summary": {
            "outstanding": data["outstanding"],
            "overdue": data["overdue"],
            "priority_score": priority["score"],
        },
    }


def generate_follow_up_recommendation(customer: Customer, invoice: Invoice | None = None) -> dict:
    return suggest_debtor_message(customer, invoice=invoice)


def generate_creditor_payment_recommendation(supplier: Supplier) -> dict:
    data = get_supplier_outstanding(supplier)
    priority = calculate_creditor_priority(supplier)
    next_step = "Pay immediately" if priority["urgency"] == "URGENT" else "Schedule this week"
    return {
        "recommended_action": next_step,
        "recommended_message": f"Prioritize payment planning for {supplier.name}",
        "urgency": priority["urgency"],
        "reasons": priority["reasons"],
        "amount": data["outstanding"],
    }
