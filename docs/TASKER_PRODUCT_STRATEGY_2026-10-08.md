# Tasker: company execution and productivity strategy

Prepared 8 October 2026. This is a source-based product and architecture review, with a proposed implementation backlog. Proposed features are not implemented by this document. The accompanying interface concept uses illustrative data.

Owner update, 21 October 2026: [business requirements](TASKER_BUSINESS_REQUIREMENTS_2026-10-21.md), [implementation status](TASKER_IMPLEMENTATION_STATUS.md) and [requirements matrix](TASKER_REQUIREMENTS_MATRIX.md) now record the supplied goals and operating rules. Confirmed additions include USD 2m total sales over two years, arrival registration, team-manager delegation/visibility, End day reports by email first (WhatsApp later), a $0.50 weekly per-employee AI cap and a small existing-employee pilot. These later decisions take precedence over generic optional-channel wording below. The original review findings/validation remain dated observations; current checks are in implementation status. No application features are implemented by this amendment.

## 1. Recommendation

Evolve Tasker into a company execution system: capture thoughts quickly, choose the most useful feasible action, help people finish it, resolve handoffs, and connect accepted business outcomes to company goals.

The primary employee experience should answer four questions immediately:

1. What should I work on now?
2. Why does it matter, and what does done mean?
3. What is waiting on someone else, and who is resolving it?
4. What changes if I accept new work?

The primary manager experience should answer: which goals are at risk, which decision unlocks the most work, and where can capacity be moved without breaking existing commitments?

Preserve the existing Django application, shared IMS authentication, service boundaries, audit history, soft deletion and focus timer. A frontend or backend rewrite is not necessary to deliver the first releases.

## 2. What the code currently does

The review covered Tasker models/migrations, forms, permissions/roles, selectors, services, signals, HTTP routes/controllers, templates, CSS/JavaScript, AI context/provider/orchestration/governance, and the existing tests. Adjacent IMS workflows were inspected for integration opportunities. Runtime production settings, live employee data and actual OpenAI connectivity were not verified.

| Capability | Current implementation | Assessment |
| --- | --- | --- |
| Task management | Ownership, delegation, priority, due date/time, status, progress, hierarchy, estimates/actual time, comments, checklists, archived/deleted records and activity history | Strong foundation to extend |
| Today | Owner-scoped progress counts, four priorities, deadline timeline, carry-over and scratchpad; bounded queries | Useful overview; lacks a continuous execution flow |
| Capture | Global modal and keyboard shortcut create a task due today | Fast entry, but ideas and notes become artificial daily commitments |
| Notes | One quick-notes text field on each DailyPlan; task working notes | No independent searchable idea/note collection |
| Planning | Current-day focus area, up to four top priorities, task selection and end-day reflection | No capacity calculation, planned start/end blocks or future-day navigation |
| Execution | Persistent focus timer, pause/resume, blocking, completion, checklist and autosaved task notes | Valuable existing execution surface |
| AI | Six personal actions: plan day, next task, prioritize, summarize day, break down task and explain task; OpenAI Responses adapter, schemas, retries, quotas, conversations and usage logs | Already implemented in code, gated by permissions and configuration; production enablement is unverified |
| AI actions | Selected generated steps can be added idempotently to a checklist | Other recommendations mostly require manual translation into work |
| Management | Owner filters, global visibility permissions, Django administration | No team capacity board, structured goal tree or strategic planning surface |
| Automation | Activity signals and synchronous user-triggered AI | No Tasker notification domain, scheduler wiring or event-driven operator loop found |

Core evidence: [models](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/models.py:141), [dashboard assembly](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/dashboard.py:31), [planning](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/planner.py:5), [focus lifecycle](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/focus.py:29), [personal AI features](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/personal_assistant.py:17), [routes](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/urls.py:7).

README_Tasker.md still describes user-facing AI as future work. The code is ahead of that documentation; update the documentation after the correctness work.

## 3. Resolve trust and correctness issues first

These findings are verified from source. AI-validator edge cases were additionally exercised by extracting the validator in isolation, without database access or provider calls. The full Django suite could not run; see validation below.

