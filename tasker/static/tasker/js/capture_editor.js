(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) {
    root.TaskerCaptureEditor = api;
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => api.install(root), {once: true});
    else api.install(root);
  }
})(typeof window === 'undefined' ? null : window, function () {
  'use strict';
  const KINDS = ['TASK', 'IDEA', 'NOTE', 'LINK'];
  const normalizeText = value => typeof value === 'string' ? value.replace(/\r\n?/g, '\n') : '';
  const validVersion = value => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
  function normalizeItem(item) {
    const tags = Array.isArray(item.tags) ? item.tags : typeof item.tags === 'string' ? item.tags.split(',') : [];
    return {
      kind: typeof item.kind === 'string' ? item.kind : 'NOTE',
      title: normalizeText(item.title), content: normalizeText(item.content), url: normalizeText(item.url),
      tags: [...new Set(tags.filter(tag => typeof tag === 'string').map(tag => tag.trim()).filter(Boolean))],
      pinned: item.pinned === true,
    };
  }
  const fingerprint = item => JSON.stringify(normalizeItem(item));
  function audience(item) {
    if (item?.visibility === 'PRIVATE') return {visibility: 'PRIVATE', shared_team_id: null, shared_team_name: ''};
    if (item?.visibility === 'TEAM') return {visibility: 'TEAM', shared_team_id: item.shared_team_id,
      shared_team_name: typeof item.shared_team_name === 'string' ? item.shared_team_name : ''};
    return null;
  }
  const sameAudience = (first, second) => first?.visibility === second?.visibility
    && first?.shared_team_id === second?.shared_team_id;
  const validItem = item => !!item && typeof item === 'object' && KINDS.includes(item.kind)
    && ['title', 'content', 'url'].every(name => typeof item[name] === 'string')
    && Array.isArray(item.tags) && item.tags.every(tag => typeof tag === 'string') && typeof item.pinned === 'boolean';

  function createController(options) {
    const schedule = options.schedule || ((callback, delay) => setTimeout(callback, delay));
    const cancel = options.cancel || (id => clearTimeout(id));
    let version = Number(options.version), saved = normalizeItem(options.initialItem);
    let reviewedAudience = audience(options.initialItem);
    let generation = 0, pending = null, running = null, conflict = null, stopped = null;
    let dirty = false, disposed = false, debounceTimer = null, retryTimer = null, retryDelay = 2000;
    let currentState = {phase: 'saved', message: 'Saved.', version, dirty: false};
    if (!validVersion(version)) throw new Error('A saved capture version is required.');
    function state(phase, message, extra) {
      currentState = {phase, message, version, audience: reviewedAudience, dirty: dirty || !!pending, ...extra};
      if (!disposed) options.onState?.(currentState);
    }
    const read = () => normalizeItem(options.read());
    function clearTimers() {
      if (debounceTimer !== null) cancel(debounceTimer);
      if (retryTimer !== null) cancel(retryTimer);
      debounceTimer = retryTimer = null;
    }
    function retryLater() {
      if (disposed || conflict || stopped) return;
      if (retryTimer !== null) cancel(retryTimer);
      const delay = retryDelay;
      retryDelay = Math.min(retryDelay * 2, 30000);
      retryTimer = schedule(() => { retryTimer = null; void flush(); }, delay);
      state('error', `Not saved. Retrying in ${delay / 1000} seconds. Your draft is still in this page; keep it open.`, {retryable: true});
    }
    async function saveLoop() {
      while (!disposed && (dirty || pending)) {
        if (conflict || stopped) return false;
        // An uncertain request may already have committed. Replay that exact
        // payload/version before sending any newer text.
        pending ||= {item: read(), version, generation};
        state('saving', 'Saving…');
        let result;
        try { result = await options.send({...pending.item, version: pending.version}); }
        catch (_error) { retryLater(); return false; }
        if (disposed) return false;
        const data = result?.data, status = Number(result?.status || 0);
        if (status === 409 && data?.conflict === true && validVersion(data.version) && validItem(data.item)) {
          const currentAudience = audience(data.item);
          conflict = {item: normalizeItem(data.item), version: Number(data.version), audience: currentAudience,
            audienceChanged: !!reviewedAudience && !!currentAudience && !sameAudience(reviewedAudience, currentAudience)};
          pending = null; dirty = true;
          state('conflict', conflict.audienceChanged ? 'Sharing changed. Saving is paused; review the new audience before continuing. Your draft is retained.'
            : 'Changed in another window. Compare the saved version with your draft below.');
          options.onConflict?.(conflict);
          return false;
        }
        if (status === 400 || status === 422) {
          pending = null; stopped = 'validation';
          state('validation', 'Not saved. Correct the highlighted fields, then save again. Your draft is still here.', {errors: data?.errors || {}, retryable: true});
          return false;
        }
        if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
          pending = null; stopped = 'access';
          state('error', 'Not saved. Check your sign-in and access, then retry. Keep this page open to preserve your draft.', {retryable: true});
          return false;
        }
        if (result?.redirected) {
          pending = null; stopped = 'access';
          state('error', 'Not saved. Sign in again in another tab, then retry. Your draft is still in this page.', {retryable: true});
          return false;
        }
        if (status < 200 || status >= 300 || data?.ok !== true || !validVersion(data.version)
            || Number(data.version) < pending.version || !validItem(data.item)) {
          retryLater(); return false;
        }
        const sent = pending;
        version = Number(data.version); saved = normalizeItem(data.item); pending = null;
        const currentAudience = audience(data.item);
        if (reviewedAudience && currentAudience && !sameAudience(reviewedAudience, currentAudience)) {
          // An uncertain write can be acknowledged after another tab shares
          // it. Never dispatch a newer queued draft into that new audience.
          conflict = {item: saved, version, audience: currentAudience, audienceChanged: true};
          dirty = true;
          state('conflict', 'Sharing changed. Saving is paused; review the new audience before continuing. Your draft is retained.');
          options.onConflict?.(conflict);
          return false;
        }
        reviewedAudience = currentAudience || reviewedAudience;
        // Only adopt server-normalized fields when no newer edit occurred.
        // A delayed response must never replace a user's newer draft.
        if (generation === sent.generation && fingerprint(read()) === fingerprint(sent.item)) options.write?.(saved);
        dirty = fingerprint(read()) !== fingerprint(saved);
        retryDelay = 2000;
      }
      if (disposed) return false;
      state('saved', 'Saved.');
      return true;
    }
    function flush() {
      clearTimers();
      if (disposed || conflict || stopped) return Promise.resolve(false);
      if (running) return running;
      if (!dirty && !pending) {
        if (currentState.phase !== 'saved' || currentState.version !== version) state('saved', 'Saved.');
        return Promise.resolve(true);
      }
      running = saveLoop().finally(() => { running = null; });
      return running;
    }
    function change() {
      if (disposed) return;
      generation += 1;
      dirty = fingerprint(read()) !== fingerprint(saved) || !!pending;
      if (stopped === 'validation') { stopped = null; pending = null; }
      if (conflict) {
        state('conflict', 'Your draft is retained. Choose how to resolve the saved-version conflict below.');
        return;
      }
      if (stopped) { state('error', 'Your draft is retained. Restore your sign-in or access, then retry.', {retryable: true}); return; }
      if (!dirty) { clearTimers(); state('saved', 'Saved.'); return; }
      if (retryTimer !== null) return;
      if (debounceTimer !== null) cancel(debounceTimer);
      state('pending', 'Unsaved changes. Saving shortly…');
      debounceTimer = schedule(() => { debounceTimer = null; void flush(); }, 600);
    }
    async function retry() {
      if (conflict || disposed) return false;
      stopped = null;
      dirty = fingerprint(read()) !== fingerprint(saved) || !!pending;
      return flush();
    }
    function useServer() {
      if (!conflict || disposed) return false;
      clearTimers(); version = conflict.version; saved = conflict.item;
      reviewedAudience = conflict.audience || reviewedAudience;
      conflict = pending = stopped = null; generation += 1;
      options.write?.(saved); dirty = false;
      options.onConflict?.(null); state('saved', 'Saved version loaded.');
      return true;
    }
    function keepMine() {
      if (!conflict || disposed) return Promise.resolve(false);
      if (conflict.audienceChanged) {
        state('conflict', 'Sharing changed. Copy your draft before loading the saved version, or make the capture private in another tab.');
        return Promise.resolve(false);
      }
      clearTimers(); version = conflict.version; saved = conflict.item;
      reviewedAudience = conflict.audience || reviewedAudience;
      conflict = pending = stopped = null; generation += 1;
      dirty = fingerprint(read()) !== fingerprint(saved);
      options.onConflict?.(null);
      return flush();
    }
    return {
      change, flush, retry, useServer, keepMine,
      isDirty: () => dirty || !!pending,
      isBlocked: () => !!conflict || !!stopped,
      getVersion: () => version,
      getState: () => ({...currentState}),
      destroy: () => { disposed = true; clearTimers(); },
    };
  }

  function install(win) {
    const doc = win.document, form = doc.querySelector('[data-capture-editor]');
    if (!form || form.dataset.captureInitialized === 'true') return null;
    const status = doc.getElementById('captureSaveStatus'), error = doc.getElementById('captureSaveError');
    const panel = doc.getElementById('captureConflict'), serverContent = doc.getElementById('captureServerContent');
    const retryButton = doc.querySelector('[data-capture-retry]');
    const audienceLabel = doc.getElementById('captureAudience');
    function showAudience(value) {
      if (!value) return;
      doc.querySelectorAll('[data-capture-visibility-label]').forEach(label => { label.textContent = value.visibility === 'TEAM' ? 'Shared with team' : 'Private'; });
      if (!audienceLabel) return;
      audienceLabel.textContent = value.visibility === 'TEAM'
        ? `Shared with ${value.shared_team_name || 'the selected team'}. Team members and authorized managers can read current and original text.`
        : 'Private to you in Tasker. Privileged system administrators retain operational access.';
    }
    let initial;
    try { initial = JSON.parse(doc.getElementById('captureInitialData').textContent); }
    catch (_error) {
      if (status) status.textContent = 'Autosave is unavailable. Keep this page open and use the Save button.';
      return null;
    }
    const input = name => form.elements.namedItem(name);
    function read() {
      return {kind: input('kind').value, title: input('title').value, content: input('content').value,
        url: input('url').value, tags: input('tags').value, pinned: input('pinned').checked};
    }
    function write(item) {
      for (const name of ['kind', 'title', 'content', 'url']) input(name).value = item[name];
      input('tags').value = item.tags.join(', '); input('pinned').checked = item.pinned;
    }
    function syncVersion(version) {
      form.dataset.version = String(version);
      const field = input('version');
      if (field) field.value = String(version);
      doc.querySelectorAll('[data-capture-action-form] [name="version"]').forEach(field => { field.value = String(version); });
    }
    function clearErrors() {
      form.querySelectorAll('[aria-invalid="true"]').forEach(field => field.removeAttribute('aria-invalid'));
      if (error) { error.hidden = true; error.textContent = ''; }
    }
    function onState(state) {
      if (status) { status.textContent = state.message; status.dataset.state = state.phase; }
      if (retryButton) retryButton.hidden = !state.retryable;
      clearErrors(); syncVersion(state.version);
      if (state.phase === 'saved') {
        showAudience(state.audience);
        const item = read();
        const external = doc.getElementById('captureExternalLink');
        if (external) { external.hidden = !/^https?:\/\//i.test(item.url); if (!external.hidden) external.href = item.url; }
        const kind = input('kind').selectedOptions?.[0]?.textContent
          || {TASK: 'Task', IDEA: 'Idea', NOTE: 'Note', LINK: 'Link'}[item.kind] || 'Capture';
        doc.querySelectorAll('[data-capture-version-label]').forEach(label => { label.textContent = String(state.version); });
        doc.querySelectorAll('[data-capture-title]').forEach(label => { label.textContent = item.title.trim() || 'Untitled capture'; });
        doc.querySelectorAll('[data-capture-kind-label]').forEach(label => { label.textContent = kind; });
      }
      if (state.phase === 'error' && error) { error.hidden = false; error.textContent = state.message; }
      if (state.phase === 'validation' && error) {
        error.hidden = false;
        const messages = [];
        for (const [name, values] of Object.entries(state.errors || {})) {
          const field = input(name);
          field?.setAttribute('aria-invalid', 'true');
          for (const message of Array.isArray(values) ? values : [values]) {
            if (typeof message === 'string') messages.push(`${field?.labels?.[0]?.textContent || name}: ${message}`);
          }
        }
        error.textContent = messages.join(' ') || state.message;
      }
    }
    async function send(payload) {
      const controller = typeof win.AbortController === 'function' ? new win.AbortController() : null;
      const timeout = controller ? win.setTimeout(() => controller.abort(), 15000) : null;
      try {
        const response = await win.fetch(form.dataset.saveUrl, {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': input('csrfmiddlewaretoken').value},
          body: JSON.stringify(payload), ...(controller ? {signal: controller.signal} : {}),
        });
        let data = null;
        try { data = await response.json(); } catch (_error) { /* HTML access failures remain classified by their status. */ }
        return {status: response.status, redirected: response.redirected, data};
      } finally { if (timeout !== null) win.clearTimeout(timeout); }
    }
    let controller;
    try {
      // A bound server error form can contain a draft based on an older
      // version. Retain that base rather than silently granting the draft
      // authority to overwrite the latest JSON snapshot.
      const versionField = input('version');
      controller = createController({initialItem: initial.item || initial,
        version: versionField ? versionField.value : initial.version || form.dataset.version,
        read, write, send, onState, schedule: (callback, delay) => win.setTimeout(callback, delay), cancel: id => win.clearTimeout(id),
        onConflict: conflict => {
          if (panel) panel.hidden = !conflict;
          if (conflict) showAudience(conflict.audience);
          const keep = doc.querySelector('[data-capture-keep-mine]');
          if (keep) { keep.disabled = !!conflict?.audienceChanged; keep.textContent = conflict?.audienceChanged
            ? 'Sharing changed — review saved version first' : 'Save my draft over this version'; }
          if (serverContent) serverContent.textContent = conflict ? [conflict.audienceChanged
            ? 'AUDIENCE CHANGED. Saving this draft is paused. Copy your draft before loading the saved version, or make this capture private in another tab.' : '',
            conflict.audience?.visibility === 'TEAM' ? `Audience: shared with ${conflict.audience.shared_team_name || 'the selected team'}` : 'Audience: private',
            conflict.item.title, conflict.item.content,
            conflict.item.url, conflict.item.tags.join(', '), `Pinned: ${conflict.item.pinned ? 'Yes' : 'No'}`].filter(Boolean).join('\n\n') : '';
        },
      });
    } catch (_error) {
      if (status) status.textContent = 'Autosave could not start. Keep this page open and use the Save button.';
      return null;
    }
    form.dataset.captureInitialized = 'true';
    let navigating = false;
    const approved = new WeakSet();
    function focusError() {
      const target = panel && !panel.hidden ? panel : error;
      if (target) { target.setAttribute('tabindex', '-1'); target.focus(); }
    }
    async function navigate(callback) {
      if (navigating) return false;
      navigating = true;
      try {
        if (!await controller.flush()) { focusError(); return false; }
        callback(); return true;
      } finally { navigating = false; }
    }
    form.addEventListener('input', () => controller.change());
    form.addEventListener('change', () => controller.change());
    form.addEventListener('submit', event => { event.preventDefault(); void controller.retry().then(saved => { if (!saved) focusError(); }); });
    retryButton?.addEventListener('click', () => { void controller.retry().then(saved => { if (!saved) focusError(); }); });
    doc.querySelector('[data-capture-use-server]')?.addEventListener('click', () => { controller.useServer(); input('content').focus(); });
    doc.querySelector('[data-capture-keep-mine]')?.addEventListener('click', () => { void controller.keepMine().then(saved => { if (!saved) focusError(); }); });
    win.addEventListener('beforeunload', event => {
      if (controller.isDirty()) { event.preventDefault(); event.returnValue = ''; }
    });
    win.addEventListener('pageshow', () => controller.change());
    doc.addEventListener('click', event => {
      const link = event.target.closest?.('a[href]');
      if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
          || link.target && link.target !== '_self' || link.hasAttribute('download')) return;
      let target;
      try { target = new URL(link.href, win.location.href); } catch (_error) { return; }
      if (target.origin !== win.location.origin || !/^https?:$/.test(target.protocol) || target.hash || target.href === win.location.href) return;
      if (!controller.isDirty() && !controller.isBlocked()) return;
      event.preventDefault();
      void navigate(() => win.location.assign(target.href));
    }, true);
    doc.addEventListener('submit', event => {
      const action = event.target;
      if (!action.matches?.('[data-capture-action-form]') || event.defaultPrevented) return;
      if (approved.delete(action)) return;
      if (!controller.isDirty() && !controller.isBlocked()) return;
      event.preventDefault();
      const submitter = event.submitter;
      void navigate(() => {
        approved.add(action);
        try { action.requestSubmit(submitter || undefined); }
        finally { approved.delete(action); }
      });
    }, true);
    const active = {controller, flush: () => controller.flush(), navigate};
    win.TaskerCaptureEditor.active = active;
    if (fingerprint(read()) !== fingerprint(initial.item || initial)) controller.change();
    else onState(controller.getState());
    return active;
  }
  return {createController, normalizeItem, fingerprint, install, active: null};
});
