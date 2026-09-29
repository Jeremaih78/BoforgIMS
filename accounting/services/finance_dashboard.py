from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone

from accounting.models import APPayment, ARPayment, Expense, SupplierBill
from sales.models import DocumentLine, Invoice, Payment


ZERO = Decimal("0.00")
VALID_INVOICE_STATUSES = {"PENDING", "CONFIRMED", "PAID", "OVERDUE", "SENT", "UNPAID", "PARTIALLY_PAID"}
POSTED_EXPENSE_FILTER = {"status": Expense.POSTED}
POSTED_BILL_STATUSES = {SupplierBill.POSTED, SupplierBill.PARTIAL, SupplierBill.PAID}


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str = "Custom"


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def current_period(preset: str = "this_month", start: date | None = None, end: date | None = None) -> Period:
    today = timezone.localdate()
    if start and end:
        return Period(start, end, "Custom")
    if preset == "today":
        return Period(today, today, "Today")
    if preset == "yesterday":
        day = today - timedelta(days=1)
        return Period(day, day, "Yesterday")
    if preset == "this_week":
        start_date = today - timedelta(days=today.weekday())
        return Period(start_date, today, "This Week")
    if preset == "last_week":
        this_week = today - timedelta(days=today.weekday())
        start_date = this_week - timedelta(days=7)
        return Period(start_date, start_date + timedelta(days=6), "Last Week")
    if preset == "last_month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        return Period(last_month_end.replace(day=1), last_month_end, "Last Month")
    if preset == "this_year":
        return Period(today.replace(month=1, day=1), today, "This Year")
    if preset == "last_year":
        year = today.year - 1
        return Period(date(year, 1, 1), date(year, 12, 31), "Last Year")
    return Period(today.replace(day=1), today, "This Month")


def previous_period(period: Period) -> Period:
    days = (period.end - period.start).days + 1
    end = period.start - timedelta(days=1)
    return Period(end - timedelta(days=days - 1), end, "Previous Period")


def pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    current = money(current)
    previous = money(previous)
    if previous == ZERO:
        return None if current == ZERO else Decimal("100.00")
    return money(((current - previous) / previous) * Decimal("100"))


def invoice_queryset(period: Period, filters: dict | None = None):
    qs = Invoice.objects.filter(date__gte=period.start, date__lte=period.end, status__in=VALID_INVOICE_STATUSES)
    filters = filters or {}
    if filters.get("customer"):
        qs = qs.filter(customer_id=filters["customer"])
    return qs


def invoice_line_queryset(period: Period, filters: dict | None = None):
    qs = DocumentLine.objects.filter(invoice__in=invoice_queryset(period, filters))
    filters = filters or {}
    if filters.get("product"):
        qs = qs.filter(product_id=filters["product"])
    if filters.get("category"):
        qs = qs.filter(product__category_id=filters["category"])
    return qs


def expense_queryset(period: Period, filters: dict | None = None):
    qs = Expense.objects.filter(date__gte=period.start, date__lte=period.end).filter(
        posted=True
    ) | Expense.objects.filter(date__gte=period.start, date__lte=period.end, **POSTED_EXPENSE_FILTER)
    qs = qs.distinct().select_related("category", "category__default_account")
    filters = filters or {}
    if filters.get("expense_category"):
        qs = qs.filter(category_id=filters["expense_category"])
    if filters.get("account"):
        qs = qs.filter(category__default_account_id=filters["account"])
    if filters.get("payee"):
        qs = qs.filter(payee__icontains=filters["payee"])
    return qs


def supplier_bill_queryset(period: Period, filters: dict | None = None):
    qs = SupplierBill.objects.filter(date__gte=period.start, date__lte=period.end, status__in=POSTED_BILL_STATUSES)
    filters = filters or {}
    if filters.get("supplier"):
        qs = qs.filter(supplier_id=filters["supplier"])
    return qs


