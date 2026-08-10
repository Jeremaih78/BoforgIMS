from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from tasker.models import Task


class TaskerRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_tasker_roles", verbosity=0)
        cls.owner = get_user_model().objects.create_user(username="employee", password="test-pass")
        cls.other = get_user_model().objects.create_user(username="other", password="test-pass")
        cls.supervisor = get_user_model().objects.create_user(username="supervisor", password="test-pass")
        cls.manager = get_user_model().objects.create_user(username="manager", password="test-pass")
        cls.owner.groups.add(Group.objects.get(name="Tasker Employee"))
        cls.other.groups.add(Group.objects.get(name="Tasker Employee"))
        cls.supervisor.groups.add(Group.objects.get(name="Tasker Supervisor"))
        cls.manager.groups.add(Group.objects.get(name="Tasker Manager"))
        cls.task = Task.objects.create(title="Employee private task", owner=cls.owner, assigned_by=cls.owner)

    def test_all_four_roles_are_provisioned_idempotently(self):
        call_command("setup_tasker_roles", verbosity=0)
        self.assertEqual(
            set(Group.objects.filter(name__startswith="Tasker ").values_list("name", flat=True)),
            {"Tasker Employee", "Tasker Supervisor", "Tasker Manager", "Tasker Administrator"},
        )

    def test_employee_has_workflow_permissions_but_no_global_visibility(self):
        self.assertTrue(self.owner.has_perm("tasker.add_task"))
        self.assertTrue(self.owner.has_perm("tasker.change_task"))
        self.assertFalse(self.owner.has_perm("tasker.view_all_tasks"))
        self.assertFalse(self.owner.has_perm("tasker.manage_tasker"))

    def test_employee_cannot_read_or_mutate_another_employee_task(self):
        self.client.force_login(self.other)
        detail = reverse("ims:tasker:task_detail", kwargs={"slug": self.task.slug})
        edit = reverse("ims:tasker:task_edit", kwargs={"slug": self.task.slug})
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(edit).status_code, 404)

    def test_supervisor_can_read_all_but_cannot_edit_unassigned_task(self):
        self.client.force_login(self.supervisor)
        detail = reverse("ims:tasker:task_detail", kwargs={"slug": self.task.slug})
        edit = reverse("ims:tasker:task_edit", kwargs={"slug": self.task.slug})
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.get(edit).status_code, 403)

    def test_manager_can_read_and_manage_all_tasks(self):
        self.client.force_login(self.manager)
        detail = reverse("ims:tasker:task_detail", kwargs={"slug": self.task.slug})
        edit = reverse("ims:tasker:task_edit", kwargs={"slug": self.task.slug})
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.get(edit).status_code, 200)
