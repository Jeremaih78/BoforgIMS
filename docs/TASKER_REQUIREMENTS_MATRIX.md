# Tasker requirements matrix

Prepared 21 October 2026. Sources: [strategy](TASKER_PRODUCT_STRATEGY_2026-10-08.md), [playbook](TASKER_IMPLEMENTATION_PLAYBOOK_2026-10-08.md) and [owner business addendum](TASKER_BUSINESS_REQUIREMENTS_2026-10-21.md). `S3` means strategy section 3; `P4` means playbook Stage 4; `BIZ-*` refers to the addendum. The addendum supersedes earlier proposed defaults where the owner has made a decision.

Stage 1 update, 26 October 2026: implementation and local verification are recorded in [the evidence report](TASKER_STAGE1_IMPLEMENTATION_2026-10-22.md). T01-02 through T01-16 have passing local evidence. T01-01 now has owner-confirmed revocation, authorized working-document cleanup (1 token removed; follow-up 0), fixed-path local-history assessment (2 revisions, 1 unique blob, 0 key-pattern blobs) and 28 synthetic credential-tool tests. Stage 1 is verified for this local scope; replacement configuration/authentication and remote-copy certification are separate. See [key recovery](TASKER_API_KEY_RECOVERY.md).

Stage 2 update, 22 October 2026: the owner authorized local implementation while deferring credential cleanup. See [Stage 2 evidence](TASKER_STAGE2_IMPLEMENTATION_2026-10-22.md). Scoped teams, goals, accepted outcomes and baseline software are locally verified; actual roster/metric/pilot configuration and baseline adoption remain pending. Stages 3-13 remain open.

Stage 3 update, 22 October 2026: the focused shell, basic authorized Strategy overview, interface components and theme/shortcut preferences are implemented. See [Stage 3 evidence](TASKER_STAGE3_IMPLEMENTATION_2026-10-22.md). The full isolated suite passed 336 Tasker/configuration tests and 26 Node tests; desktop/mobile, keyboard, theme/remapping and validation walkthroughs passed. Overall Stage 3 remains implemented-verification-pending for native 200% zoom, OS reduced-motion and screen-reader walkthroughs plus real employee usability observations. The 320px stress and keyboard mobile-filter checks passed, as did disabling/resetting shortcuts; no browser console errors were observed. Inbox/arrival remain hidden until Stage 4/6; no Stage 10 strategy board or productivity uplift is claimed. The credential action remains deferred, and AI stays disabled.

Statuses describe upgrade acceptance, not whether an older foundation exists. `Not started` does not erase existing functionality; it means this requirement has no verified upgrade evidence. Use `in progress`, `implemented-verification-pending` and `verified` only with evidence in [status](TASKER_IMPLEMENTATION_STATUS.md). An owner action remains pending until its owner provides evidence. Never copy credentials into evidence.

Stage 4 update, 27 October 2026: the non-AI Capture Inbox core is implemented and verified locally. The full isolated PostgreSQL suite passed **401 tests in 166.027 seconds** and **47 Node tests passed** (20 capture, 17 preferences, 10 Focus). Browser evidence covers two-tab conflicts with retained drafts and explicit resolution, private capture promoted into team work, audience-change protection and 390px capture/pin/archive/Trash/restore. The 320px light and 1440px dark walkthroughs also passed without horizontal overflow; screenshots were saved and the system theme/default viewport restored. See [Stage 4 evidence](TASKER_STAGE4_IMPLEMENTATION_2026-10-27.md). T04-04 has an unavailable optional proposal seam only; actual AI extraction/preview remains Stage 9 work. API authentication/dotenv correction are user-deferred, AI is disabled, and native accessibility, live inference and pilot productivity are not certified.

Stage 5 update, 27 October 2026: compact Work list/Board, private saved views/defaults, scoped filters, audited all-item bulk triage, detail drawer, explicit definition-of-done acceptance and direct Focus are implemented. Focus adds private idea parking, optional advisory intervals and a bounded owned-task chooser after completion/blocking. **469 Python and 77 Node tests pass**; desktop/mobile workflow and keyboard evidence is in [Stage 5](TASKER_STAGE5_IMPLEMENTATION_2026-10-27.md). Full capacity/dependency-aware operator behavior remains Stage 7; AI setup remains user-deferred. At that checkpoint, Stages 6–13 had not started.

The Stage column identifies the primary delivery stage; follow-on stages are shown where behavior spans slices. Keep the IDs stable. Add evidence links and actual results as implementation proceeds. Conditional extensions must retain an explicit selected/deferred state instead of disappearing.

Stage 6 update, 27 October 2026: explicit local work policies/exclusions, union-based capacity, versioned draft/preview/accepted time blocks, remaining effort, scoped dependency readiness and an arrival/correction ledger are implemented and locally verified. **561 Python tests and 77 Node tests pass**; real PostgreSQL races and browser acceptance/correction/readiness evidence are recorded in [Stage 6](TASKER_STAGE6_IMPLEMENTATION_2026-10-27.md). Actual working policies, correction/retention rules and rollout evidence remain owner decisions. At that checkpoint Stage 7–13 work had not started.

