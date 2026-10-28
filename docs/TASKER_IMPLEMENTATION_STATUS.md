# Tasker implementation status

Updated 27 October 2026, Africa/Harare. This records preparation, Stage 1-8 application changes and remaining acceptance evidence.

## Current position

- Preparation documents created: [requirements matrix](TASKER_REQUIREMENTS_MATRIX.md), this record and [owner business requirements](TASKER_BUSINESS_REQUIREMENTS_2026-10-21.md).
- Read the [strategy](TASKER_PRODUCT_STRATEGY_2026-10-08.md) and [playbook](TASKER_IMPLEMENTATION_PLAYBOOK_2026-10-08.md) with the later business addendum. Explicit later owner decisions supersede proposed defaults.
- Stage 1-8 application changes are implemented and locally tested. See [Stage 1](TASKER_STAGE1_IMPLEMENTATION_2026-10-22.md), [Stage 2](TASKER_STAGE2_IMPLEMENTATION_2026-10-22.md), [Stage 3](TASKER_STAGE3_IMPLEMENTATION_2026-10-22.md), [Stage 4](TASKER_STAGE4_IMPLEMENTATION_2026-10-27.md), [Stage 5](TASKER_STAGE5_IMPLEMENTATION_2026-10-27.md) and [Stage 6](TASKER_STAGE6_IMPLEMENTATION_2026-10-27.md). Stage 7 evidence is in [Stage 7](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). Stage 8 evidence is in [Stage 8](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md). Stages 9-13 remain not started.
- Stage 1 verification: **216 Tasker/configuration tests and 10 JavaScript tests passed**, with desktop/mobile and two-tab note-save checks. After Stage 2, the full Tasker/configuration suite is **320 passing tests on PostgreSQL**; system/migration checks and Stage 2 desktop/mobile/keyboard/error-save walkthroughs pass. See the Stage 2 report for commands and evidence.
- Stage 3 verification: **336 Tasker/configuration tests passed in 79.955 seconds on disposable PostgreSQL 55441; 26 Node tests passed**. Real 1440px desktop and 390px mobile checks covered theme changes, preference persistence/remapping/validation, quick task capture, editable-field shortcut suppression, retained invalid form values and the remapped Focus blocker dialog without starting a timer. At 320px, Work and Preferences had matching client/scroll widths of 305px; at 390px, Now/Goals/Work matched at 375px. Enter opened the mobile filter disclosure; disabling N and resetting defaults worked, with no browser console errors. Native 200% zoom, an OS reduced-motion toggle walkthrough, screen-reader testing and actual employee usability targets remain unverified.
- Stage 4 verification: the full isolated PostgreSQL Tasker/configuration suite passed **401 tests in 166.027 seconds**; **47 Node tests passed** (20 capture, 17 preferences, 10 Focus). Evidence log: `.local-review/tasker-stage1-20261021/stage4-complete.log`; exact commands and browser evidence are in the Stage 4 report. Actual browser checks covered two-tab version conflict/draft retention and deliberate resolution, private capture promoted into team work, blocked stale save after an audience change, and 390px capture/pin/archive/Trash/restore. The 320px light-theme and 1440px dark-theme checks also passed, with matching client/scroll widths (305px and 1425px respectively), no final-tab console errors, saved screenshots, and restored system theme/default viewport; no native zoom, screen-reader, live inference or employee pilot certification is implied.
- Stage 5 verification: **469 Tasker/configuration tests passed in 262.573 seconds**, **77 Node tests passed**, system checks and migration drift check pass. Saved Work views, direct Focus, private idea parking, reliable notes/checklists, pause/interval resume, accepted completion, blocker alternatives, mobile bulk triage and keyboard reopening were exercised with synthetic data. A real PostgreSQL start race verifies one running timer. Revision checks cover same-clock writes and current criteria. See the Stage 5 report for evidence and limits.
- A dedicated disposable PostgreSQL 18 cluster runs on loopback **55441**. The old preparation endpoint on 55439 was not repurposed. The local preview uses `http://127.0.0.1:8765/`, with the Stage 5 Work screens and clearly labelled sample data; AI remains disabled.
- Confirmed: **USD 2m total sales over the next two years**; **$0.50 AI spending per employee per week**; **email-first EOD reporting, WhatsApp later**. Printing supply leadership by 2030 and African-business AI leadership within 36 months need measurable definitions.
- Overall Stage 1 status: **verified for its local software and credential-cleanup scope**. The owner confirmed key revocation on 22 October and authorized targeted cleanup/history assessment on 26 October. One token was removed from the affected README; follow-up found zero. Fixed-path history under local refs had 2 revisions, 1 unique blob and zero key-pattern blobs. No history rewrite occurred. This is not a repository-wide/remote-copy scan or independent provider revocation check. Private replacement configuration/authentication remains separate; AI stays disabled. See [key recovery](TASKER_API_KEY_RECOVERY.md).
- Stage 2 delivers scoped teams/delegation, goals/results/initiatives, accepted outcomes and baseline collection/export. Software verification is recorded in its report. Overall status is **implemented-verification-pending** for real roster/metrics/pilot timing and actual pre-rollout baseline adoption. No productivity uplift, real provider, email delivery or deployment is certified.
- Stage 3 delivers focused Now/Plan/Work/Goals navigation, an authorized basic team Strategy overview, secondary IMS switching, shared interface tokens/components, persistent per-user light/dark/system and shortcut preferences, responsive capture/navigation and accessible validation feedback. Stage 4 now enables Inbox; arrival is now provided by Stage 6, and the Stage 10 strategy board is not implemented. Overall Stage 3 status remains **implemented-verification-pending** for the native accessibility/zoom/motion checks and real employee usability evidence. Credential cleanup is recorded above; replacement configuration/authentication remains separate.
- Stage 4 delivers the **private, unscheduled Capture Inbox** for tasks, ideas, notes and links: original-text retention, search/tags/pin/archive/recoverable deletion, serialized versioned autosave and retry/conflict handling, manual Save without JavaScript, deliberate team sharing, and one-time task/initiative promotion with scoped provenance. On-demand legacy scratchpad import is owner-only and unique per DailyPlan; original notes are retained. Core non-AI behavior is **verified locally**. The optional proposal seam reports unavailable; AI interpretation/preview remains Stage 9 work, and no ambiguous interpreted date can be committed automatically. API-key authentication and the private dotenv-line-9 correction remain deferred by the user; AI stays disabled.

