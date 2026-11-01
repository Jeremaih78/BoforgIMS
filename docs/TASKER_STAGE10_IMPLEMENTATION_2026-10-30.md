# Stage 10: Management strategy board and scenarios

Implemented 29–30 October 2026 · Africa/Harare.

Stage 10 is implemented and verified locally. It adds a scoped management board, explicit employee work declarations and deterministic, reviewable planning scenarios. No production deployment, provider activation, email/WhatsApp send or financial/stock operation was performed.

## Delivered

- **Goals and people together.** Strategy shows readable company goals, a compact team overview and employee/team lanes. Employees see their own lane; managers see their currently managed teams; executive access follows explicit current company grants. Role permissions alone do not create organization-wide access.
- **Declared current and next work.** An employee confirms these fields explicitly, with a revision and immutable event. A running timer or recent activity is never used as a declaration. Changes to the source task, a different planning date, or a confirmation older than four hours mark the declaration for review. Reassignment removes an invalid former-owner declaration from the displayed current work; it does not invent the recipient's next task.
- **One factual board, three views.** Execution, Dependencies and Capacity reconcile to the same canonical tasks and readiness. The compact overview links to detail lanes; native disclosures reveal shared task cards. All graph-like relationships have ordinary text links and forms. There is no drag-only operation or employee ranking.
- **Honest capacity and status.** The board labels capacity as whole planning-day capacity, including aggregate commitments outside the visible board. Elapsed hours are not deducted from that board measure. Scenarios separately clip to future time. Private availability notes, unrelated task titles, captures, reflections and AI history are excluded. Arrival is labelled declared/not declared, and End day is submitted/not submitted or not shared with the viewer's role.
- **Actionable exceptions.** The drawer identifies stale confirmations, missing capacity, recorded overload, no remaining declared capacity, overdue shared responses, owners needing review and goals without linked work in the current view. Counts describe included lanes/tasks, not unseen work. Blocker age uses the recorded blocked transition or the request's raised time; missing history stays unknown.
- **Draft scenarios.** A manager proposes assignment, priority and deadline changes for at most ten distinct tasks. Preview records before/after fields, readiness, remaining effort, affected accepted blocks, workload/capacity effects and uncertainty. Preview/discard does not change live assignments, private plans, time blocks, operator state or notification jobs.
- **Explicit safe commit.** Commit rechecks live scope, membership, source versions/digests, expiry, running Focus, dependencies and feasibility. It applies the complete batch atomically through the existing task service, preserves unrelated note bytes and definition of done, audits the changes and queues targeted in-app updates. Replayed commit returns the existing receipt. A committed change is not undone by discarding a draft; reversing it requires a fresh, feasible reviewed change.

## Scenario limits and safety

The comparison covers **seven calendar days**, starting in each affected employee's configured business timezone. It uses readable shared workload plus all accepted commitments; other unplanned work may exist. This is not a completion-date forecast or a skill certification. Eligibility means active membership in the selected team.

Incoming effort is counted once, and existing reservations for that same task are deducted before comparing unreserved demand. The batch combines incoming and outgoing work. Recipient hours/effort must be known; existing conflicts, insufficient future capacity or an infeasible earlier deadline block reassignment. A running Focus timer must be stopped before transfer. Moving work away from an inactive/former member can proceed when the new recipient is eligible and feasible, without opening the former member's private capacity.

Metadata-only changes may retain an explicitly unknown completion forecast; they do not reserve additional time. An earlier deadline cannot silently contradict an existing accepted block. Moving ownership explicitly cancels that task's accepted blocks through the established lifecycle service; the new owner must plan new blocks. Private daily intentions are not opened or edited.

Drafts expire after 15 minutes. Source changes require a fresh preview. Discard remains owner-only and available after the feature flag or management permission is removed; unavailable content is not exposed through that closure path.

To prevent a concurrent manual task creation/transfer from changing the workload during the final check, a short PostgreSQL **EXCLUSIVE NOWAIT** Task-table fence is acquired after the actor lock and before Task/scope/policy locks. Ordinary reads continue. A busy writer causes a reviewable conflict with the draft retained. This deliberately coarse pilot fence serializes Task writes only during the bounded commit; large-team/concurrent production latency must be measured before wider rollout. No network request runs under it.

