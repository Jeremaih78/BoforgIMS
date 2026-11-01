"""Content-free, exact monetary governance for the existing AI boundary."""

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


MONEY_ZERO = Decimal("0")
EMPLOYEE_MAXIMUM = Decimal("0.50")
COMPANY_MAXIMUM = Decimal("5.00")


class AIBudgetPolicy(models.Model):
    """One calendar for an employee's allowance across every billing company.

    No production calendar/currency is inferred. Once money has been admitted,
    changing its calendar would manufacture a second allowance, so that part
    of the policy is frozen. Disabling admission never erases existing holds.
    """

    key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    is_enabled = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, blank=True)
    timezone_name = models.CharField(max_length=64, blank=True)
    week_start_day = models.PositiveSmallIntegerField(null=True, blank=True)
    reset_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    reset_minute = models.PositiveSmallIntegerField(null=True, blank=True)
    employee_weekly_limit = models.DecimalField(max_digits=20, decimal_places=18, default=EMPLOYEE_MAXIMUM)
    updated_at = models.DateTimeField(auto_now=True)

    CALENDAR_FIELDS = ("currency", "timezone_name", "week_start_day", "reset_hour", "reset_minute")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(key=1), name="tasker_money_single_policy"),
            models.CheckConstraint(condition=Q(employee_weekly_limit__gte=0, employee_weekly_limit__lte=EMPLOYEE_MAXIMUM), name="tasker_money_employee_max"),
            models.CheckConstraint(condition=Q(week_start_day__isnull=True) | Q(week_start_day__lte=6), name="tasker_money_weekday"),
            models.CheckConstraint(condition=Q(reset_hour__isnull=True) | Q(reset_hour__lte=23), name="tasker_money_hour"),
            models.CheckConstraint(condition=Q(reset_minute__isnull=True) | Q(reset_minute__lte=59), name="tasker_money_minute"),
        ]

    def clean(self):
        super().clean()
        if self.is_enabled:
            if self.currency != "USD":
                raise ValidationError("An explicitly confirmed USD policy is required; currency conversion is not enabled.")
            if not self.timezone_name or any(getattr(self, field) is None for field in self.CALENDAR_FIELDS[2:]):
                raise ValidationError("Choose the weekly calendar, timezone and reset time before enabling AI spending.")
        if self.timezone_name:
            try:
                ZoneInfo(self.timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValidationError("Choose a valid reporting timezone.") from exc
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field) for field in self.CALENDAR_FIELDS):
                if AISpendReservation.objects.exists():
                    raise ValidationError("A spending calendar already has evidence. Preserve it rather than resetting an employee's allowance.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "AI weekly spending policy"


class AICompanyBudget(models.Model):
    company = models.OneToOneField("accounting.Company", on_delete=models.PROTECT, related_name="tasker_ai_budget")
    is_enabled = models.BooleanField(default=False)
    weekly_limit = models.DecimalField(max_digits=36, decimal_places=18, null=True, blank=True, default=COMPANY_MAXIMUM)
    charge_requester = models.BooleanField(default=False, help_text="Explicit personal and manager requests charge their authenticated requester. Automatic requests without an actor remain blocked.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(weekly_limit__isnull=True) | Q(weekly_limit__gte=0, weekly_limit__lte=COMPANY_MAXIMUM), name="tasker_money_company_bounds")]

    def clean(self):
        if self.is_enabled and (self.weekly_limit is None or not self.charge_requester):
            raise ValidationError("Set the company ceiling and explicitly confirm requester attribution before enabling spending.")
        if self.weekly_limit is not None and not MONEY_ZERO <= self.weekly_limit <= COMPANY_MAXIMUM:
            raise ValidationError("The confirmed company ceiling is at most USD 5 per week.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"AI budget: company {self.company_id}"


class AIBudgetEnrollment(models.Model):
    """One explicit default payer; request content cannot select a payer."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_budget_enrollment")
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_ai_budget_enrollments")
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AI enrollment: user {self.user_id} / company {self.company_id}"


class AIPriceCard(models.Model):
    """A verified fixed-model price version; old request prices never change."""

    provider = models.CharField(max_length=20, default="OPENAI")
    model = models.CharField(max_length=100)
    service_tier = models.CharField(max_length=20, default="default")
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    input_usd_per_million = models.DecimalField(max_digits=24, decimal_places=12)
    cached_input_usd_per_million = models.DecimalField(max_digits=24, decimal_places=12)
    cache_write_usd_per_million = models.DecimalField(max_digits=24, decimal_places=12, null=True, blank=True)
    output_usd_per_million = models.DecimalField(max_digits=24, decimal_places=12)
    max_input_tokens = models.PositiveIntegerField()
    max_output_tokens = models.PositiveIntegerField()
    capability_profile = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    FROZEN_FIELDS = ("provider", "model", "service_tier", "version", "is_verified", "verified_at", "valid_until", "input_usd_per_million", "cached_input_usd_per_million", "cache_write_usd_per_million", "output_usd_per_million", "max_input_tokens", "max_output_tokens", "capability_profile")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "model", "service_tier", "version"], name="tasker_money_price_version"),
            models.UniqueConstraint(fields=["provider", "model", "service_tier"], condition=Q(is_active=True), name="tasker_money_active_price"),
            models.CheckConstraint(condition=Q(input_usd_per_million__gt=0, cached_input_usd_per_million__gte=0, output_usd_per_million__gt=0), name="tasker_money_price_nonneg"),
            models.CheckConstraint(condition=Q(cache_write_usd_per_million__isnull=True) | Q(cache_write_usd_per_million__gte=0), name="tasker_money_write_nonneg"),
            models.CheckConstraint(condition=Q(max_input_tokens__gt=0, max_output_tokens__gt=0), name="tasker_money_price_bounds"),
        ]

    def clean(self):
        if self.is_active:
            if not self.is_verified or not self.verified_at or not self.valid_until or self.valid_until <= self.verified_at:
                raise ValidationError("An active card requires verified pricing and an explicit validity window.")
            if self.provider != "OPENAI" or self.service_tier != "default":
                raise ValidationError("Only OpenAI text requests on the default tier are costed.")
            if not isinstance(self.capability_profile, dict) or not self.capability_profile:
                raise ValidationError("Record a versioned model capability profile before activating a price card.")
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field) for field in self.FROZEN_FIELDS):
                raise ValidationError("Price versions are immutable. Create a new version and retain the original evidence.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.provider} {self.model} / {self.service_tier} v{self.version}"


class AISpendReservation(models.Model):
    usage_log = models.OneToOneField("tasker.AIUsageLog", on_delete=models.PROTECT, related_name="money_reservation")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_spend_reservations")
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_ai_spend_reservations")
    policy = models.ForeignKey(AIBudgetPolicy, on_delete=models.PROTECT)
    price_card = models.ForeignKey(AIPriceCard, on_delete=models.PROTECT)
    admitted_at = models.DateTimeField(default=timezone.now, editable=False)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    configuration_snapshot = models.JSONField(default=dict)
    price_snapshot = models.JSONField(default=dict)
    policy_snapshot = models.JSONField(default=dict)
    prompt_fingerprint = models.CharField(max_length=64)
    configuration_fingerprint = models.CharField(max_length=64)
    input_bound = models.PositiveIntegerField()
    output_bound = models.PositiveIntegerField()
    max_attempts = models.PositiveSmallIntegerField()
    settled_at = models.DateTimeField(null=True, blank=True)
    requires_review = models.BooleanField(default=False)
    accounting_code = models.CharField(max_length=64, blank=True)

    FROZEN_FIELDS = (
        "usage_log_id", "user_id", "company_id", "policy_id", "price_card_id",
        "admitted_at", "period_start", "period_end", "configuration_snapshot",
        "price_snapshot", "policy_snapshot", "prompt_fingerprint",
        "configuration_fingerprint", "input_bound", "output_bound", "max_attempts",
    )

    class Meta:
        indexes = [models.Index(fields=["user", "period_start"], name="tasker_money_user_week_idx"), models.Index(fields=["company", "period_start"], name="tasker_money_comp_week_idx")]
        constraints = [models.CheckConstraint(condition=Q(period_end__gt=models.F("period_start")), name="tasker_money_period_order")]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field) for field in self.FROZEN_FIELDS):
                raise ValidationError("An admitted request's attribution, prices and configuration are immutable audit evidence.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"AI spending reservation {self.pk}"


class AISpendAttempt(models.Model):
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved before dispatch"
        DISPATCHED = "DISPATCHED", "Provider attempt started"
        KNOWN = "KNOWN", "Measured charge"
        UNKNOWN = "UNKNOWN", "Unknown charge held"
        RELEASED = "RELEASED", "Not submitted"

    reservation = models.ForeignKey(AISpendReservation, on_delete=models.PROTECT, related_name="attempts")
    number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)
    held_amount = models.DecimalField(max_digits=36, decimal_places=18, default=MONEY_ZERO)
    charged_amount = models.DecimalField(max_digits=36, decimal_places=18, default=MONEY_ZERO)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    usage_snapshot = models.JSONField(default=dict)
    failure_code = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reservation", "number"], name="tasker_money_attempt_once"),
            models.CheckConstraint(condition=Q(number__gte=1, held_amount__gte=0, charged_amount__gte=0), name="tasker_money_attempt_nonneg"),
        ]

    def __str__(self):
        return f"AI spend attempt {self.reservation_id}/{self.number}"