## Repository checkpoint

Stage 8 delivers structured blocker/handoff requests, targeted notifications with quiet/Focus/digests, one PostgreSQL outbox/worker/scheduler foundation, factual End day submissions and versioned scoped team reports through a shared test-email delivery ledger. **751 Python tests passed in 361.760 seconds; 77 Node tests pass.** The focused 112-test suite and browser/local worker/scheduler/health walkthrough passed, including the final roster-edge regression. See [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) and [job operations](TASKER_JOBS_RUNBOOK_2026-10-27.md). Actual sender/recipients/closing policy, retention and production supervision remain owner actions; live email and AI are disabled.

Stage 7 delivers a deterministic Now/Next/Waiting/Decisions operator, feasible critical-first recommendations, protected progress on older strategic/long work, stable Focus, versioned private evidence, fresh execution checks, synchronous invalidation and reasoned alternatives. **638 Python tests passed in 215.906 seconds; 77 Node tests pass.** Real PostgreSQL races, desktop/mobile workflows, retained stale drafts and keyboard preference saves passed; see [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). AI remains disabled.

Stage 6 delivers explicit work hours/timezone, local exclusions and union-based capacity; separate draft/preview/accepted time blocks; remaining-effort re-estimation; scoped acyclic dependencies with live readiness; and explicit self-service arrival/correction history. **561 Python tests passed in 200.318 seconds; 77 Node tests pass.** Competing schedule, dependency and arrival/correction races pass on isolated PostgreSQL. Browser evidence covers overloaded-plan rejection/resolution, unchanged deadlines, prerequisite acceptance/readiness, and mobile arrival corrections. See [Stage 6 evidence](TASKER_STAGE6_IMPLEMENTATION_2026-10-27.md). Stage 6 is verified for local non-AI software scope; actual policy, roster, correction/retention and rollout choices remain owner decisions.

