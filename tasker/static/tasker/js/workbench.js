(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) root.document.addEventListener('DOMContentLoaded', () => api.mount(root));
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  function createPreviewLoader({load, render, clear, fail}) {
    let revision = 0;
    return {
      async open(url) {
        const current = ++revision;
        clear(true);
        try {
          const result = await load(url);
          if (current !== revision) return;
          if (!result || result.status !== 200 || !result.data || typeof result.data.title !== 'string') throw new Error('Unavailable');
          render(result.data);
        } catch (_) { if (current === revision) { clear(false); fail(); } }
      },
      close() { ++revision; clear(false); }
    };
  }
  function selectionState(items) {
    const eligible = items.filter(item => !item.disabled);
    const selected = eligible.filter(item => item.checked).length;
    return {selected, all: eligible.length > 0 && selected === eligible.length, mixed: selected > 0 && selected < eligible.length};
  }
  function safePath(value) { return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !/[\\\u0000-\u0020\u007f]/.test(value); }
  function mount(win) {
    const doc = win.document, board = doc.querySelector('[data-workbench]');
    if (!board) return;
    const selected = Array.from(board.querySelectorAll('[data-work-select]'));
    const all = board.querySelector('[data-work-select-all]');
    function update() {
      const state = selectionState(selected);
      const count = board.querySelector('[data-work-selection-count]');
      if (count) count.textContent = state.selected + ' selected';
      if (all) { all.checked = state.all; all.indeterminate = state.mixed; }
    }
    selected.forEach(input => input.addEventListener('change', update));
    if (all) all.addEventListener('change', () => { selected.filter(item => !item.disabled).forEach(item => { item.checked = all.checked; }); update(); });
    const action = board.querySelector('[data-work-action]');
    function values() {
      board.querySelectorAll('[data-work-value]').forEach(group => {
        const enabled = group.getAttribute('data-work-value') === action.value;
        group.hidden = !enabled;
        group.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = !enabled; });
      });
    }
    if (action) { action.addEventListener('change', values); values(); }
    update();
    const drawer = doc.getElementById('workTaskDrawer'), body = doc.querySelector('[data-work-drawer-body]');
    if (!drawer || !body || !win.bootstrap || !win.bootstrap.Offcanvas) return;
    const panel = win.bootstrap.Offcanvas.getOrCreateInstance(drawer);
    const title = doc.getElementById('workDrawerTitle');
    const node = (tag, text, className) => { const item = doc.createElement(tag); if (text != null) item.textContent = text; if (className) item.className = className; return item; };
    function link(label, path, className) {
      if (!safePath(path)) return null;
      const item = node('a', label, className); item.href = path; return item;
    }
    function section(label, text) { body.appendChild(node('h3', label)); body.appendChild(node('p', text, 'workbench-preview-text')); }
    const preview = createPreviewLoader({
      load: async url => { const response = await win.fetch(url, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}}); return {status: response.status, data: response.ok ? await response.json() : null}; },
      clear: loading => { body.replaceChildren(); title.textContent = 'Task preview'; if (loading) body.appendChild(node('p', 'Loading task…', 'text-muted')); },
      fail: () => { body.appendChild(node('p', 'This preview is unavailable. Close it and reload Work to check your current access.', 'text-muted')); },
      render: data => {
        body.replaceChildren(); title.textContent = data.title;
        body.appendChild(node('p', [data.priority, data.status, data.owner, data.due ? 'Due ' + data.due : 'No due date'].join(' · '), 'small text-muted'));
        section('Definition of done', data.definition_of_done || 'No definition recorded. Add the outcome and acceptance criteria in Edit.');
        if (data.description) section('Context', data.description);
        if (data.blocked_reason && data.status === 'Blocked') section('Blocker', data.blocked_reason);
        if (data.goal && safePath(data.goal_url)) { body.appendChild(node('h3', 'Company goal')); body.appendChild(link(data.goal, data.goal_url)); }
        if (safePath(data.source_url)) body.appendChild(link('Open source capture', data.source_url, 'd-block mt-3'));
        if (Array.isArray(data.checklist) && data.checklist.length) {
          body.appendChild(node('h3', 'Checklist · ' + data.checklist_total + ' steps'));
          const steps = node('ul'); data.checklist.forEach(step => steps.appendChild(node('li', (step.is_completed ? 'Done: ' : 'Next: ') + step.title))); body.appendChild(steps);
        }
        body.appendChild(node('p', data.accepted ? 'Outcome accepted.' : 'Task is awaiting completion acceptance.', 'small text-muted mt-3'));
        const actions = node('div', null, 'workbench-preview-actions');
        const detail = link('Open task', data.url, 'btn btn-outline-secondary'); if (detail) actions.appendChild(detail);
        const edit = link('Edit', data.edit_url, 'btn btn-outline-secondary'); if (edit) actions.appendChild(edit);
        if (safePath(data.start_url)) {
          const form = node('form'); form.method = 'post'; form.action = data.start_url;
          const csrf = board.querySelector('[name=csrfmiddlewaretoken]');
          if (csrf) { const token = node('input'); token.type = 'hidden'; token.name = 'csrfmiddlewaretoken'; token.value = csrf.value; form.appendChild(token); const start = node('button', 'Start task', 'btn btn-primary'); start.type = 'submit'; form.appendChild(start); actions.appendChild(form); }
        }
        body.appendChild(actions);
      }
    });
    board.querySelectorAll('[data-work-preview]').forEach(anchor => anchor.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); panel.show(); preview.open(anchor.getAttribute('data-work-preview'));
    }));
    drawer.addEventListener('hidden.bs.offcanvas', () => preview.close());
    win.addEventListener('pagehide', () => preview.close());
  }
  return {createPreviewLoader, selectionState, safePath, mount};
});
