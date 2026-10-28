# Boforg AI Tasker

**Plan it. Do it. Track it. Improve it.**

Boforg AI Tasker is the productivity module inside the existing Boforg project. It is not a standalone project. It shares the IMS authentication, permissions, theme, PostgreSQL database, and admin, while its canonical interface is isolated on `https://ai.boforg.co.zw/`.

## Current scope

The existing module and Stage 1 correctness update currently provide:

- Nine task planning and activity models
- Auditing and recoverable soft deletion on every model
- Admin management, including restore actions
- Object CRUD permissions plus Tasker-specific permissions
- Namespaced application routing and an IMS navigation entry
- Generic, provider-neutral AI metadata placeholders
- Database constraints and indexes for primary access patterns
- An owner-scoped Today Dashboard with progress, priorities, timeline, carry-over, and empty states
- Audited quick task creation and a persistent daily scratchpad
- Six personal AI actions: plan day, suggest next task, prioritize, summarize day, explain and break down a task; availability remains controlled by permissions/configuration
- Responsive task cards, detail pages, creation, editing, and recoverable deletion
- Search, filters, owner filtering, pagination, and safe sorting
- Checklists, comments, time entries, synchronized progress, and task activity history
- Daily planning with a focus area, motivational quote, top four priorities, selected tasks, and notes
- End of day reflection with energy, productive time, distractions, lessons, carry-over work, and tomorrow focus
- A shared responsive UI layer with toolbar quick add, loading feedback, quick actions, and reduced-motion support
- Keyboard-first navigation and task capture
- Bounded dashboard/detail queries, reusable selectors, and use-case-specific view modules
- Provider-neutral, disabled-by-default AI contracts and a minimized context builder
- Full-screen Focus Mode with a persistent timer, pause/resume/stop, checklist progress, autosaving notes, blocker capture, completion, and keyboard controls
- Idempotent Employee, Supervisor, Manager, and Administrator role provisioning

Stage 1 adds semantic/executable priority selection, complete local AI result validation, durable atomic request/token quotas, protected note saves, accurate private/shared boundaries and consistent task lifecycle/timer cleanup. See [Stage 1 implementation and checks](docs/TASKER_STAGE1_IMPLEMENTATION_2026-10-22.md).

Background workers, notifications, team/reporting structures, attendance, company-goal tracking and the weekly monetary AI cap belong to later stages. Stage 1 does not enable live AI or email/WhatsApp delivery.

## Architecture

`AuditedSoftDeleteModel` is the abstract lifecycle base for every Tasker entity. Its default `objects` manager hides soft-deleted rows; `all_objects` exposes all rows for administration and recovery.

```python
task.delete(user=request.user)       # recoverable delete
task.restore(user=request.user)      # restore
task.hard_delete()                   # permanent; use only for controlled maintenance
Task.all_objects.filter(...)         # include deleted records
```

Audit actors are nullable because deleting a user must not destroy business history. Task ownership is protected so an employee with active tasks cannot be removed without explicit reassignment. Child activity belongs to a task and is cascade-deleted only during an explicit hard delete; normal deletes remain recoverable.

`ai_metadata` and `ai_processed_at` are common extension points, while `Task` reserves `ai_summary`, `ai_recommendation`, and `ai_score`. Provider calls are owned by `tasker.ai.service.AIService`; models and views do not call the SDK directly. Personal recommendations remain advisory; selected checklist steps require an explicit authorized, version-checked apply action.

Dashboard reads are assembled by `tasker.services.dashboard.build_today_dashboard`. The service scopes every task query to the signed-in owner, keeps GET requests free of database writes, and returns a presentation-neutral data object reusable by future interfaces. Quick actions call explicit service functions and populate all ownership and audit fields.

The dashboard performs five bounded queries regardless of task volume. Task detail limits displayed time history to eight records and activity history to thirty records at the database boundary. Shared ordering and planning query composition lives in `tasker.selectors`.

Task visibility and mutation rules live in `tasker.policies`. Employees see work they own or assigned. Existing `view_all_tasks` and `manage_tasker` are global capabilities; actual team membership/reporting boundaries are Stage 2 work. Task mutations live in `tasker.services.tasks` and run atomically. Private daily plans/reviews and AI content are owner-scoped and read-only in staff admin; superusers retain privileged operational access. Shared task notes are labeled as visible to authorized task viewers.

Focus lifecycle operations live in `tasker.services.focus`, with shared state policy in `tasker.services.lifecycle`. Timer transitions use transactions and row locks; PostgreSQL also enforces one active timer per employee. Blocking, completing, cancelling, archiving or deleting a task closes sessions, including admin task deletion. Checklist completion reports steps; explicit Complete/status actions or submitting 100 to the progress action accept the task. Completed work stays at 100 until explicitly reopened. Focus Mode makes no AI or external-service calls.

