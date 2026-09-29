from __future__ import annotations

from core.storage import private_document_storage

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DebtorAccount(TimeStampedModel):
    class RiskLevel(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class CollectionStatus(models.TextChoices):
        CURRENT = "CURRENT", "Current"
        OVERDUE = "OVERDUE", "Overdue"
        PROMISE_TO_PAY = "PROMISE_TO_PAY", "Promise To Pay"
        DISPUTED = "DISPUTED", "Disputed"
        ESCALATED = "ESCALATED", "Escalated"
        LEGAL_HOLD = "LEGAL_HOLD", "Legal Hold"
        WRITTEN_OFF = "WRITTEN_OFF", "Written Off"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    customer = models.OneToOneField("customers.Customer", on_delete=models.CASCADE, related_name="debtor_account")
    total_invoiced = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_paid = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_outstanding = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    current_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    overdue_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit_limit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=30)
    last_payment_date = models.DateField(null=True, blank=True)
    last_follow_up_date = models.DateField(null=True, blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.LOW)
    collection_status = models.CharField(
        max_length=20,
        choices=CollectionStatus.choices,
        default=CollectionStatus.CURRENT,
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["customer__name"]
        permissions = [
            ("credit_control_finance_manager", "Can manage all credit control settings"),
            ("credit_control_accountant", "Can process debtor and creditor actions"),
            ("credit_control_collections_officer", "Can manage collection follow-ups"),
            ("credit_control_sales", "Can view debtor portfolio"),
            ("credit_control_procurement", "Can view creditor portfolio"),
            ("credit_control_viewer", "Can view credit control dashboards"),
        ]

    def __str__(self):
        return f"{self.customer} debtor account"


class CreditorAccount(TimeStampedModel):
    class RiskLevel(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class PaymentStatus(models.TextChoices):
        CURRENT = "CURRENT", "Current"
        DUE_SOON = "DUE_SOON", "Due Soon"
        OVERDUE = "OVERDUE", "Overdue"
        NEGOTIATED = "NEGOTIATED", "Negotiated"
        DISPUTED = "DISPUTED", "Disputed"
        CRITICAL_SUPPLIER = "CRITICAL_SUPPLIER", "Critical Supplier"
        ON_HOLD = "ON_HOLD", "On Hold"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    supplier = models.OneToOneField("inventory.Supplier", on_delete=models.CASCADE, related_name="creditor_account")
    total_billed = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_paid = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total_outstanding = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    current_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    overdue_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    payment_terms_days = models.PositiveIntegerField(default=30)
    last_payment_date = models.DateField(null=True, blank=True)
    last_follow_up_date = models.DateField(null=True, blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.LOW)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.CURRENT)
    notes = models.TextField(blank=True)
    is_critical_supplier = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["supplier__name"]

    def __str__(self):
        return f"{self.supplier} creditor account"


class DebtorFollowUp(TimeStampedModel):
    class FollowUpType(models.TextChoices):
        CALL = "CALL", "Call"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"
        VISIT = "VISIT", "Visit"
        INTERNAL_NOTE = "INTERNAL_NOTE", "Internal Note"
        PAYMENT_REMINDER = "PAYMENT_REMINDER", "Payment Reminder"
        FINAL_NOTICE = "FINAL_NOTICE", "Final Notice"

    class Direction(models.TextChoices):
        OUTBOUND = "OUTBOUND", "Outbound"
        INBOUND = "INBOUND", "Inbound"
        INTERNAL = "INTERNAL", "Internal"

    class Outcome(models.TextChoices):
        NO_RESPONSE = "NO_RESPONSE", "No Response"
        RESPONDED = "RESPONDED", "Responded"
        PROMISED_TO_PAY = "PROMISED_TO_PAY", "Promised To Pay"
        PARTIAL_PAYMENT_MADE = "PARTIAL_PAYMENT_MADE", "Partial Payment Made"
        DISPUTED = "DISPUTED", "Disputed"
        WRONG_CONTACT = "WRONG_CONTACT", "Wrong Contact"
        ESCALATE = "ESCALATE", "Escalate"
        RESOLVED = "RESOLVED", "Resolved"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="debtor_follow_ups")
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="debtor_follow_ups")
    debtor_account = models.ForeignKey(DebtorAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_ups")
    follow_up_date = models.DateTimeField(default=timezone.now)
    follow_up_type = models.CharField(max_length=20, choices=FollowUpType.choices)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.OUTBOUND)
    subject = models.CharField(max_length=255)
    message_summary = models.TextField(blank=True)
    full_message = models.TextField(blank=True, null=True)
    outcome = models.CharField(max_length=30, choices=Outcome.choices, default=Outcome.NO_RESPONSE)
    next_action_date = models.DateField(null=True, blank=True)
    next_action_type = models.CharField(max_length=20, blank=True, null=True)
    promised_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    promised_payment_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="debtor_followups_created")
    attachment = models.FileField(storage=private_document_storage, upload_to="credit_control/followups/debtors/", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-follow_up_date"]

    def __str__(self):
        return f"{self.customer} {self.get_follow_up_type_display()}"


class CreditorFollowUp(TimeStampedModel):
    class FollowUpType(models.TextChoices):
        CALL = "CALL", "Call"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        EMAIL = "EMAIL", "Email"
        VISIT = "VISIT", "Visit"
        PAYMENT_NEGOTIATION = "PAYMENT_NEGOTIATION", "Payment Negotiation"
        INTERNAL_NOTE = "INTERNAL_NOTE", "Internal Note"

    class Outcome(models.TextChoices):
        PAYMENT_AGREED = "PAYMENT_AGREED", "Payment Agreed"
        EXTENSION_GRANTED = "EXTENSION_GRANTED", "Extension Granted"
        DISPUTED = "DISPUTED", "Disputed"
        WAITING_RESPONSE = "WAITING_RESPONSE", "Waiting Response"
        ESCALATE = "ESCALATE", "Escalate"
        RESOLVED = "RESOLVED", "Resolved"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.CASCADE, related_name="creditor_follow_ups")
    bill = models.ForeignKey("accounting.SupplierBill", on_delete=models.SET_NULL, null=True, blank=True, related_name="creditor_follow_ups")
    creditor_account = models.ForeignKey(CreditorAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_ups")
    follow_up_date = models.DateTimeField(default=timezone.now)
    follow_up_type = models.CharField(max_length=30, choices=FollowUpType.choices)
    subject = models.CharField(max_length=255)
    message_summary = models.TextField(blank=True)
    full_message = models.TextField(blank=True)
    outcome = models.CharField(max_length=30, choices=Outcome.choices, default=Outcome.WAITING_RESPONSE)
    next_action_date = models.DateField(null=True, blank=True)
    promised_payment_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    promised_payment_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="creditor_followups_created")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-follow_up_date"]

    def __str__(self):
        return f"{self.supplier} {self.get_follow_up_type_display()}"


