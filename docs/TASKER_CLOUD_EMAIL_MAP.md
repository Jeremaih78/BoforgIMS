# Tasker email integration: inspected implementation map

Baseline 3 November 2026: IMS `release/ims-20261102-audited` at
`611ca9f77caf42471071515163e4bc719ce878a7`; Cloud
`feature/intelligent-communications` at `2ff4174414f79fd413d3c02cf6c06cab8a6e3cc5`.
IMS already has one documentation change; Cloud has 39 tracked modifications and
305 untracked files. Preserve these working copies. A private hash manifest and
original copies of touched files are retained in the task workspace. No branch
switch, reset, commit, production access or live activation is part of this work.

| Existing evidence | Reuse and required extension |
| --- | --- |
| `tasker/services/report_email.py`: `_check`, `_policy_ready`, `handle_smtp_delivery` | Preserve report grants/source proof, deployment gate and durable SMTP UNKNOWN. Add an explicit CLOUD policy path; admission never means provider acceptance. |
| `tasker/notification_models.py`: `NotificationDelivery` | Keep the business-intent ledger and one-source constraint; add a protected Cloud transport binding and durable receipt inbox. |
| `tasker/services/notifications.py`: `enqueue_delivery`, `delivery_window`, `deliver_digest` | Preserve in-app semantics; reuse calendars and current authorization. Email categories require explicit policy. |
| `tasker/services/tasks.py`: `save_task`; `coordination.py`: `dispatch_event` | Add transactional prospective intent creation; no model-save email signal or historical backfill. |
| `tasker/services/jobs.py`: `HANDLERS`, `EXTERNAL_HANDLERS`, `scheduler_tick` | Reuse reference-only leased jobs and the existing scheduler. |
| IMS `cloud_communications/` | Keep existing customer-document email integration unchanged. A dedicated staff application/credential avoids changing its live callback or allowed templates. |
| Cloud `apps/communications/auth.py`, `api.py`, `services.py` | Reuse signed scoped credentials, primary-database atomic admission, immutable payload digests, sender/template controls and rate limits. Add a guarded contract and scoped lookup by key. |
| Cloud `worker.py`, `transports.py` | Keep durable attempt uncertainty and SMTP classification. For guarded messages require fresh IMS authorization immediately before DATA, with no uncertain fallback. |
| Cloud `callbacks.py`, `services.audit` | Extend durable signed receipts with explicit identities and state versions. Preserve polling as recovery. |
| Cloud `prune_communications.py` | Preserve guarded dedupe tombstones; never erase unknown or pending work. |

The live deployment described by repository documentation has not been inspected.
All new gates default off. Tests use a new loopback-only disposable PostgreSQL
cluster and injected network transports. Production secrets and `.env` files are
not copied into this work.
