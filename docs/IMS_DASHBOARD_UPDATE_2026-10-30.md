# Main IMS dashboard - 30 October 2026

The dashboard now supports daily work directly: create invoices and quotations, search the workspace, inspect overdue balances, replenish stock, resume quotations, and follow up on shipments. The local preview remains at `http://127.0.0.1:8067/` with its existing isolated demonstration database.

## Behavior

- Global search suggests up to four matching products, customers, invoices and quotations per type. It supports SKUs, barcodes, phone numbers (including formatting differences), document numbers and references. The `/` shortcut focuses search; arrows and Enter open a result. A normal GET search remains available when suggestions fail or JavaScript is disabled.
- Quick links support new invoices/quotations/customers, stock scanning, stock movements, price-list PDF export, accounting and credit control. Actions and data sections are shown according to the same backend permissions used by their destinations.
- Today, last seven days and current month select the reporting period. Separate currency selection prevents adding different currencies together.
- Invoiced amounts use saved invoice line totals before tax. Recorded payments are selected by payment date, including receipts for invoices issued in earlier periods. Separate subqueries prevent multiple line/payment joins from inflating amounts.
- Still to collect shows positive remaining balances across all dates up to today, after recorded payments. Legacy paid statuses remain authoritative when older receipts were not imported. Overdue includes unpaid invoices whose due date has passed, as well as invoices explicitly marked overdue; settled balances are excluded.
- Active inventory-tracked products need replenishment when quantity minus reservations reaches their configured reorder level. Inactive products and services are excluded. Dashboard, low-stock inventory filter and filtered price-list export use the same selector.
- Draft quotation continuation excludes quotations that already have an invoice, even if their stored status is stale.
- Shipment follow-up covers overdue arrivals still in Created/In transit, plus Arrived/Cleared shipments with quantities remaining to receive. Its View all link uses the same selector.
- Recent invoices, attention rows and metrics link directly to their records or matching filtered lists. Filters survive sales pagination and live searches. Invoice drill-downs focus the invoice list; draft quotation links focus quotations.
- Dashboard requests are read-only and uncached. They do not synchronize or write credit-control account summaries while loading the page. Refresh updates figures and the displayed update time.

## Limits and financial interpretation

The schema has no document-level currency snapshot. Currency is inferred from linked product currencies; adjustment-only invoices use the configured base currency. Invoices containing multiple product currencies are excluded from monetary dashboard totals and flagged for review. A historical currency snapshot requires a separate data-model change.

The existing difference between document totals before tax and accounting tax postings is unchanged; the dashboard labels its invoiced amounts explicitly. Recorded invoice payments do not represent bank reconciliation or all business cash inflows. Supplier payable and cash-pressure estimates from the old dashboard were replaced with direct, permitted links to the relevant finance screens.

## Verification and rollout

The focused dashboard tests cover balances, multiple payments/lines, date periods, overdue classification, paid historical invoices, currency separation, mixed documents, reorder thresholds, shipment drill-downs, draft conversion, search, permissions and bounded read-only queries. Existing sales, inventory, customer, reliability and hostname-routing tests are also run against isolated PostgreSQL.

Real Chrome checks covered all 33 dashboard links, keyboard search, period selection and page widths of 360, 390, 768 and 1366 pixels. Desktop and phone screenshots were visually inspected. Demonstration priority records were added only to the isolated local preview and are labelled Demo.

Final results: all 191 regression tests passed, followed by all 16 focused dashboard tests (including the added formatted-phone case). Currency switching, focused invoice/quotation drill-downs and GET search fallback after a simulated network failure passed in Chrome. Django system and migration-drift checks passed. The reused test database required its accounting chart seed to be restored before the broad regression run; no production or preview records were involved in that test-setup repair.

No new schema migration or dependency is introduced by this dashboard change. Include the new Python selectors, templates and static assets, collect static files, and restart the application service. The earlier sales customization migration remains required for that preceding update. Production deployment has not been performed in this chat.
