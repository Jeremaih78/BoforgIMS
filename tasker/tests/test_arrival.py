"""Arrival declarations, self correction and current team read scope."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from accounting.models import Company
from tasker.arrival_models import ArrivalCorrection, ArrivalDeclaration
from tasker.models import Task, TaskTimeEntry, Team, TeamMembership
from tasker.services.arrival import (
    can_read_arrival, correct_arrival, current_business_zone, declared_datetime,
    register_arrival, today_arrival, visible_arrivals_for,
)


NOW = datetime(2026, 10, 27, 10, 0, tzinfo=datetime_timezone.utc)


class ArrivalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("arrival-employee")
        self.peer = get_user_model().objects.create_user("arrival-peer")
        self.manager = get_user_model().objects.create_user("arrival-manager", is_staff=True)
        permissions = Permission.objects.filter(content_type__app_label="tasker", codename__in=(
            "view_task", "assign_task", "change_task", "view_arrivaldeclaration", "view_arrivalcorrection"))
        for actor in (self.user, self.peer, self.manager):
            actor.user_permissions.set(permissions)
        self.company = Company.objects.create(name="Arrival company")
        self.team = Team.objects.create(company=self.company, name="Arrival team")
        self.membership = TeamMembership.objects.create(user=self.user, team=self.team)
        TeamMembership.objects.create(user=self.manager, team=self.team, role=TeamMembership.Role.MANAGER)
        self.client.force_login(self.user)

    def register(self, user=None, at=NOW):
        with patch("tasker.services.arrival.timezone.now", return_value=at):
            return register_arrival(user=user or self.user)[0]

    def correction(self, record, **values):
        values.setdefault("user", self.user)
        values.setdefault("arrival_id", record.pk)
        values.setdefault("effective_at", NOW - timedelta(minutes=45))
        values.setdefault("reason", "Forgot to register when I arrived")
        values.setdefault("client_key", uuid.uuid4())
        values.setdefault("expected_revision", record.revision)
        with patch("tasker.services.arrival.timezone.now", return_value=NOW):
            return correct_arrival(**values)

    def test_register_uses_server_time_and_repeated_day_is_idempotent(self):
        with patch("tasker.services.arrival.timezone.now", return_value=NOW):
            first, created = register_arrival(user=self.user)
            second, replay_created = register_arrival(user=self.user)
            self.assertEqual(today_arrival(self.user).pk, first.pk)
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.registered_at, NOW)
        self.assertEqual(first.local_date.isoformat(), "2026-10-27")
        self.assertEqual(first.timezone_name, "Africa/Harare")
        self.assertEqual(list(first.teams.all()), [self.team])
        self.assertEqual(ArrivalDeclaration.objects.count(), 1)

    def test_registration_next_local_day_creates_a_separate_original(self):
        first = self.register(at=datetime(2026, 10, 27, 21, 59, tzinfo=datetime_timezone.utc))
        next_day = self.register(at=datetime(2026, 10, 27, 22, 1, tzinfo=datetime_timezone.utc))
        self.assertNotEqual(first.pk, next_day.pk)
        self.assertEqual(next_day.local_date.isoformat(), "2026-10-28")

    def test_work_policy_timezone_is_used_without_creating_a_default_policy(self):
        policy_model = apps.get_model("tasker", "WorkPolicy")
        self.assertFalse(policy_model.objects.filter(owner=self.user).exists())
        self.assertEqual(current_business_zone(self.user).key, "Africa/Harare")
        self.assertFalse(policy_model.objects.filter(owner=self.user).exists())
        policy_model.objects.create(owner=self.user, timezone_name="Pacific/Auckland")
        record = self.register(at=datetime(2026, 10, 27, 12, 0, tzinfo=datetime_timezone.utc))
        self.assertEqual(record.timezone_name, "Pacific/Auckland")
        self.assertEqual(record.local_date.isoformat(), "2026-10-28")

    def test_arrival_does_not_start_timers_change_tasks_or_derive_hours(self):
        task = Task.objects.create(title="Real work", owner=self.user)
        self.register()
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertIsNone(task.actual_duration)
        self.assertFalse(TaskTimeEntry.objects.exists())
        fields = {field.name for field in ArrivalDeclaration._meta.fields}
        self.assertNotIn("hours_worked", fields)
        self.assertNotIn("productivity", fields)

    def test_post_ignores_forged_employee_and_timestamp_and_get_cannot_register(self):
        url = reverse("ims:tasker:arrival_register")
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch("tasker.services.arrival.timezone.now", return_value=NOW):
            response = self.client.post(url, {"user": self.peer.pk, "registered_at": "2020-01-01T08:00:00Z"})
        self.assertEqual(response.status_code, 302)
        record = ArrivalDeclaration.objects.get()
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.registered_at, NOW)

    def test_correction_preserves_original_and_has_attributable_reason(self):
        record = self.register()
        correction = self.correction(record)
        record.refresh_from_db()
        self.assertEqual(record.registered_at, NOW)
        self.assertEqual(record.effective_at, NOW - timedelta(minutes=45))
        self.assertEqual(correction.actor, self.user)
        self.assertEqual(correction.created_at, NOW)
        self.assertEqual(correction.reason, "Forgot to register when I arrived")
        self.assertEqual(record.revision, correction.pk)
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            correction.save()
        with self.assertRaises(ValidationError):
            correction.delete()

    def test_future_out_of_day_blank_and_naive_corrections_are_rejected(self):
        record = self.register()
        for overrides in ({"effective_at": NOW + timedelta(seconds=1)},
                          {"effective_at": NOW - timedelta(days=1)},
                          {"reason": "   "}, {"effective_at": NOW.replace(tzinfo=None)}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.correction(record, **overrides)
        self.assertFalse(ArrivalCorrection.objects.exists())

    def test_correction_retry_reuses_identity_but_conflict_and_stale_revision_fail(self):
        record = self.register()
        key = uuid.uuid4()
        first = self.correction(record, client_key=key, expected_revision=0)
        replay = self.correction(record, client_key=key, expected_revision=0)
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaises(ValidationError):
            self.correction(record, client_key=key, reason="Changed payload", expected_revision=0)
        with self.assertRaisesMessage(ValidationError, "another window"):
            self.correction(record, expected_revision=0)
        self.assertEqual(ArrivalCorrection.objects.count(), 1)

    def test_neither_manager_nor_peer_can_correct_another_employee(self):
        record = self.register()
        for actor in (self.manager, self.peer):
            with self.subTest(actor=actor.pk), self.assertRaises(PermissionDenied):
                self.correction(record, user=actor)
            self.client.force_login(actor)
            self.assertEqual(self.client.post(reverse("ims:tasker:arrival_correct", args=[record.pk])).status_code, 404)
        self.assertFalse(ArrivalCorrection.objects.exists())

    def test_team_reader_needs_recorded_and_current_membership(self):
        record = self.register()
        self.assertTrue(can_read_arrival(self.manager, record))
        self.assertEqual(list(visible_arrivals_for(self.manager, team=self.team)), [record])
        self.assertFalse(can_read_arrival(self.peer, record))
        with self.assertRaises(PermissionDenied):
            visible_arrivals_for(self.peer, team=self.team)
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertFalse(can_read_arrival(self.manager, record))
        self.assertFalse(visible_arrivals_for(self.manager, team=self.team).exists())
        self.assertTrue(can_read_arrival(self.user, record))

    def test_new_manager_cannot_see_history_from_before_team_membership(self):
        record = self.register()
        another = Team.objects.create(company=self.company, name="New team")
        TeamMembership.objects.create(user=self.user, team=another)
        TeamMembership.objects.create(user=self.peer, team=another, role=TeamMembership.Role.MANAGER)
        self.assertFalse(can_read_arrival(self.peer, record))
        self.assertFalse(visible_arrivals_for(self.peer, team=another).exists())

    def test_manager_page_shows_authorized_correction_history_without_edit_control(self):
        record = self.register()
        self.correction(record)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("ims:tasker:arrival_home"), {"team": self.team.pk, "date": "2026-10-27"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forgot to register when I arrived")
        self.assertContains(response, "Original server registration")
        self.assertNotContains(response, "Correct my declared time")
        self.client.force_login(self.peer)
        self.assertEqual(self.client.get(reverse("ims:tasker:arrival_home"), {"team": self.team.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("ims:tasker:arrival_home"), {"team": "invalid"}).status_code, 404)

    def test_correction_http_keeps_errors_and_original_until_success(self):
        record = self.register()
        url = reverse("ims:tasker:arrival_correct", args=[record.pk])
        prefix = f"arrival-{record.pk}-"
        data = {prefix + "declared_time": "11:15:00", prefix + "reason": "A deliberate correction",
                prefix + "expected_revision": "0", prefix + "client_key": str(uuid.uuid4())}
        with patch("tasker.services.arrival.timezone.now", return_value=NOW):
            invalid = self.client.post(url, {**data, prefix + "reason": ""})
            self.assertEqual(invalid.status_code, 400)
            self.assertContains(invalid, 'value="11:15:00"', status_code=400)
            self.assertFalse(ArrivalCorrection.objects.exists())
            valid = self.client.post(url, data)
        self.assertEqual(valid.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.effective_at.hour, 9)
        self.assertEqual(record.effective_at.minute, 15)
        self.assertEqual(record.registered_at, NOW)

    def test_revoked_capability_and_inactive_identity_cannot_declare_or_correct(self):
        record = self.register()
        permission = Permission.objects.get(content_type__app_label="tasker", codename="view_task")
        self.user.user_permissions.remove(permission)
        with self.assertRaises(PermissionDenied):
            self.register()
        with self.assertRaises(PermissionDenied):
            self.correction(record)
        self.user.user_permissions.add(permission)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.register()

    def test_admin_is_scoped_and_read_only(self):
        record = self.register()
        correction = self.correction(record)
        request = RequestFactory().get("/admin/")
        request.user = self.manager
        for model, obj in ((ArrivalDeclaration, record), (ArrivalCorrection, correction)):
            model_admin = admin.site._registry[model]
            self.assertTrue(model_admin.has_view_permission(request, obj))
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request, obj))
            self.assertFalse(model_admin.has_delete_permission(request, obj))
            self.assertTrue(model_admin.get_queryset(request).filter(pk=obj.pk).exists())
            self.assertNotIn("teams", model_admin.get_readonly_fields(request, obj))
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertFalse(admin.site._registry[ArrivalDeclaration].get_queryset(request).exists())


class ArrivalConcurrencyTests(TransactionTestCase):
    def test_simultaneous_same_employee_registration_creates_one_declaration(self):
        user = get_user_model().objects.create_user("arrival-concurrent")
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
        barrier = Barrier(2)

        def declare():
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                actor = get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=5)
                record, created = register_arrival(user=actor)
                return record.pk, created
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=15) for future in [pool.submit(declare), pool.submit(declare)]]
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(value[1] for value in results), [False, True])
        self.assertEqual(ArrivalDeclaration.objects.count(), 1)

    def test_concurrent_corrections_from_one_revision_cannot_overwrite_each_other(self):
        user = get_user_model().objects.create_user("arrival-correction-concurrent")
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
        record, _created = register_arrival(user=user)
        barrier = Barrier(2)

        def correct(reason):
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                actor = get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=5)
                try:
                    correct_arrival(user=actor, arrival_id=record.pk, effective_at=record.registered_at,
                        reason=reason, client_key=uuid.uuid4(), expected_revision=0)
                    return "recorded"
                except ValidationError as exc:
                    if "another window" not in str(exc):
                        raise
                    return "conflict"
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=15) for future in [pool.submit(correct, "First reason"), pool.submit(correct, "Second reason")]]
        self.assertEqual(sorted(results), ["conflict", "recorded"])
        self.assertEqual(ArrivalCorrection.objects.count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.registered_at, record.effective_at)
