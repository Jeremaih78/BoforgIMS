# Stage 11: collections integration slice

Prepared 30 October 2026, Africa/Harare. **Stage 11 is in progress.** This slice connects Tasker to Credit Control collection follow-ups and payment-promise review. It does not complete the other IMS adapters in the [requirements matrix](TASKER_REQUIREMENTS_MATRIX.md).

Subsequent 30 October work adds the independently gated [replenishment review slice](TASKER_STAGE11_REPLENISHMENT_IMPLEMENTATION_2026-10-30.md). The collections verification below retains its original checkpoint meaning; current stage status and remaining-adapter counts are in the implementation tracker and the later report.

## Implemented boundary

- `BusinessRecordLink` identifies one registered source application, source type, record and action. Its unique identity prevents retries from creating a second action. `BusinessLinkEvent` retains compact reconciliation evidence; the link is a reference, rather than a second `Task` that could be completed independently.
- CollectionTask remains authoritative for follow-up ownership, deadline, priority and lifecycle. PromiseToPay has an explicit nullable responsible employee. Existing creators are not silently assigned responsibility. A known CHECK_PROMISE relationship suppresses the separate promise action, including after that check is completed. A legacy check without a known promise reference needs source review; no relationship is guessed from customer/date proximity.
- Tasker requires its own view capability **and** current Credit Control permission and object/company scope. Current company membership/grants, active companies and active actors govern access. A legacy null-company record is owner-scoped except for an explicitly privileged operational administrator. Tasker role membership alone does not grant financial access.
- Business actions show generic work titles, responsible employee, deadline, priority and lifecycle. Customer identities, contact details, amounts and source notes are not copied to Tasker. Every page and source opening rechecks source authority. Delayed projections cannot preserve access after revocation or override current assignment, status or priority.
- The Business actions list supports assigned/all-readable scope, active/closed/all lifecycle and source filters, with 24 rows per page. Detail explains source authority and the background snapshot timestamp. Now and Work show up to five current assigned actions, critical first within this separate business queue. When no ordinary Tasker next move or active Focus exists, Now directs the employee to review the business queue. These are not combined into a single ranking with ordinary Tasker recommendations.
- Source opening uses the canonical Credit Control detail route on the IMS hostname. An arbitrary request URL or `next` parameter cannot replace that destination. Tasker pages expose no source-completion checkbox, payment posting or outbound communication action.

## Canonical source workflow

Credit Control detail pages retain the decision and its evidence. Follow-up start/complete/reopen/cancel and metadata changes use current source permission, a source revision and an idempotent action receipt. Assignment changes require the appropriate source-management authority and a currently eligible employee. Payment-promise responsibility can be assigned explicitly; its linked canonical check follows that responsibility.

Completing a collection follow-up records completed work. It does **not** establish payment receipt, change actual-paid amounts, create a journal entry or automatically award a company goal outcome. Promise financial state remains Credit Control's authority.

Source-owned records, revision/receipt fields and link/event/cursor tables use additive migrations: Credit Control `0003_sourceactionevent_collectiontask_promise_to_pay_and_more` and Tasker `0024_businesssynccursor_businessrecordlink_and_more`. Tasker 0024 explicitly depends on the source migration. No migration invents company membership, assigns real employees, guesses legacy lineage or changes historical financial findings.

Known check responsibility requires an employee who can read both the promise and the check. Incoherent company/customer/invoice lineage is rejected before reassignment. If automatic check creation would assign unreadable work, the check is left uncreated and the standalone promise review remains available. The source admin applies the same scope, revision and lineage rules.

## Delivery and reconciliation

Source changes enqueue the registered `business.reconcile` job through the existing Stage 8 outbox. Payloads contain only a registered source type and integer source identity. Worker handling reads the current source; an old queued event cannot overwrite a newer source state. Reconciliation is idempotent, records source state changes and repairs missed hooks through a bounded, rotating scheduler scan.

Current source assignment drives the personal queue. Missing or inactive owners need review; they are not reassigned to the creator. Deleted or unavailable sources cease to be actionable. Known duplicate promise/check work stays suppressed. Current source proofs are rechecked before business notifications are presented or delivered; stale assignment, status or permission cannot keep an obsolete reminder active.

Business action GET requests do not reconcile records, enqueue jobs or change source work. When `TASKER_COLLECTIONS_ADAPTER_ENABLED` is false, collections actions/reminders are hidden; shared Business actions routes can remain open for another independently enabled adapter. They close when all business adapters are off. No OpenAI key or provider request is required. AI activation and private replacement-key setup remain unchanged.

The existing local notification/delivery infrastructure is reused. This slice does not activate SMTP, WhatsApp or another live communication provider. A queued notification is not evidence of external delivery.

## Verification checkpoint

Python syntax compilation passed for the new UI/forms/tests. The UI suite covers source and Tasker capability separation, company boundary/revocation, safe generic presentation, live assignment/status/priority filters, known promise-check suppression, canonical IMS redirects, read-only GET behavior, bounded pagination, navigation and Now/Work panels. It includes an explicit feature-off assertion that no source-table query or panel build occurs.