def get_total_revenue(period: Period, basis: str = "accrual", filters: dict | None = None) -> Decimal:
    if basis == "cash":
        return get_cash_in(period, filters)
    total = invoice_line_queryset(period, filters).aggregate(total=Coalesce(Sum("line_total"), ZERO))["total"]
    return money(total)


def get_cash_in(period: Period, filters: dict | None = None) -> Decimal:
    filters = filters or {}
    legacy_payments = Payment.objects.filter(date__gte=period.start, date__lte=period.end)
    if filters.get("customer"):
        legacy_payments = legacy_payments.filter(invoice__customer_id=filters["customer"])
    legacy_total = legacy_payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]

    ar_payments = ARPayment.objects.filter(date__gte=period.start, date__lte=period.end)
    if filters.get("customer"):
        ar_payments = ar_payments.filter(customer_id=filters["customer"])
    ar_total = ar_payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return money(legacy_total + ar_total)


def get_total_expenses(period: Period, filters: dict | None = None) -> Decimal:
    expenses = expense_queryset(period, filters).aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    bills = supplier_bill_queryset(period, filters).aggregate(total=Coalesce(Sum("total"), ZERO))["total"]
    return money(expenses + bills)


def get_cash_out(period: Period, filters: dict | None = None) -> Decimal:
    expenses = expense_queryset(period, filters).aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    ap_payments = APPayment.objects.filter(date__gte=period.start, date__lte=period.end)
    filters = filters or {}
    if filters.get("supplier"):
        ap_payments = ap_payments.filter(supplier_id=filters["supplier"])
    ap_total = ap_payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return money(expenses + ap_total)


def get_net_cashflow(period: Period, filters: dict | None = None) -> Decimal:
    return money(get_cash_in(period, filters) - get_cash_out(period, filters))


def get_opening_cash_balance(date_from: date, filters: dict | None = None) -> Decimal:
    earliest = date(2000, 1, 1)
    if date_from <= earliest:
        return ZERO
    return get_net_cashflow(Period(earliest, date_from - timedelta(days=1)), filters)


def get_closing_cash_balance(date_to: date, filters: dict | None = None) -> Decimal:
    return get_opening_cash_balance(date_to + timedelta(days=1), filters)


def get_total_cogs(period: Period, filters: dict | None = None) -> Decimal:
    amount = ExpressionWrapper(
        F("quantity") * Coalesce(F("cost_unit_snapshot"), F("product__avg_cost")),
        output_field=DecimalField(max_digits=18, decimal_places=6),
    )
    total = invoice_line_queryset(period, filters).filter(product__isnull=False).aggregate(total=Coalesce(Sum(amount), ZERO))["total"]
    return money(total)


def get_gross_profit(period: Period, filters: dict | None = None) -> Decimal:
    return money(get_total_revenue(period, "accrual", filters) - get_total_cogs(period, filters))


def get_net_profit(period: Period, filters: dict | None = None) -> Decimal:
    return money(get_gross_profit(period, filters) - get_total_expenses(period, filters))


def margin(part: Decimal, total: Decimal) -> Decimal:
    total = money(total)
    if total == ZERO:
        return ZERO
    return money((money(part) / total) * Decimal("100"))


def _trunc(group_by: str, field: str):
    if group_by == "day":
        return TruncDay(field)
    if group_by == "week":
        return TruncWeek(field)
    if group_by == "year":
        return TruncYear(field)
    return TruncMonth(field)


def revenue_trend(period: Period, group_by: str = "month", filters: dict | None = None):
    qs = invoice_line_queryset(period, filters).annotate(period=_trunc(group_by, "invoice__date"))
    return list(qs.values("period").annotate(total=Coalesce(Sum("line_total"), ZERO)).order_by("period"))


def expense_trend(period: Period, group_by: str = "month", filters: dict | None = None):
    qs = expense_queryset(period, filters).annotate(period=_trunc(group_by, "date"))
    return list(qs.values("period").annotate(total=Coalesce(Sum("amount"), ZERO)).order_by("period"))


