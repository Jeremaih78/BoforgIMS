# Tasker Stage 3 implementation

22 October 2026, Africa/Harare. Stage 3 implements navigation and interface foundations in the existing Django application. Status: **implemented-verification-pending** for the native accessibility walkthroughs and employee usability evidence below. Stages 1 and 2 are preserved.

## Delivered

- Focused **Now, Plan, Work and Goals** navigation, with **Strategy** only for authorized users who have actual team or company management scope. Existing routes and authentication remain compatible. Tasker links are relative, including in the localhost preview; IMS switching is secondary.
- Responsive sidebar/offcanvas and text-labelled mobile bottom navigation with quick task access. Work search stays visible; remaining filters use a keyboard-operable native disclosure, automatically expanded when filters are active.
- Shared light/dark design tokens and components across existing task cards, forms, planner, goals and standalone Focus. Visible focus, touch targets, wrapping content, readable state labels, validation summaries and reduced-motion CSS are included.
- `/preferences/` offers system/light/dark appearance, shortcut enable/disable and individual remapping. Help labels reflect saved bindings. Preferences use browser storage scoped to the account, `tasker:ui:v1:<user id>`, without task content or credentials. Malformed or denied storage falls back safely and reports persistence honestly.
- Shortcuts pause while typing/composing, inside dialogs and on repeated key events. Ctrl/Command combinations stay with the browser. Workspace and Focus bindings have separate scopes. Focus retains its existing note-save, flush and conflict guarantees.
- Marked edit forms show unsaved/saving feedback and warn before discarding edits. Failed submissions retain bound values and focus an error summary. Loading and duplicate-submit handling respect cancelled confirmations, note-flush handlers, named submitters and restored navigation.
- New task forms default Owner to the current user, fixing a ModelForm initial-value interaction without widening delegation rights.

## Stage boundaries and privacy

Inbox remains hidden until Stage 4 implements durable tasks, ideas, notes and links. Current Quick task retains its existing semantics. Arrival stays hidden until its Stage 6 service exists.

`/strategy/` is a read-only entry to existing Stage 2 scoped team work summaries, goals and baseline evidence. The Stage 10 strategy board and scenario engine remain later work. Global capabilities alone do not grant access; revoked membership or company grants remove both navigation and direct access. The existing direction feature flag applies. Private notes and reflections are excluded.

No continuous operator, EOD email/WhatsApp pipeline or weekly monetary AI ledger is claimed here. AI remains disabled. The owner's deferred credential revocation/removal/history action remains open; excluded credential-bearing documentation and secret files were not read or changed.

## Main implementation files

| Area | Files |
| --- | --- |
| Navigation and destinations | `tasker/navigation.py`, `tasker/views/shell.py`, `tasker/context_processors.py`, `tasker/urls.py` |
| Shared shell | `templates/base.html`, `tasker/templates/tasker/shell/` |
| Preferences and Strategy | `tasker/templates/tasker/preferences.html`, `strategy_home.html` |
| Existing screen integration | Tasker index, Work, task form/detail, planner/review, direction workspace/forms/teams and `focus_mode.html` templates |
| Validation | `tasker/templates/tasker/includes/form_errors.html`, marked edit forms, `tasker/forms.py` |
| Theme and behavior | `tasker/static/tasker/css/ui.css`, `shell.css`, `tasker/static/tasker/js/ui_preferences.js`, `ui.js`; narrow keyboard integration in `focus.js` |
| Local dependencies | `tasker/static/tasker/vendor/`, including MIT licenses, source/integrity records and icon fonts |
| Regression coverage | `tasker/tests/test_shell.py`, `tasker/tests/js/ui_preferences.test.js`; existing Focus save reliability tests retained |
| Component guidance | [Tasker UI components](TASKER_UI_COMPONENTS.md) |

No Stage 3 schema change or migration is required. Stage 2 additive migrations remain in place. Only the dedicated disposable review database and synthetic data were used.

## Verification

Commands ran from `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS`. PostgreSQL was explicitly pinned to loopback **55441**, base database/user `ims_review`, test database `test_ims_review`. Preview settings keep the AI key blank, AI disabled and email in local memory.

```powershell
$env:IMS_TEST_PORT = '55441'
.\venv\Scripts\python.exe manage.py test tasker config.tests --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tasker/tests/js/ui_preferences.test.js tasker/tests/js/focus_save_reliability.test.js
```

| Check | Actual result |
| --- | --- |
| Full Tasker/configuration suite | **336 tests passed in 79.955 seconds** on PostgreSQL. Log: `.local-review/tasker-stage1-20261021/stage3-complete.log`. Mocked provider/accounting failure logs are expected test paths. |
| JavaScript | **26 tests passed**: 16 preferences/form/keyboard and 10 Focus save reliability tests. |
| Django system check | Exit 0, no issues. |
| Tasker migration drift | Exit 0, no changes detected. |
| Scope/template contracts | Relative URLs, active navigation, preferences authentication/GET-only contract, real manager scope, isolation, revocation, hidden unfinished routes, asset ordering and IMS isolation pass. |
| Local vendor integrity | Official npm package integrity verified before extracting named Bootstrap 5.3.3 and Bootstrap Icons 1.11.3 assets. File hashes/licenses retained; no install scripts ran. |
| Explicit-file whitespace checks | Passed; no unrestricted diff or excluded-file read. |

