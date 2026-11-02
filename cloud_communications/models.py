import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CommunicationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey("sales.Invoice", null=True, on_delete=models.PROTECT)
    quotation = models.ForeignKey("sales.Quotation", null=True, on_delete=models.PROTECT)
    document_digest = models.CharField(max_length=64)
    payload = models.JSONField()
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default="queued")
    remote_message_id = models.UUIDField(null=True, unique=True)
    remote_status = models.CharField(max_length=20, blank=True)
    remote_updated_at = models.DateTimeField(null=True)
    available_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True)
    leased_until = models.DateTimeField(null=True)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-approved_at"]
        indexes = [models.Index(fields=["status", "available_at"])]


class CallbackReceipt(models.Model):
    event_id = models.UUIDField(primary_key=True)
    request = models.ForeignKey(CommunicationRequest, on_delete=models.CASCADE)
    received_at = models.DateTimeField(auto_now_add=True)
