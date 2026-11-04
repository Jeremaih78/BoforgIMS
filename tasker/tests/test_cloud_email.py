"""Synthetic PostgreSQL tests; all Cloud calls are injected mocks."""

import time
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from tasker.cloud_email_models import (
    StaffEmailBinding,
    StaffEmailPolicy,
    StaffEmailPreference,
    StaffEmailReceipt,
)
from tasker.email_protocol import canonical, digest, sign
from tasker.models import Task
from tasker.notification_models import NotificationPreference
from tasker.services.cloud_email import (
    apply_receipt,
    eligibility,
    handle_cloud_delivery,
    record_task_change,
    schedule_reminders,
)
from tasker.tests import test_coordination as fixtures
from tasker.tests import test_reporting as report_fixtures


@override_settings(
    TASKER_CLOUD_EMAIL_ENABLED=True, TASKER_EMAIL_ORIGIN="https://ims.example.invalid"
)
class StaffEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.CoordinationTests.setUpTestData.__func__(cls)

    def setUp(self):
        fixtures.CoordinationTests.setUp(self)
        self.owner.email = "employee@example.invalid"
        self.owner.save(update_fields=["email"])
        self.policy = StaffEmailPolicy.objects.create(
            company=self.company,
            enabled=True,
            tenant_id="example-tenant",
            client_id=uuid.uuid4(),
            activation_after=self.now - timedelta(days=1),
        )

    def intent(self):
        record_task_change(self.task)
        return StaffEmailBinding.objects.get()

    def receipt(self, binding, status="queued", version=1, **changes):
        return {
            "schema_version": "2",
            "id": str(binding.remote_message_id or uuid.uuid4()),
            "tenant_id": self.policy.tenant_id,
            "client_id": str(self.policy.client_id),
            "delivery_id": str(binding.pk),
            "payload_digest": binding.payload_digest,
            "status": status,
            "state_version": version,
            "submitted_at": self.now.isoformat() if status == "smtp_accepted" else None,
            **changes,
        }

    def admitted(self):
        binding = self.intent()

        def submit(value, *, idempotency_key):
            binding.refresh_from_db()
            return self.receipt(binding)

        client = Mock()
        client.submit.side_effect = submit
        handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        binding.refresh_from_db()
        return binding, client

    def test_arbitrary_model_save_does_not_create_email(self):
        self.task.save()
        self.assertFalse(StaffEmailBinding.objects.exists())

    def test_replayed_source_event_reuses_intent(self):
        first = self.intent()
        record_task_change(self.task)
        self.assertEqual(StaffEmailBinding.objects.get().pk, first.pk)

    def test_same_clock_reassignment_occurrences_remain_distinct(self):
        self.intent()
        for recipient in (self.peer, self.owner):
            previous = Task.objects.get(pk=self.task.pk)
            self.task.owner = recipient
            self.task.save()
            record_task_change(self.task, previous)
        self.assertEqual(StaffEmailBinding.objects.count(), 3)
        self.assertEqual(
            StaffEmailBinding.objects.filter(delivery__recipient=self.owner).count(), 2
        )

    def test_background_action_uses_canonical_tasker_routes_and_restores_context(self):
        from django.urls import get_urlconf, resolve
        from urllib.parse import urlsplit
        from tasker.services.cloud_email import envelope

        binding = self.intent()
        previous = get_urlconf()
        with override_settings(
            TASKER_EMAIL_ORIGIN="https://ai.example.invalid",
            TASKER_EMAIL_URLCONF="config.host_urls.ai",
        ):
            value = envelope(binding)
        match = resolve(
            urlsplit(value["context"]["action_url"]).path, urlconf="config.host_urls.ai"
        )
        self.assertEqual(match.view_name, "ims:tasker:task_detail")
        self.assertEqual(get_urlconf(), previous)

    def test_shared_contract_fixture_and_receipt(self):
        import json
        from pathlib import Path
        from tasker.email_protocol import SubmissionReceipt

        fixture = json.loads((Path(__file__).parent / "fixtures/staff-email-v2.json").read_text())
        self.assertEqual(digest(fixture["envelope"]), fixture["digest"])
        self.assertEqual(SubmissionReceipt.parse(fixture["receipt"]).state, "queued")

    def test_intent_and_source_transaction_roll_back_together(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                previous = Task.objects.get(pk=self.task.pk)
                self.task.owner = self.peer
                self.task.save()
                record_task_change(self.task, previous)
                raise RuntimeError("synthetic rollback")
        self.assertFalse(StaffEmailBinding.objects.exists())
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.owner.pk)

    def test_disabled_gate_never_calls_cloud(self):
        binding = self.intent()
        client = Mock()
        with override_settings(TASKER_CLOUD_EMAIL_ENABLED=False):
            handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        client.submit.assert_not_called()
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "HELD")

    def test_activation_watermark_prevents_catchup(self):
        binding = self.intent()
        self.policy.activation_after = self.now + timedelta(seconds=1)
        self.policy.save()
        binding.policy = self.policy
        self.assertEqual(eligibility(binding)[:2], ("deny", "before_activation_watermark"))

    def test_admission_is_external_queue_not_provider_acceptance(self):
        binding, client = self.admitted()
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "EXTERNAL_QUEUED")
        self.assertIsNone(binding.delivery.accepted_at)
        self.assertIsNone(binding.delivery.delivered_at)
        self.assertEqual(client.submit.call_args.kwargs["idempotency_key"], str(binding.pk))

    def test_provider_acceptance_has_no_delivered_or_acknowledged_time(self):
        binding, _ = self.admitted()
        apply_receipt(binding.pk, uuid.uuid4(), self.receipt(binding, "smtp_accepted", 3))
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "PROVIDER_ACCEPTED")
        self.assertIsNotNone(binding.delivery.accepted_at)
        self.assertIsNone(binding.delivery.delivered_at)
        self.assertIsNone(binding.delivery.notification.acknowledged_at)

    def test_duplicate_stale_and_cross_tenant_receipts_cannot_regress(self):
        binding, _ = self.admitted()
        value = self.receipt(binding, "smtp_accepted", 3)
        event = uuid.uuid4()
        self.assertTrue(apply_receipt(binding.pk, event, value))
        self.assertFalse(apply_receipt(binding.pk, event, value))
        self.assertFalse(
            apply_receipt(binding.pk, uuid.uuid4(), self.receipt(binding, "queued", 2))
        )
        self.assertFalse(
            apply_receipt(
                binding.pk, uuid.uuid4(), self.receipt(binding, "failed", 4, tenant_id="wrong")
            )
        )
        self.assertTrue(StaffEmailReceipt.objects.filter(quarantined=True).exists())
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "PROVIDER_ACCEPTED")

    def test_reviewed_cloud_retry_does_not_erase_prior_acceptance_uncertainty(self):
        binding, _ = self.admitted()
        self.assertTrue(
            apply_receipt(
                binding.pk,
                uuid.uuid4(),
                self.receipt(
                    binding, "queued", 4, provider_acceptance="UNKNOWN", delivery_evidence="NONE"
                ),
            )
        )
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "UNKNOWN")
        self.assertIsNone(binding.delivery.accepted_at)

    def test_same_version_conflicting_acceptance_timestamp_is_quarantined(self):
        binding, _ = self.admitted()
        original = self.receipt(binding, "smtp_accepted", 3)
        apply_receipt(binding.pk, uuid.uuid4(), original)
        event = uuid.uuid4()
        changed = {**original, "submitted_at": (self.now + timedelta(seconds=1)).isoformat()}
        self.assertFalse(apply_receipt(binding.pk, event, changed))
        self.assertTrue(StaffEmailReceipt.objects.get(pk=event).quarantined)
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.accepted_at, self.now)

    def test_unverified_delivery_or_inconsistent_acceptance_is_rejected(self):
        from tasker.email_protocol import SubmissionReceipt

        binding, _ = self.admitted()
        for changes in (
            {"delivery_evidence": "DELIVERED"},
            {"provider_acceptance": "ACCEPTED"},
            {"attempt_id": 123},
        ):
            with self.assertRaises(ValueError):
                SubmissionReceipt.parse(self.receipt(binding, **changes))

    def test_unknown_is_reconciled_without_new_provider_submission(self):
        binding, _ = self.admitted()
        apply_receipt(binding.pk, uuid.uuid4(), self.receipt(binding, "unknown", 3))
        client = Mock()
        client.request.return_value = self.receipt(binding, "unknown", 3)
        handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        client.submit.assert_not_called()

    def test_quarantined_lookup_cannot_choose_a_cancellation_target(self):
        binding, _ = self.admitted()
        binding.cancel_requested = True
        binding.save(update_fields=["cancel_requested"])
        client = Mock()
        client.request.return_value = self.receipt(binding, "queued", 2, id=str(uuid.uuid4()))
        result = handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        self.assertEqual(result.reason, "cloud_receipt_quarantined")
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(client.request.call_args.args[0], "GET")
        self.assertTrue(StaffEmailReceipt.objects.filter(quarantined=True).exists())

    def test_lost_response_reconciles_original_key(self):
        binding = self.intent()
        client = Mock()
        client.submit.side_effect = TimeoutError()
        handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        binding.refresh_from_db()
        self.assertEqual(binding.phase, "submitting")
        client.request.return_value = self.receipt(binding)
        handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        self.assertEqual(client.submit.call_count, 1)
        self.assertIn(str(binding.pk), client.request.call_args.args[1])

    def test_reassignment_and_completion_deny_delayed_dispatch(self):
        binding, _ = self.admitted()
        self.task.owner = self.peer
        self.task.save()
        fresh = StaffEmailBinding.objects.get(pk=binding.pk)
        self.assertEqual(eligibility(fresh)[0], "deny")
        self.task.owner = self.owner
        self.task.status = "COMPLETED"
        self.task.save()
        self.assertEqual(eligibility(StaffEmailBinding.objects.get(pk=binding.pk))[0], "deny")

    def test_changed_email_and_preferences_deny_admitted_payload(self):
        binding, _ = self.admitted()
        self.owner.email = "changed@example.invalid"
        self.owner.save(update_fields=["email"])
        self.assertEqual(eligibility(StaffEmailBinding.objects.get(pk=binding.pk))[0], "deny")
        StaffEmailPreference.objects.create(owner=self.owner, assignments=False)
        self.assertEqual(
            eligibility(StaffEmailBinding.objects.get(pk=binding.pk))[1],
            "email_preference_disabled",
        )

    def test_quiet_hours_are_held(self):
        from datetime import time as clock_time

        NotificationPreference.objects.create(
            owner=self.owner,
            quiet_enabled=True,
            quiet_start=clock_time(9),
            quiet_end=clock_time(11),
        )
        binding = self.intent()
        self.assertEqual(eligibility(binding)[:2], ("defer", "quiet_hours"))

    def test_due_date_only_reminders_are_deduplicated(self):
        self.policy.reminders_enabled = True
        self.policy.categories = [*self.policy.categories, "due_soon", "overdue"]
        self.policy.save()
        self.task.due_date = self.now.date() - timedelta(days=1)
        self.task.save()
        schedule_reminders()
        schedule_reminders()
        self.assertEqual(StaffEmailBinding.objects.filter(category="overdue").count(), 1)
        self.assertIsNone(self.task.due_time)

    def test_signed_final_eligibility_fails_closed_and_records_grant(self):
        binding, _ = self.admitted()
        secret = "synthetic-only-eligibility-secret-000000"
        value = {
            "schema_version": "2",
            "request_id": str(uuid.uuid4()),
            "message_id": str(binding.remote_message_id),
            "tenant_id": self.policy.tenant_id,
            "client_id": str(self.policy.client_id),
            "company_id": str(self.company.pk),
            "delivery_id": str(binding.pk),
            "payload_digest": binding.payload_digest,
        }
        body, stamp = canonical(value), str(int(time.time()))
        headers = {
            "X-Boforg-Key-ID": "current",
            "X-Boforg-Timestamp": stamp,
            "X-Boforg-Signature": sign(secret, stamp, body),
        }
        path = reverse("ims:tasker:email_eligibility")
        with override_settings(TASKER_CLOUD_EMAIL_KEYS={str(self.company.pk): {"current": secret}}):
            response = self.client.post(
                path, body, content_type="application/json", headers=headers, secure=True
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["decision"], "allow")
            self.assertEqual(
                self.client.post(
                    path, body, content_type="application/json", headers=headers, secure=True
                ).status_code,
                403,
            )
            with override_settings(TASKER_CLOUD_EMAIL_ENABLED=False):
                value["request_id"] = str(uuid.uuid4())
                body = canonical(value)
                headers["X-Boforg-Signature"] = sign(secret, stamp, body)
                response = self.client.post(
                    path, body, content_type="application/json", headers=headers, secure=True
                )
                self.assertEqual(response.json()["decision"], "defer")

    def test_callback_before_post_response_and_rotation_overlap(self):
        binding = self.intent()
        secret = "synthetic-only-receipt-secret-00000000"
        message_id = str(uuid.uuid4())

        def submit(value, *, idempotency_key):
            binding.refresh_from_db()
            fact = self.receipt(
                binding,
                "smtp_accepted",
                3,
                id=message_id,
                event_id=str(uuid.uuid4()),
                event_type="communication.status",
            )
            body, stamp = canonical(fact), str(int(time.time()))
            headers = {
                "X-Boforg-Key-ID": "old",
                "X-Boforg-Timestamp": stamp,
                "X-Boforg-Signature": sign(secret, stamp, body),
            }
            response = self.client.post(
                reverse("ims:tasker:email_receipts"),
                body,
                content_type="application/json",
                headers=headers,
                secure=True,
            )
            self.assertEqual(response.status_code, 200)
            return self.receipt(binding, "queued", 1, id=message_id)

        client = Mock()
        client.submit.side_effect = submit
        with override_settings(
            TASKER_CLOUD_EMAIL_RECEIPT_KEYS={
                str(self.company.pk): {
                    "old": secret,
                    "current": "synthetic-new-receipt-secret-00000000",
                }
            }
        ):
            handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        binding.delivery.refresh_from_db()
        self.assertEqual(binding.delivery.state, "PROVIDER_ACCEPTED")

    def test_foreign_event_collision_does_not_mutate_existing_receipt(self):
        from django.core.exceptions import ValidationError

        binding, _ = self.admitted()
        event_id = uuid.uuid4()
        value = self.receipt(binding)
        apply_receipt(binding.pk, event_id, value)
        other = Task.objects.create(
            owner=self.owner,
            company=self.company,
            team=self.team,
            title="Separate synthetic assignment",
        )
        record_task_change(other)
        second = StaffEmailBinding.objects.exclude(pk=binding.pk).get()
        with self.assertRaises(ValidationError):
            apply_receipt(second.pk, event_id, value)
        self.assertFalse(StaffEmailReceipt.objects.get(event_id=event_id).quarantined)

    def test_typed_adapter_validates_receipts_without_network(self):
        import json
        from pathlib import Path
        from tasker.services.cloud_email_client import CloudEmailAdapter

        fixture = json.loads((Path(__file__).parent / "fixtures/staff-email-v2.json").read_text())
        http = Mock()
        http.submit.return_value = fixture["receipt"]
        adapter = CloudEmailAdapter(http)
        self.assertEqual(
            adapter.submit(fixture["envelope"], idempotency_key="synthetic").receipt.state, "queued"
        )
        http.submit.return_value = {**fixture["receipt"], "payload_digest": "malformed"}
        with self.assertRaises(ValueError):
            adapter.submit(fixture["envelope"], idempotency_key="synthetic")

    def test_repair_recovers_polling_without_another_submission(self):
        from tasker.job_models import OutboxJob
        from tasker.services.cloud_email import repair_cloud_email_jobs

        binding, _ = self.admitted()
        OutboxJob.objects.filter(dedupe_key=f"cloud-email:{binding.pk}").update(state="SUCCEEDED")
        StaffEmailBinding.objects.filter(pk=binding.pk).update(
            checked_at=self.now - timedelta(minutes=10)
        )
        self.assertEqual(repair_cloud_email_jobs(), 1)
        self.assertEqual(
            OutboxJob.objects.get(dedupe_key=f"cloud-email:{binding.pk}").state, "QUEUED"
        )
        client = Mock()
        client.request.return_value = self.receipt(binding)
        handle_cloud_delivery({"delivery_id": binding.delivery_id}, client=client)
        client.submit.assert_not_called()

    def test_repair_preserves_exhausted_or_cancelled_jobs(self):
        from tasker.job_models import OutboxJob
        from tasker.services.cloud_email import repair_cloud_email_jobs

        binding, _ = self.admitted()
        job = OutboxJob.objects.get(dedupe_key=f"cloud-email:{binding.pk}")
        job.state, job.attempts = "FAILED", job.max_attempts
        job.save()
        StaffEmailBinding.objects.filter(pk=binding.pk).update(
            checked_at=self.now - timedelta(minutes=10)
        )
        self.assertEqual(repair_cloud_email_jobs(), 0)
        job.refresh_from_db()
        self.assertEqual(job.state, "FAILED")

    def test_private_centre_is_owner_scoped_and_does_not_send(self):
        self.intent()
        self.client.force_login(self.peer)
        response = self.client.get(reverse("ims:tasker:email_centre"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.task.title)
        self.assertEqual(response["Cache-Control"], "no-store")

    def reminder_items(self):
        from tasker.services.email_digest import prepare_digests

        self.policy.reminders_enabled = True
        self.policy.categories = ["due_soon", "overdue"]
        self.policy.save()
        StaffEmailPreference.objects.create(owner=self.owner, reminders=True, digest_reminders=True)
        self.task.due_date = self.now.date() - timedelta(days=1)
        self.task.save()
        Task.objects.create(
            owner=self.owner,
            company=self.company,
            team=self.team,
            title="Second synthetic reminder",
            due_date=self.task.due_date,
        )
        schedule_reminders()
        with patch(
            "django.utils.timezone.now", return_value=self.now + timedelta(hours=1, seconds=1)
        ):
            prepare_digests()
        return StaffEmailBinding.objects.get(category="digest")

    def test_digest_is_reference_only_and_checks_each_item(self):
        from tasker.services.cloud_email import envelope

        master = self.reminder_items()
        self.assertEqual(len(master.digest_members), 2)
        self.assertEqual(eligibility(master)[0], "allow")
        value = envelope(master)
        self.assertIn("2 pending", value["context"]["notification_title"])
        self.assertNotIn(self.task.title, str(value))
        self.task.archived = True
        self.task.save()
        self.assertEqual(eligibility(StaffEmailBinding.objects.get(pk=master.pk))[0], "deny")

    def test_digest_removes_invalid_items_before_admission_and_skips_empty(self):
        from tasker.services.email_digest import refresh_before_submission

        master = self.reminder_items()
        self.task.archived = True
        self.task.save()
        refresh_before_submission(master)
        self.assertEqual(len(master.digest_members), 1)
        Task.objects.update(archived=True)
        refresh_before_submission(master)
        self.assertEqual(eligibility(master)[:2], ("deny", "digest_empty"))

    def test_manual_digest_cancellation_does_not_recreate_the_same_reminders(self):
        from tasker.services.cloud_email import envelope
        from tasker.services.email_digest import prepare_digests

        master = self.reminder_items()
        master.payload = envelope(master)
        master.payload_digest = digest(master.payload)
        master.phase = "admitted"
        master.remote_message_id = uuid.uuid4()
        master.save()
        apply_receipt(master.pk, uuid.uuid4(), self.receipt(master, "cancelled", 2))
        self.assertFalse(
            StaffEmailBinding.objects.filter(pk__in=master.digest_members, phase="held").exists()
        )
        prepare_digests()
        self.assertEqual(StaffEmailBinding.objects.filter(category="digest").count(), 1)

    def test_previous_day_overdue_occurrence_cannot_catch_up(self):
        master = self.reminder_items()
        child = StaffEmailBinding.objects.get(pk=master.digest_members[0])
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=1)):
            self.assertEqual(eligibility(child)[:2], ("deny", "reminder_occurrence_expired"))

    def test_policy_mapping_cannot_retarget_existing_evidence(self):
        from django.core.exceptions import ValidationError

        self.intent()
        self.policy.tenant_id = "different-tenant"
        with self.assertRaises(ValidationError):
            self.policy.full_clean()