| Priority | Finding | Required change |
| --- | --- | --- |
| Immediate | An apparent OpenAI credential is present in an existing, locally modified documentation file | Revoke/rotate through the account owner; remove the exposed value while preserving other local edits; check tracked history and introduce secret scanning. Do not copy it into reports or test fixtures. File: tasker/README_AI_Extensions.md, line 2 |
| High | Dashboard top priorities include BLOCKED tasks; an undated critical task can be absent unless selected/carry-forward | Separate executable candidates from waiting/escalation work; always evaluate critical work even without a date. [dashboard](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/dashboard.py:14) |
| High | Personal AI context orders raw priority strings with `-priority`, unlike the semantic priority selector | Reuse `priority_rank_expression`; ensure priority-critical, active-focus and explicitly selected tasks survive context limits. [context](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/personal_context.py:63), [selector](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/selectors.py:12) |
| High | Next-task validation accepts blocked task IDs and negative focus durations; duplicate negative-duration plan entries and unknown IDs in summary unfinished work are not rejected | Validate the complete response locally and enforce business rules, not only schema shape. Fetch current authorized records again before applying. Canonical task titles must come from those records. [validator](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/personal_assistant.py:72) |
| High | An incorrectly typed recommendation item can raise an uncaught AttributeError | Parse into typed validated results, handle malformed/refused/incomplete output gracefully, and fall back to deterministic recommendations |
| High | Context repeats full descriptions in workload/today/carry-over arrays, caps at 75 tasks and reports that capped length as workload size | Send one compact task collection with ID references, enforce an overall token/character budget, disclose omitted counts, and query real totals. A synthetic 25-task example with 2,000-character descriptions exceeded the configured 50,000-character gate. [payload](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/personal_context.py:113), [gate](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/service.py:255) |
| High | A STARTED usage log is inserted before the limiter counts logs with `>= limit`; limit 1 rejects the first request | Use an atomic quota reservation with explicit treatment of current/in-flight requests and token reservations; test boundary and concurrent cases. [creation](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/service.py:232), [quota](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/rate_limits.py:20) |
| High | Invalid quick-add submissions retain a bound form in the view, but the shared modal renders unbound inputs without errors | Preserve entered data, reopen capture, show associated field errors and a concise error summary. [view](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/views/dashboard.py:25), [modal](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/templates/base.html:85) |
| High | Focus checklist navigation bypasses the save-before-navigation handler; edits during an in-flight save can also clear the dirty flag incorrectly | Version saves, serialize requests, flush before all navigation, retain unsaved drafts, and retry without requiring another edit. [focus JS](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/static/tasker/js/focus.js:23) |
| High | Task notes are labeled Private context but visible to anyone allowed to view the task; personal AI context also includes daily scratchpad/reflections | Define personal versus shared data explicitly. Add truly personal capture/note records and separate manager-safe context. Review staff AI-message admin access and retention. [detail](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/templates/tasker/task_detail.html:45), [AI admin](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/admin.py:202) |
| Medium | Checklist completion updates progress without synchronizing completion status/timestamp; manual progress uses different rules | Establish one explicit lifecycle policy. Prefer distinguishing checklist completion from accepted task completion and prompting for definition-of-done confirmation. [progress](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/tasks.py:101) |
| Medium | Applying previously suggested checklist steps does not recheck whether the task has since completed or been archived | Revalidate eligibility and entity versions at commit time; expired proposals should be regenerated. [apply](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/ai/personal_assistant.py:129) |
| Medium | Dashboard completion percentage counts completed tasks due/planned today, not productivity or tasks completed today | Label the metric accurately; introduce separate accepted-output and cycle-time measurements. [counts](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/tasker/services/dashboard.py:38) |

## 4. Premium employee experience

### Navigation and interaction

Use a focused Tasker shell: **Now, Inbox, Plan, Work, Goals**, with **Strategy** for authorized managers. Put the IMS module switcher in secondary chrome. Embed AI assistance beside the work it affects; retain a history view without making employees visit a separate assistant page for routine guidance.

