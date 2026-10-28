"""Regression contracts for bound capture errors and optimistic shared-note saves."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from tasker.models import Task


def notes_version(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SaveReliabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reliable-writer")
        self.user.user_permissions.set(Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("view_task", "add_task", "change_task"),
        ))
        self.task = Task.objects.create(title="Shared working document", owner=self.user, notes="Original")
        self.client.force_login(self.user)
        self.notes_url = reverse("ims:tasker:focus_notes", kwargs={"slug": self.task.slug})

    def test_invalid_capture_keeps_values_errors_and_reopens_modal(self):
        response = self.client.post(reverse("ims:tasker:index"), {
            "action": "quick_add", "title": "Call supplier <today>",
            "priority": "HIGH", "due_time": "10:30", "estimated_minutes": "0",
            "next": reverse("ims:tasker:task_list"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title="Call supplier <today>").exists())
        self.assertContains(response, 'value="Call supplier &lt;today&gt;"')
        self.assertContains(response, 'value="HIGH" selected')
        self.assertContains(response, 'value="10:30"')
        self.assertContains(response, 'value="0"')
        self.assertContains(response, 'id="quickAddErrors"')
        self.assertContains(response, 'id="globalTaskEstimateErrors"')
        self.assertContains(response, 'aria-describedby="globalTaskEstimateErrors"')
        self.assertContains(response, 'data-reopen="true"')
        self.assertEqual(response.context["quick_add_next"], reverse("ims:tasker:task_list"))

    def test_invalid_capture_does_not_preserve_an_external_return_url(self):
        response = self.client.post(reverse("ims:tasker:index"), {
            "action": "quick_add", "title": "Retain this", "priority": "HIGH",
            "estimated_minutes": "0", "next": "https://untrusted.example/",
        })
        self.assertEqual(response.context["quick_add_next"], reverse("ims:tasker:index"))

    def test_focus_exposes_version_shared_label_and_conflict_controls(self):
        response = self.client.get(reverse("ims:tasker:focus_mode", kwargs={"slug": self.task.slug}))
        self.assertEqual(response.context["notes_version"], notes_version("Original"))
        self.assertContains(response, 'data-notes-version="' + notes_version("Original") + '"')
        self.assertContains(response, 'data-draft-user="' + str(self.user.pk) + '"')
        self.assertContains(response, 'id="focusNotesConflict"')
        self.assertContains(response, "Shared task notes")

    def test_notes_require_base_version_and_do_not_truncate(self):
        response = self.client.post(self.notes_url, {"notes": "Unversioned"})
        self.assertEqual(response.status_code, 428)
        response = self.client.post(self.notes_url, {
            "notes": "x" * 10001, "base_version": notes_version("Original"),
        })
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Original")

    def test_notes_return_new_version_and_same_payload_retry_is_safe(self):
        payload = {"notes": "First edit", "base_version": notes_version("Original")}
        first = self.client.post(self.notes_url, payload)
        self.assertEqual(first.json(), {"saved": True, "version": notes_version("First edit")})
        retry = self.client.post(self.notes_url, payload)
        self.assertEqual(retry.json(), first.json())

    def test_stale_tab_cannot_overwrite_newer_notes(self):
        base = notes_version("Original")
        self.client.post(self.notes_url, {"notes": "From first tab", "base_version": base})
        response = self.client.post(self.notes_url, {"notes": "From stale tab", "base_version": base})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["notes"], "From first tab")
        self.assertEqual(response.json()["version"], notes_version("From first tab"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "From first tab")

    def test_notes_recheck_write_permission_after_locking(self):
        other = get_user_model().objects.create_user(username="new-task-owner")

        def stale_authorized_task(*_args):
            Task.objects.filter(pk=self.task.pk).update(owner=other)
            return self.task

        with patch("tasker.views.focus._mutable_task", side_effect=stale_authorized_task):
            response = self.client.post(self.notes_url, {
                "notes": "Must not write", "base_version": notes_version("Original"),
            })
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Original")

    def test_note_versions_and_saves_normalize_browser_newlines(self):
        from tasker.services.notes import notes_version as actual_version

        self.task.notes = "Line one\r\nLine two\rLine three"
        self.task.save(update_fields=["notes"])
        expected = actual_version("Line one\nLine two\nLine three")
        self.assertEqual(actual_version(self.task.notes), expected)
        response = self.client.post(self.notes_url, {
            "notes": "Updated\r\ntext", "base_version": expected,
        })
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Updated\ntext")

    def test_stale_task_edit_preserves_draft_and_displays_saved_notes_for_merge(self):
        url = reverse("ims:tasker:task_edit", kwargs={"slug": self.task.slug})
        page = self.client.get(url)
        self.assertEqual(page.context["form"]["notes_version"].value(), notes_version("Original"))
        self.client.post(self.notes_url, {
            "notes": "Other tab <saved>", "base_version": notes_version("Original"),
        })
        payload = {
            "title": self.task.title, "priority": "MEDIUM", "status": "PENDING", "progress": "0",
            "notes": "My <draft>", "notes_version": notes_version("Original"),
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My &lt;draft&gt;")
        self.assertContains(response, "Other tab &lt;saved&gt;")
        self.assertContains(response, "merge your changes before saving again")
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Other tab <saved>")
        payload["notes"] = "Reviewed and merged"
        payload["notes_version"] = response.context["form"]["notes_version"].value()
        self.assertRedirects(self.client.post(url, payload), self.task.get_absolute_url())
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Reviewed and merged")

    def test_changed_task_edit_without_notes_version_is_not_saved(self):
        response = self.client.post(reverse("ims:tasker:task_edit", kwargs={"slug": self.task.slug}), {
            "title": "Must not save", "priority": "MEDIUM", "status": "PENDING", "progress": "0",
            "notes": "Unversioned edit",
        })
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Shared working document")
        self.assertEqual(self.task.notes, "Original")


class ConcurrentFocusNotesTests(TransactionTestCase):
    def test_concurrent_tabs_cannot_both_replace_the_same_base_version(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock behavior requires disposable PostgreSQL")
        user = get_user_model().objects.create_user(username="concurrent-note-writer")
        user.user_permissions.set(Permission.objects.filter(
            content_type__app_label="tasker", codename__in=("view_task", "change_task"),
        ))
        task = Task.objects.create(title="Concurrent notes", owner=user, notes="Original")
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(user)
        barrier = Barrier(2)
        url = reverse("ims:tasker:focus_notes", kwargs={"slug": task.slug})

        def save(index):
            try:
                barrier.wait(timeout=10)
                return clients[index].post(url, {
                    "notes": f"Tab {index}", "base_version": notes_version("Original"),
                }).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(save, range(2)))
        self.assertEqual(sorted(statuses), [200, 409])
        task.refresh_from_db()
        self.assertIn(task.notes, ("Tab 0", "Tab 1"))
