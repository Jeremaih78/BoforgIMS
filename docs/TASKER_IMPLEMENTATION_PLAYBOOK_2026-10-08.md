# Tasker implementation playbook

Prepared 8 October 2026.

This companion contains exact copy-and-paste prompts. Application changes are not implemented by generating this guide.

Business update, 21 October 2026: read [the owner requirements](TASKER_BUSINESS_REQUIREMENTS_2026-10-21.md), [requirements matrix](TASKER_REQUIREMENTS_MATRIX.md) and [current status](TASKER_IMPLEMENTATION_STATUS.md) before executing a stage. They add confirmed USD 2m cumulative sales over two years, printing/AI leadership goals, arrival registration, manager-scoped End day reports, required email-first delivery, WhatsApp later and a $0.50 per-employee weekly AI cap. Other unconfirmed business definitions remain unknown. The original 8 October PDF is the baseline guide; these newer Markdown records contain the updated scope.

## How to use

Open the existing BoforgIMS project. Complete owner actions, paste Preparation once, then execute one stage at a time. In each fresh stage chat paste the shared contract before its stage prompt. Use review, fixes, a manual demo and a selective local checkpoint between stages.

Default to sequential coding in one checkout. Read-only reviews can run separately. Keep verified changes and the strategy/playbook available if choosing an isolated worktree.

## Owner preparation

- Revoke/rotate the exposed credential with the provider account owner. Never paste it into chat. Sanitize the modified README before including it in any commit; preserve unrelated edits.
- Preserve current local work and identify an explicitly disposable PostgreSQL test database and staging target. Operational databases are not test substitutes.
- Supply three measurable goals, team/manager scope, private-data policy, work hours, accepted-output definitions, alert rules, AI budget/retention and pilot comparison. Missing answers remain unknown.
- Start baseline collection before employee rollout; nominate acceptance and deployment owners.

## Shared implementation contract

```text
SHARED IMPLEMENTATION CONTRACT
Work in the existing BoforgIMS Django repository. Read applicable
repository instructions, the strategy, this playbook, the requirements
matrix, stage status and owner business addendum dated 2026-10-21.
Inspect actual code and current Git status.
Implement the selected stage; do not stop after a plan and do not
automatically implement later stages. Make routine reversible choices.

Preserve existing local changes. Do not reset, clean, broadly stash,
automatically commit or run an unrestricted diff. Do not read or print
.env or secret values. tasker/README_AI_Extensions.md has existing
changes and may contain a credential: exclude it from reads and diffs.
Credential revocation/removal is a separate owner action; never claim
rotation merely because documentation changed.

Reuse completed Tasker services, selectors, policies and contracts.
Use additive migrations, explicit legacy-data policies, feature flags,
audit events and rollback notes where applicable. No competing write,
authorization, provider or background-processing paths. Preserve IMS.

Use isolated local/test settings and provider mocks. Do not write to an
operational database, deploy, push, publish, send external messages or
call paid providers. Test schema changes only on disposable databases.
PostgreSQL must verify locks, constraints and concurrency. If it is
unavailable, complete independent work and mark verification pending;
do not substitute production or claim unrun checks passed.

Run meaningful focused tests and required regressions. For UI changes,
check desktop, 390px mobile, keyboard and save/error behavior; report
browser limitations honestly. Keep private captures/reflections out of
manager views, exports, search, caches and manager AI context.

Update the requirements matrix and stage status with delivered behavior,
files/contracts/migrations, exact checks and results, assumptions,
remaining requirements and rollout/rollback notes. Distinguish not
started, in progress, implemented-verification-pending, and verified.
Finish with changed/tested/pending and the next authorized step.
```

## Preparation prompt

```text
Prepare this repository for the Tasker upgrade. This prompt authorizes
local preparation only; do not implement the 13 stages yet.

Read docs/TASKER_PRODUCT_STRATEGY_2026-10-08.md
and the shared contract in docs/TASKER_IMPLEMENTATION_PLAYBOOK_2026-10-08.md.
Inspect applicable instructions and Git status without exposing secrets.
Preserve all local edits, especially the excluded credential-bearing
README. Record the current branch and revision without changing them.

Create docs/TASKER_REQUIREMENTS_MATRIX.md: one ID per concrete strategy
requirement, source section, responsible stage, acceptance evidence and
status. Include cross-cutting requirements and conditional extensions.
Create docs/TASKER_IMPLEMENTATION_STATUS.md with the 13 stages, their
dependencies, owner actions and an explicit stage-completion template.

Inspect the Python/test configuration without printing secret values.
Verify an explicitly disposable PostgreSQL test endpoint. The prior
review could not connect to 127.0.0.1:55439; recheck current state rather
than assuming. Do not run tests against operational databases. Run safe
baseline checks, record any failures, and prepare exact isolated commands.
If the test service is unavailable, document recovery steps and continue
independent preparation without silently switching database targets.

Record business inputs still needed: goals, role-specific accepted
outputs, team boundaries, work hours, notification rules, AI budget and
pilot comparison. Do not invent production answers. Return the readiness
report and the exact next prompt to implement Stage 1.
```

