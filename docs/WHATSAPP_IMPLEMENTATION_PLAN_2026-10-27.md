# Boforg WhatsApp implementation plan

Prepared: 27 October 2026, Africa/Harare.

Status: **Ready to start implementation. Planning and repository inspection only have been completed.** No application code, database schema, Meta settings, production service or customer messaging has been changed by this plan.

## Outcome and release order

Complete Facebook Login for Business and WhatsApp account connection, then deliver the original business workflow:

1. An authorized Boforg user finishes an invoice or quotation and issues the completed document.
2. IMS records the customer recipient and a fixed PDF version, then queues delivery automatically.
3. The customer receives the actual PDF attachment through Boforg's connected WhatsApp number.
4. IMS displays queued, accepted, sent, delivered, read, failed or uncertain delivery states as applicable.
5. Boforg can subsequently connect and serve another business with explicit account and data isolation.

The first milestone is a working **Connect WhatsApp** flow. The second is a controlled Boforg PDF pilot. Automatic sending follows the successful pilot. External-client production is a separate final gate because the existing sales system is not tenant-isolated.

The document-issue action will be an explicit POST, initially presented as **Generate and send**. Automatic means that this final action queues the completed PDF without staff opening WhatsApp or attaching files. Saving the initially empty document, editing lines, previewing and downloading will not trigger messages. Later payment reminders, campaigns, full CRM functionality and AI replies are outside this delivery unless separately requested.

## Evidence and constraints

| Area | Verified current position | Consequence |
|---|---|---|
| Meta app | User screenshots identify Boforg WABA, app ID `2283190605837968`. | Use this app consistently for configuration, tokens, review and webhook signatures. |
| Review | Screenshots mark business verification and testing complete; Facebook Login for Business and App Review remain unfinished. The user reports a successful management API call. | Recheck live statuses at execution; a test call or role declaration is not proof of Advanced Access, publication or own-number coexistence. |
| PDFs | `sales/views.py` generates quotation/invoice PDFs through `sales/pdf_utils.py`. | Extract and reuse the existing rendering, branding and calculation behavior. |
| Document lifecycle | Initial create saves the document before its product lines exist. | Add an explicit completed-document issue/version boundary. |
| Customer phone | Optional free-text `Customer.phone`; no WhatsApp permission record. | Add number normalization and recorded messaging permission/opt-out handling. |
| Transport | Credit-control WhatsApp function is a logging stub. | Build a real integration; never label a logged stub as sent. |
| Login | No working Embedded Signup page, SDK initialization, authorization-code exchange or deauthorization handler was found. | Implement the flow before supplying definitive dashboard URLs. |
| Deletion | `legal/views.py` verifies signed requests but leaves the actual deletion work as TODO. | Complete lifecycle handling and truthful deletion status reporting. |
| Worker | New PostgreSQL `OutboxJob`, worker, scheduler and health commands exist in current Tasker development. | Reuse this foundation after its schema and tests stabilize; avoid a second broker/queue. |
| Shared work | Many Tasker/settings/template changes and the new job foundation are uncommitted or untracked. | Preserve other work; establish the integration baseline before editing shared files. |
| External sends | Current worker runs handlers inside a database transaction. | Add a durable external-send boundary; an accepted WhatsApp send cannot be rolled back with the database. |
| Client isolation | Sales and Customer have no company ownership field; IMS role grants are global. | A connected client receives no internal IMS access. Prove independent business scope before releasing client document operations. |
| Hosting | Existing Boforg HTTPS hosts, Hostinger and Cloudflare are documented/confirmed. Live service configuration has not been inspected. | Verify real staging/production paths and supervision instead of assuming they exist. |

### Meta version decision

Meta's version guide now lists Embedded Signup **v5**, released 20 October 2026. v4 remains supported; v2/v3 were deprecated on 15 October. Plan new work against v5, subject to confirming availability for this app at implementation time. Pin the selected Graph API and Embedded Signup versions separately.

Use the current v5 payload and coexistence options. Do not copy old `featureType`/`sessionInfoVersion` examples into a different version unchanged. v5 includes Business App onboarding by default, but successful coexistence still depends on the actual account, number, provider and onboarding eligibility.

**Early eligibility gate:** establish whether Boforg's existing app number can join through Boforg's own provider app and portfolio. This has not been established by the screenshots. Keep external-client onboarding and Boforg's own-number onboarding as separate proof cases. If Meta does not support the intended own-asset route, document the result and present a supported route before changing the live number. Do not silently migrate it, delete its app registration or invent a second business portfolio to bypass a restriction.

