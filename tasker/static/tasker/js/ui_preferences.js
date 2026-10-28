/* Per-account display preferences. No task or note content is stored. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) { root.TaskerUIPreferences = api; api.connect(root, root.document); }
})(typeof window === 'undefined' ? null : window, function () {
  'use strict';
  const NORMAL_ACTIONS = Object.freeze(['capture', 'create', 'now', 'plan', 'work', 'goals', 'search', 'help']);
  const FOCUS_ACTIONS = Object.freeze(['focus_pause', 'focus_block', 'focus_complete', 'focus_exit']);
  const ACTIONS = Object.freeze([...NORMAL_ACTIONS, ...FOCUS_ACTIONS]);
  const DEFAULT_KEYS = Object.freeze({capture: 'n', create: 'shift+n', now: 't', plan: 'p', work: 'w', goals: 'g', search: '/', help: '?', focus_pause: 'space', focus_block: 'b', focus_complete: 'c', focus_exit: 'escape'});
  let browser = null, document = null, storageKey = null, preferences = defaults(), systemDark = false;
  function defaults() { return {theme: 'system', shortcuts_enabled: true, shortcuts: {...DEFAULT_KEYS}}; }
  function parseShortcut(raw, action) {
    if (typeof raw !== 'string' || raw.length > 40) return null;
    const value = raw.trim().toLowerCase().replace(/\s*\+\s*/g, '+');
    if (!value) return {binding: '', key: '', alt: false, shift: false};
    const parts = value.split('+'), key = parts.pop();
    if (new Set(parts).size !== parts.length || parts.some(part => part !== 'alt' && part !== 'shift')) return null;
    const alt = parts.includes('alt'), shift = parts.includes('shift');
    if (!/^[a-z0-9/?]$/.test(key) && !(FOCUS_ACTIONS.includes(action) && ['space', 'escape'].includes(key))) return null;
    if (alt && ['d', 'e', 'f', 'h', 'space', 'escape'].includes(key)) return null;
    if (['space', 'escape'].includes(key) && (alt || shift)) return null;
    const actualKey = key === '/' && shift ? '?' : key;
    const actualShift = ['/', '?'].includes(actualKey) ? false : shift;
    return {binding: `${alt ? 'alt+' : ''}${actualShift ? 'shift+' : ''}${actualKey}`, key: actualKey, alt, shift: actualShift};
  }
  function validateKeymap(candidate) {
    const errors = {}, bindings = {}, used = new Map();
    for (const action of ACTIONS) {
      const parsed = parseShortcut(candidate?.[action], action);
      if (!parsed) { errors[action] = 'Use a letter, number, / or ?, optionally with Alt or Shift. Space and Escape are available in Focus.'; continue; }
      bindings[action] = parsed.binding;
      if (!parsed.binding) continue;
      const identity = `${FOCUS_ACTIONS.includes(action) ? 'focus' : 'normal'}:${parsed.binding}`;
      if (used.has(identity)) {
        errors[action] = 'This shortcut is already assigned in the same workspace.';
        errors[used.get(identity)] = 'This shortcut is already assigned in the same workspace.';
      } else used.set(identity, action);
    }
    return {valid: Object.keys(errors).length === 0, errors, bindings};
  }
  function normalizePrefs(value) {
    const result = defaults();
    if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
    if (['system', 'light', 'dark'].includes(value.theme)) result.theme = value.theme;
    if (typeof value.shortcuts_enabled === 'boolean') result.shortcuts_enabled = value.shortcuts_enabled;
    const keys = {...DEFAULT_KEYS};
    if (value.shortcuts && typeof value.shortcuts === 'object' && !Array.isArray(value.shortcuts)) {
      for (const action of ACTIONS) if (Object.hasOwn(value.shortcuts, action)) keys[action] = value.shortcuts[action];
      const checked = validateKeymap(keys);
      if (checked.valid) result.shortcuts = checked.bindings;
    }
    return result;
  }
  function resolveTheme(preference, dark) { return preference === 'dark' || (preference !== 'light' && dark) ? 'dark' : 'light'; }
  function eventBinding(event) {
    if (!event || event.ctrlKey || event.metaKey || event.isComposing || event.repeat || event.defaultPrevented) return null;
    let key = String(event.key || '').toLowerCase();
    if (key === ' ' || key === 'spacebar') key = 'space';
    if (key === 'esc') key = 'escape';
    const shift = ['/', '?', 'space', 'escape'].includes(key) ? false : !!event.shiftKey;
    return `${event.altKey ? 'alt+' : ''}${shift ? 'shift+' : ''}${key}`;
  }
  function actionForEvent(prefs, event, context = {}) {
    if (!prefs?.shortcuts_enabled || context.editable || context.dialogOpen) return null;
    const binding = eventBinding(event);
    if (!binding) return null;
    const actions = context.scope === 'focus' ? FOCUS_ACTIONS : NORMAL_ACTIONS;
    return actions.find(action => prefs.shortcuts[action] && prefs.shortcuts[action] === binding) || null;
  }
  function isEditableTarget(target) {
    return !!(target?.isContentEditable || target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"]'));
  }
  function formatShortcut(binding) {
    if (!binding) return 'Off';
    return binding.split('+').map(part => ({shift: 'Shift', alt: 'Alt', space: 'Space', escape: 'Esc'}[part] || part.toUpperCase())).join(' + ');
  }
  function updateShortcutLabels(doc, prefs = preferences) {
    doc.querySelectorAll('[data-shortcut-label]').forEach(label => { label.textContent = prefs.shortcuts_enabled ? formatShortcut(prefs.shortcuts[label.dataset.shortcutLabel]) : 'Off'; });
  }
  function get() { return normalizePrefs(preferences); }
  function read() {
    try { return normalizePrefs(JSON.parse(browser.localStorage.getItem(storageKey) || 'null')); }
    catch (_error) { return defaults(); }
  }
  function announce() {
    if (!document) return;
    document.documentElement.dataset.bsTheme = resolveTheme(preferences.theme, systemDark);
    document.documentElement.dataset.taskerTheme = preferences.theme;
    if (browser.CustomEvent) document.dispatchEvent(new browser.CustomEvent('tasker:preferences-changed', {detail: get()}));
  }
  function save(value) {
    const checked = validateKeymap(value?.shortcuts);
    if (!checked.valid || !['system', 'dark', 'light'].includes(value?.theme)) return {ok: false, errors: checked.errors};
    preferences = normalizePrefs({...value, shortcuts: checked.bindings});
    let persisted = false;
    try { browser.localStorage.setItem(storageKey, JSON.stringify(preferences)); persisted = true; } catch (_error) { /* Apply in memory and report durability honestly. */ }
    announce();
    return {ok: true, persisted, preferences: get()};
  }
  function reset() { return save(defaults()); }
  function connect(win, doc) {
    browser = win; document = doc;
    const userId = doc.documentElement?.dataset.taskerUser;
    if (!userId) return;
    storageKey = `tasker:ui:v1:${userId}`;
    preferences = read();
    let media;
    try { media = win.matchMedia('(prefers-color-scheme: dark)'); systemDark = !!media.matches; } catch (_error) { systemDark = true; }
    announce();
    const changed = event => { systemDark = !!event.matches; if (preferences.theme === 'system') announce(); };
    if (media?.addEventListener) media.addEventListener('change', changed);
    else media?.addListener?.(changed);
    win.addEventListener('storage', event => { if (event.key === storageKey || event.key === null) { preferences = read(); announce(); } });
  }
  return {ACTIONS, NORMAL_ACTIONS, FOCUS_ACTIONS, DEFAULT_KEYS, defaults, parseShortcut, validateKeymap, normalizePrefs, resolveTheme, eventBinding, actionForEvent, isEditableTarget, formatShortcut, updateShortcutLabels, connect, get, save, reset};
});
