const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../../static/tasker/js/capture_editor.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const copy = value => JSON.parse(JSON.stringify(value));
const initial = () => ({kind: 'NOTE', title: 'Saved title', content: 'Saved content', url: '', tags: [], pinned: false});
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const success = (item, version) => ({status: 200, data: {ok: true, item: api.normalizeItem(item), version}});

function fixture(send, stored = initial()) {
  let draft = copy(stored), nextTimer = 1;
  const calls = [], states = [], conflicts = [], timers = new Map();
  const controller = api.createController({initialItem: stored, version: 1,
    read: () => draft, write: value => { draft = copy(value); },
    send: payload => { calls.push(copy(payload)); return send(payload, calls.length); },
    onState: state => states.push(copy(state)), onConflict: conflict => conflicts.push(copy(conflict)),
    schedule: (callback, delay) => { const id = nextTimer++; timers.set(id, {callback, delay}); return id; },
    cancel: id => timers.delete(id),
  });
  return {controller, calls, states, conflicts, timers, read: () => copy(draft),
    edit: values => { draft = {...draft, ...values}; controller.change(); },
    runTimer: async () => { const [id, timer] = [...timers][0]; timers.delete(id); timer.callback(); await tick(); },
  };
}

test('debounces changes and announces Saved only after persisted acknowledgement', async () => {
  const response = deferred(), f = fixture(() => response.promise);
  f.edit({content: 'Private idea'});
  assert.equal(f.calls.length, 0);
  assert.equal([...f.timers.values()][0].delay, 600);
  assert.equal(f.controller.getState().phase, 'pending');
  const save = f.controller.flush();
  assert.equal(f.controller.getState().phase, 'saving');
  assert.equal(f.controller.isDirty(), true);
  response.resolve(success(f.calls[0], 2));
  assert.equal(await save, true);
  assert.equal(f.controller.getState().phase, 'saved');
  assert.equal(f.controller.isDirty(), false);
});

test('a private-to-team conflict cannot publish a retained draft through Keep mine', async () => {
  const shared = {visibility: 'TEAM', shared_team_id: 7, shared_team_name: 'Sample team'};
  const f = fixture((payload, count) => Promise.resolve(count === 1
    ? {status: 409, data: {conflict: true, version: 4, item: {...initial(), ...shared}}}
    : {status: 200, data: {ok: true, version: 5, item: {...api.normalizeItem(payload), ...shared}}}),
    {...initial(), visibility: 'PRIVATE', shared_team_id: null});
  f.edit({content: 'Draft intended to stay private'});
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.conflicts[0].audienceChanged, true);
  assert.equal(await f.controller.keepMine(), false);
  assert.equal(f.calls.length, 1);
  assert.equal(f.read().content, 'Draft intended to stay private');
  assert.equal(f.controller.useServer(), true);
  assert.equal(f.controller.getState().audience.visibility, 'TEAM');
  f.edit({content: 'A new edit deliberately made after reviewing the shared version'});
  assert.equal(await f.controller.flush(), true);
  assert.equal(f.calls.length, 2);
});

test('a changed team audience also blocks overwrite and retains the draft', async () => {
  const f = fixture(() => Promise.resolve({status: 409, data: {conflict: true, version: 3,
    item: {...initial(), visibility: 'TEAM', shared_team_id: 2, shared_team_name: 'Other team'}}}),
    {...initial(), visibility: 'TEAM', shared_team_id: 1, shared_team_name: 'First team'});
  f.edit({content: 'Only intended for first team'});
  await f.controller.flush();
  assert.equal(f.conflicts[0].audienceChanged, true);
  assert.equal(await f.controller.keepMine(), false);
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.calls.length, 1);
});

test('an acknowledgement after sharing never dispatches a newer queued private draft', async () => {
  const response = deferred();
  const f = fixture(() => response.promise, {...initial(), visibility: 'PRIVATE', shared_team_id: null});
  f.edit({content: 'An uncertain earlier edit'});
  const saving = f.controller.flush();
  f.edit({content: 'A newer draft that has not been shared'});
  response.resolve({status: 200, data: {ok: true, version: 3,
    item: {...api.normalizeItem(f.calls[0]), visibility: 'TEAM', shared_team_id: 7, shared_team_name: 'Sample team'}}});
  assert.equal(await saving, false);
  assert.equal(f.calls.length, 1);
  assert.equal(f.read().content, 'A newer draft that has not been shared');
  assert.equal(f.conflicts[0].audienceChanged, true);
  assert.equal(await f.controller.keepMine(), false);
});