Stage 5 delivers the compact Work list/Board, owner-local saved/default views, filters, inline/bulk triage, authorized detail drawer and definition-of-done acceptance. Focus reuses the single timer with advisory intervals, reliable notes and private idea parking. Stage 7 now replaces the earlier completion/blocking fallback with the shared capacity/dependency-aware operator. Stage 5 is **verified for local non-AI software scope**; native accessibility and employee pilot evidence remain separate.

| Item | Observed value |
| --- | --- |
| Repository | `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS` |
| Branch / starting revision | `review/ims-reliability` / `b416b71` |
| Existing modified file | `tasker/README_AI_Extensions.md`; targeted owner-authorized redaction on 26 October removed one token and preserved other bytes. Raw reads/diffs remain excluded. |
| Existing untracked artifacts | Strategy/playbook, `output/` and `tmp/`; preserved. |
| Preparation changes | Business addendum, matrix, status and narrow Markdown strategy/playbook amendments. |
| Stage 1 changes | Runtime correctness/save/privacy/quota/lifecycle changes, migration 0011, regression tests, staged-secret gate and updated safe documentation. See the implementation report for files and contracts. |
| Stage 2 changes | Scoped company/teams/delegation, goals/evidence, baseline collection/export, basic management UI, feature flag and additive migrations 0012-0013. Full 320-test PostgreSQL regression passes; see Stage 2 report. |
| Stage 3 changes | Focused Tasker shell, authorized basic Strategy overview, shared design components, local per-user theme/shortcut preferences and responsive/keyboard validation feedback. Full 336-test PostgreSQL regression and 26 Node tests pass; see Stage 3 report for files, browser evidence and remaining checks. |
| Stage 4 changes | CaptureItem/CaptureEvent and additive migration 0014; private Inbox UI/services, audited owner-only mutations, shared read access, durable versioned saving, promotion and deliberate legacy import. Staff admin is owner-scoped and read-only; superusers retain privileged operational viewing. Destination IDs avoid leaking inaccessible linked titles. Full 401-test PostgreSQL regression and 47 Node tests pass; see Stage 4 report. |
| Stage 5 changes | SavedWorkView/WorkbenchPreference and additive migration 0015; compact Work list/Board and scoped atomic triage, drawer and completion criteria, Focus intervals/private parking/next-task fallback. Full 469-test PostgreSQL regression and 77 Node tests pass; see Stage 5 report. |
| Stage 6 changes | Additive migration 0016; explicit WorkPolicy/Availability, versioned ScheduleBlocks/PlanningEvents and remaining effort; scoped DAG/readiness; immutable arrival/correction ledger. Dated intentions/reviews and existing lifecycle integrations preserve privacy. Full 561-test PostgreSQL regression and 77 Node tests pass; see Stage 6 report. |
| Stage 7 changes | Additive migration 0017; private policy/state/snapshot/choice evidence; deterministic operator, strict reviewed actions, domain invalidation, compact Now UI and shared Focus continuation. End-day review locks and fresh authority checks preserve the new event order. See Stage 7 report. |
| Stage 8 changes | Additive migrations 0018-0020; coordination/events, attention/preferences/shared delivery, PostgreSQL jobs/health, factual submissions/report snapshots/policy; new scoped screens and End day/operator/readiness integration. Local test-email adapter only; no production service or send activated. |
| Git actions | No staging, commit, stash, reset, clean, push or branch change. |