The Now page has one dominant action, a short next queue, waiting items and actionable decisions. Task lists support a compact list and board, saved views, inline actions and a detail drawer. Preserve useful shortcuts and add shortcut settings. Every drag interaction must have a keyboard/button equivalent.

Premium means predictable behavior, clear hierarchy, responsive performance, touch-friendly controls, useful empty states, associated labels, readable contrast, visible focus, and reliable save/undo states. Use design tokens and consistent components. Offer light/dark preference and respect reduced motion.

Proposed usability targets, to validate with employees: thought captured within 10 seconds; useful work started from Now in one action; morning planning within 90 seconds; no silently discarded capture or note; routine administration under five minutes per day. These are targets, not measured current results.

### Universal capture and idea development

Capture accepts a thought, task, idea, note or link with one input. Default to a private unscheduled Inbox. A user can explicitly choose today or a due date; capture itself is not a commitment.

AI can propose type, title, next action, related goal and parsed dates. Show the interpreted date/time before commitment, especially for ambiguous phrases. Saving the original must succeed independently of AI availability. Keep source text and a link to any task created from it.

Give notes/ideas individual records, search, tags, pinning, archive, versioned autosave and recoverable deletion. Private ideas can be deliberately shared with a team. A weekly idea review groups related ideas, surfaces potential duplicates and proposes a small experiment with owner, expected benefit and review date. Promotion to a task/project preserves provenance and does not duplicate the original thought.

On mobile, prioritize capture and Now. Add queued offline capture only after the online flow is reliable, with per-user draft isolation, explicit pending/synced states, safe logout handling and idempotent synchronization. Voice capture is a later option with consent and an editable transcript.

### Continuous execution

Example, using illustrative company work:

> Work on this now: Confirm the customer's payment promise — 15 minutes.
> Why: The agreed date is today and it advances the cash-collection goal.
> First step: Open the linked invoice and call the contact.
> Done means: Record the agreed outcome against the customer record.

Offer **Start**, **Choose another**, **Blocked** and a concise explanation. Start uses existing Focus Mode. Show the relevant checklist step, definition of done and source record. An idea parking lot captures distractions without leaving focus.

Completion offers the next useful action. Blocking records what is needed, the resolver, expected response and escalation rule, then offers executable alternative work. A handoff includes context, required action and acknowledgement. An accepted task cannot disappear between departments merely because someone added a comment.

Repeated carry-over should trigger a useful decision: clarify the outcome, split the next step, delegate, explicitly reschedule or stop the work. A weekly improvement review can turn recurring blockers into reusable process/checklist templates and small experiments. Generate a brief manager decision agenda from actual unresolved approvals to reduce status-meeting preparation and repeated chasing.

## 5. The operator engine

Use **Now / Next / Waiting / Decisions** as distinct queues. Preserve the employee's current commitment; recomputation should not constantly reorder an active focus session.

First enforce hard constraints: current authorization, active lifecycle, dependency readiness, required capability, working hours, fixed commitments and available capacity. Waiting work can be important without being executable; route its unblock action to the appropriate resolver.

Then rank feasible candidates using configurable factors: company-goal contribution, deadline risk, dependency work unlocked, agreed urgency, task age, remaining effort and context-switch cost. Keep a deterministic fallback available. Managers set policy; AI may propose rationale or estimates but cannot silently redefine company priorities.

Include undated important work, protect long strategic work from endless small-task preference, and require re-estimation when a task overruns. Remaining effort is not reliably `original estimate - logged time`. Do not treat unknown estimates as zero.

Recompute after completion, blocker/dependency changes, approved urgent assignments, plan/availability changes and scheduled review points. Store the candidate snapshot, policy version, reasons, excluded constraints and freshness. Expire stale recommendations and revalidate before acting. Employee overrides should record a lightweight reason and preserve control.

Planning must distinguish **deadline** from **planned work block**. Compute capacity from working hours minus fixed commitments, leave and an explicitly chosen interruption buffer. Start with editable local availability; external calendar sync can follow. Show overcapacity before acceptance and offer defer, delegate, split scope or change the commitment. Permit planning/review for tomorrow and previous dates.

