# Stage 9: Governed AI assistance

29 October 2026 · Africa/Harare

Stage 9 application code is implemented. Local software checks use synthetic data and mocked providers. Live AI activation, actual model selection/evaluation, employee rollout and production deployment remain separate. Stages 10–13 have not been implemented by this change.

## Delivered behavior

- Personal assistant queues capture interpretation, planning, explanation, task breakdown, grounded briefings, blocker/handoff summaries and deliberately supplied meeting notes. Relevant Inbox, Plan, task and team screens link to it. Existing assistant POST controls use the same asynchronous request path when the rollout flag is enabled.
- One canonical record per included task, bounded ID references, real totals/omissions, current readiness/capacity/goals and five-minute source snapshots. The 75-task ceiling is an inclusion limit, not a claim that other work does not exist. Explicit truncation and aggregate UTF-8/input bounds survive large workloads.
- Manager assistance uses currently readable shared team facts and employee display names. It excludes task descriptions, private estimates, captures, reflections, conversation history and meeting text. Every retained source is reauthorized before history or a proposal is shown. Manager results are read-only.
- Generation uses the existing AIService, Responses adapter, versioned prompts, conversations, usage records and PostgreSQL outbox. No provider call runs inside the outbox/domain transaction. Pending screens poll status only; cancellation and explicit fresh-request retries are available. A reclaimed running request becomes uncertain rather than silently resending.
- Four allowlisted personal actions: add checklist steps, classify a selected private Inbox capture, set daily intentions, and accept a feasible time block through existing domain services. No autonomous delegation, email, financial/stock mutation, arbitrary ORM operation or external tool execution is offered.
- Review checkboxes start unchecked. Apply checks owner, current scope, exact IDs, revision, source evidence and expiry, then commits the selected batch atomically. Duplicate steps and ambiguous date commitments are rejected. Replayed apply/undo does not repeat changes. Undo is an audited compensating change and refuses subsequent human edits.
- Feedback, private request history, owner-controlled erasure and bounded retention are included. Erasure cancels active requests and clears saved request/proposal/message/conversation/feedback content, including a response that arrives later or after account deactivation. Applied work and monetary evidence remain. Erasing content removes automatic undo data.

## Confirmed monetary policy

The owner confirmed USD, calendar weeks resetting **Monday at 00:00 Africa/Harare**, **USD 0.50 maximum per employee**, and **USD 5 maximum per company per week**. The employee ceiling is global across billing-company changes; each company's ceiling also covers its enrolled employees.

Migration 0022 records the confirmed global calendar and employee limit **disabled**. Company budgets default to a USD 5 maximum but no company budget, employee enrollment, price card or paid activation is created automatically. Explicit requester billing attribution is required; automatic AI work without an authenticated payer remains blocked. Factual Stage 8 reporting does not acquire an AI charge.

The shared gateway reserves exact Decimal money before each dispatch, including streaming and retries. An injected provider or token limiter cannot bypass monetary admission in production. Quotes use the entire effective input/schema and worst applicable input/output price. Current immutable model/price/capability records and per-request snapshots/fingerprints are required. The selected model and actual returned model/default service tier must agree. Measured cached input/reasoning/cache-write usage is counted once.

Timeouts, worker crashes and missing/unverified usage retain their worst-case reservation. A retry needs additional headroom. Unknown holds are **not automatically refunded or expired**. Trusted provider receipt binding/reconciliation is a follow-up; a caller-supplied claim of verification cannot release a hold. Out-of-bounds or unexpected model/tier evidence blocks further spending for review.

## Verification and evaluation evidence

