# Tasker Stage 8 job foundation

27 October 2026. This runbook describes implemented local commands; it does not install or certify a production worker, scheduler, email provider or supervision service.

## Stage 11 collections extension (30 October)

`business.reconcile` is registered in the same queue. Its payload is exactly `source_type` (`collection_task` or `promise_to_pay`) and positive integer `source_id`; it contains no source note, customer/contact, amount or payment body. Source hooks enqueue it and the scheduler rotates bounded scans over both source tables and existing links to repair missed/bulk updates and deletions.

The handler reloads authoritative source state and commits its unique reference, observed transition and targeted in-app attention with the existing job lease fence. Replays of unchanged state do not duplicate references/events. A completed no-op job that observed disabled/different facts gets a uniquely deduplicated successor when repair is still needed; its completed receipt is preserved. Failed attempt limits and intentionally cancelled jobs are not silently reset. Use the existing authorized job retry workflow for genuine failures.

Business reminders use the recipient's normal quiet/work/focus settings, current source assignment and permission, and current metadata/revision proofs. Completion/reassignment/revocation hides obsolete attention immediately; reconciliation cancels its pending delivery. Only in-app delivery is supported for this slice. Credit Control's separate broadcast notification audience is not reused.

`TASKER_COLLECTIONS_ADAPTER_ENABLED` defaults false. Disabling it hides collections actions and source reminders; shared business routes/panels remain if another authorized adapter is enabled. Disabled bounded reconciliation can retire projections. Preserve source data, additive schema and receipts. Canonical Credit Control scope/admin hardening remains in effect with this code, so review real company memberships, legacy mappings and source permissions before any deployment even when the adapter flag is off. See [Stage 11 collections evidence and remaining adapters](TASKER_STAGE11_COLLECTIONS_IMPLEMENTATION_2026-10-30.md). No production scheduler, source-owner mapping, provider activation or deployment is installed by this extension.

## Stage 11 replenishment extension (30 October)

The same `business.reconcile` handler now accepts the registered `replenishment_plan` source type. Its integer source ID identifies the canonical Inventory responsibility/review, not a stock movement or purchase order. Payloads still contain only the source type and source ID; standing notes, costs and supplier contacts remain outside Tasker/outbox/reminder bodies.

Product/plan save hooks enqueue promptly when `TASKER_REPLENISHMENT_ADAPTER_ENABLED` is enabled. Bulk reservation writes can bypass hooks and Product timestamps, so current SQL/presentation/dispatch proofs include raw quantity, reservations, reorder level and active/tracked flags. The scheduler rotates bounded adapter/source/link scans; collections and replenishment have independent cursors/flags and share the caller's bounded sweep budget.

Enabled reconciliation locks Product before the source Plan/Company and then the Tasker reference/attention rows. It records observed low/cleared/reopened need and uses the existing leased worker fence. Disabled retirement only locks and retires saved projections/attention; it does not hydrate or lock canonical inventory sources, avoiding multi-product/company lock inversion in retirement batches. Neither path invokes shipment/identity receiving, changes stock or posts finance. Availability recovered by released reservations is labeled **Replenishment need cleared**, not purchase received or accepted procurement output. Source review steps and receipt/event history preserve their separate meanings.

`TASKER_REPLENISHMENT_ADAPTER_ENABLED` defaults false. Disabling it hides inventory-source Tasker actions and permits bounded projection/reminder retirement without changing Product or source decisions. `TASKER_COLLECTIONS_ADAPTER_ENABLED` retains its independent chosen state; disabling one adapter does not close the other. Canonical Inventory pages remain controlled by source permissions/company scope, and per-product responsibility can be paused separately. Preserve schema, cycles, source receipts and terminal job history. Do not silently reset failed/cancelled jobs or purge evidence.

