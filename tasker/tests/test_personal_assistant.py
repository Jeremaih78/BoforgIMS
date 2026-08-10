import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from tasker.ai.personal_assistant import PersonalProductivityAssistant, apply_suggested_steps
from tasker.ai.personal_context import build_personal_context, build_personal_task_context
from tasker.ai.service import AIService
from tasker.ai.types import ProviderResult, TokenUsage
from tasker.models import AIConfiguration, AIMessage, Task, TaskChecklistItem


class NoopLimiter:
    def check(self, **kwargs):
        return None


class JSONProvider:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return ProviderResult(
            text=json.dumps(self.payload), response_id="resp_personal", request_id="req_personal",
            usage=TokenUsage(input_tokens=20, output_tokens=10),
        )


@override_settings(TASKER_AI_OPENAI_API_KEY="test-key")
class PersonalAssistantTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="personal", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")
        permissions = Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("use_ai", "view_task", "change_task", "add_taskchecklistitem"),
        )
        self.user.user_permissions.add(*permissions)
        self.configuration = AIConfiguration.objects.get(provider="OPENAI")
        self.configuration.is_enabled = True
        self.configuration.save(update_fields=["is_enabled", "updated_at"])
        self.task = Task.objects.create(title="Prepare proposal", owner=self.user)
        self.other_task = Task.objects.create(title="Confidential other work", owner=self.other)

    def assistant(self, payload):
        provider = JSONProvider(payload)
        service = AIService(
            provider_factory=lambda configuration: provider,
            rate_limiter=NoopLimiter(),
        )
        return PersonalProductivityAssistant(service), provider

    def test_context_contains_only_authenticated_employees_data(self):
        context = build_personal_context(user=self.user)
        serialized = json.dumps(context)
        self.assertIn("Prepare proposal", serialized)
        self.assertNotIn("Confidential other work", serialized)
        self.assertEqual(context["scope"], "authenticated_employee_only")

    def test_task_context_rejects_another_employees_task(self):
        with self.assertRaises(PermissionError):
            build_personal_task_context(user=self.user, task=self.other_task)

    def test_next_task_is_structured_logged_and_explained(self):
        payload = {
            "task_id": self.task.pk, "title": self.task.title,
            "next_action": "Draft the outline", "estimated_focus_minutes": 25,
            "why": "It is the only active task.", "reasoning": "It creates immediate progress.",
        }
        assistant, provider = self.assistant(payload)
        result, message = assistant.run(user=self.user, feature="next_task")
        self.assertEqual(result["task_id"], self.task.pk)
        self.assertEqual(message.message_metadata["structured_result"]["why"], payload["why"])
        self.assertIn("output_schema", provider.kwargs)
        self.assertNotIn("Confidential other work", json.dumps(provider.kwargs))

    def test_task_ai_url_does_not_leak_manager_visible_task(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_all_tasks")
        )
        self.client.force_login(self.user)
        url = reverse(
            "ims:tasker:assistant_task_action", args=[self.other_task.slug, "explain"]
        )
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_generated_steps_require_selection_and_are_idempotent(self):
        payload = {
            "task_id": self.task.pk, "task_title": self.task.title, "approach": "Work in order.",
            "steps": [
                {"order": 1, "title": "Gather requirements", "why": "Reduces ambiguity", "estimated_minutes": 15},
                {"order": 2, "title": "Draft proposal", "why": "Creates the deliverable", "estimated_minutes": 30},
                {"order": 3, "title": "Review figures", "why": "Improves accuracy", "estimated_minutes": 10},
            ],
            "reasoning": "Small steps make progress visible.",
        }
        assistant, _ = self.assistant(payload)
        _, message = assistant.run(user=self.user, feature="break_task", task=self.task)
        first = apply_suggested_steps(
            user=self.user, task=self.task, message_id=message.pk, selected_indexes=[0, 2]
        )
        second = apply_suggested_steps(
            user=self.user, task=self.task, message_id=message.pk, selected_indexes=[1]
        )
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)
        self.assertEqual(
            set(TaskChecklistItem.objects.filter(task=self.task).values_list("title", flat=True)),
            {"Gather requirements", "Review figures"},
        )
