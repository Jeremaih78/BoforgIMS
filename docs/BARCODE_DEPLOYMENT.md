# Barcode release: deploy and recover

Read `BARCODE_IMPLEMENTATION.md` and `BARCODE_STAFF_GUIDE.md` first. This release extends the current Django/PostgreSQL application and uses the existing ReportLab dependency. No new runtime package or separate service is needed.

## Preparation

Merge the reviewed release branch into `main` in Git, then deploy that exact commit. Do not run `git reset --hard` over VPS edits. The implementation workspace must be committed/pushed before these commands can retrieve it. Do not include local `.env`, test databases, `.local-review`, or unrelated edits in the release.

The repository's existing production guide identifies `gunicorn-ims`; verify the actual service, virtualenv and project directory on your VPS. Follow its secret-loading instructions. Never run `config.test_settings` on the VPS.

During a maintenance window, pause web writes, background workers and integrations that mutate inventory. Keep the public proxy in maintenance mode until migration and smoke checks pass. Capture the old commit, database backup, media/private uploads and deployed configuration. Verify the backup can be restored to a separate database.

Example shell sequence (replace bracketed values with your actual settings):

```bash
cd /actual/path/to/BoforgIMS
source /actual/path/to/venv/bin/activate
git status --short
git rev-parse HEAD > /secure/backup/before-barcode-commit.txt
sudo systemctl stop gunicorn-ims
# Also stop configured workers/integrations before taking the backup.
pg_dump --format=custom --host=DB_HOST --username=DB_USER --file=/secure/backup/before-barcode.dump DB_NAME
pg_restore --list /secure/backup/before-barcode.dump > /secure/backup/before-barcode-contents.txt
git switch main
git pull --ff-only origin main
python -m pip install -r requirements.txt
python manage.py check_inventory_identity
python manage.py check_ims_integrity
python manage.py migrate --plan
```

Review both reports before proceeding. `check_inventory_identity` works before or after these migrations. It reports normalized duplicate serials, namespace ambiguity, serialized count gaps, sold records without sale dates, missing IDs and reservation anomalies. It does not modify data. `--fail` can turn findings into a nonzero exit code for release automation. Preserve reports securely.

Do not silently merge duplicate machines, erase serials or adjust quantities. Resolve identity conflicts with their source documents and an authorized, audited correction. Legacy count gaps require a real count and explicit adoption after upgrade, not guessed historical receipts. Migration 0017 aborts on duplicate manufacturer serials after case/whitespace normalization.

## Apply

```bash
python manage.py migrate --noinput
python manage.py check
python manage.py check_inventory_identity
python manage.py check_ims_integrity
python manage.py collectstatic --noinput
sudo systemctl start gunicorn-ims
sudo systemctl status gunicorn-ims --no-pager
```

Run checks with the same production environment and settings used by Gunicorn. Review `journalctl -u gunicorn-ims` if startup fails, without copying secrets into tickets. Restart any separately configured workers only after success. Existing infrastructure/configuration is unchanged; Nginx does not need a new route definition for these Django pages.

Migrations:

- `sales.0010_documentline_scan_generated`: scanner-managed line marker.
- `inventory.0016_product_identity_enforced_product_warranty_days_and_more`: identity, audit, stocktake, service-case schema and optional manufacturer serials.
- `inventory.0017_productunit_unit_manufacturer_serial_normalized_unique_and_more`: stable IDs for existing units, legacy history/sale snapshots, normalized serial uniqueness and assignment constraint. Existing quantities/costs are untouched. Existing nonempty serial spelling is preserved. Warranty dates are not invented for legacy sales.
- `inventory.0018_stocktakescan_note`: auditable bulk-count explanations.

The backfill runs in a transaction. Rehearse on a restored production copy and measure migration time with your data volume. Keep a maintenance window for the backfill and indexes. Never reset PostgreSQL sequences or reuse printed IDs.

## Permissions and rollout

The existing Admin group and superusers can manage identities. Staff retain normal sales/view/count access. Grant explicit Django permissions to narrower operational roles as appropriate:

| Action | Permission |
|---|---|
| Assign product barcodes/adopt stock | `inventory.change_product` |
| Receive shipments | `inventory.change_shipment` |
| Print/reprint labels | `inventory.print_inventory_labels` |
| Manage returns/repair/serial corrections | `inventory.manage_unit_lifecycle` |
| Approve reconciliation | `inventory.approve_stocktake` |
| Count stock | `inventory.add_stocktake`, `inventory.change_stocktake`, `inventory.view_stocktake` |
| View unit/service history | `inventory.view_productunit`, `inventory.view_servicecase` |
| Scan documents | `sales.change_invoice` / `sales.change_quotation` |

Keep view permissions with the corresponding write permissions. Permissions use the existing `core.permissions` Admin/Staff compatibility; this release creates no competing role system.

Smoke-test in a restored staging database: receive three machines with only one manufacturer serial; print and physically scan both label types; add a quantity item twice; add the same unit twice; reserve it on another invoice and verify rejection; pay once; return and inspect/restock; run a stocktake and approve a reviewed difference. Check receipt/payment accounting as well as stock/unit counts. Use a small pilot product group before labeling the whole warehouse. Product identification alone does not require unit tracking or relabeling valid manufacturer barcodes.

## Rollback

This is a data-bearing upgrade. Migration 0017 is deliberately forward-only: reversing the schema would discard identity/service history and cannot turn blank manufacturer serials back into truthful mandatory serial numbers.

Before any real writes after upgrade, rollback means: stop all writers, restore the verified pre-release database into a separate replacement database, restore the matching code commit/config/media, point the service at that restored database, collect static files and smoke-test before reopening. Use the existing production guide for your actual database ownership and secret configuration. Retain the upgraded database for investigation.

After any live receipts/sales/labels/returns under the new release, prefer a forward fix. A pre-release restore would lose subsequent business records and could cause printed Unit ID reuse. Reconcile/export all intervening activity before any planned restoration; do not run a blind `migrate inventory 0015` or sequence reset. Never run old application code against an actively used upgraded stock database: it cannot honor the new physical-unit protections.

No deployment, restart or database migration on the real VPS is performed by this implementation task.
