import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from sales.models import Invoice, Quotation
from .client import APIError, CommunicationsClient, verify_callback
from .models import CallbackReceipt, CommunicationRequest
from .services import approve_document, document_digest, process_requests, reconcile

PDF = b"%PDF-1.7\nApproved test invoice\n%%EOF\n"


@override_settings(CLOUD_COMMUNICATIONS_ENABLED=True, CLOUD_COMMUNICATIONS_CURRENCY="USD")
class ConsumerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="communications-staff")
        self.user.groups.add(Group.objects.get_or_create(name="Staff")[0])
        self.customer = Customer.objects.create(name="Customer", email="test@example.test")
        self.invoice = Invoice.objects.create(customer=self.customer, status=Invoice.CONFIRMED)
        self.invoice.add_misc_line("Approved work", 100)
        self.quote = Quotation.objects.create(customer=self.customer, status=Quotation.SENT)
        self.quote.add_misc_line("Proposed work", 100)
        self.client.force_login(self.user)

    def approve(self, *, kind="invoice", approval_id=None, expected_digest=None):
        doc = self.invoice if kind == "invoice" else self.quote
        return approve_document(
            user=self.user,
            kind=kind,
            document_id=doc.pk,
            approval_id=approval_id or uuid.uuid4(),
            expected_digest=expected_digest or document_digest(doc),
            render_pdf=lambda _: PDF,
        )

    def remote(self, row, **changes):
        return {
            "id": str(row.remote_message_id or uuid.uuid4()),
            "status": "queued",
            "external_reference": row.payload["external_reference"],
            "updated_at": timezone.now().isoformat(),
            "error_code": "",
            **changes,
        }

    def test_approval_snapshots_pdf_and_performs_no_network_io(self):
        with patch("cloud_communications.client.build_opener") as opener:
            row = self.approve()
        opener.assert_not_called()
        self.assertEqual(row.payload["context"]["document_total"], "100.00")
        self.assertEqual(row.payload["recipients"], [self.customer.email])
        self.assertEqual(row.status, "queued")
        self.assertEqual(Invoice.objects.count(), 1)

    def test_both_document_types_and_idempotent_approval(self):
        identifier = uuid.uuid4()
        row = self.approve(approval_id=identifier)
        repeated = self.approve(approval_id=identifier)
        self.assertEqual(row.pk, repeated.pk)
        self.assertEqual(CommunicationRequest.objects.count(), 1)
        quotation = self.approve(kind="quotation")
        self.assertEqual(quotation.payload["event_type"], "quotation.approved")

    def test_approval_rolls_back_with_local_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.approve()
                raise RuntimeError()
        self.assertFalse(CommunicationRequest.objects.exists())

    def test_draft_unapproved_documents_and_stale_preview_are_rejected(self):
        self.quote.status = Quotation.DRAFT
        self.quote.save()
        with self.assertRaises(ValidationError):
            self.approve(kind="quotation")
        self.invoice.status = Invoice.PENDING
        self.invoice.save()
        with self.assertRaises(ValidationError):
            self.approve()
        self.invoice.status = Invoice.CONFIRMED
        self.invoice.save()
        with self.assertRaises(ValidationError):
            self.approve(expected_digest="0" * 64)

    def test_currency_must_be_confirmed_and_permissions_are_required(self):
        with (
            override_settings(CLOUD_COMMUNICATIONS_CURRENCY=""),
            self.assertRaises(ValidationError),
        ):
            self.approve()
        self.user.groups.clear()
        with self.assertRaises(PermissionDenied):
            self.approve()

    def test_service_outage_keeps_local_document_and_retries_same_key(self):
        row = self.approve()
        client = Mock()
        client.submit_event.side_effect = APIError("service_unavailable", retryable=True)
        self.assertEqual(process_requests(client=client), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "queued")
        self.assertEqual(Invoice.objects.get(pk=self.invoice.pk).total, 100)
        CommunicationRequest.objects.filter(pk=row.pk).update(
            available_at=timezone.now() - timedelta(seconds=1)
        )
        client.submit_event.side_effect = None
        client.submit_event.return_value = self.remote(row)
        process_requests(client=client)
        keys = [call.kwargs["idempotency_key"] for call in client.submit_event.call_args_list]
        self.assertEqual(keys, [str(row.pk), str(row.pk)])
        row.refresh_from_db()
        self.assertEqual(row.status, "submitted")
        self.assertEqual(process_requests(client=client), 0)

    def test_worker_restart_reuses_idempotency_key(self):
        row = self.approve()
        CommunicationRequest.objects.filter(pk=row.pk).update(
            status="submitting",
            lease_token=uuid.uuid4(),
            leased_until=timezone.now() - timedelta(minutes=1),
        )
        client = Mock()
        client.submit_event.return_value = self.remote(row)
        self.assertEqual(process_requests(client=client), 1)
        self.assertEqual(client.submit_event.call_args.kwargs["idempotency_key"], str(row.pk))

    def test_authentication_rejection_is_visible_without_retries(self):
        row = self.approve()
        client = Mock()
        client.submit_event.side_effect = APIError("http_error", status=401)
        process_requests(client=client)
        row.refresh_from_db()
        self.assertEqual(row.status, "failed")
        self.assertEqual(process_requests(client=client), 0)

    def test_changed_document_cancels_unsent_approval(self):
        row = self.approve()
        self.invoice.notes = "Superseded"
        self.invoice.save()
        client = Mock()
        process_requests(client=client)
        client.submit_event.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")

    def test_reconciliation_tracks_remote_submission_without_changing_invoice(self):
        row = self.approve()
        client = Mock()
        client.submit_event.return_value = self.remote(row)
        process_requests(client=client)
        row.refresh_from_db()
        client.status.return_value = self.remote(row, status="smtp_accepted")
        self.assertEqual(reconcile(client=client), 1)
        row.refresh_from_db()
        self.assertEqual(row.remote_status, "smtp_accepted")
        self.assertEqual(Invoice.objects.get(pk=self.invoice.pk).status, Invoice.CONFIRMED)

    def test_document_and_history_views_obey_feature_flag_and_permissions(self):
        url = reverse("ims:sales:cloud_document_email", args=["invoice", self.invoice.pk])
        self.assertContains(self.client.get(url), "Approve and queue email")
        row = self.approve()
        self.assertContains(
            self.client.get(reverse("ims:sales:cloud_message", args=[row.pk])),
            "Awaiting submission",
        )
        with override_settings(CLOUD_COMMUNICATIONS_ENABLED=False):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.user.groups.clear()
        self.assertEqual(self.client.get(url).status_code, 403)

    @override_settings(
        CLOUD_COMMUNICATIONS_CALLBACK_SECRET="test-only-callback-secret-32-characters-minimum"
    )
    def test_signed_callback_deduplication_and_out_of_order_delivery(self):
        row = self.approve()
        message_id = str(uuid.uuid4())
        latest = timezone.now()

        def callback(status, updated, event_id=None):
            body = json.dumps(
                {
                    "event_id": str(event_id or uuid.uuid4()),
                    "event_type": "communication.status",
                    "message_id": message_id,
                    "external_reference": row.payload["external_reference"],
                    "status": status,
                    "updated_at": updated.isoformat(),
                    "error_code": "",
                }
            ).encode()
            timestamp = str(int(time.time()))
            from django.conf import settings

            sig = hmac.new(
                settings.CLOUD_COMMUNICATIONS_CALLBACK_SECRET.encode(),
                timestamp.encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            return self.client.post(
                reverse("ims:sales:cloud_callback"),
                body,
                content_type="application/json",
                secure=True,
                headers={"X-Boforg-Timestamp": timestamp, "X-Boforg-Signature": sig},
            )

        identifier = uuid.uuid4()
        self.assertEqual(callback("smtp_accepted", latest, identifier).status_code, 200)
        self.assertEqual(callback("smtp_accepted", latest, identifier).status_code, 200)
        self.assertEqual(CallbackReceipt.objects.count(), 1)
        self.assertEqual(callback("queued", latest - timedelta(seconds=2)).status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.remote_status, "smtp_accepted")

    @override_settings(
        CLOUD_COMMUNICATIONS_CALLBACK_SECRET="test-only-callback-secret-32-characters-minimum"
    )
    def test_invalid_and_expired_callback_signatures_are_rejected(self):
        self.assertEqual(
            self.client.post(
                reverse("ims:sales:cloud_callback"),
                b"{}",
                content_type="application/json",
                secure=True,
            ).status_code,
            401,
        )
        with self.assertRaises(APIError):
            verify_callback("s" * 40, str(int(time.time()) - 301), "0" * 64, b"{}")

    def test_reference_client_rejects_unsafe_origins_and_classifies_outage(self):
        for url in [
            "http://cloud.example.test",
            "https://secret@cloud.example.test",
            "https://cloud.example.test/other",
        ]:
            with self.assertRaises(ValueError):
                CommunicationsClient(url, "secret")
        opener = Mock()
        opener.open.side_effect = TimeoutError()
        with self.assertRaises(APIError) as raised:
            CommunicationsClient(
                "https://cloud.example.test", "secret", opener=opener
            ).capabilities()
        self.assertTrue(raised.exception.retryable)
