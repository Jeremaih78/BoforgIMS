# Tasker Stage 2 implementation and local verification

22 October 2026, Africa/Harare. Stage 2 only: company goals, teams, delegation scope and pre-rollout measurement.

## Authorization and status

The owner explicitly instructed: **"implement stage 2 we will fix the key issue later"**. This supersedes the playbook's sequencing prerequisite for local Stage 2 implementation. It does not establish credential revocation, authorize deployment or enable paid providers. The excluded README and secret files remain unread and unchanged by this work. Stage 1's credential gate remains open.

Stage 2 application code and isolated verification are delivered. Overall acceptance remains **implemented-verification-pending** for real company/team configuration, metric definitions, pilot timing and baseline adoption. Synthetic browser records are not an employee baseline or evidence of a productivity improvement.

## Delivered behavior

- Reuses `accounting.Company`; adds `Team`, `TeamMembership`, explicit `OrganizationAccess` and immutable `OrganizationEvent` audit records. Permission groups supply capabilities; they do not establish reporting membership or executive authority.
- Adds nullable Task company/team mapping. Existing tasks stay unmapped and owner-only, with explicit superuser recovery access. Membership alone never exposes a coworker's personal tasks, daily notes, reflections or personal AI conversations.
- Managers see authorized shared team work and each member's visible task/completed/blocked totals. The team page opens a pre-scoped delegation form. Server-side assignment validates the actual live team and recipient, rejects forged IDs, and checks current permissions again after acquiring scope locks. Removing membership or company rights prevents stale writes.
- Owners cannot remove the reporting scope of assigned work to hide it. Parent/child scope is coherent. Reassignment closes the previous owner's timer in both the normal service and privileged admin path. Task and organization events retain changes.
- Adds Goal, KeyResult, Initiative and a single primary TaskContribution. Goal/result fields support accountable owner, unit, baseline, target, direction, aggregation, horizon and evidence policy. Unknown remains unknown; lower-is-better goals work; task/checklist percentages do not fabricate achievement.
- Explicitly loads the three agreed directions for a selected authorized company. Stable references make repeated loading safe, even after titles change. The USD 2m cumulative-sales target is recorded; unconfirmed recognition rules, baseline, owners and dates remain unset. Printing and African AI leadership measures remain undefined drafts.
- OutcomeMeasurement holds source occurrence identity, employee, date, value and evidence. A business occurrence can be attributed once per company, even across different goals or after soft deletion. Explicit authorized acceptance, audited retraction and correction preserve evidence. Reopening, archiving, deleting, reassignment or invalidating a task contribution retracts its accepted task evidence; recompletion does not silently reaccept it.
- Actual outcomes reject future dates; progress defensively excludes future records as well. Both SUM of accepted changes and LATEST absolute observations use exact Decimal arithmetic.
- Adds BaselineStudy and BaselineObservation. A study declares role, workload, accepted-output unit/definition, acceptance, staffed-hour denominator, quality/rework, source and comparison plan. Collection requires definitions and a date window. Starting collection records a server timestamp and rejects a known rollout that already began; later rollout backdating is checked against the recorded start. Unknown rollout timing remains unknown.
- Baseline observations are explicitly accepted/retracted, source-deduplicated and scoped to the employee or authorized manager/executive. Accepted measurements and study definitions cannot be silently rewritten. Missing hours/output/quality stay unknown; zero hours do not cause division by zero. CSV export enforces the same scope and escapes spreadsheet formula prefixes.
- Adds basic Goals, Teams and Productivity baseline screens with desktop/mobile navigation, edit/error handling and scoped admin inspection. Staff Tasker work admin is read-only and scoped; normal forms/services perform writes. Organization grants and report-recipient eligibility require explicit administrator setup. No report is sent.
- Defines arrival identity/access in `arrival_can_read`. Attendance records, arrival buttons, end-of-day delivery, weekly AI money budgeting and strategy-board UI remain with their later stages.

## Files and contracts

| Area | Files |
| --- | --- |
| Organization | `tasker/organization_models.py`, `tasker/organization.py`, `tasker/policies.py` |
| Goals/evidence | `tasker/goal_models.py`, `tasker/services/goals.py`, `tasker/services/business_goals.py` |
| Baseline | `tasker/baseline_models.py`, `tasker/services/baseline.py` |
| Public model API | `tasker/models.py` retains existing models and imports the new domain classes at its end. |
| Writes/lifecycle | `tasker/forms.py`, `tasker/services/tasks.py`, `tasker/services/focus.py`, `tasker/views/tasks.py`, `tasker/signals.py` |
| Workspace | `tasker/stage2_forms.py`, `tasker/views/direction.py`, `tasker/urls.py`, `tasker/templates/tasker/direction/`, `tasker/static/tasker/css/direction.css` |
| Integration | `tasker/admin.py`, `tasker/direction_admin.py`, `tasker/context_processors.py`, `templates/base.html`, Task form/detail templates, role setup command |
| Schema | `0012_organization_goals_baseline`, `0013_baseline_start_and_goal_reference` |
| Tests | `test_organization.py`, `test_goals.py`, `test_baseline.py`, `test_direction_ui.py`, `test_direction_migration.py`, updated role/task and historical migration fixtures |

