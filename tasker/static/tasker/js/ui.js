(() => {
  'use strict';
  const api = window.TaskerUIPreferences;
  const loader = document.getElementById('taskerRouteLoader');
  const config = document.getElementById('taskerUIConfig')?.dataset || {};
  const busyForms = new Set();
  const editForms = new Map();
  const showLoader = () => loader?.classList.add('is-active');
  const defer = callback => Promise.resolve().then(callback);
  const dialogOpen = () => !!document.querySelector('.modal.show, .offcanvas.show, dialog[open], [role="dialog"][aria-modal="true"]');
  const setStatus = (element, text) => { if (element && element.textContent !== text) element.textContent = text; };
  document.querySelectorAll('[data-tasker-edit-form]').forEach(form => {
    const state = {dirty: !!form.querySelector('.errorlist, [aria-invalid="true"]'), submitting: false, status: form.querySelector('[data-tasker-form-status]')};
    editForms.set(form, state);
    const changed = () => { state.dirty = true; state.submitting = false; setStatus(state.status, 'Unsaved changes.'); };
    form.addEventListener('input', changed);
    form.addEventListener('change', changed);
  });
  function clearTransientState() {
    loader?.classList.remove('is-active');
    for (const form of busyForms) {
      delete form.dataset.taskerSubmitting; form.removeAttribute('aria-busy');
      form.querySelectorAll('[data-tasker-busy-button]').forEach(button => { button.removeAttribute('aria-busy'); delete button.dataset.taskerBusyButton; });
      form.querySelectorAll('[data-tasker-submit-spinner]').forEach(spinner => spinner.remove());
      const state = editForms.get(form);
      if (state) { state.submitting = false; if (state.dirty) setStatus(state.status, 'Your changes are still here. Review before saving again.'); }
    }
    busyForms.clear();
  }
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      if (form.dataset.taskerSubmitting === 'true') { event.preventDefault(); return; }
      // A notes/confirmation handler may cancel or defer this event later.
      defer(() => {
        // Named controls such as "target" and "action" can shadow form
        // properties. Read attributes so promotion still marks a real save.
        const target = form.getAttribute('target') || '_self';
        if (event.defaultPrevented || target !== '_self' || form.getAttribute('method') === 'dialog') return;
        let action;
        try { action = new URL(form.getAttribute('action') || window.location.href, window.location.href); } catch (_error) { return; }
        if (action.origin !== window.location.origin) return;
        form.dataset.taskerSubmitting = 'true'; form.setAttribute('aria-busy', 'true'); busyForms.add(form);
        const state = editForms.get(form);
        if (state) { state.submitting = true; setStatus(state.status, 'Saving changes…'); }
        const button = event.submitter || form.querySelector('button[type="submit"], input[type="submit"]');
        if (button && !button.disabled) {
          // Never disable a successful submitter: its name/value selects some
          // server actions. Prevent repeated submission at the form instead.
          button.dataset.taskerBusyButton = 'true'; button.setAttribute('aria-busy', 'true');
          if (button.tagName === 'BUTTON') {
            const spinner = document.createElement('span'); spinner.className = 'tasker-submit-spinner';
            spinner.dataset.taskerSubmitSpinner = 'true'; spinner.setAttribute('aria-hidden', 'true'); button.prepend(spinner);
          }
        }
        showLoader();
      });
    });
  });
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || link.target && link.target !== '_self' || link.hasAttribute('download')) return;
    let url;
    try { url = new URL(link.href, window.location.href); } catch (_error) { return; }
    if (url.origin !== window.location.origin || !/^https?:$/.test(url.protocol) || url.hash || url.href === window.location.href) return;
    defer(() => { if (!event.defaultPrevented) showLoader(); });
  });
  window.addEventListener('pageshow', clearTransientState);
  window.addEventListener('popstate', clearTransientState);
  window.addEventListener('beforeunload', event => {
    if (![...editForms.values()].some(state => state.dirty && !state.submitting)) return;
    event.preventDefault(); event.returnValue = '';
    // If the user cancels leaving, the browser remains on this page and runs
    // this callback. A cancelled navigation must not strand a busy form.
    window.setTimeout?.(clearTransientState, 0);
  });
  document.querySelectorAll('.task-card, .tasker-card, .planner-panel').forEach((element, index) => { element.dataset.taskerAnimate = ''; element.style.setProperty('--stagger', Math.min(index, 8)); });
  function openModal(id, focusId) {
    const element = document.getElementById(id);
    if (!element || !window.bootstrap?.Modal) return false;
    if (focusId) element.addEventListener('shown.bs.modal', () => document.getElementById(focusId)?.focus(), {once: true});
    window.bootstrap.Modal.getOrCreateInstance(element).show(); return true;
  }
  function navigate(url) { if (!url) return false; const go = () => window.location.assign(url); if (window.TaskerCaptureEditor?.active) window.TaskerCaptureEditor.active.navigate(go); else go(); return true; }
  document.addEventListener('keydown', event => {
    if (!api) return;
    const action = api.actionForEvent(api.get(), event, {scope: 'normal', editable: api.isEditableTarget(event.target), dialogOpen: dialogOpen()});
    let handled = false;
    if (action === 'capture') handled = document.getElementById('captureModal') ? openModal('captureModal', 'id_quick_capture-content') : openModal('quickAddModal', 'globalTaskTitle');
    else if (action === 'create') handled = navigate(config.createUrl);
    else if (action === 'now') handled = navigate(config.todayUrl);
    else if (action === 'plan') handled = navigate(config.plannerUrl);
    else if (action === 'work') handled = navigate(config.workUrl);
    else if (action === 'goals') handled = navigate(config.goalsUrl);
    else if (action === 'help') handled = openModal('taskerShortcutsModal');
    else if (action === 'search') {
      const search = document.querySelector('[data-task-search]');
      if (search) { search.focus(); handled = true; }
      else if (config.workUrl) { const url = new URL(config.workUrl, window.location.href); url.searchParams.set('focus', 'search'); handled = navigate(url.href); }
    }
    if (handled) event.preventDefault();
  });
  if (new URL(window.location.href).searchParams.get('focus') === 'search') document.querySelector('[data-task-search]')?.focus();
  const serverErrors = document.querySelector('[data-tasker-error-summary]');
  if (serverErrors && !serverErrors.hidden && !document.querySelector('#quickAddModal[data-reopen="true"]')) {
    serverErrors.setAttribute('tabindex', '-1'); serverErrors.focus();
  }
  if (!api) return;
  const form = document.getElementById('taskerPreferencesForm');
  const status = document.getElementById('taskerPreferenceStatus');
  const errorSummary = document.getElementById('taskerPreferenceErrors');
  let dirtyPreferences = false;
  function field(name) { return form?.elements.namedItem(name); }
  function fillPreferences() {
    const prefs = api.get(); api.updateShortcutLabels(document, prefs);
    if (!form || dirtyPreferences) return;
    field('theme').value = prefs.theme; field('shortcuts_enabled').checked = prefs.shortcuts_enabled;
    for (const action of api.ACTIONS) if (field(`shortcut_${action}`)) field(`shortcut_${action}`).value = prefs.shortcuts[action];
  }
  function clearErrors() {
    if (errorSummary) { errorSummary.hidden = true; errorSummary.replaceChildren(); }
    form?.querySelectorAll('[aria-invalid="true"]').forEach(input => input.removeAttribute('aria-invalid'));
  }
  function showErrors(errors) {
    if (!errorSummary) return;
    errorSummary.hidden = false;
    const title = document.createElement('p'); title.textContent = 'Preferences have not been saved. Correct these shortcuts:'; errorSummary.append(title);
    const list = document.createElement('ul');
    for (const [action, message] of Object.entries(errors)) {
      const input = field(`shortcut_${action}`); if (!input) continue;
      input.setAttribute('aria-invalid', 'true');
      const item = document.createElement('li'), link = document.createElement('a');
      link.href = `#${input.id}`; link.textContent = `${input.labels?.[0]?.textContent || action}: ${message}`; item.append(link); list.append(item);
    }
    errorSummary.append(list); errorSummary.setAttribute('tabindex', '-1'); errorSummary.focus();
  }
  function reportSave(result) {
    if (status) status.textContent = result.persisted ? 'Preferences saved in this browser.' : 'Preferences applied for this tab. Browser storage is unavailable, so these settings may reset when you reload.';
  }
  fillPreferences(); document.addEventListener('tasker:preferences-changed', fillPreferences);
  if (form) {
    form.querySelectorAll('[data-tasker-preferences-submit], [data-tasker-reset-preferences]').forEach(button => { button.disabled = false; });
    form.addEventListener('input', () => { dirtyPreferences = true; if (status) status.textContent = 'Unsaved preferences.'; });
    form.addEventListener('submit', event => {
      event.preventDefault(); clearErrors();
      const candidate = {theme: field('theme').value, shortcuts_enabled: field('shortcuts_enabled').checked, shortcuts: {}};
      for (const action of api.ACTIONS) candidate.shortcuts[action] = field(`shortcut_${action}`)?.value ?? api.get().shortcuts[action];
      const result = api.save(candidate);
      if (!result.ok) { showErrors(result.errors); return; }
      dirtyPreferences = false; fillPreferences(); reportSave(result);
    });
    form.querySelector('[data-tasker-reset-preferences]')?.addEventListener('click', () => { clearErrors(); dirtyPreferences = false; const result = api.reset(); fillPreferences(); reportSave(result); });
  }
  document.querySelectorAll('[data-tasker-toggle-theme]').forEach(button => button.addEventListener('click', () => {
    const current = api.get(); current.theme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
    const result = api.save(current); if (result.ok) reportSave(result);
  }));
})();
