# IMS reliability review — 2026-09-10

Work branch: `review/ims-reliability`. Existing user edits to
`tasker/README_AI_Extensions.md` are preserved. No production access or deployment.
No AGENTS.md was found in the repository or inspected ancestor directories.

## Architecture and scope

One Django 5.2/PostgreSQL application, WSGI/Gunicorn, django-hosts, WhiteNoise,
optional Redis and S3. Website, shop, IMS, AI and API use separate URLconfs;
localhost retains legacy paths. No separate FastAPI service is present.
Deployment guide predates the implemented host router; README describes it better.
Products/categories/suppliers/customers, expanded combo lines, quotations,
invoices/payments, reservations, shipments/landed costs/serials, accounting,
credit control, Tasker/planning/focus, WhatsApp links and Paynow are implemented.
Tasker has explicit model/object policies, persisted time entries and AI gateway
boundaries. There is no implemented lay-by/collection/refund lifecycle found.
Shipment receipt intentionally requires all outstanding items; partial receiving
is not supported. No cancellation/returns workflow for IMS invoices was found.

## Initial audit (before application edits)

| Severity | Files | Business impact | Proposed action |
|---|---|---|---|
| Critical | shop/views.py, shop/services.py | Forged payment callbacks/URL status; repeated callbacks deduct stock again | Verify provider status, lock order/payment, enforce monotonic state |
| Critical | inventory/api.py, config/host_urls/api.py; core views | Anonymous legacy API writes; any login can access sensitive operations | Shared backend permission policy preserving documented Admin/Staff groups |
| High | sales/services/__init__.py | Reservation per line overwrites repeated products; retries compare wrong available quantity; concurrent finalization | Aggregate demand, lock invoice/products, reconcile reservation deltas |
| High | sales/views.py, sales/forms.py | Posted invoice ID can differ; payment persists after failed stock finalization; mutable paid documents | Bind invoice server-side, atomic payment workflow, protect paid documents |
| High | inventory/models.py | StockMovement re-save applies stock twice, stale product overwrites, negative movements | Immutable movements and row locks with validation |
| High | shop/services.py | Concurrent carts oversell, checkout retries reserve twice | Lock cart/products, consume cart atomically |
| High | accounting/signals.py, accounting/services/posting.py | Exceptions silently swallowed; COGS repeats on instalments | Atomic posting and idempotent COGS, explicit failure |
| High | config/settings.py | Embedded secret/password defaults; debug enabled by default | Environment-only production secrets and fail-closed settings |
| High | accounting/services/posting.py, finance_dashboard.py | VAT totals disagree; reports use current product cost | Preserve totals pending VAT decision; document reconciliation and historical costing limitation |
| High | sales/models.py | Customer deletion cascades financial history; generated numbers race | Protect history; safe numbering strategy |
| Medium | inventory/views.py, sales/views.py | GET deletes product/converts quotation; conversion repeats | CSRF POST, locked repeat-safe conversion |
| Medium | templates/base.html, static/css/app.css | Crowded icon-only mobile nav, no content clearance, flex overflow | Accessible complete navigation, contained tables, responsive forms |
| Medium | requirements.txt | Old security patch versions, duplicate dependency | Audit advisories and apply verified compatible security patches |

## Phased checklist

- [x] Git/doc/code inventory and isolated PostgreSQL on loopback port 55439.
- [x] Baseline system check and migration drift: clean.
- [x] Baseline full test suite: 140 tests passed on PostgreSQL (191.589s with original password hashing).
- [x] Critical access/payment verification and configuration fixes.
- [x] Stock/payment/conversion concurrency and validation fixes.
- [x] Focused performance and maintainability improvements.
- [x] Shared responsive UX and representative browser workflows.
- [x] Regression tests, migration/deployment checks, final findings and rollout instructions.
- [ ] Owner decision: VAT-inclusive versus VAT-exclusive entered prices.
- [ ] Owner review of historical reconciliation and existing public attachment relocation.
- [ ] Ubuntu staging/native WeasyPrint, real provider sandbox and production configuration verification.
- [ ] Deployment, only with explicit owner authorization (not performed).

