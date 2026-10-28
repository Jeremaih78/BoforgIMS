# Tasker Stage 4: capture Inbox and durable notes

Implemented 26–27 October 2026, Africa/Harare. Stage 4 core capture is implemented and verified locally. The owner authorized continuing while deferring remaining API configuration. AI remains disabled; this work does not require a provider.

## Delivered behavior

- **Inbox** is now a real primary destination. Global **Capture** and the N shortcut save task ideas, ideas, notes and links. Saving creates a private, unscheduled CaptureItem; even the Task type does not create a Task or a due-today commitment.
- Captures have editable working text and an immutable original text snapshot, a type, optional title/link, tags, pinning, archive and recoverable Trash. Search includes authorized working/original text, title and URL; type/tag/pinned filters preserve scope. Mobile keeps search visible and advanced filters collapsible.
- Saved captures use versioned autosave with visible pending/saving/saved/error feedback. Only acknowledged persistence earns Saved. Saves serialize; uncertain writes retry the same snapshot before newer edits. Navigation, shortcuts and actions flush pending edits. Authentication/validation errors retain the draft and stop unsafe retry loops.
- Two-tab conflicts retain the draft and show the server copy. Choosing the saved version or explicitly replacing a same-audience version is deliberate. **If visibility or the shared team changes, overwrite is blocked** until the saved audience is reviewed. A successful acknowledgement after a sharing change also stops newer queued text from being sent to that audience.
- Manual form saving works without JavaScript. A stale save retains the bound draft, shows the current server text and requires an explicit save over its displayed current version. The conflict page does not bootstrap autosave over that choice.
- Owners can deliberately share a capture with a live authorized team. Shared captures are read-only for other authorized readers. Private captures do not become visible to managers through role names, search, source links or staff admin. Team/company scope is live; revoked access stops working. Privileged superusers/database operators retain operational access.
- **Turn into work** previews the text to copy and requires confirmation. A Task stays personal/unscheduled unless a team/date is explicitly selected. An initiative requires a goal the actor can actually manage and the existing direction flag. Repeated promotion returns the same destination, with fresh capability/access checks. Promotion preserves the original capture's visibility and adds source provenance; only authorized readers receive a clickable source link.
- **Older scratchpads** lists the owner's daily-note history, including recoverable deleted plans. Import is deliberate and permanently unique per source plan, even after the imported capture is deleted. The original DailyPlan and its notes stay intact. Re-import opens the original imported capture rather than duplicating or silently overwriting it.

Capture pages and save responses use private/no-store cache controls. No capture content enters shared organization/task audit logs or AI context. CaptureEvent records action/version/IDs only. Staff admin is owner-scoped and read-only; promoted destinations display stable IDs rather than potentially inaccessible current titles.

## Scope and limits

The optional `optional_capture_proposal` seam returns unavailable, no proposal and an explicit confirmation requirement. Actual AI extraction and proposal preview/application remain Stage 9 work. No dates or goals are inferred or committed automatically. T04-04 is therefore split: the Stage 4 seam exists; actual governed AI proposal behavior is pending.

Capture is an online server feature. Unsaved drafts remain in the open page's memory with unload protection; this is not an offline capture store. Voice/offline extensions remain conditional later work. The dedicated Focus parking lot/workbench upgrades belong to Stage 5; the existing Focus note lifecycle was preserved.

Limits are 100,000 characters for working/original text, 255 for titles, 2,048 for HTTP(S) URLs and 12 normalized tags of up to 40 characters. Oversized legacy notes are rejected for import without modifying or truncating their source. Later scratchpad changes do not overwrite an already imported snapshot; the history remains accessible separately.

## Files and contracts

| Area | Main files |
| --- | --- |
| Schema and original/source identity | `tasker/capture_models.py`, model exports in `tasker/models.py`, `tasker/migrations/0014_capture_inbox.py` |
| Locked writes, visibility, replay, promotion, import | `tasker/services/captures.py` |
| Owner-scoped read-only admin | `tasker/capture_admin.py`, registration in `tasker/admin.py` |
| Forms and HTTP views | `tasker/capture_forms.py`, `tasker/views/captures.py`, `tasker/urls.py` |
| Inbox/editor/promotion/history UI | `tasker/templates/tasker/capture/`, `tasker/static/tasker/css/capture.css` |
| Save controller and browser behavior | `tasker/static/tasker/js/capture_editor.js`, narrow `ui.js` navigation/form integration |
| Shell, legacy entry points and provenance | `templates/base.html`, Tasker navigation/context/shell/preferences/index and task/goal detail integrations |
| Regression coverage | `test_captures.py`, `test_capture_views.py`, `tests/js/capture_editor.test.js`, updated shell/UI interaction tests |

Routes: `/inbox/`, `/inbox/new/`, `/inbox/<id>/`, `/save/`, `/actions/<action>/`, `/promote/`, `/inbox/legacy/` and the per-plan import action. All use existing authentication and Tasker access. Personal capture requires Tasker workspace access; mutation additionally requires ownership. Task/initiative promotion reuses the existing work/goal permission and company/team policies. The roles command adds capture permissions for scoped admin inspection; granting a model permission does not bypass owner boundaries.

Creation has a permanent `(owner, client_key)` uniqueness constraint and retained submission fingerprint. An exact retry after edits/deletion still resolves to the original capture; reusing that key for another payload fails safely. Updates use a locked version and accept an older identical-content retry without another version increment. One-to-one promotion and legacy-source links survive soft deletion. Audit content is separate from the user text.

## Migration, release and recovery