`TASKER_DIRECTION_ENABLED` defaults to false when absent. The new workspace routes return 404 and its navigation links are hidden when disabled. Isolated test settings enable it explicitly. This UI flag does **not** restore the former global manager access policy: scope enforcement remains mandatory.

## Local verification

All database checks use the disposable PostgreSQL 18 service at loopback **55441**, role/base database `ims_review`; Django creates and destroys `test_ims_review`. No operational database was used.

```powershell
# Repository: C:\Users\admin\Videos\Boforg\IMS\BoforgIMS
$env:IMS_TEST_PORT = '55441'
& .\venv\Scripts\python.exe manage.py test tasker config.tests --settings=config.test_settings --noinput
& .\venv\Scripts\python.exe manage.py check --settings=config.test_settings
& .\venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
```

- Focused integration run: **106 tests passed**.
- Final full regression run: **320 tests passed in 184.837 seconds**. This includes the three reproduced future-outcome regressions and all Stage 1/configuration regressions. Log: `.local-review/tasker-stage1-20261021/stage2-complete.log`.
- System check: zero issues. Migration drift check: no changes. Explicit-file whitespace check passed; CRLF conversion notices do not change its result.
- PostgreSQL tests include simultaneous source replay, conflicting goal attribution, simultaneous acceptance, durable uniqueness, historical migrations, revoked membership and cached-permission revocation. The historical Stage 1 quota migration test now restores the actual current migration leaves instead of leaving the schema at 0011.
- Future-outcome tests were first run before the fix: all three failed, reproducing recording, acceptance and progress inflation. They are included in final regression verification.
- Real browser: followed Goals → sales result (USD target, baseline/progress unknown); Teams → member totals → Delegate task using Enter; blank-title error retained owner/company/team/description, then corrected save created the delegated sample task. Baseline draft could not start collection without definitions; error summary and values were retained. Mobile navigation/goal cards fit 390px without horizontal overflow; desktop inspected at 1440px. Normal viewport restored.
- Screenshots: `output/tasker-stage2/goals-desktop.png`, `output/tasker-stage2/goals-mobile.png`.
- Provider, email and WhatsApp calls were not made. AI-related test success/failure log messages use mocks. The intentionally mocked accounting posting failure in configuration tests is expected coverage, not a live operation.

## Preview and data

The local app remains at **http://127.0.0.1:8765/goals/**, using `tmp.tasker_stage1_ui_settings` and the dedicated disposable database. Only this preview base was migrated through 0013.

`tmp/tasker_stage2_ui_seed.py` asserts the exact isolated database/port and blank AI configuration before adding synthetic records. It uses the existing `stage1-ui-20261022` review account as a sample scoped manager, creates a clearly labelled sample company/team/employee and draft baseline, and loads the agreed business directions as drafts. Existing personal tasks are preserved. No accepted business outputs or actual employee measurements are seeded. The browser adds one explicitly named sample delegated task during verification.

## Legacy mapping, rollout and rollback

1. Finish the deferred credential revocation/removal/history action before release or enabling a real provider. This work does not certify that action.
2. Back up the real database and verify restoration on staging. Apply additive migrations 0012 then 0013. They create structures and nullable task fields; they do not guess company mapping, managers, report recipients, goals or historical achievements.
3. Refresh capability groups with `setup_tasker_roles` in the explicitly selected target. Configure actual Company access and Team memberships separately. A Manager group alone intentionally loses prior global task visibility. Keep unmapped tasks private; map each work task deliberately with its authorized owner and scope. Review parent/child relationships and primary contributions.
4. The system administrator grants named company-wide management/read/report rights through the new form. A company administrator configures teams/members; team managers operate their actual teams. Grant only the required capability bundle and company/team scope. Confirm employee/delegation/revocation and staff-admin boundary checks on staging.
5. Enable `TASKER_DIRECTION_ENABLED = True` only for the selected environment after configuration and validation. Load the agreed company drafts, then agree owners, dates, sales/evidence rules and printing/AI measures. Do not activate unknown commitments as published measurements.
6. Name the pilot employees and rollout date. Approve role-specific baseline definitions, source/acceptance and staffed-hour rules, quality/rework and the comparison plan. Start actual baseline collection before employee behavior changes. Dates alone do not establish that employees have not already changed their workflow.
7. Rehearse scoped CSV export, acceptance/retraction, source replay, task reopening and user revocation. No greater-than-70% productivity claim is warranted until a suitable later comparison has been collected and assessed.
8. For rollback, disable the new workspace and pause its writes; preserve the additive tables, evidence, grants and audit trail. Keep the access-scope fixes. Rolling back to pre-Stage-2 global authorization can expose personal/other-team data and requires a separate reviewed recovery plan. Do not delete evidence, reverse migrations or reset the checkout as a substitute for recovery. No deployment or automatic Git checkpoint was performed here.

## Completion update

Final isolated regression run completed successfully: **320 tests passed**, with zero Django system-check issues. Migration drift and explicit-file whitespace checks passed. Desktop/mobile navigation, keyboard delegation, error retention and baseline-start validation were checked in the real browser. The dedicated local app/database remain running for review; no operational database, paid provider or live notification was used.

The next implementation stage is Stage 3 (premium navigation/UI foundations), only on the owner's instruction. Real configuration/baseline acceptance and the deferred credential action remain explicit owner follow-up; later-stage code is not included.
