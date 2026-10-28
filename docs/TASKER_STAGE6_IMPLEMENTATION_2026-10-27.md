# Tasker Stage 6 — Feasible planning, dependencies and arrival

Implemented 27 October 2026, Africa/Harare, following the owner's instruction to implement the next stage and subsequent continuations. Scope: **T06-01–T06-07, BIZ-W01 and BIZ-W01-P**. Stage 1–5 behavior and the business addendum remain authoritative. AI activation and private API configuration remain deferred.

## Delivered behavior

1. **Work hours and real capacity.** Each employee explicitly configures an IANA timezone, weekdays, start/end hours and a daily interruption buffer. New employees have unknown capacity until configured; the form's suggested hours are not active commitments. Leave and fixed commitments are local records independent of external calendars. Capacity uses unions of overlapping exclusions clipped to working windows, then subtracts the chosen fixed buffer once per local calendar date, floored at zero. Example: **480 − 60 fixed commitment − 60 leave − 60 buffer = 300 usable minutes**. Overnight shifts are clipped into local dates; interval arithmetic uses actual UTC elapsed time. Ambiguous/missing local wall times are rejected, and unresolved calendar boundaries return validation feedback instead of invented availability or a server error.
2. **Draft, preview, accept.** Real ScheduleBlocks have explicit start/end times, separate from task deadlines. A draft does not reserve capacity. Review displays the proposed duration, capacity figures, dependencies, missing effort and conflicts before acceptance. Availability, overlapping blocks, outside-hours work, deadline risk and overload warnings require resolution or a deliberate authorized override with a recorded reason. Permission failures, inactive owners, terminal/archived/blocked work, unfinished prerequisites and past block acceptance cannot be overridden. Acceptance locks the task and employee policy, checks current versions, and writes an audited snapshot. Concurrent acceptances for different tasks cannot both spend the same unreviewed capacity.
3. **Future planning and previous dates.** Time blocks and daily intentions have date navigation. Now, quick task dates, scratchpads and planning use the employee's local work date. Existing DailyPlan checkbox selections remain **intentions**, with explicit wording and links to capacity-reviewed time blocks. They do not silently reserve time. Their version proof and owner/task locks prevent stale intent saves and changed assignments from overwriting the plan. Historical and future end-day reviews are read-only, preventing old reviews from altering today's global carry-over choices.
4. **Honest remaining effort.** TaskEffortEstimate stores a current explicit remaining estimate anchored to recorded time. An original total estimate is usable before any time is recorded; afterward a new judgement is required. Exhausted estimates become unknown/re-estimation-needed, rather than silently reaching zero or accepting the task. Zero remaining minutes still requires outcome review. Re-estimation after an overrun includes context and an audit record.
5. **Scoped dependency graph.** TaskDependency is independent of parent/child hierarchy. Links require current permissions and the same company/team scope, or the same personal owner. Bounded graph traversal rejects cycles. Every reachable prerequisite must remain visible, active and accepted as completed. Reopening, deletion, archiving or lost access makes dependent work wait without exposing restricted task identities. Broken links can be removed using only the dependency-link identifier. Scope changes cannot silently break live dependency boundaries. Per-scope mutation serialization and revision checks reject moving graph snapshots. Focus Start and the existing next-task fallback enforce readiness; Work and task details show it.
6. **Explicit arrival with corrections.** Register arrival records the authenticated employee's server timestamp, local date/timezone and active team snapshot. One declaration per employee/local date handles retries and simultaneous submissions. Arrival is separate from authentication, Focus, physical presence, staffed hours, payroll and productivity; missing declarations stay missing. Only the employee can append a reasoned correction, within the original local date and not in the future. The original is retained. Corrections have idempotency and revision checks; manager views require both recorded team scope and current authorization/membership. Admin history is read-only and excludes unrelated team names.

## Important operating semantics

