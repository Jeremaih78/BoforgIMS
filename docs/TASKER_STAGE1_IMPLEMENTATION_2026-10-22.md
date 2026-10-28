# Tasker Stage 1 implementation and verification

Completed local coding and verification on 22 October 2026, Africa/Harare. Stage scope: correctness, reliability and security of the existing module. Stages 2-13 were not implemented.

**Verdict updated 26 October:** Stage 1 is **verified for its local software and credential-cleanup scope**. The owner confirmed revocation on 22 October and authorized targeted README cleanup/history assessment on 26 October: one working-file token removed, zero remaining matches, and zero key-pattern blobs across 2 historical revisions/1 unique blob at this path under local refs. No key value was displayed or history rewritten. Replacement configuration/authentication, other paths and remote/artifact copies are not certified. See [key recovery](TASKER_API_KEY_RECOVERY.md). The detailed 22 October evidence below is retained as history.

## Delivered behavior

- Dashboard priorities use semantic urgency and executable states. Blocked/terminal tasks are excluded from executable priorities; important undated critical work can appear.
- Personal AI context uses true database totals, a bounded canonical task collection and ID references. Focus, commitments, critical work and a ready candidate receive protected selection; omissions are explicit. A backlog of 75 blocked critical tasks cannot hide the existence of ready work. Aggregate budgeting compacts text before dropping records and fails safely if necessary.
- All six personal AI responses receive exact local shape/type and semantic validation. Invalid IDs, blocked/terminal ready-work suggestions, negative/boolean durations, duplicates, malformed items and unsupported fields fail safely. Displayed task titles come from current records. Provider refusals, incomplete responses and empty responses are not successes.
- Checklist proposals carry task/checklist revision fingerprints. Apply refetches/locks records, checks fresh permissions/ownership/state and preserves replay safety. Empty selection does not consume a proposal; stale/legacy proposals require regeneration.
- Durable per-user minute/day request admission and per-attempt token reservations prevent concurrent overbooking. A request limit of one permits the first request. No provider I/O or retry sleep runs under the quota lock.
- Failed quick-add submissions preserve values, render field errors/summary and reopen the modal. Return URLs are constrained to local authorized navigation.
- Shared note saves normalize newlines and use content versions with row locks. Focus and regular edit forms reject stale writes; uncertain identical-content retries are safe. The browser serializes saves, preserves newer edits, retries transient failures and flushes before checklist/form/link navigation. Tab-local recovery drafts are user/task isolated and cleared on logout/account changes.
- Checklist progress is distinct from accepted completion. Explicit Complete/status actions or submitting 100 to the progress action accept a task. Completed tasks retain 100 until explicitly reopened. Active checklist progress uses live items; removing the last step resets unaccepted progress to zero. Admin save/delete/restore and inlines follow the same rule.
- Blocking/completing/cancelling/archiving/deleting closes timers where applicable, including admin task deletion. Stale focus/progress actions cannot complete cancelled/archived work or block completed work. Internal duration updates no longer suppress subsequent status audit events. Manual time writes lock the parent before aggregating duration.
- Notes are honestly labeled shared with authorized task viewers. Scratchpad/reflection/task-note text is excluded from personal AI context. Staff admin access to private plans/reviews and AI content is owner-scoped and read-only; superusers retain privileged operational access. No absolute privacy claim is made against superusers/database operators.
- A manual index-only secret gate blocks sensitive staged paths without reading them and redacts detected values. It neither scans unstaged/history content nor revokes credentials.

## Acceptance traceability