## Stage 1: Correctness, reliability and security

Outcome: The existing Tasker behaves consistently and preserves user work.

Before starting: Preparation complete; disposable test setup identified.

Strategy sections 2, 3, 7, 9 and 12.

### Copy and paste

```text
Implement Stage 1 only under the shared implementation contract.
Reproduce current defects before fixing them; preserve the existing six
AI features, Django architecture and all unrelated local edits.

Use semantic priority ranking and correct blocked/terminal eligibility.
Include or explicitly account for current focus, top commitments and
important undated work beyond context caps. Deduplicate context, report
true total/included/omitted counts and enforce an aggregate input budget.
Validate all six AI response contracts locally: shapes, types, IDs,
positive durations, uniqueness and relevant lifecycle rules. Handle
malformed/refused/incomplete responses safely. Revalidate permissions,
revision and task state before applying checklist steps; preserve replay
safety. Correct request-quota boundaries and concurrent reservations;
define in-flight token reservation/reconciliation behavior.

Fix quick-add errors/value preservation and focus-note races, retries,
dirty-state/version handling and every navigation/save path. Exclude
soft-deleted checklist items from aggregates. Establish one documented
checklist/progress/accepted-completion policy and consistent timer cleanup.
Correct misleading private labels and review staff AI-content access and
scratchpad/reflection transmission. Update safe documentation. Leave
the excluded credential README intact; list rotation/removal as an owner
action. Do not build workers or the full operator in this stage.

Add focused regressions, run isolated checks, and update the requirements
matrix/status with exact evidence and any PostgreSQL verification pending.
```

### Acceptance gates

- Limit one permits one request; parallel requests cannot bypass it.
- All-blocked, >75 tasks, malformed output and stale apply are safe.
- Invalid capture retains input; rapid edits/navigation preserve notes.

Your demo: Submit invalid quick add, correct it, then edit focus notes and immediately toggle a checklist item. The saved text and lifecycle must remain correct.

## Stage 2: Company goals, teams and baseline

Outcome: Work has explicit scope, measurable purpose and a pre-rollout baseline.

Before starting: Stage 1 gates satisfied; business inputs recorded or marked unknown.

Strategy sections 6, 9 and 10.

### Copy and paste

```text
Implement Stage 2 only under the shared implementation contract.
Use the actual business inputs recorded during preparation. If an input
is missing, implement configurable structures and label it unknown;
do not seed invented company goals or reporting relationships.

Read the 2026-10-21 owner addendum: USD 2m cumulative sales over two
years, printing supply leadership by 2030 and African AI bridge leadership
within 36 months. Define team work visibility/delegation, explicit
executive recipient rights and the arrival identity/access contract.

Add Team and TeamMembership scope distinct from Django permission groups.
Respect any existing company model and define legacy-record mapping.
Implement explicit employee, team-manager and organization-wide access,
including policy-scoped selectors, assignment and administrative access.
Do not expand every manager to all private information.

Add measurable Goal/KeyResult and Initiative concepts plus task links.
Support owner, unit, baseline, target, direction, horizon, evidence and
primary contribution. Define accepted output, retraction/reopening and
deduplication; checking task boxes must not fabricate KPI achievement.
Add outcome measurement/instrumentation and a baseline collection/export
workflow before rollout. Capture role/workload/staffed-hour denominators,
quality, rework and data completeness without collecting surveillance.

Provide usable basic goal/team management and consistent scope helpers.
Test cross-team/forged-ID access, revoked membership, lower-is-better
goals, missing baseline, duplicate/retracted outcomes and legacy data.
Use additive migrations and document backfill/rollback and baseline
start criteria. Update the matrix/status; do not claim 70% improvement.
```

### Acceptance gates

- Role capabilities and membership scope are independently enforced.
- Accepted source outcomes count once and can be reconciled/retracted.
- Baseline collection starts before changing employee work behavior.

Your demo: A team manager opens their team goal and evidence; another team's restricted record is inaccessible. A missing metric remains visibly unknown.

## Stage 3: Premium navigation and UI foundations

Outcome: A focused, accessible Tasker shell with consistent interaction states.

Before starting: Stage 2 scope/policy contracts available.

Strategy sections 4 and 9.

### Copy and paste

```text
Implement Stage 3 only under the shared implementation contract.
Build within the existing Django project and preserve compatible routes,
authentication, IMS navigation and previous-stage behavior. Use the
illustrative design concept for direction, not as production code/data.

Create focused Tasker navigation for Now, Inbox, Plan, Work and Goals,
with Strategy available only to authorized managers. Keep the IMS module
switcher secondary. Connect available screens and feature-flag/hide
unfinished destinations rather than exposing broken links.

Establish reusable tokens/components for typography, spacing, forms,
buttons, drawers, empty states, errors, loading, saved/unsaved feedback,
confirmation and undo where implemented. Add persisted light/dark
preference, reduced-motion support, useful shortcuts with disable/remap
settings, semantic labels, visible focus and keyboard alternatives.

Make Now and capture easy to reach on mobile. Avoid unnecessary page
hops and do not rewrite the frontend framework solely for styling.
Check 1440px desktop and 390px mobile, with a 320px layout stress check,
keyboard-only navigation, zoom, validation and screen-reader names.
Ensure Tasker styles do not leak into IMS. Add only meaningful template
and navigation regressions; report browser inspection limitations.
Update the matrix/status, component guidance and rollout/rollback notes.
```