Stage 7 update, 27 October 2026: deterministic Now/Next/Waiting/Decisions, hard feasibility gates, critical-first and protected strategic/long progress, stable Focus, versioned private snapshots, fresh actions and synchronous invalidation are implemented. **638 Python tests and 77 Node tests pass**; PostgreSQL races and desktop/mobile/keyboard workflows are recorded in [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). AI activation remains user-deferred; no Stage 8 job/notification pipeline or productivity uplift is claimed.

Stage 8 update, 27 October 2026: structured requests, quiet/focus/digest notifications, one PostgreSQL transactional outbox with retries/repair/health, and factual End day/team-report versions with a test-only email adapter are implemented. **751 Python tests and 77 Node tests pass**; local verification is recorded in [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md); real reporting policy, recipients/sender, evidence retention and production supervision remain separate. AI and real external delivery stay disabled.

## Stage 1: Correctness, reliability and security

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T01-01 | S3 | 1; owner | Revoke/rotate the exposed credential and remove it safely from documentation; assess tracked history and add secret scanning. | Owner confirms revocation/removal; sanitized scanner evidence; no secret copied into reports. Excluded README remained untouched during preparation. | Verified within recorded scope 26 October: owner-confirmed revocation, targeted cleanup and local path-history assessment; 28 synthetic tool tests pass. No remote-copy or replacement-authentication claim |
| T01-02 | S3 | 1 | Use semantic priority consistently instead of sorting priority strings. | Critical/high/medium/low fixtures agree across selectors, dashboard and AI candidates. | Verified locally; see Stage 1 report |
| T01-03 | S3 | 1 | Exclude blocked/terminal tasks from executable recommendations. | All-blocked and terminal-work tests route waiting/unblock work without invalid Now actions. | Verified locally; see Stage 1 report |
| T01-04 | S3, P1 | 1 | Account for undated critical work, current focus and top commitments beyond candidate/context caps. | More-than-75-task and undated-important fixtures retain required candidates or disclose exclusions. | Verified locally; see Stage 1 report |
| T01-05 | S3 | 1 | Compact/deduplicate context, use true totals and disclose included/omitted counts under an aggregate input budget. | Repeated descriptions are not copied into multiple arrays; bounded payload and actual totals tested. | Verified locally; see Stage 1 report |
| T01-06 | S3, P1 | 1 | Validate all six existing AI result contracts locally for shape, types and malformed items. | Invalid/refused/incomplete outputs fail safely for every feature; no uncaught item AttributeError. | Verified locally; see Stage 1 report |
| T01-07 | S3 | 1 | Validate authorized IDs, positive durations, uniqueness and lifecycle semantics. | Negative/duplicate durations, unknown unfinished IDs and blocked next-task IDs are rejected. | Verified locally; see Stage 1 report |
| T01-08 | S3 | 1 | Recheck authority, state and revision when applying generated checklist steps. | Completed/archived/reassigned/revoked/stale/replayed apply tests; canonical titles from records. | Verified locally; see Stage 1 report |
| T01-09 | S3 | 1 | Correct daily request quota off-by-one behavior. | Limit one allows its first eligible request and rejects the next. | Verified locally; see Stage 1 report |
| T01-10 | S3, P1 | 1 | Reserve request/token capacity atomically and reconcile in-flight work. | PostgreSQL boundary/concurrency/failure tests prevent double spending and leaked reservations. | Verified locally; see Stage 1 report |
| T01-11 | S3 | 1 | Preserve invalid quick-add data, reopen capture and display associated errors. | Invalid submission can be corrected without retyping; keyboard error focus verified. | Verified locally; see Stage 1 report |
| T01-12 | S3 | 1 | Serialize/version focus-note saves and protect all navigation/mutation paths. | Rapid edits, concurrent save, checklist navigation, retry and draft-recovery checks lose no text. | Verified locally; see Stage 1 report |
| T01-13 | P1 | 1 | Exclude soft-deleted checklist records from progress aggregates. | Delete/restore and progress-count regressions. | Verified locally; see Stage 1 report |
| T01-14 | S3 | 1; 5 | One documented checklist/progress/accepted-completion policy with consistent timestamps/timer cleanup. | Manual/checklist complete/reopen flows agree with policy and preserve audit history. | Verified locally; see Stage 1 report |
| T01-15 | S3 | 1; 2; 9 | Correct misleading privacy labels and review staff access to AI content/reflections. | Shared notes labeled honestly; admin access/data-transmission review and scoped regression evidence. | Verified locally; see Stage 1 report |
| T01-16 | S2, S12 | 1 | Update safe Tasker documentation to match existing and changed features. | Documentation review without reading/diffing the excluded credential file. | Verified locally; see Stage 1 report |