## Rules requiring business confirmation

The UI totals line prices without tax while accounting adds tax. Confirm whether
entered prices include VAT before changing either or reconciling historical data.
No lay-by implementation found; refund percentage basis remains unverified.
Moving-average costing with first-payment posting is retained, including existing
fallback behavior, until accounting policy is confirmed. Never recalculate old
financial history from current prices/costs without a reviewed reconciliation.

## Credential finding

**Release blocker:** the pre-existing user edit in
`tasker/README_AI_Extensions.md:2` contains an API key. The user edit was preserved;
the key was not used or tested. Treat it as exposed, revoke/replace it through its
provider, and remove it from the README before committing or sharing. Do not copy
its value into tickets, commits or logs. This is separate from the settings defaults
below. No credential rotation was performed.


`config/settings.py` contains a secret-key fallback and active/commented database
password defaults. Treat any deployed copies as exposed: owner must replace them
through the deployment secret store and assess session invalidation/history
cleanup. Values are intentionally not reproduced here. No credentials rotated.


## Implemented outcomes

- Shared backend permissions on IMS inventory, customers, sales, accounting,
  credit control and legacy/host APIs. Documented `Admin`/`Staff` groups remain
  valid; explicit Django grants are supported. Ordinary authenticated accounts
  cannot read financial/customer records or modify the catalogue. Tasker retains
  its established ownership and supervisor policies.
- Paynow return/callback parameters no longer establish payment truth. The server
  verifies the stored provider poll URL over HTTPS, checks order reference and
  amount, and atomically handles order/payment/stock transitions. Duplicate paid
  callbacks and late failed callbacks cannot deduct stock twice or undo a sale.
  Initiation and polling HTTP calls have connect/read timeouts and no redirects.
  Browser order lookup is bound to its session. Callback error responses leave
  records unchanged for retry/reconciliation.
- Checkout locks the cart and products in stable order, consumes cart lines in
  the same transaction, and generates numbers from the inserted order ID.
  A retry cannot create another order from the same consumed cart.
- Invoice reservations aggregate all component/direct lines by product. Repeated
  requests use reservation deltas; removed products release their reservation.
  Finalization locks the invoice/products, validates serial ownership and records
  an immutable stock movement once. A persisted finalization flag protects retries.
  Legacy paid invoices are never silently finalized again.
- Receipt forms use a unique submission UUID and take their invoice from the URL.
  Payments, stock, status and ledger postings commit together. Invalid/overpaid
  receipts are rejected, and invoices with payments/finalized stock cannot be
  edited through staff views. Existing signed discount adjustment lines remain
  supported. There is no new refund workflow.
- COGS posts once per invoice, preserving the existing first-payment timing and
  moving-average/price-fallback method. New first-payment postings snapshot unit
  cost; financial dashboard queries use that snapshot. Old lines remain NULL and
  retain the prior current-cost estimate until reviewed. Receipt journals now use
  the entered payment date, including backdated receipts. Date defaults now use the Harare business date,
  with a regression test across local midnight.
- Stock movements validate positive quantities/costs, lock fresh product state,
  preserve moving average across receipts, and reject re-saving an existing
  movement. Serial assignment is shared by HTML and API, locks affected units,
  rejects units assigned to another invoice, and protects finalized assignments.
- Quotations convert only via CSRF-protected POST and reuse the existing invoice
  on retry. Insufficient stock rolls the entire conversion back. Product deletion
  and collection reminders also require POST. Customer/payment/movement deletion
  protection preserves referenced transaction history.
- Expense forms validate amounts, exchange rates and supporting documents. Receipt
  UUIDs prevent repeated expense creation. Failed ledger posting rolls back the
  expense and displays an actionable error. Number sequences have uniqueness
  constraints and row locks so concurrent independent journals do not collide.
- Business attachments (expenses, shipment costs, debtor follow-ups, Tasker files)
  now use private storage and authenticated, non-cacheable attachment downloads.
  Tasker attachment access follows task visibility. Product images stay public.
  Existing public objects are not moved/deleted by migrations; see rollout below.