@override_settings(TASKER_CLOUD_EMAIL_ENABLED=True)
class IntentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        fixtures.CoordinationTests.setUpTestData.__func__(type(self))
        fixtures.CoordinationTests.setUp(self)
        StaffEmailPolicy.objects.create(
            company=self.company,
            tenant_id="synthetic-tenant",
            client_id=uuid.uuid4(),
            activation_after=self.now,
        )

    def test_concurrent_source_replays_create_one_intent(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections

        def operation(_):
            close_old_connections()
            try:
                record_task_change(self.task)
                return str(StaffEmailBinding.objects.get().pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            identities = list(pool.map(operation, range(2)))
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(StaffEmailBinding.objects.count(), 1)


@override_settings(
    TASKER_REPORTS_ENABLED=True,
    TASKER_IMPROVEMENT_ENABLED=True,
    TASKER_REPORT_EMAIL_LIVE_ENABLED=False,
    TASKER_CLOUD_EMAIL_ENABLED=True,
    TASKER_EMAIL_ORIGIN="https://ims.example.invalid",
)
class CloudReportGateTests(TestCase):
    def setUp(self):
        report_fixtures.ReportingTests.setUp(self)
        self.executive.email = "executive@example.invalid"
        self.executive.save(update_fields=["email"])
        self.policy = StaffEmailPolicy.objects.create(
            company=self.company,
            enabled=True,
            tenant_id="report-tenant",
            client_id=uuid.uuid4(),
            categories=["report"],
            activation_after=report_fixtures.NOW - timedelta(days=1),
        )
        report_fixtures.ReportingTests.configure(self, email_mode="CLOUD", email_enabled=True)
        report_fixtures.ReportingTests.close(self)
        self.report = report_fixtures.ReportingTests.build(self)
        self.binding = StaffEmailBinding.objects.get(category="report")

    def test_report_gate_cannot_be_bypassed_by_cloud(self):
        client = Mock()
        result = handle_cloud_delivery({"delivery_id": self.binding.delivery_id}, client=client)
        client.submit.assert_not_called()
        self.assertEqual(result.reason, "report_activation_pending")

    def test_explicit_cloud_report_is_minimal_and_requires_current_grant(self):
        from tasker.services.cloud_email import envelope

        with override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True):
            self.assertEqual(eligibility(self.binding)[0], "allow")
            value = envelope(self.binding)
            self.assertNotIn("PRIVATE_", str(value))
            self.assertNotIn(self.task.title, str(value))
            self.grant.can_receive_reports = False
            self.grant.save()
            self.assertEqual(
                eligibility(StaffEmailBinding.objects.get(pk=self.binding.pk))[0], "deny"
            )

    def test_cloud_binding_fences_smtp_and_test_transport_even_when_disabled(self):
        from tasker.report_models import ReportPolicy
        from tasker.services.report_email import handle_smtp_delivery
        from tasker.services.reporting import handle_report_delivery

        self.binding.phase = "submitting"
        self.binding.payload_digest = "a" * 64
        self.binding.save()
        delivery = self.binding.delivery
        delivery.state = "EXTERNAL_QUEUED"
        delivery.save(update_fields=["state"])
        smtp, local = Mock(), Mock()
        smtp.send.return_value = {"state": "PROVIDER_ACCEPTED"}
        local.send.return_value = {"state": "TEST_ACCEPTED"}
        with override_settings(
            TASKER_CLOUD_EMAIL_ENABLED=False,
            TASKER_REPORT_EMAIL_LIVE_ENABLED=True,
            EMAIL_HOST="smtp.example.invalid",
            DEFAULT_FROM_EMAIL="Tasker <tasker@example.invalid>",
        ):
            ReportPolicy.objects.filter(company=self.company).update(email_mode="SMTP")
            handle_smtp_delivery({"delivery_id": delivery.pk}, adapter=smtp)
            ReportPolicy.objects.filter(company=self.company).update(
                email_mode="TEST", email_enabled=False
            )
            handle_report_delivery({"delivery_id": delivery.pk}, adapter=local)
        smtp.send.assert_not_called()
        local.send.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "EXTERNAL_QUEUED")

    def test_transport_change_is_blocked_while_cloud_admission_is_uncertain(self):
        from django.core.exceptions import ValidationError
        from tasker.report_models import ReportPolicy

        self.binding.phase = "submitting"
        self.binding.payload_digest = "a" * 64
        self.binding.save()
        policy = ReportPolicy.objects.get(company=self.company)
        policy.email_mode = "SMTP"
        with self.assertRaises(ValidationError):
            policy.save()

    def test_personal_report_opt_out_is_respected(self):
        StaffEmailPreference.objects.create(owner=self.executive, reports=False)
        with override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True):
            self.assertEqual(eligibility(self.binding)[:2], ("deny", "email_preference_disabled"))