No AGENTS.md was found in checked ancestor paths or the repository file listing. Recheck applicable instructions in later sessions.

## Thirteen-stage ledger

Stage states: `not started`, `in progress`, `implemented-verification-pending`, `verified`. A stage needs gate evidence; elapsed time and a coding agent's conclusion do not establish completion.

| Stage | Delivery | Dependencies | Status | Mandatory verification / owner additions |
| --- | --- | --- | --- | --- |
| 1 | Correctness, reliability, security | Preparation; dedicated disposable PostgreSQL 55441 verified. | Verified (local scope) | 216 Python/10 JS original checks; later 336-test regression and 28 synthetic credential-tool tests pass. Owner-confirmed revocation plus authorized README cleanup and local path-history assessment complete. Provider activation/remote copies are not certified. |
| 2 | Goals, teams, baseline | Stage 1 software verified; owner explicitly deferred credential action for local Stage 2 coding. | Implemented-verification-pending | Scope/delegation/goal/evidence/baseline software delivered and locally tested. Actual roster, metric definitions, pilot and baseline adoption remain owner actions; arrival identity/access contract is implemented. |
| 3 | Premium navigation/UI | Stage 2 policies available; owner explicitly requested implementation. | Implemented-verification-pending | Software/local desktop, 390px/320px and keyboard mobile-filter checks pass. Native 200% zoom, OS reduced-motion walkthrough, screen-reader checks and employee usability measurements remain pending. Inbox is now enabled by Stage 4; arrival remains Stage 6; Strategy is a scoped basic overview. |
| 4 | Capture/durable notes | Stage 2 scope and Stage 3 UI contracts; user authorized implementation while deferring remaining API setup. | Verified (local non-AI core) | 401 Python/47 Node regression and actual conflict/sharing/promotion/390px lifecycle walkthroughs pass. Private unscheduled capture, search/organization, durable saving, recoverable deletion and legacy import verified. Optional proposal seam only; actual AI extraction/preview is pending Stage 9. Native accessibility/pilot evidence is not certified. |
| 5 | Workbench/focus | Stages 2-4; Stage 1 lifecycle; owner explicitly requested next-stage implementation. | Verified (local non-AI scope) | 469 Python/77 Node tests, real timer race and desktop/mobile/keyboard workflow evidence. Scoped saved views/triage/drawer, reliable Focus/private parking and accepted completion. Stage 7 now supplies the continuous operator; native accessibility/pilot certification remains separate. |
| 6 | Capacity/planning/dependencies | Stages 2 and 5; owner explicitly requested next-stage implementation. | Verified (local non-AI software scope) | 561 Python/77 Node tests and real interval/overload/acceptance/DAG/arrival/correction races; browser/mobile workflow evidence. Actual employee hours, attendance policy and rollout evidence remain separate. |
| 7 | Continuous operator | Stages 5-6; user explicitly requested the next implementation stage. | Verified (local non-AI software scope) | 638 Python/77 Node tests, PostgreSQL races, critical-first/fairness/unknown-data/stability/expiry/source-proof checks and desktop/mobile/keyboard workflow evidence. Current policy and rollout/retention remain separate; Stage 8 dispatch is not claimed. |
| 8 | Coordination, notifications, jobs | Stages 2, 6-7; explicit next-stage authorization. | Verified (local software and mock-delivery scope) | 751 Python/77 Node tests; real leased-worker races, targeted requests/quiet/digests, report privacy/revisions and mock delivery. Browser conflict/resolution/report and one-shot worker/scheduler/health evidence in Stage 8 report. Actual production policy, retention and activation remain separate. |
| 9 | Integrated governed AI | Stage 1; Stage 7 recommendations; Stage 8 jobs. | Not started | Scope/proposals/apply/undo/async/evals; weekly monetary reservations and company ceiling. |
| 10 | Management strategy/scenarios | Stages 2, 6-8; Stage 9 for optional AI. | Not started | Team/member/arrival/report scope, unchanged previews, deterministic safe scenario commit. |
| 11 | IMS source adapters | Stages 2, 8-10. | Not started | Collections first, then each listed source; source authorization/lifecycle/replay/rollback. |
| 12 | Improvement, SOPs, ideas | Stages 4, 8-11. | Not started | Carry-over decisions, recurring templates/experiments; End day reuses reporting pipeline. |
| 13 | Pilot, evidence, release readiness | Core gates, Stage 2 baseline, release owner. | Not started | Separate local readiness/productivity verdicts; named small cohort; email/staging/recovery evidence. |

