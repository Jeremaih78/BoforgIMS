# Inventory identity implementation

## Audit and plan (before implementation)
One Django/PostgreSQL application. Product has QUANTITY/SERIAL modes and cached quantity/reserved totals. ProductUnit already links manufacturer serials, shipments and invoice lines. ShipmentService posts stock and landed-cost accounting atomically; StockService reserves totals and finalizes only on full invoice payment. Combos expand to normal component lines. Shop orders reserve and decrement quantities separately. Permissions use core.permissions and Django grants with Admin/Staff compatibility. No stocktake, unit passport or warranty case system exists.

Extend ProductUnit, Product, StockMovement and their existing services. Add product barcode aliases, immutable unit events/sale history, stocktake snapshots/observations and lightweight service cases. Add request receipts for retry-safe scans. Keep views thin and reuse existing pricing, reservations, payment and receiving logic.

Sequence: identity schema and migration; receiving/adoption; sales/shop lifecycle; scanner interfaces and labels; stocktakes/after-sales; regression and browser tests; staff/deployment documentation.

## Identity and migration decisions
- BF-P-{product primary key padded to nine digits} identifies a product. BF-U-{unit primary key padded to nine digits} identifies a physical unit. PostgreSQL sequences allocate concurrently; never use max+1, delete identities or reset sequences.
- Existing serial_number remains the manufacturer serial and becomes optional. Existing non-empty values are preserved. New unit IDs are backfilled without changing quantities, money or sales.
- ProductBarcode supports manufacturer and alternate aliases, normalized ASCII with leading zeroes preserved. Exact resolution checks aliases, SKU, unit IDs and manufacturer serials; ambiguous legacy identities fail closed rather than guess.
- Existing products default to legacy count mode. Managers can explicitly identify existing stock and enable strict physical-unit consistency. New clean serialized receipts enable it automatically. No historical stock is fabricated during migration.
- Unit history and sale records preserve customer/invoice ownership across restock and resale. Returns enter quarantine; only approved restock adds saleable stock. Warranty handling does not imply a refund or credit note.
- Stocktake snapshots do not mutate inventory. Approval validates that the live baseline has not changed, and requires a reason. Unit discrepancies require explicit unit decisions.

## Main risks and controls
Concurrent sale/reservation: lock document, all affected products in primary-key order, then units. Retry: store UUID request receipts transactionally. Legacy mismatch: report and require explicit adoption. Shop serialized orders: reserve exact units and preserve paid-order history. Unknown scans: no automatic product creation. Immutable identifiers/history, explicit permissions, POST+CSRF for mutation; scan content never interpreted as executable markup or external URLs. Labels contain identifiers (no customer information). No offline mutation.

## Delivered components and files

| Area | Main files and behavior |
|---|---|
| Domain | `inventory/models.py`, `identity_models.py`: extended Product/ProductUnit/StockMovement; ProductBarcode, UnitEvent, UnitSale, ScanRequest, Stocktake, StocktakeLine, StocktakeScan, ServiceCase |
| Services | `inventory/services/identity.py`, `shipments.py`, `stocktake.py`, `after_sales.py`, `labels.py`: one identity resolver, atomic receiving/adoption/transitions, snapshot review, case management, PDF rendering |
| Sales | `sales/services/scanning.py`, `serials.py`, `__init__.py`, `scan_views.py`, API/forms/views: reuse pricing/reservation/finalization and exact-unit assignment; `sales/models.py` marks scanner-created lines |
| Shop | `shop/services.py`: exact-unit reservations, release on failure, immutable unit-sale history on payment |
| Interface | `inventory/identity_views.py`, `identity_forms.py`, `urls.py`, `templates/inventory/identity/*`; existing product/receiving templates; sales invoice/quote/assignment/PDF templates; `templates/base.html` navigation |
| Browser | `static/js/scanner.js`, `static/css/identity.css`, delegated `static/js/document_line_delete.js`, `static/js/product_combobox.js`: focused HID input, retained retry IDs, optional sound/focus, safe table refresh, serial capture rows, responsive dark interface and cancellation of stale product-search results |
| Operations | `inventory/admin.py` makes unit/audit records read-only; `inventory/management/commands/check_inventory_identity.py` provides read-only preflight before/after upgrade |
| Tests | `inventory/tests/test_identity.py`: identity, receiving, sales, bundles, stocktake, after-sales, shop, permissions, migration preservation and real concurrent PostgreSQL transactions |
| Schema | `sales/migrations/0010*`, `inventory/migrations/0016*`, `0017*`, `0018*` |

These are additions to existing IMS workflows, not a second inventory database. Unit IDs use PostgreSQL primary-key sequences; aborted transactions may leave gaps, which are intentional. Unit count is enforced for adopted/newly received stock. Legacy records remain visible without silently inventing units during migration.

## Routes

Routes below use the local `/ims/` prefix. The production IMS host may mount the same inventory/sales namespaces at its root; templates use Django reversals rather than fixed host URLs.