Production source setup requires explicit Product/company responsibility, an eligible procurement employee, source-read/Product-read and employee source-change rights; configuration/reassignment also needs `inventory.manage_replenishment` plus current company-management authority. Neither the migration nor a Tasker role makes those decisions. See [replenishment implementation, setup and remaining adapters](TASKER_STAGE11_REPLENISHMENT_IMPLEMENTATION_2026-10-30.md): 1,313 full regressions, 107 final focused/static checks, 77 JavaScript checks and synthetic desktop/mobile workflows pass. No production worker/scheduler, real permission/owner mapping, deployment or provider activation is certified by this extension.

## Stage 13 controlled rollout and recovery

See [the infrastructure-specific Stage 13 runbook](TASKER_STAGE13_IMPLEMENTATION_2026-10-31.md) for the historically identified VPS application/venv paths, both Gunicorn units, proposed worker/scheduler supervision, strict static checks, forward migration/restore order, monitoring ownership and containment. The verified local synthetic journey includes a failed leased report job, authorized retry and successful canonical handler recovery; a separate synthetic PostgreSQL backup/restore compared evidence and constraints. These do not certify production supervision or a production-data restoration.

Optional cohort enforcement holds unreleased AI dispatch and recurring generation through the existing gateway/worker. Recurrence defers the held job without spending failure attempts. Measurement pages keep their own current selectors. Pausing an enforced pilot stops its cohort access; disabling only rollout writes does not revoke prior grants, and disabling enforcement restores the old access rules. Live email/AI and loaded workers require their own stop/flag/restart response. Preserve UNKNOWN external outcomes and dedupe receipts during any recovery.

## Infrastructure decision

The checkout uses Django and PostgreSQL. Redis is an optional cache; no Celery, RQ, Huey or other worker package/scheduler was configured in the inspected requirements or Tasker app. Stage 8 therefore uses **one PostgreSQL transactional outbox** and standard Django management commands, requiring no second broker or deployment stack. Later AI jobs and IMS adapters should register bounded handlers in this foundation.

`OutboxJob` stores a unique source-event key, reference-only payload/digest, optional scoped owner, scheduled time, attempts, lease token/expiry, state, small result and generic error code. `JobWorkerHeartbeat` records process name/role, heartbeat and last job ID without task/report content.

## Local disposable verification commands

From `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS`, after root has applied the matching migration to the established disposable database and confirmed that external adapters remain disabled:

```powershell
$env:IMS_TEST_PORT='55441'
.\venv\Scripts\python.exe manage.py tasker_scheduler --once --limit 100 --settings=tmp.tasker_stage1_ui_settings
.\venv\Scripts\python.exe manage.py tasker_worker --once --limit 25 --settings=tmp.tasker_stage1_ui_settings
.\venv\Scripts\python.exe manage.py tasker_jobs_health --settings=tmp.tasker_stage1_ui_settings
```

Each command defaults to a bounded single pass. The scheduler repairs coordination, notifications, reports and Stage 11 business references through their service seams and fills missing/stale operator refresh jobs. A worker pass claims due jobs; repeat bounded passes if a handler deliberately schedules follow-up work. A future `run_after` is not processed early.

For deliberate local continuous testing, the same commands accept `--loop`, with `--poll-seconds 2` for the worker and `--poll-seconds 30` for the scheduler. Stop with Ctrl+C. No loop was launched automatically by adding these commands.

## Execution and delivery guarantees

Jobs are enqueued in the same database transaction as domain mutations, so rollback cannot leave a phantom event. Repeated source keys reuse the original row; a changed kind/owner/payload under the same key is rejected. Claims use PostgreSQL `SELECT … FOR UPDATE SKIP LOCKED`; unsupported database backends fail clearly.

Claim transactions are short and release the outbox lock before handler domain work. A handler runs in another transaction and acquires its Task/scope/delivery locks normally. Its final conditional outbox update requires the current lease token, payload digest and unexpired lease. If another worker reclaimed the job or an operator revision superseded it, the old handler's database effects roll back. The worker never holds an outbox row while waiting for Task locks, avoiding the reverse of domain mutation → enqueue ordering.

