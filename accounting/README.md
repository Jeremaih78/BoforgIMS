Accounting module (phase 1)

Overview
- Double-entry core: Accounts, JournalEntry, JournalLine with base-currency totals and balance validation.
- Seeded chart: Cash/Bank, A/R, Inventory, A/P, Equity, Sales, COGS, VAT Payable.
- Auto-posting hooks: Sales invoice revenue and AR receipt + COGS on payment.

Settings
- BASE_CURRENCY_CODE: default USD.
- ACCOUNTING_COGS_METHOD: MOVING_AVERAGE (stub; uses product.price if avg_cost missing).
- ACCOUNTING_POST_COGS_ON: INVOICE or PAYMENT (default PAYMENT).
- DEFAULT_TAX_RATE_ID: optional default tax.
- PERIOD_CLOSE_ENFORCED: reserved for period lock enforcement.

Posting Rules Implemented
- Sales Invoice on payment:
  - Dr Accounts Receivable (gross), Cr Sales (net), Cr VAT Payable (tax) if not already posted for invoice.
  - Dr Bank, Cr Accounts Receivable for receipt amount.
  - Dr COGS, Cr Inventory at average cost proxy.

CLI Seed
- python manage.py seed_chart_of_accounts — creates base currency USD, default VAT 15%, chart of accounts, default bank.

Notes
- Future iterations will add expenses, supplier bills, reports, period locking and FX conversions across currencies.