def cashflow_trend(period: Period, group_by: str = "month", filters: dict | None = None):
    def key(value):
        return value.date() if hasattr(value, "date") else value

    inflows = {key(row["period"]): money(row["total"]) for row in Payment.objects.filter(date__gte=period.start, date__lte=period.end).annotate(period=_trunc(group_by, "date")).values("period").annotate(total=Coalesce(Sum("amount"), ZERO))}
    outflows = {key(row["period"]): money(row["total"]) for row in expense_queryset(period, filters).annotate(period=_trunc(group_by, "date")).values("period").annotate(total=Coalesce(Sum("amount"), ZERO))}
    keys = sorted(set(inflows) | set(outflows))
    balance = get_opening_cash_balance(period.start, filters)
    rows = []
    for key in keys:
        cash_in = inflows.get(key, ZERO)
        cash_out = outflows.get(key, ZERO)
        balance = money(balance + cash_in - cash_out)
        rows.append({"period": key, "cash_in": cash_in, "cash_out": cash_out, "net": money(cash_in - cash_out), "balance": balance})
    return rows


def expenses_by_category(period: Period, filters: dict | None = None):
    return list(expense_queryset(period, filters).values("category__name").annotate(total=Coalesce(Sum("amount"), ZERO)).order_by("-total"))


def expenses_by_account(period: Period, filters: dict | None = None):
    return list(expense_queryset(period, filters).values("category__default_account__name", "category__default_account__code").annotate(total=Coalesce(Sum("amount"), ZERO)).order_by("-total"))


def expenses_by_payee(period: Period, filters: dict | None = None):
    return list(expense_queryset(period, filters).values("payee").annotate(total=Coalesce(Sum("amount"), ZERO)).order_by("-total")[:10])


def revenue_by_customer(period: Period, filters: dict | None = None):
    return list(invoice_line_queryset(period, filters).values("invoice__customer__name", "invoice__customer_id").annotate(total=Coalesce(Sum("line_total"), ZERO)).order_by("-total")[:10])


def revenue_by_product(period: Period, filters: dict | None = None):
    return list(invoice_line_queryset(period, filters).values("product__name", "product_id").annotate(total=Coalesce(Sum("line_total"), ZERO)).order_by("-total")[:10])


def profitability_by_product(period: Period, filters: dict | None = None):
    amount = ExpressionWrapper(
        F("quantity") * Coalesce(F("cost_unit_snapshot"), F("product__avg_cost")),
        output_field=DecimalField(max_digits=18, decimal_places=6),
    )
    rows = invoice_line_queryset(period, filters).filter(product__isnull=False).values("product__name", "product_id").annotate(
        revenue=Coalesce(Sum("line_total"), ZERO),
        cogs=Coalesce(Sum(amount), ZERO),
    ).order_by("-revenue")
    output = []
    for row in rows:
        gross_profit = money(row["revenue"] - row["cogs"])
        output.append({**row, "gross_profit": gross_profit, "gross_margin": margin(gross_profit, row["revenue"])})
    return output


def top_expenses(period: Period, filters: dict | None = None):
    return list(expense_queryset(period, filters).order_by("-amount")[:10])


def outstanding_debtors() -> Decimal:
    invoiced = Invoice.objects.filter(status__in=VALID_INVOICE_STATUSES).aggregate(total=Coalesce(Sum("lines__line_total"), ZERO))["total"]
    paid = Payment.objects.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return money(invoiced - paid)


def outstanding_creditors() -> Decimal:
    billed = SupplierBill.objects.filter(status__in=POSTED_BILL_STATUSES).aggregate(total=Coalesce(Sum("total"), ZERO))["total"]
    paid = APPayment.objects.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return money(billed - paid)