## Proposed implementation shape

Add a focused Django app, provisionally `whatsapp_integration`, within this repository. Keep sales calculations, inventory finalization and payment processing in their existing services.

- Explicit business membership and integration permissions, followed by connection, credential, signup-session, document-snapshot, recipient-permission, delivery, send-attempt, webhook-receipt and deletion-request records.
- Server-held encrypted credentials, with encryption keys outside the database and repository. Record account IDs, token validity and scopes without exposing tokens to browsers, logs or job payloads.
- Dedicated WhatsApp delivery records. Tasker's `NotificationDelivery` is designed for internal users/email and should not be stretched to represent customer PDF deliveries.
- Existing PostgreSQL outbox/runtime with a narrowly reviewed extension for external I/O and a `whatsapp.deliver` handler. Job payloads contain record IDs, not phone numbers, PDFs, tokens or message bodies.
- Reusable PDF rendering and private immutable snapshots. Upload PDF bytes directly to Meta; a staff-only IMS download URL is not a suitable media-fetch URL.
- Initial access controlled by explicit grants/invitations. Existing global Staff/Admin grants do not imply external-client business membership.

Proposed route ownership, to be finalized and implemented in Stage 2:

| Surface | Host / proposed location | Access |
|---|---|---|
| Client connection flow | `boforg.co.zw`, under a dedicated `/whatsapp/` namespace | Authenticated, invited business members; distinct from internal IMS navigation |
| Boforg staff connection/delivery controls | `ims.boforg.co.zw`, under a dedicated integrations namespace | Authorized Boforg staff |
| Meta webhook receiver | `api.boforg.co.zw`, under an integration namespace | Challenge verification and signed POSTs; no staff-session dependency |
| Privacy/deletion information | Existing public `boforg.co.zw/legal/` pages | Public information; request-status pages expose only minimal opaque status |

These are **planned locations, not live callback URLs**. Derive and verify the exact allowed domains/redirects from the implemented signup flow. With the JavaScript SDK, the flow returns data to its spawning window; a conventional `/auth/facebook/callback` route is not automatically required. Do not enter an invented callback path merely to fill Meta's form.

## Stages and completion gates

### Stage 1 — Establish the baseline and confirm the Meta route

Work:

- Capture the current commit, scoped working-tree changes and relevant migrations without resetting, stashing or committing unrelated work.
- Confirm the job-foundation integration baseline with its matching migrations and checks. Select an isolated implementation branch/worktree only when required shared changes are available there.
- Record the app, verified portfolio, WABA/Messaging account, distinct number-account IDs, phone-number ID, sender registration, provider links and review/publication status. Never infer one ID from another.
- Confirm v5/configuration availability and own-number coexistence eligibility. Establish a controlled test recipient and the applicable account's billing/template readiness.
- Freeze first-release behavior: explicit completed-document issue, immutable revisions, manual resend, permission roles and opt-in/opt-out rules. Existing saved documents are excluded from automatic backfill.

Deliverable: a short implementation inventory/decision log, migration baseline and test configuration with all outbound delivery disabled.

Completion check: required local prerequisites are known; unknown external prerequisites have a named action and owner. Eligibility uncertainty prevents real-number activation, not independent implementation with mocks.

### Stage 2 — Build scoped connection and credential foundations

Work:

- Add the integration app, additive migrations, explicit business memberships and connection state model.
- Keep Boforg's sales adapter explicitly bound to Boforg. An external business cannot query Boforg customers, documents, stock or finance by acquiring a connection.
- Define invitation/access flow and host routing without redirecting external clients into the staff dashboard.
- Add server configuration for app ID, secret, signup configuration ID, selected API versions, verification secret, encryption keys, callback origins and disabled-by-default feature flags.
- Implement encrypted secret storage, redacted diagnostics, credential replacement and a connection health view.

Deliverable: business-scoped connection models, staff/client access boundaries and a configuration template containing names/placeholders only.

Completion check: unauthorized and cross-business requests fail; secrets do not appear in HTML, JSON responses, errors or logs; migrations apply cleanly to a disposable PostgreSQL database.

### Stage 3 — Implement Facebook Login for Business and Embedded Signup

Work:

- Build the real **Connect WhatsApp** page with a current, version-pinned SDK flow.
- Bind each signup attempt to the authenticated actor and business using expiring, single-use server-held context. Validate message origins, expected payload shape, account ownership/access and returned IDs server-side.
- Exchange authorization codes only on the server. Handle cancellation, expired/reused codes, partial onboarding and wrong-business selections without recording a false connected state.
- Create/configure WhatsApp Embedded Signup in Meta's dashboard and capture its configuration ID. Use the configuration location actually available for this app; investigate missing dashboard options instead of guessing a replacement menu.
- Deploy the working page to staging HTTPS, then set the exact accepted domains and redirect entries used by that implementation.
- For the custom SDK route, follow the current guide for Client OAuth, Web OAuth, HTTPS, strict redirect matching, Embedded Browser OAuth and JavaScript SDK login. Leave unrelated device/login options tied to actual use.

Deliverable: an implemented account connection flow and a dashboard-settings register containing the real deployed URLs and configuration ID, or the exact pending dashboard prerequisite where Meta has not exposed configuration yet.

Completion check: local integration and permitted test-role checks pass for code handling, cancellation, replay and account substitution; only the intended business can acquire a connection. Successful real account connection is a later live gate after Stage 4's webhook, permission and eligibility prerequisites are met. If Meta prevents pre-review configuration/onboarding, preserve the tested local implementation and proceed to review preparation using the officially accepted test-tool evidence. Record the live connection as pending rather than declaring it successful.

### Stage 4 — Complete webhooks, privacy lifecycle and Meta review readiness

Work:

- Implement webhook verification and raw-body signature validation. Store valid events durably before acknowledgement and process them asynchronously. Handle duplicate/out-of-order events and unknown account identifiers.
- Subscribe only to fields required by the chosen messaging, account-lifecycle and coexistence behavior. Determine whether history/contact synchronization is needed; do not import chat history by default.
- Implement disconnect/revocation handling that stops new sends and cancels eligible queued work. Recheck authorization immediately before any send.
- Complete signed deauthorization/deletion request handling, real cleanup jobs and tracked deletion status. Apply a documented retention schedule; retain only required minimal accounting/audit evidence where applicable.
- Update privacy/terms/deletion pages from the older Facebook/Instagram description to the actual WhatsApp service. Name Hostinger/Cloudflare in the appropriate provider disclosures and check that the processor list matches real data paths.
- Prepare actual reviewer access instructions, separate permission videos and matching permission descriptions. Reuse existing valid test evidence; repeat only when an implementation change or failed requirement makes it necessary. An initial Tech Provider submission may use Meta's accepted API Setup send/receipt and WhatsApp Manager template-creation demonstrations. Any claim about the completed IMS PDF path must match the real path exercised in Stages 5-6; a connection demonstration alone is not evidence of message delivery.
- Verify required Advanced Access, access verification if requested, app publication prerequisites and the management-test counter. Meta review and counter updates are external state, not a code-test result.

Deliverable: verified webhook/lifecycle endpoints, truthful public information, a working deletion path and a review-ready connection demonstration.

Completion check: signature, replay, revocation and deletion tests pass. Local technical work can complete while approval is pending; external-client production remains gated on actual approval. Stages 5's offline work may proceed during review.

### Stage 5 — Build PDF preparation and reliable delivery

Work:

- Extract shared rendering from invoice/quotation GET views without changing prices, taxes, balances, inventory or payment behavior.
- Add phone normalization, recorded WhatsApp permission and opt-out checks. Ask staff to resolve missing/invalid/ambiguous numbers instead of guessing a recipient.
- Implement an explicit issue/version service that validates completed lines, snapshots recipient and PDF content/hash, and enqueues one delivery in the same successful database transaction.
- Resolve permitted document edits after issue using a new revision; keep the original queued/sent snapshot fixed. Preserve existing prohibitions on editing paid, payment-bearing or stock-finalized invoices: a PDF revision must not bypass financial/stock controls. Distinguish financial document status from channel delivery status.
- Add PDF media upload, approved document-header template selection and message submission with bounded network timeouts. Check actual template language, category, approval and document compatibility. A requested quotation's category must be approved, not assumed.
- Add dedicated delivery/attempt/receipt records and register the outbox adapter/reconciliation hooks without altering unrelated Tasker delivery behavior.
- Add a durable attempt reservation that commits before outbound I/O and a fenced finalization after I/O. Keep remote sending outside a rollback-only handler transaction. Make the smallest reviewed worker extension needed for this, with the existing database-only job guarantees preserved.
- Persist Meta's accepted message ID and distinguish acceptance from delivery. Where supported, attach an opaque local correlation reference for receipt reconciliation; verify support rather than assuming a provider idempotency key exists.
- Treat a timeout/crash after possible provider acceptance as **UNKNOWN**. Reconcile receipts or require deliberate operator action; never blindly retry an uncertain external send or claim exactly-once delivery.