## Stage 2: Teams, goals and baseline

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T02-01 | S6, S9 | 2 | Reuse existing Company identity and add Team/TeamMembership separately from permission groups. | Explicit legacy mapping, multi-team/company boundaries and no competing company authority. | Verified locally; Stage 2 report |
| T02-02 | S6, BIZ-T01 | 2; 5; 10 | Employees, team managers and executives have explicit scoped visibility. | Cross-team/forged-ID/revoked-membership and admin/endpoint tests. | Verified locally; Stage 2 report |
| T02-03 | BIZ-T01, P2 | 2; 5 | Managers can delegate authorized work to actual members of their teams. | Team-only assignment choices, server-side recipient checks and audited reassignment tests. | Verified locally; Stage 2 report |
| T02-04 | S6 | 2 | Link company goal, measurable result, initiative, task and accepted outcome. | Canonical drilldown with owner, unit, baseline, target, direction, horizon and evidence. | Verified locally; Stage 2 report |
| T02-05 | S6, P2 | 2 | Goal progress supports unknown baselines and lower-is-better measures. | Unknown values remain unknown; goal progress is not an average of arbitrary task percentages. | Verified locally; Stage 2 report |
| T02-06 | S6, P2 | 2; 11 | Use primary contribution, explicit acceptance and provenance; deduplicate/retract outcomes. | Replay/reopen/retraction and no-double-attribution tests. | Verified locally; Stage 2 report |
| T02-07 | S10 | 2 | Instrument pre-rollout accepted output, role/workload, staffed-hour denominator and quality. | Usable baseline collection/export with defined sources, completeness and acceptance. | Software verified locally; actual baseline adoption pending |
| T02-08 | S10 | 2; 13 | Separate business productivity from task completion, clicks and focus/arrival time. | Metric definitions and tests cannot inflate accepted output by splitting tasks/timers. | Verified locally; Stage 2 report |
| T02-09 | P2 | 2 | Provide usable basic goal/team management. | Authorized owner can configure/edit actual teams and goal drafts without inventing missing data. | Verified locally; Stage 2 report |
| BIZ-G01 | Addendum | 2; 11; 13 | Record the confirmed USD 2m total-sales objective over the next two years. | Agreed sales recognition/returns/tax policy, baseline, source evidence, owner and horizon; do not substitute profit, cash or annual run rate. | Draft/target implemented; owner/evidence/baseline/horizon pending |
| BIZ-G02 | Addendum | 2; 11; 13 | Record printing equipment/consumables leadership by 2030 across the stated categories. | Geography, largest-supplier metric, comparator, baseline, deadline and owner agreed. | Direction draft implemented; measurable definition pending |
| BIZ-G03 | Addendum | 2; 9; 13 | Record African-business AI/automation/SaaS/agents bridge leadership within 36 months. | Measurable customer/business outcomes, market, target, baseline, horizon and owner agreed. | Direction draft implemented; measurable definition pending |

## Stage 3: Premium navigation and interface

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T03-01 | S4 | 3 | Focused Now, Inbox, Plan, Work, Goals shell; Strategy for authorized managers. | Every visible destination works and direct access is authorized; unfinished destinations handled honestly. | Verified locally; Stage 4 enables Inbox and Stage 6 provides arrival; Strategy is a scoped basic overview |
| T03-02 | S4, P3 | 3 | Keep IMS switching secondary and preserve shared authentication/routes. | Representative IMS and existing-route regressions. | Verified locally; relative Tasker routes, secondary IMS switch and asset-isolation regressions pass |
| T03-03 | S4 | 3 | Consistent design tokens, hierarchy and reusable form/button/drawer/state components. | Representative screens and component guidance reviewed. | Implemented and locally reviewed; see Stage 3 report and component guidance |
| T03-04 | S4 | 3 | Theme preference and reduced-motion support. | Reload persistence, contrast and motion-preference walkthroughs. | Theme persistence verified locally; OS reduced-motion walkthrough pending |
| T03-05 | S4 | 3; all UI | Keyboard operation, labels, readable contrast, visible focus and touch-friendly controls. | Keyboard/screen-reader and button alternatives to every drag action. | Keyboard/local interface checks pass; screen-reader walkthrough pending |
| T03-06 | S4, P3 | 3 | Preserve useful shortcuts and expose shortcut settings. | Editable fields and preference/remapping/disable behavior tested. | Verified locally; preferences validation, remapping, editable-field suppression and Focus shortcut checks pass |
| T03-07 | S4, P3 | 3; all UI | Responsive mobile/desktop/zoom behavior. | Recorded desktop and 390px walkthrough, with narrow/zoom stress checks. | 1440px/390px/320px and keyboard mobile-filter checks pass; native 200% zoom pending |
| T03-08 | S4 | 3; all UI | Clear empty/loading/validation/pending/saved/error/conflict/undo states. | Failure/retry and persistence walkthroughs, not success screenshots alone. | Implemented/local validation and retained-error-state checks pass; no unsupported undo promised |
| T03-09 | S4 | 13 | Validate capture <=10 seconds, Start in one action, planning <=90 seconds and routine admin <5 minutes/day. | Actual employee usability observations; label targets unmeasured beforehand. | Unmeasured; actual employee usability observations remain Stage 13 work |

