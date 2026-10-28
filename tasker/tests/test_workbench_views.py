"""Work UI preserves scope, explicit acceptance and bounded rendering."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tasker.models import SavedWorkView, Task, TaskChecklistItem, WorkbenchPreference
from tasker.services.workbench import work_version


class WorkbenchViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('work-ui-owner')
        cls.peer = get_user_model().objects.create_user('work-ui-peer')
        for user in (cls.user, cls.peer):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('ims:tasker:task_list')

    def task(self, **changes):
        return Task.objects.create(**{'owner': self.user, 'title': 'Sample work', **changes})

    def action_url(self, task, action):
        return reverse('ims:tasker:task_work_action', args=(task.slug, action))

    def test_list_and_board_keep_private_work_out_and_start_in_one_action(self):
        mine = self.task(title='Visible sample')
        foreign = self.task(owner=self.peer, title='Private peer sample')
        for layout in ('list', 'board'):
            response = self.client.get(self.url, {'layout': layout})
            self.assertContains(response, mine.title)
            self.assertNotContains(response, foreign.title)
            self.assertContains(response, reverse('ims:tasker:focus_start', args=(mine.slug,)))
            self.assertContains(response, 'data-work-preview=')
            self.assertContains(response, f'aria-label="Edit {mine.title}"')

    def test_presets_distinguish_blocked_due_and_unscheduled_work(self):
        blocked = self.task(title='Waiting sample', status='BLOCKED', blocked_reason='Dependency')
        soon = self.task(title='Due sample', due_date=timezone.localdate()+timedelta(days=2))
        later = self.task(title='Later sample', due_date=timezone.localdate()+timedelta(days=30))
        self.assertEqual(list(self.client.get(self.url, {'preset':'waiting'}).context['page_obj']), [blocked])
        self.assertEqual(list(self.client.get(self.url, {'preset':'duesoon'}).context['page_obj']), [soon])
        self.assertEqual(list(self.client.get(self.url, {'preset':'unscheduled'}).context['page_obj']), [blocked])
        later.archived = True; later.save()
        self.assertNotContains(self.client.get(self.url), later.title)
        self.assertContains(self.client.get(self.url, {'archived':'1'}), later.title)

    def test_board_is_paginated_and_does_not_imply_complete_counts(self):
        for index in range(28): self.task(title=f'Page sample {index:02d}')
        response = self.client.get(self.url, {'layout':'board'})
        self.assertEqual(len(response.context['page_obj']),24)
        self.assertEqual(response.context['page_obj'].paginator.count,28)
        self.assertContains(response,'Board shows this page only')
        self.assertEqual(sum(len(column['tasks']) for column in response.context['columns']),24)

    def test_list_queries_do_not_grow_with_each_task_or_checklist(self):
        task = self.task()
        TaskChecklistItem.objects.create(task=task,title='First step',position=1)
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as small: self.client.get(self.url)
        for index in range(23):
            task = self.task(title=f'Query sample {index}')
            TaskChecklistItem.objects.create(task=task,title='Next sample step',position=1)
        with CaptureQueriesContext(connection) as large: response = self.client.get(self.url)
        self.assertEqual(len(response.context['page_obj']),24)
        self.assertLessEqual(len(large),len(small)+2)
        self.assertLess(len(large),50)

    def test_next_step_excludes_deleted_items_and_checklist_is_not_acceptance(self):
        task = self.task()
        removed = TaskChecklistItem.objects.create(task=task,title='Removed secret step',position=1)
        removed.delete(user=self.user)
        TaskChecklistItem.objects.create(task=task,title='Real next step',position=2)
        response = self.client.get(self.url)
        shown = response.context['page_obj'][0]
        self.assertEqual(shown.work_step_total,1)
        self.assertContains(response,'Real next step')
        self.assertNotContains(response,'Removed secret step')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'PENDING')

    def test_saved_filters_and_defaults_are_owner_local_and_rechecked(self):
        task = self.task(owner=self.peer,title='Foreign filter sample')
        save = reverse('ims:tasker:work_view_save')
        response = self.client.post(save,{'name':'Critical board','filters':json.dumps({'preset':'mine','layout':'board','priority':'CRITICAL','owner':str(self.peer.pk)})})
        self.assertEqual(response.status_code,302)
        saved = SavedWorkView.objects.get(owner=self.user)
        response = self.client.get(self.url,{'saved':saved.pk})
        self.assertEqual(response.context['layout'],'board')
        self.assertNotContains(response,task.title)
        self.client.post(reverse('ims:tasker:work_preferences_save'), {'preset':'waiting','layout':'board'})
        self.assertEqual(WorkbenchPreference.objects.get(owner=self.user).preset,'waiting')
        self.assertEqual(self.client.get(self.url).context['layout'],'board')
        self.client.force_login(self.peer)
        self.assertEqual(self.client.get(self.url,{'saved':saved.pk}).status_code,404)
        self.assertEqual(self.client.post(save,{'pk':saved.pk,'name':'Hijack','filters':'{}'}).status_code,403)
        self.assertEqual(self.client.post(reverse('ims:tasker:work_view_delete',args=(saved.pk,))).status_code,403)

    def test_saved_view_update_delete_and_bad_filter_inputs(self):
        saved = SavedWorkView.objects.create(owner=self.user,name='Saved',filters={})
        response = self.client.post(reverse('ims:tasker:work_view_save'), {'pk':saved.pk,'name':'Renamed','filters':'{"status":"BLOCKED"}'})
        self.assertEqual(response.status_code,302); saved.refresh_from_db()
        self.assertEqual(saved.name,'Renamed')
        self.assertEqual(saved.filters,{'status':'BLOCKED'})
        for bad in ('-1','9'*50,'x'):
            self.assertEqual(self.client.get(self.url,{'saved':bad}).status_code,404)
        self.assertEqual(self.client.get(self.url,{'priority':'INVALID'}).status_code,400)
        self.assertEqual(self.client.post(reverse('ims:tasker:work_view_save'),{'name':'Bad','filters':'{"owner":{}}'}).status_code,400)
        self.client.post(reverse('ims:tasker:work_view_delete',args=(saved.pk,)))
        self.assertFalse(SavedWorkView.objects.filter(pk=saved.pk).exists())
        self.assertTrue(SavedWorkView.all_objects.get(pk=saved.pk).is_deleted)

    def test_drawer_is_no_store_scoped_and_preserves_plain_text(self):
        task = self.task(title='<script>sample</script>',definition_of_done='Deliver <b>sample</b>')
        url = reverse('ims:tasker:task_preview',args=(task.slug,))
        response = self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response.json()['title'],task.title)
        self.assertEqual(response.json()['definition_of_done'],task.definition_of_done)
        self.client.force_login(self.peer)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_mixed_bulk_selection_cannot_partially_change_owned_work(self):
        owned, foreign = self.task(), self.task(owner=self.peer)
        response = self.client.post(reverse('ims:tasker:task_bulk_action'),{'action':'archive','task_ids':[owned.pk,foreign.pk], 'versions':json.dumps({str(item.pk):work_version(item) for item in (owned,foreign)})})
        self.assertEqual(response.status_code,403)
        self.assertFalse(Task.objects.get(pk=owned.pk).archived)
        self.assertFalse(Task.objects.get(pk=foreign.pk).archived)

    def test_stale_inline_action_has_recoverable_conflict_and_safe_return(self):
        task = self.task(); proof = json.dumps({str(task.pk):work_version(task)})
        task.notes='Newer work'; task.save()
        response = self.client.post(self.action_url(task,'priority'),{'value':'CRITICAL','versions':proof,'next':'https://example.test'})
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'No tasks were changed',status_code=409)
        self.assertEqual(Task.objects.get(pk=task.pk).priority,'MEDIUM')
        response = self.client.post(self.action_url(task,'priority'),{'value':'HIGH','versions':json.dumps({str(task.pk):work_version(task)}),'next':'https://example.test'})
        self.assertRedirects(response,self.url)

    def test_completion_requires_review_and_acceptance_of_current_outcome(self):
        task = self.task(definition_of_done='Signed customer acceptance')
        response = self.client.post(self.action_url(task,'complete'),{'next':self.url})
        self.assertContains(response,'Signed customer acceptance')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'PENDING')
        proof = response.context['versions_json']
        task.definition_of_done='New acceptance criterion'; task.save()
        url = reverse('ims:tasker:task_bulk_action')
        response = self.client.post(url,{'task_ids':[task.pk],'action':'complete','accept_definition':'on','versions':proof})
        self.assertEqual(response.status_code,409)
        response = self.client.post(url,{'task_ids':[task.pk],'action':'complete','accept_definition':'on','versions':json.dumps({str(task.pk):work_version(task)})})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Task.objects.get(pk=task.pk).status,'COMPLETED')

    def test_mutations_are_post_only_and_csrf_protected(self):
        task = self.task()
        self.assertEqual(self.client.get(self.action_url(task,'archive')).status_code,405)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.action_url(task,'archive'),{}).status_code,403)

    def test_same_timestamp_changes_still_invalidate_a_work_revision(self):
        task = self.task()
        proof = work_version(task)
        Task.objects.filter(pk=task.pk).update(notes='A newer same-clock draft')
        response = self.client.post(self.action_url(task,'archive'), {'versions':json.dumps({str(task.pk):proof})})
        self.assertEqual(response.status_code,409)
        self.assertFalse(Task.objects.get(pk=task.pk).archived)

    def test_change_permission_without_view_permission_cannot_preview_completion(self):
        user = get_user_model().objects.create_user('work-ui-change-only')
        user.user_permissions.add(Permission.objects.get(content_type__app_label='tasker',codename='change_task'))
        task = self.task(owner=user,definition_of_done='Protected criteria')
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.action_url(task,'complete'),{}).status_code,403)

    @override_settings(TASKER_DIRECTION_ENABLED=False)
    def test_direction_can_be_disabled_without_disabling_work(self):
        self.task()
        self.assertEqual(self.client.get(self.url).status_code,200)