### Extensions and delivery decisions

| Extension | Decision | Remaining inputs |
| --- | --- | --- |
| Email EOD | Required first channel; Stage 8/13. | Provider/sender/recipient rights, cadence/policy and authorized live checks. |
| WhatsApp EOD | Selected later; outside first email pilot. | Real transport, provider/sender/audience/cost policy after reporting foundation. |
| Calendar | Conditional; not selected. | Stage 6 and provider/privacy policy. |
| Voice | Conditional; not selected. | Stages 4/9; consent/provider/retention. |
| Offline | Conditional; not selected. | Stage 4 online reliability and local-storage policy. |
| Other push/chat | Conditional; not selected. | Stage 8 and explicit provider/audience selection. |

## Preparation checks performed (21 October; historical)

The unavailable-database results below describe preparation only. Stage 1 subsequently provisioned and verified dedicated disposable PostgreSQL on 55441; current passing evidence is in the implementation report above.

Commands ran from the repository above. `IMS_TEST_PORT` was explicitly pinned to `55439` for Django checks, preventing an inherited override from selecting another port. No operational database was used.

| Check | Actual result | Limitation |
| --- | --- | --- |
| Git status/branch/revision | Existing edits/artifacts and checkpoint recorded. | No unrestricted diff or excluded-file read. |
| `venv\Scripts\python.exe manage.py check --settings=config.test_settings` | Exit 0; no Django system-check issues. | Existing dotenv parse warning at line 9; contents not read/printed/changed. Not a database/browser/production check. |
| Loopback TCP probe | `127.0.0.1:55439` unavailable. | No database writes. |
| PostgreSQL 18 `pg_isready` | `no response` on that endpoint. | No authenticated/schema verification. |
| PostgreSQL 18 `pg_ctl status -D .local-review\pgdata` | `no server running`. | Cluster not initialized, started, deleted or repurposed. |
| `venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings` | Exit 0; no Tasker model changes detected. | Consistent migration history could not be checked because PostgreSQL was offline. Not a migration-history pass. |
| Tasker database tests | Not run in this preparation because endpoint is unavailable. | Earlier review found 88 tests but could not set up its database. No current count/full-suite pass claimed. |
| Browser/provider/email/WhatsApp/live deployment | Not run. | Later stage and separately scoped activation work. |
| Productivity uplift | Not measured. | Baseline/cohort observation has not begun. |

## Original preparation test setup and recovery

For current Stage 1 reproduction, use the dedicated cluster/port 55441 and commands in the implementation report. The original 55439 recovery notes below are retained as history; do not silently substitute an operational endpoint.

`config/test_settings.py` explicitly configures PostgreSQL at loopback port `IMS_TEST_PORT` or `55439`, base database/user `ims_review`, local memory email/cache, local file storage and a blank Tasker AI key. The default Django test database is **`test_ims_review`**, distinct from the base connection database.

Available paths:

- Python: `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS\venv\Scripts\python.exe`.
- PostgreSQL 18 tools: `C:\Program Files\PostgreSQL\18\bin`.
- Existing review cluster: `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS\.local-review\pgdata`.

Recovery steps:

