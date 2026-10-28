"""Capture HTTP, privacy and retained-draft contracts using synthetic content."""

import json
import uuid
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounting.models import Company
from tasker.models import CaptureItem, DailyPlan, Task, Team, TeamMembership
from tasker.services.captures import create_capture


@override_settings(TASKER_AI_OPENAI_API_KEY="")
class CapturePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user("capture-page-owner")
        cls.member = users.objects.create_user("capture-page-member")
        cls.outsider = users.objects.create_user("capture-page-outsider")
        cls.no_access = users.objects.create_user("capture-page-no-access")
        permissions = Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("view_task", "add_task", "change_task", "view_dailyplan"),
        )
        for user in (cls.owner, cls.member, cls.outsider):
            user.user_permissions.add(*permissions)
        cls.company = Company.objects.create(name="Capture page sample company")
        cls.team = Team.objects.create(company=cls.company, name="Capture page sample team")
        cls.owner_membership = TeamMembership.objects.create(team=cls.team, user=cls.owner)
        cls.member_membership = TeamMembership.objects.create(team=cls.team, user=cls.member)

    def setUp(self):
        self.client.force_login(self.owner)

    def url(self, name, **kwargs):
        return reverse(f"ims:tasker:{name}", kwargs=kwargs)

    def payload(self, **changes):
        data = {
            "client_key": str(uuid.uuid4()), "kind": "IDEA", "title": "Private page idea",
            "content": "Private original capture", "url": "", "tags": "planning, ideas",
        }
        data.update(changes)
        return data

    def capture(self, **changes):
        data = {
            "user": self.owner, "client_key": uuid.uuid4(), "kind": "IDEA",
            "title": "Private page idea", "content": "Private original capture",
        }
        data.update(changes)
        return create_capture(**data)

    def save(self, item, **changes):
        data = {
            "version": item.version, "kind": item.kind, "title": item.title,
            "content": item.content, "url": item.url, "tags": item.tags, "pinned": item.pinned,
        }
        data.update(changes)
        return self.client.post(
            self.url("capture_save", pk=item.pk), json.dumps(data), content_type="application/json",
        )

    def manual_save(self, item, **changes):
        data = {
            "version": item.version, "kind": item.kind, "title": item.title,
            "content": item.content, "url": item.url, "tags": ",".join(item.tags), "pinned": item.pinned,
        }
        data.update(changes)
        return self.client.post(self.url("capture_save", pk=item.pk), data)

    def action(self, item, action, **changes):
        data = {"version": item.version}
        data.update(changes)
        return self.client.post(self.url("capture_action", pk=item.pk, action=action), data)

    def assert_private_denial(self, response):
        self.assertIn(response.status_code, (403, 404))

    def test_login_and_tasker_capability_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url("capture_list")).status_code, 302)
        self.client.force_login(self.no_access)
        self.assertEqual(self.client.get(self.url("capture_list")).status_code, 403)
        self.assertEqual(self.client.post(self.url("capture_create"), self.payload()).status_code, 403)
        self.assertEqual(CaptureItem.objects.count(), 0)

    def test_each_capture_type_is_private_and_unscheduled_without_ai(self):
        task_count, plan_count = Task.objects.count(), DailyPlan.objects.count()
        for kind in ("TASK", "IDEA", "NOTE", "LINK"):
            with self.subTest(kind=kind):
                payload = self.payload(kind=kind, title=f"Saved {kind}")
                if kind == "LINK":
                    payload["url"] = "https://example.com/synthetic-reference"
                response = self.client.post(self.url("capture_create"), payload)
                self.assertEqual(response.status_code, 302)
                item = CaptureItem.objects.get(client_key=payload["client_key"])
                self.assertEqual(item.kind, kind)
                self.assertEqual(item.owner_id, self.owner.pk)
                self.assertEqual(item.visibility, "PRIVATE")
                self.assertEqual(item.triage_state, "INBOX")
                self.assertEqual(item.original_text, payload["content"])
                self.assertIsNone(item.shared_team_id)
                self.assertIsNone(item.promoted_task_id)
                self.assertIsNone(item.promoted_initiative_id)
        self.assertEqual(Task.objects.count(), task_count)
        self.assertEqual(DailyPlan.objects.count(), plan_count)

    def test_double_submit_creates_one_capture_and_replays_its_destination(self):
        payload = self.payload()
        first = self.client.post(self.url("capture_create"), payload)
        second = self.client.post(self.url("capture_create"), payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.url, second.url)
        self.assertEqual(CaptureItem.objects.filter(client_key=payload["client_key"]).count(), 1)

    def test_reused_capture_key_cannot_replace_a_different_original(self):
        payload = self.payload()
        self.client.post(self.url("capture_create"), payload)
        response = self.client.post(self.url("capture_create"), {**payload, "content": "Different retained draft"})
        self.assertIn(response.status_code, (200, 400, 409))
        self.assertContains(response, "Different retained draft", status_code=response.status_code)
        item = CaptureItem.objects.get(client_key=payload["client_key"])
        self.assertEqual(item.original_text, "Private original capture")
        self.assertEqual(item.content, "Private original capture")

    def test_invalid_capture_retains_draft_and_link_validation_is_safe(self):
        payload = self.payload(kind="LINK", title="Retain this draft", url="javascript:alert(1)")
        response = self.client.post(self.url("capture_create"), payload)
        self.assertIn(response.status_code, (200, 400))
        self.assertContains(response, "Retain this draft", status_code=response.status_code)
        self.assertContains(response, "Private original capture", status_code=response.status_code)
        self.assertFalse(CaptureItem.objects.filter(client_key=payload["client_key"]).exists())

    def test_editor_save_is_versioned_and_original_text_stays_immutable(self):
        item = self.capture()
        old_version = item.version
        response = self.save(item, content="Saved from first tab")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertGreater(response.json()["version"], old_version)
        stale = self.save(item, content="Retain stale tab draft")
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(stale.json()["conflict"])
        self.assertEqual(stale.json()["item"]["content"], "Saved from first tab")
        item.refresh_from_db()
        self.assertEqual(item.content, "Saved from first tab")
        self.assertEqual(item.original_text, "Private original capture")

    def test_uncertain_identical_save_retry_does_not_increment_version_again(self):
        item = self.capture()
        first = self.save(item, content="One persisted edit")
        retry = self.save(item, content="One persisted edit")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.json()["version"], retry.json()["version"])
        item.refresh_from_db()
        self.assertEqual(item.content, "One persisted edit")

    def test_browser_newline_variants_retry_as_one_canonical_edit(self):
        item = self.capture()
        first = self.save(item, content="Line one\r\nLine two\rLine three")
        retry = self.save(item, content="Line one\nLine two\nLine three")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.json()["version"], retry.json()["version"])
        item.refresh_from_db()
        self.assertEqual(item.content, "Line one\nLine two\nLine three")
        self.assertEqual(item.original_text, "Private original capture")

    def test_json_shape_and_unsafe_link_fail_without_mutating_the_capture(self):
        item = self.capture()
        response = self.client.post(self.url("capture_save", pk=item.pk), "[]", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.save(item, kind="LINK", url="javascript:alert(1)", content="Unsaved draft")
        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.content, "Private original capture")

    def test_json_rejects_malformed_tag_shapes_without_mutating_the_capture(self):
        item = self.capture()
        for tags in ([{"unexpected": "object"}], [1], {"key": "value"}, ["one,two"], None):
            with self.subTest(tags=tags):
                response = self.save(item, tags=tags, content="Unpersisted malformed-tag draft")
                self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.content, "Private original capture")
        self.assertEqual(item.tags, [])

    def test_manual_form_save_works_without_javascript_and_stale_draft_is_retained(self):
        item = self.capture()
        first = self.manual_save(item, content="Saved without JavaScript")
        self.assertEqual(first.status_code, 302)
        stale = self.manual_save(item, content="My retained manual draft")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.context["form"]["content"].value(), "My retained manual draft")
        self.assertEqual(stale.context["item"].content, "Saved without JavaScript")
        self.assertContains(stale, "My retained manual draft", status_code=409)
        current = CaptureItem.objects.get(pk=item.pk)
        self.assertEqual(current.content, "Saved without JavaScript")
        resolved = self.manual_save(current, content="My retained manual draft")
        self.assertEqual(resolved.status_code, 302)
        current.refresh_from_db()
        self.assertEqual(current.content, "My retained manual draft")
        self.assertEqual(current.original_text, "Private original capture")

    def test_private_capture_pages_disallow_response_caching(self):
        item = self.capture()
        for name, kwargs in (
            ("capture_list", {}), ("capture_create", {}),
            ("capture_detail", {"pk": item.pk}), ("capture_legacy", {}),
        ):
            with self.subTest(page=name):
                response = self.client.get(self.url(name, **kwargs))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_mutations_are_post_only_and_csrf_protected(self):
        item = self.capture()
        self.assertEqual(self.client.get(self.url("capture_save", pk=item.pk)).status_code, 405)
        self.assertEqual(self.client.get(self.url("capture_action", pk=item.pk, action="archive")).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url("capture_action", pk=item.pk, action="archive"), {"version": item.version}).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.triage_state, "INBOX")

    def test_capture_text_is_rendered_as_text_instead_of_executable_markup(self):
        marker = '<script data-capture-synthetic="true">alert(1)</script>'
        item = self.capture(title="Escaped capture reference", content=marker)
        response = self.client.get(self.url("capture_detail", pk=item.pk))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, marker)
        self.assertContains(response, "&lt;script")

    def test_private_capture_is_absent_from_foreign_search_and_direct_urls(self):
        item = self.capture(title="PRIVATE capture searchable marker")
        self.client.force_login(self.member)
        page = self.client.get(self.url("capture_list"), {"q": "PRIVATE capture searchable marker"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["page"].paginator.count, 0)
        self.assertNotContains(page, "Private original capture")
        self.assert_private_denial(self.client.get(self.url("capture_detail", pk=item.pk)))
        self.assert_private_denial(self.save(item, content="Forged member edit"))
        self.assert_private_denial(self.action(item, "share", team=self.team.pk))
        item.refresh_from_db()
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertEqual(item.content, "Private original capture")

    def test_deliberate_team_share_is_read_only_and_revocation_removes_access(self):
        item = self.capture(title="Explicitly shared capture")
        self.assertEqual(self.action(item, "share", team=self.team.pk).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.visibility, "TEAM")
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url("capture_detail", pk=item.pk)).status_code, 200)
        self.assertContains(self.client.get(self.url("capture_list"), {"q": "Explicitly shared capture"}), "Explicitly shared capture")
        self.assert_private_denial(self.save(item, content="Forbidden coworker edit"))
        self.assert_private_denial(self.action(item, "delete"))
        TeamMembership.objects.filter(pk=self.member_membership.pk).update(is_active=False)
        self.assert_private_denial(self.client.get(self.url("capture_detail", pk=item.pk)))
        revoked_search = self.client.get(self.url("capture_list"), {"q": "Explicitly shared capture"})
        self.assertEqual(revoked_search.context["page"].paginator.count, 0)
        self.assertNotContains(revoked_search, "Private original capture")
        item.refresh_from_db()
        self.assertEqual(item.content, "Private original capture")

    def test_search_tag_pin_archive_and_restore_filters_do_not_lose_the_record(self):
        item = self.capture(title="Find later reference", tags=["research", "printer"])
        self.capture(title="Another unrelated idea", tags=["other"])
        response = self.client.get(self.url("capture_list"), {"q": "Find later", "tag": "research", "kind": "IDEA"})
        self.assertContains(response, "Find later reference")
        self.assertNotContains(response, "Another unrelated idea")
        self.assertEqual(self.action(item, "pin").status_code, 302)
        item.refresh_from_db()
        self.assertContains(self.client.get(self.url("capture_list"), {"pinned": "1"}), "Find later reference")
        self.assertEqual(self.action(item, "archive").status_code, 302)
        item.refresh_from_db()
        self.assertNotContains(self.client.get(self.url("capture_list")), "Find later reference")
        self.assertContains(self.client.get(self.url("capture_list"), {"state": "archive"}), "Find later reference")
        self.assertEqual(self.action(item, "unarchive").status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.triage_state, "INBOX")

    def test_recoverable_delete_and_restore_remain_owner_scoped(self):
        item = self.capture(title="Recover this capture")
        self.assertEqual(self.action(item, "delete").status_code, 302)
        deleted = CaptureItem.all_objects.get(pk=item.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertFalse(CaptureItem.objects.filter(pk=item.pk).exists())
        self.assertContains(self.client.get(self.url("capture_list"), {"state": "trash"}), "Recover this capture")
        self.client.force_login(self.member)
        self.assert_private_denial(self.action(deleted, "restore"))
        self.client.force_login(self.owner)
        self.assertEqual(self.action(deleted, "restore").status_code, 302)
        restored = CaptureItem.objects.get(pk=item.pk)
        self.assertFalse(restored.is_deleted)
        self.assertEqual(restored.original_text, "Private original capture")

    def test_task_promotion_replay_reuses_one_personal_unscheduled_destination(self):
        item = self.capture(title="Promote exactly once")
        task_count = Task.objects.count()
        data = {
            "version": item.version, "target": "task", "title": "One promoted work record",
            "confirm_copy": "on", "team": "", "due_date": "", "goal": "",
        }
        promote_url = self.url("capture_promote", pk=item.pk)
        first = self.client.post(promote_url, data)
        replay = self.client.post(promote_url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(first.url, replay.url)
        item.refresh_from_db()
        task = Task.objects.get(pk=item.promoted_task_id)
        self.assertEqual(Task.objects.count(), task_count + 1)
        self.assertIsNone(task.due_date)
        self.assertIsNone(task.team_id)
        self.assertIsNone(task.company_id)
        self.assertIn(self.url("capture_detail", pk=item.pk), task.description)
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertEqual(item.original_text, "Private original capture")
        owner_work = self.client.get(self.url("task_detail", slug=task.slug))
        self.assertEqual(owner_work.status_code, 200)
        self.assertEqual(owner_work.context["source_capture"].pk, item.pk)
        self.assertContains(owner_work, f'href="{self.url("capture_detail", pk=item.pk)}"')

    def test_shared_promoted_work_copies_selected_text_without_exposing_private_source(self):
        item = self.capture(title="Private retained source", content="private retained marker")
        self.assertEqual(self.save(item, content="public selected work text").status_code, 200)
        item.refresh_from_db()
        due_date = timezone.localdate() + timedelta(days=5)
        response = self.client.post(self.url("capture_promote", pk=item.pk), {
            "version": item.version, "target": "task", "title": "Deliberately shared work",
            "confirm_copy": "on", "team": self.team.pk, "due_date": due_date.isoformat(), "goal": "",
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        task = Task.objects.get(pk=item.promoted_task_id)
        self.assertEqual(task.due_date, due_date)
        self.assertEqual(task.team_id, self.team.pk)
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertIsNone(item.shared_team_id)
        self.assertEqual(item.original_text, "private retained marker")
        self.assertNotIn("private retained marker", task.description)
        # An authorized team reader follows Stage 2 task scope; employee
        # membership alone does not expose another employee's assigned tasks.
        self.member_membership.role = "MANAGER"
        self.member_membership.save(update_fields=["role"])
        self.member.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="assign_task"))
        self.client.force_login(self.member)
        shared_work = self.client.get(self.url("task_detail", slug=task.slug))
        self.assertEqual(shared_work.status_code, 200)
        self.assertContains(shared_work, "public selected work text")
        self.assertNotContains(shared_work, "private retained marker")
        self.assertIsNone(shared_work.context["source_capture"])
        self.assertNotContains(shared_work, f'href="{self.url("capture_detail", pk=item.pk)}"')
        self.assert_private_denial(self.client.get(self.url("capture_detail", pk=item.pk)))
        self.client.force_login(self.owner)
        owner_work = self.client.get(self.url("task_detail", slug=task.slug))
        self.assertEqual(owner_work.context["source_capture"].pk, item.pk)
        self.assertContains(owner_work, f'href="{self.url("capture_detail", pk=item.pk)}"')

    def test_legacy_notes_are_self_scoped_and_deliberate_import_is_idempotent(self):
        date = timezone.localdate() - timedelta(days=3)
        plan = DailyPlan.objects.create(owner=self.owner, plan_date=date, quick_notes="Legacy own notes preserved")
        other_plan = DailyPlan.objects.create(owner=self.member, plan_date=date, quick_notes="PRIVATE foreign legacy marker")
        response = self.client.get(self.url("capture_legacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legacy own notes preserved")
        self.assertNotContains(response, "PRIVATE foreign legacy marker")
        self.assertEqual(CaptureItem.objects.count(), 0)
        import_url = self.url("capture_import_legacy", plan_pk=plan.pk)
        self.assertEqual(self.client.get(import_url).status_code, 405)
        first = self.client.post(import_url)
        second = self.client.post(import_url)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.url, second.url)
        self.assertEqual(CaptureItem.all_objects.filter(legacy_plan=plan).count(), 1)
        imported = CaptureItem.objects.get(legacy_plan=plan)
        self.assertEqual(imported.visibility, "PRIVATE")
        self.assertEqual(imported.original_text, "Legacy own notes preserved")
        plan.refresh_from_db()
        self.assertEqual(plan.quick_notes, "Legacy own notes preserved")
        self.assert_private_denial(self.client.post(self.url("capture_import_legacy", plan_pk=other_plan.pk)))


    def test_stale_save_reports_the_new_audience_without_publishing_the_draft(self):
        item = self.capture(content="Saved private source")
        self.assertEqual(self.action(item, "share", team=self.team.pk).status_code, 302)
        response = self.save(item, content="Draft written before learning about sharing")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["item"]["visibility"], "TEAM")
        self.assertEqual(response.json()["item"]["shared_team_id"], self.team.pk)
        self.assertIn(self.team.name, response.json()["item"]["shared_team_name"])
        item.refresh_from_db()
        self.assertEqual(item.content, "Saved private source")


class CaptureAdminPrivacyTests(TestCase):
    def test_staff_admin_only_reads_owned_captures_and_cannot_mutate_them(self):
        users = get_user_model()
        staff = users.objects.create_user("capture-admin-staff", is_staff=True)
        other = users.objects.create_user("capture-admin-other")
        staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="tasker", codename__in=("view_task", "view_captureitem"),
        ))
        other.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
        owned = create_capture(user=staff, client_key=uuid.uuid4(), kind="NOTE", content="Own admin note")
        foreign = create_capture(user=other, client_key=uuid.uuid4(), kind="NOTE", content="PRIVATE foreign admin note")
        model_admin = admin.site._registry[CaptureItem]
        request = RequestFactory().get("/admin/tasker/captureitem/")
        request.user = staff
        self.assertEqual(set(model_admin.get_queryset(request).values_list("pk", flat=True)), {owned.pk})
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, owned))
        self.assertFalse(model_admin.has_delete_permission(request, owned))
        self.assertFalse(model_admin.has_view_permission(request, foreign))
        self.assertTrue(model_admin.has_view_permission(request, owned))
        self.assertIn("content", model_admin.get_readonly_fields(request, owned))
        self.assertIn("original_text", model_admin.get_readonly_fields(request, owned))