An existing dotenv parse warning at line 9 remains; its source was not read or changed. These results do not certify production settings. A final label-only alignment of direction navigation from Today to Now followed the full suite; all **16 focused shell tests passed again in 1.689 seconds** afterward (`manage.py test tasker.tests.test_shell --settings=config.test_settings --noinput`).

## Actual browser evidence

A separate in-app review tab used synthetic data. Original tabs and scratchpad content were preserved. Preview: **http://127.0.0.1:8765/tasks/**.

| Walkthrough | Observed result |
| --- | --- |
| 1440px desktop, light/dark | Work cards, navigation and themes render correctly without page overflow. Strategy shows only the authorized sample team and existing measurement plan. |
| 390px mobile | Keyboard bottom links reach Now, Work and Goals. Offcanvas/quick task work; Work search stays on one line, with compact filters. Client and scroll widths both 375px, excluding scrollbar. |
| 320px stress | Work and Preferences fit at client/scroll width 305px. Menu-to-Preferences and Quick task open/close work. |
| Filter disclosure | Enter expands/collapses labelled filter controls. |
| Theme persistence | Saved Light survives reload; topbar theme toggle applies immediately. System defaults restored at the end. |
| Shortcut remapping | Alt+W navigates from Preferences to Work. Focus Alt+B opens the blocker dialog; old B does not. Escape closes the dialog without leaving Focus. No timer started. |
| Preference validation | Duplicate binding produces a focused error, retains values and does not apply the pending theme change. |
| Disable/reset | Disabled N does not open capture. Reset restores default bindings, and N opens capture again. |
| Typing protection | N in Search does not open capture; N from main does, focusing its title. |
| Failed form/correction | Blank title focuses an error summary and retains description. Correcting it creates the synthetic “Stage 3 interface review” task. |
| Browser console | No captured errors in the final walkthrough. |

Saved screenshots in `output/tasker-stage3/`:

- `work-desktop-1440-dark.png`
- `work-desktop-1440-light.png`
- `work-mobile-390-light.png`
- `work-mobile-320-system.png`
- `goals-mobile-390-light.png`

Screenshots show sample data, not employee performance, at the stated test viewport sizes. The browser connection became unavailable during the final attempt to reset its temporary viewport; the reset and keeping the review tab open could not be confirmed. The preview server was independently confirmed listening on 8765 afterward.

## Remaining acceptance checks

- Native **200% browser zoom** remains unverified; narrow viewport tests do not certify native zoom.
- An **OS reduced-motion toggle walkthrough** remains pending. Scoped reduced-motion CSS is implemented; system theme media changes are covered by JavaScript tests.
- A **screen-reader walkthrough** remains pending. Semantic labels, active-page semantics, keyboard actions and validation focus were inspected, without claiming blanket accessibility certification.
- Actual employee capture/planning/admin timings and the **>70% productivity objective remain unmeasured**, requiring baseline and pilot observation. Passing software tests does not establish productivity uplift.
- Stage 1 credential cleanup was subsequently recorded on 26 October in [key recovery](TASKER_API_KEY_RECOVERY.md). Replacement configuration/authentication and Stage 2 real roster/metric/baseline adoption remain separate actions.

## Rollout and recovery

1. Review this report, the [component guide](TASKER_UI_COMPONENTS.md), [matrix](TASKER_REQUIREMENTS_MATRIX.md) and [status ledger](TASKER_IMPLEMENTATION_STATUS.md). Complete native accessibility checks before declaring the whole stage verified.
2. Release matching templates and Tasker assets together through the normal authorized process. Include all local vendor assets/fonts in the project's normal `collectstatic`. No operational deployment or static collection was performed here.
3. Custom assets use matched query version `stage3-20261022-final`. Advance it together when changing assets to avoid stale scripts leaving preferences disabled. Restart/reload template-caching workers, then verify preferences and quick task.
4. `TASKER_DIRECTION_ENABLED` controls Goals/Strategy, not the entire shell. No Stage 3 master flag was added. Recover the shell with a matching-code release rollback.
5. Restore matching pre-Stage-3 UI files together if required, preserving Stage 1 reliability fixes and Stage 2 authorization/data. Browser preferences can reset through Preferences. No Stage 3 database rollback is needed.
6. Keep provider activation, credentials, real rosters and pilot enrollment under their existing separate actions. No Git commit/push, deployment, paid-provider call, email or WhatsApp send occurred.

Stage 4 is capture Inbox and durable notes/ideas/links. It starts only on the user's request; it is not delivered by this interface work.