This gives transactional, deduplicated **database** effects. It does not claim exactly-once SMTP or another external side effect. External adapters must recheck their Delivery ledger, audience and current job lease immediately before sending, use provider idempotency where available, and retain an ambiguous outcome rather than blindly retry a potentially accepted send. Stage 8 uses the selected mocked email path until separately configured/authorized activation.

Handlers accept `payload=...` and `job=...`. Return a small count/code dictionary for success. Return `JobDeferred(run_after=..., reason='quiet_hours')` to commit a HELD delivery ledger and reschedule the job without spending a failure attempt; raising it instead rolls back partial domain effects before deferral. Return `JobCancelled('obsolete')` to commit obsolete-delivery cancellation without retry. `JobPermanentError('generic_code')` fails immediately. Other handler exceptions roll back effects and retry after 30, 60, 120, … seconds, capped at one hour, with at most five attempts by default.

Return `JobRetry('generic_failure_code', run_after=...)` for a known safe-to-retry delivery failure when its FAILED ledger must commit. This spends an attempt and eventually marks the outbox FAILED. Do not use `JobDeferred` for provider failures; it deliberately preserves the attempt budget for quiet/focus/cutoff windows. Matching failed operator revisions also stay failed until a manual retry or a new source revision, rather than being reset by every scheduler sweep.

`assert_current_job_lease()` is available inside handlers before adapter use. Generic exception text, private notes, provider credentials and copied report bodies are not job diagnostics. Payloads are limited to 4 KiB, shallow record-reference JSON and bounded string/list sizes; content/credential keys are rejected.

## Operator coalescing and recovery

`enqueue_operator_refresh(owner_id=..., revision=...)` keeps one reusable row per operator owner; generic `enqueue_job` rejects this kind. The helper locks an existing owner state before the outbox row and uses that state's current revision, so an older reconciliation read cannot overwrite a newer event. A queued event replaces the pending revision. A revision arriving during an in-flight refresh supersedes the old read, rolls back its database effects and queues the latest revision with a fresh attempt budget. A superseded worker that has not started its handler also releases the old lease immediately. A completed row is reused for a later revision. Scheduler reconciliation selects missing/different revisions rather than repeatedly sweeping the same first page of owners.

If a worker exits mid-handler, its transaction rolls back and its lease eventually expires. Another worker reclaims it; an old worker cannot later commit using the replaced lease token. Exhausted attempts become FAILED with a generic code. Superuser retry uses `retry_failed(user=..., job_id=...)` and resets attempts; this does not bypass the handler's current source/audience checks.

The health command reports queued/running/succeeded/failed/cancelled counts, expired leases, recent worker heartbeat and oldest queued age. Use `--fail-on-unhealthy` for an external health probe. An absent recent worker with queued work, expired lease or failed job requires inspection. A recorded heartbeat is only local process evidence, not proof of a deployed supervisor or successful email delivery.

## Supervision, retention and rollback

Before production, the deployment owner must configure the real application settings/environment privately, PostgreSQL permissions, two independently named supervised processes (worker and scheduler), restart policy, logs, health probes and backup/recovery. Windows Task Scheduler/service supervision or a Linux process supervisor can run the same commands. Neither mechanism is installed by this change. Keep worker and scheduler names distinct so their heartbeat roles remain meaningful.

Retention is an explicit scheduler action: `tasker_scheduler --once --prune --retention-days 30`. Thirty days is a local proposed default, not an approved company retention policy. It clears completed/cancelled payload/result detail in bounded batches and retains compact event-key/digest tombstones so an old replay cannot re-create an already performed action. Reusable operator rows retain their revision payload while old result summaries are cleared. It does not delete pending/failed jobs, delivery evidence or reusable operator jobs. Tombstone count is intentionally not claimed to be bounded; production archival/export/retention remains an owner decision. Without `--prune`, no retention mutation is requested.