class PromiseToPay(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        KEPT = "KEPT", "Kept"
        BROKEN = "BROKEN", "Broken"
        PARTIALLY_KEPT = "PARTIALLY_KEPT", "Partially Kept"
        CANCELLED = "CANCELLED", "Cancelled"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="promises_to_pay")
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="promises_to_pay")
    follow_up = models.ForeignKey(DebtorFollowUp, on_delete=models.SET_NULL, null=True, blank=True, related_name="promises")
    promised_amount = models.DecimalField(max_digits=18, decimal_places=2)
    promised_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    actual_paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    actual_paid_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="promises_created")

    class Meta:
        ordering = ["promised_date", "-id"]

    def __str__(self):
        return f"{self.customer} {self.promised_amount} on {self.promised_date}"


class PaymentDispute(TimeStampedModel):
    class DisputeType(models.TextChoices):
        PRICE_DISPUTE = "PRICE_DISPUTE", "Price Dispute"
        PRODUCT_ISSUE = "PRODUCT_ISSUE", "Product Issue"
        DELIVERY_DELAY = "DELIVERY_DELAY", "Delivery Delay"
        MISSING_DOCUMENT = "MISSING_DOCUMENT", "Missing Document"
        WRONG_AMOUNT = "WRONG_AMOUNT", "Wrong Amount"
        CUSTOMER_CASHFLOW = "CUSTOMER_CASHFLOW", "Customer Cashflow"
        SUPPLIER_ISSUE = "SUPPLIER_ISSUE", "Supplier Issue"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    dispute_type = models.CharField(max_length=20, choices=DisputeType.choices)
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_disputes")
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_disputes")
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_disputes")
    bill = models.ForeignKey("accounting.SupplierBill", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_disputes")
    opened_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    description = models.TextField()
    resolution_notes = models.TextField(blank=True)
    resolved_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_disputes_owned")

    class Meta:
        ordering = ["-opened_date", "-id"]

    def __str__(self):
        target = self.customer or self.supplier
        return f"{self.get_dispute_type_display()} - {target}"


class FollowUpTemplate(TimeStampedModel):
    class Audience(models.TextChoices):
        DEBTOR = "DEBTOR", "Debtor"
        CREDITOR = "CREDITOR", "Creditor"

    class Channel(models.TextChoices):
        WHATSAPP = "WHATSAPP", "WhatsApp"
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"

    class Tone(models.TextChoices):
        FRIENDLY = "FRIENDLY", "Friendly"
        PROFESSIONAL = "PROFESSIONAL", "Professional"
        FIRM = "FIRM", "Firm"
        FINAL_NOTICE = "FINAL_NOTICE", "Final Notice"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=120)
    audience = models.CharField(max_length=10, choices=Audience.choices)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    tone = models.CharField(max_length=20, choices=Tone.choices, default=Tone.PROFESSIONAL)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "name", "audience", "channel", "tone")]
        ordering = ["audience", "channel", "name"]

    def __str__(self):
        return self.name


