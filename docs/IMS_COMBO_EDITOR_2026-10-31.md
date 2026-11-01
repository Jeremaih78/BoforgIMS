# Searchable combos in the sales editor - 31 October 2026

New invoices and quotations can contain only combos, only individual products, or both, and are saved in one submission. The same editor can append combos to an existing editable document.

## Staff workflow

Use Add combo beside Add product or in the Combos section. Search by combo name, code, or an included product name/SKU. Suggestions show the discounted price, component quantities and currently available stock. Arrow keys and Enter select a result; focus moves to the number of combos. Multiple combo rows are supported, with live totals and savings. An unused product row can be left blank when making a combo-only document.

Save and Save & download PDF submit the customer, individual products, combos and document options together. Saved combos expand into the existing component and discount lines, as in the original IMS workflow. The editor displays those saved lines on reopening and starts with no new combo additions, so saving again does not duplicate them. It does not introduce a separate editable group model for historical combos.

## Pricing and inventory

The existing `inventory.services.combos.add_combo_to_document` service remains responsible for expansion and discounts. Prices are calculated from current catalogue components on the server, including the existing rounding per combo; posted price/discount overrides are ignored. Both fixed and percentage discounts are preserved. Product and combo rows must use a single currency. Empty/inactive combos, inactive components, invalid quantities, mixed currencies, malformed management fields and decimal-field overflow are rejected.

The complete header, lines, discount adjustments and stock reservation changes share a transaction. Invoices reserve the aggregate demand across direct products and every combo, including overlapping components. Insufficient stock rolls back everything. Quotations can include unavailable stock and do not reserve it; conversion reserves the copied component demand once. Paid/finalized invoices and converted quotations retain their edit protections. Serial-tracked components still require physical unit assignment before finalization, and payment retry protection continues to prevent repeated deduction.

The 100-line editor cap includes expanded combo components and discount lines. Existing financial/tax conventions are unchanged. No migration or dependency is added for this update.

## PDF and usability fixes

Invoice and quotation PDF descriptions now handle discount lines without attempting a product lookup on a null product. Component names are printed along with their combo label, and discount amounts and the final total remain visible. Edited standalone product descriptions and unit identifiers are retained. Focused fields are scrolled above the sticky save bar so quantity entry is usable on shorter screens.

## Verification

- Broad regression suite: 215 tests passed, covering sales, inventory, customers, dashboard, hostname routing and reliability.
- Final focused suite: 43 tests passed, including the added PDF rendering case, unavailable-stock quoting/conversion, and service-only combos. Coverage also includes combo-only and mixed submissions, shared component demand, percent/fixed discount arithmetic and rounding, stock rollback, conversion retries, serial restrictions, payment finalization/retries, edit/save retention, permission checks, invalid input and line limits.
- Real Chrome: combo-only quotation saved and downloaded as PDF; mixed invoice saved with a direct product and fixed-price combo; removed invalid rows did not block native validation; reopening and saving retained totals and discounts without duplication. Keyboard search and layouts passed at 360, 390, 768 and 1366 pixels, with no JavaScript errors or page-width overflow.
- The browser-created mixed invoice was reconciled against stored records: total 1,140, exactly one discount of 60, reservations for seven printers and two presses, and no stock deducted before payment. Demonstration records exist only in the isolated preview database.
- Combo quotation PDF was rendered and visually inspected. Django system checks and JavaScript syntax checks passed. The sales migration drift check is separate from unrelated pending Tasker model work in this shared checkout.

Deploy the changed/new sales modules, templates and JavaScript, collect static assets and restart the application. The prior sales customization migration is still required for the earlier feature update; this combo change adds no new one. The local preview is running on port 8067.
