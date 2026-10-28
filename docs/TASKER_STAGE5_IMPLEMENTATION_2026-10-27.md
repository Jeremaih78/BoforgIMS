# Tasker Stage 5 — Workbench and Focus

Implemented 27 October 2026, Africa/Harare, following the owner's instruction to implement the next stage while leaving API setup for later. Requirements: **T05-01–T05-07**. Stages 1–4 and the owner's business addendum remain in force.

## Delivered behavior

1. **Compact Work list and Board.** Existing `/tasks/` and task URLs remain compatible. All work, Mine, Waiting, Due soon and Unscheduled presets use current object visibility. Waiting means blocked; Due soon includes overdue open work and the next seven days. Search, priority, status, owner, category, archived and sort filters compose with presets. Both layouts show at most 24 tasks per page; Board explicitly labels its counts as the current page. The Board has its own keyboard-focusable horizontal scroller.
2. **Personal saved views and defaults.** Save current filters under a name, open, update or soft-delete that view, and save a default preset/layout. Saved filters never grant task access. Owner controls and fresh scope apply on every request. The initial default remains All work/List for compatibility.
3. **Inline and bulk triage.** Start, priority, completion review, reopening and archive controls avoid unnecessary edit-page navigation. Bulk actions also support due dates, scoped assignment and blockers. Selection is bounded to 50 server-side; the UI selects only this page. Every selected task is locked, authorized and version-checked before any write. Invalid, mixed-owner and stale selections make no partial changes. Scope/assignment validation and existing audited lifecycle services remain authoritative.
4. **Task detail drawer.** Open a title to preview criteria, context, goal, accessible source capture, checklist, owner and due date. Full task/edit links remain available; title links work normally without JavaScript. The drawer renders text safely, ignores obsolete responses, clears content on failures/closing, and fetches without caching.
5. **Definition of done and acceptance.** An additive task field records the required outcome. Task editing, progress completion, Work completion and Focus completion require deliberate acceptance when criteria exist. Work confirmation previews current outcomes and carries their revision; Focus verifies the exact criteria shown. A checked checklist alone never completes or accepts a task. Legacy tasks with no criteria retain existing service behavior; the Work review still asks for deliberate confirmation. Existing goal evidence acceptance stays separate.
6. **One Focus timer, useful context.** Direct Start reuses Focus Mode and the existing time-entry model/unique running-timer rule. Actor locking serializes simultaneous starts on different tasks. Focus shows the next checklist step, goal/source context and criteria. Optional interval targets reuse timer metadata; pause/resume preserves remaining time and reaching a target leaves the task/timer open. Users can stop their own timer from Work even after their task access changes, without exposing its former title.
7. **Private idea parking and useful transitions.** Focus can save an idea to the actor's private Stage 4 Inbox without changing the task's shared notes or timer. The controller flushes notes before actions, retains failed/uncertain drafts, retries the same idempotency key, and distinguishes access/terminal-state failures from key collisions. Completion or blocking closes timers and opens up to three executable owned tasks, ordered by priority then due date. The blocked task and its reason remain available. This replaceable fallback does not claim the capacity/dependency-aware continuous operator planned for Stage 7.

## Implementation contracts

- Models/admin/migration: `tasker/workbench_models.py`, `workbench_admin.py`, `models.py`, `admin.py`, `migrations/0015_workbench_views_definition.py`.
- Work services/views: `tasker/services/workbench.py`, `views/workbench.py`, compatible `views/tasks.py` list entry point, `urls.py`.
- Acceptance: `forms.py`, `services/tasks.py`, `services/lifecycle.py`, `signals.py`, task form/detail templates. Criteria changes are audited. Omitted criteria from older clients are preserved under lock; defined tasks require a current edit proof.
- Focus: `services/focus.py`, `views/focus.py`, `services/next_actions.py`, Focus/wrap-up templates, `focus.js`, `focus_workflow.js`, `focus_workbench.css`.
- Work presentation: `task_list.html`, `templates/tasker/work/`, `workbench.js`, `workbench.css`. Uses existing Tasker theme/shortcut infrastructure and labelled native form controls.
- Revision proofs are opaque SHA-256 digests of canonical task state and its saved timestamp. This also rejects same-clock updates. Values are converted through model field types so equivalent time/date representations agree. No task text appears in the revision token. Versions are concurrency controls, never authorization tokens.
- Company/team grants, source capture visibility and goal visibility are checked independently. Global role capabilities do not confer team access. Private parked ideas belong to the person capturing them, including a manager focusing on delegated work.
- Lists use annotations/subqueries and batched provenance lookups. Tests compare one task with 24 tasks/checklists to guard against per-row query growth. Drawer checklists are capped at 12 displayed steps; Focus displays up to 50 and states its total.
- No new notification queue, capacity engine, attendance record or inference workflow is introduced. No drag interaction is required to move work forward.