## Stage 4: Capture and durable notes

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T04-01 | S4 | 4 | Universal capture accepts task, idea, note and link. | Each type captured globally and on mobile. | Verified locally; global/mobile private Inbox supports all four types; Stage 4 report |
| T04-02 | S4 | 4 | Default to private unscheduled Inbox; scheduling/sharing is explicit. | Capturing an idea creates no artificial due-today commitment. | Verified locally; capture creates no task/date/plan; promotion date and audience require explicit selection |
| T04-03 | S4 | 4 | Persist original text independently of AI. | Provider-disabled/outage and network-retry tests preserve capture without duplicates. | Verified locally; immutable original, provider-independent creation, idempotent client keys and uncertain-save retries |
| T04-04 | S4 | 4; 9 | Preview AI-proposed type/title/action/goal/date, including ambiguous interpreted dates. | Rejected/ambiguous proposals cannot silently schedule or share. | Stage 4 optional proposal interface implemented as unavailable; actual AI extraction/preview pending Stage 9. No automatic interpreted date commitment |
| T04-05 | S4 | 4 | Independent searchable notes/ideas with tags, pinning and archive. | Records survive date/navigation changes and scoped search/filter tests. | Verified locally; scoped search/tags/pin/archive and Trash/restore pass tests and local walkthroughs |
| T04-06 | S4, S9 | 4 | Versioned autosave, retries/conflicts and recoverable deletion. | Competing tabs, offline failure, delete/restore and no false saved state. | Verified locally; serialized saves/unknown retry/409 retained draft and explicit resolution, audience-change checks, manual no-JS Save and recoverable deletion. Network failure tested; no offline extension claimed |
| T04-07 | S4 | 4 | Deliberate sharing and provenance-preserving idempotent promotion. | Repeated promotion creates one task/link; private original remains protected. | Verified locally; explicit team sharing, one Task/Initiative promotion with current authorization and provenance; private original retained, source access follows visibility, inaccessible admin FK titles excluded |
| T04-08 | P4 | 4 | Explicit legacy scratchpad/history migration and access policy. | Legacy-note migration/retrieval with no data loss or expanded access. | Verified locally; on-demand owner-only unique DailyPlan import, original scratchpad retained, no automatic backfill or duplicated import |

## Stage 5: Workbench and focus

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T05-01 | S4, P5 | 5 | Compact list/board, saved views and scoped filtering. | Mine/Waiting/Due soon/Unscheduled views persist and return authorized results. | Verified locally; owner-local saved/default views, 24-item pages and bounded queries. |
| T05-02 | S4, P5 | 5 | Inline actions, bulk triage and task detail drawer. | Reduced page-hop walkthrough; mixed-owner bulk permission/failure tests. | Verified locally; atomic scope/version checks, safe drawer and mobile bulk walkthrough. |
| T05-03 | S4 | 5 | Show definition of done, goal contribution, source record and next step. | Canonical links and accepted-completion workflow. | Verified locally; independent provenance scopes, deliberate/current acceptance on manual, progress, Work and Focus paths. |
| T05-04 | S4, P5 | 5 | Direct Start reuses Focus Mode and one running timer per employee. | Concurrent-start, pause/resume and consistent lifecycle tests. | Verified locally; real PostgreSQL simultaneous-start test and browser pause/resume/complete/block. |
| T05-05 | S4, P5 | 5 | Focus presents current checklist/context/interval without losing working notes. | Navigation/save regressions and representative work walkthrough. | Verified locally; advisory intervals, retained notes across checklist/actions and failure-path JS tests. |
| T05-06 | S4 | 5 | Private distraction parking lot inside focus. | Capture remains private and focus state survives. | Verified locally; actor-owned idempotent Capture Inbox saves, draft retention and unchanged running timer. |
| T05-07 | S4 | 5; 7 | Completion offers next useful work; block offers an executable alternative. | Completion/block transitions retain the waiting record and work context. | Stage 5 fallback and Stage 7 feasibility-aware continuous operator verified locally; see [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). |

## Stage 6: Feasible planning and arrival

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T06-01 | S5 | 6 | Editable work hours/timezone, leave, fixed commitments and explicit interruption buffer. | Configurable local availability and date-boundary tests. | Verified locally; explicit self-configured policy, private exclusion notes, overnight/DST/date-boundary handling. |
| T06-02 | S5, P6 | 6 | Compute capacity without double-subtracting overlapping intervals. | Deterministic overlap examples and documented buffer semantics. | Verified locally; interval union/clipping, fixed daily buffer and 480−60−60−60=300 example. |
| T06-03 | S5 | 6 | Separate deadlines from planned work blocks. | Moving a block preserves deadline unless explicitly changed. | Verified locally; distinct ScheduleBlock fields and browser deadline-preservation walkthrough. |
| T06-04 | S5 | 6 | Plan tomorrow/future dates and review previous dates. | Cross-date/timezone navigation and persistence. | Verified locally; dated intentions/time blocks, consistent local work date and historical read-only review. |
| T06-05 | S5 | 6 | Reveal overload/conflicts before acceptance; defer/delegate/split or explicitly authorized override. | Overcapacity and concurrent-plan acceptance tests with audit. | Verified locally; preview, hard/soft constraints, reasoned override, stale proofs, PostgreSQL competing acceptance and existing-conflict warnings. |
| T06-06 | S5 | 6; 7 | Unknown estimates/capacity stay unknown; remaining effort and overrun re-estimation are explicit. | Missing estimates are not zero; logged time subtraction is not the sole estimate rule. | Stages 6-7 verified locally; explicit remaining anchors/overrun states, no automatic acceptance and feasibility-aware ranking; see [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). |
| T06-07 | S5, P6 | 6 | Scoped acyclic dependencies distinct from parent hierarchy. | Chain/cycle/inaccessible prerequisite, complete/reopen/delete readiness tests. | Verified locally; serialized DAG, bounded live/transitive readiness, hidden-link privacy, Focus gate and scope-change guard. |
| BIZ-W01 | Addendum | 2; 3; 6; 10 | Authenticated employee explicitly registers arrival, with date/timezone and attributable timestamp. | Self-only action, replay/double-submit behavior, authorized team display and audited corrections. | Stage 6 locally verified: self-only immutable original, reasoned correction ledger, current/team-snapshot scope and real concurrency tests. Production correction policy remains owner decision; Stage 10 board later. |
| BIZ-W01-P | Addendum | 6; 10; 13 | Arrival is a declaration separate from login, focus, physical presence, payroll and productivity. | Missing/corrected declarations stay honest; no automatic staffed-hour or productivity inference. | Stage 6 locally verified; explicit UI wording, missing records and preserved correction history; no capacity/staffed-hour inference. Pilot/management evidence later. |