Deliverable: real upload/send adapter behind disabled flags, manual **Generate and send** controls, a delivery ledger and retry/uncertainty UI.

Completion check: meaningful tests cover repeated clicks, transaction rollback, two competing workers, expired leases, remote acceptance followed by a database failure, duplicate/out-of-order receipts, token revocation, consent withdrawal, changed documents and permission failures. No mocked success is displayed as a real delivery.

### Stage 6 — Run a controlled end-to-end pilot

Work:

- Apply additive schema and deploy the feature-disabled build to staging; verify HTTPS and public callbacks through the real Cloudflare/Hostinger route.
- Run disposable-database integration tests and targeted regressions for sales, private-document access, host routing and the shared job runtime.
- Use an authorized test recipient, approved template and verified eligible sender to send one completed quotation PDF and one invoice PDF manually.
- Open each received attachment and compare its customer, number, lines, totals and document hash against the intended snapshot.
- Verify delivery from signed provider receipts, then exercise safe failure/retry and disconnect behavior.
- Prove the desired phone-app/API coexistence behavior separately on the real number if eligible. A successful Meta test-number send is not proof of that behavior.

Deliverable: pilot evidence, test results and an unresolved-issues list with each external prerequisite's real status.

Completion check: actual PDFs arrive at the correct controlled recipient and status history is accurate. No unverified own-number route or missing template/approval is marked complete. Local work can be ready while this external gate is pending.

### Stage 7 — Activate Boforg automatic delivery and production operations

Work:

- Prepare release/rollback commands, backups, migration order and the actual worker/scheduler service definitions for the inspected deployment. Do not assume the local job commands already have production supervision.
- Verify that starting a shared worker will not activate unrelated in-progress Tasker notifications, AI jobs or external adapters. Use reviewed job-kind controls if deployment isolation requires them.
- Deploy with outbound sending off, validate the release and enable the Boforg sender/test-recipient pilot first.
- Enable automatic queuing at document issue for Boforg after the manual pilot passes. Preview/download/ordinary edits continue to have no send side effect; historic documents are not auto-sent.
- Add queue-age/worker-health checks, failed/unknown-delivery visibility, credential expiry/revocation alerts, rate-limit handling and a global/per-business pause switch.
- Check routing, proxy/body limits, protected PDF storage, retention and authorized manual resend in the running deployment.

Deliverable: a production runbook and Boforg's automatic quotation/invoice PDF workflow.

Completion check: a newly issued Boforg document sends once through the normal workflow, duplicate clicks/restarts do not cause routine duplicate delivery, staff can resolve failures, and pausing delivery leaves existing sales/payment operations usable.

### Stage 8 — Prove external-client servicing and close the release

Work:

- Gate this stage on the app's actual Advanced Access/publication and the client's valid business/number onboarding.
- Onboard a separate, invited business; verify that its account, credentials, templates, recipients, documents and receipts remain scoped to it.
- Provide a minimal business-owned document-submission path or explicit client IMS adapter before enabling its PDF sends. Validate submitted PDF type, size and ownership without fetching arbitrary supplied URLs. External clients never receive access to Boforg's unscoped Sales tables. Define and test ownership for every exposed record.
- Provide the customer-enquiry behavior promised in the submitted review description, or revise the description to match the shipped capability before submission. Route enquiries to the connected app only where that behavior has been demonstrated; do not advertise a full inbox that has not been built.
- Test another business's attempted access by direct URL, API, file path and substituted account/message IDs. Verify disconnect, token revocation, deletion and queued-job cancellation independently for each business.
- Finish operator instructions, configuration checklist, restore/rollback evidence and the implementation-status register.

Deliverable: first external-client pilot evidence and a supported, scoped onboarding/sending process.

Completion check: external client operation is proven without access to Boforg or another client's data. Scope remaining outside the first release is explicitly recorded rather than presented as delivered.

## Execution dependencies

