"""Scope, lifecycle, acyclicity and concurrent dependency edits."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from threading import Barrier
from unittest.mock import patch
import uuid
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from accounting.models import Company
from tasker.dependency_models import DependencyGraphState, TaskDependency
from tasker.models import Task, TaskActivity, TaskTimeEntry, Team, TeamMembership
from tasker.organization import validate_task_scope
from tasker.services.dependencies import (
    DependencyConflict, add_dependency, assert_task_ready, dependency_candidates,
    readiness_for, readiness_for_tasks, remove_dependency, validate_dependency_scope,
)
from tasker.services.focus import complete_focus, start_focus
from tasker.services.next_actions import suggest_next_work
from tasker.services.tasks import soft_delete_task, update_progress


class DependencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('dependency-owner', is_staff=True)
        cls.peer = get_user_model().objects.create_user('dependency-peer')
        cls.manager = get_user_model().objects.create_user('dependency-manager')
        for user in (cls.owner, cls.peer, cls.manager):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))
        cls.company = Company.objects.create(name='Dependency sample company')
        cls.team = Team.objects.create(name='Dependency sample team', company=cls.company)
        cls.other_team = Team.objects.create(name='Other dependency sample team', company=cls.company)
        for user in (cls.owner, cls.peer):
            TeamMembership.objects.create(user=user, team=cls.team)
        cls.manager_membership = TeamMembership.objects.create(user=cls.manager, team=cls.team, role='MANAGER')

    def task(self, **changes):
        return Task.objects.create(**{'owner':self.owner,'title':'Sample dependency task', **changes})

    def link(self, task, prerequisite, user=None):
        user = user or self.owner
        return add_dependency(task=task, prerequisite=prerequisite, user=user,
                              expected_version=readiness_for(task=task,user=user).version)

    def test_new_tasks_have_known_ready_state_without_changing_hierarchy(self):
        parent, task, prerequisite = self.task(), self.task(), self.task()
        task.parent_task=parent; task.save()
        self.assertTrue(readiness_for(task=task,user=self.owner).ready)
        self.link(task,prerequisite)
        task.refresh_from_db()
        self.assertEqual(task.parent_task_id,parent.pk)
        self.assertEqual(TaskDependency.objects.get(task=task).prerequisite_id,prerequisite.pk)

    def test_pending_complete_and_reopened_prerequisite_change_readiness(self):
        task, prerequisite = self.task(), self.task()
        self.link(task,prerequisite)
        waiting = readiness_for(task=task,user=self.owner)
        self.assertFalse(waiting.ready); self.assertEqual(waiting.code,'waiting')
        complete_focus(task=prerequisite,user=self.owner)
        ready = readiness_for(task=task,user=self.owner)
        self.assertTrue(ready.ready); self.assertNotEqual(ready.version,waiting.version)
        update_progress(task=prerequisite,user=self.owner,progress=0)
        self.assertFalse(readiness_for(task=task,user=self.owner).ready)
        self.assertEqual(Task.objects.get(pk=task.pk).status,'PENDING')

    def test_checklist_or_progress_alone_does_not_satisfy_prerequisite(self):
        task, prerequisite = self.task(), self.task(progress=100)
        self.link(task,prerequisite)
        self.assertFalse(readiness_for(task=task,user=self.owner).ready)

    def test_chain_and_transitive_reopen_are_checked(self):
        first, second, third = self.task(), self.task(), self.task()
        self.link(first,second); self.link(second,third)
        complete_focus(task=second,user=self.owner)
        self.assertFalse(readiness_for(task=first,user=self.owner).ready)
        complete_focus(task=third,user=self.owner)
        self.assertTrue(readiness_for(task=first,user=self.owner).ready)
        update_progress(task=third,user=self.owner,progress=0)
        self.assertFalse(readiness_for(task=first,user=self.owner).ready)

    def test_self_and_indirect_cycles_never_commit(self):
        first, second, third = self.task(), self.task(), self.task()
        with self.assertRaises(ValidationError): self.link(first,first)
        self.link(first,second); self.link(second,third)
        with self.assertRaises(ValidationError): self.link(third,first)
        self.assertEqual(TaskDependency.objects.count(),2)

    def test_duplicate_add_and_remove_are_idempotent_and_audited_once(self):
        task, prerequisite = self.task(), self.task()
        version = readiness_for(task=task,user=self.owner).version
        edge = add_dependency(task=task,prerequisite=prerequisite,user=self.owner,expected_version=version)
        repeated = add_dependency(task=task,prerequisite=prerequisite,user=self.owner,expected_version=version)
        self.assertEqual(edge.pk,repeated.pk)
        self.assertEqual(TaskActivity.objects.filter(task=task,description='Prerequisite added').count(),1)
        current = readiness_for(task=task,user=self.owner).version
        remove_dependency(task=task,dependency_id=edge.pk,user=self.owner,expected_version=current)
        remove_dependency(task=task,dependency_id=edge.pk,user=self.owner,expected_version=current)
        self.assertFalse(TaskDependency.objects.exists())
        self.assertTrue(TaskDependency.all_objects.get(pk=edge.pk).is_deleted)
        self.assertEqual(TaskActivity.objects.filter(task=task,description='Prerequisite removed').count(),1)

    def test_stale_edit_cannot_replace_a_newer_graph_or_prerequisite_state(self):
        task, first, second = self.task(), self.task(), self.task()
        version = readiness_for(task=task,user=self.owner).version
        edge = self.link(task,first)
        with self.assertRaises(DependencyConflict):
            add_dependency(task=task,prerequisite=second,user=self.owner,expected_version=version)
        fresh = readiness_for(task=task,user=self.owner).version
        complete_focus(task=first,user=self.owner)
        with self.assertRaises(DependencyConflict):
            remove_dependency(task=task,dependency_id=edge.pk,user=self.owner,expected_version=fresh)
        self.assertEqual(TaskDependency.objects.count(),1)

    def test_cross_owner_personal_and_cross_team_links_are_rejected(self):
        task, private = self.task(), self.task(owner=self.peer,title='Private peer sample')
        with self.assertRaises(PermissionDenied): self.link(task,private)
        first = self.task(company=self.company,team=self.team)
        second = self.task(company=self.company,team=self.other_team)
        with self.assertRaises(PermissionDenied): self.link(first,second,user=self.manager)
        self.assertFalse(TaskDependency.objects.exists())

    def test_real_manager_may_link_shared_work_but_employee_cannot_see_peer_title(self):
        task = self.task(company=self.company,team=self.team)
        prerequisite = self.task(owner=self.peer,company=self.company,team=self.team,title='Restricted supplier sample')
        edge = self.link(task,prerequisite,user=self.manager)
        result = readiness_for(task=task,user=self.owner)
        self.assertFalse(result.ready); self.assertEqual(result.code,'unavailable')
        item = result.prerequisites[0]
        self.assertEqual(item.dependency_id,edge.pk)
        self.assertIsNone(item.task_id); self.assertEqual(item.url,''); self.assertFalse(item.accessible)
        self.assertNotIn(prerequisite.title,str(result))
        remove_dependency(task=task,dependency_id=edge.pk,user=self.owner,expected_version=result.version)
        self.assertTrue(readiness_for(task=task,user=self.owner).ready)

    def test_current_permissions_and_manager_membership_are_rechecked(self):
        first, second = self.task(company=self.company,team=self.team), self.task(company=self.company,team=self.team)
        version = readiness_for(task=first,user=self.manager).version
        TeamMembership.objects.filter(pk=self.manager_membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            add_dependency(task=first,prerequisite=second,user=self.manager,expected_version=version)
        self.owner.user_permissions.clear()
        with self.assertRaises(PermissionDenied): readiness_for(task=first,user=self.owner)

    def test_deleted_archived_or_cancelled_prerequisites_do_not_unlock_work(self):
        for state in ('deleted','archived','cancelled'):
            with self.subTest(state=state):
                task, prerequisite = self.task(), self.task(status='COMPLETED',progress=100)
                self.link(task,prerequisite)
                self.assertTrue(readiness_for(task=task,user=self.owner).ready)
                if state == 'deleted': soft_delete_task(task=prerequisite,user=self.owner)
                elif state == 'archived': prerequisite.archived=True; prerequisite.save()
                else: prerequisite.status='CANCELLED'; prerequisite.save()
                self.assertFalse(readiness_for(task=task,user=self.owner).ready)

    def test_scope_change_helper_and_existing_assignment_contract_protect_links(self):
        task, prerequisite = self.task(), self.task()
        self.link(task,prerequisite)
        changed = copy(task); changed.owner=self.peer
        with self.assertRaises(ValidationError): validate_dependency_scope(candidate=changed,previous=task)
        scoped = self.task(company=self.company,team=self.team)
        other = self.task(company=self.company,team=self.team)
        self.link(scoped,other,user=self.manager)
        changed = copy(scoped); changed.owner=self.peer
        validate_dependency_scope(candidate=changed,previous=scoped)

    def test_legacy_scope_mutation_fails_closed_without_revealing_new_scope(self):
        task, prerequisite = self.task(), self.task(status='COMPLETED',progress=100)
        self.link(task,prerequisite)
        Task.objects.filter(pk=prerequisite.pk).update(owner=self.peer)
        result = readiness_for(task=task,user=self.owner)
        self.assertFalse(result.ready)
        self.assertFalse(result.prerequisites[0].accessible)

    @patch("django.utils.timezone.now", return_value=datetime(2026, 10, 27, 8, tzinfo=dt_timezone.utc))
    def test_focus_start_is_gated_and_next_actions_skip_waiting_critical_work(self, clock):
        from tasker.models import WorkPolicy
        WorkPolicy.objects.create(owner=self.owner, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(16), buffer_minutes=60)
        task = self.task(priority='CRITICAL', estimated_duration=timedelta(minutes=30))
        prerequisite, alternative = self.task(priority='LOW', estimated_duration=timedelta(minutes=30)), self.task(priority='HIGH',title='Executable sample', estimated_duration=timedelta(minutes=30))
        self.link(task,prerequisite)
        with self.assertRaises(ValueError): start_focus(task=task,user=self.owner)
        self.assertFalse(TaskTimeEntry.objects.exists())
        suggestions = suggest_next_work(user=self.owner)
        self.assertNotIn(task,[item for item in suggestions])
        self.assertIn(alternative,[item for item in suggestions])
        complete_focus(task=prerequisite,user=self.owner)
        start_focus(task=task,user=self.owner)
        self.assertEqual(TaskTimeEntry.objects.filter(ended_at__isnull=True).count(),1)

    def test_readiness_expected_version_rejects_reopened_work(self):
        task, prerequisite = self.task(), self.task(status='COMPLETED',progress=100)
        self.link(task,prerequisite)
        version = readiness_for(task=task,user=self.owner).version
        update_progress(task=prerequisite,user=self.owner,progress=0)
        with self.assertRaises(DependencyConflict): assert_task_ready(task=task,user=self.owner,expected_version=version)

    def test_batch_and_candidates_are_scoped_bounded_and_safe(self):
        tasks = [self.task(title=f'Batch sample {index}') for index in range(3)]
        foreign = self.task(owner=self.peer)
        self.link(tasks[0],tasks[1])
        results = readiness_for_tasks(tasks=tasks,user=self.owner)
        self.assertFalse(results[tasks[0].pk].ready)
        self.assertTrue(results[tasks[2].pk].ready)
        self.assertNotIn(foreign,list(dependency_candidates(task=tasks[0],user=self.owner)))
        with self.assertRaises(ValidationError): readiness_for_tasks(tasks=list(range(1,102)),user=self.owner)

    def test_oversized_and_corrupt_graphs_fail_closed(self):
        first, second, third = self.task(), self.task(), self.task()
        self.link(first,second); self.link(second,third)
        with patch('tasker.services.dependencies.MAX_NODES',2):
            self.assertFalse(readiness_for(task=first,user=self.owner).ready)
        TaskDependency.objects.create(task=third,prerequisite=second)
        Task.objects.filter(pk__in=(first.pk,second.pk,third.pk)).update(status='COMPLETED',progress=100)
        result = readiness_for(task=first,user=self.owner)
        self.assertFalse(result.ready); self.assertEqual(result.code,'cycle')

    def test_batch_query_count_does_not_grow_per_task(self):
        prerequisite=self.task(status='COMPLETED',progress=100)
        tasks=[self.task(title=f'Bounded sample {index}') for index in range(24)]
        for task in tasks: TaskDependency.objects.create(task=task,prerequisite=prerequisite)
        with CaptureQueriesContext(connection) as one:
            readiness_for_tasks(tasks=tasks[:1],user=self.owner)
        with CaptureQueriesContext(connection) as many:
            results=readiness_for_tasks(tasks=tasks,user=self.owner)
        self.assertTrue(all(item.ready for item in results.values()))
        self.assertLessEqual(len(many),len(one)+2)

    def test_changing_transitive_graph_during_readiness_fails_closed(self):
        task, prerequisite = self.task(), self.task(status='COMPLETED',progress=100)
        self.link(task,prerequisite)
        from tasker.services import dependencies
        chain = dependencies._chain
        def moving_chain(ids):
            result = chain(ids)
            DependencyGraphState.objects.update(revision=uuid.uuid4())
            return result
        with patch('tasker.services.dependencies._chain',side_effect=moving_chain):
            result = readiness_for(task=task,user=self.owner)
        self.assertFalse(result.ready)
        self.assertEqual(result.code,'graph_changed')

    def test_admin_never_resolves_private_fk_titles(self):
        task = self.task(company=self.company,team=self.team)
        private = self.task(owner=self.peer,company=self.company,team=self.team,title='Hidden dependency sample')
        edge = self.link(task,private,user=self.manager)
        request = RequestFactory().get('/admin/'); request.user=self.owner
        dependency_admin = admin.site._registry[TaskDependency]
        self.assertFalse(dependency_admin.get_queryset(request).filter(pk=edge.pk).exists())
        self.assertNotIn(private.title,str(edge))


class DependencyConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner=get_user_model().objects.create_user('dependency-race-owner')
        self.owner.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))
        self.tasks=[Task.objects.create(owner=self.owner,title=f'Race sample {index}') for index in range(4)]

    def _race(self, pairs):
        barrier=Barrier(2)
        versions=[readiness_for(task=task,user=self.owner).version for task,_ in pairs]
        def commit(index):
            close_old_connections()
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '12s'")
                actor=get_user_model().objects.get(pk=self.owner.pk)
                barrier.wait(timeout=8)
                task, prerequisite=pairs[index]
                try:
                    return ('saved',add_dependency(task=task.pk,prerequisite=prerequisite.pk,user=actor,expected_version=versions[index]).pk)
                except (DependencyConflict,ValidationError) as error:
                    return ('rejected',type(error).__name__)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(commit,(0,1)))

    def test_simultaneous_same_link_is_one_edge_and_one_audit(self):
        first,second=self.tasks[:2]
        results=self._race([(first,second),(first,second)])
        self.assertEqual([state for state,_ in results],['saved','saved'])
        self.assertEqual(TaskDependency.objects.count(),1)
        self.assertEqual(TaskActivity.objects.filter(description='Prerequisite added').count(),1)

    def test_disjoint_concurrent_edges_cannot_close_a_cycle(self):
        first,second,third,fourth=self.tasks
        for task,prerequisite in ((first,second),(third,fourth)):
            add_dependency(task=task,prerequisite=prerequisite,user=self.owner,expected_version=readiness_for(task=task,user=self.owner).version)
        results=self._race([(second,third),(fourth,first)])
        self.assertEqual(sum(state=='saved' for state,_ in results),1)
        self.assertEqual(sum(state=='rejected' for state,_ in results),1)
        self.assertEqual(TaskDependency.objects.count(),3)