Focus note POSTs require a normalized-content `base_version`; missing versions return 428, conflicts return 409 with the latest text/version, and oversized input returns 400 without truncation. Writes are serialized and uncertain identical-content retries are safe. The normal edit form also protects notes with `notes_version`. Failed saves retain the draft and block navigation; recovery drafts are isolated by user/task in tab session storage, cleared on logout/account switching. Private browser mode or denied storage may prevent local recovery and is shown honestly.

HTTP controllers are separated by use case under `tasker.views.dashboard`, `tasker.views.tasks`, and `tasker.views.planner`. `tasker.views.__init__` preserves the stable public names consumed by URL configuration.

Daily planning writes live in `tasker.services.planner`. Top priorities are capped at four and automatically included in planned work. End day review carry-over selections synchronize the task `carry_forward` flag, which feeds the existing Today Dashboard. Planner GET requests do not create records.

`TaskActivity` is the append-oriented user-facing history stream. Task model signals capture lifecycle changes consistently, while checklist, comment, and time services add semantic activity events. Activity rows remain separate from comments so the audit timeline can expand without overloading collaboration content.

## Models

| Model | Purpose |
| --- | --- |
| `Task` | Core work record, ownership, priority, lifecycle, scheduling, progress, hierarchy, and AI placeholders |
| `TaskCategory` | Reusable colour-coded task classification |
| `DailyPlan` | One employee plan per day, including focus, top priorities, planned tasks, and notes |
| `DailyReview` | End-of-day reflection linked one-to-one with a plan |
| `TaskComment` | Task discussion/history note |
| `TaskChecklistItem` | Ordered completion item within a task |
| `TaskTimeEntry` | Employee work session and duration record |
| `TaskAttachment` | Storage-ready attachment placeholder; upload UI is deferred |
| `TaskActivity` | Immutable-style task lifecycle and interaction history |

## Permissions

Django creates `add`, `change`, `delete`, and `view` permissions for every model. `Task` also defines:

- `tasker.assign_task` — assign work to another user
- `tasker.view_all_tasks` — see tasks owned by other users
- `tasker.manage_tasker` — administer the module

The module landing route requires authentication and `tasker.view_task`. The sidebar entry follows the same permission. Superusers receive implicit access. The `setup_tasker_roles` command creates or refreshes four namespaced groups without assigning users: `Tasker Employee`, `Tasker Supervisor`, `Tasker Manager`, and `Tasker Administrator`. Object-level policies continue to prevent cross-employee mutation even where a group has model permissions.

## URLs

The application is mounted at `/` on the AI host and continues to reverse as `ims:tasker:index`. The legacy website path `/ims/tasker/` redirects to the AI host. The route accepts owner-scoped dashboard reads and two named POST actions, `quick_add` and `quick_notes`.

Task management routes are under `/tasks/` on the AI host. Named routes cover list, create, detail, edit, soft delete, comments, checklist operations, time logging, and progress updates. Every mutation route is POST-only where appropriate and enforces both Django permissions and object-level policy.

Focus Mode routes sit below `/tasks/<slug>/focus/` on the AI host. State changes and note autosaves are POST-only and CSRF-protected. The page intentionally does not inherit the IMS navigation chrome.

The daily planner is available at `/planner/` on the AI host, with the end day review at `/planner/review/`. Both are restricted to the signed-in employee’s plan and use the standard `DailyPlan` and `DailyReview` model permissions.

## Keyboard shortcuts

Shortcuts work outside form fields and editable controls:

- `N` — quick-add modal
- `Shift+N` — full task creation form
- `T` — Today Dashboard
- `P` — Daily Planner, when permitted
- `/` — focus task search when present
- `?` — shortcut reference

The shared `tasker/static/tasker/js/ui.js` interaction layer also provides submit loading states and internal route feedback. Animations are disabled when the operating system requests reduced motion.

Focus Mode additionally supports Space to pause/resume, `B` to record a blocker, `C` to complete, and Escape to stop the timer and exit. Shortcuts are disabled while typing in a field.

## AI extension boundary

AI availability is controlled by `AIConfiguration`, provider configuration and permissions; Stage 1 does not change live enablement. The service, prompts/conversations, adapter, retries, streaming transport, quotas and content-free usage logging live under `tasker.ai`. See [foundation operations](tasker/README_AI_FOUNDATION.md) and [personal assistant boundaries](tasker/README_AI_PERSONAL_ASSISTANT.md). Scratchpads, task notes and private reflections are excluded from personal AI context. Six response contracts are validated locally for shape, types, IDs, positive durations and eligibility; refused/incomplete/empty provider responses fail safely.

