# Stage 11: stock replenishment and procurement responsibility

Prepared 30 October 2026, Africa/Harare. **Replenishment review and procurement responsibility are verified locally. Stage 11 remains in progress.** This is the second source-adapter slice, following the [locally verified collections core](TASKER_STAGE11_COLLECTIONS_IMPLEMENTATION_2026-10-30.md). Seven other adapters and the broader accepted-outcome/prioritization work remain open.

## Source and ownership contract

The existing Product represents one global stock pool. It has on-hand quantity, reserved quantity and a reorder level, but no company ownership or procurement employee. A new `inventory.ReplenishmentPlan` provides **one standing responsibility per global Product**, enforced by its one-to-one product reference. A manager explicitly selects the responsible company and procurement employee. This does not partition inventory by company or establish legal stock ownership. Supplier, creator and employee membership are never guessed as the responsible company or owner.

The source plan contains an enabled flag, review step, optional next-review date, standing procurement notes, revision and observed need/cycle facts. New mappings start disabled until deliberately enabled. An owner can remain explicitly unassigned for manager triage; an inactive employee is not replaced by the creator. One configured product has one source-linked Tasker action, rather than a duplicate Tasker task with an independent completion checkbox.

The source workflow is a **replenishment review**. Its steps are Needs review, Reviewing replenishment, Waiting for a procurement decision and Following up expected stock. Recording these steps does not approve a purchase, place an order, confirm a supplier commitment, receive goods, move stock, post a payment or accept a financial/productivity outcome. Existing stock-movement, serialized-receiving, shipment and finance services retain those authorities.

## Current stock and lifecycle

| Raw stock need | Meaning and behavior |
| --- | --- |
| `LOW` | Active, inventory-tracked product with valid raw records and `quantity - reserved <= reorder_level`. Equality counts as low stock; a zero reorder level still detects a stockout. A zero available quantity is critical; other low stock is high priority within the business queue. |
| `CLEAR` | Valid available stock is above the reorder level. The source action becomes **Replenishment need cleared**. Released reservations can cause this, so it is not evidence of a purchase received. |
| `INVALID` | Negative on-hand/reserved/reorder values, or reservations above on-hand stock, need source review. They are preserved and not treated as an ordinary stockout or automatically corrected. |
| `INACTIVE` | The product is inactive or is not inventory tracked. It has no actionable replenishment need. |

The plan's enabled flag is separate from raw stock truth. Pausing a plan suppresses its Tasker action; it does not change stock. The raw product can still be low while its review is paused. An inactive company or missing/inactive owner also prevents an ordinary ready assigned action.

Source GETs calculate current availability and effective state without updating observations, creating events or enqueueing jobs. Reconciliation records observed need transitions. A renewed observed low-stock cycle resets the review step and prior cycle's next-review date; standing source notes remain available as context. Repeated observations of unchanged facts do not duplicate accepted work or action events. No target quantity, order value, delivery deadline or procurement approval is invented.

## Permissions and source pages

Source access requires current `inventory.view_replenishmentplan`, the existing Product-read authority (`inventory.view_product`, including its established Staff read exception), an active actor and current company membership/grant. Tasker permissions or a Tasker management title alone do not grant inventory access.

An assigned employee needs `inventory.change_replenishmentplan` to update the source review. Configuration/reassignment requires the explicit `inventory.manage_replenishment` capability and authority to manage the responsible company. A company transfer rechecks both the existing and proposed source scope. Eligible procurement employees need current company scope and source read/change capabilities. Source writes recheck authority after acquiring stable locks.

Inventory supplies the source-authorized review list, product detail, explicit configuration and progress pages. Forms require source revision/action keys, and progress also requires the current raw-stock proof. Stale submissions retain the draft and reject an unreviewed write. The read-only Django admin shares source scope and links to the canonical Inventory workflow; it cannot bypass the versioned services or perform stock/finance operations.

Setup in a separately approved staging/pilot environment:

