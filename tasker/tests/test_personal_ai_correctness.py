"""Regression coverage for Stage 1 personal AI boundaries; no live provider calls."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from tasker.ai.exceptions import AIConfigurationError, AIResponseValidationError
from tasker.ai.personal_assistant import PersonalProductivityAssistant, apply_suggested_steps
from tasker.ai.personal_context import build_personal_context
from tasker.ai.service import AIService
from tasker.models import AIConfiguration, AIMessage, DailyPlan, DailyReview, Task, TaskChecklistItem, TaskTimeEntry
from tasker.tests.test_personal_assistant import JSONProvider, NoopLimiter


def response_examples(task_id=11):
    return {
        "plan_day": {
            "headline": "A practical plan", "focus": "Finish the proposal",
            "recommendations": [{"task_id": task_id, "title": "Proposal", "order": 1,
                                 "suggested_start": "09:00", "estimated_minutes": 25, "why": "It is due."}],
            "reasoning": "Use the ready work.",
        },
        "next_task": {
            "task_id": task_id, "title": "Proposal", "next_action": "Draft an outline",
            "estimated_focus_minutes": 25, "why": "It is ready.", "reasoning": "It creates progress.",
        },
        "prioritize": {
            "headline": "Ready work first", "ranked_tasks": [{"rank": 1, "task_id": task_id,
                "title": "Proposal", "why": "It is due.", "next_action": "Draft an outline"}],
            "reasoning": "Respect the deadline.",
        },
        "summarize_day": {
            "headline": "Recorded work", "accomplishments": [], "insights": [],
            "unfinished": [{"task_id": task_id, "title": "Proposal", "why_unfinished": "Still pending",
                            "next_action": "Draft an outline"}],
            "tomorrow_focus": "Finish the proposal", "reasoning": "Use recorded status only.",
        },
        "break_task": {
            "task_id": task_id, "task_title": "Proposal", "approach": "Work in order",
            "steps": [{"order": index, "title": title, "why": "Makes progress", "estimated_minutes": 10}
                      for index, title in enumerate(("Gather requirements", "Draft proposal", "Review figures"), 1)],
            "reasoning": "Small concrete steps.",
        },
        "explain_task": {
            "task_id": task_id, "title": "Proposal", "plain_language_goal": "Deliver a proposal",
            "why_it_matters": "A recorded commitment", "definition_of_done": "Proposal reviewed",
            "first_action": "Draft an outline", "risks": [], "reasoning": "Use the task record.",
        },
    }


class PersonalAIResponseValidationTests(SimpleTestCase):
    def setUp(self):
        self.context = {"current_workload": [
            {"id": 11, "title": "Canonical proposal", "status": Task.Status.PENDING},
            {"id": 12, "title": "Blocked work", "status": Task.Status.BLOCKED},
        ], "completed_today": [{"id": 13, "title": "Completed", "status": Task.Status.COMPLETED}]}
        self.task = SimpleNamespace(pk=11, status=Task.Status.PENDING, archived=False, is_deleted=False)

    def validate(self, feature, payload):
        return PersonalProductivityAssistant._validate(
            payload=payload, feature=feature, context=self.context,
            task=self.task if feature in {"break_task", "explain_task"} else None,
        )

    def test_all_six_valid_contracts_remain_supported(self):
        for feature, payload in response_examples().items():
            with self.subTest(feature=feature):
                self.validate(feature, payload)

    def test_all_six_reject_missing_extra_and_wrong_type_fields(self):
        for feature, original in response_examples().items():
            for change in ("missing", "extra", "type", "blank"):
                payload = copy.deepcopy(original)
                if change == "missing":
                    del payload[next(key for key in payload if key != "reasoning")]
                elif change == "extra":
                    payload["unexpected_action"] = "execute"
                else:
                    payload["reasoning"] = False if change == "type" else "   "
                with self.subTest(feature=feature, change=change), self.assertRaises(AIResponseValidationError):
                    self.validate(feature, payload)

    def test_malformed_recommendation_is_a_controlled_validation_error(self):
        payload = response_examples()["plan_day"]
        payload["recommendations"] = ["malformed"]
        with self.assertRaises(AIResponseValidationError):
            self.validate("plan_day", payload)

    def test_blocked_or_terminal_work_is_never_recommended_as_ready(self):
        for feature in ("next_task", "plan_day", "prioritize"):
            for task_id in (12, 13, 999):
                payload = response_examples(task_id)[feature]
                with self.subTest(feature=feature, task_id=task_id), self.assertRaises(AIResponseValidationError):
                    self.validate(feature, payload)

    def test_negative_boolean_and_duplicate_plan_values_are_rejected(self):
        for change in ("negative", "boolean_id", "duplicate", "bad_order"):
            payload = response_examples()["plan_day"]
            item = payload["recommendations"][0]
            if change == "negative":
                item["estimated_minutes"] = -5
            elif change == "boolean_id":
                item["task_id"] = True
                self.context["current_workload"].append({"id": 1, "title": "Task", "status": Task.Status.PENDING})
            elif change == "duplicate":
                payload["recommendations"].append({**item, "order": 2})
            else:
                item["order"] = 0
            with self.subTest(change=change), self.assertRaises(AIResponseValidationError):
                self.validate("plan_day", payload)

    def test_summary_rejects_unknown_or_completed_unfinished_ids(self):
        for task_id in (13, 999):
            with self.subTest(task_id=task_id), self.assertRaises(AIResponseValidationError):
                self.validate("summarize_day", response_examples(task_id)["summarize_day"])

    def test_breakdown_rejects_invalid_step_duration_and_duplicate_titles(self):
        for change in ("duration", "duplicate", "wrong_task"):
            payload = response_examples()["break_task"]
            if change == "duration":
                payload["steps"][0]["estimated_minutes"] = False
            elif change == "duplicate":
                payload["steps"][1]["title"] = payload["steps"][0]["title"].upper()
            else:
                payload["task_id"] = 999
            with self.subTest(change=change), self.assertRaises(AIResponseValidationError):
                self.validate("break_task", payload)


@override_settings(TASKER_AI_OPENAI_API_KEY="test-key")
class PersonalAIContextAndApplyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="stage1-personal")
        self.other = get_user_model().objects.create_user(username="stage1-other")
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("use_ai", "view_task", "change_task", "add_taskchecklistitem"),
        ))
        self.configuration = AIConfiguration.objects.get(provider="OPENAI")
        self.configuration.is_enabled = True
        self.configuration.save(update_fields=["is_enabled", "updated_at"])
        self.task = Task.objects.create(title="Canonical proposal", owner=self.user)

    def assistant(self, payload):
        provider = JSONProvider(payload)
        service = AIService(provider_factory=lambda configuration: provider, rate_limiter=NoopLimiter())
        return PersonalProductivityAssistant(service), provider

    def make_proposal(self):
        assistant, _ = self.assistant(response_examples(self.task.pk)["break_task"])
        return assistant.run(user=self.user, feature="break_task", task=self.task)[1]

    def test_context_uses_semantic_priority_ranking(self):
        for priority in (Task.Priority.LOW, Task.Priority.HIGH, Task.Priority.CRITICAL):
            Task.objects.create(title=priority, owner=self.user, priority=priority)
        context = build_personal_context(user=self.user)
        self.assertEqual([item["priority"] for item in context["current_workload"]],
                         [Task.Priority.CRITICAL, Task.Priority.HIGH, Task.Priority.MEDIUM, Task.Priority.LOW])

    def test_context_keeps_focus_top_commitment_and_undated_critical_beyond_cap(self):
        today = timezone.localdate()
        Task.objects.bulk_create([Task(title=f"Old low {index}", slug=f"stage1-old-low-{index}", owner=self.user,
            priority=Task.Priority.LOW, due_date=today - timedelta(days=20)) for index in range(76)])
        focused = Task.objects.create(title="Current focus", owner=self.user, priority=Task.Priority.LOW,
                                      status=Task.Status.IN_PROGRESS)
        commitment = Task.objects.create(title="Top commitment", owner=self.user)
        critical = Task.objects.create(title="Undated critical", owner=self.user, priority=Task.Priority.CRITICAL)
        plan = DailyPlan.objects.create(owner=self.user, plan_date=today)
        plan.top_priorities.add(commitment)
        TaskTimeEntry.objects.create(task=focused, user=self.user, started_at=timezone.now(),
                                     entry_type=TaskTimeEntry.EntryType.FOCUS)
        context = build_personal_context(user=self.user)
        included = {item["id"] for item in context["current_workload"]}
        self.assertTrue({focused.pk, commitment.pk, critical.pk}.issubset(included))
        self.assertEqual(context["workload"]["active_task_count"], 80)
        self.assertEqual(context["workload"]["included_task_count"], len(included))
        self.assertEqual(context["workload"]["omitted_task_count"], 80 - len(included))
        self.assertLessEqual(len(included), 75)

    def test_context_is_deduplicated_bounded_and_excludes_private_journal(self):
        today = timezone.localdate()
        plan = DailyPlan.objects.create(owner=self.user, plan_date=today, quick_notes="PRIVATE-SCRATCHPAD")
        DailyReview.objects.create(plan=plan, accomplishments="PRIVATE-REFLECTION", lessons_learned="PRIVATE-LESSON")
        Task.objects.bulk_create([Task(title=f"Long work {index}", slug=f"stage1-long-{index}",
            owner=self.user, description="x" * 2000, due_date=today, carry_forward=True) for index in range(75)])
        context = build_personal_context(user=self.user)
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        self.assertNotIn("PRIVATE-", serialized)
        self.assertNotIn("today_tasks", context)
        self.assertNotIn("carry_over_tasks", context)
        self.assertTrue(all(type(value) is int for value in context["today_task_ids"]))
        self.assertTrue(all(type(value) is int for value in context["carry_over_task_ids"]))
        self.assertLessEqual(len(serialized), context["context_limits"]["max_characters"])

    def test_blocked_critical_backlog_cannot_hide_the_only_executable_task(self):
        self.task.priority = Task.Priority.LOW
        self.task.save()
        Task.objects.bulk_create([Task(title=f"Blocked critical {index}", slug=f"blocked-cap-{index}",
            owner=self.user, priority=Task.Priority.CRITICAL, status=Task.Status.BLOCKED,
            blocked_reason="Waiting for approval") for index in range(75)])
        context = build_personal_context(user=self.user)
        self.assertEqual(context["workload"]["active_task_count"], 76)
        self.assertEqual(context["workload"]["executable_task_count"], 1)
        self.assertIn(self.task.pk, {item["id"] for item in context["current_workload"]})
        self.assertEqual(context["workload"]["omitted_task_count"],
                         76 - len(context["current_workload"]))
        self.assertGreaterEqual(context["context_limits"]["omitted_mandatory_task_count"], 1)
        self.assertLessEqual(len(context["current_workload"]), 75)
        for feature in ("next_task", "plan_day", "prioritize"):
            with self.subTest(feature=feature):
                assistant, provider = self.assistant(response_examples(self.task.pk)[feature])
                result, _ = assistant.run(user=self.user, feature=feature)
                self.assertTrue(result)
                self.assertIsNotNone(provider.kwargs)

    def test_tiny_context_budget_reports_configuration_error_not_no_ready_work(self):
        context = build_personal_context(user=self.user)
        # Allow a metadata envelope but not a task. Previously fitting would
        # drop the real ready task and report a false empty-work condition.
        empty = copy.deepcopy(context)
        empty["current_workload"] = []
        empty["today_task_ids"] = []
        empty["carry_over_task_ids"] = []
        empty["daily_plan"].update(top_priority_ids=[], planned_task_ids=[])
        empty["workload"].update(included_task_count=0, omitted_task_count=1)
        empty["context_limits"].update(description_character_limit=0, omitted_mandatory_task_count=1)
        budget = len(json.dumps(empty, ensure_ascii=False, separators=(",", ":"))) + 64
        assistant, provider = self.assistant(response_examples(self.task.pk)["next_task"])
        with override_settings(TASKER_AI_MAX_PROMPT_CHARACTERS=budget + 4096):
            with self.assertRaises(AIConfigurationError):
                assistant.run(user=self.user, feature="next_task")
        self.assertIsNone(provider.kwargs)

    def test_context_checklist_counts_ignore_soft_deleted_items(self):
        TaskChecklistItem.objects.create(task=self.task, title="Live", position=1)
        deleted = TaskChecklistItem.objects.create(task=self.task, title="Deleted", position=2, is_completed=True)
        deleted.delete(user=self.user)
        item = build_personal_context(user=self.user)["current_workload"][0]
        self.assertEqual(item["checklist"], {"total": 1, "completed": 0})

    def test_model_title_is_replaced_with_canonical_record(self):
        payload = response_examples(self.task.pk)["next_task"]
        payload["title"] = "Invented task name"
        assistant, _ = self.assistant(payload)
        result, _ = assistant.run(user=self.user, feature="next_task")
        self.assertEqual(result["title"], self.task.title)

    def test_all_blocked_next_task_does_not_call_provider(self):
        self.task.status = Task.Status.BLOCKED
        self.task.blocked_reason = "Waiting for input"
        self.task.save()
        assistant, provider = self.assistant(response_examples(self.task.pk)["next_task"])
        with self.assertRaises(AIResponseValidationError):
            assistant.run(user=self.user, feature="next_task")
        self.assertIsNone(provider.kwargs)

    def test_stale_task_and_checklist_proposals_cannot_be_applied(self):
        for change in ("title", "checklist", "archived", "completed", "deleted", "owner"):
            with self.subTest(change=change):
                task = Task.objects.create(title=f"Change {change}", owner=self.user)
                assistant, _ = self.assistant(response_examples(task.pk)["break_task"])
                _, message = assistant.run(user=self.user, feature="break_task", task=task)
                if change == "title":
                    Task.objects.filter(pk=task.pk).update(title="Changed after proposal")
                elif change == "checklist":
                    TaskChecklistItem.objects.create(task=task, title="New human step", position=1)
                elif change == "owner":
                    Task.objects.filter(pk=task.pk).update(owner=self.other)
                elif change == "completed":
                    Task.objects.filter(pk=task.pk).update(status=Task.Status.COMPLETED)
                else:
                    Task.objects.filter(pk=task.pk).update(**{"is_deleted" if change == "deleted" else change: True})
                with self.assertRaises((AIResponseValidationError, PermissionError)):
                    apply_suggested_steps(user=self.user, task=task, message_id=message.pk, selected_indexes=[0])
                self.assertFalse(TaskChecklistItem.objects.filter(task=task, title="Gather requirements").exists())

    def test_apply_rechecks_revoked_permissions(self):
        message = self.make_proposal()
        self.user.user_permissions.clear()
        user = get_user_model().objects.get(pk=self.user.pk)
        with self.assertRaises(PermissionError):
            apply_suggested_steps(user=user, task=self.task, message_id=message.pk, selected_indexes=[0])
        self.assertFalse(TaskChecklistItem.objects.filter(task=self.task).exists())

    def test_empty_selection_does_not_consume_valid_proposal(self):
        message = self.make_proposal()
        self.assertEqual(apply_suggested_steps(user=self.user, task=self.task, message_id=message.pk, selected_indexes=[]), 0)
        self.assertEqual(apply_suggested_steps(user=self.user, task=self.task, message_id=message.pk, selected_indexes=[0]), 1)