Request and token admission use a short per-user PostgreSQL lock, not a lock held across provider calls. Each retry needs additional token capacity. Known usage settles to actual tokens; unknown failed/interrupted attempts retain conservative charges. `quota_charged_tokens` can therefore exceed provider-measured `total_tokens`. The $0.50 employee weekly monetary ledger remains Stage 9 work.

## Database setup

```powershell
python manage.py migrate tasker
python manage.py setup_tasker_roles
```

After provisioning, assign employees to the appropriate Tasker group through the existing IMS administration workflow. The command is idempotent and deliberately never changes user membership.

## Development checks

Use an explicitly disposable database and `config.test_settings`; do not substitute production. The local Stage 1 setup/commands and migration notes are in the implementation report.

Before a selective commit, run `venv\Scripts\python.exe tools/check_staged_secrets.py`. It inspects staged blobs only, blocks excluded credential paths without reading them, and prints no matched values. An empty index pass does not certify working-tree/history cleanliness or credential rotation. The authorized credential cleanup and fixed-path local-history assessment are recorded in the API key recovery guide; the excluded README remains outside general reads/diffs and staging.

```powershell
python manage.py check
python manage.py test tasker
python manage.py makemigrations --check --dry-run
```

## Future extension points

- Put task use cases and AI orchestration in `tasker/services/`.
- Keep views thin and permission-aware.
- Use `select_related`/`prefetch_related` around owner, category, checklist, and plan relationships.
- Keep planner task choices owner-scoped as future integrations add IMS-derived work.
- Add attachment type/size security policy before enabling uploads.
- Keep new task mutations routed through services so semantic activity history remains complete.

## Deployment notes

Run migrations before exposing the menu to non-superusers. File storage already follows the IMS default storage backend and is compatible with the project’s optional S3 configuration. The placeholder does not accept uploads in Milestone 1.


## Stage 4 capture Inbox (27 October 2026)

Use **Inbox** or global **Capture** (N) for private, unscheduled task ideas, ideas, notes and links. Choosing the Task capture type does not create a scheduled Task. The editor saves with versions, retains drafts on conflicts/errors, and requires a review if the sharing audience changes. Tags, pinning, archive and recoverable Trash are available.

**Turn into work** deliberately copies selected text into one Task or authorized goal initiative. Team/date choices are explicit, promotion retries reuse the same work record, and the original capture keeps its visibility. **Older scratchpads** imports one private snapshot per owned DailyPlan while leaving source notes intact. Capture works with AI disabled.

Apply additive migration `0014_capture_inbox` before releasing matching capture routes/templates/assets. See [Stage 4 implementation and recovery](docs/TASKER_STAGE4_IMPLEMENTATION_2026-10-27.md). Live API authentication, the dotenv warning and AI activation remain deferred; the $0.50 weekly monetary cap is later Stage 9 work.

## Stage 7 work operator (27 October 2026)

**Now** recommends one feasible work slice with its first action, done condition, remaining effort and reasons. **Next**, **Waiting** and **Decisions** separate alternatives, blockers and choices that make work possible. Critical work leads feasible recommendations; older long/goal-linked work gets protected progress. Configure actual work hours/buffer and remaining estimates first. Unknown values prompt decisions.

Start reuses Focus. Choose another requires a short reason; Blocked keeps the task in Waiting and asks for fresh guidance. Healthy Focus stays stable. The five-minute review and its sources are rechecked at execution. Ranking preferences are personal and versioned. Core guidance works without AI.

Apply migrations through `0017_operatorpolicy_operatorsnapshot_operatoroverride_and_more` with matching code/templates/assets. See [Stage 7 policy, verification and recovery](docs/TASKER_STAGE7_IMPLEMENTATION_2026-10-27.md). Stage 8 reliable jobs/notifications/reports and Stage 9 weekly monetary AI controls are separate.

## Stage 8 coordination and reports (27 October 2026)

Use **Request help or handoff** on a task to name an authorized resolver, requested response and expected time. Acknowledgement confirms receipt; resolution preserves the original owner and accepted-completion policy. **Coordination** and the notification bell show targeted requests, with quiet hours, Focus suppression and hourly digest preferences.

With reporting enabled, an explicit **End day** save records shared facts for versioned team reports. Managers and explicit report recipients see missing submissions, accepted work, commitments and blockers; private reflections/notes remain excluded. Email is test-only and its status is separate from generation.

Apply migrations through `0020_report_reconciliation_diagnostics` and use the Stage 8 flags deliberately. One PostgreSQL outbox serves all worker/scheduler work. See [Stage 8 behavior and evidence](docs/TASKER_STAGE8_IMPLEMENTATION_2026-10-27.md) and the [worker/scheduler runbook](docs/TASKER_JOBS_RUNBOOK_2026-10-27.md). Actual live sending and production supervision remain separate owner actions.
