# IMS and Business Cloud staff email: local implementation report

Status: substantial cross-project integration implemented, tested locally and
left uncommitted for review on 3 November 2026. Not deployed or activated.
The complete brief is not fully satisfied: independently verified remote
delivery/bounce/complaint evidence is unsupported, and browser visual QA was
blocked. These limits are detailed below.

## Inspected baseline and source ownership

| Project | Branch and commit | Remote |
| --- | --- | --- |
| IMS | `release/ims-20261102-audited`, `611ca9f77caf42471071515163e4bc719ce878a7` | `https://github.com/Jeremaih78/BoforgIMS.git` |
| Cloud | `feature/intelligent-communications`, `2ff4174414f79fd413d3c02cf6c06cab8a6e3cc5` | `https://github.com/Jeremaih78/Boforg-Business-Cloud.git` |

IMS began with one modified document; Cloud began with 39 tracked modifications
and 305 untracked files, including the communications engine. No applicable
AGENTS.md or local skill directory was found. Original file hashes and copies
were recorded before edits. Guarded writes reject concurrent changes. Final
audit verified the same branches/commits, no unrelated baseline changes and no
unexpected new files. No reset, branch switch, commit, push or secret-file edit
was performed. Review patches overlay this exact dirty baseline; they are not a
standalone deployment artifact against either repository's HEAD.

The owner reports the Cloud engine is live. Repository code and local regression
tests were inspected; VPS deployment, production settings, sender authentication,
service registration and provider behavior were not independently inspected.

## Reused and added

Reused IMS NotificationDelivery, leased reference jobs, scheduler, current source
authorization, report proof/audience grants, scoped business links and delivery
calendars. Reused Cloud signed service credentials, tenant/application admission,
templates/sender controls, queue/attempt durability, SMTP outcome classification,
callback outbox, monitoring and retention. Existing customer-document integration
and report SMTP/test adapters remain available with their original gates.

Added transactional assignment/reassignment/material-update intents; existing
request/escalation/business-notification hooks; prospective company policies and
per-category preferences; bounded date-aware reminders and optional link-first
hourly digests; explicit CLOUD report mode; protected transport bindings;
authenticated final eligibility grants; receipt inbox, quarantine, scoped lookup
and conditional cancellation; polling repair; safe owner-only preview/history and
superuser policy/evidence administration. New additive migrations are IMS
0033/0034/0035 and Cloud 0003, with no historical backfill.

An immutable original binding/key survives ambiguous admission. Cloud queue
admission never sets provider acceptance. SMTP acceptance sets accepted_at only;
it never sets delivered_at or employee acknowledgement. UNKNOWN never switches
to another transport or automatic provider resend. Legacy SMTP and TEST handlers
are fenced for every Cloud-bound delivery even when the staff gate is disabled.
Report transport changes are blocked while Cloud admission may remain unsettled.

The final source grant is requested after SMTP's 354 reply and before body bytes,
binds the complete identity and payload digest, and lasts at most five seconds.
Disabled gates, denied/expired grants and IMS outages fail closed. A bounded
revocation race remains between the recorded IMS allow decision and body
initiation; this is not atomic distributed revocation or exactly-once SMTP.

Tasker currently uses the AI host, not the IMS-only host. Email links use the
explicit Tasker URL configuration (`config.host_urls.ai`) and approved origin.
Normal application authentication/authorization protects the deep links.

## Validation and exact reproducible commands

All checks used synthetic data in PostgreSQL 18 listening only at
127.0.0.1:55457. The verification runner ignores .env, blocks real SMTP and rejects
Python socket connections except the disposable PostgreSQL port. Fake HTTP and
provider transports were injected. No external messages were sent.

