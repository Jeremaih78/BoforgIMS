# Phase 2 Milestone 2: Personal Productivity Assistant

This milestone adds the first user-facing Boforg AI assistant. It is deliberately
restricted to the authenticated employee's productivity records.

## Capabilities

- Plan My Day
- Suggest Next Task
- Prioritize Tasks
- Summarize Day
- Explain Task
- Break Task Into Steps

Every recommendation contains a visible explanation. AI results are advisory and
do not change tasks automatically. Generated checklist steps are stored with the
audited response and require the employee to select and apply them explicitly.

## Privacy boundary

`tasker.ai.personal_context` is the only context builder used by this assistant.
All task, plan, review, checklist, and focus-time queries are filtered by the
authenticated user's primary key. Task-level endpoints additionally require the
task owner to equal the current user, even when that user has team-wide task-view
permissions. Company-wide data, comments, attachments, inventory, customers,
payments, email, and other employees are excluded.

The context is bounded to 75 active workload tasks and 50 tasks completed today.
Text fields are length-limited before transmission.

## Request lifecycle

1. A POST-only action builds the employee-scoped context.
2. `PersonalProductivityAssistant` selects a versioned database prompt.
3. `AIService` applies permissions, limits, timeout/retry policy, and audit logging.
4. OpenAI's Responses API returns a strict JSON-schema response.
5. Application validation rejects unknown task IDs or missing explanations.
6. The structured result is stored on the employee-owned `AIMessage` and rendered.

## Configuration

Never place an API key in source control or the database. Configure a newly issued
key in the deployment environment:

```text
OPENAI_API_KEY=<secret managed by the host>
```

Then use Django admin to enable the active `AIConfiguration`. The application
defaults to disabled and presents a safe unavailable state until both requirements
are satisfied. Model, reasoning effort, response verbosity, output limit, timeout,
retry policy, streaming, and usage limits remain administrator-controlled.

Prompt keys are seeded by migration `0009_seed_personal_assistant_prompts` and can
be versioned in admin without changing views or service code.

## Safety and operations

- Routes require `tasker.use_ai`; checklist application also requires the normal
  task and checklist permissions.
- Requests use a stable hashed `safety_identifier` and are logged in `AIUsageLog`.
- Provider response storage is disabled by default.
- Invalid structured responses are marked failed and never rendered or applied.
- The pasted/generated response cannot be used to apply steps to another task or
  another employee's conversation.
- No dangerous action, external communication, or company-wide query is available.

## Extension points

Future agents should add a new scoped context builder, a prompt template with a
strict schema, and an application service method. They must continue to use
`AIService`; provider calls must not be made directly from views.