## Stage 7: Continuous next-action operator

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T07-01 | S4, S5 | 7 | Separate Now/Next/Waiting/Decisions queues. | Blocked work never executable Now; resolver actions are visible. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-02 | S5 | 7 | Enforce authorization, lifecycle, readiness, capability, hours and capacity before ranking. | Explicit exclusion scenario for each hard constraint. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-03 | S5 | 7 | Configurable goal impact, deadline risk, unlocked work, urgency, age, remaining effort and context-switch ranking. | Versioned policy and deterministic ranking cases. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-04 | S5 | 7 | Include important undated work and protect long strategic work from starvation. | Repeated short-task/strategic/undated-critical scenarios. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-05 | S5 | 7 | Preserve healthy focus through routine recomputation; define urgent exceptions. | Routine events do not repeatedly reorder focus; approved urgent case behaves as documented. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-06 | S4 | 7 | Explain canonical task, first action, done condition and effort with Start/Choose another/Blocked. | Source-grounded actionable Now card. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-07 | S5 | 7; 9 | Snapshot policy/candidates/reasons/exclusions/freshness and expire stale advice. | Revoked/reassigned/changed task cannot execute from stale snapshot. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-08 | S5 | 7; 8 | Recompute on completion, dependency/blocker, approved urgent assignment, plans/availability and review points. | Event-to-invalidation and recomputation tests without duplicate queues. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-09 | S5 | 7 | Employee override with lightweight reason preserves control. | Override audit without silent priority-policy rewriting. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| T07-10 | S5, S7 | 7; 9 | Deterministic guidance works without AI. | Capture-to-focus and budget-exhausted journey with unavailable provider. | Verified locally; [Stage 7 evidence](TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md) |
| BIZ-W02 | Addendum | 1; 7 | Critical tasks lead feasible work; blocked critical work receives unblock/escalation handling. | Critical-vs-other ranking, blocked-critical and current-focus urgent-policy tests. | Verified locally for feasible critical-first, Waiting and explicit urgent review; structured resolver requests and policy escalation verified in [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md). |

## Stage 8: Coordination, reliable jobs and reports

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T08-01 | S4, S5 | 8 | Structured blocker: reason/action/resolver/raised time/response expectation/escalation. | Required-field, ownership and resolution lifecycle tests. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-02 | S4 | 8 | Handoff context/action/acknowledgement prevents accepted work disappearing. | Cross-department ownership and acknowledgement transitions. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-03 | S5 | 8 | In-app notifications have an authorized owner and useful action. | No default all-active-user broadcast, forged audience or cross-team leak. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-04 | S5 | 8 | Deduplication, routine digests, focus/work hours and policy escalation. | Retry/quiet-hour/acknowledgement-vs-escalation tests. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-05 | S5, S8 | 8 | Cancel obsolete reminders and recheck access/resolver after changes. | Resolution/reassignment/revoked-recipient cases. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-06 | S7-S9 | 8 | One transactional outbox and deployment-compatible job foundation. | Runnable local worker/scheduler commands; no claim of deployment from defining a job. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-07 | S8 | 8; 9; 11 | Retryable idempotent delivery/jobs plus missed-event repair/reconciliation. | Worker restart, duplicate/out-of-order and missed-event recovery. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| T08-08 | P8 | 8 | Failed-job visibility, health/retention and operational ownership. | Failure diagnostics, recovery runbook and bounded retained job history. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| BIZ-R01 | Addendum | 8; 10; 12 | Employee End day triggers authorized manager/team report data. | Versioned snapshot reconciles accepted work, commitments, blockers/next actions and missing submissions. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| BIZ-R01-P | Addendum | 2; 8; 10 | Separate top-manager recipient rights; exclude private notes/reflections/conversations. | Cross-team/private-data and changed-recipient/send-time authorization tests. | Verified locally; [Stage 8 evidence](TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) |
| BIZ-R01-D | Addendum | 8; 13 | Report trigger/cutoff/correction/deduplication/failure semantics are explicit. | Replayed End day, late edit, absent submission, failed worker and retry tests. | Local test-policy/version/replay/missing/failure semantics verified; actual closing/correction/consolidation policy remains owner input |
| BIZ-R02 | Addendum | 8; 13 | Selected email-first EOD report delivery reuses Notification/Delivery. | Fake recipients and mocked sends first; real sender/recipient/provider plus authorized staging evidence before activation. | Email-first test adapter verified locally; real sender/recipients/policy and live activation pending |
| BIZ-R02-S | Addendum | 8; 13 | Report and send status distinguishes generated/queued/provider-accepted/delivered where knowable/failed. | A queued job or WhatsApp stub cannot claim actual delivery or reading. | Queued/held/test-accepted/failure/unknown states locally verified; no real provider delivery or reading claim |