test('serializes edits made in flight and never replaces a newer draft', async () => {
  const first = deferred(), second = deferred();
  const f = fixture((_payload, count) => count === 1 ? first.promise : second.promise);
  f.edit({content: 'First edit'});
  const save = f.controller.flush();
  f.edit({content: 'Newer edit', title: 'Changed title'});
  first.resolve(success(f.calls[0], 2)); await tick();
  assert.equal(f.read().content, 'Newer edit');
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].version, 2);
  assert.equal(f.calls[1].content, 'Newer edit');
  assert.equal(f.states.some(state => state.phase === 'saved'), false);
  second.resolve(success(f.calls[1], 3));
  assert.equal(await save, true);
  assert.equal(f.controller.getVersion(), 3);
});

test('uncertain failure retries the same snapshot before newer edits', async () => {
  const f = fixture((payload, count) => count === 1 ? Promise.reject(new Error('Network failed')) : Promise.resolve(success(payload, count)));
  f.edit({content: 'Possibly committed'});
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.controller.getState().phase, 'error');
  assert.equal([...f.timers.values()][0].delay, 2000);
  f.edit({content: 'Newer draft'});
  await f.runTimer();
  assert.deepEqual(f.calls[1], f.calls[0]);
  assert.equal(f.calls[2].content, 'Newer draft');
  assert.equal(f.calls[2].version, 2);
  assert.equal(f.controller.isDirty(), false);
});

test('conflict retains the draft, blocks navigation flush and supports loading saved fields', async () => {
  const saved = {...initial(), content: 'Other tab', title: 'Other title', tags: ['shared'], pinned: true};
  const f = fixture(() => Promise.resolve({status: 409, data: {conflict: true, version: 8, item: saved}}));
  f.edit({content: 'My private draft'});
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.read().content, 'My private draft');
  assert.equal(f.controller.isBlocked(), true);
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.calls.length, 1);
  assert.equal(f.timers.size, 0);
  f.controller.useServer();
  assert.deepEqual(f.read(), saved);
  assert.equal(f.controller.getVersion(), 8);
  assert.equal(f.controller.isDirty(), false);
  assert.equal(f.controller.getState().phase, 'saved');
});

test('keeping a conflicted draft is explicit and writes against the reviewed latest version', async () => {
  const f = fixture((payload, count) => Promise.resolve(count === 1
    ? {status: 409, data: {conflict: true, version: 9, item: {...initial(), content: 'Other tab'}}}
    : success(payload, 10)));
  f.edit({content: 'My draft'}); await f.controller.flush();
  assert.equal(f.calls.length, 1);
  f.edit({content: 'My reviewed draft'});
  assert.equal(f.calls.length, 1);
  assert.equal(await f.controller.keepMine(), true);
  assert.equal(f.calls[1].version, 9);
  assert.equal(f.calls[1].content, 'My reviewed draft');
  assert.equal(f.controller.isBlocked(), false);
});

test('an already-identical conflict resolution updates version without another write', async () => {
  const f = fixture(payload => Promise.resolve({status: 409, data: {conflict: true, version: 7, item: api.normalizeItem(payload)}}));
  f.edit({content: 'Same persisted draft'}); await f.controller.flush();
  assert.equal(await f.controller.keepMine(), true);
  assert.equal(f.calls.length, 1);
  assert.equal(f.controller.getState().phase, 'saved');
  assert.equal(f.controller.getState().version, 7);
});

test('validation failure retains fields, stops retry, and permits a corrected edit', async () => {
  const f = fixture((payload, count) => Promise.resolve(count === 1
    ? {status: 400, data: {errors: {content: ['Enter some text.']}}} : success(payload, 2)));
  f.edit({content: ''}); assert.equal(await f.controller.flush(), false);
  assert.equal(f.controller.getState().phase, 'validation');
  assert.deepEqual(f.controller.getState().errors, {content: ['Enter some text.']});
  assert.equal(f.read().content, '');
  assert.equal(f.timers.size, 0);
  f.edit({content: 'Corrected text'});
  assert.equal(await f.controller.flush(), true);
  assert.equal(f.calls[1].content, 'Corrected text');
});