1. Configure the real company/team membership and explicit source permissions for the procurement manager and employees. Existing Tasker roles do not silently receive these inventory capabilities.
2. Open **Inventory → Replenishment → Set responsibility**.
3. Choose the responsible company, then select the unmapped product and eligible procurement owner. A validated product preselection survives the company-selection step.
4. Deliberately enable the source review, enter a next-review date if known and save any standing procurement notes. Leaving the owner blank creates an explicit manager-triage item, rather than inferring responsibility.
5. After matching code/migrations and verification, enable the Tasker replenishment adapter for the approved pilot. Open **Tasker → Business actions → Stock replenishment**; continue through **Open in Inventory** to record the source review.
6. Use the existing authorized receiving/stock workflows for actual stock changes. Availability then clears or reopens the source need. If a review is missing, inspect **All configured products**, enabled state, active stock tracking, source permissions and current owner/company scope.

These are setup instructions, not evidence that production permissions, real employee mappings or activation have been configured.

## Tasker presentation and privacy

Tasker shows the authorized product title, responsible employee/company, review deadline, priority and source lifecycle. Product identity is operational context; supplier contacts, purchase costs, prices and standing procurement notes are excluded from Tasker action/event/reminder bodies. Inventory source notes remain visible only to source-authorized colleagues and are not private personal reflections.

The shared Business actions list can contain independently enabled collections and replenishment sources. Filters, source labels and canonical IMS redirects follow the relevant adapter. Now/Work show a bounded assigned business queue. When the ordinary Tasker queue has no next move or active Focus, its guidance points to the existing business queue. Full combined ranking with ordinary Tasker tasks and management-board team attribution remain pending Stage 11 work.

Current source scope and live stock facts govern reads and reminder visibility, even while background projections lag. Tasker GETs do not reconcile a source or post a source change. Tasker has no stock completion/quantity/payment controls, and an arbitrary request URL cannot replace the canonical Inventory source destination.

## Jobs, versions and repair

The existing Stage 8 outbox handles `business.reconcile` with only registered `source_type='replenishment_plan'` and integer `source_id`. Product/plan hooks are prompt signals; they are not the only correctness mechanism. Reservation creation/release can use bulk updates that bypass Product signals and timestamps, so bounded rotating source/link sweeps repair missed changes.

Raw-stock proofs cover quantity, reserved quantity, reorder level and active/tracked flags. Presentation, source writes and notification dispatch check current facts rather than trusting a timestamp or cached owner/status. SQL selection uses the same raw stock/version evidence; the existing low-stock catalogue and new availability comparisons use safe wider arithmetic for valid boundary counts.

Source decisions use actor → Product → Plan → Company ordering. Source reconciliation follows Product → Plan → Company before link/notification rows; the shared worker retains its late lease fence. This slice does not acquire shipment/identity locks or invoke receiving from replenishment reconciliation. Replays preserve one action, immutable source/observed-transition receipts and bounded failure budgets. Failed or deliberately cancelled jobs are not silently revived; completed no-op history is retained when a fresh deduplicated repair is needed.

Reminders are targeted in-app attention for the current assigned employee, subject to existing quiet/work/focus settings and current source permission. Cleared/paused/reassigned/revoked work cannot retain actionable stale reminders. SMTP, WhatsApp and paid AI are not activated.

## Schema, flags and recovery

- Inventory migration `0019_replenishmentplan_replenishmentevent_and_more` adds the explicit responsibility and source-event schema.
- Tasker migration `0025_replenishment_adapter` is an explicit dependency on the matching Inventory schema after Tasker 0024. It does not create a second stock ledger.
- `TASKER_REPLENISHMENT_ADAPTER_ENABLED` defaults **false** and is independent of `TASKER_COLLECTIONS_ADAPTER_ENABLED`. One flag does not enable the other source. If both are off, shared Tasker navigation/panels do not read the source tables.
- Source Inventory pages retain their own permission boundary independently of the Tasker flag. A per-product plan can be paused separately through its enabled field.
- Disable the Tasker replenishment flag to hide this source and retire its projections/reminders through bounded reconciliation. Keep the collections adapter's chosen state. Retirement does not change Product stock or canonical procurement decisions.
- Preserve additive schema, source receipts, cycles and outbox deduplication history. Do not reverse historical stock/finance state or purge evidence as routine recovery. Review the actual migration plan, backup and source-role setup before any separately authorized deployment.

