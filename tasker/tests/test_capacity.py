"""Actual interval feasibility, honest uncertainty and private exclusions."""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from accounting.models import Company
from tasker.models import Availability, PlanningEvent, ScheduleBlock, Task, Team, TeamMembership, WorkPolicy
from tasker.services.capacity import (
    PlanningConflict, aware_local, can_view_capacity, capacity_for_day,
    interval_intersections, interval_minutes, interval_subtract, interval_union,
    remove_availability, save_availability, save_work_policy, work_windows,
)
from tasker.services.effort import effort_for_task, set_remaining_effort
from tasker.services.scheduling import accept_schedule, preview_schedule, save_schedule_draft


class IntervalMathTests(SimpleTestCase):
    def window(self, start, end):
        return aware_local(date(2030,1,7),time(start),"Africa/Harare"), aware_local(date(2030,1,7),time(end),"Africa/Harare")

    def test_union_counts_touching_and_overlapping_exclusions_once(self):
        self.assertEqual(interval_minutes([self.window(9,11),self.window(10,12),self.window(12,13)]),240)
        self.assertEqual(len(interval_union([self.window(9,11),self.window(10,12)])),1)

    def test_subtraction_clips_to_working_windows(self):
        windows = [self.window(8,16)]
        excluded = [self.window(6,9),self.window(12,13),self.window(15,18)]
        self.assertEqual(interval_minutes(interval_intersections(windows,excluded)),180)
        self.assertEqual(interval_minutes(interval_subtract(windows,excluded)),300)

    def test_dst_work_duration_is_real_elapsed_time(self):
        policy = SimpleNamespace(configured=True,buffer_minutes=0,timezone_name="America/New_York",weekdays=[6],start_time=time(0),end_time=time(8))
        self.assertEqual(interval_minutes(work_windows(policy,date(2027,3,14))),420)
        self.assertEqual(interval_minutes(work_windows(policy,date(2027,11,7))),540)

    def test_ambiguous_and_nonexistent_wall_times_require_deliberate_correction(self):
        for day, clock in ((date(2027,3,14),time(2,30)),(date(2027,11,7),time(1,30))):
            with self.subTest(day=day), self.assertRaises(ValidationError): aware_local(day,clock,"America/New_York")

    def test_overnight_shift_is_clipped_into_local_calendar_dates(self):
        policy = SimpleNamespace(configured=True,buffer_minutes=0,timezone_name="Africa/Harare",weekdays=[0],start_time=time(20),end_time=time(4))
        self.assertEqual(interval_minutes(work_windows(policy,date(2030,1,7))),240)
        self.assertEqual(interval_minutes(work_windows(policy,date(2030,1,8))),240)
        self.assertEqual(interval_minutes(work_windows(policy,date(2030,1,9))),0)

    def test_naive_intervals_never_infer_the_host_computers_timezone(self):
        with self.assertRaises(ValidationError): interval_union([(datetime(2030,1,7,8),datetime(2030,1,7,9))])


class CapacityServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("capacity-owner")
        cls.manager = get_user_model().objects.create_user("capacity-manager")
        cls.peer = get_user_model().objects.create_user("capacity-peer")
        for user in (cls.user,cls.manager,cls.peer): user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        cls.company = Company.objects.create(name="Capacity sample company")
        cls.team = Team.objects.create(company=cls.company,name="Capacity sample team")
        TeamMembership.objects.create(team=cls.team,user=cls.user)
        cls.management = TeamMembership.objects.create(team=cls.team,user=cls.manager,role="MANAGER")
        cls.day = date(2030,1,7)

    def at(self,hour,minute=0,day=None): return aware_local(day or self.day,time(hour,minute),"Africa/Harare")

    def policy(self,**changes):
        args = dict(user=self.user,timezone_name="Africa/Harare",weekdays=[0,1,2,3,4],start_time=time(8),end_time=time(16),buffer_minutes=60)
        args.update(changes)
        return save_work_policy(**args)

    def test_unconfigured_and_missing_capacity_remain_unknown(self):
        result = capacity_for_day(user=self.user,day=self.day)
        self.assertFalse(result["known"]); self.assertIsNone(result["usable_minutes"])
        self.assertFalse(WorkPolicy.objects.exists())
        WorkPolicy.objects.create(owner=self.user)
        self.assertIsNone(capacity_for_day(user=self.user,day=self.day)["available_minutes"])

    def test_explicit_480_less_60_commitment_60_leave_60_buffer_is_300(self):
        self.policy()
        save_availability(user=self.user,kind="COMMITMENT",starts_at=self.at(9),ends_at=self.at(10))
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(11),ends_at=self.at(12))
        result = capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["work_minutes"],480)
        self.assertEqual(result["excluded_minutes"],120)
        self.assertEqual(result["usable_minutes"],300)

    def test_overlapping_leave_and_commitment_are_not_double_subtracted(self):
        self.policy()
        save_availability(user=self.user,kind="COMMITMENT",starts_at=self.at(9),ends_at=self.at(10))
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(9,30),ends_at=self.at(10,30))
        result = capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["excluded_minutes"],90)
        self.assertEqual(result["usable_minutes"],330)

    def test_exclusions_outside_work_do_not_consume_time_and_buffer_is_floored(self):
        self.policy(buffer_minutes=600)
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(4),ends_at=self.at(7))
        result = capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["excluded_minutes"],0)
        self.assertEqual(result["usable_minutes"],0)

    def test_policy_edit_is_versioned_and_validates_timezone_weekdays_buffer(self):
        policy = self.policy()
        with self.assertRaises(PlanningConflict): self.policy(buffer_minutes=0)
        updated = self.policy(buffer_minutes=0,expected_version=str(policy.revision))
        self.assertNotEqual(updated.revision,policy.revision)
        self.assertEqual(PlanningEvent.objects.filter(action="policy.saved").count(),2)
        for changes in ({"timezone_name":"Not/AZone"},{"weekdays":[True]},{"buffer_minutes":None},{"start_time":time(8),"end_time":time(8)}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                self.policy(expected_version=str(updated.revision),**changes)

    def test_availability_private_note_is_not_returned_to_the_manager(self):
        self.policy()
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(10),ends_at=self.at(11),private_note="Private medical details sample")
        result = capacity_for_day(user=self.user,day=self.day,actor=self.manager)
        self.assertEqual(result["leave_minutes"],60)
        self.assertNotIn("Private medical details sample",str(result))
        self.assertFalse(can_view_capacity(self.peer,self.user))
        with self.assertRaises(PermissionDenied): capacity_for_day(user=self.user,day=self.day,actor=self.peer)
        TeamMembership.objects.filter(pk=self.management.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied): capacity_for_day(user=self.user,day=self.day,actor=self.manager)

    def test_availability_mutations_are_self_only_and_stale_safe_recoverable(self):
        self.policy()
        item = save_availability(user=self.user,kind="COMMITMENT",starts_at=self.at(9),ends_at=self.at(10))
        with self.assertRaises(PermissionDenied): remove_availability(user=self.peer,item=item,expected_version=str(item.revision))
        with self.assertRaises(PlanningConflict): remove_availability(user=self.user,item=item,expected_version="stale")
        remove_availability(user=self.user,item=item,expected_version=str(item.revision))
        self.assertFalse(Availability.objects.filter(pk=item.pk).exists())
        self.assertTrue(Availability.all_objects.get(pk=item.pk).is_deleted)
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["usable_minutes"],420)

    def test_unknown_effort_and_total_estimate_are_not_silently_zero(self):
        task = Task.objects.create(owner=self.user,title="Unknown effort sample")
        self.assertIsNone(effort_for_task(task=task)["remaining_minutes"])
        task.estimated_duration=timedelta(minutes=60); task.save()
        self.assertEqual(effort_for_task(task=task)["remaining_minutes"],60)
        task.actual_duration=timedelta(minutes=5); task.save()
        result = effort_for_task(task=task)
        self.assertIsNone(result["remaining_minutes"]); self.assertTrue(result["needs_reestimate"])

    def test_explicit_remaining_anchor_and_overrun_require_new_judgement(self):
        task = Task.objects.create(owner=self.user,title="Remaining work sample",estimated_duration=timedelta(minutes=60),actual_duration=timedelta(minutes=5))
        current = effort_for_task(task=task)
        with self.assertRaises(ValidationError): set_remaining_effort(user=self.user,task=task,remaining_minutes=20,expected_version=current["version"])
        record = set_remaining_effort(user=self.user,task=task,remaining_minutes=20,reason="Reviewed remaining deliverable",expected_version=current["version"])
        task.refresh_from_db()
        self.assertEqual(effort_for_task(task=task)["remaining_minutes"],20)
        task.actual_duration=timedelta(minutes=15); task.save(); task.refresh_from_db()
        self.assertEqual(effort_for_task(task=task)["remaining_minutes"],10)
        task.actual_duration=timedelta(minutes=30); task.save(); task.refresh_from_db()
        result = effort_for_task(task=task)
        self.assertIsNone(result["remaining_minutes"]); self.assertTrue(result["needs_reestimate"])
        with self.assertRaises(PlanningConflict): set_remaining_effort(user=self.user,task=task,remaining_minutes=10,reason="Updated",expected_version=current["version"])

    def test_arrival_is_not_a_capacity_or_staffed_hour_input(self):
        self.policy()
        result = capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["usable_minutes"],420)
        self.assertNotIn("arrival",result)
        self.assertNotIn("productivity",result)

    def test_capacity_effort_queries_are_batched_as_schedule_grows(self):
        self.policy()
        for index in range(24):
            task=Task.objects.create(owner=self.user,title=f"Capacity query sample {index}",estimated_duration=timedelta(minutes=60))
            ScheduleBlock.objects.create(task=task,owner=self.user,starts_at=self.at(8),ends_at=self.at(9),state="ACCEPTED")
            if index == 0:
                with CaptureQueriesContext(connection) as small: capacity_for_day(user=self.user,day=self.day)
        with CaptureQueriesContext(connection) as large: result=capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["committed_minutes"],1440)
        self.assertLessEqual(len(large),len(small)+2)
        self.assertLess(len(large),20)

    def test_new_leave_warns_about_existing_accepted_work_even_without_aggregate_overload(self):
        self.policy()
        task=Task.objects.create(owner=self.user,title="Accepted before availability change",estimated_duration=timedelta(minutes=120))
        draft=save_schedule_draft(user=self.user,task=task,starts_at=self.at(9),ends_at=self.at(10))
        preview=preview_schedule(user=self.user,task=task,starts_at=draft.starts_at,ends_at=draft.ends_at,block=draft)
        accept_schedule(user=self.user,block=draft,expected_version=str(draft.revision),preview_version=preview["version"])
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(9),ends_at=self.at(10),private_note="Private sample leave explanation")
        result=capacity_for_day(user=self.user,day=self.day,actor=self.manager)
        self.assertEqual(result["overload_minutes"],0)
        self.assertEqual(result["availability_conflict_count"],1)
        self.assertTrue(any("overlaps current leave" in warning for warning in result["warnings"]))
        self.assertNotIn("Private sample leave explanation",str(result))

    def test_changed_hours_and_overlapping_accepted_blocks_have_distinct_generic_warnings(self):
        policy=self.policy()
        for index, (start,end) in enumerate(((8,10),(9,11))):
            task=Task.objects.create(owner=self.user,title=f"Existing block sample {index}",estimated_duration=timedelta(minutes=120))
            ScheduleBlock.objects.create(task=task,owner=self.user,starts_at=self.at(start),ends_at=self.at(end),state="ACCEPTED")
        save_work_policy(user=self.user,timezone_name="Africa/Harare",weekdays=[0,1,2,3,4],start_time=time(10),end_time=time(18),buffer_minutes=60,expected_version=str(policy.revision))
        result=capacity_for_day(user=self.user,day=self.day)
        self.assertEqual(result["outside_hours_count"],2)
        self.assertEqual(result["overlap_conflict_count"],2)
        self.assertTrue(any("outside the current working hours" in warning for warning in result["warnings"]))
        self.assertTrue(any("overlap one another" in warning for warning in result["warnings"]))