| Requirement | Evidence |
| --- | --- |
| T01-01 | [Staged gate](../tools/check_staged_secrets.py), synthetic tests in [test_secret_gate.py](../tasker/tests/test_secret_gate.py); [26 October credential cleanup evidence](TASKER_API_KEY_RECOVERY.md). The README was untouched during original Stage 1 work; subsequent targeted access was explicitly owner-authorized. |
| T01-02 to T01-05 | [Dashboard](../tasker/services/dashboard.py), [context](../tasker/ai/personal_context.py), priority/cap/count/privacy regressions in [test_personal_ai_correctness.py](../tasker/tests/test_personal_ai_correctness.py) and [test_stage1_integrity.py](../tasker/tests/test_stage1_integrity.py). |
| T01-06 to T01-08 | [Assistant validation/apply](../tasker/ai/personal_assistant.py), [provider guard](../tasker/ai/providers/openai.py), schema/semantic/permission/stale/replay tests in the same regression modules. |
| T01-09 to T01-10 | [Limiter](../tasker/ai/rate_limits.py), [service](../tasker/ai/service.py), [quota tests](../tasker/tests/test_ai_quotas.py), [migration test](../tasker/tests/test_quota_migration.py). PostgreSQL request/token races, retry/failure/stream/disconnect settlement pass. |
| T01-11 to T01-12 | [Save tests](../tasker/tests/test_save_reliability.py), [browser-event tests](../tasker/tests/js/focus_save_reliability.test.js), real desktop/mobile walkthrough below. |
| T01-13 to T01-14 | [Task services](../tasker/services/tasks.py), [lifecycle](../tasker/services/lifecycle.py), [focus](../tasker/services/focus.py), [admin](../tasker/admin.py), checklist/admin/timer/audit regressions in test_stage1_integrity.py and existing focus/task tests. |
| T01-15 | Owner-scoped content admin tests, no-journal context tests and inspected shared-notes labels on desktop/mobile. Superuser access is explicitly documented. |
| T01-16 | Updated [main guide](../README_Tasker.md), [foundation guide](../tasker/README_AI_FOUNDATION.md), [personal assistant guide](../tasker/README_AI_PERSONAL_ASSISTANT.md), matrix and status. |

## Verification results

All database work used a newly initialized, dedicated disposable PostgreSQL 18 cluster at `.local-review/tasker-stage1-20261021/pgdata`, loopback port **55441**, user `ims_review`. It was created empty for this Stage 1 work; no existing cluster or operational database was repurposed. `IMS_TEST_PORT=55441` was explicitly set. Django tests used disposable `test_ims_review`. Separate synthetic browser fixtures used base `ims_review` on that same disposable cluster.

| Check | Result |
| --- | --- |
| Existing Tasker baseline before fixes | 88 tests passed. |
| New red regressions before fixes | Reproduced request-limit-one rejection, concurrent quota failures, malformed/schema/eligibility errors, stale apply, overwritten notes, modal error loss, checklist/lifecycle/privacy/provider defects. |
| Final `manage.py test tasker config.tests --settings=config.test_settings --noinput` | **216 tests passed** in 65.305 seconds. Includes PostgreSQL concurrency and 0010-to-0011 migration/backfill cases. |
| `node --test tasker/tests/js/focus_save_reliability.test.js` | **10 passed**, zero failures. Includes in-flight edits, uncertain retries, conflicts/reload recovery, timeout, storage denial, account isolation and HTML permission errors. |
| `manage.py check --settings=config.test_settings` | Passed: zero issues. |
| `manage.py makemigrations tasker --check --dry-run --settings=config.test_settings` | Passed: no model changes detected, with the disposable database available. |
| Scoped `git diff --check` | Passed for changed implementation files. |
| Manual staged-secret command | Passed against the currently empty staged index. Synthetic gate tests verify detection/blocking/redaction. This is not a working-tree/history scan. |
| Independent source reviews | Findings on ready-candidate starvation, historical retry charging, timer/audit cleanup and admin/progress paths were fixed; final narrow review found no further actionable defects. |

The existing dotenv parse warning at line 9 remains. Configuration contents were not read, printed or edited. Logged failures from mocked AI/accounting tests are expected test paths; the final suite result is OK.

Local diagnostic logs are under `.local-review/tasker-stage1-20261021/`, including `tasker-final.log`. The database and browser server are local test infrastructure, not a deployment. Stop temporary services after checks; retained fixtures are synthetic.

### Browser walkthrough

Used the in-app browser against `127.0.0.1:8765` with synthetic user/tasks, AI disabled, local-memory email and blank provider credentials.