| Check | Recorded result |
| --- | --- |
| IMS tasker + cloud_communications regression | 1,236 tests passed in 15 exact, nonoverlapping fresh-database groups; all process exit codes 0 |
| Final affected IMS staff-email module | 39 tests passed; 16.708 seconds after final-review fixes |
| Final discovery/coverage comparison | 1,241 current cases; all covered by the original partitioned regression plus final affected module, none missing |
| Cloud apps.communications, including client/monitoring/URL and new source/concurrency tests | 90 tests passed; 17.178 seconds after final-review fixes |
| Django checks, both projects and both delivered safe runner entry points | No issues |
| Migration drift, both projects | No changes detected |
| OpenAPI YAML/reference resolution and paired protocol/fixture byte comparison | Passed |
| Ruff E4/E7/E9/F on newly added Python modules | Passed; unrelated baseline style preserved |
| Additive migration and pg_dump/pg_restore rehearsal | Every historical state and every original delivery/message column preserved; no queued backfill |

From the task workspace, the recorded IMS group command is executed for each
group ID g00 through g14 by `implementation/check_group.py`:

```powershell
$env:STAFF_EMAIL_TEST_GROUP='g00' # change only this group ID for each group
& 'C:\Users\admin\Videos\Boforg\IMS\BoforgIMS\.local-review\audit-venv\Scripts\python.exe' -u -B implementation/run_django.py ims test tasker cloud_communications --testrunner=group_runner.GroupedRunner --noinput --failfast --verbosity 1

$env:STAFF_EMAIL_TEST_GROUP='g99'
& 'C:\Users\admin\Videos\Boforg\IMS\BoforgIMS\.local-review\audit-venv\Scripts\python.exe' -u -B implementation/run_django.py ims test tasker.tests.test_cloud_email --noinput --failfast --verbosity 1
Remove-Item Env:STAFF_EMAIL_TEST_GROUP

& 'C:\Users\admin\Videos\Boforg Business Cloud\boforg-cloud\.venv\Scripts\python.exe' -u -B implementation/run_django.py cloud test apps.communications --keepdb --noinput
```

For portable reproduction from each repository, use its delivered safe runner
with the existing virtual environment and a fresh disposable PostgreSQL cluster:

```text
# IMS repository
python -B tools/test_staff_email.py test tasker cloud_communications --noinput
python -B tools/test_staff_email.py check
python -B tools/test_staff_email.py makemigrations --check --dry-run
python -B tools/test_staff_email.py check_staff_email

# Cloud repository
python -B tools/test_staff_email.py test apps.communications --noinput
python -B tools/test_staff_email.py check
python -B tools/test_staff_email.py makemigrations --check --dry-run
```

The full IMS suite must start from a fresh database. Existing transaction tests
flush data-migration seed rows; a kept database caused unrelated AI seed failures.
The initial regression also found a disabled-scheduler import compatibility issue
and an import-edit mistake; both were fixed before the passing runs above. One
fresh monolithic run ended without a final summary and is not counted as passed.
The exact partition inventory, individual logs/exit statuses and final coverage
proof are retained in the task workspace. Partitioning verifies the cases in
fresh groups; it does not claim an uninterrupted final monolithic run.

The expanded rehearsal moved IMS 0032 -> 0035 across all eight original states
and Cloud 0002 -> 0003 across all nine original states. Original values, including
keys, digests, timestamps, recipient/content fields and UNKNOWN, matched after
migration and after restoring each backup into another empty synthetic database.
The rehearsal creates no staff binding or send job from historical rows.

## Synthetic demonstration and evidence

`implementation/demonstrate.py` used the real authorized task creation service,
created one assignment intent and ran the overdue scan twice, yielding one
overdue intent. One fake Cloud submission became EXTERNAL_QUEUED with no
accepted_at; a simulated versioned acceptance became PROVIDER_ACCEPTED with
accepted_at and delivered_at still null. All demo database records were rolled
back. The generated HTML carries an explicit synthetic/no-external-delivery banner.
The centre's ownership, no-send preview and preferences were tested server-side.

Evidence under the local task workspace's `implementation` directory:

- `coverage-proof.json`, `group-summary.json`, `test-groups.json`, and
  `group-results/*.json` / `*.log`: IMS counts, exact case IDs, logs and exits.