1. Confirm the cluster and `test_ims_review` are disposable review infrastructure without operational/user-owned data. Their names alone do not prove that. Preserve the existing cluster; do not initialize over it, delete it or substitute production.
2. Recover the confirmed test cluster using its actual setup on loopback `55439`, or provision a separate explicitly disposable cluster. Verify role/database and test schema privileges. Do not migrate base `ims_review` during preparation.
3. Run the checks below. `--keepdb` avoids teardown, but migrations/tests still write to the test database, so its disposable purpose must be verified first.

```powershell
# From C:\Users\admin\Videos\Boforg\IMS\BoforgIMS
$env:IMS_TEST_PORT = '55439'
& 'C:\Program Files\PostgreSQL\18\bin\pg_isready.exe' -h 127.0.0.1 -p 55439 -U ims_review -d postgres
& '.\venv\Scripts\python.exe' manage.py check --settings=config.test_settings

# After confirming the isolated target and disposable test database:
& '.\venv\Scripts\python.exe' manage.py test tasker --settings=config.test_settings --keepdb
& '.\venv\Scripts\python.exe' manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
```

If unavailable, complete independent Stage 1 work and leave database verification pending. SQLite cannot certify PostgreSQL locking/constraints/concurrency.

## Architecture findings for the new requirements

- Company already exists in `accounting/models.py`; avoid a competing identity model.
- Stage 2 adds Team/TeamMembership and explicit company grants. Global capabilities alone no longer expose other employees' work. Arrival identity/access is defined; Stage 6 now provides arrival declaration/correction records.
- DailyPlan/DailyReview and focus TaskTimeEntry can supply work evidence, but do not establish arrival/productivity. Private reflections do not become report input.
- Stage 1 implements durable request/token reservations and settlement. A monetary weekly ledger/company ceiling is still absent, so the $0.50-per-employee weekly money cap remains Stage 9 work.
- Credit Control's notification helper can default an empty audience to all active users; do not inherit that rule. Its WhatsApp function is a stub. A manual job command is not proof of an operating scheduler.
- Stage 8 verifies one shared notification/delivery/outbox/job foundation locally, with a test-only email adapter. Actual sender/recipients, live transport activation and production supervision remain separate; see [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md).

## Remaining owner inputs/actions

| Item | Effect |
| --- | --- |
| Replacement key configuration/authentication | Owner-confirmed revocation and authorized cleanup/local path-history assessment complete. Owner's 26 October offline diagnostic detects a configured key; authentication and private correction of dotenv line 9 remain pending and explicitly deferred while Stage 6 proceeds. AI stays disabled; see key recovery guide. |
| Sales definition/baseline/source/owner/start date | Metric/currency now confirmed as USD 2m total sales over two years; returns/tax/recognition and measurement details still need agreement. |
| Printing/AI leadership definitions | Geography, evidence, baseline, metric, target and accountable owners. |
| Team/executive roster/company mapping | Delegation and report audience boundaries. |
| Hours, arrival corrections, definition/acceptance of done | Planning and honest work records. |
| Weekly AI reset/company ceiling/report cost attribution | Per-employee amount/period confirmed; production policy remains incomplete. |
| EOD sender/recipients/cutoff/consolidation/corrections/missing reports | Required before activating email. |
| Staging, worker/scheduler/backup owners | Still needed for eventual rollout. Stage 1 disposable database verification is complete on 55441. |
| Pilot IDs, comparison, accepted output and observation window | Required before baseline and productivity claims. |
| Stage 3 native accessibility and usability checks | Native 200% zoom, OS reduced-motion toggle, screen-reader walkthrough and actual employee usability observations remain pending. 320px and keyboard mobile-filter browser checks passed. |

## Stage completion template

