# Tasker Stage 8 job foundation

27 October 2026. This runbook describes implemented local commands; it does not install or certify a production worker, scheduler, email provider or supervision service.

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

Each command defaults to a bounded single pass. The scheduler repairs coordination, notifications and reports through their service seams and fills missing/stale operator refresh jobs. A worker pass claims due jobs; repeat bounded passes if a handler deliberately schedules follow-up work. A future `run_after` is not processed early.

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
