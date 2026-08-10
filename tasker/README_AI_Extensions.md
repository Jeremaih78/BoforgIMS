# Boforg AI Tasker — AI Extension Boundary

No user-facing AI feature is enabled. Phase 2 Milestone 1 now implements the audited provider foundation described in [README_AI_FOUNDATION.md](README_AI_FOUNDATION.md). This document remains the data-minimization and future-productivity contract.

## Components

- `tasker.ai.contracts` contains immutable input/output data transfer objects and the `TaskerAIProvider` protocol.
- `tasker.ai.context.build_employee_day_context` builds a minimal, owner-scoped snapshot of existing Tasker data. It performs no external call and writes nothing.
- `tasker.ai.service.AIService` is the production application boundary. `TaskerAIGateway` remains only as compatibility for the Phase 1 typed productivity contracts.
- Provider adapters should live under a future `tasker.ai.providers` package. OpenAI-specific request construction belongs there—not in views, models, signals, or general Tasker services.

## Intended flow

```text
permission-aware view
    → AI application service
        → build_employee_day_context(user)
        → policy / consent / quota checks
        → TaskerAIGateway
            → provider adapter
        → validate structured result
        → explicitly persist approved fields
```

The four disabled buttons map naturally to `plan_day`, `suggest_next_task`, `answer`, and `summarize_day`. Do not enable those buttons merely because an adapter exists; feature flags, permissions, audit logging, timeouts, and user-facing failure handling must be completed first.

## Data minimization

Context version `1.0` includes employee identity, day/focus, owner-scoped actionable tasks, category, scheduling, duration estimate, progress, and selected priority IDs. It intentionally excludes:

- comments and attachments;
- private task notes and daily scratchpad notes;
- customer, quotation, invoice, and inventory records;
- authentication, contact, and credential data;
- activity-history change payloads.

Future IMS data connectors must add purpose-specific DTOs and permission checks rather than appending arbitrary model dictionaries to prompts.

Context construction is bounded to 50 ranked tasks and 4,000 description characters per task. Future adapters must retain explicit input limits and expose truncation/selection behavior to users where it can affect a recommendation.

## Persistence

Provider adapters must return contract objects. A future application service may persist reviewed output into `Task.ai_summary`, `Task.ai_recommendation`, `Task.ai_score`, and `ai_metadata`. Providers must never save models directly. Store provider/model identifiers, prompt/schema versions, generation time, and approval state in structured metadata without storing secrets.

## Production checklist for Phase 2

1. Add a provider adapter with strict structured-output validation.
2. Load credentials from environment/deployment secrets only.
3. Add explicit feature flags and dedicated AI permissions.
4. Define consent, retention, redaction, and acceptable-use policy.
5. Add request timeouts, retry limits, quotas, and cost controls.
6. Add audit events without logging sensitive prompt content.
7. Add deterministic contract tests and provider-mocked integration tests.
8. Add user review/approval before AI output changes plans or tasks.
9. Assess whether slow calls require the later background-job architecture.
10. Enable one narrowly scoped action at a time.