No production migration, real ownership assignment, Git push, deployment, paid provider request or live external send is performed by this implementation.

## Verification checkpoint

Python compilation, Django system checks and scoped whitespace checks passed for the implemented UI/source surfaces. The earlier combined focused run passed **223 tests**, covering new source behavior and existing collections/business/UI/jobs. That run preceded the last catalogue boundary/preselection regressions and is not the final whole-change certificate.

The UI suites now contain 47 cases across canonical Inventory and Tasker surfaces. They cover company/source/Tasker permission separation, CSRF, current raw availability and bulk reservation release, missing/inactive responsibility, source-only note/cost privacy, read-only GETs, stale draft retention (including read-only retained drafts after a need clears or a plan pauses), source/action replay, admin restrictions, canonical redirects, independent rollout flags and safe large-count arithmetic. Backend/engine tests additionally exercise lifecycle, repair, job leases, source concurrency and absence of stock/financial side effects.

The fresh coordinated PostgreSQL run passed **1,313 tests in 772.649 seconds** across Tasker/configuration, Inventory, Credit Control, sales, shop and accounting. After the final source-list priority, date-label and contrast refinements, **107 focused source/engine/UI/static tests passed in 72.395 seconds**, including strict compressed-manifest asset collection. **77 JavaScript tests passed.** System checks, migration-drift checks and scoped whitespace checks passed. Inventory 0019 and Tasker 0025 applied successfully to the dedicated loopback `ims_review` database. An existing AI privacy test fixture was made deterministic with explicit ordered start/completion timestamps; production AI behavior and its timing constraint were unchanged.

Browser evidence used synthetic products, users and separate local Tasker/IMS hostnames. It verified source login/permission denial, reservation-adjusted stock, saving a next step and follow-up date, manager assignment of an unassigned product, immediate personal-queue ownership changes, simulated raw reservation release without timestamps/save hooks, need clearing, a retained read-only draft after a stale-stock rejection, and one reference through repeated clear/reopen observations. Replay created no extra source actions or stock movements, financial rows, native Tasker tasks or accepted outcomes. Procurement progress and private standing notes remained at the Inventory source.

Desktop and 390px viewport checks passed for Tasker and Inventory; both narrow pages had matching 375px content/client width with a scrollbar. Source cards and warning text were checked visually and corrected to match the existing IMS dark palette. Warning text measured `rgb(102,77,3)` on `rgb(255,243,205)`. Critical stockouts lead the default low-stock source list and Tasker business queue. The default viewport was restored. Screenshots include `output/stage11-stock-tasker.png`, `output/stage11-stock-owner.png`, `output/stage11-stock-mobile.png`, `output/stage11-stock-tasker-mobile.png`, `output/stage11-stock-conflict.png` and `output/stage11-stock-validation.png`.

Native accessibility, real employee usability, production scale/latency and pilot productivity remain separate gates. Earlier collections/Stage 10 counts retain their original checkpoint meaning and are not substituted for new replenishment verification. No real procurement owners or production permissions were configured. Keep deployment/activation separate; public collected Inventory assets, including the new CSS, must be readable by the web server without changing private configuration permissions.

## Remaining Stage 11 work

| Pending adapter | Required source contract |
| --- | --- |
| Shipment ETA, clearance and receiving | Explicit current responsible employee/company scope and action-specific lifecycle, preserving receiving authority. |
| Service/repair | Technician/case scope, deadlines/SLA, handoffs and accepted quality evidence. |
| Stocktake REVIEW approval | Authorized reviewer/company/deadline mapping with the existing approval boundary. |
| SENT quotation follow-up | Explicit sales owner and next-contact/duplicate-contact policy before automation. |
| Paid shop-order fulfilment | Responsible fulfilment employee and actual fulfilled/shipped evidence. |
| Supplier-bill review | Finance reviewer, current scope and existing approval/payment authority. |
| Fiscal-close checklist | Scoped period responsibility and authoritative finance evidence. |

Automatic accepted-outcome mappings, integrated operational prioritization and management-board attribution also remain open. A source need clearing is not automatically an accepted purchase or company-goal outcome. Stage 12 routines and Stage 13 pilot/measurement remain later work. The requested 70% productivity improvement is a pilot target, not an established result.
