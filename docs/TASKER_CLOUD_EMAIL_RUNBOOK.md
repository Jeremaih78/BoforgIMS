# Staff email: local implementation and staged rollout

This change is local and default off. No production deployment, production
migration, external email, recipient activation or provider test is authorized by
this document. Existing IMS customer-document mail and Django SMTP defaults are
preserved. See `TASKER_CLOUD_EMAIL_MAP.md` for the inspected baseline.

## Ownership and data flow

IMS owns source events, company membership, recipients, report proof, preferences
and calendars. Cloud owns admission, attempts, SMTP classification and transport
audit. No database is shared. A separate Cloud staff application and credential
must be provisioned; do not change the live customer-document application's token,
callback, templates, sender or scopes.

`save_task()` records a prospective email intent inside its source transaction.
The latest immutable `TaskActivity` ID identifies the occurrence, including
A-to-B-to-A reassignments. Scenario, SOP and recurring-work creation already use
this service. Existing coordination and business-action producers enter through
`create_notification()`. Arbitrary model saves and historical migrations do not
produce mail. Personal tasks without a company are excluded.

The existing `OutboxJob` worker handles `email.cloud` as an external handler.
The binding commits immutable payload/digest and `submitting` before HTTP. The
binding UUID remains the admission key. Admission means `EXTERNAL_QUEUED`, with no
accepted/delivered timestamp. A lost response is looked up by the original key.
A primary-database 404 may cause a same-key/same-payload admission replay; it is
never proof of nonacceptance or permission to create a new identity. Existing SMTP
UNKNOWN deliveries have no Cloud binding and are never converted automatically.

After SMTP accepts the DATA command with 354, but before any message-body bytes,
Cloud requests an IMS grant bound to delivery, company, tenant,
application, message and immutable payload digest. IMS checks the current source,
recipient, policy, preferences, report proof, report gate and delivery calendar.
The signed grant lasts at most five seconds. IMS outage, bad signature, mismatched
identity, expired approval or a disabled gate prevents body transmission. The
body deadline and socket timeout are bounded by both grant expiry and dispatch
lease expiry; an expired lease token never authorizes body transmission. Cloud rechecks its
lease, sender, suppressions, tenant pause and all sending gates immediately before
body transmission. Source checking and grant recording serialize on existing
source/scope rows.

The authorization linearization point is IMS's recorded allow decision. A
revocation race remains between that point and DATA initiation, bounded by the
five-second grant before body initiation. There is no distributed atomic revocation, SMTP deduplication
or recall promise. Processing/accepted/UNKNOWN cancellation reports acceptance
as possible, rather than claiming confirmed cancellation. Manager reports use a
minimal authenticated link, without report facts or private reflections.

## Staff workflow and state mapping

Open Tasker Notifications, then **Email centre**. Current previews recheck access;
old work text is not shown after access loss. The screen shows holds, receipt
versions, quarantined evidence and transport status. It never sends. Personal
preferences do not enable deployment or company policy. Reading/opening a link
does not acknowledge or complete work.

| Cloud fact | IMS ledger |
| --- | --- |
| Not admitted; policy/calendar hold | HELD |
| Durable queued/scheduled/deferred with no prior uncertain attempt | EXTERNAL_QUEUED; no acceptance timestamp |
| Processing, or any fact retaining prior acceptance uncertainty | UNKNOWN; remote state remains separately recorded |
| Confirmed `smtp_accepted` | PROVIDER_ACCEPTED; accepted_at only |
| `unknown` | UNKNOWN; reconcile, no automatic provider retry/fallback |
| Confirmed pending cancellation | CANCELLED |
| Known rejected/failed outcome | FAILED; preserve phase, evidence and original identity |

IN_APP DELIVERED retains its existing meaning. SMTP acceptance does not prove
remote delivery, inbox placement or reading. No verified Mailcow delivery/bounce
receipt adapter exists in the inspected engine; `delivery_evidence` remains NONE.
Do not invent delivered/bounced facts from an API response or a model flag.
Receipt parsing rejects unsupported delivery claims and inconsistent acceptance
facts. Same-version acceptance contradictions are quarantined. A quarantined or
superseded lookup fact cannot choose a cancellation target.