test('permission failures never loop automatically or discard the draft', async () => {
  for (const status of [401, 403, 404]) {
    const f = fixture((payload, count) => Promise.resolve(count === 1 ? {status, data: null} : success(payload, 2)));
    f.edit({content: 'Do not discard'}); assert.equal(await f.controller.flush(), false);
    assert.equal(f.timers.size, 0);
    assert.equal(f.controller.isDirty(), true);
    assert.equal(f.read().content, 'Do not discard');
    assert.equal(await f.controller.flush(), false);
    assert.equal(f.calls.length, 1);
    assert.equal(await f.controller.retry(), true);
  }
});

test('redirected login responses stop retries and do not claim a save', async () => {
  const f = fixture(() => Promise.resolve({status: 200, redirected: true, data: null}));
  f.edit({content: 'Draft while signed out'}); await f.controller.flush();
  assert.equal(f.controller.getState().phase, 'error');
  assert.equal(f.controller.isDirty(), true);
  assert.equal(f.timers.size, 0);
});

test('malformed or boolean-version acknowledgements remain uncertain and unsaved', async () => {
  for (const data of [null, {ok: true, version: true, item: initial()}, {ok: true, version: 2, item: {content: 'Incomplete'}}]) {
    const f = fixture(() => Promise.resolve({status: 200, data}));
    f.edit({content: 'Keep this draft'}); assert.equal(await f.controller.flush(), false);
    assert.equal(f.controller.isDirty(), true);
    assert.equal(f.controller.getState().phase, 'error');
    assert.equal([...f.timers.values()][0].delay, 2000);
  }
});

test('concurrent flush callers share one request and wait for persistence', async () => {
  const response = deferred(), f = fixture(() => response.promise);
  f.edit({content: 'Before navigating'});
  const first = f.controller.flush(), second = f.controller.flush();
  assert.equal(first, second); assert.equal(f.calls.length, 1);
  response.resolve(success(f.calls[0], 2));
  assert.equal(await second, true);
});

test('server normalization is adopted only after the matching saved response', async () => {
  const f = fixture(payload => Promise.resolve(success({...payload, title: payload.title.trim(), tags: ['sales']}, 2)));
  f.edit({title: '  New title ', tags: ' sales , sales '});
  assert.equal(await f.controller.flush(), true);
  assert.equal(f.read().title, 'New title');
  assert.deepEqual(f.read().tags, ['sales']);
  assert.equal(f.calls.length, 1);
});

test('destroy prevents late acknowledgements from writing fields or scheduling retries', async () => {
  const response = deferred(), f = fixture(() => response.promise);
  f.edit({content: 'Before closing'}); const save = f.controller.flush();
  f.controller.destroy(); response.resolve(success({...f.calls[0], content: 'Late server text'}, 2));
  assert.equal(await save, false);
  assert.equal(f.read().content, 'Before closing');
  assert.equal(f.timers.size, 0);
});

function browserFixture({draftContent, draftVersion = '1', savedVersion = 1} = {}) {
  const handlers = {}, windowHandlers = {}, formHandlers = {}, calls = [], response = deferred();
  const node = () => ({hidden: true, textContent: '', dataset: {}, setAttribute() {}, removeAttribute() {}, focus() { this.focused = true; }});
  const fields = {};
  for (const [name, value] of Object.entries({...initial(), csrfmiddlewaretoken: 'synthetic-csrf'})) {
    fields[name] = {...node(), value: name === 'tags' ? '' : value, checked: value === true};
  }
  fields.version = {...node(), value: draftVersion};
  if (draftContent !== undefined) fields.content.value = draftContent;
  const status = node(), error = node(), conflict = node(), server = node(), retry = node(), useServer = node(), keepMine = node();
  const version = {value: '1'};
  const form = {dataset: {version: '1', saveUrl: '/inbox/1/save/'}, elements: {namedItem: name => fields[name]},
    querySelectorAll: () => [], addEventListener: (name, callback) => { formHandlers[name] = callback; }};
  const doc = {
    querySelector: selector => ({'[data-capture-editor]': form, '[data-capture-retry]': retry,
      '[data-capture-use-server]': useServer, '[data-capture-keep-mine]': keepMine}[selector] || null),
    querySelectorAll: () => [version],
    getElementById: id => ({captureInitialData: {textContent: JSON.stringify({item: initial(), version: savedVersion})}, captureSaveStatus: status,
      captureSaveError: error, captureConflict: conflict, captureServerContent: server}[id]),
    addEventListener: (name, callback) => { handlers[name] = callback; },
  };
  for (const button of [retry, useServer, keepMine]) button.addEventListener = (_name, callback) => { button.click = callback; };
  const win = {document: doc, TaskerCaptureEditor: {}, location: {href: 'http://localhost/inbox/1/', origin: 'http://localhost', assign: url => calls.push(url)},
    setTimeout: () => 1, clearTimeout() {}, addEventListener: (name, callback) => { windowHandlers[name] = callback; },
    fetch: () => response.promise,
  };
  const active = api.install(win);
  return {win, active, fields, handlers, windowHandlers, formHandlers, calls, response, status, error, conflict, server, version};
}