- **Arrival correction policy:** self-service corrections with immutable history are the local implementation policy. The owner has not supplied a production correction window, approval or retention policy. Those remain rollout decisions; no manager can edit another employee's declaration through this feature.
- **Changing an accepted block:** saving revised times explicitly returns it to draft and releases the old reservation. The UI warns before that action and labels the button accordingly. Earlier acceptance remains in PlanningEvent/TaskActivity history; the replacement needs another preview and acceptance. This is not a scenario preview that leaves every committed block unchanged; Stage 10 scenarios remain separate work.
- **Lifecycle changes:** completion, archive, deletion, reassignment and scope changes cancel affected draft/accepted blocks through existing services/admin integration, retaining audit evidence. Reopening does not revive a cancelled block. Even a direct out-of-service reassignment/deletion is excluded from former-owner capacity calculations.
- **Changed availability:** accepted blocks that now overlap exclusions, sit outside revised hours or overlap each other produce generic warnings on the date overview, even when total minutes still fit. Private exclusion notes and private task titles do not enter manager capacity summaries.
- **Allocation is not time remaining on the clock:** the displayed minutes describe full-day capacity minus reservations. They do not infer worked hours from arrival, timers or elapsed wall time. Overnight buffers apply once to each local date's allocation.
- **Readiness is live:** a later prerequisite reopening makes the dependent wait on its next check. It does not silently rewrite task status or automatically stop an existing Focus session. Stage 7 will define operator recomputation and interruption policy.
- Local planning works without AI, calendar credentials, a scheduler or external messaging. No Stage 7 operator, Stage 8 reporting pipeline or Stage 10 strategy board is claimed here.

## Files and contracts

| Area | Main files |
| --- | --- |
| Models and additive schema | `tasker/planning_models.py`, `dependency_models.py`, `arrival_models.py`, model re-exports; `migrations/0016_feasible_planning_arrival.py` |
| Capacity and commitments | `services/capacity.py`, `services/scheduling.py`, `services/effort.py`, `services/work_dates.py` |
| Dependencies/readiness | `services/dependencies.py`, existing Focus/next-action integration, `organization.validate_task_scope` hook |
| Arrival | `services/arrival.py`, `arrival_forms.py`, `views/arrival.py`, arrival templates/CSS |
| Screens | `planning_forms.py`, `views/planning.py`, planning templates/CSS, dated planner/review, Work/task-detail readiness, Arrival entry points |
| Existing lifecycle integration | Task save/delete/progress, Focus completion, Work archive/assignment and operational TaskAdmin |
| Admin | Owner/scoped read-only `planning_admin.py`, `dependency_admin.py`, `arrival_admin.py` |

Public services include `capacity_for_day`, `preview_schedule`, `save_schedule_draft`, `accept_schedule`, `cancel_schedule`, `effort_for_task`, `set_remaining_effort`, `readiness_for[_tasks]`, `add_dependency`, `remove_dependency`, `register_arrival` and `correct_arrival`. Views use fresh scope checks; URL identifiers and client-supplied revision proofs never grant authority. Error previews also reauthorize after concurrent reassignment.

Queries batch schedule estimates/running time and dependency readiness. Dependency bounds are 100 roots, 1,000 nodes, 2,000 edges and 128 levels; oversized/unstable graphs fail closed. Lists are limited/paginated. The Today dashboard now uses six fixed queries, including its new local WorkPolicy lookup; its performance regression expectation was updated for that intentional query.

## Migration, legacy data and recovery

Migration **0016** adds WorkPolicy, Availability, ScheduleBlock, TaskEffortEstimate, PlanningEvent, TaskDependency, DependencyGraphState, ArrivalDeclaration and ArrivalCorrection, with supporting indexes and uniqueness/positive-span constraints. It performs no bulk backfill, arrival inference, deadline conversion or fabricated estimate/policy configuration. Legacy plans, notes, accepted outcomes and time entries remain intact.