class CollectionTask(TimeStampedModel):
    class TaskType(models.TextChoices):
        FOLLOW_UP_DEBTOR = "FOLLOW_UP_DEBTOR", "Follow Up Debtor"
        FOLLOW_UP_CREDITOR = "FOLLOW_UP_CREDITOR", "Follow Up Creditor"
        CHECK_PROMISE = "CHECK_PROMISE", "Check Promise"
        ESCALATION_REVIEW = "ESCALATION_REVIEW", "Escalation Review"
        SEND_REMINDER = "SEND_REMINDER", "Send Reminder"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        DONE = "DONE", "Done"
        CANCELLED = "CANCELLED", "Cancelled"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks")
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks")
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks")
    bill = models.ForeignKey("accounting.SupplierBill", on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks_assigned")
    due_date = models.DateField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="collection_tasks_created")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "-priority", "id"]

    def __str__(self):
        target = self.customer or self.supplier or self.invoice or self.bill
        return f"{self.get_task_type_display()} {target}"


class ReminderRule(TimeStampedModel):
    class Audience(models.TextChoices):
        DEBTOR = "DEBTOR", "Debtor"
        CREDITOR = "CREDITOR", "Creditor"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    audience = models.CharField(max_length=10, choices=Audience.choices)
    name = models.CharField(max_length=120)
    offset_days = models.IntegerField(help_text="Negative = before due date, positive = after due date")
    is_active = models.BooleanField(default=True)
    task_type = models.CharField(max_length=20, choices=CollectionTask.TaskType.choices, default=CollectionTask.TaskType.SEND_REMINDER)
    priority = models.CharField(max_length=10, choices=CollectionTask.Priority.choices, default=CollectionTask.Priority.MEDIUM)

    class Meta:
        unique_together = [("company", "audience", "offset_days", "name")]
        ordering = ["audience", "offset_days"]

    def __str__(self):
        return f"{self.audience} {self.name} ({self.offset_days})"


class RiskRule(TimeStampedModel):
    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    key = models.CharField(max_length=60, unique=True)
    label = models.CharField(max_length=120)
    weight = models.IntegerField(default=10)
    threshold_low = models.IntegerField(default=20)
    threshold_medium = models.IntegerField(default=45)
    threshold_high = models.IntegerField(default=70)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.label


class Notification(TimeStampedModel):
    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        DANGER = "DANGER", "Danger"

    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_control_notifications")
    title = models.CharField(max_length=255)
    message = models.TextField()
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AuditEvent(TimeStampedModel):
    company = models.ForeignKey("accounting.Company", on_delete=models.CASCADE, null=True, blank=True)
    event_type = models.CharField(max_length=60)
    model_name = models.CharField(max_length=120)
    object_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=30)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
