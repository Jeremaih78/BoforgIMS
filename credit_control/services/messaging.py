from __future__ import annotations

from datetime import date

from credit_control.models import DebtorFollowUp, FollowUpTemplate


def render_template(template: FollowUpTemplate, context: dict) -> tuple[str, str]:
    subject = (template.subject or "").format(**context)
    body = (template.body or "").format(**context)
    return subject, body


def send_whatsapp_reminder(*, customer, invoice=None, tone: str = "PROFESSIONAL", created_by=None) -> DebtorFollowUp:
    template = (
        FollowUpTemplate.objects.filter(
            audience=FollowUpTemplate.Audience.DEBTOR,
            channel=FollowUpTemplate.Channel.WHATSAPP,
            tone=tone,
            is_active=True,
        )
        .order_by("id")
        .first()
    )

    context = {
        "customer_name": customer.name,
        "invoice_number": invoice.number if invoice else "N/A",
        "amount": getattr(invoice, "total", 0),
        "due_date": invoice.due_date if invoice and invoice.due_date else date.today(),
    }

    if template:
        subject, message = render_template(template, context)
    else:
        subject = "Payment Reminder"
        message = (
            "Hi {customer_name}, reminder for invoice {invoice_number} amount {amount}. "
            "Due date {due_date}. Kindly confirm payment date."
        ).format(**context)

    follow_up = DebtorFollowUp.objects.create(
        customer=customer,
        invoice=invoice,
        follow_up_type=DebtorFollowUp.FollowUpType.WHATSAPP,
        direction=DebtorFollowUp.Direction.OUTBOUND,
        subject=subject,
        message_summary=message[:250],
        full_message=message,
        outcome=DebtorFollowUp.Outcome.RESPONDED,
        status=DebtorFollowUp.Status.COMPLETED,
        created_by=created_by,
    )

    # Stub transport integration point.
    follow_up.message_summary = f"[WHATSAPP-STUB] {follow_up.message_summary}"
    follow_up.save(update_fields=["message_summary", "updated_at"])
    return follow_up