## Stage 9: Governed integrated AI

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T09-01 | S7 | 9 | Reuse AIService/Responses adapter, conversations, prompt versions, usage and idempotent apply foundation. | Architecture review and existing-feature regressions; no parallel provider authority. | Not started |
| T09-02 | S7 | 9 | Typed compact permission-scoped context with true omission counts and freshness. | Oversized/>75-task and permission-aware cache tests. | Not started |
| T09-03 | S7 | 9 | Personal and manager-safe AI context remain separate with minimum content. | Private captures/reflections/transcripts cannot enter manager tools/context/cache. | Not started |
| T09-04 | S7 | 9 | Integrated capture extraction, explanation, breakdown, grounded briefings and blocker/handoff summaries. | Useful source-grounded proposals in workflow; meeting content remains deliberately supplied. | Not started |
| T09-05 | S7 | 9 | Allowlisted bounded tools derive identity/scope from authenticated server context. | AI-supplied identity, arbitrary ORM/financial/stock/external operations are rejected. | Not started |
| T09-06 | S7 | 9 | Separate read/propose/commit; show explicit proposal preview. | Read/propose cannot mutate committed work; apply requires authorized displayed proposal. | Not started |
| T09-07 | S7 | 9 | Atomic versioned expiry/idempotency on apply with canonical entity validation. | Replay, stale permission/state, unknown IDs and expired proposal tests. | Not started |
| T09-08 | S7, P9 | 9 | Audited undo respects subsequent human edits and conflicts. | Apply/undo replay and intervening-edit tests. | Not started |
| T09-09 | S7 | 9 | Long-running AI jobs expose pending/cancel/retry states. | Timeout/restart/cancellation and failed-job recovery without blocking core work. | Not started |
| T09-10 | S7 | 9 | Select/preflight model capabilities by measured quality/latency/cost. | Mocked capability preflight, recorded versioned configuration and representative evaluation results. | Not started |
| T09-11 | S7, P9 | 9 | Immutable effective prompt/config fingerprints, feedback and usable history. | Historical requests remain attributable after config changes; feedback/history scope tested. | Not started |
| T09-12 | S7 | 9 | Conversation retention/deletion/redaction and truthful provider-storage claims. | Stored-content deletion/retention tests; provider store flag is not a zero-retention promise. | Not started |
| T09-13 | S7 | 9 | Company and employee request/token/spend ceilings with reservation and settlement. | Concurrent boundaries, retries, unknown timeout usage and actual-price accounting tests. | Not started |
| T09-14 | S7 | 9 | Representative evals cover blocked/overdue/strategic/empty/missing/>75/malicious/overload cases. | Recorded invalid/unsafe rate, usefulness, time, latency and cost per accepted useful action. | Not started |
| T09-15 | S7 | 9 | Core capture/tasks/focus/factual reporting/operator survive provider outage or exhausted budget. | Complete degraded-mode walkthrough. | Not started |
| BIZ-A01 | Addendum | 1; 9; 13 | Enforce $0.50 per employee per week as money, not a fixed token count. | Weekly reset/currency policy confirmed; exact cost reservations/settlement and concurrency tests across every employee-attributed path. | Confirmed amount/period; not implemented |
| BIZ-A01-P | Addendum | 9 | Confirm weekly boundary/timezone, currency, company ceiling and automatic report cost attribution. | Explicit policy config with employee spent/reserved/remaining and scoped admin totals. | Policy pending |

## Stage 10: Management strategy board

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T10-01 | S6 | 10 | Goals across the board, teams/people as lanes with scoped drilldowns. | Employee/team-manager/executive view and forged-link tests. | Not started |
| T10-02 | S6, BIZ-T01 | 10 | Each member shows declared current/next work, load, risk, blockers/resolver and last confirmed update. | Populated/missing-data cases; progress reconciles to canonical team tasks. | Not started |
| T10-03 | S6 | 10 | Execution, dependency and capacity views. | Facts reconcile across all views; accessible alternatives to graph/drag controls. | Not started |
| T10-04 | S6 | 10 | Exception drawer for orphan goals, unowned work, stale confirmations, overdue handoffs and insufficient capacity. | Explicit fixture and useful action for each exception. | Not started |
| T10-05 | S6 | 10 | Draft what-if assignment/priority/deadline scenarios leave live work unchanged. | Preview/discard before/after state checks. | Not started |
| T10-06 | S6 | 10 | Show displaced commitments, dependency/capacity effects and forecast uncertainty. | Deterministic calculations; missing inputs remain unknown and AI does not invent forecasts. | Not started |
| T10-07 | S6 | 10 | Explicit scenario commit rechecks authority, version and feasibility then audits/notifies. | Stale/unauthorized/conflicting commit and correctly targeted notification tests. | Not started |
| T10-08 | S6, S10 | 10; 13 | No task-count leaderboard or opaque AI employee score; no inferred attendance/productivity. | Product field/copy review and honest last-confirmed/update labels. | Not started |
| T10-09 | BIZ-R01, BIZ-W01 | 10 | Scoped arrival and EOD report overview shows declarations and missing data clearly. | Team/executive access, private exclusions, snapshot reconciliation and audit drilldown. | Not started |