Due reminders require company opt-in and personal opt-in. Defaults are one
due-soon occurrence per due date and one overdue occurrence per task/recipient
local day. Previous-day overdue occurrences expire; they cannot catch up in a
flood. A fair per-company cursor bounds scans. A date-only due date stays a date.
Optional hourly reminder digests contain a count and an authenticated IMS link.
Each item is rechecked, invalid items are removed before admission, and empty
digests are cancelled. A changed admitted digest is cancelled when provably
unsent; any replacement gets a new identity. Unknown/accepted members are never
released for another send. Manual Cloud cancellation stays cancelled; only
confirmed source invalidation can regroup the still-valid members.

A Cloud binding permanently fences that delivery from legacy SMTP and TEST
handlers, even while the new gate is disabled. Report transport changes are
blocked while an existing Cloud admission may remain unsettled. Polling repair
uses the original binding and job; it never revives an exhausted, failed or
cancelled job budget.

## Local verification

The request path uses the existing `/api/v1/communications/messages` API, not the
illustrative `/api/communications/v1` path in the brief. Protocol 2 adds the
source guard to that API; existing invoice/quotation protocol remains compatible.
Validation retains the engine's HTTP 400 convention. Binding UUID is both the
opaque IMS delivery reference and original admission key. Cloud renders the
registered, versioned template; IMS renders only authorized template variables.
The paired JSON fixture contains exact synthetic request and receipt examples.

```mermaid
sequenceDiagram
    participant IMS as IMS source transaction / worker
    participant Cloud as Existing Cloud communications worker
    participant SMTP as Existing SMTP transport
    IMS->>IMS: Commit source event, delivery and reference job together
    IMS->>IMS: Check current eligibility; commit immutable submitting binding
    IMS->>Cloud: Signed admission with original key and payload digest
    Cloud-->>IMS: Durable queued identity (no provider acceptance)
    Cloud->>SMTP: Authenticated envelope and DATA command
    SMTP-->>Cloud: 354 ready for body
    Cloud->>IMS: Signed, identity-bound final eligibility challenge
    IMS-->>Cloud: Current allow/deny/defer and grant expiry
    Cloud->>Cloud: Recheck lease, quota, suppression and gates
    Cloud->>SMTP: Body only under a valid short grant
    SMTP-->>Cloud: Confirmed acceptance or uncertain outcome
    Cloud-->>IMS: Durable signed versioned receipt; polling repairs missed callbacks
    IMS->>IMS: Store inbox fact, validate binding, apply without resend
```

Use a new disposable PostgreSQL cluster on loopback, never an application or
production database. `tools/test_staff_email.py` ignores `.env`, blocks real SMTP
and blocks Python sockets except the selected local PostgreSQL port. Test fixtures
are synthetic; HTTP, eligibility and provider transports are injected. Default
port is 55457; set `STAFF_EMAIL_TEST_PORT`, `STAFF_EMAIL_TEST_USER` and, if needed,
`STAFF_EMAIL_TEST_PASSWORD` privately for the disposable cluster.

```text
python -B tools/test_staff_email.py test tasker cloud_communications --noinput
python -B tools/test_staff_email.py check
python -B tools/test_staff_email.py makemigrations --check --dry-run
python -B tools/test_staff_email.py check_staff_email
python -B tools/test_staff_email.py reconcile_staff_email
```

The paired fixture is `tasker/tests/fixtures/staff-email-v2.json`; Cloud keeps an
identical copy. Offline readiness does not authenticate the provider or verify a
live company/recipient roster. `reconcile_staff_email` is a read-only local ledger
summary. Automatic polling applies authenticated facts through the same binding;
it does not authorize a second provider attempt.

Use a fresh database for the full IMS suite. Existing transaction tests flush
data-migration seed rows; reusing that database with `--keepdb` can make unrelated
AI-configuration tests fail before their normal fixtures are available.

## Reviewed staging sequence

1. Preserve the dirty baseline and review only the new diff. Do not deploy the
   broader Cloud working tree. Package a concrete reviewed source artifact.
2. Back up and rehearse restore in isolation. Keep all new and report mail gates
   false. Rehearse additive IMS migrations 0033/0034/0035 and Cloud 0003. Verify that
   prior delivery states, dedupe keys and business rows remain unchanged.
