"""Offline capability preflight for explicitly reviewed model configurations.

This checks a recorded declaration; it never authenticates or calls a provider.
"""
import hashlib
import json

from django.conf import settings

from .exceptions import AIConfigurationError


def effective_configuration(configuration, *, profile=None):
    return {
        "provider": str(configuration.provider), "model": str(configuration.model),
        "max_output_tokens": configuration.max_output_tokens,
        "temperature": str(configuration.temperature) if configuration.temperature is not None else None,
        "reasoning_effort": str(configuration.reasoning_effort),
        "response_verbosity": str(configuration.response_verbosity),
        "timeout_seconds": str(configuration.timeout_seconds),
        "max_retries": configuration.max_retries, "store": False,
        "service_tier": "default", "capability_profile": profile or {},
    }


def configuration_fingerprint(configuration, *, profile=None):
    encoded = json.dumps(effective_configuration(configuration, profile=profile), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def preflight_configuration(configuration, *, profile=None, pricing_verified=False):
    """Return fixed check codes, not an unsupported claim of live availability."""
    if profile is None:
        registry = getattr(settings, "TASKER_AI_MODEL_CAPABILITIES", {})
        profile = registry.get(str(configuration.model)) if isinstance(registry, dict) else None
    checks = []
    required = {"model", "revision", "structured_outputs", "temperature", "reasoning_efforts", "verbosity_levels", "max_output_tokens"}
    valid = isinstance(profile, dict) and required.issubset(profile)
    if not valid:
        checks.append("capabilities_unconfigured")
    else:
        if type(profile["model"]) is not str or profile["model"] != configuration.model:
            checks.append("capability_model_mismatch")
        if type(profile["revision"]) is not str or not profile["revision"].strip() or len(profile["revision"]) > 100:
            checks.append("capability_revision_missing")
        if profile["structured_outputs"] is not True:
            checks.append("structured_output_unsupported")
        if type(profile["temperature"]) is not bool or (configuration.temperature is not None and not profile["temperature"]):
            checks.append("temperature_unsupported")
        efforts = profile["reasoning_efforts"]
        if type(efforts) is not list or any(type(value) is not str for value in efforts) or configuration.reasoning_effort not in efforts:
            checks.append("reasoning_unsupported")
        levels = profile["verbosity_levels"]
        if type(levels) is not list or any(type(value) is not str for value in levels) or configuration.response_verbosity not in levels:
            checks.append("verbosity_unsupported")
        maximum = profile["max_output_tokens"]
        if type(maximum) is not int or maximum < 16 or not 16 <= configuration.max_output_tokens <= maximum:
            checks.append("output_limit_unsupported")
    if configuration.provider != "OPENAI":
        checks.append("provider_unsupported")
    if pricing_verified is not True:
        checks.append("pricing_unverified")
    safe_profile = profile if isinstance(profile, dict) else {}
    try:
        fingerprint = configuration_fingerprint(configuration, profile=safe_profile)
    except (TypeError, ValueError):
        checks.append("capability_record_invalid")
        fingerprint = ""
    return {"ok": not checks, "checks": checks, "model": str(configuration.model),
            "fingerprint": fingerprint, "live_verified": False,
            "service_tier": "default", "store": False}


def require_preflight(report):
    if type(report) is not dict or report.get("ok") is not True:
        raise AIConfigurationError("Model capabilities and pricing must be reviewed before AI requests can run.")
    return report