- Product edits cannot overwrite reserved/on-hand/average-cost fields. Staff stock
  changes use movements. Sensitive admin shortcuts for invoice/payment/reservation
  editing are disabled in favor of the transactional workflow. Stock movements
  cannot be changed/deleted in admin. This is intentionally stricter than before.
- Mobile navigation exposes all sidebar destinations through a labeled drawer.
  Phone product cards show availability, reservations and price. Shared forms have
  touch-sized controls, native date fields, keyboard focus and suitable input modes.
  Wide tables scroll within their containers; print CSS hides navigation/actions.
  Shop cart forms have native POST fallbacks, and shop product links stay within
  the current route configuration during local development.
- Sales list status evaluation uses prefetched lines/payments. Measured regression:
  **21 queries before versus 3 after for ten invoices**. This is a bounded query
  comparison, not a production latency or load benchmark.
- Removed embedded credential defaults and made production startup require a
  configured secret. Debug is now opt-in. Security-related dependency pins were
  updated in a separate virtual environment; the original `venv` is unchanged.

## Verification evidence

- Original baseline: **140 tests passed**, system checks clean, no migration drift.
- Full updated PostgreSQL suite: **172 tests passed** in 14.481 seconds. The isolated test settings use a fast test-only password hasher;
  do not compare this runtime with the baseline as an application speed benchmark.
- Threaded PostgreSQL tests cover two staff selling the last unit, competing carts,
  simultaneous duplicate payment submissions, and independent concurrent payments
  sharing accounting number sequences. They use separate database connections.
- Regression coverage includes stock/combos, rollback on failed ledger posting,
  idempotent receipts, first-payment cost history, backdated receipt journal dates,
  forged callbacks, permissions, private downloads, conversion retries and queries.
  Existing Tasker/focus/planning/AI tests remain intact; role fixtures were granted
  actual permissions instead of weakening workflow assertions.
- Chrome 152 headless: 12 representative pages at **360, 390, 768, 1024 and 1440px**.
  Initial sweep: 60 HTTP-200 page/width combinations, no page-level overflow.
  Screenshots were visually inspected; phone product layout was improved after
  that inspection. Final layout evidence is stored alongside this report.
- Browser actions at 390px: drawer open/close, product search/availability, customer
  creation, quotation creation/product search/quantity change, invoice creation,
  adding a line, recording full payment, full-shipment receipt, expense posting,
  finance viewing, focus start/refresh/pause/resume/confirmed completion, cart add
  and inspection of the encoded WhatsApp URL. No WhatsApp message was sent.
- Invoice, quotation and sales-summary PDF endpoints returned valid, text-readable
  one-page PDFs via **xhtml2pdf fallback**. Native WeasyPrint cannot load
  `libgobject-2.0-0` on this Windows machine; Ubuntu/native layout remains a staging
  requirement. PDF byte/text checks are not a claim of native visual print QA.
- Fresh database migration, reversal to previous app migration targets, and full
  reapplication passed on a second isolated PostgreSQL database.
- `check --deploy` passed with production flags and an ephemeral test secret;
  this does not verify actual VPS environment variables, Nginx or TLS behavior.
- `collectstatic`: 179 files collected. `pip check`: no broken requirements.
- Final dependency audit: **69 resolved packages, zero known advisory matches**
  from the queried advisory database. This does not prove an absence of unknown
  vulnerabilities. See [dependency audit](DEPENDENCY_AUDIT.md).
- `check_ims_integrity` reports no anomalies in the synthetic browser dataset.
  No production or historical customer database was inspected or repaired.

## Important remaining risks and next priorities

1. **Credential remediation and VAT decision required.** Remove and revoke/replace
   the exposed credential identified above before sharing or release.
   **VAT:** Line totals and ledger tax still disagree for
   nonzero VAT. The existing behavior was preserved; no historic balance was
   rewritten. Resolve inclusive/exclusive pricing and combo discount tax treatment
   before relying on VAT-bearing reconciliations.
2. **Review old financial/stock records.** Old repeated COGS, absent receipt
   journals, stale reservations and live-cost profitability estimates may exist.
   Run the read-only command below, compare to bank/stock/source documents, then
   approve individual correcting entries. Preserve original records and a dated
   audit trail. No automatic financial repair is supplied.