### Acceptance gates

- No dead navigation or unauthorized Strategy entry/endpoint.
- Mobile, keyboard, theme and reduced-motion flows stay usable.
- Tasker assets and changes do not break other IMS pages.

Your demo: Navigate Now, Work and Goals on a phone-sized screen, switch theme, and repeat key actions using only the keyboard.

## Stage 4: Capture inbox and durable notes

Outcome: Tasks, ideas, notes and links are saved without artificial urgency.

Before starting: Stages 2-3 scope and UI contracts available.

Strategy sections 4, 7 and 9.

### Copy and paste

```text
Implement Stage 4 only under the shared implementation contract.
Add owner-scoped CaptureItem records for task, idea, note and link,
retaining original text, visibility, triage state and promotion provenance.
Default to a private unscheduled inbox. Scheduling or sharing must be
explicit. Keep existing daily scratchpad history recoverable and provide
a deliberate migration/access path without duplicating records.

Provide global quick capture and mobile capture, search, tags, pinning,
archive and recoverable deletion. Implement versioned autosave, visible
pending/saved/error states, conflict handling and retry-safe writes.
Never announce success before persistence. Support deliberate team
sharing and idempotent promotion into a task/initiative with a source link.

Saving original capture must work without AI. Define an optional proposal
interface for later type/date/goal extraction, but do not block capture
on providers or implement a second AI stack. Ambiguous dates require a
clear preview before commitment. Keep online capture as the core here;
offline/voice are separate conditional extensions in Stage 12.

Test double submit, save conflict/retry, search/archive, promotion replay,
private/team access via URL/search/admin and failed network feedback.
Use the existing audited soft-delete conventions. Update the matrix,
status, migration/backfill and rollback documentation.
```

### Acceptance gates

- Capturing an idea creates no implicit due-today commitment.
- Private captures do not leak through search, admin or shared work.
- Repeated promotion/save requests do not create duplicate records.

Your demo: Capture a private idea, find it later, promote it once, and deliberately share the resulting work while keeping the original visibility clear.

## Stage 5: Task workbench and Focus Mode

Outcome: Employees can inspect, start and finish useful work with less navigation.

Before starting: Stages 3-4 UI/capture delivered; Stage 2 goal links available.

Strategy sections 3, 4 and 9.

### Copy and paste

```text
Implement Stage 5 only under the shared implementation contract.
Upgrade Work with compact list and board views, saved Mine/Waiting/Due
soon/Unscheduled views, useful filtering, inline actions, bulk triage and
a detail drawer. Enforce scope on every item/action, including mixed-owner
bulk requests. Keep existing detail URLs and audited mutation services.

Add a definition of done and explicit accepted-completion behavior using
the Stage 1 lifecycle policy. Display goal contribution and source links
when present. Separate checklist progress from accepted outcomes.
Starting work should open the existing Focus Mode in one action, with
current checklist step, working context, optional focus interval and
clear pause/block/complete behavior. Preserve the one-running-timer rule.

Add a private idea parking lot through the Stage 4 capture service.
Flush/version notes on every exit, checklist and mutation path; preserve
unsaved work on failures. Completion should offer useful next work via a
replaceable next-action interface until Stage 7 supplies the full engine.
Do not introduce a parallel timer or completion implementation.

Test bulk authorization, note races, timer conflicts, pause/resume,
blocking, completion/reopening and privacy of parked ideas. Check mobile
and keyboard workflows. Bound list/detail queries and document the user
flow, acceptance evidence, migrations and rollback in the matrix/status.
```

### Acceptance gates

- A task starts in one action; only one timer can run per employee.
- Bulk operations cannot mutate unauthorized tasks.
- Parked ideas remain private; notes survive all navigation paths.

Your demo: Start a task from Work, capture a distraction without leaving focus, pause/resume, then complete against its definition of done.

## Stage 6: Capacity, scheduling and dependencies

Outcome: Plans reflect available time and actual readiness.

Before starting: Stages 2 and 5 goal/lifecycle contracts available.

Strategy sections 5 and 9.

### Copy and paste

