# Whole IMS audit — 2 November 2026

The candidate is verified and ready for a controlled deployment. The updated local preview runs at http://127.0.0.1:8067/. Production was not deployed from this chat. Run the final preflight against the actual target before switching the release.

Machine-readable evidence: [ims-audit-20261102.json](review-evidence/ims-audit-20261102.json).

## Corrections and improvements

| Area | Correction |
| --- | --- |
| Retried saves | Durable submission keys prevent duplicate invoices, quotations, appended combos and stock reservations. Reusing a key with different details is rejected. Earlier clients remain compatible. |
| Concurrent editing | Header/line signatures detect stale editors. Header locks coordinate editing with payments. A stale window cannot overwrite a newer save. |
| Dependencies | Updated Django to 5.2.18, REST framework to 3.18.2, Pillow to 13.0.0, pypdf to 6.21.4, urllib3 to 2.8.0, Brotli to 1.2.1 and certifi to 2026.10.18; tested a clean installation from requirements. |
| Filters and exports | Invalid dates and finance IDs return 400. Finance controls retain selections. CSV exports quote formula-like text while preserving numeric money cells. |
| Error handling | Branded 400/403/404/500 pages have support references and no-store caching. Error responses do not need database-backed navigation or static assets. API errors use JSON. |
| Health probes | Read-only /health/live/ and /health/ready/ endpoints; readiness returns 503 on database or migration problems without exposing configuration or exceptions. |
| Navigation | Main IMS Bootstrap, icons and menu/dialog JavaScript use bundled assets. Browser workflows passed with external requests blocked. |
| Ledger performance | Added posted-journal indexes for company/date and source/source ID. |
| Company settings | Base currencies and default tax rates are changed atomically within their own company. One company no longer clears another company's defaults. |
| AI recovery | Usage logs capture start time explicitly and preserve timestamp order if the wall clock moves backward. Disabled-provider/manual-work recovery passes. |
| Reconciliation | Expanded check_ims_integrity for empty/unbalanced journals, missing expense/bill/payment journals, invoice/ledger disagreements and overpayments, alongside stock/reservation checks. --strict rejects unresolved findings. |
| Release gate | Added check_ims_release for dependency pins, production Django checks, PostgreSQL, migrations, reconciliation, hashed assets and private-upload isolation. It is read-only. |

Earlier accounting/mobile work, searchable products and combos, tax after discounts, price-list PDFs, dashboard and private receipts were included in regression coverage.

## Verification

- Full repository suite: **1,580 tests passed** on PostgreSQL with release dependencies and four isolated workers. Coverage includes inventory/serials/shipments, sales/combos/tax, accounting, credit control, permissions, shop/payments, host routing, communications and Tasker.
- The first baseline overlapped a schema change and was discarded. The reported result is the clean run against the fixed schema.
- New regressions cover duplicate/concurrent saves, stale editing, rollback, invalid filters, CSV formulas, company defaults, reconciliation, database loss and production error pages.
- Final dependency audit: **zero reported known vulnerabilities**. Dependency consistency passed; the isolated environment's installer was updated too.
- Production-style configuration, manifest/static, schema, storage isolation and reconciliation gates **passed**, including a populated synthetic fixture.
- Chrome checked expense capture, customer receipts, owner funding, mixed product/combo quotation and PDF at **320px, 390px and 1365px**, with external requests blocked and no horizontal overflow or JavaScript errors.
- Canonical-host checks: **31 requests, zero server errors**, across IMS, website, shop, AI and API, including invalid inputs.
- Backup/restore: **177 tables and 1,086 fixture rows** matched by row counts and ordered-content digests. Connection time zones were normalized to UTC for comparison. The backup used synthetic data.
- Source scanning found existing synthetic fixture/rehearsal patterns; no production credential was introduced by this candidate. Environment files and private keys were preserved.

Deployment preparation additionally verified 43 focused tests after adding database defaults for rollback compatibility. These defaults preserve legacy inserts while new ORM-created documents continue to add tax on top. Full Linux/server checks are recorded separately in the deployment receipt.

## Deployment

Prepare a current private database/media backup and verify recovery ownership. Preserve the production environment, persistent secret key and approved AI/mail/rollout flags. Rehearse on an isolated restored production copy.

Install the tested requirements in the release environment, review the migration plan, then run the following against staging with production settings:

```bash
python manage.py migrate --plan
python manage.py migrate
python manage.py check_ims_integrity --strict
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py check_ims_release --production --fail-on-blockers
```

The release gate emits JSON and exits unsuccessfully on blockers. Reconciliation findings require review; they do not authorize rewriting historical prices, tax, payments or stock. Use the reviewed maintenance/release process for the authorized live deployment, restart the actual web/worker services, and check /health/ready/ and primary role workflows before reopening writes.

Combined candidate migrations: accounting 0013/0014/0015 and sales 0012/0013/0014/0015. Existing document totals retain their agreed tax convention. No historical financial entries were automatically corrected.

`deploy/ims_release.py` is the archived November 1 release with a specific 20-migration allowlist. It deliberately rejects other plans and is not the runner for this candidate. Use the current reviewed plan and new release gate.

Source-bound local Tasker evidence was refreshed from this verification. Real-cohort productivity, invitations, live AI/email activation and rollout authorization retain their separate operating controls.

## Evidence limits

Verification used isolated Windows/PostgreSQL environments and synthetic data. Target-server Linux/native-PDF rendering, worker supervision, current production reconciliation and provider sandbox checks remain part of the final server preflight. Live provider transactions were not initiated by this audit. A production reconciliation finding should stop rollout until reviewed.

## Primary references

[Django 5.2.18 security release](https://docs.djangoproject.com/en/5.2/releases/5.2.18/), [deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), [REST framework release notes](https://www.django-rest-framework.org/community/release-notes/).
