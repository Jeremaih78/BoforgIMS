import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from sales.models import Invoice, Quotation
from sales.services.payments import record_payment
from .finalization import finalize_document
from .models import CommunicationRequest
from .services import approve_document, document_digest, process_requests, queue_finalized_document

PDF = b"%PDF-1.7\nFinalized test document\n%%EOF\n"


@override_settings(
    CLOUD_COMMUNICATIONS_ENABLED=True, CLOUD_COMMUNICATIONS_CURRENCY="USD",
    CLOUD_COMMUNICATIONS_AUTO_INVOICE=True, CLOUD_COMMUNICATIONS_AUTO_QUOTATION=True,
)
class FinalizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="finalization-staff")
        self.user.groups.add(Group.objects.get_or_create(name="Staff")[0])
        self.customer = Customer.objects.create(name="Finalization customer", email="controlled@example.test")
        self.invoice = Invoice.objects.create(customer=self.customer)
        self.invoice.add_misc_line("Completed work", 100)
        self.quote = Quotation.objects.create(customer=self.customer)
        self.quote.add_misc_line("Proposed work", 100)
        self.renderer = patch("sales.pdf_utils.render_pdf_from_html", return_value=PDF).start()
        self.addCleanup(patch.stopall)
        self.client.force_login(self.user)

    def finalize(self, kind="invoice"):
        doc = self.invoice if kind == "invoice" else self.quote
        return finalize_document(user=self.user, kind=kind, document_id=doc.pk)

    def pay(self, amount, key=None):
        with (
            patch("accounting.signals.posting.post_sales_invoice"),
            patch("accounting.signals.posting.post_ar_receipt"),
            patch("accounting.signals.posting.post_cogs_for_invoice"),
        ):
            return record_payment(
                invoice_id=self.invoice.pk, user=self.user, amount=Decimal(amount),
                date=timezone.localdate(), method="CASH", note="", submission_key=key or uuid.uuid4(),
            )

    def dispatch(self, row):
        remote = Mock()
        remote.submit_event.return_value = {
            "id": str(uuid.uuid4()), "status": "queued",
            "updated_at": timezone.now().isoformat(),
            "external_reference": row.payload.get("external_reference", "blocked-test"),
        }
        process_requests(client=remote)
        return remote

    def test_confirmation_and_quotation_finalization_queue_once_without_network(self):
        with patch("cloud_communications.client.build_opener") as network:
            invoice_row = self.finalize()
            quote_row = self.finalize("quotation")
            self.assertIsNone(self.finalize())
            self.assertIsNone(self.finalize("quotation"))
            self.invoice.confirm(user=self.user)
        network.assert_not_called()
        self.assertEqual(CommunicationRequest.objects.count(), 2)
        self.assertEqual(self.renderer.call_count, 2)
        for kind, row in [("invoice", invoice_row), ("quotation", quote_row)]:
            self.assertEqual(row.trigger, "finalized")
            self.assertEqual(row.status, "queued")
            self.assertEqual(row.payload["event_type"], f"{kind}.approved")
            self.assertEqual(row.payload["recipients"], [self.customer.email])
            self.assertEqual(row.approved_by, self.user)

    def test_ordinary_saves_and_existing_finalized_documents_do_not_backfill(self):
        self.invoice.status = Invoice.CONFIRMED
        self.invoice.save()
        self.quote.status = Quotation.SENT
        self.quote.save()
        self.finalize()
        self.finalize("quotation")
        self.assertFalse(CommunicationRequest.objects.exists())

    def test_flags_are_independent_and_integration_flag_overrides_both(self):
        with override_settings(CLOUD_COMMUNICATIONS_AUTO_INVOICE=False):
            self.finalize()
        self.assertFalse(CommunicationRequest.objects.exists())
        self.finalize("quotation")
        self.assertEqual(CommunicationRequest.objects.count(), 1)
        other = Invoice.objects.create(customer=self.customer)
        other.add_misc_line("Work", 10)
        with override_settings(CLOUD_COMMUNICATIONS_ENABLED=False):
            other.confirm(user=self.user)
        self.assertEqual(CommunicationRequest.objects.count(), 1)

    def test_rollback_retains_neither_status_transition_nor_outbox(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.finalize("quotation")
                raise RuntimeError("rejected operation")
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quotation.DRAFT)
        self.assertFalse(CommunicationRequest.objects.exists())

    def test_partial_payment_does_not_send_full_payment_queues_once_after_receipt(self):
        self.pay("40")
        self.assertFalse(CommunicationRequest.objects.exists())
        key = uuid.uuid4()
        self.pay("60", key)
        self.pay("60", key)
        row = CommunicationRequest.objects.get()
        self.assertEqual(row.status, "queued")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.PAID)
        self.assertTrue(self.invoice.stock_finalized)
        self.assertEqual(self.invoice.payments.count(), 2)
        self.assertEqual(self.renderer.call_args.args[0].count("PAID") > 0, True)

    def test_payment_after_confirmation_does_not_cancel_or_duplicate_original_email(self):
        row = self.finalize()
        self.pay("100")
        self.invoice.lines.update(cost_unit_snapshot=Decimal("17.50"))
        self.assertEqual(CommunicationRequest.objects.count(), 1)
        self.dispatch(row).submit_event.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.status, "submitted")

    def test_accounting_failure_rolls_back_payment_finalization_and_email(self):
        with patch("accounting.signals.posting.post_sales_invoice", side_effect=ValueError("posting failed")):
            with self.assertRaises(ValueError):
                record_payment(
                    invoice_id=self.invoice.pk, user=self.user, amount=Decimal("100"),
                    date=timezone.localdate(), method="CASH", note="", submission_key=uuid.uuid4(),
                )
        self.invoice.refresh_from_db()
        self.assertFalse(self.invoice.stock_finalized)
        self.assertEqual(self.invoice.status, Invoice.PENDING)
        self.assertFalse(self.invoice.payments.exists())
        self.assertFalse(CommunicationRequest.objects.exists())

    def test_quotation_conversion_preserves_original_snapshot_but_edits_cancel(self):
        row = self.finalize("quotation")
        self.quote.refresh_from_db()
        self.quote.status = Quotation.CONVERTED
        self.quote.save(update_fields=["status"])
        self.dispatch(row).submit_event.assert_called_once()
        invoice_row = self.finalize()
        self.invoice.lines.update(description="Changed financial content")
        self.dispatch(invoice_row).submit_event.assert_not_called()
        invoice_row.refresh_from_db()
        self.assertEqual(invoice_row.status, "cancelled")

    def test_missing_recipient_is_visible_and_does_not_lose_payment(self):
        self.customer.email = ""
        self.customer.save(update_fields=["email"])
        self.pay("100")
        row = CommunicationRequest.objects.get()
        self.assertEqual(row.status, "blocked")
        self.assertEqual(row.error_code, "finalization_email_needs_review")
        self.assertEqual(self.invoice.payments.count(), 1)
        self.dispatch(row).submit_event.assert_not_called()

    def test_bad_pdf_is_blocked_and_can_be_manually_approved_after_repair(self):
        self.renderer.return_value = b"broken"
        blocked = self.finalize()
        self.assertEqual(blocked.status, "blocked")
        self.invoice.refresh_from_db()
        manual = approve_document(
            user=self.user, kind="invoice", document_id=self.invoice.pk,
            approval_id=uuid.uuid4(), expected_digest=document_digest(self.invoice),
            render_pdf=lambda _: PDF,
        )
        self.assertEqual(manual.status, "queued")
        self.assertEqual(manual.trigger, "manual")

    def test_manual_approval_reuses_automatic_request_including_failed_outcome(self):
        row = self.finalize()
        CommunicationRequest.objects.filter(pk=row.pk).update(status="failed")
        self.invoice.refresh_from_db()
        for _ in range(2):
            manual = approve_document(
                user=self.user, kind="invoice", document_id=self.invoice.pk,
                approval_id=uuid.uuid4(), expected_digest=document_digest(self.invoice),
                render_pdf=lambda _: PDF,
            )
            self.assertEqual(manual.pk, row.pk)
        self.assertEqual(CommunicationRequest.objects.count(), 1)

    def test_permissions_post_only_csrf_and_empty_document(self):
        url = reverse("ims:sales:document_finalize", args=["quotation", self.quote.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.user.groups.clear()
        with self.assertRaises(PermissionDenied):
            self.finalize()
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertFalse(CommunicationRequest.objects.exists())
        self.user.groups.add(Group.objects.get(name="Staff"))
        self.quote.lines.all().delete()
        with self.assertRaises(ValidationError):
            self.finalize("quotation")

    def test_ui_finalization_and_history_show_automatic_delivery(self):
        url = reverse("ims:sales:document_finalize", args=["quotation", self.quote.pk])
        detail = reverse("ims:sales:quotation_edit", args=[self.quote.pk])
        self.assertContains(self.client.get(detail), "Finalize quotation &amp; email")
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(CommunicationRequest.objects.count(), 1)
        history = reverse("ims:sales:cloud_document_email", args=["quotation", self.quote.pk])
        self.assertContains(self.client.get(history), "automatically queues its first email")

    def test_repeated_hook_reuses_uuid_and_service_outage_retains_queue(self):
        row = self.finalize()
        other = queue_finalized_document(user=self.user, kind="invoice", document_id=self.invoice.pk)
        self.assertEqual(row.pk, other.pk)
        from .client import APIError
        remote = Mock()
        remote.submit_event.side_effect = APIError("service_unavailable", retryable=True)
        process_requests(client=remote)
        row.refresh_from_db()
        self.assertEqual(row.status, "queued")
        self.assertEqual(remote.submit_event.call_args.kwargs["idempotency_key"], str(row.pk))


@override_settings(
    CLOUD_COMMUNICATIONS_ENABLED=True, CLOUD_COMMUNICATIONS_CURRENCY="USD",
    CLOUD_COMMUNICATIONS_AUTO_INVOICE=True, CLOUD_COMMUNICATIONS_AUTO_QUOTATION=True,
)
class ConcurrentFinalizationTests(TransactionTestCase):
    def test_concurrent_quotation_finalization_creates_one_snapshot(self):
        user = get_user_model().objects.create_superuser(username="concurrent-finalizer")
        customer = Customer.objects.create(name="Concurrent customer", email="controlled@example.test")
        quote = Quotation.objects.create(customer=customer)
        quote.add_misc_line("Proposed work", 100)

        def finalize():
            close_old_connections()
            try:
                return finalize_document(
                    user=get_user_model().objects.get(pk=user.pk), kind="quotation", document_id=quote.pk,
                )
            finally:
                close_old_connections()

        with patch("sales.pdf_utils.render_pdf_from_html", return_value=PDF):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(finalize) for _ in range(2)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(row is not None for row in results), 1)
        self.assertEqual(CommunicationRequest.objects.filter(quotation=quote).count(), 1)