```text
Implement Stage 6 only under the shared implementation contract.
Add local WorkPolicy/Availability and real ScheduleBlock concepts.
Implement the owner's explicit Register arrival action with attributable
date/timezone, team-scoped visibility and an audited correction policy.
Arrival is a declaration, separate from authentication and focus timers;
do not infer physical presence, staffed hours or productivity from it.
Support work hours/timezone, leave, fixed commitments and a configurable
interruption buffer. Compute interval unions so overlapping exclusions
are not subtracted twice. Unknown effort/capacity remains visibly unknown.

Allow planning tomorrow/future dates and reviewing previous dates.
Distinguish planned start/end from task deadline; do not reuse due_time
as a scheduled start. Show conflicts/overload before acceptance and offer
defer, delegate, split or explicit authorized override with an audit.
Track remaining effort separately; overruns require re-estimation.

Implement TaskDependency as a scoped acyclic graph distinct from parent
hierarchy. Define completion/reopening/deletion and inaccessible-source
readiness policies without leaking restricted task content. Provide
reusable readiness/capacity services for the operator and strategy board.
Serialize/version concurrent scheduling and assignment changes.

Test interval overlap, 480 minutes minus 60 commitments minus 60 leave
minus 60 buffer equals 300 usable minutes, missing estimates, conflicts,
deadline/start separation, dependency chains/cycles and reopened work.
Verify PostgreSQL concurrency separately. External calendars are optional
later integration; local planning must work independently. Update the
matrix/status, model contracts and migration/backfill/rollback notes.
```

### Acceptance gates

- Capacity math handles overlaps and unknown values honestly.
- Dependency cycles fail; completed/reopened prerequisites recompute readiness.
- Conflicts are visible; concurrent edits cannot silently overwrite plans.

Your demo: Build an overloaded plan, resolve the warning, and complete a prerequisite to make its dependent task ready.

## Stage 7: The next-action operator

Outcome: Now gives a feasible next move with a useful, auditable reason.

Before starting: Stages 5-6 execution, readiness and capacity available.

Strategy sections 4, 5, 7 and 9.

### Copy and paste

```text
Implement Stage 7 only under the shared implementation contract.
Build a deterministic next-action service and Now/Next/Waiting/Decisions
queues. Enforce authorization, active lifecycle, dependency readiness,
working hours, capability and capacity before ranking. Blocked work goes
to Waiting or a separate resolver action, never executable Now.

Rank feasible work using configurable goal impact, deadline risk, agreed
urgency, work unlocked, age, remaining effort and context-switch cost.
Include important undated work and protect strategic/long tasks from
perpetual short-task preference. Missing data cannot become false zeroes.
Show canonical task, first action, definition of done, effort and reason
with Start, Choose another and Blocked. Record lightweight overrides.

Store versioned recommendation/candidate snapshots, policy/reasons and
expiry. Revalidate eligibility at execution. Keep active healthy focus
stable during routine recomputation; define genuine urgent exceptions.
Add domain event hooks for completion, blockers, assignment, deadline and
availability changes. Use synchronous local invalidation initially and a
clear interface for Stage 8 reliable dispatch; do not build two queues.

Test empty/all-blocked work, undated critical tasks, starvation prevention,
overcapacity, stale/reassigned/restricted tasks and focus stability.
The service must work with AI disabled. Bound queries and update the
matrix/status, policy documentation and rollout/rollback notes.
```

### Acceptance gates

- Blocked, terminal, deleted or unauthorized work is never executable Now.
- Routine refresh preserves focus; stale recommendations fail safely.
- Useful guidance remains available with AI disabled.

Your demo: Finish a task and see the next feasible move; block that move and receive alternative work with the waiting item retained.

## Stage 8: Blockers, handoffs, notifications and jobs

Outcome: Actionable coordination runs reliably without constant interruption.

Before starting: Stages 2, 6 and 7 scope, scheduling and domain events available.

Strategy sections 4, 5, 8 and 9.

### Copy and paste

```text
Implement Stage 8 only under the shared implementation contract.
Add structured Blocker/Handoff records: reason, requested action, resolver,
raised time, expected response, acknowledgement and escalation policy.
Use existing lifecycle services to close focus consistently and ask the
operator for alternative ready work. Preserve shared/private boundaries.

Implement targeted in-app Notification/Delivery records with actionable
links, deduplication, read/acknowledge states, quiet hours, focus suppression,
digests and policy-based escalation. Do not broadcast to all active users.
Cancel obsolete reminders after resolution and recheck audience access.

Choose one deployment-compatible background-processing approach after
inspecting current infrastructure; document the decision. Implement a
transactional outbox, retryable idempotent jobs, delivery/reconciliation
sweeps, failure diagnostics and bounded retention. Wire Stage 7 events
into it. Provide local run commands, scheduler/worker supervision and
health checks, without launching production services. Stages 9 and 11
must reuse this foundation. The owner addendum selects EOD email as
required first-channel work. Add versioned manager/team report snapshots
and a mocked email adapter through this same pipeline. Recheck executive
recipient rights, exclude private content, deduplicate End day/retry/
correction events and show missing submissions/failures. Do not activate
live sends without real sender/recipients and closing/consolidation policy.
WhatsApp is selected later; other external channels remain conditional.

Test duplicate/out-of-order events, worker restart, retries, resolver and
permission changes, quiet hours, acknowledgement versus escalation and
obsolete-reminder cancellation. Do not claim scheduler deployment from
merely adding a function. Update the matrix/status and operational,
migration/backfill, feature-flag and rollback instructions.
```