For application rollback, stop the supervised processes, restore matching code/templates/settings, and retain the additive outbox/delivery tables for review. Do not routinely drop job tables, erase dedupe keys or requeue ambiguous external sends. No production process, destructive rollback or external message is executed by this runbook.

## Stage 9 AI work (29 October)

The same worker now handles `ai.generate`. Its payload contains only an owner-private request UUID. Provider work runs outside the outbox transaction; only current lease/nonce/source versions can publish a result. A reclaimed RUNNING request records uncertainty without another provider call. AIService money/token audit settlement survives cancellation and lost publication leases.

The default rollout switch is off. Configure a reviewed lease/timeout/retry combination before activation; `--lease-seconds 600` may be used for a small reviewed pilot. Source snapshots expire after five minutes. Status polling never runs generation. An explicit retry creates a new request and needs new monetary headroom. Unknown charges retain their holds; they are not cleared by retries, erasure or database rollback.

Run `python manage.py check_tasker_ai_readiness` for offline configuration checks. It neither authenticates credentials nor makes writes/calls. See [Stage 9 implementation](TASKER_STAGE9_IMPLEMENTATION_2026-10-29.md) for confirmed USD policy, setup, safety evidence and rollout/rollback gates. No production worker or provider was activated by Stage 9 implementation.
# Stage 11 source integration completion (30 October 2026)

See [Stage 11 completion](TASKER_STAGE11_COMPLETION_2026-10-30.md) for the seven additional Operations adapters, independent flags, source permission/owner/team/policy setup and additive migration dependencies. They use `business.reconcile` reference-only payloads and the existing worker/scheduler. Repair rotation shares one bounded budget across collections, replenishment and enabled operational sources; it does not hydrate native sources while their adapter is disabled. Unchanged source scans can enqueue a targeted in-app due/overdue cue once per company calendar day. A same-day source-change cue suppresses a duplicate due cue. Current source authorization, quiet/work hours and focus suppression are rechecked at delivery.

Outcome repair retracts retained accepted credit whose source proof no longer holds; goal totals and report validation exclude that credit immediately, before the sweep. No external send/provider is activated by this code. Production source setup and staged rollout remain separate operator actions.

## Stage 12 improvement and email (31 October 2026)

`routine.generate` uses the same leased worker and supervised scheduler. Payloads contain only a routine ID. The unique local occurrence date, SOP execution receipt and final lease fence prevent duplicate tasks. Calendar-local release checks avoid pre-release job floods. Latest/skip policies, a default skip-open overlap policy and 20-occurrence catch-up bounds limit backlog pressure. Permission/source-scope loss pauses a routine for review. Failed/cancelled job budgets are not reset by reconciliation.

`reports.smtp` is an external handler over the existing NotificationDelivery ledger. Its payload is only a delivery ID. SMTP is possible only with the improvement/report switches, separate live-email switch, approved SMTP company policy, explicit recipient grants and private sender configuration. Test-mode `reports.deliver` remains local. Both kinds are recognized by report repair; routine sweeps do not access domain records when disabled.

Before network dispatch SMTP commits UNKNOWN intent and increments the attempt counter. Uncertain sends are never automatically replayed; reconcile them with provider logs through the operating owner. Do not change UNKNOWN to QUEUED to retry. Provider acceptance is not delivery/read confirmation. Use `python manage.py check_tasker_report_readiness` for an offline, secret-free configuration check; it makes no database query, provider call or activation change. It does not authenticate the sender.

See [Stage 12 user/setup/rollback guide](TASKER_STAGE12_IMPLEMENTATION_2026-10-31.md). All three new switches default off. Roll back by disabling live email/recurrence and restarting supervised processes before disabling improvement or reverting matching code. Preserve occurrence/declaration history, dedupe tombstones and delivery uncertainty. No worker, scheduler or production sender is installed/activated by implementation alone.
