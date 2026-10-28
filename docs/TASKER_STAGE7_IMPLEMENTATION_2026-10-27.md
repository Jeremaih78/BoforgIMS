# Stage 7 — Continuous next-action operator

27 October 2026, Africa/Harare. User authorization: “implement next stage”, followed by “continue”. Scope: Stage 7 of the agreed playbook, preserving Stages 1–6. Local software verification is recorded below; deployment and employee productivity evidence are separate.

## Delivered behavior

Now has one dominant recommendation and separate **Next**, **Waiting** and **Decisions** queues. It shows the canonical task, first incomplete checklist action (or task title), actual definition of done, remaining-effort label, proposed work slice and reasons. Missing definitions are labelled and linked for clarification. Daily counts, deadlines, intentions and legacy scratchpad remain available in a secondary expandable overview. The dedicated `/operator/` view uses the same service without the scratchpad.

**Start** enters the existing Focus lifecycle and single timer. **Choose another** requires a short reason and records it without changing priority or policy. **Blocked** requires a shared task reason, closes that task's timer through the existing lifecycle, retains it in Waiting and returns to fresh guidance. Completion/blocking wrap-up now uses this same operator. `services/next_actions.py` is a compatibility adapter, not another ranking engine.

Blocked/dependency-waiting work has resolver links; unknown effort has an estimation link; unknown hours, unavailable time and overcapacity have planning links. Restricted assignments and inaccessible prerequisites use generic guidance. No original capture, private availability note, scratchpad or reflection is copied into recommendation evidence or invalidation reasons.

## Version 1 policy

Hard gates run before ranking: fresh active account/capability; owned live unarchived active task; current company/team membership and source access; dependency readiness; explicitly configured local work hours and buffer; no leave/fixed-commitment conflict; known positive remaining effort for a new start; and a fitting work slice within the current contiguous window and available declared capacity.

- Critical tasks lead feasible work. Blocked critical work stays in Waiting.
- A slice is at most 25 minutes, or the smaller known remaining estimate. A long task can therefore progress without requiring its entire remaining estimate to fit today.
- An accepted current block can supply its own reservation. Other accepted blocks, including later reservations, truncate the available contiguous window. Overloaded plans require a decision first. Starting Focus does not automatically accept a time block.
- The Stage 6 buffer is an aggregate daily allowance. No positioned break, worked-hour denominator or attendance deduction is invented. Declared capacity does not infer unknown past unplanned work; current clock windows still limit execution.
- A healthy active Focus remains Now during routine refresh. Unknown remaining effort after time is recorded adds an estimate-review decision without silently stopping it. New critical work becomes an explicit urgent-review decision at a safe pause. Version 1 never interrupts a timer automatically; the employee explicitly stops/finishes it before switching. If access/readiness/hours/capacity changes make it unhealthy, guidance requests review and offers no second Start.
- Critical/noncritical grouping is fixed. Within each group, goal-linked or long work open at least seven days gets a protected lane; then weighted factors and stable creation/id ties apply. This prevents repeated fresh small tasks from permanently hiding older strategic work. The employee can explicitly choose a different feasible task with a reason.

Default integer weights (editable for the employee, 0–100; age minimum 1): goal connection 60, deadline risk 20, agreed priority 10, work unlocked 15, age 5, effort fit 3, context continuity 10. Each save uses an optimistic policy revision and immutable before/after audit. The UI explains the fixed critical/fairness rules.

Goal connection is a visible active-goal link, **not measured business impact**. Missing goal/deadline/effort data remains unknown. Deadline risk uses local calendar-day buckets; same-day due-time precision is retained in task state/display but is not a separate scoring factor in version 1. Unlock value counts scoped active direct dependents; other prerequisites may still prevent their immediate execution. Context uses the recent task category. A bounded small-task fit bonus cannot override the protected lane.

Candidate review is capped at 100: up to 40 oldest nonblocked critical assignments before blocked critical work, 20 oldest strategic assignments, 20 oldest other work, 20 priority/deadline assignments, then fill unused places. Current Focus is included first. Totals and omissions are disclosed; the short queues display leading items (up to 6 Next, 10 Waiting and 12 Decisions), with Work for the remainder. This is a bounded review, not a claim that every omitted task was examined.

## Evidence, invalidation and execution

Additive migration `0017_operatorpolicy_operatorsnapshot_operatoroverride_and_more` creates `OperatorPolicy`, `OperatorState`, `OperatorSnapshot` and `OperatorOverride`. It backfills no employee policy or recommendation and changes no existing tasks. Only the isolated preview database was migrated during this work.

Snapshots store owner, policy/version/weights, candidate IDs, queue/reasons/score, opaque task/dependency/effort/first-action proofs, exclusion counts, source digest, selected task and a five-minute expiry. They retain no task title, description, checklist text, definition text or note body. Snapshots/choices are immutable through their model save API and read-only admin; staff inspection is owner-scoped, with the existing privileged superuser operational exception. Owner retention policy remains a rollout decision.

`operator_events.invalidate_operator(user_ids=None, reason=..., task_id=None)` is the single synchronous local invalidation interface for Stage 8 dispatch integration. It changes existing state revisions only; it creates no provider request, timer, outbox, notification or second queue. Reasons are allowlisted generic codes. Normal completion/blocking/assignment/deadline/priority, dependency, goal, plan/review, schedule/availability/effort, checklist/focus and permission/membership mutations are covered. Transitive/global changes conservatively invalidate existing owners; routine refresh still preserves healthy Focus. This conservatism can require additional reviews in a busy rollout and should be measured in the pilot.

