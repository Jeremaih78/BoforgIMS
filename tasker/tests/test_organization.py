from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounting.models import Company
from tasker.forms import TaskForm
from tasker.models import OrganizationAccess, OrganizationEvent, Task, TaskTimeEntry, Team, TeamMembership
from tasker.organization import (
    arrival_can_read, assignable_users_for, can_manage_company, can_manage_team,
    validate_task_scope, visible_companies_for, visible_teams_for,
)
from tasker.policies import can_change_task, visible_tasks_for
from tasker.services.tasks import save_task
from tasker.services.tasks import update_progress
from tasker.services.focus import start_focus


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class OrganizationScopeTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.manager = users.objects.create_user(username="org-manager")
        self.employee = users.objects.create_user(username="org-employee")
        self.coworker = users.objects.create_user(username="org-coworker")
        self.outsider = users.objects.create_user(username="org-outsider")
        self.executive = users.objects.create_user(username="org-executive")
        self.company = Company.objects.create(name="Organization A")
        self.other_company = Company.objects.create(name="Organization B")
        self.team = Team.objects.create(company=self.company, name="Sales")
        self.other_team = Team.objects.create(company=self.company, name="Service")
        self.foreign_team = Team.objects.create(company=self.other_company, name="Foreign")
        self.manager_membership = TeamMembership.objects.create(team=self.team, user=self.manager, role=TeamMembership.Role.MANAGER)
        TeamMembership.objects.create(team=self.team, user=self.employee)
        TeamMembership.objects.create(team=self.team, user=self.coworker)
        TeamMembership.objects.create(team=self.foreign_team, user=self.outsider)
        self.permissions(self.manager, "view_task", "add_task", "change_task", "delete_task", "assign_task")
        self.permissions(self.employee, "view_task", "add_task", "change_task")
        self.permissions(self.executive, "view_task", "view_all_tasks", "manage_tasker", "change_task", "assign_task")
        self.task = Task.objects.create(title="Shared sales task", owner=self.employee, company=self.company, team=self.team, assigned_by=self.manager)
        self.personal = Task.objects.create(title="Private legacy task", owner=self.employee, assigned_by=self.manager)

    @staticmethod
    def permissions(user, *names):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker", codename__in=names))

    def payload(self, **extra):
        data = {"title": "Delegated task", "owner": self.employee.pk, "company": self.company.pk,
                "team": self.team.pk, "priority": "HIGH", "status": "PENDING", "progress": 0,
                "due_date": "", "due_time": "", "category": "", "parent_task": "", "colour": "",
                "notes": "", "description": ""}
        data.update(extra)
        return data

    def test_live_team_manager_sees_only_explicit_shared_work(self):
        Task.objects.create(title="Different team", owner=self.employee, company=self.company, team=self.other_team)
        Task.objects.create(title="Foreign company", owner=self.outsider, company=self.other_company, team=self.foreign_team)
        self.assertEqual(list(visible_tasks_for(self.manager)), [self.task])
        self.assertTrue(can_change_task(self.manager, self.task))
        self.assertFalse(can_change_task(self.manager, self.personal))

    def test_capability_and_membership_are_both_required(self):
        self.assertFalse(visible_tasks_for(self.executive).exists())
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="assign_task"))
        manager = get_user_model().objects.get(pk=self.manager.pk)
        self.assertFalse(can_manage_team(manager, self.team))
        self.assertFalse(visible_tasks_for(manager).exists())

    def test_member_does_not_gain_coworker_task_visibility(self):
        Task.objects.create(title="Coworker work", owner=self.coworker, company=self.company, team=self.team)
        self.assertEqual(list(visible_tasks_for(self.employee).values_list("pk", flat=True).order_by("pk")), [self.task.pk, self.personal.pk])

    def test_explicit_company_read_grant_does_not_expose_unmapped_work(self):
        grant = OrganizationAccess.objects.create(company=self.company, user=self.executive, can_view_all=True)
        self.assertEqual(list(visible_tasks_for(self.executive)), [self.task])
        self.assertFalse(can_change_task(self.executive, self.task))
        grant.can_manage = True
        grant.save()
        self.assertTrue(can_change_task(self.executive, self.task))
        self.assertTrue(can_manage_company(self.executive, self.company))
        self.assertFalse(can_manage_company(self.executive, self.other_company))

    def test_company_grant_still_requires_capability(self):
        OrganizationAccess.objects.create(company=self.company, user=self.outsider, can_view_all=True, can_manage=True)
        self.assertFalse(can_manage_company(self.outsider, self.company))
        self.assertFalse(visible_tasks_for(self.outsider).filter(pk=self.task.pk).exists())

    def test_revoked_manager_loses_read_write_and_delegation(self):
        self.manager_membership.is_active = False
        self.manager_membership.save()
        self.assertFalse(visible_tasks_for(self.manager).exists())
        self.assertFalse(can_change_task(self.manager, self.task))
        self.assertFalse(assignable_users_for(self.manager, team=self.team).filter(pk=self.employee.pk).exists())
        with self.assertRaises(PermissionDenied):
            update_progress(task=self.task, user=self.manager, progress=100)
        with self.assertRaises(PermissionDenied):
            start_focus(task=self.task, user=self.manager)

    def test_forged_ids_are_not_visible_at_endpoints(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.personal.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse("ims:tasker:task_create"), {"team": self.foreign_team.pk}).status_code, 404)
        form = TaskForm(self.payload(owner=self.outsider.pk), user=self.manager)
        self.assertFalse(form.is_valid())
        self.assertIn("owner", form.errors)

    def test_delegate_creates_audit_and_is_rechecked_after_form_validation(self):
        form = TaskForm(self.payload(), user=self.manager)
        self.assertTrue(form.is_valid(), form.errors)
        self.manager_membership.is_active = False
        self.manager_membership.save()
        with self.assertRaises(PermissionDenied):
            save_task(form=form, user=self.manager)
        self.assertFalse(Task.objects.filter(title="Delegated task").exists())

    def test_cached_change_capability_does_not_survive_revocation(self):
        self.assertTrue(self.manager.has_perm("tasker.change_task"))
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="change_task"))
        # The original request actor still has Django's cached permission set.
        self.assertTrue(self.manager.has_perm("tasker.change_task"))
        with self.assertRaises(PermissionDenied):
            update_progress(task=self.task, user=self.manager, progress=100)
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress, 0)

    def test_cached_create_capability_does_not_survive_validated_form(self):
        form = TaskForm(self.payload(), user=self.manager)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(self.manager.has_perm("tasker.add_task"))
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="add_task"))
        self.assertTrue(self.manager.has_perm("tasker.add_task"))
        with self.assertRaises(PermissionDenied):
            save_task(form=form, user=self.manager)
        self.assertFalse(Task.objects.filter(title="Delegated task").exists())

    def test_employee_cannot_hide_assigned_work_by_removing_reporting_scope(self):
        form = TaskForm(self.payload(company="", team=""), instance=self.task, user=self.employee)
        self.assertFalse(form.is_valid())
        self.task.refresh_from_db()
        self.assertEqual(self.task.team_id, self.team.pk)

    def test_reassignment_closes_old_timer_and_records_scope_event(self):
        entry = TaskTimeEntry.objects.create(task=self.task, user=self.employee, started_at=timezone.now())
        form = TaskForm(self.payload(title=self.task.title, owner=self.coworker.pk), instance=self.task, user=self.manager)
        self.assertTrue(form.is_valid(), form.errors)
        saved = save_task(form=form, user=self.manager)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.ended_at)
        self.assertEqual(saved.owner, self.coworker)
        self.assertEqual(saved.assigned_by, self.manager)
        self.assertTrue(OrganizationEvent.objects.filter(object_id=str(saved.pk), action="task.scope_changed", actor=self.manager).exists())
        self.assertTrue(saved.activities.filter(changes__has_key="owner_id").exists())

    def test_parent_scope_and_company_team_coherence(self):
        invalid = Task(title="Invalid", owner=self.employee, company=self.other_company, team=self.team)
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        invalid = Task(title="Invalid", owner=self.employee, company=self.company, team=self.team, parent_task=self.personal)
        with self.assertRaises(ValidationError):
            validate_task_scope(self.manager, invalid)

    def test_live_membership_and_grant_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeamMembership.objects.create(team=self.team, user=self.employee)
        OrganizationAccess.objects.create(company=self.company, user=self.executive)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationAccess.objects.create(company=self.company, user=self.executive)

    def test_soft_deleted_team_and_inactive_company_or_user_remove_scope(self):
        self.team.delete(user=self.manager)
        self.assertFalse(visible_teams_for(self.manager).exists())
        self.assertFalse(can_manage_team(self.manager, self.team))
        self.team.restore(user=self.manager)
        self.company.is_active = False
        self.company.save(update_fields=["is_active"])
        self.assertFalse(visible_companies_for(self.manager).exists())
        self.assertFalse(visible_tasks_for(self.manager).exists())
        self.company.is_active = True
        self.company.save(update_fields=["is_active"])
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])
        self.assertFalse(visible_tasks_for(self.manager).exists())

    def test_arrival_scope_contract_self_manager_and_company_executive(self):
        self.assertTrue(arrival_can_read(self.employee, self.employee, self.team))
        self.assertTrue(arrival_can_read(self.manager, self.employee, self.team))
        self.assertFalse(arrival_can_read(self.coworker, self.employee, self.team))
        self.assertFalse(arrival_can_read(self.manager, self.outsider, self.foreign_team))
        OrganizationAccess.objects.create(company=self.company, user=self.executive, can_view_all=True)
        self.assertTrue(arrival_can_read(self.executive, self.employee, self.team))

    def test_superuser_is_explicit_privileged_boundary(self):
        admin = get_user_model().objects.create_superuser(username="org-admin", email="", password="fake")
        self.assertTrue(visible_tasks_for(admin).filter(pk=self.personal.pk).exists())
        self.assertTrue(can_change_task(admin, self.personal))