- `review-ims.log`, `review-cloud.log`, `review-test-status.json`: exact final-review commands, results and process exit codes; earlier `cloud-final-deadline.log` retains the preceding 85-test result.
- `review-baseline-proof.json`: unchanged legacy document helper/template definitions and existing mail/gate configuration (including settings that remain absent and use Django defaults).
- `rehearsals/ims-result.json`, `cloud-result.json`, `ims_v3.backup` and
  `cloud_v3.backup`: all-state migration/restore results and backup hashes.
- `demo/result.json`, `demo/notification-centre.html`: rolled-back synthetic demo.
- `final-audit.json`, `ims-own-changes.patch`, `cloud-own-changes.patch`,
  `baseline.json`, `originals/`: review scope, hashes and preserved original files.

## Final review against the brief and original baseline

The final local review found and fixed these actionable issues:

1. Source authorization now rejects an expired dispatch lease even if its token
   still matches, and caps the body deadline by both grant and lease expiry.
2. A manually reviewed retry preserves acceptance uncertainty from every earlier
   attempt. It cannot make cancellation confirmed_unsent; source revocation or
   later terminal failure cannot turn an earlier uncertain attempt into a known
   unsent intent. Guarded queued/deferred receipts retain UNKNOWN certainty, and
   IMS preserves UNKNOWN separately from Cloud's current scheduling state.
3. Quarantined/superseded lookup facts stop reconciliation before choosing any
   cancellation target. Same-version acceptance timestamp/identity contradictions
   are quarantined; unsupported delivery claims and inconsistent receipt
   certainty are rejected.

These fixes have focused regression tests. The new historical-uncertainty
fences apply to guarded staff messages. Tenant/application-scoped lookup,
server-selected service identity, exact-payload same-key admission and existing
UNKNOWN provider non-retry behavior were checked against the preserved source.
The legacy document path keeps its unguarded SMTP call, document templates,
sender/provider settings and original cancellation/retry behavior. The full
Cloud communications regression covers its existing invoice/quotation flows.
No live document system was accessed or modified.

| Brief area | Local status |
| --- | --- |
| Reuse architecture and preserve dirty baseline | Implemented; map, originals, hashes and task-only patches retained |
| Assignment/reassignment, bounded reminders, requests/escalation, material updates, supported business links | Implemented through existing authorized services; no model-save/backfill mail |
| Manager reports and recipient/report grants | Explicit Cloud mode; existing report gate/proof preserved; minimal link-first content |
| Durable intents, stable occurrence keys and one transport authority | Implemented with existing ledger/jobs and protected binding |
| Final eligibility, current authorization, calendars and default-off gates | Implemented with short HMAC grants, lease fence and final transport checks |
| Tenant/client isolation, scoped API, callbacks and receipt replay rules | Implemented; paired protocol/fixtures and negative tests |
| UNKNOWN, cancellation, idempotent recovery and reconciliation | Implemented; historical uncertainty retained; no automatic fallback/provider resend |
| Centre/preferences, digest and safe diagnostics/retention | Implemented; server-side UI tests; browser visual QA outstanding |
| Additive migrations, all-state restore rehearsal, synthetic demo and rollback preparation | Passed locally; production effects excluded |
| Independently verified delivered/bounced/complaint evidence | Deferred: no trustworthy provider event source available in the inspected engine |
| Production credentials/configuration, staging/live canary and deployment | Not performed; require separate authorization and verified registration/configuration |

Final-review affected tests used the delivered safe entry points, with the exact
interpreter/argument arrays and exit statuses in review-test-status.json:

```text
# IMS, existing virtual environment
python -u -B tools/test_staff_email.py test tasker.tests.test_cloud_email --noinput --verbosity 1
# Cloud, existing virtual environment
python -u -B tools/test_staff_email.py test apps.communications --noinput --verbosity 1
```

## Remaining limits and blocked action

1. The inspected SMTP engine has no independently verified delivered, bounced
   or complaint event source. No such adapter or post-acceptance evidence state
   was fabricated. Protocol 2 explicitly reports delivery_evidence=NONE. The
   brief's verified delivery/bounce/complaint portion remains unsupported; it
   needs a specified trustworthy provider event source, provenance validation
   and corresponding implementation/tests before that portion can be complete.