def dashboard_summary(period: Period, filters: dict | None = None):
    previous = previous_period(period)
    revenue = get_total_revenue(period, "accrual", filters)
    expenses = get_total_expenses(period, filters)
    cogs = get_total_cogs(period, filters)
    gross_profit = money(revenue - cogs)
    net_profit = money(gross_profit - expenses)
    prev_revenue = get_total_revenue(previous, "accrual", filters)
    prev_expenses = get_total_expenses(previous, filters)
    return {
        "period": period,
        "cash_in": get_cash_in(period, filters),
        "cash_out": get_cash_out(period, filters),
        "net_cashflow": get_net_cashflow(period, filters),
        "revenue": revenue,
        "expenses": expenses,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "gross_margin": margin(gross_profit, revenue),
        "net_margin": margin(net_profit, revenue),
        "revenue_change": pct_change(revenue, prev_revenue),
        "expense_change": pct_change(expenses, prev_expenses),
        "outstanding_debtors": outstanding_debtors(),
        "outstanding_creditors": outstanding_creditors(),
    }


def financial_health_score(period: Period, filters: dict | None = None):
    summary = dashboard_summary(period, filters)
    score = 50
    if summary["net_cashflow"] > 0:
        score += 15
    else:
        score -= 15
    if summary["net_profit"] > 0:
        score += 20
    else:
        score -= 20
    if summary["gross_margin"] >= Decimal("30"):
        score += 10
    if summary["expense_change"] and summary["expense_change"] > Decimal("25"):
        score -= 10
    if summary["outstanding_debtors"] > summary["cash_in"]:
        score -= 10
    score = max(0, min(100, score))
    if score >= 80:
        status = "Strong"
    elif score >= 60:
        status = "Stable"
    elif score >= 40:
        status = "Caution"
    else:
        status = "Under Pressure"
    return {"score": score, "status": status}


def generate_financial_insights(period: Period, filters: dict | None = None):
    summary = dashboard_summary(period, filters)
    insights = []
    if summary["net_cashflow"] < 0:
        insights.append({"title": "Cash outflows exceed inflows", "severity": "danger", "description": "Actual cash movement is negative for this period.", "suggested_action": "Review expenses and accelerate collections."})
    if summary["expense_change"] and summary["expense_change"] > Decimal("25"):
        insights.append({"title": "Expenses rising quickly", "severity": "warning", "description": f"Expenses are up {summary['expense_change']}% versus the previous period.", "suggested_action": "Open expense analytics and inspect top categories."})
    if summary["gross_margin"] < Decimal("20") and summary["revenue"] > 0:
        insights.append({"title": "Gross margin is low", "severity": "warning", "description": "Sales are not converting into enough gross profit.", "suggested_action": "Review product pricing and landed costs."})
    top_customer = revenue_by_customer(period, filters)
    if top_customer and summary["revenue"] > 0:
        share = margin(top_customer[0]["total"], summary["revenue"])
        if share >= Decimal("40"):
            insights.append({"title": "Revenue concentration risk", "severity": "warning", "description": f"{top_customer[0]['invoice__customer__name']} contributes {share}% of revenue.", "suggested_action": "Diversify sales and monitor this account closely."})
    if not insights:
        insights.append({"title": "No major finance alerts", "severity": "success", "description": "Revenue, expense, and cashflow indicators are within expected ranges.", "suggested_action": "Keep monitoring collections and margins."})
    return insights


def dashboard_payload(period: Period, group_by: str = "month", filters: dict | None = None):
    summary = dashboard_summary(period, filters)
    return {
        "summary": summary,
        "health": financial_health_score(period, filters),
        "insights": generate_financial_insights(period, filters),
        "cashflow_trend": cashflow_trend(period, group_by, filters),
        "revenue_trend": revenue_trend(period, group_by, filters),
        "expense_trend": expense_trend(period, group_by, filters),
        "expenses_by_category": expenses_by_category(period, filters)[:8],
        "expenses_by_account": expenses_by_account(period, filters)[:8],
        "expenses_by_payee": expenses_by_payee(period, filters),
        "revenue_by_customer": revenue_by_customer(period, filters),
        "revenue_by_product": revenue_by_product(period, filters),
        "profitability_by_product": profitability_by_product(period, filters)[:10],
    }
