from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasker.models import DailyPlan, DailyReview, Task
from tasker.services.dashboard import build_today_dashboard


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES)
class DailyPlannerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="planner-user", password="pass")
        self.other = get_user_model().objects.create_user(username="other-planner", password="pass")
        codenames = (
            "view_dailyplan",
            "add_dailyplan",
            "change_dailyplan",
            "view_dailyreview",
            "add_dailyreview",
            "change_dailyreview",
            "view_task",
            "add_task",
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="tasker", codename__in=codenames)
        )
        self.client.force_login(self.user)
        self.plan_url = reverse("ims:tasker:daily_plan")
        self.review_url = reverse("ims:tasker:daily_review")

    def make_task(self, title, owner=None, **kwargs):
        return Task.objects.create(
            title=title,
            owner=owner or self.user,
            created_by=self.user,
            updated_by=self.user,
            **kwargs,
        )

    def plan_payload(self, priorities=(), planned=(), **overrides):
        payload = {
            "focus_area": "Complete the customer onboarding work",
            "motivational_quote": "Clarity creates momentum.",
            "top_priorities": [str(task.pk) for task in priorities],
            "planned_tasks": [str(task.pk) for task in planned],
            "quick_notes": "Call the customer before lunch.",
        }
        payload.update(overrides)
        return payload

    def test_planner_get_is_read_only_and_shows_default_quote(self):
        response = self.client.get(self.plan_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Success is the sum of small efforts")
        self.assertFalse(DailyPlan.objects.exists())

    def test_save_plan_audits_and_adds_priorities_to_planned_tasks(self):
        first = self.make_task("Confirm stock availability")
        second = self.make_task("Send final quotation")

        response = self.client.post(
            self.plan_url,
            self.plan_payload(priorities=(first, second), planned=(first,)),
        )

        self.assertRedirects(response, self.plan_url)
        plan = DailyPlan.objects.get(owner=self.user, plan_date=timezone.localdate())
        self.assertEqual(plan.created_by, self.user)
        self.assertEqual(plan.updated_by, self.user)
        self.assertEqual(set(plan.top_priorities.all()), {first, second})
        self.assertEqual(set(plan.planned_tasks.all()), {first, second})
        self.assertEqual(build_today_dashboard(user=self.user).quick_notes, plan.quick_notes)

    def test_plan_rejects_more_than_four_priorities(self):
        tasks = [self.make_task(f"Priority {number}") for number in range(5)]

        response = self.client.post(
            self.plan_url,
            self.plan_payload(priorities=tasks, planned=tasks),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose no more than four top priorities")
        self.assertFalse(DailyPlan.objects.exists())

    def test_plan_rejects_another_employees_task(self):
        private_task = self.make_task("Private work", owner=self.other)

        response = self.client.post(
            self.plan_url,
            self.plan_payload(priorities=(private_task,), planned=(private_task,)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DailyPlan.objects.exists())

    def test_review_requires_a_saved_daily_plan(self):
        response = self.client.get(self.review_url)

        self.assertRedirects(response, self.plan_url)
        self.assertFalse(DailyReview.objects.exists())

    def test_review_saves_reflection_and_marks_carry_over(self):
        carried = self.make_task("Finish onboarding checklist")
        plan = DailyPlan.objects.create(
            owner=self.user,
            plan_date=timezone.localdate(),
            created_by=self.user,
            updated_by=self.user,
        )
        plan.planned_tasks.add(carried)

        response = self.client.post(
            self.review_url,
            {
                "accomplishments": "Completed the stock review and customer call.",
                "energy_level": "4",
                "most_productive_time": "Late morning",
                "biggest_distraction": "Unplanned walk-in requests.",
                "lessons_learned": "Protect the first focus block.",
                "carry_over_tasks": [str(carried.pk)],
                "tomorrow_focus": "Finish onboarding before starting new work.",
            },
        )

        self.assertRedirects(response, self.review_url)
        review = DailyReview.objects.get(plan=plan)
        carried.refresh_from_db()
        self.assertEqual(review.energy_level, 4)
        self.assertEqual(review.created_by, self.user)
        self.assertEqual(review.updated_by, self.user)
        self.assertEqual(list(review.carry_over_tasks.all()), [carried])
        self.assertTrue(carried.carry_forward)
        self.assertTrue(build_today_dashboard(user=self.user).has_daily_review)

    def test_editing_review_removes_deselected_carry_over_flag(self):
        carried = self.make_task("Carry this once", carry_forward=True)
        plan = DailyPlan.objects.create(owner=self.user, created_by=self.user, updated_by=self.user)
        plan.planned_tasks.add(carried)
        review = DailyReview.objects.create(
            plan=plan,
            accomplishments="Initial review",
            energy_level=3,
            created_by=self.user,
            updated_by=self.user,
        )
        review.carry_over_tasks.add(carried)

        self.client.post(
            self.review_url,
            {
                "accomplishments": "Updated review",
                "energy_level": "3",
                "most_productive_time": "Early afternoon",
                "biggest_distraction": "",
                "lessons_learned": "",
                "tomorrow_focus": "Start fresh",
            },
        )

        carried.refresh_from_db()
        review.refresh_from_db()
        self.assertFalse(carried.carry_forward)
        self.assertFalse(review.carry_over_tasks.exists())