```text
Stage / slice:
Status: not started | in progress | implemented-verification-pending | verified
Revision / selective checkpoint:
Requirements IDs and source:
Delivered behavior / manual demo:
Files / services / selectors / policies / routes / contracts:
Schema / migrations / legacy-data policy:
Private data / team and source permissions:
Flags / rollout / rollback / recovery:
Checks: exact command, target, result, evidence:
PostgreSQL / browser / provider checks not run:
Review findings resolved / open:
Remaining requirement IDs / owner actions:
Next authorized action / exact continuation prompt:
```

## Exact continuation prompt

```text
Review docs/TASKER_STAGE1_IMPLEMENTATION_2026-10-22.md,
docs/TASKER_REQUIREMENTS_MATRIX.md and this status before continuing.
Stage 1-8 coding/local verification are delivered; preserve all earlier work and read the Stage 7 evidence.
Read docs/TASKER_STAGE2_IMPLEMENTATION_2026-10-22.md and
docs/TASKER_STAGE3_IMPLEMENTATION_2026-10-22.md and
docs/TASKER_STAGE4_IMPLEMENTATION_2026-10-27.md and
docs/TASKER_STAGE5_IMPLEMENTATION_2026-10-27.md and
docs/TASKER_STAGE6_IMPLEMENTATION_2026-10-27.md and
docs/TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md and
docs/TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md as well.
Complete any remaining documented Stage 3 manual checks without
claiming native zoom, motion or screen-reader evidence not observed.
Do not restart the module or automatically begin Stage 9.

Stage 6 feasibility and arrival software is locally verified. Preserve
unknown capacity/effort, interval unions, deadline/block separation,
draft-preview-accept proofs, reasoned overrides and lifecycle cancellation.
Keep dependency graphs scoped/acyclic and inaccessible sources generic;
recheck readiness at Start/acceptance. Preserve immutable arrival originals,
reasoned corrections and current plus recorded team boundaries. Arrival
must not become worked-hour, payroll or productivity evidence. Actual
work policies, correction/retention choices and rollout remain separate.

Stage 5 Work/Focus is locally verified. Preserve current scope and
all-item bulk authorization/version checks, explicit current-criteria
completion acceptance, one timer, note recovery, private idea parking
and owner-local saved views. Stage 7 supplies the shared operator; preserve
hard eligibility, fixed critical-first/fairness rules, private versioned evidence,
expiry/source checks, stable Focus and explicit reasoned alternatives.
Stage 8 connects that interface to one coalesced PostgreSQL outbox.
Preserve lease fencing, bounded failure retries, attention/source checks and
immutable report evidence. Keep email on the test adapter; do not activate
live sends or turn legacy reflections into report submissions.
Read the Stage 8 jobs runbook before changing worker/scheduler behavior.
Existing explicit manual Focus starts retain their user-controlled policy.

Stage 4 non-AI capture is locally verified. Preserve private defaults,
the immutable original text, version/audience conflict checks, explicit
sharing, idempotent promotion, and unique owner-only scratchpad import.
The optional proposal seam is unavailable; do not claim AI extraction,
date interpretation or preview before Stage 9 is implemented.

Credential cleanup is recorded in docs/TASKER_API_KEY_RECOVERY.md:
owner-confirmed revocation, one working-document token removed and
zero key-pattern blobs in the fixed-path local-history assessment.
The 26 October exception authorized targeted helper access only.
Keep the README and secret files out of general reads/diffs; do not
infer provider revocation or replacement authentication from scans.
Private replacement authentication and dotenv-line-9 correction remain
separate user-deferred actions. Keep AI disabled.

Do not deploy, commit, send messages or make paid-provider calls from
this continuation prompt. The next implementation stage is Stage 9,
only on the user's instruction. Actual roster, metric and baseline
rollout acceptance and employee usability observations remain separate
from passing local software tests.
```

Preparation on 21 October changed documentation only. Stages 1-8 subsequently changed application code and local disposable test data. No operational database write, deployment, Git commit/push, paid-provider call, email or WhatsApp send was performed.
