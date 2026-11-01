# IMS sales and selection update - 30 October 2026

The main IMS now supports typing into ordinary dropdowns, composing an invoice or quotation on one screen, and downloading a customer-facing product price list. Existing work in this checkout was preserved. Production has not been deployed by this change.

## Changes staff will see

- Product, supplier, category, shipment, movement, cost, status and other single-choice dropdowns offer filtered suggestions with arrow-key and Enter selection. Native selections remain available if JavaScript is unavailable; multi-select controls and AI Tasker retain their own behavior.
- Inventory, customer and sales lists filter while typing and keep focus in the search box. Normal GET forms remain the server fallback. Inventory's price-list download keeps the search and low-stock filter and includes matching active products across all pages.
- New invoice and quotation pages include the customer, dates, product rows, editable quantities/prices/tax rates/descriptions, and a live total. Add and remove rows without saving between products. Ctrl/Cmd+Enter saves.
- Save & download PDF writes the complete document, returns to its saved page, and starts the download. Returning to the page avoids leaving a new-document form behind after downloading.
- Saved documents have an Edit details & all lines action. Edits update stock reservations in the same transaction. Existing adjustments are displayed and retained; the original document page still supports combos, scanning, serial selection, payments and individual line removal.
- Reference/PO number, printed notes, terms/instructions, and up to 12 named custom fields can be edited on either document. Custom fields support 60-character labels and 500-character values. Quotation conversion copies these fields into the invoice.
- PDF lines now print the edited description and the actual line total. Missing customer details no longer print as `None`. Customer-facing price lists include product, SKU, category, availability and currency/price, with repeated table headers and page numbers.
- Phone controls fit their rows; forms have consistent full-width fields, clear selection states and visible errors.

## Data and behavior

Migration: `sales/0011_invoice_custom_fields_invoice_reference_and_more.py`. It adds optional `reference`, `terms`, and `custom_fields` columns to invoices and quotations. Existing documents receive empty values; financial records are not rewritten.

The batch editor validates the whole submission before writing, checks that line IDs belong to the document, and uses the existing stock reservation service. Failed stock changes roll back the header and all lines together. Paid/finalized invoices and converted quotations remain locked. Product or quantity changes and deletions of lines with assigned physical units are rejected in the batch editor; use the existing document removal action to release those units correctly. Single-document editing is capped at 100 product rows. Mixed product currencies are rejected because no exchange-rate conversion is performed.

Prices entered in the batch editor are saved as shown. Existing tax calculations and historical amounts were not migrated: document totals are quantity multiplied by unit price, while accounting has a separate tax posting calculation. The editor labels its live amount as before tax. Reconcile tax presentation, payment totals and accounting as a separate financial-policy change before changing historical totals.

Dropdown enhancement works with rendered choices. Large catalogues still load those choices on legacy forms; remote paging can be added later if catalogue size makes that necessary. A developer can opt an individual select out with `data-native-select`.

## Verification

- Existing sales, inventory and customer suites plus the initial new cases: 126 tests passed on isolated PostgreSQL.
- All 17 final experience tests passed, including five additional cases covering stock release after deletion, failed-edit rollback, saved-page PDF download, mixed currencies and stale converted-quotation status. Across the runs, 131 unique tests passed.
- Django system checks, migration drift checks, JavaScript syntax checks and Git whitespace checks passed.
- Real Chrome workflow: selected a customer by phone, added multiple products by keyboard, changed quantity and price, added custom fields, saved and downloaded an invoice, reopened the batch editor, searched stock movements and filtered inventory live. No JavaScript errors.
- Additional browser checks passed for quotation creation with Ctrl+Enter, live customer filtering on a phone, normal form fallback after a network failure, and removing a row containing an invalid quantity.
- Inventory, customers, sales, shipment details, stock movement and the new composer returned 200 and had no page-width overflow at 390, 768 and 1366 pixels.
- Price-list and invoice PDFs were generated and rendered for visual review. The 40-product synthetic price list spans three pages and repeats headers correctly. Preview/test files contain synthetic data, not the production catalogue.

## Rollout

Review and include all newly added files with the changes. Follow the repository's deployment procedure and actual service configuration. Back up the production database first, then install the repository requirements, run `python manage.py migrate`, `python manage.py check`, and `python manage.py collectstatic --noinput`, and restart the application service. This update adds no dependency beyond the existing requirements. A local development environment had Django 5.2.6 installed; deployment should use the repository's pinned version.

After deployment, smoke-test a quotation, conversion, an unpaid invoice with two lines, stock reservation, a filtered price-list PDF, phone layout, and custom fields on both PDFs. The default `.env` parser reports an existing malformed statement at line 9 locally; it was not edited as part of the UI work.

For application rollback, restore the previous code/static bundle and retain the additive columns. Reverse the sales migration only after preserving any new reference, terms and custom-field data that users have entered.