3. **Move existing public attachments.** New uploads are private, but old filesystem
   or S3 copies remain reachable until relocated and their public originals are
   removed by the owner. Back up and verify every path before removal.
4. **Stage integrations/deployment.** Paynow was tested with mocks; no live/sandbox
   payment was initiated. The timeout adapter intentionally depends on signing
   helpers in the pinned Paynow 1.0.8 SDK and has a contract regression test.
   Gateway initiation is still a session-bound GET legacy route; review a full
   provider initiation idempotency/POST flow before exposing a higher-volume shop.
   Background reservation expiry and late-payment reconciliation remain manual.
5. **Complete financial policy work.** Foreign-currency expense/report conversion,
   supplier-bill expense classification, duplicate legacy/AR payment representations
   and comprehensive correcting/refund entries need an accounting-policy review.
   Invoice models have no company/currency ownership, so legacy postings explicitly
   use the company-neutral chart; this is not a multi-company isolation guarantee.
6. **Lay-by is absent.** Deposit, collection, 60-day expiry and refund basis cannot
   be verified against an implementation. Do not advertise those controls as
   available. Tasker attachment metadata exists, but not every model has a complete
   staff-facing workflow. No missing module was invented.
7. **Further scale work.** Credit-control pages still synchronize all accounts in
   requests, and several detailed reports/catalogue APIs remain unbounded. Profile
   representative production-sized synthetic datasets, paginate/version API results
   without breaking consumers, and move summary refresh to the existing scheduled
   job only after defining freshness. No cache of financial/stock truth was added.
8. **Remaining audit depth.** Granular owner/manager/sales grants should be reviewed
   against actual staff membership. Application-level stock/payment paths are much
   safer, but a comprehensive price/permission/financial correction audit ledger,
   admin bypass review, upload malware scanning, API throttling and real reverse-
   proxy/header tests remain follow-up work. This review does not certify the whole
   application as secure or production-ready.

## Reproduce local verification

The test settings always use loopback PostgreSQL, database/user `ims_review`,
port 55439 (or `IMS_TEST_PORT`). They never select `DATABASE_URL` as their database.
The review created an isolated PostgreSQL 18 cluster under ignored `.local-review`.
Do not point these settings at production. Synthetic browser secrets, screenshots,
local logs and virtual environments are kept in that ignored directory.

```powershell
.\.local-review\app\Scripts\python.exe manage.py test --settings=config.test_settings --noinput
.\.local-review\app\Scripts\python.exe manage.py check --settings=config.test_settings
.\.local-review\app\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
.\.local-review\app\Scripts\python.exe manage.py check_ims_integrity --settings=config.test_settings
.\.local-review\tools\Scripts\python.exe -m pip_audit -r requirements.txt
```

## Safe deployment (not performed)

1. Review this branch and the unresolved policy/security items above. Keep the
   pre-existing Tasker README edit separate from this release review.
2. Record the current release/migration state and take a verified PostgreSQL backup
   plus public/private media and deployment configuration backups. Test restoration.
3. Prepare a fresh release virtual environment with `pip install -r requirements.txt`;
   run `pip check`. Install/verify the native WeasyPrint dependencies on Ubuntu and
   exercise invoice/report PDF printing in staging. Security upgrades include
   Django 5.2.17, Pillow 12.3.0, cryptography 50.0.1 and WeasyPrint 70.0; tinycss2 1.5.0
   is required by the updated renderer.
