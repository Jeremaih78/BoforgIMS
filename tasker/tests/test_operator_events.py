"""Domain mutations expire advice without copying content or creating a queue."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Barrier, Event
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase

from accounting.models import Company
from tasker.models import (
    Availability, DailyPlan, DailyReview, Task, TaskChecklistItem, TaskDependency,
    TaskTimeEntry, Team, TeamMembership, WorkPolicy,
)
from tasker.operator_events import invalidate_operator, task_changed
from tasker.operator_models import OperatorState


class OperatorEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("operator-event-owner")
        cls.peer = get_user_model().objects.create_user("operator-event-peer")
        cls.owner_state = OperatorState.objects.create(owner=cls.owner)
        cls.peer_state = OperatorState.objects.create(owner=cls.peer)

    def revisions(self):
        return dict(OperatorState.objects.values_list("owner_id", "revision"))

    def task(self, **changes):
        return Task.objects.create(owner=self.owner, title="Operator event sample", **changes)

    def at(self, hour):
        return datetime(2030, 1, 7, hour, tzinfo=ZoneInfo("Africa/Harare"))

    def assert_owners_changed(self, before, expected):
        after = self.revisions()
        changed = {owner_id for owner_id in before if before[owner_id] != after[owner_id]}
        self.assertEqual(changed, expected)

    def test_invalidation_updates_only_existing_requested_states_in_one_query(self):
        before = self.revisions()
        with self.assertNumQueries(1):
            count = invalidate_operator(user_ids=[self.owner.pk, None, "2", True, -1], reason="availability_changed")
        self.assertEqual(count, 1)
        self.assert_owners_changed(before, {self.owner.pk})
        with self.assertNumQueries(0):
            self.assertEqual(invalidate_operator(user_ids=[], reason="plan_changed"), 0)
        self.assertEqual(OperatorState.objects.count(), 2)

    def test_arbitrary_content_cannot_be_copied_into_invalidation_evidence(self):
        invalidate_operator(user_ids=[self.owner.pk], reason="private unavailable note: medical appointment", task_id=999999)
        self.owner_state.refresh_from_db()
        self.assertEqual(self.owner_state.reason, "work_changed")

    def test_no_state_or_queue_is_created_by_mutating_work(self):
        OperatorState.objects.all().delete()
        self.task()
        self.assertEqual(invalidate_operator(reason="manual_refresh"), 0)
        self.assertFalse(OperatorState.objects.exists())

    def test_notes_only_save_keeps_advice_but_deadline_and_priority_expire_it(self):
        task = self.task()
        before = self.revisions()
        task.notes = "Private draft content is irrelevant to ranking"
        task.save(update_fields=["notes", "updated_at"])
        self.assert_owners_changed(before, set())
        for field, value in (("due_date", date(2030, 1, 8)), ("priority", Task.Priority.CRITICAL)):
            before = self.revisions()
            setattr(task, field, value)
            task.save(update_fields=[field, "updated_at"])
            self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})

    def test_unsaved_task_fields_do_not_expire_notes_only_write(self):
        task = self.task()
        before = self.revisions()
        task.priority = Task.Priority.CRITICAL
        task.notes = "A notes-only persisted edit"
        task.save(update_fields=["notes", "updated_at"])
        self.assert_owners_changed(before, set())

    def test_block_and_assignment_expire_transitive_and_former_owner_advice(self):
        task = self.task()
        before = self.revisions()
        task.status = Task.Status.BLOCKED
        task.blocked_reason = "Sample blocker, not stored in state"
        task.save(update_fields=["status", "blocked_reason", "updated_at"])
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})
        before = self.revisions()
        task.owner = self.peer
        task.save(update_fields=["owner", "updated_at"])
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})
        self.assertEqual(set(OperatorState.objects.values_list("reason", flat=True)), {"task_changed"})

    def test_work_hours_and_private_availability_are_owner_scoped(self):
        before = self.revisions()
        WorkPolicy.objects.create(owner=self.owner, configured=True, buffer_minutes=0,
                                  start_time=time(8), end_time=time(16))
        self.assert_owners_changed(before, {self.owner.pk})
        before = self.revisions()
        item = Availability.objects.create(owner=self.owner, kind=Availability.Kind.LEAVE,
            starts_at=self.at(9), ends_at=self.at(10), private_note="Do not copy this unavailable note")
        self.assert_owners_changed(before, {self.owner.pk})
        before = self.revisions()
        item.delete(user=self.owner)
        self.assert_owners_changed(before, {self.owner.pk})
        self.owner_state.refresh_from_db()
        self.assertEqual(self.owner_state.reason, "availability_changed")

    def test_plan_membership_add_remove_and_reverse_clear_expire_owner(self):
        task = self.task()
        plan = DailyPlan.objects.create(owner=self.owner, plan_date=date(2030, 1, 7))
        for relation in ("top_priorities", "planned_tasks"):
            before = self.revisions()
            getattr(plan, relation).add(task)
            self.assert_owners_changed(before, {self.owner.pk})
            before = self.revisions()
            getattr(plan, relation).remove(task)
            self.assert_owners_changed(before, {self.owner.pk})
            getattr(plan, relation).add(task)
            before = self.revisions()
            reverse = task.priority_daily_plans if relation == "top_priorities" else task.daily_plans
            reverse.clear()
            self.assert_owners_changed(before, {self.owner.pk})

    def test_reverse_review_clear_captures_owner_before_links_disappear(self):
        task = self.task()
        plan = DailyPlan.objects.create(owner=self.owner, plan_date=date(2030, 1, 7))
        review = DailyReview.objects.create(plan=plan, lessons_learned="Private reflection")
        review.carry_over_tasks.add(task)
        before = self.revisions()
        task.daily_reviews_carried_over.clear()
        self.assert_owners_changed(before, {self.owner.pk})
        self.owner_state.refresh_from_db()
        self.assertEqual(self.owner_state.reason, "review_completed")

    def test_dependency_creation_and_soft_removal_expire_other_owners(self):
        dependent, prerequisite = self.task(), self.task()
        before = self.revisions()
        edge = TaskDependency.objects.create(task=dependent, prerequisite=prerequisite)
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})
        before = self.revisions()
        edge.delete(user=self.owner)
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})

    def test_timer_and_first_checklist_action_invalidate_conservatively_before_task_writes(self):
        task = self.task()
        before = self.revisions()
        TaskTimeEntry.objects.create(task=task, user=self.owner, started_at=self.at(9),
                                    ended_at=self.at(10), duration=timedelta(hours=1))
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})
        before = self.revisions()
        TaskChecklistItem.objects.create(task=task, title="A changed first action")
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})

    def test_membership_and_permission_revocation_expire_scope_proofs(self):
        company = Company.objects.create(name="Operator event company")
        team = Team.objects.create(company=company, name="Operator event team")
        membership = TeamMembership.objects.create(team=team, user=self.owner)
        before = self.revisions()
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})
        permission = Permission.objects.get(content_type__app_label="tasker", codename="view_task")
        self.owner.user_permissions.add(permission)
        before = self.revisions()
        self.owner.user_permissions.remove(permission)
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})

    def test_rolled_back_domain_change_does_not_leave_false_invalidation(self):
        task = self.task()
        before = self.revisions()
        with self.assertRaises(ValueError):
            with transaction.atomic():
                task.priority = Task.Priority.CRITICAL
                task.save(update_fields=["priority", "updated_at"])
                self.assertNotEqual(self.revisions(), before)
                raise ValueError("Roll back the sample mutation")
        self.assert_owners_changed(before, set())

    def test_maintenance_bulk_update_has_an_explicit_invalidation_seam(self):
        task = self.task()
        before = self.revisions()
        Task.objects.filter(pk=task.pk).update(priority=Task.Priority.CRITICAL)
        self.assert_owners_changed(before, set())
        invalidate_operator(reason="task_changed", task_id=task.pk)
        self.assert_owners_changed(before, {self.owner.pk, self.peer.pk})

    def test_raw_fixture_signal_does_not_invalidate_live_advice(self):
        task = self.task()
        before = self.revisions()
        task_changed(sender=Task, instance=task, created=True, raw=True)
        self.assert_owners_changed(before, set())

    def test_review_rechecks_selected_owner_before_any_review_write(self):
        from tasker.forms import DailyReviewForm
        from tasker.services.planner import save_daily_review
        self.owner.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker",
            codename__in=("view_dailyreview", "add_dailyreview", "view_task")))
        now = self.at(10)
        with patch("django.utils.timezone.now", return_value=now):
            task = self.task(due_date=now.date())
            plan = DailyPlan.objects.create(owner=self.owner, plan_date=now.date())
            form = DailyReviewForm({"energy_level": 3, "carry_over_tasks": [task.pk]}, user=self.owner, plan=plan)
            self.assertTrue(form.is_valid(), form.errors)
            Task.objects.filter(pk=task.pk).update(owner=self.peer)
            before = self.revisions()
            with self.assertRaises(PermissionDenied):
                save_daily_review(form=form, user=self.owner, plan=plan)
            self.assertFalse(DailyReview.objects.exists())
            self.assert_owners_changed(before, set())
            self.assertFalse(Task.objects.get(pk=task.pk).carry_forward)

    def test_review_rechecks_current_capability_instead_of_cached_user_permissions(self):
        from tasker.forms import DailyReviewForm
        from tasker.services.planner import save_daily_review
        permissions = Permission.objects.filter(content_type__app_label="tasker",
            codename__in=("view_dailyreview", "add_dailyreview", "view_task"))
        self.owner.user_permissions.add(*permissions)
        self.assertTrue(self.owner.has_perm("tasker.add_dailyreview"))
        with patch("django.utils.timezone.now", return_value=self.at(10)):
            plan = DailyPlan.objects.create(owner=self.owner, plan_date=self.at(10).date())
            form = DailyReviewForm({"energy_level": 3}, user=self.owner, plan=plan)
            self.assertTrue(form.is_valid(), form.errors)
            self.owner.user_permissions.remove(permissions.get(codename="add_dailyreview"))
            before = self.revisions()
            with self.assertRaises(PermissionDenied):
                save_daily_review(form=form, user=self.owner, plan=plan)
            self.assertFalse(DailyReview.objects.exists())
            self.assert_owners_changed(before, set())


class OperatorEventConcurrencyTests(TransactionTestCase):
    """Independent task writers must never upgrade scoped state locks."""

    def test_two_employees_can_log_time_without_state_lock_upgrade_deadlock(self):
        from tasker.organization import require_task_access
        from tasker.services.tasks import log_time
        users = [get_user_model().objects.create_user(f"operator-event-concurrent-{index}") for index in range(2)]
        for user in users:
            user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
            OperatorState.objects.create(owner=user)
        tasks = [Task.objects.create(owner=user, title=f"Independent sample work {index}") for index, user in enumerate(users)]
        barrier = Barrier(2)

        def synchronized_access(user, task, **kwargs):
            result = require_task_access(user, task, **kwargs)
            barrier.wait(timeout=10)
            return result

        def write(index):
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                actor = get_user_model().objects.get(pk=users[index].pk)
                task = Task.objects.get(pk=tasks[index].pk)
                entry = log_time(task=task, user=actor, started_at=datetime(2030, 1, 7, 9,
                    tzinfo=ZoneInfo("Africa/Harare")), duration_minutes=15)
                return entry.pk
            finally:
                connections.close_all()

        with patch("tasker.services.tasks.require_task_access", side_effect=synchronized_access):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(write, range(2)))
        self.assertEqual(len(set(results)), 2)
        self.assertEqual(TaskTimeEntry.objects.count(), 2)
        for task in tasks:
            task.refresh_from_db()
            self.assertEqual(task.actual_duration, timedelta(minutes=15))

    def test_review_locks_carry_over_task_before_invalidating_its_recommendation(self):
        from tasker.operator_events import invalidate_operator as actual_invalidation
        from tasker.services.planner import save_daily_review
        user = get_user_model().objects.create_user("operator-event-review-race")
        user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        OperatorState.objects.create(owner=user)
        now = datetime(2030, 1, 7, 10, tzinfo=ZoneInfo("Africa/Harare"))
        with patch("django.utils.timezone.now", return_value=now):
            task = Task.objects.create(owner=user, title="Sample carry-over work", due_date=now.date())
            plan = DailyPlan.objects.create(owner=user, plan_date=now.date())
            task_locked, review_started, review_invalidated = Event(), Event(), Event()

            def observe_invalidation(**kwargs):
                result = actual_invalidation(**kwargs)
                if kwargs.get("reason") == "review_completed":
                    review_invalidated.set()
                return result

            def set_timeout():
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")

            def edit_task():
                close_old_connections()
                try:
                    set_timeout()
                    with transaction.atomic():
                        current = Task.objects.select_for_update().get(pk=task.pk)
                        task_locked.set()
                        if not review_started.wait(timeout=10):
                            raise AssertionError("Review did not start")
                        # Older code saved Review -> operator state before
                        # touching Task. Let that order expose its cycle; fixed
                        # code waits on Task first, so this brief wait expires.
                        review_invalidated.wait(timeout=0.5)
                        current.priority = Task.Priority.HIGH
                        current.save(update_fields=["priority", "updated_at"])
                    return "edited"
                finally:
                    connections.close_all()

            def submit_review():
                close_old_connections()
                try:
                    set_timeout()
                    if not task_locked.wait(timeout=10):
                        raise AssertionError("Task writer did not start")
                    actor = get_user_model().objects.get(pk=user.pk)
                    current_plan = DailyPlan.objects.get(pk=plan.pk)
                    current_task = Task.objects.get(pk=task.pk)

                    class ReviewedForm:
                        cleaned_data = {"carry_over_tasks": [current_task]}

                        def save(self, commit=False):
                            self.instance = DailyReview(plan=current_plan, energy_level=3)
                            return self.instance

                        def save_m2m(self):
                            self.instance.carry_over_tasks.set([current_task])

                    review_started.set()
                    save_daily_review(form=ReviewedForm(), user=actor, plan=current_plan)
                    return "reviewed"
                finally:
                    connections.close_all()

            with patch("tasker.operator_events.invalidate_operator", side_effect=observe_invalidation):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    edit = pool.submit(edit_task)
                    review = pool.submit(submit_review)
                    self.assertEqual({edit.result(timeout=15), review.result(timeout=15)}, {"edited", "reviewed"})
        task.refresh_from_db()
        self.assertTrue(task.carry_forward)
        self.assertEqual(task.priority, Task.Priority.HIGH)
        self.assertEqual(DailyReview.objects.filter(plan=plan).count(), 1)