Notifications should have an owner and an action: start, acknowledge, unblock, approve, reschedule or open the relevant source. Begin with an in-app center. Deduplicate events, group routine items into digests, respect focus/work hours, and escalate actionable risk according to policy. Email/push/chat are optional later delivery channels, not prerequisites for the operator.

## 6. Management strategy board

Use goals across the top and teams/people down the side. Each employee card shows declared current task, next commitment, estimated load against availability, deadline risk, blocker age, resolver and last confirmed update. A running timer or recent interaction is not proof of productive work or presence.

Add three complementary views:

- **Execution board:** Where each goal's work sits, who owns it and what is waiting.
- **Dependency map:** Which decision or delivery unlocks other tasks; show the affected milestones and responsible resolver.
- **Capacity outlook:** Upcoming commitments, overloaded teams, unassigned work and forecast uncertainty.

An exception drawer surfaces orphan goals, unowned work, stale confirmations, overdue handoffs and deadlines with insufficient capacity. The most useful manager prompt is often “resolve this approval to unblock three people.”

Support reversible what-if scenarios: temporarily assign an available qualified person, preview displaced commitments and milestone effects, then explicitly commit the scenario. Derive forecasts from dependency/capacity calculations and show uncertainty; AI can explain the tradeoff. Audit actual reassignment and notify affected people.

Use team-scoped permissions, separate organization-wide executive access and explicit source-record checks. Django role groups are capability bundles, not reporting structures. Current supervisor visibility is global; add TeamMembership boundaries before exposing personal captures or detailed team views. Do not use task-count leaderboards or opaque AI employee scores.

Goals need owner, metric, baseline, target, horizon and authoritative evidence. Structure the relationship as **Company goal → measurable result → initiative → task → accepted outcome**. Use direct primary contribution first; define allocation rules before attributing the same outcome to multiple goals. A goal's progress must not be the average of arbitrary task percentages.

## 7. OpenAI design

Retain the existing Responses adapter, prompt versions, local conversations, content-free usage aggregates and idempotent checklist-apply pattern. The six existing personal features provide a good start. Add action continuity before adding more assistant buttons.

Use AI where it reduces real effort: capture extraction, concise next-action explanations, task breakdown, source-grounded briefings, meeting-to-draft actions, blocker/handoff summaries, idea clustering and weekly improvement suggestions. Select models by measured quality/latency/cost for each operation rather than fixing one expensive model for every request.

Separate responsibilities:

1. Application code obtains permission-filtered candidates and authoritative business facts.
2. Deterministic services enforce scheduling, dependencies, lifecycle and quota policy.
3. AI returns typed proposals with source IDs and reasons.
4. The server validates shape and semantics, resolves canonical records and displays a concrete change preview.
5. An authorized action invokes existing domain services with an idempotency key and audit event.

Add allowlisted tools only for bounded operations, such as `list_my_ready_tasks`, `draft_plan`, `propose_checklist`, `save_capture`, `apply_plan` and `request_handoff`. Identity and scope come from the authenticated server context, never an AI-supplied user ID. Tool calls must still pass normal permission and lifecycle checks. Separate read/propose/commit operations; external communications and financial/stock changes require their existing business approval paths.

OpenAI's [function-calling guide](https://developers.openai.com/api/docs/guides/function-calling) supports connecting models to application actions; its [strict-mode guidance](https://developers.openai.com/api/docs/guides/function-calling#strict-mode) explains schema-constrained arguments. Schema compliance does not establish authorization, capacity or business truth. Maintain local semantic validation. The [Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs#tips-for-your-json-schema) recommends evaluating schemas with real examples.

Build a representative evaluation set: overdue versus strategic work, blocked high-priority tasks, empty workload, missing estimates, >75 tasks, interrupted API responses, duplicate apply, malicious instructions in notes, permission changes and overloaded schedules. Measure unsafe/invalid proposal rate, useful recommendation acceptance, planning time, latency and cost per accepted useful action. Business-output measurement remains separate from AI-response quality.

