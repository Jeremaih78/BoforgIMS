"""Stage 6 user journeys, privacy, dates and mutation proofs."""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tasker.models import Availability, DailyPlan, ScheduleBlock, Task, TaskDependency, WorkPolicy
from tasker.planning_forms import local_datetime
from tasker.services.capacity import save_work_policy
from tasker.services.dashboard import build_today_dashboard,save_quick_notes
from tasker.services.dependencies import add_dependency, readiness_for
from tasker.services.effort import effort_for_task
from tasker.services.planner import plan_version
from tasker.services.scheduling import preview_schedule, save_schedule_draft


class PlanningViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=get_user_model().objects.create_user('planning-ui-owner')
        cls.peer=get_user_model().objects.create_user('planning-ui-peer')
        for user in (cls.user,cls.peer):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))

    def setUp(self):
        self.client.force_login(self.user)
        self.day=timezone.localdate()+timedelta(days=2)
        self.zone=ZoneInfo('Africa/Harare')
        self.start=datetime.combine(self.day,time(10),tzinfo=self.zone)

    def task(self,**changes):
        return Task.objects.create(**{'owner':self.user,'title':'Stage 6 UI sample','estimated_duration':timedelta(hours=2),**changes})

    def policy(self):
        return save_work_policy(user=self.user,timezone_name='Africa/Harare',weekdays=list(range(7)),start_time=time(8),end_time=time(16),buffer_minutes=60)

    def draft(self,task=None,**changes):
        return save_schedule_draft(user=self.user,task=task or self.task(),starts_at=changes.get('starts_at',self.start),ends_at=changes.get('ends_at',self.start+timedelta(hours=1)))

    def test_unknown_capacity_get_is_private_readonly_and_honest(self):
        response=self.client.get(reverse('ims:tasker:planning_home'))
        self.assertContains(response,'Capacity unknown')
        self.assertIn('no-store',response['Cache-Control'])
        self.assertFalse(WorkPolicy.objects.exists())
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_self_policy_and_unavailable_time_forms_persist_in_displayed_zone(self):
        response=self.client.post(reverse('ims:tasker:work_policy'),{'timezone_name':'Africa/Harare','weekdays':['0','1','2','3','4'],'start_time':'08:00','end_time':'16:00','buffer_minutes':'60'})
        self.assertEqual(response.status_code,302)
        policy=WorkPolicy.objects.get(owner=self.user)
        self.assertTrue(policy.configured)
        response=self.client.post(reverse('ims:tasker:availability_home'),{'kind':'LEAVE','starts_at':f'{self.day}T12:00','ends_at':f'{self.day}T13:00','private_note':'Synthetic private exclusion'})
        self.assertEqual(response.status_code,302)
        item=Availability.objects.get(owner=self.user)
        self.assertEqual(item.starts_at.astimezone(self.zone).hour,12)
        self.client.force_login(self.peer)
        self.assertNotContains(self.client.get(reverse('ims:tasker:availability_home')),'Synthetic private exclusion')
        self.assertEqual(self.client.post(reverse('ims:tasker:availability_remove',args=(item.pk,)),{'expected_version':str(item.revision)}).status_code,404)

    def test_policy_stale_proof_keeps_edit_draft(self):
        policy=self.policy()
        response=self.client.post(reverse('ims:tasker:work_policy'),{'timezone_name':'Africa/Harare','weekdays':['2'],'start_time':'09:00','end_time':'15:00','buffer_minutes':'35','expected_version':'11111111-1111-4111-8111-111111111111'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'35')
        policy.refresh_from_db(); self.assertEqual(policy.start_time,time(8))

    def test_schedule_draft_preview_accept_preserves_deadline(self):
        self.policy(); task=self.task(due_date=self.day+timedelta(days=1),due_time=time(15))
        url=reverse('ims:tasker:schedule_create')+'?task='+task.slug
        response=self.client.get(url); self.assertContains(response,'Africa/Harare')
        response=self.client.post(url,{'task':task.pk,'starts_at':f'{self.day}T10:00','ends_at':f'{self.day}T11:00'})
        self.assertEqual(response.status_code,302)
        block=ScheduleBlock.objects.get(task=task)
        response=self.client.get(response.url)
        preview=response.context['preview']
        self.assertEqual(block.state,'DRAFT')
        response=self.client.post(reverse('ims:tasker:schedule_accept',args=(block.pk,)),{'expected_version':preview['block_version'],'preview_version':preview['version']})
        self.assertEqual(response.status_code,302)
        block.refresh_from_db();task.refresh_from_db()
        self.assertEqual(block.state,'ACCEPTED')
        self.assertEqual((task.due_date,task.due_time),(self.day+timedelta(days=1),time(15)))

    def test_unknown_effort_and_capacity_warn_before_explicit_override(self):
        block=self.draft(self.task(estimated_duration=None))
        response=self.client.get(reverse('ims:tasker:schedule_detail',args=(block.pk,)))
        self.assertContains(response,'Capacity is unknown')
        self.assertContains(response,'Remaining effort is unknown')
        preview=response.context['preview']
        url=reverse('ims:tasker:schedule_accept',args=(block.pk,))
        payload={'expected_version':preview['block_version'],'preview_version':preview['version']}
        self.assertEqual(self.client.post(url,payload).status_code,409)
        block.refresh_from_db(); self.assertEqual(block.state,'DRAFT')
        self.assertEqual(self.client.post(url,{**payload,'override_reason':'Synthetic deliberate unknown-capacity commitment'}).status_code,302)
        block.refresh_from_db();self.assertEqual(block.state,'ACCEPTED')
        self.assertTrue(block.override_reason)

    def test_changed_profile_invalidates_accept_preview(self):
        policy=self.policy();block=self.draft()
        preview=preview_schedule(user=self.user,task=block.task,starts_at=block.starts_at,ends_at=block.ends_at,block=block)
        save_work_policy(user=self.user,timezone_name='Africa/Harare',weekdays=list(range(7)),start_time=time(8),end_time=time(16),buffer_minutes=120,expected_version=policy.revision)
        response=self.client.post(reverse('ims:tasker:schedule_accept',args=(block.pk,)),{'expected_version':preview['block_version'],'preview_version':preview['version']})
        self.assertEqual(response.status_code,409)
        block.refresh_from_db();self.assertEqual(block.state,'DRAFT')

    def test_private_blocks_dependencies_and_effort_reject_foreign_urls(self):
        task=self.task();block=self.draft(task)
        self.client.force_login(self.peer)
        for name,args in [('schedule_detail',(block.pk,)),('schedule_edit',(block.pk,)),('task_dependencies',(task.slug,)),('task_effort',(task.slug,))]:
            self.assertEqual(self.client.get(reverse('ims:tasker:'+name,args=args)).status_code,404)
        self.assertEqual(self.client.get(reverse('ims:tasker:planning_home'),{'owner':self.user.pk}).status_code,403)

    def test_dependency_ui_waiting_then_completion_makes_work_startable(self):
        task=self.task(title='Dependent UI sample');required=self.task(title='Prerequisite UI sample')
        url=reverse('ims:tasker:task_dependencies',args=(task.slug,))
        proof=readiness_for(task=task,user=self.user).version
        self.assertEqual(self.client.post(url,{'action':'add','prerequisite':required.pk,'expected_version':proof}).status_code,302)
        self.assertContains(self.client.get(url),required.title)
        response=self.client.get(reverse('ims:tasker:task_list'))
        row=next(item for item in response.context['page_obj'] if item.pk==task.pk)
        self.assertFalse(row.work_startable)
        required.status='COMPLETED';required.save()
        response=self.client.get(reverse('ims:tasker:task_list'))
        row=next(item for item in response.context['page_obj'] if item.pk==task.pk)
        self.assertTrue(row.work_startable)

    def test_effort_form_uses_opaque_proof_and_preserves_task_status(self):
        task=self.task();url=reverse('ims:tasker:task_effort',args=(task.slug,))
        proof=effort_for_task(task=task)['version']
        self.assertContains(self.client.get(url),proof)
        response=self.client.post(url,{'remaining_minutes':'30','reason':'Synthetic smaller scope','expected_version':proof})
        self.assertEqual(response.status_code,302)
        task.refresh_from_db();self.assertEqual(task.status,'PENDING')
        self.assertEqual(effort_for_task(task=task)['remaining_minutes'],30)

    def test_planner_future_and_past_dates_keep_records_separate(self):
        url=reverse('ims:tasker:daily_plan')+'?date='+str(self.day)
        task=self.task()
        response=self.client.post(url,{'focus_area':'Future synthetic intent','top_priorities':[task.pk],'planned_tasks':[task.pk],'quick_notes':'Private future sample'})
        self.assertRedirects(response,url)
        self.assertTrue(DailyPlan.objects.filter(owner=self.user,plan_date=self.day).exists())
        response=self.client.get(reverse('ims:tasker:daily_plan'))
        self.assertNotContains(response,'Private future sample')
        self.assertContains(self.client.get(url),'Private future sample')
        self.assertEqual(self.client.get(reverse('ims:tasker:planning_home'),{'date':'bad'}).status_code,404)

    def test_stale_intentions_cannot_overwrite_private_notes(self):
        plan=DailyPlan.objects.create(owner=self.user,plan_date=self.day,quick_notes='Original synthetic intent')
        proof=plan_version(plan)
        plan.quick_notes='Newer synthetic note';plan.save()
        response=self.client.post(reverse('ims:tasker:daily_plan')+'?date='+str(self.day),{'focus_area':'Retained draft','quick_notes':'Stale replacement','plan_version':proof})
        self.assertContains(response,'Retained draft')
        self.assertContains(response,'intentions changed')
        plan.refresh_from_db();self.assertEqual(plan.quick_notes,'Newer synthetic note')

    def test_planning_mutations_are_post_only_and_csrf_protected(self):
        block=self.draft()
        url=reverse('ims:tasker:schedule_accept',args=(block.pk,))
        self.assertEqual(self.client.get(url).status_code,405)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(url,{}).status_code,403)

    def test_timezone_wall_input_rejects_ambiguous_and_missing_times(self):
        from django.core.exceptions import ValidationError
        zone=ZoneInfo('America/New_York')
        for value in ('2026-03-08T02:30','2026-11-01T01:30'):
            with self.assertRaises(ValidationError):local_datetime(value,zone)
        self.assertEqual(local_datetime('2026-11-01T03:30',zone).hour,3)

    def test_missing_local_calendar_boundary_is_validation_not_server_error(self):
        WorkPolicy.objects.create(owner=self.user,timezone_name='Pacific/Apia')
        response=self.client.get(reverse('ims:tasker:planning_home'),{'date':'2011-12-30'})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'ambiguous or does not exist',status_code=400)
        response=self.client.get(reverse('ims:tasker:daily_plan'),{'date':'2011-12-30'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Capacity is unavailable')

    def test_previous_review_cannot_unset_current_carry_over_choices(self):
        task=self.task(carry_forward=True)
        yesterday=timezone.localdate()-timedelta(days=1)
        plan=DailyPlan.objects.create(owner=self.user,plan_date=yesterday)
        plan.planned_tasks.add(task)
        url=reverse('ims:tasker:daily_review')+'?date='+str(yesterday)
        self.assertContains(self.client.get(url),'This date is for review only')
        self.assertEqual(self.client.post(url,{'accomplishments':'Historical review','energy_level':'3'}).status_code,403)
        task.refresh_from_db();self.assertTrue(task.carry_forward)

    def test_dashboard_planner_and_scratchpad_share_the_employee_local_date(self):
        WorkPolicy.objects.create(owner=self.user,timezone_name='America/Los_Angeles')
        instant=datetime(2026,10,28,1,tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now',return_value=instant):
            self.assertEqual(self.client.get(reverse('ims:tasker:daily_plan')).context['plan_date'],date(2026,10,27))
            self.assertEqual(build_today_dashboard(user=self.user).date,date(2026,10,27))
            save_quick_notes(user=self.user,notes='Timezone consistency sample')
        self.assertTrue(DailyPlan.objects.filter(owner=self.user,plan_date=date(2026,10,27),quick_notes='Timezone consistency sample').exists())

    def test_error_preview_rechecks_scope_after_concurrent_reassignment(self):
        block=self.draft()
        def revoke(**kwargs):
            Task.objects.filter(pk=block.task_id).update(owner=self.peer,title='Now private peer title')
            raise ValueError('The plan changed')
        with patch('tasker.views.planning.accept_schedule',side_effect=revoke):
            response=self.client.post(reverse('ims:tasker:schedule_accept',args=(block.pk,)),{'expected_version':str(block.revision),'preview_version':'stale'})
        self.assertEqual(response.status_code,404)
        self.assertNotContains(response,'Now private peer title',status_code=404)
