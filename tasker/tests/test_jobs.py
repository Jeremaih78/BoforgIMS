"""Durable jobs: dedupe, crash recovery, fencing, repair and private diagnostics."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
import json
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase

from tasker.job_models import JobWorkerHeartbeat, OutboxJob
from tasker.models import Task, TaskActivity
from tasker.operator_models import OperatorState
from tasker.services.jobs import (
    JobCancelled, JobConflict, JobDeferred, JobPermanentError, JobRetry,
    claim_next_job, enqueue_job, enqueue_operator_refresh, jobs_health,
    prune_terminal_jobs, retry_failed, run_claimed_job, run_worker_once, scheduler_tick,
)


NOW = datetime(2030, 1, 7, 8, tzinfo=dt_timezone.utc)


class JobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("job-test-owner")
        self.task = Task.objects.create(owner=self.user, title="Job domain test task")
        self.clock = patch("django.utils.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def enqueue(self, key="sample-job", **changes):
        return enqueue_job(**{"kind": "coordination.dispatch", "dedupe_key": key,
            "payload": {"request_id": self.task.pk, "event": "raised"}, "owner": self.user, **changes})

    def effect(self, *, payload, job=None):
        TaskActivity.objects.create(task=self.task, event_type="UPDATED", description="Job handler committed effect")
        return {"changed": 1}

    def effects(self):
        return TaskActivity.objects.filter(task=self.task, description="Job handler committed effect").count()

    def test_enqueue_is_atomic_with_domain_transaction_and_deduplicated(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.enqueue()
                raise RuntimeError("rolled back domain write")
        self.assertFalse(OutboxJob.objects.exists())
        first = self.enqueue()
        self.assertEqual(first.pk, self.enqueue().pk)
        self.assertEqual(OutboxJob.objects.count(), 1)
        with self.assertRaises(JobConflict):
            self.enqueue(payload={"request_id": self.task.pk, "event": "different"})
        with self.assertRaises(JobConflict):
            self.enqueue(kind="reports.build")

    def test_payload_references_are_bounded_and_private_content_is_refused(self):
        for payload in ({"email": "fake@example.test"}, {"notes": "private"}, {"nested": {"api_key": "fake"}},
                        {"reference": "x"*201}, {"refs": list(range(101))}, {"value": 1.5}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.enqueue(payload=payload)
        with self.assertRaises(ValidationError):
            self.enqueue(kind="unregistered.handler")
        with self.assertRaises(ValidationError):
            self.enqueue(kind="operator.refresh")
        with self.assertRaises(ValidationError):
            self.enqueue(run_after=NOW.replace(tzinfo=None))
        self.assertFalse(OutboxJob.objects.exists())

    def test_repeated_worker_passes_commit_one_domain_effect(self):
        job = self.enqueue()
        with patch("tasker.services.jobs._handler_for", return_value=self.effect):
            self.assertEqual(run_worker_once(limit=10)[0]["outcome"], "succeeded")
            self.assertEqual(run_worker_once(limit=10), [])
        self.assertEqual(self.effects(), 1)
        job.refresh_from_db()
        self.assertEqual(job.state, "SUCCEEDED")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.result, {"changed": 1})

    def test_failure_rolls_back_domain_changes_and_retries_without_sensitive_error_text(self):
        job = self.enqueue()
        def broken(**kwargs):
            self.effect(**kwargs)
            raise RuntimeError("private exception content must not be stored")
        with patch("tasker.services.jobs._handler_for", return_value=broken):
            self.assertEqual(run_worker_once()[0]["outcome"], "retry")
        self.assertEqual(self.effects(), 0)
        job.refresh_from_db()
        self.assertEqual(job.last_error_code, "handler_error")
        self.assertEqual(job.run_after, NOW+timedelta(seconds=30))
        self.assertNotIn("private exception", json.dumps(job.result))
        self.assertIsNone(claim_next_job())
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=31)), \
             patch("tasker.services.jobs._handler_for", return_value=self.effect):
            self.assertEqual(run_worker_once()[0]["outcome"], "succeeded")
        self.assertEqual(self.effects(), 1)

    def test_failed_attempts_are_bounded_and_manual_retry_requires_superuser(self):
        job = self.enqueue()
        OutboxJob.objects.filter(pk=job.pk).update(max_attempts=2)
        with patch("tasker.services.jobs._handler_for", return_value=lambda **kwargs: (_ for _ in ()).throw(RuntimeError())):
            run_worker_once()
            with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=31)):
                self.assertEqual(run_worker_once()[0]["outcome"], "failed")
        job.refresh_from_db()
        self.assertEqual(job.state, "FAILED")
        self.assertIsNone(claim_next_job())
        with self.assertRaises(PermissionDenied): retry_failed(user=self.user, job_id=job.pk)
        admin = get_user_model().objects.create_user("job-test-admin", is_superuser=True)
        retried = retry_failed(user=admin, job_id=job.pk)
        self.assertEqual(retried.state, "QUEUED")
        self.assertEqual(retried.attempts, 0)

    def test_permanent_errors_fail_without_consuming_all_retries(self):
        job = self.enqueue()
        with patch("tasker.services.jobs._handler_for", return_value=lambda **kwargs: (_ for _ in ()).throw(JobPermanentError("unsupported_source"))):
            self.assertEqual(run_worker_once()[0]["outcome"], "failed")
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.last_error_code, "unsupported_source")

    def test_returned_deferral_commits_held_effect_and_does_not_exhaust_failure_attempts(self):
        job = self.enqueue()
        def held(**kwargs):
            self.effect(**kwargs)
            return JobDeferred(run_after=NOW+timedelta(hours=1), reason="quiet_hours")
        with patch("tasker.services.jobs._handler_for", return_value=held):
            self.assertEqual(run_worker_once()[0]["outcome"], "deferred")
        self.assertEqual(self.effects(), 1)
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts, job.deferrals), ("QUEUED", 0, 1))
        self.assertEqual(job.run_after, NOW+timedelta(hours=1))

    def test_raised_deferral_rolls_back_partial_effect_but_reschedules(self):
        job = self.enqueue()
        def held(**kwargs):
            self.effect(**kwargs)
            raise JobDeferred(run_after=NOW+timedelta(minutes=5), reason="focus_active")
        with patch("tasker.services.jobs._handler_for", return_value=held):
            self.assertEqual(run_worker_once()[0]["outcome"], "deferred")
        self.assertEqual(self.effects(), 0)
        job.refresh_from_db()
        self.assertEqual(job.last_error_code, "focus_active")

    def test_returned_retry_commits_failure_ledger_but_consumes_bounded_attempts(self):
        job = self.enqueue()
        OutboxJob.objects.filter(pk=job.pk).update(max_attempts=2)
        def failed_delivery(**kwargs):
            self.effect(**kwargs)
            return JobRetry("test_adapter_failed", run_after=NOW+timedelta(minutes=1))
        with patch("tasker.services.jobs._handler_for", return_value=failed_delivery):
            self.assertEqual(run_worker_once()[0]["outcome"], "retry")
            self.assertEqual(self.effects(), 1)
            with patch("django.utils.timezone.now", return_value=NOW+timedelta(minutes=2)):
                self.assertEqual(run_worker_once()[0]["outcome"], "failed")
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts, job.last_error_code), ("FAILED", 2, "test_adapter_failed"))
        self.assertEqual(self.effects(), 2)

    def test_obsolete_work_can_cancel_and_commit_its_ledger_effect(self):
        job = self.enqueue()
        def obsolete(**kwargs):
            self.effect(**kwargs)
            return JobCancelled("source_resolved")
        with patch("tasker.services.jobs._handler_for", return_value=obsolete):
            self.assertEqual(run_worker_once()[0]["outcome"], "cancelled")
        job.refresh_from_db()
        self.assertEqual(job.state, "CANCELLED")
        self.assertEqual(self.effects(), 1)

    def test_restart_reclaims_expired_lease_and_stale_worker_cannot_execute(self):
        self.enqueue()
        first = claim_next_job(worker_id="worker-one", lease_seconds=10)
        self.assertIsNone(claim_next_job(worker_id="worker-two"))
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=11)):
            second = claim_next_job(worker_id="worker-two")
            self.assertNotEqual(first.lease_token, second.lease_token)
            with patch("tasker.services.jobs._handler_for", return_value=self.effect):
                self.assertEqual(run_claimed_job(first)["outcome"], "lost_lease")
                self.assertEqual(run_claimed_job(second)["outcome"], "succeeded")
        self.assertEqual(self.effects(), 1)

    def test_expired_final_attempt_becomes_visible_failure(self):
        job = self.enqueue()
        OutboxJob.objects.filter(pk=job.pk).update(max_attempts=1)
        claim_next_job(lease_seconds=10)
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=11)):
            self.assertIsNone(claim_next_job())
        job.refresh_from_db()
        self.assertEqual((job.state, job.last_error_code), ("FAILED", "lease_exhausted"))

    def test_future_jobs_are_not_claimed(self):
        self.enqueue(run_after=NOW+timedelta(days=1))
        self.assertIsNone(claim_next_job())

    def test_raw_payload_edit_without_its_digest_cannot_execute_changed_work(self):
        job = self.enqueue()
        OutboxJob.objects.filter(pk=job.pk).update(payload={"request_id": self.task.pk+1})
        with patch("tasker.services.jobs._handler_for", return_value=self.effect):
            self.assertEqual(run_worker_once()[0]["outcome"], "failed")
        job.refresh_from_db()
        self.assertEqual(job.last_error_code, "payload_integrity_changed")
        self.assertEqual(self.effects(), 0)

    def test_operator_events_coalesce_one_pending_row_and_allow_future_revisions(self):
        one, two = uuid.uuid4(), uuid.uuid4()
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=one)
        newer = enqueue_operator_refresh(owner_id=self.user.pk, revision=two)
        self.assertEqual(job.pk, newer.pk)
        self.assertEqual(OutboxJob.objects.count(), 1)
        self.assertEqual(newer.payload["revision"], str(two))
        claimed = claim_next_job()
        with patch("tasker.services.jobs._handler_for", return_value=lambda **kwargs: {"reviewed": 0}):
            run_claimed_job(claimed)
        again = enqueue_operator_refresh(owner_id=self.user.pk, revision=uuid.uuid4())
        self.assertEqual(again.pk, job.pk)
        self.assertEqual(again.state, "QUEUED")

    def test_matching_failed_operator_revision_is_not_silently_retried_by_repair(self):
        revision = uuid.uuid4()
        OperatorState.objects.create(owner=self.user, revision=revision)
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=revision)
        OutboxJob.objects.filter(pk=job.pk).update(state="FAILED", attempts=5, last_error_code="handler_error")
        self.assertEqual(enqueue_operator_refresh(owner_id=self.user.pk, revision=revision).state, "FAILED")
        module = SimpleNamespace(reconcile_coordination=lambda **kwargs: {},
            reconcile_notifications=lambda **kwargs: {}, reconcile_reports=lambda **kwargs: {})
        with patch("tasker.services.jobs.importlib.import_module", return_value=module): scheduler_tick(limit=10)
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("FAILED", 5))

    def test_operator_enqueue_uses_current_state_after_a_stale_scheduler_read(self):
        old_revision, current_revision = uuid.uuid4(), uuid.uuid4()
        state = OperatorState.objects.create(owner=self.user, revision=old_revision)
        original = enqueue_operator_refresh(owner_id=self.user.pk, revision=old_revision)
        OperatorState.objects.filter(pk=state.pk).update(revision=current_revision)
        current = enqueue_operator_refresh(owner_id=self.user.pk, revision=current_revision)
        stale = enqueue_operator_refresh(owner_id=self.user.pk, revision=old_revision)
        self.assertEqual((stale.pk, stale.payload["revision"]), (original.pk, str(current_revision)))
        self.assertEqual(stale.payload_digest, current.payload_digest)

    def test_operator_revision_superseded_before_handler_starts_is_requeued_with_fresh_budget(self):
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=uuid.uuid4())
        OutboxJob.objects.filter(pk=job.pk).update(attempts=4)
        claimed = claim_next_job()
        new_revision = uuid.uuid4()
        enqueue_operator_refresh(owner_id=self.user.pk, revision=new_revision)
        with patch("tasker.services.jobs._handler_for", return_value=self.effect) as handler:
            self.assertEqual(run_claimed_job(claimed)["outcome"], "superseded")
            handler.assert_not_called()
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("QUEUED", 0))
        self.assertEqual(job.payload["revision"], str(new_revision))
        self.assertEqual(self.effects(), 0)

    def test_expired_superseded_operator_lease_keeps_new_revisions_full_attempt_budget(self):
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=uuid.uuid4())
        OutboxJob.objects.filter(pk=job.pk).update(attempts=4)
        claim_next_job(lease_seconds=10)
        enqueue_operator_refresh(owner_id=self.user.pk, revision=uuid.uuid4())
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=11)):
            recovered = claim_next_job()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.attempts, 1)

    def test_new_operator_event_during_refresh_rolls_back_old_read_and_keeps_latest(self):
        old = uuid.uuid4()
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=old)
        newer = uuid.uuid4()
        def changed(**kwargs):
            self.effect(**kwargs)
            enqueue_operator_refresh(owner_id=self.user.pk, revision=newer)
            return {"reviewed": 1}
        with patch("tasker.services.jobs._handler_for", return_value=changed):
            self.assertEqual(run_worker_once()[0]["outcome"], "superseded")
        # The synthetic newer event was in the rolled-back handler transaction;
        # real domain events arrive in their own transaction (covered by race).
        self.assertEqual(self.effects(), 0)
        job.refresh_from_db()
        self.assertEqual(job.state, "QUEUED")

    def test_result_summary_cannot_copy_arbitrary_handler_content(self):
        job = self.enqueue()
        result = {"created": 1, "private": "some sensitive text", "nested": {"body": "private"}}
        with patch("tasker.services.jobs._handler_for", return_value=lambda **kwargs: result):
            run_worker_once()
        job.refresh_from_db()
        self.assertEqual(job.result, {"created": 1})

    def test_result_summaries_remain_small_for_oversized_handler_diagnostics(self):
        job = self.enqueue()
        result = {"unbounded_integer": 2**64, **{f"count_{index}": index for index in range(100)}}
        with patch("tasker.services.jobs._handler_for", return_value=lambda **kwargs: result):
            run_worker_once()
        job.refresh_from_db()
        self.assertEqual(len(job.result), 24)
        self.assertNotIn("unbounded_integer", job.result)
        self.assertLess(len(json.dumps(job.result).encode("utf-8")), 4096)

    def test_retention_prunes_completed_detail_but_preserves_dedupe_and_failures(self):
        done = self.enqueue("completed")
        with patch("tasker.services.jobs._handler_for", return_value=self.effect): run_worker_once()
        failed = self.enqueue("failed")
        OutboxJob.objects.filter(pk=failed.pk).update(state="FAILED", finished_at=NOW-timedelta(days=60))
        OutboxJob.objects.filter(pk=done.pk).update(finished_at=NOW-timedelta(days=60))
        pending = self.enqueue("pending")
        self.assertEqual(prune_terminal_jobs(retention_days=30), 1)
        done.refresh_from_db(); failed.refresh_from_db(); pending.refresh_from_db()
        self.assertEqual(done.payload, {})
        self.assertTrue(failed.payload)
        self.assertTrue(pending.payload)
        self.assertEqual(self.enqueue("completed").pk, done.pk)
        self.assertEqual(OutboxJob.objects.count(), 3)

    def test_health_reports_missing_worker_expired_lease_and_failures_without_content(self):
        self.assertTrue(jobs_health()["healthy"])
        job = self.enqueue()
        health = jobs_health()
        self.assertFalse(health["healthy"])
        self.assertEqual(health["counts"], {"QUEUED": 1})
        self.assertNotIn("payload", health)
        output = StringIO()
        call_command("tasker_jobs_health", stdout=output)
        self.assertIn('"QUEUED": 1', output.getvalue())
        with patch("tasker.services.jobs._handler_for", return_value=self.effect): run_worker_once()
        self.assertTrue(jobs_health()["healthy"])
        self.assertTrue(JobWorkerHeartbeat.objects.exists())

    def test_health_requires_worker_heartbeats_and_exposes_expired_leases_and_failures(self):
        job = self.enqueue()
        JobWorkerHeartbeat.objects.create(name="scheduler-only", role="scheduler", seen_at=NOW)
        self.assertFalse(jobs_health()["worker_recent"])
        claim_next_job(lease_seconds=10)
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(seconds=11)):
            health = jobs_health()
        self.assertEqual(health["expired_leases"], 1)
        self.assertFalse(health["healthy"])
        OutboxJob.objects.filter(pk=job.pk).update(state="FAILED")
        JobWorkerHeartbeat.objects.create(name="healthy-worker", role="worker", seen_at=NOW)
        self.assertEqual(jobs_health()["counts"], {"FAILED": 1})
        self.assertFalse(jobs_health()["healthy"])

    def test_retention_clears_result_only_jobs_and_operator_summaries_without_losing_revision(self):
        empty = self.enqueue("empty-reference", payload={})
        operator = enqueue_operator_refresh(owner_id=self.user.pk, revision=uuid.uuid4())
        OutboxJob.objects.filter(pk__in=(empty.pk, operator.pk)).update(state="SUCCEEDED",
            finished_at=NOW-timedelta(days=60), result={"reviewed": 1})
        self.assertEqual(prune_terminal_jobs(retention_days=30, limit=1), 1)
        self.assertEqual(prune_terminal_jobs(retention_days=30, limit=1), 1)
        self.assertEqual(prune_terminal_jobs(retention_days=30, limit=1), 0)
        empty.refresh_from_db(); operator.refresh_from_db()
        self.assertEqual(empty.result, {})
        self.assertEqual(operator.result, {})
        self.assertIn("revision", operator.payload)

    def test_scheduler_repair_sweeps_share_the_same_queue(self):
        calls = []
        module = SimpleNamespace(
            reconcile_coordination=lambda **kwargs: calls.append("coordination") or {"checked": 1},
            reconcile_notifications=lambda **kwargs: calls.append("notifications") or {"checked": 1},
            reconcile_reports=lambda **kwargs: calls.append("reports") or {"checked": 1})
        OperatorState.objects.create(owner=self.user)
        with patch("tasker.services.jobs.importlib.import_module", return_value=module):
            scheduler_tick(limit=10)
            scheduler_tick(limit=10)
        self.assertEqual(calls, ["coordination", "notifications", "reports"]*2)
        self.assertEqual(OutboxJob.objects.count(), 1)

    def test_bounded_scheduler_passes_do_not_starve_later_missing_owner_jobs(self):
        users = get_user_model().objects.bulk_create([
            get_user_model()(username=f"repair-owner-{index}") for index in range(10)])
        OperatorState.objects.bulk_create([OperatorState(owner=user) for user in users])
        module = SimpleNamespace(reconcile_coordination=lambda **kwargs: {},
            reconcile_notifications=lambda **kwargs: {}, reconcile_reports=lambda **kwargs: {})
        with patch("tasker.services.jobs.importlib.import_module", return_value=module):
            for _ in range(4): scheduler_tick(limit=3)
        self.assertEqual(OutboxJob.objects.filter(kind="operator.refresh").count(), 10)

    def test_worker_command_runs_one_bounded_pass_by_default(self):
        self.enqueue()
        output = StringIO()
        with patch("tasker.services.jobs._handler_for", return_value=self.effect):
            call_command("tasker_worker", once=True, limit=5, stdout=output)
        self.assertIn('"processed": 1', output.getvalue())
        self.assertEqual(self.effects(), 1)


class JobConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("job-concurrency-owner")
        self.task = Task.objects.create(owner=self.user, title="Concurrent job sample")

    def enqueue(self, kind="coordination.dispatch", payload=None):
        return enqueue_job(kind=kind, dedupe_key="concurrent-job", payload=payload or {"request_id": self.task.pk}, owner=self.user)

    def test_competing_workers_execute_once_with_skip_locked_claims(self):
        job = self.enqueue()
        barrier = Barrier(2)
        def handler(**kwargs):
            TaskActivity.objects.create(task_id=self.task.pk, event_type="UPDATED", description="Concurrent committed job effect")
            return {"created": 1}
        def work(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return run_worker_once(worker_id=f"racer-{index}", limit=1)
            finally:
                connections.close_all()
        with patch("tasker.services.jobs._handler_for", return_value=handler):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(work, range(2)))
        self.assertEqual(sum(len(items) for items in results), 1)
        self.assertEqual(TaskActivity.objects.filter(task=self.task, description="Concurrent committed job effect").count(), 1)
        job.refresh_from_db()
        self.assertEqual(job.state, "SUCCEEDED")

    def test_competing_domain_events_create_one_durable_dedupe_row(self):
        barrier = Barrier(2)
        def enqueue():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return enqueue_job(kind="coordination.dispatch", dedupe_key="same-source-event",
                    payload={"request_id": self.task.pk}, owner=self.user.pk).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: enqueue(), range(2)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(OutboxJob.objects.count(), 1)

    def test_new_committed_operator_event_supersedes_an_inflight_refresh(self):
        old_revision, new_revision = uuid.uuid4(), uuid.uuid4()
        job = enqueue_operator_refresh(owner_id=self.user.pk, revision=old_revision)
        claimed = claim_next_job()
        effect_written, newer_event = Event(), Event()
        def handler(**kwargs):
            TaskActivity.objects.create(task_id=self.task.pk, event_type="UPDATED", description="Superseded operator effect")
            effect_written.set()
            if not newer_event.wait(timeout=10): raise AssertionError("New event was not committed")
            return {"reviewed": 1}
        def run_old():
            close_old_connections()
            try:
                return run_claimed_job(claimed)
            finally:
                connections.close_all()
        with patch("tasker.services.jobs._handler_for", return_value=handler):
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(run_old)
                self.assertTrue(effect_written.wait(timeout=10))
                enqueue_operator_refresh(owner_id=self.user.pk, revision=new_revision)
                newer_event.set()
                self.assertEqual(running.result(timeout=15)["outcome"], "superseded")
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("QUEUED", 0))
        self.assertEqual(job.payload["revision"], str(new_revision))
        self.assertFalse(TaskActivity.objects.filter(task=self.task, description="Superseded operator effect").exists())

    def test_stolen_lease_rolls_back_the_old_workers_partial_domain_write(self):
        job = self.enqueue()
        first = claim_next_job(worker_id="old-worker", lease_seconds=10)
        effect_written, lease_stolen = Event(), Event()
        def handler(**kwargs):
            TaskActivity.objects.create(task_id=self.task.pk, event_type="UPDATED", description="Stale worker effect")
            effect_written.set()
            if not lease_stolen.wait(timeout=10): raise AssertionError("Recovery worker did not reclaim lease")
            return {"changed": 1}
        def run_old():
            close_old_connections()
            try:
                return run_claimed_job(first)
            finally:
                connections.close_all()
        with patch("tasker.services.jobs._handler_for", return_value=handler):
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(run_old)
                self.assertTrue(effect_written.wait(timeout=10))
                OutboxJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone_now_minus_one())
                recovered = claim_next_job(worker_id="recovery-worker")
                self.assertIsNotNone(recovered)
                lease_stolen.set()
                self.assertEqual(running.result(timeout=15)["outcome"], "lost_lease")
        self.assertFalse(TaskActivity.objects.filter(task=self.task, description="Stale worker effect").exists())
        job.refresh_from_db()
        self.assertEqual(job.lease_token, recovered.lease_token)


def timezone_now_minus_one():
    from django.utils import timezone
    return timezone.now()-timedelta(seconds=1)
