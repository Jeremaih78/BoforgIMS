# Boforg AI Tasker

**Plan it. Do it. Track it. Improve it.**

Boforg AI Tasker is the productivity module inside the existing Boforg project. It is not a standalone project. It shares the IMS authentication, permissions, theme, PostgreSQL database, and admin, while its canonical interface is isolated on `https://ai.boforg.co.zw/`.

## Current scope

Phase 1 milestones 1 through 6 currently provide:

- Nine task planning and activity models
- Auditing and recoverable soft deletion on every model
- Admin management, including restore actions
- Object CRUD permissions plus Tasker-specific permissions
- Namespaced application routing and an IMS navigation entry
- Generic, provider-neutral AI metadata placeholders
- Database constraints and indexes for primary access patterns
- An owner-scoped Today Dashboard with progress, priorities, timeline, carry-over, and empty states
- Audited quick task creation and a persistent daily scratchpad
- Disabled AI action placeholders with no provider calls
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

Automation, background work, notifications, and user-facing AI productivity features are intentionally not implemented yet. Phase 2 Milestone 1 adds only the disabled-by-default AI service foundation and internal usage governance.

## Architecture

`AuditedSoftDeleteModel` is the abstract lifecycle base for every Tasker entity. Its default `objects` manager hides soft-deleted rows; `all_objects` exposes all rows for administration and recovery.

```python
task.delete(user=request.user)       # recoverable delete
task.restore(user=request.user)      # restore
task.hard_delete()                   # permanent; use only for controlled maintenance
Task.all_objects.filter(...)         # include deleted records
```

Audit actors are nullable because deleting a user must not destroy business history. Task ownership is protected so an employee with active tasks cannot be removed without explicit reassignment. Child activity belongs to a task and is cascade-deleted only during an explicit hard delete; normal deletes remain recoverable.

AI is represented only by empty data fields. `ai_metadata` and `ai_processed_at` are common extension points, while `Task` reserves `ai_summary`, `ai_recommendation`, and `ai_score`. No model calls an AI provider. A future service layer should own provider calls and orchestration rather than model methods or views.

Dashboard reads are assembled by `tasker.services.dashboard.build_today_dashboard`. The service scopes every task query to the signed-in owner, keeps GET requests free of database writes, and returns a presentation-neutral data object reusable by future interfaces. Quick actions call explicit service functions and populate all ownership and audit fields.

The dashboard performs five bounded queries regardless of task volume. Task detail limits displayed time history to eight records and activity history to thirty records at the database boundary. Shared ordering and planning query composition lives in `tasker.selectors`.

Task visibility and mutation rules live in `tasker.policies`; views do not reproduce ownership checks. Employees see work they own or assigned, while `view_all_tasks` grants team visibility and `manage_tasker` grants team mutation authority. Task mutations live in `tasker.services.tasks` and run atomically.

Focus lifecycle operations live in `tasker.services.focus`. Timer transitions use transactions and row locks; PostgreSQL also enforces one active timer per employee. Completing, cancelling, archiving, or deleting a task closes open sessions so timers cannot become orphaned. Focus Mode remains entirely local to Tasker and makes no AI or external-service calls.

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

AI remains disabled for employees. The provider-neutral service, prompt/conversation managers, OpenAI adapter, retries, streaming transport, quotas and content-free usage logging live under `tasker.ai`. Foundation operations are documented in [tasker/README_AI_FOUNDATION.md](tasker/README_AI_FOUNDATION.md), with data-minimization rules in [tasker/README_AI_Extensions.md](tasker/README_AI_Extensions.md).

## Database setup

```powershell
python manage.py migrate tasker
python manage.py setup_tasker_roles
```

After provisioning, assign employees to the appropriate Tasker group through the existing IMS administration workflow. The command is idempotent and deliberately never changes user membership.

## Development checks

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