## Stage 11: IMS source adapters

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T11-01 | S8 | 11 | BusinessRecordLink/action identity with unique source/type/record/action keys. | Replay creates one action/outcome and retains canonical source links. | Not started |
| T11-02 | S8 | 11 | Credit Control CollectionTask/payment promise adapter first. | Unified authorized collection action and source-driven completion/reopen/escalation. | Not started |
| T11-03 | S8 | 11 | Available-stock replenishment adapter with explicit procurement owner. | Reservations/reorder state and approved procurement source drive action lifecycle. | Not started |
| T11-04 | S8 | 11 | Shipment ETA/status exceptions, receiving and clearance actions. | Source changes/reopen reconcile and stale reminders cancel. | Not started |
| T11-05 | S8 | 11 | Service/repair queue with handoffs, quality acceptance and SLA. | Technician/case permission and accepted source-outcome evidence. | Not started |
| T11-06 | S8 | 11 | Stocktake REVIEW approval actions preserve source approval authority. | Restricted user cannot approve via Tasker or AI. | Not started |
| T11-07 | S8 | 11 | SENT quotation follow-up after defining sales owner/next-contact policy. | Owner policy configured before automation; duplicate/contact/source-state tests. | Not started |
| T11-08 | S8 | 11 | Paid shop-order fulfilment actions follow actual fulfilled/shipped state. | Checkbox alone cannot fabricate shipment or business outcome. | Not started |
| T11-09 | S8 | 11 | Supplier-bill due-payment review preserves finance approvals. | No payment posting from task completion; approved source lifecycle tests. | Not started |
| T11-10 | S8 | 11 | Fiscal-close checklist links authoritative finance evidence. | Fiscal period/finance permissions and source acceptance tests. | Not started |
| T11-11 | S8 | 11 | Each adapter rechecks source scope and reconciles complete/reopen/delete/reassign. | Adapter-specific lifecycle, cross-scope, missed-event/worker retry tests. | Not started |
| T11-12 | S8 | 11 | Reuse outbox/jobs; separate adapter flags; preserve workflow authority and outcome deduplication. | No credit-control broadcast inheritance; per-adapter rollback/runbook and pending-slice list. | Not started |

## Stage 12: Improvement routines and close day

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T12-01 | S4 | 12 | Repeated carry-over leads to clarify/split/delegate/reschedule-with-reason/stop. | Explicit decisions replace infinite automatic rollover. | Not started |
| T12-02 | S4 | 12 | Weekly personal/team review and manager decision agenda use unresolved authorized facts. | Scope, resolved/stale approval and private-exclusion tests. | Not started |
| T12-03 | S4, P12 | 12 | Recurring work and versioned SOP/checklist templates reuse job foundation. | Replay/timezone/pause/exception/missed-occurrence tests without duplicate work. | Not started |
| T12-04 | P12 | 12 | Template changes affect future occurrences and preserve historical evidence. | Version/history and already-created-instance tests. | Not started |
| T12-05 | S4 | 12 | Idea grouping/duplicate suggestions and small measurable experiments. | Owner, expected benefit, review date, accepted outcome and provenance with deliberate sharing. | Not started |
| T12-06 | BIZ-R01 | 12 | End-day action closes/reviews work and triggers the shared report pipeline once. | Retry/late-edit/missing-submission behavior and no private reflection leakage. | Not started |

## Stage 13: Pilot and release evidence

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| T13-01 | S10 | 13 | Predefine comparable output/effort, workload adjustment, quality and observation window. | Agreed baseline/control or staggered cohort protocol, adequate volume and uncertainty method. | Not started |
| T13-02 | S10 | 13 | Report throughput/cycle/blocked time/deadline reliability/outcomes/rework/overtime/admin/cost/completeness. | Canonical measurements and clear missing/unknown indicators. | Not started |
| T13-03 | S10 | 13 | Test >70% uplift honestly without substituting task percentage or timer/arrival activity. | Real comparable accepted-output/hour and quality evidence; no guaranteed-benefit claim. | Not started |
| T13-04 | P13 | 13 | Separate local release-readiness and real productivity-validation verdicts. | Synthetic tests do not close productivity gate; real cohort/window evidence required. | Not started |
| T13-05 | S11, P13 | 13 | End-to-end capture/promote/goal/plan/focus/block/handoff/accept/source/manager walkthrough. | Employee/manager/restricted-user, AI outage and failed-job recovery evidence. | Not started |
| T13-06 | S9-S12 | 13 | Required regressions and PostgreSQL migrations/constraints/concurrency on disposable infrastructure. | Exact commands/results and explicitly pending checks; no SQLite substitution for locking behavior. | Not started |
| T13-07 | S4, P13 | 13 | Employee usability/accessibility/mobile sessions and staff guidance/training. | Recorded observations, unresolved issues and completed walkthroughs. | Not started |
| T13-08 | S9, P13 | 13 | Infrastructure-specific staged migration/static/web/worker/scheduler/secret/flag runbook. | Tested backup/restore and staging rehearsal, health checks, named operators and rollback triggers. | Not started |
| T13-09 | S11 | 13 | Small monitored rollout; expand based on quality and measured benefit. | Cohort flags, monitoring, recovery and explicit deployment/provider/send activation decisions. | Not started |
| T13-10 | P13 | 13 | Reconcile all mandatory requirements and selected extensions before release. | No vanished requirement; every gate verified or explicitly unresolved with owner. | Not started |
| BIZ-P01 | Addendum | 2; 13 | Pilot with a few existing registered employees. | Explicit roster/roles/manager and start/comparison window; no automatic all-user enrollment. | Roster pending |