Move long-running AI requests to background jobs with visible pending/cancel/retry states. Current user-facing calls are synchronous; internal streaming is not wired to an employee endpoint. Use compact, deduplicated context with explicit freshness and omission counts, short-lived permission-aware caching and minimum necessary content. Keep personal reflections out of manager context. Set retention/deletion rules for stored conversations, not only provider-side storage. Quotas need atomic reservations and a company-level spend ceiling. AI outage must leave capture, tasks, focus and deterministic next-action guidance usable.

## 8. IMS integration opportunities

Start with one source-linked adapter. Keep each business module authoritative; an inbox item should not create a second conflicting collections, payment or stock workflow.

| Source | Proposed Tasker action | Important implementation detail |
| --- | --- | --- |
| Credit Control CollectionTask and payment promises | Unified collector queue, overdue promise action, escalation decision | Existing assignee/due date/source links make this a strong first integration |
| Inventory quantity/reservations/reorder level | Procurement/replenishment review | Use available stock and explicit procurement owner; do not infer completion from a checked task |
| Shipment ETA/status | Clearance, receiving and late-shipment actions | Link to shipment and reconcile source transitions |
| ServiceCase technician/status | Repair queue and handoffs | Include acceptance/quality evidence and case SLA |
| Stocktake REVIEW state | Authorized approval action | Preserve source approval permissions |
| SENT quotation | Sales follow-up | Sales owner and next-contact policy must first be defined; not currently present on Quotation |
| Paid shop order | Fulfilment queue | Completion follows actual shipped/fulfilled state |
| Supplier bills and fiscal close | Due-payment review and close checklist | Finance permissions and approval flow remain authoritative |

Code entry points: [CollectionTask](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/credit_control/models.py:312), [collection automation](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/credit_control/services/workflows.py:192), [inventory](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/inventory/models.py:74), [service cases](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/inventory/identity_models.py:119), [quotation](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/sales/models.py:27), [orders](C:/Users/admin/Videos/Boforg/IMS/BoforgIMS/shop/models.py:57).

Use unique source/type/record/action keys, transactional event outbox, retryable workers and periodic reconciliation. Recheck source authorization when reading and acting. Cancel stale reminders after source completion; handle reopened/deleted/reassigned source work. Credit Control's existing notification default broadcasts to all active users; that audience rule must not be inherited by Tasker. Its daily job function is not evidence that a deployed scheduler exists.

## 9. Data and service evolution

Introduce models incrementally rather than implementing this entire list at once:

| Slice | New concepts |
| --- | --- |
| Capture | CaptureItem with type, original text, owner, visibility, triage state and promoted-task link; Note revisions or a deliberate revision field |
| Organization and goals | Team, TeamMembership, Goal/KeyResult, Initiative and task-goal linkage; company boundary if the business requires multiple companies |
| Feasibility | WorkPolicy/Availability, ScheduleBlock, TaskDependency and structured Blocker/Handoff |
| Reliable operation | Recommendation snapshot, Notification/Delivery, domain event outbox and idempotent job/action records |
| Business evidence | BusinessRecordLink/source adapter and OutcomeMeasurement with acceptance/provenance |

Suggested service modules: capture, prioritization, capacity, scheduling, blockers, goals, strategy, notifications, integrations and outcomes. Keep views thin, selectors scoped and writes atomic. Reuse existing task/planner/focus services; add explicit lifecycle contracts before relying on automatic completion. Bounded employee and board queries, indexes and pagination remain important.

Roll out behind feature flags. Preserve existing routes and data, migrate additively, backfill with unknown values rather than invented estimates, and make each slice reversible. Add concurrency/version handling for notes, schedule edits, assignment, notification delivery and quota reservations.

## 10. Measuring the 70% objective honestly

There are two different targets: 70% of time spent productively, and 70% greater productivity than a comparison group. They are not interchangeable. Pending company clarification, interpret the request as **more than 70% improvement in comparable accepted output per staffed hour**, while maintaining quality.

`Productivity uplift = (Tasker accepted-output rate / comparison accepted-output rate - 1) × 100%`

Illustration only: 10 comparable accepted units/hour versus 17 units/hour is a 70% improvement. A 70% task-completion dashboard is not evidence of that improvement. The target is a hypothesis to test, not a guaranteed product claim.