Applied only to the verified disposable local preview **127.0.0.1:55441 / ims_review**. Tests create/destroy **test_ims_review** on that cluster. Deployment remains a separate release action: apply the migration to the explicitly authorized target, restart application processes and refresh static assets. Role setup can refresh new model permissions; application capabilities retain existing scope controls.

For an application rollback, preserve additive data and use the normal release rollback. Do not reverse 0016 after real use without first preserving arrival/correction history, planning events, estimates, blocks and dependency links. Cancellation/soft deletion retains planning history; immutable arrival records have no destructive application control. No operational database, Git staging/commit/push, deployment, paid-provider request, email or WhatsApp send was performed.

## Verification

```powershell
$env:IMS_TEST_PORT = '55441'
.\venv\Scripts\python.exe manage.py test tasker config.tests --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tasker/tests/js/*.test.js
```

- **561 Tasker/configuration tests passed in 200.318 seconds** on isolated PostgreSQL. Log: `.local-review/tasker-stage1-20261021/stage6-complete.log`.
- **77 JavaScript tests passed** across existing capture, shared UI/preferences, Focus save/parking/navigation and Work behaviors. Log: `stage6-javascript.log` in the same directory. New Stage 6 screens use native forms rather than another client mutation controller.
- Focused planner/dependency/UI integration: **43 tests passed in 11.417 seconds**, before the final three date/privacy regressions were added and included in the full suite. Log: `stage6-integration.log`.
- System checks report no issues; Tasker model/migration drift check reports no changes.
- Final planning/shell/Work rendering check after presentation polish: **48 tests passed in 9.336 seconds**. Log: `stage6-final-ui.log`.
- Real PostgreSQL concurrency tests cover simultaneous DAG mutations, competing schedule acceptance against one employee's capacity, duplicate arrival registration and conflicting arrival corrections.
- Scope, hidden prerequisites, reopened/deleted work, naive/DST/overnight intervals, overlapping exclusions, unknown effort, re-estimation, deliberate overrides, stale previews, changed assignments, cancellation and non-revival pass. Availability changes warn about existing accepted conflicts. Historical review, local-date consistency and post-error access revocation have regression coverage.

Expected mocked AI/accounting errors in the test log are exercised failures, not live provider activity. The existing dotenv line-9 parse warning remains user-deferred; its contents were not read or changed. AI remains disabled.

## Browser walkthrough and remaining acceptance

Used the synthetic local review account and labelled Stage 6 samples. The preview rejected a 360-minute draft against 300 usable minutes without an override. Reducing it to 120 minutes cleared warnings; acceptance left 180 unallocated minutes and preserved the separate deadline. The dependency view initially showed Waiting; deliberate acceptance of the prerequisite outcome made it ready. Arrival registration and a reasoned mobile correction retained both the original timestamp and appended history.

At **390px**, arrival fields/history, work-hours controls and date navigation worked with matching **375px** client/body widths. At **320px**, dependency pages fit matching **305px** widths and remained keyboard-operable. At **1440px**, the final planning screen had matching **1440px** client/body widths. The review tab recorded no console errors; normal viewport sizing was restored and the time-block screen retained as the deliverable.

Screenshots in `output/tasker-stage6/`: `overloaded-preview-desktop.png`, `feasible-plan-desktop-1440.png`, `feasible-plan-mobile-390.png`, `arrival-mobile-390.png` and `dependency-ready-mobile-320.png`. A final isolated-data assertion verified the accepted shorter block, unchanged deadline, ready dependent task and retained original plus one arrival correction. The local app remains on loopback port 8765.

Native zoom, OS motion and screen-reader walkthroughs remain the existing Stage 3 acceptance tasks. Real employee working policies, roster/pilot setup, attendance correction/retention policy and operational rollout remain owner decisions. No usability-time target, staffed-hour denominator or greater-than-70% productivity improvement is certified by software tests or arrival records.

Stage 6 is locally verified for its non-AI software scope. **Stage 7 — the continuous next-action operator — is next, only on the owner's instruction.**