### Acceptance gates

- Repeated events/restarts do not duplicate actions or deliveries.
- Only authorized owners/resolvers/managers receive notifications.
- Worker/scheduler can run locally and expose failed-job diagnostics.

Your demo: Raise a blocker, acknowledge it as the resolver, resolve it, and confirm that downstream work unlocks without repeated alerts.

## Stage 9: Integrated OpenAI assistant and proposals

Outcome: AI proposals become concrete, governed actions inside the work flow.

Before starting: Stages 1, 7 and 8 validation, operator and job foundation available.

Strategy sections 4, 7 and 9.

### Copy and paste

```text
Implement Stage 9 only under the shared implementation contract.
Reuse AIService, the Responses adapter, Stage 7 events and Stage 8 jobs.
Add no second worker/provider stack or synchronous long-running call.

Build versioned compact context with one task record plus ID references,
true totals/omissions and aggregate token budget. Include authorized goals,
readiness/capacity/freshness. Separate personal from manager-safe context;
exclude private captures/reflections/transcripts from manager assistance.
Implement async pending/cancel/retry states and deterministic fallback.

Add capture extraction, feasible plan proposals, explanations, breakdown,
briefings and blocker/handoff summaries beside relevant work. Start with
a small allowlisted tool set routed through existing policies/services.
Add proposal preview, actor/scope, versions, expiry, source IDs and atomic
idempotent apply. Recheck live eligibility. Undo is an audited compensating
change that refuses to overwrite intervening human edits. No arbitrary
ORM, external communications or autonomous finance/stock writes.
Identity/scope must come from authenticated server context, never AI-
supplied IDs. Read/propose tools cannot commit; apply requires an explicit
authorized user action against the displayed proposal.

Implement model/capability preflight, immutable effective prompt/config
fingerprints, user/company request/token/spend reservations and settlement,
feedback/history links and content retention/redaction. Provider store=false
is not a zero-retention claim. Mock provider access; real availability
remains unverified until a separately authorized smoke test.

Enforce the owner's $0.50 per-employee weekly monetary limit through one
cost-rated ledger and atomic pre-dispatch reservations across interactive,
background and retry paths. Do not replace it with a fixed token quota.
Record currency/reset, company ceiling and report-cost attribution policy;
do not silently invent production settings or enable paid calls.

Add evals for overload, all-blocked/empty work, missing estimates, >75
tasks, malicious notes, cross-team access, stale/replayed apply/undo,
timeouts/restarts/outage and budgets. Record quality/latency/cost criteria
before release. Update the matrix/status and rollout/rollback evidence.
```

### Acceptance gates

- Apply/replay/expiry/revoked access and conflicting undo are tested.
- No user request waits for long provider work; core flow survives outage.
- Budget, retention, context privacy and representative evals have evidence.

Your demo: Preview an AI plan, accept it once, then change a task manually. A stale proposal or undo must not overwrite that edit.

## Stage 10: Management strategy board and scenarios

Outcome: Managers can identify interventions that unlock company work.

Before starting: Stages 2, 6-8 goal/scope, capacity, operator and events available.

Strategy sections 6 and 9.

### Copy and paste

```text
Implement Stage 10 only under the shared implementation contract.
Build an authorized Strategy workspace with goals across the board and
teams/people as the execution lanes. Show declared current and next work,
committed versus available capacity, deadline risk, blocker age/resolver
and last confirmed update. Unknown data stays unknown; timer/activity
is not proof of presence or productivity. Keep private content excluded.

Provide execution, dependency and capacity views plus an exception drawer
for unowned work, orphan goals, overloaded people, stale confirmations,
overdue handoffs and commitments without sufficient capacity. Use scoped
drilldowns, bounded queries, accessible alternatives to graph/drag controls
and explicit forecast uncertainty. Do not rank employees by task counts.

Implement draft what-if reassignment/priority/deadline scenarios. Preview
must not mutate live work. Show displaced commitments and dependency/
capacity effects from deterministic services. Commit only after explicit
manager action, with transactional permission/version/feasibility checks,
an audited change set and correctly targeted notifications. AI may explain
facts via Stage 9 where available, but must not invent forecasts.

Test team/organization scope on all views, stale scenario commit after
concurrent changes, keyboard alternatives, missing data, unchanged preview
records and authorized commit/notification. Update the matrix/status,
performance evidence and feature-flag/migration/rollback documentation.
```

### Acceptance gates

- Every board/drilldown obeys scope and excludes private information.
- Scenario previews leave live assignments unchanged.
- Commit rejects stale versions and shows effects on prior commitments.

Your demo: Preview moving work to another qualified employee; inspect what is displaced, then commit or discard without accidental live changes.

## Stage 11: IMS integration through source adapters

Outcome: Tasker leads operational work while each IMS source remains authoritative.

Before starting: Stages 2 and 8-10 scope, reliable jobs and board available.

Strategy sections 8 and 9.

### Copy and paste