Agree a baseline and metric for each role before rollout. Examples: accurate orders fulfilled per staffed hour; accepted repair cases per technician hour, adjusted for case type; resolved collection actions and cash outcomes against a comparable debtor mix; stock discrepancies resolved with verified evidence. Money collected alone is not a fair labor-productivity measure because portfolio size and payment timing vary.

Run a prospective baseline, then a staggered team rollout or comparable control where feasible. Keep workload mix, staffing, holidays, learning effects and shared-team spillovers visible. Predefine which outputs count, quality acceptance, effort denominator and attribution rules. Use adequate observation volume and report uncertainty rather than selecting a flattering week.

Track accepted throughput, cycle time, blocked hours, deadline reliability, goal outcomes, rework/errors, overtime and employee administration burden. Show team/role trends and data completeness. Task splitting, timer hours, clicks and easy-task selection must not inflate the result. Useful breaks and collaboration are not automatically time waste.

## 11. Prioritized implementation roadmap

The order below is a dependency sequence, not a delivery-time estimate. Team size, production architecture and company priorities must inform estimates.

| Release | Reviewable result | Acceptance gate |
| --- | --- | --- |
| A — Trust and baseline | Correct ranking/validation/quota behavior, reliable note/capture saves, accurate privacy wording and documented baseline metrics | Edge-case and concurrent-write regressions pass; exposed credential handled by account owner; current flows remain usable |
| B — Now and Inbox | Focused navigation, unscheduled task/idea/note capture, searchable personal records, executable next-action card and direct Start | Capture survives provider/network failure; blocked work stays in Waiting; start useful work in one action; mobile/keyboard walkthrough passes |
| C — Goals and feasible plans | Team membership/access, measurable goals, definition of done, availability, future planning, dependency and capacity checks | No unauthorized cross-team/private-data access; overloaded/conflicting plan cannot be silently accepted; undated goal work can be surfaced |
| D — Operator and action continuity | Event-based recomputation, structured blockers/handoffs, quiet in-app notifications, accept/undo AI plan changes, asynchronous AI | Repeated events/apply actions are idempotent; focus remains stable; stale proposals are revalidated; core work continues during AI outage |
| E — Strategy and first IMS adapter | Goal/people board, dependency/exception view, capacity scenario preview and collections source-linked inbox | Scenario shows displaced commitments; source completion reconciles; manager data is scoped; business workflow remains authoritative |
| F — Pilot and expansion | Measured team rollout, outcome reporting, refined policy, additional adapters and repeatable SOP templates | Report benefit/quality/effort with comparable evidence; expand features that reduce cycle time or blocked work |

The first vertical slice should let one employee capture an idea, promote it into a task, receive a feasible recommendation, start focus, record a blocker and complete accepted work without losing context. Manager strategy depth follows reliable underlying state.

## 12. Validation and open decisions

- `venv/Scripts/python.exe manage.py check --settings=config.test_settings` passed with no Django system-check issues. Startup separately reported an existing dotenv parse warning at line 9; configuration content was not printed or changed.
- `venv/Scripts/python.exe manage.py test tasker --settings=config.test_settings --noinput` discovered 88 tests but failed during database setup because the isolated PostgreSQL endpoint at 127.0.0.1:55439 was unavailable. No passing full-suite claim is made.
- AI-validator edge cases were exercised independently of Django database setup. No paid OpenAI calls were made for this review.
- The illustrative concept passed fragment, JavaScript-syntax, DOM-reference and no-network source checks. Browser visual inspection could not proceed because its automatic approval review timed out; this was not a determination that the preview was unsafe. Responsive visual QA remains unverified.
- Source review establishes implementation behavior, not production adoption, visual usability or delivered productivity. Employee usability sessions, staging browser checks, database tests and pilot measurement remain implementation acceptance work.
- Application code and existing local edits were preserved; this review adds a strategy document and an illustrative interface concept only.

Company inputs still needed for tailoring: the first three measurable goals; roles/team/reporting boundaries; largest current bottlenecks; working hours/availability; what constitutes accepted work; notification/escalation preferences; and production worker/scheduler ownership. These affect policy and rollout, but do not prevent the first correctness and capture improvements.
