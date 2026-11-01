"""Offline declaration, provider-response and proposal-shape checks."""
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from tasker.ai.capabilities import configuration_fingerprint, preflight_configuration, require_preflight
from tasker.ai.exceptions import AIConfigurationError, AIProviderError, AIResponseValidationError
from tasker.ai.providers.openai import OpenAIProvider
from tasker.ai.settings import AIEnvironmentSettings
from tasker.ai.workflow_prompts import WORKFLOW_PROMPTS
from tasker.ai.workflow_schema import FEATURES, schema_for_feature, validate_workflow_payload
from tasker.models import AIConfiguration


def workflow_payload(**changes):
    return {"schema_version": "3", "headline": "Review current work", "summary": "There is recorded work to review.",
            "reasoning": "Use the source facts.", "actions": [], "questions": [], "citations": [], "date_hints": [], **changes}


class CapabilityPreflightTests(SimpleTestCase):
    def setUp(self):
        self.configuration = AIConfiguration(model="explicit-reviewed-snapshot", max_output_tokens=500,
            reasoning_effort="none", temperature=None)
        self.profile = {"model": self.configuration.model, "revision": "reviewed-v1", "structured_outputs": True,
                        "temperature": False, "reasoning_efforts": ["none"],
                        "verbosity_levels": ["low", "medium", "high"], "max_output_tokens": 1000}

    def report(self, **changes):
        return preflight_configuration(self.configuration, profile=self.profile, pricing_verified=True, **changes)

    def test_reviewed_exact_configuration_is_offline_only(self):
        report = self.report()
        self.assertTrue(report["ok"])
        self.assertFalse(report["live_verified"])
        self.assertFalse(report["store"])
        self.assertEqual(report["service_tier"], "default")
        self.assertIs(require_preflight(report), report)

    def test_missing_model_capability_or_price_fails_closed(self):
        for kwargs in ({"profile": {}, "pricing_verified": True},
                       {"profile": self.profile, "pricing_verified": False}):
            with self.subTest(kwargs=kwargs):
                report = preflight_configuration(self.configuration, **kwargs)
                self.assertFalse(report["ok"])
                with self.assertRaises(AIConfigurationError):
                    require_preflight(report)

    def test_model_parameters_and_response_limit_must_match_review(self):
        for field, value, code in (("model", "another-model", "capability_model_mismatch"),
                ("structured_outputs", False, "structured_output_unsupported"),
                ("reasoning_efforts", ["high"], "reasoning_unsupported"),
                ("verbosity_levels", [], "verbosity_unsupported"),
                ("max_output_tokens", 100, "output_limit_unsupported")):
            with self.subTest(field=field):
                profile = {**self.profile, field: value}
                self.assertIn(code, preflight_configuration(self.configuration, profile=profile,
                    pricing_verified=True)["checks"])
        self.configuration.temperature = 0.3
        self.assertIn("temperature_unsupported", self.report()["checks"])

    def test_fingerprint_changes_with_effective_parameter_or_profile(self):
        before = configuration_fingerprint(self.configuration, profile=self.profile)
        self.configuration.max_output_tokens += 1
        self.assertNotEqual(before, configuration_fingerprint(self.configuration, profile=self.profile))
        self.configuration.max_output_tokens -= 1
        self.assertNotEqual(before, configuration_fingerprint(self.configuration,
            profile={**self.profile, "revision": "reviewed-v2"}))


