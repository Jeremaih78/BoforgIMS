# Tasker business requirements and decisions

Recorded 21 October 2026, Africa/Harare. Source: the company owner's instructions and follow-up answers in this chat. This is a preparation record; it does not enable features, configure live budgets or send messages.

Read this addendum together with [the product strategy](TASKER_PRODUCT_STRATEGY_2026-10-08.md), [the implementation playbook](TASKER_IMPLEMENTATION_PLAYBOOK_2026-10-08.md), [the requirements matrix](TASKER_REQUIREMENTS_MATRIX.md) and [implementation status](TASKER_IMPLEMENTATION_STATUS.md). Later explicit owner decisions take precedence over earlier proposed defaults.

## Confirmed company direction

| ID | Owner's objective | Confirmed horizon | Still needed for a measurable goal |
| --- | --- | --- | --- |
| BIZ-G01 | Reach **USD 2m total sales over the next two years**. The owner explicitly selected cumulative sales in USD in the follow-up clarification. | Using this instruction's date gives a planning reference of 21 October 2028; confirm the business start/end dates before publishing commitments. | Sales recognition, tax/returns/cancellation treatment; authoritative accounting/source evidence; baseline; owner. Do not substitute profit, cash collected or annual run rate. |
| BIZ-G02 | Become the largest supplier of printing equipment and consumables: DTF, sublimation, vinyl cutters, 3D printing, laser engraving and paper printers. | By the year 2030. | Market/geography; how "largest" is measured; comparator/source; baseline; specific deadline within 2030; accountable owner. |
| BIZ-G03 | Lead in AI, automation, SaaS and AI agents; Boforg must be a bridge between African businesses and artificial intelligence. | In the next 36 months. Using this instruction's date gives a planning reference of 21 October 2029. | Which African markets and customer segments; how leadership and customer benefit are measured; baseline; targets; authoritative evidence; owner. |

These are real strategic objectives, but the unconfirmed measures must remain unknown. Do not seed fictitious baselines, market shares, customer targets, team structures or financial achievement. Suggested measures below are decision aids, not approved commitments.

## Confirmed operating requirements

| ID | Requirement | Stage ownership |
| --- | --- | --- |
| BIZ-T01 | A team manager can see team members' shared work tasks and progress, delegate work to members of that team, and see an overview of each member's work. | Stage 2 access/delegation; Stage 5 work controls; Stage 10 overview. |
| BIZ-W01 | Each employee can register their arrival time. | Stage 2 identity/access contract; Stage 3 entry point; Stage 6 attendance/availability records; Stage 10 scoped overview. |
| BIZ-W02 | Critical tasks receive top priority. | Stage 1 semantic ranking; Stage 7 feasible next-action policy. |
| BIZ-R01 | Employee end-of-day actions generate report information about team members under their manager for top management. | Stage 8 event/report snapshot/jobs; Stage 10 manager report view; Stage 12 close-day workflow; Stage 13 pilot checks. |
| BIZ-R02 | Deliver these reports by email first. | Stage 8 email delivery adapter and mocked tests; Stage 13 configured staging/pilot verification. |
| BIZ-R03 | Add WhatsApp delivery later. | Selected later extension of Stage 8 delivery, after provider, audience and sending policy are chosen. It is not required to finish the initial email pilot. |
| BIZ-A01 | Each employee may use at most $0.50 of AI spending per week. | Stage 1 existing quota correctness; Stage 9 monetary weekly cap and settlement; Stage 13 pilot cost reporting. |
| BIZ-P01 | Pilot Tasker with a few employees already registered in IMS. | Stage 2 baseline; Stage 13 explicit pilot cohort and staged rollout. |

## Access and privacy contract

- Team membership and manager assignments define visibility and delegation scope. Permission groups alone do not establish reporting relationships. An employee cannot acquire a broader team audience by forging an ID or changing a URL.
- Managers receive work-relevant task, progress, commitment, blocker and declared-arrival information for their authorized teams. Personal captures, private notes, reflections and personal AI conversations remain outside manager reports and manager AI context unless deliberately shared through an explicit supported action.
- Top-management visibility is a separate explicit authorization. A report cannot be sent to all active users or every staff account by default. Recipient changes and revoked membership must be rechecked before delivery.
- Arrival is a self-reported event. Authentication login, a focus timer and browser activity do not prove physical presence, accepted work, hours worked or productivity. Do not automatically use arrival-to-close elapsed time as the staffed-hour denominator.
- Record server timestamps and show the business timezone. Define corrections and audit history; do not silently overwrite a declaration. Exact attendance/edit policy remains an owner input.

## Critical work and the operator

Confirmed: critical tasks are top priority. Apply this within the strategy's authorization, lifecycle, readiness and capacity constraints. Blocked critical work belongs in Waiting with a visible unblock/escalation action; it cannot be presented as executable work when its prerequisite is missing. Approved urgent changes may interrupt current work under an explicit policy. Routine refreshes should preserve focus.

Managers should see why important work is waiting and who can resolve it. Do not use a critical flag to hide missed deadlines, overload or impossible assignments.