## Schema and recovery

Migration **0015** adds `Task.definition_of_done` (blank default, maximum 5,000 characters), owner-scoped audited/soft-deleted `SavedWorkView`, and `WorkbenchPreference`. It does not rewrite legacy tasks or infer criteria. Only the disposable local preview database at **127.0.0.1:55441 / ims_review** was migrated. Django tests created/destroyed **test_ims_review** on that same dedicated PostgreSQL cluster.

Deployment remains a separate release action. Apply 0015 to the intended authorized environment before serving this code, and restart application processes/refresh static assets. For application rollback, preserve the additive tables/field and revert the release through the normal deployment process. Do not reverse 0015 after users have recorded criteria or saved views without first preserving those values. Saved views are recoverable through the audited soft-delete records; archiving tasks is reversible. Do not delete capture originals, notes or time evidence to roll back the interface.

## Validation

```powershell
$env:IMS_TEST_PORT = '55441'
.\venv\Scripts\python.exe manage.py test tasker config.tests --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tasker/tests/js/*.test.js
```

- Full Tasker/configuration suite: **469 tests passed in 262.573 seconds** on isolated PostgreSQL. Evidence: `.local-review/tasker-stage1-20261021/stage5-complete.log`.
- Focused state-proof/acceptance/Work regression: **50 tests passed in 12.541 seconds**. Evidence: `stage5-version-regression.log` in the same directory.
- Final Work HTTP/shared UI rendering check after presentation polish: **21 tests passed in 9.686 seconds**. Evidence: `stage5-final-ui.log`.
- JavaScript: **77 tests passed** across capture, preferences, Focus notes/navigation/parking and Work preview/selection safety. Evidence: `stage5-javascript.log`.
- Django system checks report no issues; Tasker model/migration drift check reports no changes.
- Actual PostgreSQL cross-task simultaneous starts enforce one active timer. Permission revocation, private parking replay, stale criteria, mixed-owner bulk rollback, timer closure, pause/resume, terminal-state rejection and manual acceptance are covered.
- Review found and fixed same-clock revision collisions, equivalent time serialization, unsafe local-link controls, malformed huge saved-view IDs, read-only owner-filter visibility, stale preview responses and terminal parking retry handling.

Expected mocked AI/accounting failure logs in tests are not live provider calls. The existing dotenv line-9 parse warning is unchanged and remains deferred by the user. Offline key presence does not establish authenticated credentials. AI remains disabled.

## Browser evidence and practical limits

Used the existing synthetic local review account and three clearly labelled Stage 5 samples. Verified a saved Board view; direct Start; notes retained across checklist changes; private idea parking; pause; interval resume; 100% checklist remaining open until acceptance; completion; blocked-to-next-work transition; mobile bulk due-date change; and keyboard reopening from Work.

At **390px**, Focus fit the viewport; application client/body widths matched at **375px** on Work. The Board scrolls internally and ArrowRight moved its focused scroller. At **320px**, Work client/body widths matched at **305px** and the drawer filled the available width. Filter and action disclosures worked with Enter, and Escape closed the drawer. Board counts and pagination limits are explicit. Page-level horizontal clipping keeps offscreen UI outside the Work viewport while preserving the Board's own scrolling.

At **1440px**, client/body widths matched at **1425px** and all five Board columns fit within its **1101px** content width. The final review tab recorded no console errors. A final isolated-data assertion verified completed status, retained shared notes, exactly one private parked idea and no running sample timers.

Screenshots retained under `output/tasker-stage5/`: `work-board-desktop-1440.png`, `drawer-mobile-320.png`, `completion-mobile-390.png`, and `blocker-mobile-390.png`. The final preview uses normal viewport sizing and leaves Work available at `http://127.0.0.1:8765/tasks/` with synthetic samples. No employee data entry, real provider authentication, live email/WhatsApp delivery or operational deployment is certified. Native zoom/motion/screen-reader checks remain the documented Stage 3 acceptance work. Employee usability timing and the requested productivity uplift still require the actual baseline/pilot; test counts cannot establish them.

Stage 5 is locally verified for its non-AI software scope. **Stage 6 (capacity, planning, dependencies and arrival records) is next, only on the owner's instruction.**