## Cross-cutting requirements

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| X01 | S1, S9 | All | Preserve Django/IMS architecture, authentication, selectors/services, audit, soft deletion and existing workflows. | Previous-stage/IMS regressions and no competing write/provider/permission/job authority. | Not started |
| X02 | S3, S6, S7 | All | Personal information boundaries apply to UI/API/search/export/cache/admin/AI/report delivery. | Channel-specific scoped tests; hiding a button is insufficient. | Not started |
| X03 | S9 | All | Atomic writes and version/concurrency handling on notes/schedule/assignment/proposals/delivery/quotas. | Relevant PostgreSQL transaction/race tests. | Not started |
| X04 | S9 | All | Additive migrations, explicit legacy policy and backfill unknowns without invented estimates. | Migration/rehearsal with preserved data and documented rollback compatibility. | Not started |
| X05 | S9 | All | Bounded queries, indexes and pagination for employee/board/report/source workflows. | Query/performance evidence under representative data volume. | Not started |
| X06 | S9, S11 | All | Per-slice flags and reversible rollout with source/data reconciliation. | Disable/rollback tests and documented recovery implications. | Not started |
| X07 | P1-P13 | All | Each stage records contracts/files/migrations, exact checks, gaps, owner actions and handoff. | Status/matrix updated; no unrun check marked passed. | Not started |
| X08 | Playbook | All | Preserve unrelated local edits and excluded secret files; no automatic commit/push/deploy/provider calls/sends. | Sanitized scoped change review and actual action record. | Observed through Stage 1; continue each stage |

## Conditional and selected-later extensions

| ID | Source | Stage | Requirement | Required acceptance evidence | Status |
| --- | --- | --- | --- | --- | --- |
| EXT-CAL | S5, playbook | After 6 | Optional external calendar; start with read-only busy time and local fallback. | Provider/policy selection, timezone/recurrence/revocation/reconciliation/privacy tests. | Conditional; not selected |
| EXT-VOICE | S4, playbook | After 4, 9 | Optional explicit voice capture with consent/editable transcript/private draft. | Denied/interrupted recording, retry/deletion/retention and budget tests. | Conditional; not selected |
| EXT-OFFLINE | S4, playbook | After 4 | Optional minimal per-user offline drafts with honest sync states. | Reconnect/replay/conflict/logout/account-switch/revoked-access tests. | Conditional; not selected |
| BIZ-R03 | Addendum | After email pilot | WhatsApp later reuses the report/delivery foundation, with a real transport. | Provider/sender/recipients/cost policy plus mocked failures/replays and separately authorized live verification. | Selected later; not started |
| EXT-OTHER | S5, playbook | After 8 | Other push/chat channels remain conditional. | Explicit selection/provider/audience/consent/cost and delivery evidence before activation. | Conditional; not selected |

Email is covered by BIZ-R02 as a required first-channel feature. It must not be deferred merely because the original generic channel prompt was optional. Weekly budget period is confirmed; rows or summaries calling it unknown are stale.

## Source coverage check

| Source | Requirement groups |
| --- | --- |
| Strategy 1-2 | X01; T01; T03-T05; T09 foundations. |
| Strategy 3 | T01-01 through T01-16; X02-X03. |
| Strategy 4 | T03-T05; T07; T08; T12; conditional capture extensions. |
| Strategy 5 | T06-T08; X03. |
| Strategy 6 | T02; T10; X02. |
| Strategy 7 | T01; T09; T08 job foundation; X02-X03. |
| Strategy 8 | T11-01 through T11-12; T08 outbox/reconciliation. |
| Strategy 9 | X01-X07; primary stage service/data contracts. |
| Strategy 10 | T02 baseline; T13 measurement; honest productivity/attendance requirements. |
| Strategy 11 | T03-T13 vertical journey, gates and staged pilot. |
| Strategy 12 | T13 verification; status records actual current limitations. |
| Owner 21 October | BIZ-G01-G03, BIZ-T01 via T02-02/03 and T10-02, BIZ-W01/W02, BIZ-R01-R03, BIZ-A01 and BIZ-P01. |