## Arrival and close-day reporting

The interface needs explicit **Register arrival** and **End day** actions. Keep these separate from ordinary login and task timers. Existing daily planning/reflection screens are reusable entry points, not proof that attendance or reporting is already implemented.

Required report facts should come from authorized records: employee/team, reporting date, declared arrival where permitted, accepted completions and outcomes, remaining commitments, blockers/resolver/age, deadline risks, next steps and whether an employee has closed their day. Label missing information and unsubmitted end-day reports explicitly. Private reflection text must not be included.

Generate a versioned report snapshot through the shared event/outbox/job foundation. Use idempotency, deduplication, retry/failure visibility and recipient authorization at send time. A plain factual report must remain available without AI; optional AI narration is subject to privacy, validation and budget controls.

Confirmed delivery sequence: **email first; WhatsApp later**. Email is now a selected requirement, superseding the original playbook's blanket optional classification for external channels. Generic push/chat, calendar, voice and offline remain conditional.

Still open: named top managers and team managers; actual memberships; sender/provider; report recipients; per-employee versus consolidated team delivery; closing cutoff; handling late edits, weekends and missing submissions; whether a scheduled fallback is wanted; retention; delivery-cost budget; production worker/scheduler owner. Do not invent these answers or send live messages during implementation. Build and test against fake recipients and mocked providers until live activation is separately authorized.

## Weekly AI spending policy

The owner explicitly confirmed **$0.50 per employee per week**. This replaces the previously unresolved budget period. It is a monetary limit, not an interchangeable number of tokens or requests.

Implementation requirements:

- Account for every employee-attributed AI path, including interactive actions, background work, retries, voice if later selected, and model/tool charges where applicable. Define attribution for manager/team reports and automatic work instead of silently charging an arbitrary employee.
- Use configured, versioned pricing and exact monetary arithmetic. Check and reserve the maximum authorized cost before sending a provider request; settle against actual usage, and handle unknown usage or failed jobs conservatively. Concurrent requests cannot each spend the same remaining allowance.
- Keep the weekly cap separate from request/token/rate limits. Show spent, reserved and remaining amounts to the employee; expose scoped cost aggregates to authorized administrators without leaking private content.
- Preserve an independently configured company-wide ceiling. Per-user caps do not replace the company budget. A deterministic fallback keeps capture, focus, factual reporting and next-action guidance usable after a cap is reached.
- Reuse one governance path across features. Do not enable paid calls merely because a budget requirement has been written down.

Still open: explicit AI-budget currency confirmation (the sales goal is now confirmed USD); calendar week versus rolling seven days; reset day/time; pricing and exchange policy if not USD; company ceiling; automatic report attribution; model choices; retention and who may change budgets. A calendar week starting Monday 00:00 Africa/Harare is a possible implementation default for review, not an approved production setting.

## Pilot and evidence

Use a small explicit allowlist of existing employees; do not enroll every registered user. Employee IDs, manager, roles, pilot start date, comparison and observation window remain unprovided.

Before changing employee behavior, record role-specific accepted outputs, staffing/effort definitions, quality and workload mix. Compare accepted output per staffed hour with a baseline or suitable comparison, alongside cycle time, blocked time, rework, deadline reliability, overtime, administration burden and costs. The original greater-than-70% productivity improvement is a hypothesis to test. Arrival records, task counts, timers and a completion percentage cannot demonstrate it.

## Candidate measures to agree with the owner

| Objective | Possible measures to discuss; no targets approved |
| --- | --- |
| Financial growth | Recognized revenue versus cash collected; gross margin and returns alongside the selected $2m metric; direct links to approved financial/source evidence. |
| Printing supply leadership | Sales or market share in a defined geography/category; availability and stockouts; on-time fulfilment; repeat customers; accepted service outcomes. |
| African AI bridge | Paying businesses using deployed solutions; retained customers; verified customer hours/cost saved; successful automations/agent runs; SaaS revenue and service quality. |

## Decisions and remaining owner actions

| Item | Status on 21 October 2026 |
| --- | --- |
| Three strategic directions | Recorded; USD 2m cumulative sales over two years confirmed; printing/AI leadership definitions partly pending. |
| Team-manager work visibility/delegation | Confirmed; identities and memberships pending. |
| Arrival action and critical-first policy | Confirmed; hours/correction/escalation details pending. |
| End-of-day reporting | Required; report audience, cadence and operating details pending. |
| Channel order | Confirmed email first; WhatsApp later. |
| Employee AI limit | Confirmed $0.50 per week; currency/reset/company policy pending. |
| Pilot | Confirmed small existing-employee cohort; names and measurement plan pending. |
| Previously identified credential revocation/removal | Owner action; not inspected, modified or claimed complete by this preparation. |
| Disposable PostgreSQL service | Current probe unavailable; recovery/verification tracked in implementation status. |
| Live deployment, sending or paid-provider calls | Not performed by this preparation. |

Unresolved business definitions do not prevent Stage 1 local correctness work. They do prevent inventing live policy, publishing measured achievements or declaring rollout readiness.