- Fresh full PostgreSQL regression: **909 tests passed in 423.522 seconds**, including Tasker and configuration/IMS reliability tests. Log: `output/stage9-full-fresh-tests.log`.
- Existing JavaScript regression: **77 tests passed**, zero failures. Log: `output/stage9-node-tests.log`.
- Final focused boundary/readiness/production-static checks: **38 tests passed in 83.322 seconds**, including two additional late-erasure/context-integrity regressions after the full run. Log: `output/stage9-final-boundary-tests.log`. System checks and migration drift checks pass.
- Real PostgreSQL races cover shared employee/company admission and concurrent proposal application. Mocked tests cover empty/all-blocked work, overload/overlapping accepted blocks, unknown estimates, >75 tasks, malicious capture instructions, cross-team/source revocation, ambiguous dates, stale/replayed apply/undo, expiry, provider failure, interrupted dispatch, lost leases, redaction races and budget exhaustion.
- Browser checks on a separate synthetic account/hostname verified an unchecked preview, explicit apply, a manual task edit and conflict-safe undo. At 390px, keyboard submission opened a pending request, cancellation worked, and client/scroll widths both measured 390px. Screenshot evidence: `output/stage9-undo-conflict.png` and `output/stage9-mobile-pending.png`. No paid provider or actual delivery was used.
- The first full `--keepdb` run failed because earlier transactional tests had flushed data-migration seeds from the reused disposable test database. A fresh test database restored those seeds; the fresh full run passed. One UI assertion was narrowed to action checkboxes instead of matching the word “checked” in explanatory text.

These are deterministic software/safety evaluations, not a measured LLM quality result. Real-model invalid/unsafe proposal rate, grounded usefulness, end-to-end latency, acceptance rate, time saved and USD cost per accepted useful action remain **unmeasured**. Before release, use a versioned representative dataset with the same cases, require zero cross-scope/unauthorized commits, record unsupported/unsafe suggestions separately from schema validity, compare against the deterministic operator/manual baseline, and choose a model only after the owner accepts quality/latency/cost results. The requested 70% productivity uplift remains a pilot target.

## Rollout and rollback

1. Review additive migrations **0021–0022** and take a verified production backup. Do not reverse the already-deployed Tasker migrations or historical IMS findings.
2. Deploy the reviewed code and migrations with the workflow switch off. Run strict production `collectstatic`; generated public static directories/files must be readable by nginx (755/644). Keep private backups/configuration private. Use the existing deployment procedure; no deployment was performed during Stage 9 implementation.
3. An owner configures a billing company budget (maximum USD 5), explicit requester attribution and a named small pilot's enrollment. Configure current versioned OpenAI pricing and capabilities for one exact model; do not use synthetic test prices. Enable the global policy and provider configuration only after credential setup, capability/pricing review and authorized live checks.
4. `python manage.py check_tasker_ai_readiness` performs offline read-only checks. `python manage.py check_tasker_ai_credentials` remains an offline presence check. Neither authenticates a key, changes configuration or sends a provider request. The existing local dotenv line-9 warning is still owner-deferred; no private configuration was read or edited here.
5. Set `TASKER_AI_WORKFLOW_ENABLED=true` only for the reviewed rollout. Run the existing supervised `tasker_worker`; there is no second AI worker. Use a lease long enough for the configured timeout/retries and publication (for a reviewed small pilot, `--lease-seconds 600` is a starting configuration, not a measured latency promise). Snapshots expire after five minutes; stale queued work requires a fresh request.
6. If problems occur, disable the workflow switch and provider/global spending policy to stop new AI work. Existing Tasker capture, tasks, planning, Focus and factual reports remain available. Owner cancellation/erasure still work with the workflow flag off. Retain additive tables, unknown holds and audit evidence; never refund a possibly billed call by deleting records or rolling back the database.

## Official API references

The implementation follows OpenAI's [per-run spending controller guidance](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api), [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs) and [Responses data-control documentation](https://developers.openai.com/api/docs/guides/your-data#v1responses). Calls use `store=false`, SDK retries are disabled in favor of the governed gateway, and generation uses local jobs rather than provider background mode. `store=false` is **not** a zero-retention guarantee.