```text
Implement Stage 11 under the shared implementation contract, in small
verified adapter slices. Start with Credit Control CollectionTask and
payment promises. Complete and test that adapter before expanding.

Create BusinessRecordLink/source adapters with unique source/type/record/
action keys, one responsible owner, canonical source permissions and
source-driven lifecycle. Reuse Stage 8 outbox/jobs and periodic repair
sweeps. Do not duplicate collections into a conflicting task workflow or
inherit its global notification audience. Reconcile completion, reopen,
reassignment and deletion; cancel stale reminders and deduplicate outcomes.

For each subsequent adapter, inspect its domain service and add source-
linked actions: available-stock replenishment; shipment exceptions and
receiving; service/repair cases; stocktake approvals; quotation follow-up;
paid-order fulfilment; supplier-bill review and fiscal close. Define sales
owner/next-contact policy before quotation automation. Preserve existing
approvals. A Tasker checkbox cannot post payment, move stock or bypass an
approval. Use Stage 2 accepted outcomes only when source evidence exists.

Test event replay, missed-event reconciliation, worker failure, source
lifecycle changes, unauthorized source access and cross-scope leakage for
each adapter. Keep separate flags and rollback for each. Record completed
and still-pending adapters explicitly; do not claim the entire stage is
complete after collections alone. Update the matrix/status and source
ownership/runbook documentation after every slice.
```

### Acceptance gates

- One repeated source event produces one action and one accepted outcome.
- Tasker access alone never grants access to a restricted source record.
- Each adapter has source lifecycle, retry/reconciliation and rollback tests.

Your demo: Open a collections action in Tasker, resolve it through its source workflow, and verify Tasker reconciles it after repeated delivery.

## Stage 12: Improvement routines, SOPs and ideas

Outcome: Repeated friction becomes better routines and small experiments.

Before starting: Stages 4 and 8-11 capture, jobs and operational evidence available.

Strategy sections 4, 5, 7, 9 and 10.

### Copy and paste

```text
Implement the core of Stage 12 under the shared implementation contract.
Add carry-over decisions that offer clarify, split, delegate, reschedule
with reason or stop. Preserve employee control and do not endlessly roll
all work forward. Build weekly personal/team reviews and a concise manager
decision agenda from authorized unresolved approvals and blocker facts.

Implement recurring work and versioned reusable SOP/checklist templates.
Use the existing job foundation, occurrence identity and idempotency so
retries do not duplicate instances. Template edits affect future work
without rewriting historical execution evidence. Include pause/resume,
exceptions and missed-occurrence policy.

Implement idea review, related-idea grouping/duplicate suggestions and
promotion into a small experiment with owner, expected benefit, review
date and accepted outcome. Use Stage 9 proposals where available without
making AI required. Sharing private ideas must stay deliberate.

Implement End day through the Stage 8 report snapshots/delivery pipeline,
with explicit close/correction rules and no private reflection leakage.

Test recurrence replay/timezone/missed-run behavior, template history,
carry-over choices, experiment provenance and private/team review scope.
Update the matrix/status, user guidance and rollback documentation.

Calendar, voice, offline capture and unselected external channels remain
conditional extensions. The owner has selected EOD email first and
WhatsApp later; do not defer email as optional. Reuse Stage 8, retain
unknown provider/policy inputs explicitly, and do not activate live
delivery from this implementation prompt.
```

### Acceptance gates

- Recurring jobs are idempotent and preserve prior execution history.
- Repeated carry-over leads to an explicit useful decision.
- Ideas retain provenance and privacy through sharing/promotion.

Your demo: Resolve a repeatedly deferred task, instantiate one recurring SOP twice safely, and promote a shared idea into a measurable experiment.

## Stage 13: Pilot, productivity validation and rollout

Outcome: A measured release decision supported by quality and operational evidence.

Before starting: Core gates verified; Stage 2 baseline collected; release owner identified.

Strategy sections 9-12.

### Copy and paste

```text
Implement Stage 13 locally under the shared implementation contract.
Reconcile every strategy requirement with the matrix and status; no
mandatory requirement may disappear because a summary was shorter.
Separate complete, failed, verification-pending and conditional/deferred.

Build pilot reporting for comparable accepted output per staffed hour,
cycle time, blocked hours, deadline reliability, quality/rework, overtime,
admin effort and data completeness. Use the baseline captured in Stage 2.
Predefine role/workload adjustment, comparison/staggered rollout, observation
window, denominator and uncertainty. Checking tasks or running timers is
not productivity proof. Report the >70% uplift as a hypothesis to test.
Track two sub-gates: local release readiness, and actual productivity
validation. Local/synthetic tests verify the release candidate only.
Keep productivity validation pending until separately authorized rollout,
real cohort/comparison data and the predefined observation window exist.

Create a runnable end-to-end pilot walkthrough: capture an idea, promote,
link goal, plan within capacity, start focus, block/handoff, resolve, accept
source outcome and inspect manager board. Include employee, manager and
restricted-user scenarios, degraded AI and failed-job recovery.

Run focused and required regressions, PostgreSQL migration/concurrency
checks, UI accessibility/mobile checks and staging rehearsal where an
isolated environment is available. Produce the release candidate evidence,
per-feature flags, staff training, monitored rollout/cohort plan, actual
deployment/migration/worker order, health checks and rollback/restore
runbook. Do not deploy or send pilot invitations from this prompt.
Record real-data decisions and any owner actions still needed. Update
the matrix/status and provide a concrete release-readiness verdict.
```