Migration **0014_capture_inbox** adds CaptureItem/CaptureEvent, indexes and constraints. It was applied only to the previously dedicated disposable review database on loopback **55441**. Existing Task/DailyPlan records are not backfilled, cleared or automatically copied. No operational database was migrated.

For a later authorized release:

1. Back up and verify recovery; apply the additive migration before exposing the new routes/templates.
2. Deploy matching Python/templates/static assets and run the normal static collection. Custom capture assets use the matched `stage4-20261027-final3` cache version. Restart workers that cache templates. Retain local Bootstrap/icon assets from Stage 3.
3. Refresh roles through `setup_tasker_roles` under the normal access-review process. Existing personal capture uses workspace access; staff admin remains owner-scoped and read-only regardless of broad role permissions.
4. Verify capture/save/conflict, promotion, shared-reader revocation and private admin behavior in staging before real enrollment. No Stage 4 master feature flag was added; Goals/initiative promotion still respect the existing direction flag.
5. Recovery uses a matching application/static rollback or hides the Inbox/capture entry points while retaining additive tables, captures, originals, audit and promotion/source links. Do not reverse 0014 and discard newly captured data as a routine rollback. Existing legacy notes remain available.

No Git staging/commit/push, history rewrite, production deployment, paid-provider call, email or WhatsApp send was performed. No secret-file contents or raw protected credential README were inspected, displayed or edited during Stage 4.

## Verification evidence

All commands ran from `C:\Users\admin\Videos\Boforg\IMS\BoforgIMS`. Database commands explicitly set `IMS_TEST_PORT=55441` for the dedicated disposable PostgreSQL 18 cluster, base database/user `ims_review`, test database `test_ims_review`. Preview settings blank the provider key, disable AI and use local-memory email.

```powershell
$env:IMS_TEST_PORT = '55441'
.\venv\Scripts\python.exe manage.py test tasker config.tests --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations tasker --check --dry-run --settings=config.test_settings
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tasker/tests/js/ui_preferences.test.js tasker/tests/js/focus_save_reliability.test.js tasker/tests/js/capture_editor.test.js
```

| Check | Result |
| --- | --- |
| Full Tasker/configuration suite | **401 tests passed in 166.027 seconds**, PostgreSQL. Log: `.local-review/tasker-stage1-20261021/stage4-complete.log`. |
| Focused reviewed capture/shell/UI suite | **68 tests passed in 19.803 seconds** after privacy and promotion-form review. |
| Final capture HTTP/UI check | **23 tests passed in 6.031 seconds** after the final saved-link and archived-capture UI polish; log `stage4-last-ui.log` in the same review directory. |
| JavaScript | **47 passed**: 20 capture controller/browser tests, 17 shared UI/preferences tests and 10 existing Focus reliability tests. |
| Concurrency | Actual PostgreSQL threaded races for same-key creation and repeated promotion produce one capture/work record. |
| Django/migrations | No system-check issues; no Tasker model drift. |
| Scope/security | Private URL/search/admin, live sharing revocation, selected-text promotion, private originals, destination title protection, malformed payloads, CSRF, no-store and source-link boundaries pass. |
| Failure handling | Serialization, uncertain retries, stale writes, changed audiences, malformed/redirected acknowledgements and validation/access failure retention pass. |

The existing dotenv line-9 parse warning remains explicitly deferred by the owner. Expected mocked AI/accounting failure logs in the suite are not real provider calls or failed final tests. An offline key-presence check is not authentication; no AI activation is implied here.

## Browser walkthrough

Used a separate in-app review tab against `http://127.0.0.1:8765/inbox/`, with the existing synthetic review account and clearly labelled Stage 4 sample captures.

- Global capture saved a private idea with no scheduling controls or implicit task. Two tabs reproduced a stale-save conflict; the draft was retained and an explicit same-audience resolution persisted it.
- A separate tab changed the capture to a team audience. The stale private-intended draft was held, the changed audience was displayed and overwrite was disabled. The saved version was deliberately loaded and the sample capture returned to private.
- Promotion copied the selected working text into an explicitly selected team Task while retaining a private source capture. The optional due date remained blank. No task timer was started.
- At **390px**, capture worked using keyboard navigation; note save/pinning, archive, Trash, restore and return to Inbox passed. Client and scroll width both measured **375px**, excluding the scrollbar.
- At **320px**, the light-themed Inbox and filter controls remained usable with **305px** matching client/scroll widths and text-labelled bottom navigation. Filtering by Note/tag returned the expected sample.
- The N shortcut opened capture; Link capture retained the HTTP(S) URL and provided an explicit Open saved link action without fetching it. A Task-type capture stayed in Inbox until deliberate promotion.

At **1440px**, the dark Inbox measured matching client/scroll widths of **1425px**, with four labelled sample captures. The final review tab had no captured console errors. The system theme and normal default viewport were restored, temporary conflict tabs closed, and the Inbox review tab retained as the deliverable. The preview server remains available locally.

Screenshots under `output/tasker-stage4/`:

- `inbox-desktop-1440-dark.png`
- `inbox-mobile-390-light.png`
- `inbox-mobile-320-light.png`
- `capture-conflict-desktop.png`
- `audience-change-protected.png`

Native zoom, OS motion switching and a screen-reader walkthrough remain the separately documented Stage 3 acceptance checks. Network failures were simulated in automated tests rather than claimed as a live outage exercise. No employee timing or >70% productivity uplift is measured; baseline/pilot evidence remains Stage 2/13 work.

Stage 5 (workbench/Focus) starts only on the user's next instruction.