```text
1 Baseline and eligibility
    -> 2 Scoped foundations
        -> 3 Connection flow
            -> 4 Webhooks / lifecycle / review readiness
        -> 5 PDF / delivery adapter (offline work can overlap Meta review)
3 + 4 -> live account connection, once Meta prerequisites permit it
3 + 4 + 5 + valid connection/sender/template/approval prerequisites
    -> 6 Actual PDF pilot
        -> 7 Boforg automatic production sending
        -> 8 Separate external-client pilot and final closeout
```

Parallelize independent backend, UI and verification work. Keep shared schema, migrations, worker changes, deployment and Meta mutations sequential and owned by one integrator. Every stage ends with a short report: changes, verification, real external status and next gate.

## Inputs required during execution

| Input | Handling / point needed |
|---|---|
| Meta administrator access | The owner uses the existing signed-in account for dashboard actions, verification and review submission. No password sharing. |
| App secret and token-encryption keys | Configure privately in the application's deployment secret source; never put them in chat, Git, screenshots or the plan. |
| WABA/Messaging account and phone-number IDs | Copy from the intended account's API Setup and verify against returned assets. |
| Signup configuration ID / chosen version | Obtain from the actual app configuration and pin with its matching frontend/backend parsing. |
| Staging/production deployment access | Needed for real HTTPS callbacks, worker supervision and final live verification. Local implementation can start first. |
| Controlled recipient and intended sender | Verify recipient permission and sender ownership; check phone-app continuity before changing registration. |
| Approved document templates and billing | Required before applicable real business-initiated document delivery. Track Meta decisions without promising approval dates. |
| Retention and processing choices | Decide with Boforg before live data storage. Do not declare unimplemented controls or government-request procedures to Meta. |

The account owner performs interactive Meta approvals/attestations. Prepare the actual code, URLs and evidence before those steps. No real customer messaging, publication or production activation is implied by this planning request; subsequent execution instructions determine authorized rollout scope.

## Verification and recovery contract

- Use a disposable PostgreSQL database for schema, scope and worker concurrency tests. Do not point destructive tests or demo seeding at production.
- Run targeted tests for changed integration/sales/host/privacy/job behavior, plus existing relevant regressions. Broaden testing when failures or shared-runtime changes justify it.
- Test without a network by default; mock external adapters honestly. Run live sends only with the controlled recipient and authorized rollout stage.
- Exercise credential rotation, replayed callbacks, revocation between queueing and sending, worker restart and provider timeout after acceptance.
- Keep deployment migrations additive. On rollback, pause WhatsApp sending/handlers and revert the matching application code without erasing snapshots, receipt evidence, attempt records or deduplication keys.
- Retain UNKNOWN attempts for investigation. After a backup restoration, start with outbound sending disabled independently of restored database flags. A restore can erase previously committed provider-acceptance records: reconcile the restore gap against independently retained send/receipt evidence and hold any unresolved deliveries as UNKNOWN before resuming. Restarting or restoring must not automatically replay a potentially accepted send.
- Do not claim the live system is ready from local tests alone. Capture the actual deployed connection, provider receipt, received PDF and worker-health evidence.

## Definition of complete

- Facebook Login for Business/Embedded Signup works on real HTTPS URLs for an authorized business.
- Relevant Meta approvals, permissions and number eligibility are verified, including the requested own-number coexistence route where applicable.
- Invoice and quotation PDFs reach the right recipient as attachments through the completed-document workflow.
- Delivery state, retry, unknown outcomes, opt-outs, disconnect and deletion are operational and auditable.
- Worker supervision, monitoring, rollback and targeted regression checks have been exercised on the deployment.
- The first external-client route passes business-isolation tests before it is offered in production.

## References

- [Embedded Signup implementation](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation)
- [Embedded Signup versions](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/versions)
- [Business App coexistence onboarding](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users)
- [Tech Provider onboarding](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers)
- [WhatsApp App Review](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/app-review)
- [Data deletion requirements](https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback)
- [WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/)
- Local current job runtime/runbook: `tasker/services/jobs.py`, `tasker/job_models.py`, `docs/TASKER_JOBS_RUNBOOK_2026-10-27.md`.
- Local sales/privacy/routing references: `sales/models.py`, `sales/views.py`, `sales/pdf_utils.py`, `customers/models.py`, `core/permissions.py`, `core/storage.py`, `legal/views.py`, `config/hosts.py`, `config/host_urls/`.