1. Desktop at 1440x1000: invalid quick-add retained priority/estimate, showed field errors, reopened/focused the dialog; correcting it created the task.
2. The dashboard showed undated critical work in priorities and blocked critical work in the timeline rather than the executable priority list.
3. Keyboard Enter started Focus Mode. Editing notes and immediately toggling a checklist persisted the latest note before navigation.
4. Two tabs opened from the same base version: the first saved; the second received a visible conflict and retained its draft alongside the latest saved text.
5. Mobile at a requested 390x844 viewport: controls and conflict resolution remained reachable, with no horizontal content overflow. The scrollbar leaves a 375px content area. Keyboard selection of the saved version and Stop & exit worked; the latest saved notes remained visible and the task stayed In Progress despite a complete checklist.
6. Temporary viewport overrides were reset and browser tabs closed.

Evidence: [desktop conflict](../output/tasker-stage1/focus-conflict-desktop.png), [mobile conflict](../output/tasker-stage1/focus-conflict-mobile.png). These are synthetic verification screenshots, not production employee data. Network failure/retry and storage-denial cases were simulated by automated browser-event tests, not claimed as live provider/outage exercises.

## Migration 0011 and operational behavior

[0011_aiusage_quota_reservations](../tasker/migrations/0011_aiusage_quota_reservations.py) adds reservation/charged-token/attempt/settlement fields, a settled-reservation constraint and a user/admission-time index. Existing measured token totals are preserved.

- Quota admission serializes per user. An admitted logical request consumes one minute/day request allowance; retries require separate token capacity.
- A reserved attempt becomes a conservative charge before provider I/O. Measured final usage replaces only that attempt's charge; unknown earlier retry usage remains charged. Construction/pre-dispatch failure releases provably unused reservations. Soft-deleting logs does not bypass limits.
- For legacy unknown usage or multiple historical attempts, the backfill charges at least the historical configuration's daily token allowance, or measured final tokens if larger, on that historical admission day. A same-day unresolved legacy call can intentionally exhaust the day's allowance. `quota_usage_estimated` discloses uncertainty; `quota_charged_tokens` is not interchangeable with measured `total_tokens`.
- No expiring reservation refunds possibly billed work. Interrupted/crashed provider attempts remain conservatively accounted for.
- This is request/token governance. The owner's $0.50-per-employee weekly monetary cap is still Stage 9 work.

Before eventual staging/production application, back up and verify restore, disable AI/new requests, apply additive migration 0011, deploy matching server/static assets, run checks and reopen only after authorized smoke tests. This report does not authorize or claim deployment. Existing configuration remains the AI enable/disable switch; correctness fixes are not hidden behind a second provider or worker stack.

Reload stale Focus Mode tabs after static/server rollout, preserving any unsaved drafts first. The versioned notes endpoint intentionally rejects old unversioned writes rather than overwriting newer notes. Old checklist proposals without fingerprints must be regenerated.

Rollback: stop new AI requests first and retain 0011's additive fields/audit data while assessing an application rollback. Do not casually reverse the migration and discard quota evidence. Older application code lacks the new protections; do not re-enable AI after a downgrade without explicit review. No Git reset or database reversal was performed here.

## Reproduce the checks

From `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS`, using the dedicated disposable cluster only:

```powershell
$env:IMS_TEST_PORT = '55441'
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D '.local-review\tasker-stage1-20261021\pgdata' -l '.local-review\tasker-stage1-20261021\postgres.log' -o '-h 127.0.0.1 -p 55441' -w start
& '.\venv\Scripts\python.exe' manage.py check --settings=config.test_settings
& '.\venv\Scripts\python.exe' manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
& '.\venv\Scripts\python.exe' manage.py test tasker config.tests --settings=config.test_settings --noinput
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tasker\tests\js\focus_save_reliability.test.js
& '.\venv\Scripts\python.exe' tools\check_staged_secrets.py
```

`--noinput` is appropriate here only because this cluster and test database were created for this disposable workflow. Do not copy the command to operational settings. If the dedicated service is already running, check its status instead of starting a second instance.

## Historical 22 October remaining gate and handoff

- **Owner action:** revoke/rotate the previously identified credential, remove the exposed value while preserving local edits, and address tracked history as needed. `tasker/README_AI_Extensions.md` was excluded from reads/diffs and left unchanged. No revocation/removal is claimed.
- No production deployment, paid provider calls, external messages, Git commit/push or Stage 2 implementation was performed.
- Stage 1 code and local test/browser evidence are ready for review. Close the owner security gate before marking the whole stage verified or advancing its release gate. See [current status](TASKER_IMPLEMENTATION_STATUS.md) for the next step.
