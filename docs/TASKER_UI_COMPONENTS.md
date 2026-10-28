# Tasker interface components

Stage 3, 22 October 2026. These conventions apply to the existing Django templates. They do not introduce a frontend framework or change task, privacy or goal acceptance policies.

## Scope and loading

Use `body.tasker-app` only on Tasker pages and `html[data-tasker-user]` for the authenticated user ID that selects local UI preferences. Never put a credential or private content in this attribute. `ui_preferences.js` resolves the stored system/light/dark choice before rendering. Load `ui.css` before page CSS and `shell.css` **after** page CSS. The final stylesheet adopts existing Tasker panels, forms, planner and Focus classes into the shared palette. It must not load on ordinary IMS pages.

Every selector in the Stage 3 CSS is scoped to `.tasker-app` or `html[data-tasker-user]`. Shared `static/css/app.css` is preserved. Focus remains a standalone layout and uses `body.tasker-app.tasker-focus-app` to obtain the same colors and reduced-motion behavior.

## Tokens

Tasker serves pinned Bootstrap 5.3.3 and Bootstrap Icons 1.11.3 locally from `tasker/static/tasker/vendor/`; include their fonts and licenses with static assets. Ordinary IMS pages retain their existing asset setup. Ship the matching templates and custom assets together and advance their shared cache version when changing them. Source and integrity records are in the vendor directory.

| Token | Dark | Light | Purpose |
| --- | --- | --- | --- |
| `--tk-bg` | `#0c1625` | `#f4f7fb` | Canvas |
| `--tk-surface` | `#142236` | `#ffffff` | Panels and navigation |
| `--tk-surface-raised` | `#1b2d44` | `#edf2f9` | Neutral control/indicator background |
| `--tk-text` | `#eaf0f8` | `#17283f` | Main content |
| `--tk-muted` | `#adbed2` | `#52657d` | Secondary content |
| `--tk-accent` | `#94bfff` | `#1a58ac` | Links and active destination |
| `--tk-line` | `#30435b` | `#dce4ef` | Panel separators |
| `--tk-line-strong` | `#61768f` | `#7e90a8` | Input and action boundaries |
| `--tk-success` | `#88e2b7` | `#14633e` | Saved/accepted/completed labels |
| `--tk-warning` | `#f4ce83` | `#805100` | Pending/unsaved/high priority labels |
| `--tk-danger` | `#ffacb1` | `#ab2738` | Error/blocked/critical labels |
| `--tk-focus` | `#ffd57d` | `#895900` | Visible keyboard outline |

Radius tokens are `.65rem`, `.95rem`, and `1.2rem`. Spacing tokens use `.25rem`, `.5rem`, `.75rem`, `1rem`, `1.5rem`, and `2rem`. Typography uses the platform system stack, normal line height 1.6, readable secondary text, compact uppercase section labels and restrained title sizes. Monetary and outcome values must retain the application's meaning rather than becoming decorative scores.

Reference contrast calculations using WCAG relative luminance: dark body/surface 13.96:1, dark secondary/surface 8.44:1, dark primary button 8.30:1; light body/surface 14.88:1, light secondary/surface 5.97:1 and light primary button 6.92:1. Light semantic labels on their respective soft backgrounds exceed 6:1. These calculations concern the reference pairs; they do not certify every possible content combination or browser rendering.

## Shell

- `.tasker-sidebar`: focused Now/Plan/Work/Goals navigation, then authorized Strategy and supporting pages. Active links use `aria-current="page"`; labels remain visible with icons. Unsupported destinations are hidden rather than pretending an Inbox or strategy board is complete.
- `.tasker-module-switch`: a native `details`/`summary` containing secondary IMS navigation. Keyboard access works without custom JavaScript.
- `.tasker-topbar`: current location, capture, preferences and identity controls. Long usernames truncate visually and must retain an accessible full label/title.
- `.tasker-mobile-nav`: real Now/Work/capture/Plan/Goals actions below 992px. Every item has text and a minimum 52px target. Account for its height and safe-area inset in content padding. Do not put it over Focus's primary actions.

