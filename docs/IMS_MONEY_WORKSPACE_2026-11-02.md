# Accounting and credit control update — 2 November 2026

## Everyday workflows

Accounting now opens a money workspace with separate cash, bank and mobile-money accounts. It shows posted book balances per currency, external inflows/outflows, internal transfers, searchable activity, transaction details and CSV downloads. Shared legacy ledger accounts are flagged and counted once in totals. Balances include all posted entries through the selected end date; the activity date range does not reset balances.

Quick capture supports paid expenses, customer invoice receipts, unpaid expense bills, payments against a selected supplier bill, same-currency transfers, owner/loan funding and withdrawals. Creating an account can record a verified opening balance. Transfers do not count as revenue or expenses; owner funding does not count as profit.

Expense entry puts amount, account, category, payee, date and a private receipt upload first. Phone cameras can capture receipts. Optional details are collapsed. Save & add another and repeat-expense shortcuts reduce repetitive entry. Repeating does not copy the old receipt or reference. Posted entries retain the operator and an audit record.

Credit control uses live balances without writing cached summaries on GET. Its currency-specific queues show oldest overdue documents, aging buckets and practical next actions. Party pages link directly to receive/pay a document and log a follow-up. Follow-ups validate the party/document relationship and paired promised amount/date. Logging a follow-up does not send a message.

## Money and stock rules

- Expense postings use the selected wallet, transaction currency and book exchange rate. Recoverable tax is split from the full amount paid; it is not added to that payment. Expense reporting uses the posted net amount so later tax-rate changes do not revalue a posted expense.
- Supplier bill payments have explicit allocations. Partial payments reduce the selected bill, and concurrent payments cannot overpay it. Old unallocated supplier payments retain FIFO handling within the exact supplier, company and currency. Draft and future bills are excluded from current credit balances.
- Invoice receipts retain the existing stock reservation, serial assignment and finalization rules. A failed accounting/stock operation rolls back the receipt, status, reservations and journals together. The invoice's book exchange rate is retained for later receipts.
- Idempotent expense, receipt, bill, supplier payment and movement saves reject reuse with different details. Posted supplier payments and movements cannot be edited through their model save methods.
- Actual money movements reject future dates and closed posting periods. Currency/company mismatches, inactive accounts and unbalanced journals are rejected.
- Cashflow charts include invoice receipts, legacy AR receipts, supplier payments, expenses, funding and withdrawals. Reports select one currency instead of summing unrelated currencies. Recoverable input tax is excluded from expense/profit totals.
- Selling prices are no longer substituted for missing inventory costs. Zero recorded costs remain zero and should be reviewed when assessing profit.

## Tax decision

New invoice and quotation prices exclude tax. Line tax is rounded half up to cents and added after discounts. Combo discounts are allocated proportionally across component tax rates; the editor preview, saved totals, payments, credit balances and PDFs agree. Product-only, combo-only and mixed documents remain supported.

The sales migration adds the tax flag as false for existing rows, then changes the default to true for future rows. Existing invoices/quotations retain their agreed totals, and converting an existing quotation preserves its tax convention. Previously posted journals are not automatically rewritten; historical tax discrepancies require reviewed corrections.

## Review and rollout

Migrations: accounting 0013, sales 0012 and 0013. Apply the normal database backup and migration procedure for deployment. This work was migrated and tested on an isolated local PostgreSQL preview, with synthetic records and outbound communications disabled; production data was not migrated during this task.

The implementation was checked with 281 accounting, credit-control, sales/combo, inventory, dashboard and reliability regression tests, including a migration test proving old totals survive. Additional final checks covered changed capture screens and net-tax reporting. PostgreSQL concurrency tests covered repeated transfers and supplier saves, and competing full payments.

Chrome checks exercised expense capture, customer receipts, funding, mixed product/combo quotation creation and PDF output. Screens were checked at 320px, 390px and 1365px without horizontal overflow or JavaScript errors. The PDF was rendered and visually checked for subtotal, tax, discount and grand-total layout.

## Current boundaries

Balances are book balances, not live bank-feed balances. Historical records without journals are identified for review rather than assigned invented balances. Main capture screens operate in the main IMS company scope. Supplier expense bills do not receive inventory. Transfers require matching currencies; conversion and realized FX differences require separate reviewed accounting entries. Foreign-currency receipts use the agreed invoice book rate, not an automatically fetched market rate. Legacy imported paid markers remain authoritative where payment history was not imported.