Notifications are queued for eligible affected owners through the existing Stage 8 delivery pipeline. The former owner receives only a generic update when current task details are no longer readable. No global audience is added; quiet hours, Focus suppression and current-source authorization still apply. Queueing does not establish delivery.

## Verification

- **987 Tasker/configuration tests passed in 954.097 seconds** on disposable PostgreSQL at `127.0.0.1:55441`, using a freshly created test database. Log: `output/stage10-full-tests.log`.
- **22 final UI/strict production-static tests passed in 38.808 seconds** after the compact overview changes. Log: `output/stage10-final-ui-static-tests.log`.
- **77 existing JavaScript tests passed**, zero failures. Log: `output/stage10-node-tests.log`. System checks and migration drift checks pass.
- The board suite includes scope/forged-link/revocation/privacy tests, a real concurrent first-confirmation race, source-version and declaration checks, arrival/report authority, unavailable prerequisite sanitization, overload/exception facts, 24-lane pagination and more than 100 tasks with true omission counts.
- Scenario tests include read-only preview/discard, combined capacity and same-task reservation accounting, private backlog exclusion, expired/stale/unauthorized commits, untouched note bytes, active Focus and deadline guards, correctly targeted attention, concurrent commit replay and busy-writer refusal with no partial changes.
- Browser walkthrough on a separate synthetic hostname/account demonstrated a previewed transfer from Asha to Ben, the disclosed old time block, explicit commit, and the manager board reflecting the new ownership without inventing Ben's declaration. A later employee login showed only Ben's lane; Ben then explicitly confirmed current and next work.
- At 390px, dependency/capacity views, keyboard exception disclosure, adding a typed scenario row, preview and keyboard discard worked. The page's client/scroll widths both measured **375px** with a vertical scrollbar, with no page-level horizontal overflow. The compact table has its own labelled keyboard-focusable horizontal scroller. No browser console errors were observed in the checked tab.
- Local read benchmark: four lanes/four tasks, **87 SQL queries**, samples **3816.25 / 504.62 / 565.00 ms**. All samples are recorded, including startup/cache overhead; no production latency certification is implied. Evidence: `output/stage10-performance.json`. Queries and populations are bounded; representative large-team benchmarking remains a rollout gate.

Screenshots: `output/stage10-scenario-preview.png`, `output/stage10-mobile-discarded.png`, `output/stage10-mobile-confirmed.png`, `output/stage10-mobile-exceptions.png` and `output/stage10-strategy-board.png`. Synthetic data is clearly labelled and is not company performance evidence.

## Rollout and rollback

1. Review and deploy the complete pending Stage 9/10 change set only after a verified production backup. Stage 10 adds **migration 0023**, following Stage 9's 0021–0022. Do not run unrelated financial corrections or reverse the deployed Tasker history.
2. Apply the reviewed additive Tasker migrations and run `setup_tasker_roles`. Existing employee scope is preserved; group capabilities never substitute for actual team membership/company grants. No real roster, hours, declarations, accepted blocks or scenario drafts are invented by migration 0023.
3. Run strict `collectstatic` and verify nginx can read the generated public assets. Preserve private configuration/backup permissions; do not repeat the earlier public-static 0700 permission issue.
4. `TASKER_STRATEGY_ENABLED` defaults **false**. Enable it only for the reviewed rollout. With the flag on, the established Strategy route opens the new board and employees gain a self-only Strategy lane. Scenarios remain limited to authorized managers. No AI key is required for factual board/scenario behavior.
5. Keep real arrivals, work hours and outcome definitions accurate, and authorize the production worker separately for actual notification delivery. Stage 9 AI activation/pricing/quality gates remain pending. Do not treat this board as attendance, payroll or a measured productivity score.
6. To stop new Stage 10 actions, set the feature flag false. Existing routes fall back to the prior Strategy overview, and ordinary Tasker work remains available. Retain additive tables, audit events and committed task changes. Discard closes a draft only; use a fresh reviewed scenario or the normal task workflow for a deliberate reversal.

Stage 11 (IMS source adapters, starting with collections) is the next implementation stage. The 70% productivity improvement remains a Stage 13 pilot target, not an established result.
