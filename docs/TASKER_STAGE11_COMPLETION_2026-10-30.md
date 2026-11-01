# Stage 11 — operational source integrations

Local implementation, 30 October 2026, Africa/Harare. This continues the verified [collections](TASKER_STAGE11_COLLECTIONS_IMPLEMENTATION_2026-10-30.md) and [replenishment](TASKER_STAGE11_REPLENISHMENT_IMPLEMENTATION_2026-10-30.md) checkpoints. Production activation and employee rollout are separate actions.

## Implemented scope

| Workflow | Current source and next action | Completion authority |
| --- | --- | --- |
| Collections and promises | Existing Credit Control ownership, follow-up and promise lineage | Credit Control; a promise or completed follow-up does not establish collected money |
| Replenishment | Reservation-adjusted available stock; explicit company and procurement owner | Inventory availability clears the need; clearing is not purchase receipt or an accepted output |
| Shipments | Explicit owner/team, priority, ETA, clearance, receiving and close guidance | Shipment status and original Inventory receiving/landed-cost controls |
| Repairs | Explicit accountable owner/team, SLA date, inspection/resolution and handoff checklist | Source case must close; a source-authorized manager explicitly accepts quality against the current inspection/resolution proof |
| Stocktake approval | REVIEW action assigned to an employee with canonical approval capability | Inventory approval and approved-by evidence; Tasker cannot reconcile inventory |
| Sent quotations | Explicit sales owner, next-contact date and contact interval required before enabling | SENT remains active; recording contact advances the follow-up using its policy; conversion stays in Sales |
| Paid shop fulfilment | Both the current order and current payment must support fulfilment | Shipped status at the original source, with current verified payment |
| Supplier bills | Draft review plus posted/partially paid bills awaiting payment | PAID at source; posting alone does not clear the due-payment review |
| Fiscal close | Assigned close review, period deadline and bank/AP/AR/stock/journal checklist | FiscalPeriod remains authoritative; checklist ticks cannot close or unlock a period |

`operations` is the IMS source workspace for responsibility metadata where the older records lack ownership/company/team/policy fields. One typed, allowlisted binding exists per native record. It stores source-only review notes and audit receipts, and does not create Tasker Tasks, purchases, stock movements, payments or journals. BusinessRecordLink points to that stable responsibility; its original native record remains accessible through the authorized source workspace.

No owner, team or company is inferred from a creator, last editor, technician, customer or a shared Product. Finance records must retain their native company. Reassignment requires a qualified active employee in the responsible company and, when mapped, team. Company managers can assign within their explicit company grants. Team managers with the required source and Operations permissions can delegate within their managed teams, without company-wide delegation authority.

## Navigation and guidance

- IMS: **Replenishment → Operational responsibilities**, or `/operations/` on the IMS host. Choose workflow/company and **Set responsibility**. Existing source permissions plus `operations.view_responsibility` are required; configuration additionally requires `operations.manage_responsibilities` and company/team management authority.
- Tasker: **Business actions** shows current, authorized source facts, with independent source filters and pagination. Source permissions are checked again on opening. Notes, contacts, costs and financial totals are excluded from Tasker projections and reminders.
- The operator compares ready operational work with its selected Tasker task by critical/high/medium/low priority, overdue state and deadline. Future follow-ups are filtered before the bounded shortlist. An active Focus remains sticky. Source work opens a review at the source; it does not start a fabricated Tasker timer or reserve unknown effort.
- The strategy board places source actions in the explicitly recorded employee/team lane. Additional memberships do not duplicate an action. Unmapped or undisplayed allocations are shown separately. Source actions do not invent time estimates or enter native-task capacity totals.
- Source changes cancel stale notices. Repair scans also produce a targeted in-app due/overdue cue at most once per company calendar day. A same-day assignment/change notice supplies that day's cue. Existing quiet-hours, work-hours, focus suppression and delivery authorization remain in force. No manager/team broadcast, email or WhatsApp send is introduced.

## Goal and outcome acceptance

Source-authorized managers can map a business action to a live team and KeyResult from its company. Operations team attribution must match its source responsibility. An accepted source-backed output requires a completed current source, a current responsible employee, matching scope, a source-defined completion date, and the existing goal acceptance permissions and evidence policy.

The supported automatic value is **one reviewed operational output**, using a SUM result measured in `outputs` or `accepted outputs`. It cannot populate USD revenue, collections, stock received or a productivity uplift. Stock-need clearing and promise status are deliberately ineligible. Shipment closure, stocktake approval, repair quality acceptance, quotation-to-invoice conversion and fiscal lock use their dated source evidence. Shop Order and SupplierBill now record `status_changed_at` when a subsequent native save enters shipped/paid status, and clear it on a native departure from that terminal state. Old and bulk-imported terminal states remain undated; an intermediate paid-order/posted-bill save cannot supply a false shipping/payment completion date. Historical evidence uses its source date, not the date Tasker first observes it.

There is one retained outcome evidence record per canonical action. Replays cannot create a second credit. Reopening, reassignment, source deletion, changed evidence/scope, or adapter disablement excludes invalid credit immediately from goal calculations and report validation; repair updates the retained acceptance ledger to Retracted. Changed retained evidence requires explicit review/retraction and cannot silently earn a replacement credit. This conservative policy does not treat recurring work as additional output; recurring workflow design belongs to Stage 12.

## Migrations and rollout