2. Browser visual QA is blocked. Opening
   `file:///C:/Users/admin/Documents/Codex/2026-11-03/task/implementation/demo/notification-centre.html`
   was explicitly denied by the browser URL policy because file: is unsupported
   (only http:/https: allowed). Status: denied, not pending or an uncertain send.
   No workaround or alternate browser surface was attempted after the denial.
   Earlier isolated headless rendering attempts produced no screenshots. The
   synthetic HTML and passing server-side UI checks are available for review;
   desktop/mobile visual verification is outstanding.
3. Production deployment/configuration, actual worker supervision and provider
   authentication/delivery remain unverified. Staged/live canaries were not run.
   The runbook requires a reviewed artifact including the pre-existing dirty
   Cloud baseline, backups, dedicated staff service/tenant/sender/template
   registration, independent API/eligibility/receipt secrets, approved Tasker
   source/action hosts, explicit company/category/recipient approvals, a future
   watermark and owner approval before production effects.

No production delivery records were changed, no production database was migrated,
and no external messages were sent. TASKER_REPORT_EMAIL_LIVE_ENABLED was kept
false in local verification and remains default-off in source. Production
configuration was neither inspected nor changed; its actual current value is
not independently asserted. New staff/source-authorization gates remain
default-off. See TASKER_CLOUD_EMAIL_RUNBOOK.md for the prepared staged rollout,
rotation, monitoring, cancellation and rollback procedure.

## Exact files changed by this task

### IMS: 36 files (23 added to the recorded baseline)

```text
config/settings.py
config/staff_email_test_settings.py
docs/STAFF_EMAIL_SAFE.env.example
docs/TASKER_CLOUD_EMAIL_IMPLEMENTATION.md
docs/TASKER_CLOUD_EMAIL_MAP.md
docs/TASKER_CLOUD_EMAIL_RUNBOOK.md
docs/staff-email-v2.openapi.yaml
tasker/admin.py
tasker/cloud_email_admin.py
tasker/cloud_email_models.py
tasker/coordination_forms.py
tasker/email_protocol.py
tasker/management/commands/check_staff_email.py
tasker/management/commands/prune_staff_email.py
tasker/management/commands/reconcile_staff_email.py
tasker/migrations/0033_staff_cloud_email.py
tasker/migrations/0034_reminder_digest.py
tasker/migrations/0035_report_email_preference.py
tasker/models.py
tasker/notification_models.py
tasker/report_models.py
tasker/services/cloud_email.py
tasker/services/cloud_email_client.py
tasker/services/email_digest.py
tasker/services/jobs.py
tasker/services/notifications.py
tasker/services/report_email.py
tasker/services/reporting.py
tasker/services/tasks.py
tasker/templates/tasker/coordination/notifications.html
tasker/templates/tasker/email_centre.html
tasker/tests/fixtures/staff-email-v2.json
tasker/tests/test_cloud_email.py
tasker/urls.py
tasker/views/email.py
tools/test_staff_email.py
```

### CLOUD: 22 files (12 added to the recorded baseline)

```text
apps/communications/api.py
apps/communications/api_urls.py
apps/communications/callbacks.py
apps/communications/fixtures/staff-email-v2.json
apps/communications/management/commands/prune_communications.py
apps/communications/migrations/0003_source_authorization.py
apps/communications/models.py
apps/communications/rendering.py
apps/communications/services.py
apps/communications/source_authorization.py
apps/communications/source_protocol.py
apps/communications/tests_source_authorization.py
apps/communications/tests_source_concurrency.py
apps/communications/transports.py
apps/communications/worker.py
config/settings/base.py
config/settings/staff_email_test.py
docs/communications/STAFF_EMAIL_SAFE.env.example
docs/communications/TASKER_CLOUD_EMAIL_MAP.md
docs/communications/TASKER_CLOUD_EMAIL_RUNBOOK.md
docs/communications/staff-email-v2.openapi.yaml
tools/test_staff_email.py
```