Use relative Django Tasker routes inside the module, including localhost preview. Keep the authenticated IMS switcher separate from primary work navigation. Do not add public/private content counts to navigation unless they use the same scoped selectors as the destination.

## Reusable components

| Component | Use |
| --- | --- |
| `.tasker-section-header` | Wrapping page title and primary action group |
| `.tasker-panel` | Standard surface, border, radius and responsive padding |
| `.tasker-empty-state` | Honest empty state with explanation and usable action |
| `.tasker-state[data-state]` | `saved`, `unsaved`, `pending`, `error` status text; pair with `role="status"` or `role="alert"` as appropriate |
| `.tasker-error-summary` | Form summary with links to invalid controls |
| `.tasker-preferences-grid` | Two-column preferences, one column on mobile |
| `.tasker-icon-action` | Icon button with an explicit accessible name and 44px target |
| `.tasker-global-modal` | Bootstrap modal with theme-aware surface and scrollable content |
| `.tasker-shortcut-row` | Action label and live keyboard binding in help |
| `.tasker-filter-options` | Native Work filter/sort disclosure; search remains visible and active filters expand it |

Continue using semantic labels and native fields. Every field needs a `for`/`id` label; link validation help through `aria-describedby` and set `aria-invalid` when appropriate. Preserve the bound form after failure. Disabled controls explain their reason nearby or through an accessible description. Do not use color alone for priority, saved state, errors or completion. Text such as Critical, Blocked and Saved stays present.

Primary actions use a solid accent and an explicit verb. Secondary actions use a quiet outlined surface; destructive actions use a danger label and an appropriate confirmation page/modal. Respect existing confirmation/undo behavior; do not invent an Undo control for an irreversible action. Keep form submission handling subordinate to existing Focus note-flush guarantees.

## Accessibility and motion

Controls have a 44px minimum touch target and a 3px visible focus outline. Tables remain horizontally scrollable within their own container instead of widening the page. Main content and cards can shrink; text and names wrap. At 390px and the 320px stress width, the sidebar is offcanvas and the bottom navigation remains text-labelled. At 200% zoom, the same responsive layout can collapse naturally rather than fixing a desktop width.

When the OS requests reduced motion, Tasker disables animations/transitions and smooth scrolling, including legacy page effects. Pending work keeps a static progress indicator and visible text. Preference settings always have normal labelled controls; single-character shortcuts can be disabled or remapped, and must not act while typing or composing text.

The palette is chosen for readable normal-size content and semantic labels in both themes; use the exact token pairs for new components. Browser verification, keyboard walkthroughs and quantitative checks are recorded in the Stage 3 implementation report. This guide alone does not certify every screen-reader/browser combination or prove capture/planning time or employee productivity targets.


## Capture components (Stage 4)

`capture.css` is scoped to `.tasker-app`. Use `.capture-workspace`, `.capture-grid`, `.capture-detail-grid` and `.capture-search-row` for responsive capture screens. Advanced filters use a native disclosure; keep search visible. Global capture is private/unscheduled and must remain distinct from explicit work promotion.

The versioned editor uses `data-capture-editor`, canonical JSON and `capture_editor.js`. Do not attach the generic `data-tasker-edit-form` behavior to that editor. Mark its mutation/global-capture forms with `data-capture-action-form` so pending edits flush first. Saved title/type/version/audience labels reflect confirmed server data; a changed audience blocks draft overwrite pending review. Use `textContent` for server conflict previews. Preserve native manual saving as the no-JavaScript fallback.

Generic form helpers must read action/method/target attributes: named controls can shadow form properties. The capture promotion form has a target control and must not be stranded by a false unload warning. See the Stage 4 report for privacy, retry and migration contracts.