test('browser intercepts navigation before loading and preserves the draft on conflict without HTML injection', async () => {
  const b = browserFixture(); b.fields.content.value = 'Private draft'; b.formHandlers.input();
  const event = {button: 0, target: {closest: () => ({href: 'http://localhost/tasks/', hasAttribute: () => false})},
    preventDefault() { this.defaultPrevented = true; }};
  b.handlers.click(event);
  assert.equal(event.defaultPrevented, true); assert.deepEqual(b.calls, []);
  const malicious = '<img src=x onerror=alert(1)>';
  b.response.resolve({status: 409, json: async () => ({conflict: true, version: 4, item: {...initial(), content: malicious}})});
  await tick();
  assert.deepEqual(b.calls, []);
  assert.equal(b.fields.content.value, 'Private draft');
  assert.equal(b.conflict.hidden, false);
  assert.ok(b.server.textContent.includes(malicious));
  assert.equal(Object.hasOwn(b.server, 'innerHTML'), false);
  const unload = {preventDefault() { this.prevented = true; }}; b.windowHandlers.beforeunload(unload);
  assert.equal(unload.prevented, true);
});

test('browser flushes action forms, updates version and retains the named submitter', async () => {
  const b = browserFixture(); b.fields.content.value = 'Save before promotion'; b.formHandlers.input();
  const submitter = {name: 'action', value: 'promote'};
  let submitted = null;
  const action = {matches: () => true, requestSubmit: button => {
    const again = {target: action, submitter: button, preventDefault() { this.defaultPrevented = true; }};
    b.handlers.submit(again); assert.notEqual(again.defaultPrevented, true); submitted = button;
  }};
  const event = {target: action, submitter, preventDefault() { this.defaultPrevented = true; }};
  b.handlers.submit(event);
  assert.equal(event.defaultPrevented, true); assert.equal(submitted, null);
  b.response.resolve({status: 200, json: async () => ({ok: true, version: 2, item: {...initial(), content: 'Save before promotion'}})});
  await tick();
  assert.equal(submitted, submitter); assert.equal(b.version.value, '2');
  assert.equal(b.fields.version.value, '2');
});

test('a bound or restored draft is initially unsaved and retains its older base version', async () => {
  const b = browserFixture({draftContent: 'Retained failed form draft', draftVersion: '2', savedVersion: 5});
  assert.equal(b.active.controller.isDirty(), true);
  assert.equal(b.status.dataset.state, 'pending');
  assert.equal(b.active.controller.getVersion(), 2);
  const save = b.active.flush();
  b.response.resolve({status: 409, json: async () => ({conflict: true, version: 5, item: initial()})});
  assert.equal(await save, false);
  assert.equal(b.fields.content.value, 'Retained failed form draft');
  assert.equal(b.conflict.hidden, false);
});

test('pageshow detects browser-restored field changes without an input event', () => {
  const b = browserFixture();
  b.fields.content.value = 'Restored browser draft';
  assert.equal(b.active.controller.isDirty(), false);
  b.windowHandlers.pageshow();
  assert.equal(b.active.controller.isDirty(), true);
  assert.equal(b.status.dataset.state, 'pending');
});