The fresh PostgreSQL regression passed **1,116 tests in 554.458 seconds** across `tasker.tests`, `config.tests` and `credit_control`. This includes actual source-action and reference-creation races, lease rollback, missed-event repair, notification scope and all prior Tasker stages. A separate focused integration/job run passed **158 tests in 47.364 seconds**. **77 JavaScript tests passed.** System checks and migration drift checks passed; both additive migrations were applied successfully to the dedicated `ims_review` database on loopback port 55441.

The browser walkthrough used synthetic data and separate local AI/IMS hostnames. It verified list/detail access, the canonical IMS source redirect and source login, completing and reopening a follow-up through Credit Control, immediate active-list/reminder changes before a worker ran, and repeated reconciliation without extra references/events, Tasker tasks/outcomes or financial rows. Completion produced one observed transition; replay preserved it; reopening produced the next transition. A known promise/check stayed one active action.

Desktop and 390px viewport checks passed, including keyboard filter expansion/collapse and no horizontal overflow (375px content/client width with the scrollbar). Filters start collapsed so the mobile queue remains visible; validation errors open them. After the final list-selection race guards, mobile refinement and empty-native-queue guidance, **30 UI/static tests passed in 17.729 seconds**, including strict compressed-manifest asset collection. Screenshots: `output/stage11-business-actions.png`, `output/stage11-business-mobile.png` and `output/stage11-source-completed.png`. Native screen-reader/zoom and employee usability evidence remain later validation.

No production deployment, live-model evaluation or employee productivity result is certified by this checkpoint. Automatic Stage 2 accepted-outcome mapping remains pending an authoritative metric, evidence and acceptance contract. `BusinessLinkEvent` is an observed source transition, not an accepted monetary or productivity outcome. Cross-source operator ranking and strategy-board team attribution are also not claimed by this separate collections queue.

## Pilot operation and recovery

1. Review the complete pending change set and verify a production backup before any separately authorized deployment. Apply the reviewed additive migrations with matching code; run system/migration checks and strict production `collectstatic`. Public generated assets must remain readable by nginx, while private configuration/backups retain private permissions.
2. Keep `TASKER_COLLECTIONS_ADAPTER_ENABLED` false until the collections slice has passed its local tests and staging walkthrough. Verify real Credit Control permissions, company scope and responsible employees before enabling a pilot. Unassigned promises/checks need explicit source decisions. The flag controls Tasker projections, routes and reminders; canonical Credit Control list/admin scope hardening applies with this code even while the flag is off. Review existing finance users and legacy null-company/unassigned records before deploying the code. The migration does not grant access or guess mappings.
3. Reuse the existing supervised Tasker scheduler/worker. In a disposable or approved staging environment, the bounded one-pass commands are `python manage.py tasker_scheduler --once --limit 100` and `python manage.py tasker_worker --once --limit 25`. Check `python manage.py tasker_jobs_health` and inspect the source-authorized Business actions list. These shared commands can process other previously enabled job types; they are not an instruction to run against production now.
4. Check source reassignment, completion, reopen, permission revocation, source deletion, stale queued work and a missed-event repair. Confirm there is one responsible action and no financial write or live communication. Use synthetic data before a real employee pilot.
5. To stop this adapter, disable its flag. Collections items disappear from Tasker pages/panels, canonical Credit Control work remains authoritative, and disabled reconciliation can retire obsolete business projections. Other independently enabled adapters can keep the shared Business actions interface available. Preserve additive tables, action receipts, event evidence and deduplication keys. Do not reverse source financial state, unapply deployed history or purge evidence as a routine rollback.

## Remaining Stage 11 adapters

| Pending slice | Required source decision before implementation |
| --- | --- |
| Available-stock replenishment | Approved procurement workflow and explicit responsible owner; reorder thresholds alone are not an assigned action. |
| Shipment ETA, clearance and receiving | Current responsible employee/company scope and action-specific lifecycle. Creator, receiver and closer are historical roles. |
| Service/repair | ServiceCase has a nullable technician and OPEN/REPAIR/CLOSED lifecycle; company scope, deadline/priority, SLA, handoff and quality-acceptance contracts still need definition. |
| Stocktake REVIEW approval | Canonical approval remains restricted. Stocktake has creator/approver evidence, but no responsible reviewer/company/deadline mapping. |
| SENT quotation follow-up | Explicit sales owner, next-contact policy and source-based duplicate/contact rules. |
| Paid shop-order fulfilment | Responsible fulfilment employee and actual fulfilled/shipped acceptance evidence. |
| Supplier-bill payment review | Finance authorization, responsible reviewer and approved payment lifecycle; task completion cannot post payment. |
| Fiscal-close checklist | Scoped fiscal-period responsibility and authoritative finance evidence. |

Each later adapter needs its own flag, permission/lifecycle tests, replay/missed-event repair, stale-reminder behavior and recovery instructions. Stage 11 remains in progress until the listed slices and their acceptance evidence are addressed, including accepted-outcome mappings and integrated operational prioritization/manager attribution. Stage 12 routines and Stage 13 pilot/productivity measurement remain later work. The requested 70% productivity improvement is a pilot target, not an established result.