New source app migrations: `operations.0001_operational_responsibility` through `0003_responsibility_audit_snapshot`. Tasker migrations `0026_business_outcome_evidence` through `0028_source_completion_evidence_alignment` depend on the required source migrations, including `shop.0003_source_status_timestamp` and `accounting.0012_source_status_timestamp`. Existing collections/replenishment dependencies remain. No legacy source owner, company, date, financial amount or accepted output is backfilled by guesswork. Earlier local audit request hashes are retained with the historical native-version slot explicitly unknown. Those legacy receipt snapshots also remain unknown; later receipts retain actor, current owner/team/company, priority, follow-up/SLA policy and checklist metadata without note bodies.

Use the reviewed deployment procedure, a verified private backup and the complete forward migration plan. Do not reuse the old recovery script's hard-coded Tasker 0020 target. Before activation, inspect `manage.py migrate --plan`, then run the complete reviewed migration set, system checks, role setup and strict manifest collection. This document does not authorize or perform production deployment.

All adapters default off. Existing flags remain:

```text
TASKER_COLLECTIONS_ADAPTER_ENABLED
TASKER_REPLENISHMENT_ADAPTER_ENABLED
```

The additional independent flags are:

```text
TASKER_SHIPMENT_ADAPTER_ENABLED
TASKER_SERVICE_ADAPTER_ENABLED
TASKER_STOCKTAKE_ADAPTER_ENABLED
TASKER_QUOTATION_ADAPTER_ENABLED
TASKER_FULFILMENT_ADAPTER_ENABLED
TASKER_BILL_ADAPTER_ENABLED
TASKER_PERIOD_ADAPTER_ENABLED
```

Grant source permissions deliberately through existing administration, along with the Operations capabilities. Tasker roles alone do not grant financial, stocktake or repair access. Configure company/team membership, owners and quotation/SLA policies before enabling the relevant source. Enable only the pilot workflows and run the existing scheduler/worker with the same settings. Event hooks optimize freshness; bounded fair source/link sweeps repair missed hooks, changes made in bulk, restarts and removed sources. Failed/Cancelled job budgets remain intact; recovery does not erase their receipts.

To roll back a slice, disable its flag and keep repair running. UI, source opening, notification visibility and dispatch fail closed immediately. Projection retirement touches only projection/attention state; disabled adapters do not lock or hydrate native source records. Source operations and business history are retained. Re-enabling reconciles into the original unique link. Accepted-output credit is conservative while an adapter is disabled and needs explicit acceptance review where its retained evidence was retracted. Do not reverse business migrations or delete audit receipts to undo a rollout.

## Verification

- Full regression checkpoint: **1,348 Python tests passed in 913.290 seconds**, covering Tasker, config, Inventory, Credit Control, Sales, Shop, Accounting and Operations on disposable PostgreSQL port 55441. An earlier integrated source checkpoint passed 160 tests.
- **77 JavaScript tests passed** with Node's test runner.
- Strict WhiteNoise compressed-manifest collection passed: 225 files copied and 1,047 post-processed, including the Operations stylesheet. System and migration-drift checks passed.
- **460 final focused tests passed in 333.749 seconds**, including the later source reminder/audit/date/priority changes, source scopes/lifecycles, PostgreSQL concurrency/lease checks, Inventory, Shop, Accounting, goals, reporting, operator and board regressions.
- The source/board/scenario checkpoint passed 117 tests; a later source/board/scenario/operator checkpoint passed 147 tests. The final acceptance run passed **192 tests in 191.113 seconds**, covering the final source-date guards, source-only evidence views, operator presentation, critical source counts, goal-linked source counts, native Shop/Accounting and the board's JSON-safe fact contract.
- Desktop/mobile walkthroughs verified the source responsibility form, review save, qualified employee/priority controls, ten readable business actions including all seven new workflows, integrated Now guidance and 390-pixel layouts without horizontal overflow. The manager's filtered board showed the seven actions once in Asha's explicit operational team, including one critical source action; saving the shipment's goal mapping produced one linked source action with no accepted-output/progress claim. Screenshots: `output/stage11-operations-source.png`, `output/stage11-operations-mobile.png`, `output/stage11-complete-business-queue.png`, `output/stage11-complete-manager-board.png` and `output/stage11-complete-goal-mapping.png`. Mobile testing found and corrected the disabled source-field contrast.

Exact broad command:

```powershell
$env:IMS_TEST_PORT='55441'
.\venv\Scripts\python.exe manage.py test tasker.tests config.tests inventory credit_control sales shop accounting operations --settings=config.test_settings --noinput --failfast
```

Later refinements receive their own focused run: source due reminders, dated status evidence, conservative accepted-outcome validity, audit snapshots, priority, unpaid bills and future-follow-up filtering. Logs are under `output/stage11-*.log`; synthetic browser screenshots are also under `output/`. Preview-only settings and fixtures are in `tmp/` and must not be deployed.

## Remaining release work

The next implementation stage is Stage 12 (continuous improvement/SOPs/recurring workflows). Stage 13 retains the employee pilot, representative scale testing, usability sessions, production rehearsal and measured productivity evaluation. A few registered employees, real managers/recipients, source permission grants and real follow-up/SLA policies still require owner setup. Live OpenAI evaluation/activation remains deferred at the user's request. No 70% uplift has been established. This stage does not commit, push, migrate production, call a paid provider or send external messages.
