(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) {
    root.TaskerFocusWorkflow = api;
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => api.install(root), {once: true});
    else api.install(root);
  }
})(typeof window === 'undefined' ? null : window, function () {
  'use strict';
  const validKey = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value);
  const safeCaptureURL = value => typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !/[\\\r\n]/.test(value);

  function createParkingController(options) {
    let pending = null, running = null, stopped = null;
    let current = {phase: 'idle', message: 'Ideas are saved privately to your Inbox.', busy: false};
    const read = () => { const value = options.read(); return typeof value === 'string' ? value : ''; };
    const dirty = () => !!pending || !!read().trim();
    function state(phase, message, extra) {
      current = {phase, message, busy: phase === 'saving' || phase === 'notes', ...extra};
      options.onState?.({...current});
    }
    async function loop() {
      while (dirty()) {
        if (stopped === 'key-conflict') return false;
        state('notes', 'Saving shared task notes before parking this private idea…');
        let notesSaved = false;
        try { notesSaved = await options.flushNotes(); } catch (_error) { /* Retain both drafts. */ }
        if (!notesSaved) {
          state('blocked', 'Idea not saved. Resolve the shared-note save or conflict first; your idea is still here.', {retryable: true});
          return false;
        }
        pending ||= {client_key: options.getKey(), content: read()};
        if (!validKey(pending.client_key) || !pending.content.trim() || pending.content.length > 10000) {
          pending = null;
          state('validation', 'Write an idea of up to 10,000 characters before saving. Your draft is retained.', {retryable: true});
          return false;
        }
        state('saving', 'Saving your private idea…');
        let result;
        try { result = await options.send({...pending}); }
        catch (_error) {
          state('error', 'Save not confirmed. Keep this page open and retry; the same submission will be checked safely.', {retryable: true});
          return false;
        }
        const status = Number(result?.status || 0), data = result?.data;
        if (result?.redirected || status === 401 || status === 403) {
          state('error', 'Idea not saved. Restore your sign-in or access in another tab, then retry. Your draft remains here.', {retryable: true});
          return false;
        }
        if (status === 409 && data?.error === 'focus_unavailable') {
          state('error', 'This focus task is no longer active. Keep or copy your idea and check your Inbox in another tab. Retry here if the task is reopened.', {retryable: true});
          return false;
        }
        if (status === 409) {
          stopped = 'key-conflict';
          state('error', 'This submission identifier cannot be reused. Copy your idea before reloading; your draft has not been discarded.', {retryable: false});
          return false;
        }
        if (status === 400 || status === 422) {
          pending = null;
          state('validation', 'Idea not saved. Check the text and try again. Your draft is still here.', {retryable: true});
          return false;
        }
        if (status < 200 || status >= 300 || data?.saved !== true || !validKey(data.new_client_key)
            || data.new_client_key === pending.client_key || !safeCaptureURL(data.capture_url)) {
          state('error', 'Save not confirmed. Keep this page open and retry; your draft is retained.', {retryable: true});
          return false;
        }
        const sent = pending;
        options.setKey(data.new_client_key);
        pending = null;
        // Do not erase an edit made after the captured snapshot was sent.
        if (read() === sent.content) options.write('');
        options.onSaved?.(data.capture_url);
      }
      state('saved', 'Idea saved privately to your Inbox. Your focus timer is unchanged.');
      return true;
    }
    function flush() {
      if (running) return running;
      if (!dirty()) return Promise.resolve(true);
      running = loop().finally(() => { running = null; });
      return running;
    }
    function submit() {
      if (!dirty()) {
        state('validation', 'Write an idea before saving.', {retryable: false});
        return Promise.resolve(false);
      }
      return flush();
    }
    function change() {
      if (running || stopped) return;
      state(dirty() ? 'draft' : 'idle', pending
        ? 'An earlier save is unconfirmed. Retry before leaving; both submissions remain safely identified.'
        : dirty() ? 'Unsaved private idea. Use Park idea to save it to your Inbox.' : 'Ideas are saved privately to your Inbox.',
      {retryable: !!pending});
    }
    return {flush, submit, change, isDirty: dirty, getState: () => ({...current})};
  }

  function install(win) {
    const doc = win.document, form = doc.getElementById('focusParkingForm');
    if (!form || form.dataset.parkingInitialized === 'true') return null;
    const text = doc.getElementById('focusParkingText'), save = doc.getElementById('focusParkingSave');
    const retry = doc.getElementById('focusParkingRetry'), status = doc.getElementById('focusParkingStatus');
    const error = doc.getElementById('focusParkingError');
    const key = form.elements.namedItem('client_key'), csrf = form.elements.namedItem('csrfmiddlewaretoken');
    if (!text || !key || !csrf) return null;
    async function send(payload) {
      const abort = typeof win.AbortController === 'function' ? new win.AbortController() : null;
      const timeout = abort ? win.setTimeout(() => abort.abort(), 15000) : null;
      try {
        const response = await win.fetch(form.dataset.parkingUrl || form.getAttribute('action'), {
          method: 'POST', credentials: 'same-origin',
          headers: {'X-CSRFToken': csrf.value, 'Accept': 'application/json'},
          body: new URLSearchParams(payload), ...(abort ? {signal: abort.signal} : {}),
        });
        let data = null;
        try { data = await response.json(); } catch (_error) { /* HTML failures are not saves. */ }
        return {status: response.status, redirected: response.redirected, data};
      } finally { if (timeout !== null) win.clearTimeout(timeout); }
    }
    const controller = createParkingController({read: () => text.value, write: value => { text.value = value; },
      getKey: () => key.value, setKey: value => { key.value = value; }, send,
      flushNotes: () => win.TaskerFocusNotes?.flush?.() || Promise.resolve(false),
      onState: state => {
        text.disabled = state.busy;
        if (save) save.disabled = state.busy;
        if (retry) { retry.disabled = state.busy; retry.hidden = !state.retryable; }
        if (status) status.textContent = state.message;
        if (error) { error.hidden = !['error', 'validation', 'blocked'].includes(state.phase); error.textContent = error.hidden ? '' : state.message; }
      },
    });
    form.dataset.parkingInitialized = 'true';
    text.addEventListener('input', () => controller.change());
    form.addEventListener('submit', event => { event.preventDefault(); void controller.submit(); });
    retry?.addEventListener('click', () => { void controller.flush(); });
    win.addEventListener('beforeunload', event => {
      if (!controller.isDirty()) return;
      event.preventDefault(); event.returnValue = '';
    });
    win.TaskerFocusParking = controller;
    return controller;
  }

  return {createParkingController, install};
});