Recomputation occurs when Now/refresh/wrap-up is requested and again before a reviewed action. Five-minute review expiry is checked on the server and shown in the UI. There is no background scheduler or live push in Stage 7; reliable dispatch and scheduled delivery belong to Stage 8. Raw maintenance `QuerySet.update/bulk_update` and recoverable queryset delete/restore bypass model signals: callers must invoke the documented invalidation seam. Execution also compares actual source proofs, so a missed signal cannot grant stale authority.

Start/choice/block checks snapshot ownership, current expiry, current task owner/access, stored queue, event revision, fresh source digest and task/dependency/effort proofs. It rechecks expiry after potentially waiting on row/graph locks. A moving source revision produces an immediately expired review with a generic refresh decision. A stale request returns a retained, escaped reason and no mutation. Another running timer cannot be replaced implicitly.

Actions serialize actor → Task → scope → WorkPolicy, then existing Focus operations. Child changes that can precede Task writes invalidate the same state set first, avoiding scoped-to-global state-lock upgrades. End-day review now rechecks the current actor/review capability and canonical Plan, locks Plan → sorted old/new Tasks → scope before saving the review or carry-over flags, and cannot mutate a former employee owner's task. Carry-over remains an owned review intention; it does not require general Task editing permission.

Existing explicit manual Focus starts remain user-controlled and retain their lifecycle/readiness checks. The new recommendation routes enforce the additional hours/capacity/proof contract. This stage does not change every manual timer into an enforced work-hours system.

## Verification

Final full regression: **638 Python tests passed in 215.906 seconds** on isolated PostgreSQL 55441. Earlier full checkpoint: 630 tests passed in 218.147 seconds. Final focused integration: **100 tests passed in 30.700 seconds**, including real PostgreSQL concurrency. **77 Node tests pass**; no new JavaScript controller was needed. System check passes; migration drift check reports no changes.

Evidence logs are under `.local-review/tasker-stage1-20261021/`: `stage7-complete-final.log`, `stage7-final-integration.log`, `stage7-capture-journey.log` and `stage7-javascript.log`. One new exhausted-quota fixture initially violated the existing total-token integrity constraint; it was corrected to include its input token, and the isolated capture-to-Focus journey passed. The fixture did not weaken the constraint or invoke a provider.

Covered regressions include empty/all-blocked work; unknown hours/effort; leave/outside hours/overload; future overlapping reservations; current reservations; undated critical work beyond 100 assignments and among 105 blocked critical tasks; seven-day strategic/long fairness; focus stability/urgent review; same-clock raw task/checklist edits; moving sources; expiry during revalidation; reassignment/terminal/restricted sources; current permission revocation; missing DST midnight; bounded query growth; private evidence; policy conflicts; reasoned overrides; capture-to-Focus with AI disabled and an exhausted token quota. The weekly monetary AI policy remains Stage 9.

Real PostgreSQL races verify one timer/one audit for two Start requests, independent employees logging time, and end-day review competing with a Task writer. No operational database was used. Existing mocked AI/accounting error logs are expected assertions in passing tests, not live provider calls.

Browser walkthrough in the isolated preview verified:

1. Canonical first action, definition, effort, critical-first reasons and Start.
2. Actual Start → existing Focus; routine refresh retained it and exposed an estimate-review decision.
3. Checklist/outcome acceptance → completion → fresh next recommendation.
4. Reasoned alternative selection → Focus; fresh operator Blocked → stopped timer, retained Waiting item and new guidance.
5. A stale second-tab block was refused while retaining its escaped draft reason.
6. Keyboard policy editing/saving feedback; sample weights restored to defaults.
7. 1440px desktop, 390px and 320px layouts; observed client/body widths 1425/1425, 375/375 and 305/305. These checks do not certify native zoom, OS motion, screen readers or employee usability timings.

Artifacts: `output/tasker-stage7/operator-mobile-390.png`, `ranking-mobile-320.png`, `stale-advice-retained-mobile-390.png` and `operator-desktop-1440.png`. The final operator tab reported no browser console errors; the temporary viewport override was reset. Samples are clearly labelled and belong to the existing synthetic review account. Original user tabs and private scratchpad/idea drafts are preserved.

## Rollout and recovery

Before a named pilot, use a backup and staging database, apply migrations through 0017, deploy matching Python/templates/CSS, configure real employee work hours/buffers and remaining estimates deliberately, and verify actual memberships/goals. Exercise Start/complete/block/override and permission revocation with pilot accounts. Decide recommendation/choice retention and measure excessive refreshes, acceptance and employee usability. Tests do not establish a 70% productivity uplift.

For application rollback, restore the pre-Stage-7 code/templates/assets checkpoint together and keep additive 0017 tables for evidence; no operator worker needs stopping. Existing tasks, timers, captures and planning records remain their original domains. Do not unapply 0017 or delete snapshot/choice history as a routine rollback. If a later schema rollback is required, back up/export evidence and plan it explicitly with the database owner. No destructive rollback was executed here.

AI activation, private replacement-key authentication and the dotenv warning remain user-deferred. No secret file or excluded credential README was read or changed; no paid API, email/WhatsApp send, deployment, staging, Git commit or push occurred. Stage 8 is the next implementation stage, only on the user's next instruction.