### Acceptance gates

- All mandatory requirements have verified evidence or an explicit blocker.
- Release-readiness gate: verified checks, worker health and recovery runbook.
- Productivity gate: real comparison data, quality and uncertainty over the agreed window.

Your demo: Run the complete employee-to-manager journey on staging and inspect the pilot dashboard alongside quality and missing-data indicators.

## Independent review prompt

```text
Review the current stage against docs/TASKER_PRODUCT_STRATEGY_2026-10-08.md,
the playbook contract, docs/TASKER_REQUIREMENTS_MATRIX.md
and docs/TASKER_IMPLEMENTATION_STATUS.md. Read only stage-relevant files
and sanitized diffs; exclude secret files and the credential-bearing
README. This is a review: do not edit implementation files.

Check behavior, privacy, authorization, state transitions, concurrency,
migrations, idempotency, failure handling, previous-stage compatibility
and whether the acceptance evidence supports each completed requirement.
Run focused safe checks if useful. Identify concrete actionable defects
with file/line evidence and distinguish code defects from unavailable
verification. Do not certify PostgreSQL behavior from SQLite results.
Report defects by severity, uncovered requirements and a clear verdict:
ready for checkpoint, needs fixes, or verification pending.
```

## Fix findings prompt

```text
Fix the concrete findings from the latest stage review under the shared
contract. Stay within the current stage, preserve unrelated local edits,
and do not introduce a second architecture. Add meaningful regressions
for the defects, rerun the relevant checks, and update docs/TASKER_IMPLEMENTATION_STATUS.md
and docs/TASKER_REQUIREMENTS_MATRIX.md. Report each finding as resolved,
still open, or verification pending with evidence. Do not start the next
stage or claim completion while its mandatory gates remain unresolved.
```

## Continue incomplete stage prompt

```text
Continue the current incomplete Tasker stage. Read docs/TASKER_IMPLEMENTATION_STATUS.md,
docs/TASKER_REQUIREMENTS_MATRIX.md, the strategy and shared contract.
Inspect actual implementation before changing anything. Preserve finished
work and local edits. Resume from recorded outstanding requirements;
do not restart the module or repeat already-passing checks without reason.
Complete independent coding and meaningful tests, record any external
blocker precisely, and update the stage handoff. Do not advance to later
stages or treat elapsed time as approval.
```

## Selective local checkpoint prompt

```text
Create a local checkpoint for the verified current Tasker stage.
Inspect Git status and stage-relevant sanitized diffs. Preserve unrelated
changes. Exclude .env, secrets, credential-bearing documentation and
unrelated files; do not use blanket staging. Confirm the stage status
and requirements evidence are accurate. If secret-containing stage files
cannot be safely included, leave them unstaged and report the issue.
Commit only the reviewed stage changes with a descriptive local message.
Do not push, rewrite history, merge or deploy. Return the commit ID and
any intentionally uncommitted work.
```

## Conditional extensions

These use separate provider/policy choices and explicit selected/deferred/complete tracking. Owner update: EOD email is selected and required for the first email pilot; WhatsApp is selected for later. Calendar, voice, offline and other channels remain conditional. The channel prompt below applies to the selected email adapter as well as later channels, with no live sending during implementation.

### Calendar integration

```text
Implement the optional calendar extension under the shared contract.
Use the selected authorized calendar provider and Stage 6 availability
contracts. Keep local planning usable without it. Start with read-only
busy-time sync; define timezone, recurring events, edits/cancellation,
token storage, disconnect, permissions and reconciliation. Do not copy
private meeting content into manager views or write calendar events
without separately selected behavior. Mock the connector and test overlap,
refresh failure, revocation and disconnect. Record provider setup and
remaining live verification; update the matrix/status.
```

### Voice capture

```text
Implement optional voice capture under the shared contract after the
provider, consent and retention policy are selected. Keep text capture
available. Use an explicit record/stop control and editable transcript;
save a private draft before any scheduling or sharing. Do not record in
the background. Reuse Stage 9 budget/error handling and Stage 4 capture
idempotency. Mock audio/provider calls. Test denied microphone access,
interruption, transcript correction, retry, deletion and privacy; record
live verification and update the matrix/status.
```

### Offline capture

```text
Implement optional offline capture under the shared contract after the
online flow is verified and local-storage policy is selected. Queue only
minimal per-user drafts with clear pending/synced/error states, occurrence
IDs, version conflict handling and retry-safe synchronization. Isolate
accounts, expire sensitive drafts and handle logout/account switching.
Never silently schedule/share or report unsynced data as saved on server.
Test offline/reconnect, repeated delivery, conflicting edits, revoked
access and logout. Document supported browsers and limitations; update
the matrix/status. Preserve the normal online path.
```