4. Require `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, real database credentials,
   allowed/trusted hosts and correct proxy/TLS settings. Replace exposed credentials
   through the owner's secret-management process; this review did not rotate them.
   Never use `config.test_settings` for deployment.
5. Set `PRIVATE_MEDIA_ROOT` outside every publicly served Nginx path, writable only
   by the application service and backup operator. Back up its contents thereafter.
   Copy existing referenced files into it while preserving relative paths under
   `expenses/`, `shipment_costs/`, `credit_control/followups/debtors/` and
   `tasker/attachments/`. Verify authenticated downloads and unauthorized denial
   before removing public originals. For S3, inventory/copy/verify old objects before
   changing public access. Migrations change storage metadata, not file contents.
6. In staging, then an approved maintenance window, use the deployment environment:

   ```bash
   python manage.py check_ims_integrity > integrity-before.json
   python manage.py migrate --plan
   python manage.py migrate --noinput
   python manage.py check --deploy
   python manage.py collectstatic --noinput
   ```

   Duplicate/nonpositive reservations or duplicate number-sequence rows can block
   new constraints. Stop and review them; do not delete them or fake balancing data.
   Eleven review migrations add receipt keys, finalization/cost metadata, protections,
   constraints and private storage. They do not recalculate historical money/stock.
7. Run staff/outsider access checks on every host; test a synthetic sale, payment
   retry, receipt, expense, private attachment and Tasker timer in staging. Verify
   provider callbacks against the provider sandbox with owner-approved test keys.
8. Only after approval, switch the service to the reviewed release and reload it.
   Monitor application errors, payment verification failures and integrity reports.
   The repository deployment handbook predates current hostname routing; use the
   actual `config/host_urls` configuration, not its obsolete reserved-API diagram.

## Rollback

Before accepting new traffic, the review migrations can be reversed using the new
code and environment, in the tested order: `sales 0006`, `inventory 0011`,
`accounting 0007`, `credit_control 0001`, `tasker 0009`. Then restore the previous
code/dependency environment. Keep maintenance mode active: the old release has the
reported security defects. Restore/copy private files to the storage expected by
that release only through the reviewed backup plan.

After new receipts or stock finalizations, **do not blindly reverse migrations**:
that drops idempotency keys/cost snapshots/finalization metadata and can enable
repeat transactions. Prefer a forward fix. If restoring a backup is necessary,
review all transactions since the backup and reconcile them before reopening.
Simply switching old code while keeping all new columns is not guaranteed to work;
some non-null fields use application defaults. No rollback or deployment was run
against production.


## Browser evidence

[Phone inventory](review-evidence/inventory-390.png),
[paid invoice](review-evidence/invoice-paid-390.png),
[finance](review-evidence/finance-390.png),
[paused Focus Mode](review-evidence/focus-paused-390.png),
[navigation](review-evidence/navigation-390.png),
[shop cart](review-evidence/shop-cart-390.png).

[Final 60 layout checks](review-evidence/browser-layout.json) and
[completed browser workflows](review-evidence/browser-workflows.json) contain the
recorded results. The final sweep also recorded 60 HTTP-200 page/width combinations
without page-level horizontal overflow. These are representative Chromium checks,
not exhaustive accessibility, device or browser certification.


Final housekeeping: the temporary loopback Django server and isolated PostgreSQL
cluster were stopped after verification. To reopen the local test database on this
machine (PostgreSQL 18), start only the review cluster:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D "$PWD\.local-review\pgdata" -l "$PWD\.local-review\postgres.log" -o '-h 127.0.0.1 -p 55439' start
```

Final code checks: all 250 repository Python files parsed successfully; migration
state and Django checks were clean; `git diff --check` reported no whitespace errors.
No existing lint/build configuration was found beyond Django/static collection.

## Follow-up: local startup and Git deployment configuration

- [x] Added a credential-free `.env.example` with the actual setting names and
  database precedence rules. Existing local `.env` values were preserved.
- [x] Load `.env` explicitly from the repository root with process environment
  precedence, including when the service starts from a different directory.
- [x] Corrected the README quickstart so credentials are configured before
  migrations; documented separate laptop/server settings and pull/restart steps.
- [x] Verified Django system checks, alternate-working-directory settings import,
  environment precedence, Git exclusion of `.env`, and whitespace checks.
- [x] Reran all 172 tests successfully against isolated PostgreSQL (15.172 seconds).
  The temporary review cluster was stopped afterward. The business database was
  not queried or migrated during this follow-up.

The local environment now contains a database password; only its presence was
checked, not its value or validity. Previous browser and migration-roundtrip
evidence above remains from the earlier review; those checks were not rerun for
this configuration/documentation change. Existing release blockers still apply.
