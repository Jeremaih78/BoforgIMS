# Boforg AI Tasker — Phase 2 AI Foundation

This milestone provides the internal infrastructure for future AI productivity features. It deliberately does not expose chat, planning, recommendations, task generation, or autonomous actions.

## Safety posture

- AI is disabled by default in `AIConfiguration`.
- Provider credentials are loaded only from environment variables.
- Provider-side response storage is disabled by default.
- Usage logs contain operational metadata, token totals, latency, request IDs, and sanitized errors—not prompts or responses.
- The seeded system prompt states that the model is advisory and cannot perform consequential actions.
- Future application workflows must obtain explicit human confirmation before external writes, destructive changes, payments, refunds, inventory changes, communications, or HR actions.

## Architecture

```text
future permission-aware feature
    -> AIService
       -> AIConfigurationManager
       -> AIRateLimiter
       -> PromptManager
       -> ConversationManager
       -> provider-neutral AIProvider
          -> OpenAIProvider / Responses API
       -> AIMessage + AIUsageLog
       -> AIResult or AIStreamChunk
```

Views must call `AIService`; they must never import the OpenAI SDK, render prompts, implement retry loops, or write token logs themselves.

## Models

| Model | Responsibility |
| --- | --- |
| `AIConfiguration` | Active provider, model, system override, generation limits, timeout, retry, quotas, streaming and retention policy |
| `PromptTemplate` | Centrally managed, strictly rendered, versioned system/user templates |
| `AIConversation` | Owner-scoped conversation and future agent identity |
| `AIMessage` | Ordered conversation messages and per-response token/latency state |
| `AIUsageLog` | Content-free request audit, provider IDs, attempts, tokens, latency and safe errors |
| `AIFeedback` | Employee rating linked to a persisted assistant response |

All models use the existing audited soft-delete base. PostgreSQL constraints enforce one active configuration per provider, one active prompt version per key, ordered unique messages, token-total consistency, valid configuration ranges, and one active feedback record per employee/message.

## Environment configuration

```dotenv
OPENAI_API_KEY=...
OPENAI_ORGANIZATION=
OPENAI_PROJECT=
TASKER_AI_MAX_PROMPT_CHARACTERS=50000
TASKER_AI_LOG_LEVEL=INFO
```

Never put an API key in `AIConfiguration`, a prompt, source control, logs, or admin notes.

## Activation

```powershell
python manage.py migrate tasker
python manage.py setup_tasker_roles
```

Then, in Django admin:

1. Review the seeded `foundation-general` prompt.
2. Review the active configuration, model, timeout, retry and usage limits.
3. Confirm the production environment has `OPENAI_API_KEY`.
4. Enable AI only when the first approved Milestone 2 feature is ready.

The setup command creates or refreshes `Tasker Employee`, `Tasker Supervisor`, `Tasker Manager`, and `Tasker Administrator`. Employees and supervisors receive `use_ai`; managers and administrators receive all Tasker AI permissions. User membership is never changed automatically.

## Service use

Future features use the same boundary:

```python
from tasker.ai import AIService

result = AIService().generate_text(
    user=request.user,
    operation="approved-feature-key",
    prompt_key="versioned-prompt-key",
    variables={"input": "purpose-limited context"},
    metadata={"feature": "approved-feature-key", "schema_version": "1.0"},
)
```

Streaming callers iterate `AIService().stream_text(...)`. For HTTP SSE, wrap those chunks with `tasker.ai.streaming.streaming_http_response`. The transport sends text deltas and content-free completion identifiers. There is intentionally no streaming endpoint in this milestone.

## Prompt management

- Prompts are stored only in `PromptTemplate`, never in views.
- Every prompt has a stable key and explicit version.
- Only one non-deleted version per key may be active.
- Declared variables use strict names and all required values must be supplied.
- Create a new version for substantive prompt changes; retain old versions for traceability.
- `system_prompt_override` is an emergency/global policy override and replaces the template system prompt when populated.

## Reliability and rate limits

The OpenAI SDK is configured with `max_retries=0`; `AIService` owns bounded exponential backoff so the exact attempt count is auditable. Transient connection, timeout, 408, 409, 429, and server failures may retry up to the configured limit. Permanent provider errors do not retry. A stream retries only before emitting text, preventing duplicate partial responses.

Timeouts are set on the provider client. Per-minute limits use the Django cache (Redis in production when configured); daily request and token quotas use durable PostgreSQL usage logs.

## Logging and privacy

Every service invocation produces an `AIUsageLog`, including disabled, unauthorized, rate-limited, failed, interrupted, and successful attempts. Logs retain stable internal request UUIDs and OpenAI request/response IDs for support. Provider exception text is replaced with controlled messages before persistence.

Conversation content is intentionally stored in `AIMessage` for future employee memory and must remain owner-scoped. It is not displayed on the organization usage dashboard. Future retention/export controls must be implemented before broad conversational features launch.

## Usage dashboard

`https://ai.boforg.co.zw/ai/usage/` requires `tasker.view_ai_usage`. It reports 30-day requests, success rate, tokens, active users, average latency, model/operation breakdowns, and recent content-free audit status.

## Future agent support

`tasker.ai.agents.AgentRegistry` defines agent identity, prompt ownership, allowed tools, and confirmation categories. Milestone 1 registers and runs no autonomous agent or tool. Future tools must be allowlisted, permission-aware, auditable, and separated into read-only versus confirmation-required actions.

## Validation

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tasker
```

Tests must mock providers. Do not use a real API key in CI or unit tests.
