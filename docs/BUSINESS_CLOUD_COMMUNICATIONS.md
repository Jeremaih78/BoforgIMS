# Business Cloud communications consumer

Deployed and enabled on **2 November 2026 at 05:45 Africa/Harare**, after authenticated Mailcow delivery and isolated end-to-end tests passed. The live release is commit `82f0ada992e647fb9112208b20c9db5c37f7845a` on `release/communications-82f0ada9`. The supervised `boforg-ims-communications.service` consumer and signed callbacks are active. Business Cloud's provider code/contracts are in the neighboring Business Cloud checkout, `docs/communications/` and `integrations/boforg_communications_client.py`; its `PRODUCTION_DEPLOYMENT_2026-11-02.md` records deployment and recovery evidence. The consumer's `client.py` is an identical standalone copy and has no third-party HTTP dependency.

## Implemented flow

Automatic invoice and quotation emails were requested after the initial deployment. The finalization implementation adds independent `CLOUD_COMMUNICATIONS_AUTO_INVOICE` and `CLOUD_COMMUNICATIONS_AUTO_QUOTATION` switches; their source defaults are false. Reminders remain disabled in Cloud and no reminder scheduler is added to IMS.

When enabled, staff use **Finalize invoice & email** or **Finalize quotation & email** on the document details page. Invoice confirmation and the first full-payment finalization also enqueue the initial invoice email. Partial payments do not finalize a pending invoice, and payment after confirmation does not send it again. A quotation finalization changes DRAFT to SENT. Repeating finalization, retrying an HTTP submission, or ordinary model saves cannot create another automatic request. Existing finalized documents are not backfilled.

Finalization and the local outbox row commit together, with one automatic request per document enforced in the database. PDF generation uses the existing IMS templates; no Cloud/SMTP call occurs in the financial transaction. Missing recipient, invalid PDF or unconfigured currency creates a visible `blocked` request for staff review rather than losing a payment. Correct the customer/PDF and use manual approval to queue a blocked delivery. A queued/failed automatic snapshot is reused by the manual approval screen; reviewed remote retries remain in Cloud.

The automatic snapshot digest excludes payment/stock/conversion state and internal cost snapshots. A later payment or quotation conversion therefore preserves the original finalization email, while changed customer details, lines or printed terms cancel an unsent snapshot for review. This does not create a new email for subsequent edits. Authorized application finalization services are the trigger; direct SQL, admin status edits and arbitrary `model.save()` do not enqueue emails.

With the feature enabled, invoice/quotation pages also show **Email & history**. An authorized sales staff member can preview the existing PDF and approve the current customer email/document digest. Quotations must be SENT; invoices must be confirmed, paid or stock-finalized. New document creation and ordinary model saves do not contact Cloud or send mail. The approval transaction writes a private immutable PDF/context snapshot into the local `CommunicationRequest` outbox; it does not transfer ownership of IMS records.

The background `process_cloud_communications` command submits approved business events through the authenticated HTTPS API. Its UUID remains the Idempotency-Key on every retry. Timeouts, worker restart and temporary service failure therefore reconcile the same Cloud request rather than creating another message. IMS invoice/quotation/stock operations remain independent of Cloud availability. Temporary failures back off, with ten HTTP attempts before local `failed`; nonretryable validation/auth errors stop for inspection. A changed document detected before submission is cancelled locally. After a prior HTTP timeout, reconcile the external reference in Cloud before assuming that local cancellation withdrew a possibly accepted request; cancel the Cloud pending message if necessary.

The queue stores Cloud's message UUID/status and reconciles through signed status GETs. Optional callbacks verify an independent HMAC/time window, persist event IDs once, correlate stable external references (even if the HTTP submission response has not arrived), and ignore older status timestamps. `smtp_accepted` is not inbox delivery; `unknown` requires review in Cloud. The integration never marks a financial invoice paid, changes stock, creates a customer or overwrites a quotation in response to communication status.

## Configuration and rollout

Configuration defaults remain off. The live deployment enables the consumer, uses `https://cloud.boforg.co.zw`, and stores its scoped token in a private file outside the release. Secrets are supplied through `/etc/boforg-ims/communications.env`; they are never printed or committed. The example below shows safe defaults for a future isolated staging deployment:

```dotenv
CLOUD_COMMUNICATIONS_ENABLED=false
CLOUD_COMMUNICATIONS_AUTO_INVOICE=false
CLOUD_COMMUNICATIONS_AUTO_QUOTATION=false
CLOUD_COMMUNICATIONS_URL=https://cloud.boforg.co.zw
CLOUD_COMMUNICATIONS_TOKEN_FILE=/private/ims-cloud-service-token
CLOUD_COMMUNICATIONS_CURRENCY=
CLOUD_COMMUNICATIONS_CALLBACK_SECRET=
```

The current IMS document models do not store currency. Production currency was verified as **USD** against the active base-currency row, `BASE_CURRENCY_CODE` and the existing sales posting code before configuration. Future deployments must verify their actual currency; approval is refused while the code is empty. Token/secret values are never included in source or browser pages. The token file must be private and outside tracked/released source. Cloud provisions a scoped credential for the correct business/application; default scopes permit only invoice/quotation submit/events/read/cancel.

1. Back up/rehearse restore of IMS; review the additive `cloud_communications/0001_initial` migration.
2. Validate the Cloud staging API, verified sender, authenticated SMTP and controlled PDF receipt before enabling consumer traffic.
3. Apply the local consumer migration using the existing deployment/migration process; use a separate staging database.
4. Enable `CLOUD_COMMUNICATIONS_ENABLED` only for the reviewed release. The live deployment applied only the additive `cloud_communications.0001_initial` migration; existing financial, inventory and customer rows were verified unchanged.
5. Start a dedicated non-root worker: `python manage.py process_cloud_communications --watch --limit 25`. It needs only the existing IMS database and outbound verified HTTPS. Keep the service token out of command arguments/logs.
6. Use a staff-approved test invoice/quotation and safe mailbox. Simulate a service outage, retry/restart, status polling and callbacks. Preserve the same approval UUID if a response is lost. Check the invoice/quotation/stock workflows still work while Cloud is unavailable.

The callback route is named `ims:sales:cloud_callback`. On the IMS-only host URL configuration it resolves under `/sales/communications/callback`; the combined site configuration includes the IMS prefix. Use `reverse()` under the actual deployment URL configuration rather than assuming a path. Provision this exact HTTPS URL and host allowlist in Cloud; use an independent 32+ character secret in both systems. Callbacks are optional; polling works without them.

The consumer returns no hidden SMTP credentials to the browser and adds no Mailcow network/database access. Feature-disabled views return 404 (callback returns 503). Roll back by disabling the flag/stopping only the consumer worker; preserve its additive tables for reconciliation and revoke its service credential after retirement. Do not delete pending/uncertain requests or issue a fresh key merely to make an error disappear.

## Local validation

`config.communications_test_settings` targets `test_ims_cloud_communications` using `IMS_TEST_DB_USER`, `IMS_TEST_DB_PASSWORD` and `IMS_TEST_DB_PORT` supplied by the local test runner. It does not read or print a production database password. Consumer tests and existing sales experience/combo regression tests use injected Cloud/PDF adapters. An existing unrelated `.env` parse warning was observed during test startup and left unchanged.

The deployed source passed **57** consumer and existing sales regression tests. An isolated restore of production data exercised real IMS PDFs through the Cloud API, Mailcow receipt, status reconciliation, a Cloud outage and a lost submission response. Live callback checks rejected an invalid signature and accepted a valid signature to an intentionally unknown reference without creating business/history rows. Staff can now use **Email & history → Approve and queue email**; ordinary document creation does not enqueue email or backfill existing documents.

Automated remote cancellation after document edits, stronger immutable IMS financial versions, local operator retry UX and commercial currency-model changes remain separately reviewed work. Staff can inspect history and use the Cloud owner's reviewed retry/cancellation controls; there is no blind resend button in IMS. After a database restore, keep the consumer disabled until every restored pending request is reconciled with Cloud/Mailcow to prevent replay of an email accepted after the backup snapshot.