| Route after `/ims/` | Purpose |
|---|---|
| `inventory/identity/` | Scan/search, status counts, filtered unit directory, CSV and batch labels |
| `inventory/identity/lookup/` (POST) | Exact identifier lookup |
| `inventory/identity/assign/` | Permission-checked assignment of unknown barcode to existing product |
| `inventory/identity/products/<pk>/` | Product aliases, generated label, explicit stock adoption |
| `inventory/units/<unit_id>/` | Passport, paginated audit, sales/warranty history, reasoned actions |
| `inventory/identity/labels/` (POST) | Unit/product Code 128 + QR PDF, A4 or thermal |
| `inventory/stocktakes/` | Start/list sessions |
| `inventory/stocktakes/<pk>/` | Count, bulk quantity correction, submit, approve and CSV |
| `inventory/stocktakes/<pk>/scan/` (POST) | Retry-safe unit/quantity observations |
| `inventory/units/<unit_id>/case/` (POST) | Open return/warranty case |
| `inventory/service-cases/<pk>/` | Inspection, repair, resolution and replacement |
| `inventory/scanner-test/` | Non-mutating HID input diagnostic |
| `sales/<invoice-or-quotation>/<pk>/scan/` (POST) | Add quantity item / reserve exact invoice unit / add quote product |

Existing shipment API accepts `generate_ids: true` for omitted manufacturer serials and optional `location`. Omitting that flag retains the prior API's serial-count requirement. The assignment API accepts `unit_ids` as well as legacy `serial_numbers` and returns structured `assigned_units` / `available_units` alongside the old serial fields.

## Operational design review

- **Cashier:** one unit scan identifies the model and machine, fills an existing combo/manual allocation first, and reserves it. Repeated unit scans cannot inflate quantity. Quantity-item scans deliberately increment quantity.
- **Receiver:** optional manufacturer serials remove fake serial entry. Each received machine still has its own permanent identity. Stock and receipt accounting roll back together if any step fails.
- **Manager:** adoption separates physical reconciliation from ordinary receiving, so existing stock is never doubled. Stocktake approvals reject concurrent inventory changes, including changes that return totals to their original values.
- **Technician/customer support:** the passport finds the original sale and warranty; quarantine prevents an uninspected return entering saleable stock. Replacements preserve the original expiry and customer relationship.
- **Architecture:** transaction-bound request receipts make retries safe; there is no claimed offline mutation or hardware detection. Events record PDF export rather than pretending a physical print succeeded.

## Validation

The full Django suite passed 216 tests on a freshly created isolated PostgreSQL database after the core implementation and usability changes. Two additional targeted tests passed for unknown-code assignment permissions and historical migration preservation. The expanded replacement test also passed after adding protection against a second replacement/customer handover for the same unit and sale. Browser automation exercised scan selection, duplicate detection, quantity increments, unknown errors, live totals, removal after DOM refresh and receiving Enter focus; nine routes had no horizontal overflow at 390, 768 and 1440 pixels and no JavaScript errors. A separate mobile assignment check verified the selected product ID and that Enter during a new search cannot choose a stale result. A4 and thermal PDF samples were rendered with Poppler and visually inspected.

Migration dry-run/check and Django system checks pass. Tests use `.local-review` tooling and `config.test_settings`, never production credentials. Fresh test databases are required for the full repository suite: several older accounting/Tasker tests depend on data migrations, and reusing a flushed `--keepdb` database loses those seeds. No runtime dependency was added. Local review files are ignored and not deployment artifacts.

## Limits and follow-up options

- Physical RKtech/printer calibration is still required on actual hardware. Browser tests simulate HID typing; they do not certify a scanner or print alignment.
- Receipt completion retains the existing whole-shipment rule. Partial shipment receiving needs a separate accounting/workflow design.
- Stocktake quantity locations are not invented; location-filtered sessions apply to individual units. Multi-warehouse quantity bins would need a stock-location ledger.
- Financial credit notes/refunds, warranty expenses and write-off journals require the existing accounting review process; lifecycle actions do not fabricate them.
- Labels provide an A4 pitch template and 100 × 50 mm thermal size. Other printer layouts can be added once the real label stock is measured. Camera scanning, offline reconciliation, bulk CSV serial imports and signed public warranty portals are future extensions.
- App-level identity/event mutation is restricted and audited, with database uniqueness and foreign-key protections. Direct SQL remains an administrator capability; operational staff should never receive database credentials. Do not purge request receipts while an old browser might retry them without a defined retention policy.

Deployment/backup/rollback commands: `BARCODE_DEPLOYMENT.md`. Staff instructions: `BARCODE_STAFF_GUIDE.md`.

## Sources
GS1: https://support.gs1.org/support/solutions/articles/43000734088-what-s-the-difference-between-a-gs1-gtin-and-other-product-identifiers-i-can-purchase-online-
ReportLab barcode support: https://docs.reportlab.com/tagref/tag_barcode/

## Sticker layout and bulk printing update

Labels use a monochrome brand header, prominent product name, centered barcode/ID, QR, optional manufacturer serial and website. A4 sheets provide 12 consistent 94 × 43 mm cutting outlines with 3 mm horizontal / 2 mm vertical gutters, plus print instructions outside the stickers. Thermal labels retain their 100 × 50 mm paper size. Inventory identity offers individual, current-page and all-filtered-pages selection with a live counter. The server reapplies the same filters and rejects batches over 1,000 instead of silently truncating. No schema change is required.