3. Provision a dedicated staff application, verified existing sender and the
   `ims.staff.*` templates in a staging tenant. Grant only messages submit/read/
   cancel. Keep provider retry scope absent by default. Configure its source
   company ID, approved action origin and registered eligibility/callback URLs.
4. Create the matching IMS StaffEmailPolicy through superuser-only administration.
   Choose explicit tenant/client IDs, categories and a future activation watermark;
   keep enabled false. Report delivery additionally requires CLOUD report mode,
   explicit report audience grants and the unchanged report-email activation gate.
5. Install independent API token, eligibility HMAC and receipt HMAC secrets in
   private configuration. Refer to safe example files; no secrets belong in Git,
   browser forms or process arguments. Request credentials use the existing
   expiring signed-token scheme. Rotate with overlap; keep the same application
   identity and revoke the old token only after reconciliation. HMAC key-ID maps
   accept old/new keys during overlap; Cloud's selected key ID must match.
6. Tasker currently lives on the AI host using `config.host_urls.ai`; the IMS-only
   host has no Tasker routes. Set `TASKER_EMAIL_ORIGIN` to the approved public
   Tasker origin and `TASKER_EMAIL_URLCONF=config.host_urls.ai`. Resolve
   `ims:tasker:email_eligibility` and `ims:tasker:email_receipts` under that host;
   do not assume an `/ims/` prefix. Preserve the separate customer-document
   callback configuration. Register the exact public HTTPS source/action hosts;
   private IPs/redirects are rejected. Verify the immutable payload/action-origin
   contract with mocks.
7. Reuse the supervised Tasker worker/scheduler and Cloud communications worker.
   Inspect offline readiness, queue age, receipt lag, authorization holds, unknown
   outcomes and quarantined receipts. Do not start a second sending scheduler.
8. Obtain explicit production migration/deployment approval and separate company
   recipient/policy approval. Only then prepare an allowlisted prospective canary,
   controlled receipt test and monitored activation. These steps were not executed.

## Recovery and retention

Use the binding UUID/admission key to correlate IMS intent and Cloud message.
Company/client/tenant belong to the policy and registered service application;
message/attempt IDs and state version belong to Cloud receipts. Queue age is
measured from binding `created_at`, reconciliation age from `checked_at`, receipt
lag from receipt `received_at` and Cloud occurrence time. Group held/error codes,
UNKNOWN outcomes, quarantined facts and pending bindings into an operational
backlog. These fields support diagnostics; no new monitoring daemon is installed.
Keep routine output free of recipient addresses, subjects, bodies and credentials.
Management diagnostics require trusted shell access; the centre exposes only
the signed-in owner's records, and policy/evidence admin is superuser-only.

Disable the new staff gate/company policy first; keep the report gate disabled.
Stop only affected staff/communications consumers if required, preserving other
approved traffic. Do not change Mailcow, DNS, firewall or unrelated services.
Reconcile pending and uncertain identities before rollback/resume or any restore.
Keep additive tables, original keys, receipts and UNKNOWN evidence. Do not clear
queues, backfill historical notifications, or use SMTP as a fallback for Cloud.

Provider resend is separate from callback replay or same-key admission replay.
Unknown outcomes need operator evidence and explicit duplicate-risk review in
Cloud. Cancellation confirms only provably pending work; it cannot recall mail.
An explicitly reviewed retry preserves every earlier uncertain attempt. Its
queued/deferred receipts still report UNKNOWN acceptance certainty. Cancellation
returns acceptance_possible rather than confirmed_unsent; source revocation or a
later failed retry leaves the logical intent UNKNOWN when an earlier attempt
may have been accepted. These extra fences apply to guarded staff messages;
legacy document cancellation/retry behavior is preserved.

Payload minimization defaults to a 90-day preview (`prune_staff_email`), with a
30-day minimum. Explicit `--execute` clears completed payload content while
retaining identity/digests/receipts. Pending and UNKNOWN content is preserved.
Cloud similarly retains guarded dedupe rows while removing completed PII/PDFs.
Review company retention and backup ownership before scheduling cleanup; none is
scheduled automatically. Backups containing payloads need access control and
encryption under existing operations policy.