class WorkflowShapeTests(SimpleTestCase):
    def setUp(self):
        self.context = {"feature": "PLAN", "scope": "PERSONAL", "tasks": [{"id": 7}],
                        "proofs": {"captures": {"9": 1}, "plans": {"2030-01-07": "proof"}}}

    def test_unknown_keys_foreign_citations_and_actor_ids_are_rejected(self):
        for changes in ({"actor_id": 12}, {"citations": [{"task_id": 99, "claim": "Unrelated work"}]},
                {"actions": [{"id": "a", "kind": "PLAN_INTENTIONS", "plan_date": "2030-01-07",
                              "task_ids": [7], "priority_ids": [99]}]}):
            with self.subTest(changes=changes), self.assertRaises(AIResponseValidationError):
                validate_workflow_payload(workflow_payload(**changes), self.context)

    def test_manager_and_readonly_features_never_accept_actions(self):
        action = {"id": "a", "kind": "CHECKLIST_ADD", "task_id": 7, "title": "Review the source"}
        for scope, feature in (("MANAGER", "BREAKDOWN"), ("PERSONAL", "EXPLAIN"), ("PERSONAL", "MEETING")):
            with self.subTest(scope=scope, feature=feature), self.assertRaises(AIResponseValidationError):
                validate_workflow_payload(workflow_payload(actions=[action]), {**self.context, "scope": scope}, feature)

    def test_capture_metadata_cannot_rewrite_body_or_visibility(self):
        action = {"id": "a", "kind": "CAPTURE_METADATA", "capture_id": 9, "capture_kind": "IDEA",
                  "title": "Review idea", "tags": ["follow up"]}
        self.assertTrue(validate_workflow_payload(workflow_payload(actions=[action]), self.context, "CAPTURE"))
        for field in ("body", "original_text", "visibility", "owner_id", "url"):
            with self.subTest(field=field), self.assertRaises(AIResponseValidationError):
                validate_workflow_payload(workflow_payload(actions=[{**action, field: "rewrite"}]), self.context, "CAPTURE")

    def test_ambiguous_dates_need_explicit_confirmation(self):
        hint = {"phrase": "next Friday", "interpreted_date": "2030-01-11", "ambiguous": True,
                "requires_confirmation": False}
        with self.assertRaises(AIResponseValidationError):
            validate_workflow_payload(workflow_payload(date_hints=[hint]), self.context)
        hint["requires_confirmation"] = True
        self.assertTrue(validate_workflow_payload(workflow_payload(date_hints=[hint]), self.context))

    def test_malformed_json_values_raise_validation_not_type_errors(self):
        cases = [workflow_payload(actions=[{"kind": []}]), workflow_payload(citations=[{"task_id": [], "claim": "x"}]),
                 workflow_payload(actions=[{"id": "a", "kind": "PLAN_INTENTIONS", "plan_date": [],
                                           "task_ids": [7], "priority_ids": []}])]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(AIResponseValidationError):
                validate_workflow_payload(payload, self.context)

    def test_all_prompt_templates_use_closed_versioned_schema(self):
        self.assertEqual(len(WORKFLOW_PROMPTS), len(FEATURES))
        for feature, spec in zip(FEATURES, WORKFLOW_PROMPTS):
            self.assertEqual(spec["version"], 1)
            self.assertEqual(spec["output_schema"], schema_for_feature(feature))
            def closed(schema):
                if isinstance(schema, dict):
                    if schema.get("type") == "object":
                        self.assertIs(schema["additionalProperties"], False)
                        self.assertEqual(set(schema["required"]), set(schema["properties"]))
                    for value in schema.values():
                        closed(value)
                elif isinstance(schema, list):
                    for value in schema:
                        closed(value)
            closed(spec["output_schema"])
            self.assertIn("untrusted data", spec["system_template"])
            spec["system_template"].format(workflow_context="{}")
            spec["user_template"].format(workflow_context="{}")


class ProviderMetadataTests(SimpleTestCase):
    def setUp(self):
        self.configuration = AIConfiguration(model="explicit-reviewed-snapshot", max_output_tokens=500,
                                             reasoning_effort="none", temperature=None)
        self.client = Mock()
        self.provider = OpenAIProvider(configuration=self.configuration, client=self.client,
            environment=AIEnvironmentSettings(api_key="", organization="", project="", max_prompt_characters=24000))

    def response(self, **changes):
        usage = SimpleNamespace(input_tokens=12, output_tokens=8, total_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=3),
            output_tokens_details=SimpleNamespace(reasoning_tokens=4))
        return SimpleNamespace(status="completed", error=None, output=[], output_text="{}", usage=usage,
            model=self.configuration.model, service_tier="default", **changes)

    def test_requests_are_no_store_fixed_tier_and_strict_json(self):
        self.client.responses.create.return_value = self.response()
        result = self.provider.generate(instructions="Use facts", input_items=[], safety_identifier="opaque",
                                        output_schema=schema_for_feature("PLAN"))
        request = self.client.responses.create.call_args.kwargs
        self.assertIs(request["store"], False)
        self.assertEqual(request["service_tier"], "default")
        self.assertIs(request["text"]["format"]["strict"], True)
        self.assertEqual(result.metadata["model"], self.configuration.model)
        self.assertTrue(result.metadata["usage_available"])
        self.assertTrue(result.metadata["usage_validated"])
        self.assertEqual(result.usage.total_tokens, 20)
        self.assertEqual(result.usage.reasoning_tokens, 4)

    def test_missing_usage_is_unverified_not_free_billable_work(self):
        response = self.response()
        response.usage = None
        result = self.provider._result(response)
        self.assertFalse(result.metadata["usage_validated"])
        self.assertFalse(result.metadata["usage_available"])

    def test_legacy_mock_without_total_accepts_result_but_not_settlement(self):
        response = self.response()
        del response.usage.total_tokens
        result = self.provider._result(response)
        self.assertEqual(result.usage.total_tokens, 20)
        self.assertFalse(result.metadata["usage_verified"])

    def test_malformed_counters_and_refusals_are_rejected(self):
        for field, value in (("input_tokens", True), ("output_tokens", -1), ("total_tokens", 1000)):
            response = self.response()
            setattr(response.usage, field, value)
            with self.subTest(field=field), self.assertRaises(AIProviderError):
                self.provider._result(response)
        response = self.response()
        response.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        with self.assertRaises(AIProviderError):
            self.provider._result(response)

    def test_cache_write_counter_remains_separate_for_price_review(self):
        response = self.response()
        response.usage.input_tokens_details.cache_write_tokens = 2
        self.assertEqual(self.provider._result(response).usage.cache_write_tokens, 2)