### Email, push or chat delivery

```text
Implement one explicitly selected external notification channel under
the shared contract after provider, recipients, consent and sending rules
are supplied. Reuse Stage 8 Notification/Delivery; no separate reminder
engine or broadcast audience. Add minimal templates, deduplication,
quiet-hour/digest behavior, retries and unsubscribe/preferences where
applicable. Mock all sends; do not contact employees from this coding
prompt. Test access/recipient changes, duplicate/retried sends, provider
failure and source resolution. Document sandbox/live activation and
cost limits; update the matrix/status.
```

## Strategy coverage map

| Requirement group | Stage(s) | Strategy section |
| --- | --- | --- |
| Corrections: ranking, validators, context, quotas, save/lifecycle | 1 | 3, 7, 12 |
| Secret owner action; accurate privacy labels/admin boundaries | 1-2, 9 | 3, 6, 7 |
| Team/company scope, assignment and revoked membership | 2; all | 6, 9 |
| Goals, direction, evidence, primary contribution, accepted output | 2, 11, 13 | 6, 9, 10 |
| Pre-rollout baseline, fair comparison, quality/uncertainty | 2, 13 | 10 |
| Navigation, tokens, accessibility, themes, shortcuts/mobile | 3; 4-10 | 4 |
| Unscheduled capture, notes, search/tags, provenance, sharing | 4 | 4, 9 |
| Workbench, saved views, bulk actions, focus and parking lot | 5 | 4 |
| Availability, blocks, future planning, effort and dependency DAG | 6 | 5, 9 |
| Now/Next/Waiting/Decisions, fairness, policy, stable focus | 7 | 4, 5 |
| Fresh snapshots, revalidation, change events and overrides | 7-9 | 5, 7 |
| Blocker resolver, handoff ACK, targeted alerts/digest/escalation | 8 | 4, 5 |
| Transactional outbox, jobs, retries, repair sweeps/health | 8; 9, 11 | 7-9 |
| Compact scoped AI, preflight, tools, proposals/apply/undo | 9 | 7 |
| AI feedback/history, retention, budgets, evals/outage | 9 | 7 |
| Goal/people board, dependencies, exceptions, uncertainty | 10 | 6 |
| What-if preview, displaced commitments, checked commit | 10 | 6 |
| Collections and all listed source adapters/permissions | 11 | 8 |
| Source lifecycle/deduplication, no conflicting authority | 11 | 8, 9 |
| Carry-over, weekly reviews, decisions, SOP/recurrence/ideas | 12 | 4, 7, 11 |
| Calendar, voice, offline and other unselected channels (conditional) | Extensions | 4, 5, 7 |
| Arrival, EOD email-first/WhatsApp-later and weekly AI money cap | 2, 6, 8-10, 12-13 | Owner addendum 2026-10-21 |
| Migration/backfill, flags, bounded queries, rollback/docs | All; 13 | 9, 11, 12 |
| Pilot E2E, deployment/runbook, training/operational monitoring | 13 | 10-12 |

## Release-planning prompt

```text
Prepare the reviewed Tasker release candidate for deployment planning.
Read the implementation status, requirement evidence and existing IMS
deployment guidance. Inspect available infrastructure without printing
secrets. Do not deploy or modify production from this prompt.

Produce exact environment-specific steps, not generic guessed commands:
backup and verified restore; staging rehearsal; dependencies; additive
migration/backfill order; static assets; web/worker/scheduler supervision;
feature flags; private storage and secret configuration; AI/model/budget
preflight; health checks; pilot cohort; monitors; rollback and source-data
reconciliation. Identify irreversible migrations and data loss risks.

List the exact target, release revision, expected downtime, operator,
backup/restore evidence, smoke tests and rollback triggers. Explain how
to disable features/stop new jobs before rollback and how persisted jobs
and new data remain compatible. Do not suggest git reset or migration
reversal as a substitute for a tested recovery plan.

Return a concrete checklist for the deployment owner. Live deployment,
real provider smoke tests and external notifications require their own
explicitly scoped authorization after this checklist is reviewed.
```

## Evidence and status

Every stage records requirements, changed behavior/contracts/files/migrations, exact checks/results, pending verification, private-data boundaries, flags, rollback and handoff. Status values: not started, in progress, implemented-verification-pending, verified. Do not move to release with unresolved mandatory gates.

## Starting validation limitations

The earlier Django system check passed; its 88-test suite could not start because isolated PostgreSQL was offline. Recheck availability. No paid provider connectivity, production enablement or productivity uplift has been verified.

## References

- [Project strategy](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/docs/TASKER_PRODUCT_STRATEGY_2026-10-08.md)
- [Official OpenAI worktree guidance](https://learn.chatgpt.com/docs/environments/git-worktrees)

The sequential workflow and stage gates are tailored recommendations. Worktree guidance does not certify this implementation or release readiness.
