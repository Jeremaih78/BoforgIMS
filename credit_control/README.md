# Credit Control Module (`credit_control`)

`credit_control` adds a Debtors & Creditors operational cash-control system to Boforg IMS.

## What it includes

- Debtor and creditor summary accounts (`DebtorAccount`, `CreditorAccount`)
- Follow-up logging for collections and supplier negotiations
- Promise-to-pay tracking with broken promise detection
- Payment disputes registry
- Collection task engine with reminder rules
- Aging engine for AR/AP
- Rule-based recommendation engine (AI-ready interfaces)
- WhatsApp reminder stub workflow
- Collections dashboard and operational pages
- CSV and PDF export endpoints for key credit control reports
- In-app notifications and audit event model

## Core model relationships

- Debtor side derives from `sales.Invoice` and `sales.Payment`
- Creditor side derives from `accounting.SupplierBill` and `accounting.APPayment` (FIFO allocation)
- Follow-ups, promises, disputes, and tasks link back to core entities

## Key services

- `get_customer_outstanding(customer, as_of_date=None)`
- `get_supplier_outstanding(supplier, as_of_date=None)`
- `build_debtor_aging(as_of_date=None)`
- `build_creditor_aging(as_of_date=None)`
- `calculate_debtor_risk(customer)`
- `calculate_creditor_priority(supplier)`
- `generate_follow_up_recommendation(customer, invoice=None)`
- `generate_creditor_payment_recommendation(supplier)`
- `auto_create_collection_tasks(as_of_date=None)`
- `sync_debtor_account_summary(customer)`
- `sync_creditor_account_summary(supplier)`

## URLs

Mounted under IMS:

- `/ims/credit-control/` (Collections Dashboard)
- `/ims/credit-control/debtors/`
- `/ims/credit-control/creditors/`
- `/ims/credit-control/followups/`
- `/ims/credit-control/promises/`
- `/ims/credit-control/disputes/`
- `/ims/credit-control/tasks/`
- `/ims/credit-control/aging/`

## Scheduled stubs

- Command: `python manage.py run_credit_control_jobs`
- Runs snapshot sync, auto-task generation, and alert generation

## Reminder and AI-ready behavior

- `ReminderRule` lets each company configure schedule offsets and priorities
- Recommendation outputs are structured (`recommended_action`, `recommended_message`, `urgency`, `reasons`) to support future LLM plug-in replacement

## Reporting exports

CSV and PDF endpoints available for:

- AR/AP aging
- promises / broken promises
- disputes
- top debtors / debtor risk
- upcoming payables
- critical supplier exposure
- creditor negotiation activity
- collections activity
- cash control and weekly net due snapshots

## Notes

- Supplier bill-level settlement uses FIFO allocation because current schema stores AP payments at supplier level.
- Credit notes/debit notes/write-offs are prepared as extension points and can be integrated if those source models are added.
