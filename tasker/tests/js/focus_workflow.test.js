const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../../static/tasker/js/focus_workflow.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const A = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';
const B = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb';
const C = 'cccccccc-cccc-4ccc-cccc-cccccccccccc';
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const success = key => ({status: 200, data: {saved: true, capture_url: '/inbox/1/', new_client_key: key}});
function fixture(send, notes = () => Promise.resolve(true)) {
  let text = 'Private idea', key = A;
  const calls = [], states = [], links = [];
  const controller = api.createParkingController({read: () => text, write: value => { text = value; },
    getKey: () => key, setKey: value => { key = value; },
    send: payload => { calls.push({...payload}); return send(payload, calls.length); },
    flushNotes: notes, onState: value => states.push(value), onSaved: link => links.push(link)});
  return {controller, calls, states, links, text: () => text, key: () => key, edit: value => { text = value; }};
}

test('parking flushes shared notes first and never clears a draft before acknowledgement', async () => {
  const notes = deferred(), response = deferred();
  const f = fixture(() => response.promise, () => notes.promise);
  const saved = f.controller.flush();
  assert.equal(f.calls.length, 0);
  assert.equal(f.text(), 'Private idea');
  notes.resolve(true); await tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.controller.getState().phase, 'saving');
  response.resolve(success(B));
  assert.equal(await saved, true);
  assert.equal(f.text(), '');
  assert.equal(f.key(), B);
  assert.equal(f.controller.isDirty(), false);
});

test('a notes conflict retains the private idea and makes no capture request', async () => {
  const f = fixture(() => success(B), () => Promise.resolve(false));
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.calls.length, 0);
  assert.equal(f.text(), 'Private idea');
  assert.equal(f.controller.getState().phase, 'blocked');
});

test('unknown writes retry the exact UUID and text before newer input uses a fresh UUID', async () => {
  const f = fixture((_payload, count) => count === 1 ? Promise.reject(new Error('Uncertain write')) : Promise.resolve(success(count === 2 ? B : C)));
  assert.equal(await f.controller.flush(), false);
  f.edit('A newer private idea');
  assert.equal(await f.controller.flush(), true);
  assert.deepEqual(f.calls[1], f.calls[0]);
  assert.equal(f.calls[2].client_key, B);
  assert.equal(f.calls[2].content, 'A newer private idea');
  assert.equal(f.key(), C);
  assert.equal(f.text(), '');
});

test('edits made in flight are not erased by the earlier save', async () => {
  const first = deferred(), second = deferred();
  const f = fixture((_payload, count) => count === 1 ? first.promise : second.promise);
  const saved = f.controller.flush(); await tick();
  f.edit('Newer idea'); first.resolve(success(B)); await tick();
  assert.equal(f.text(), 'Newer idea');
  assert.equal(f.calls[1].client_key, B);
  second.resolve(success(C)); assert.equal(await saved, true);
});

test('concurrent flush callers share one parking request', async () => {
  const response = deferred(), f = fixture(() => response.promise);
  const first = f.controller.flush(), second = f.controller.flush(); await tick();
  assert.equal(f.calls.length, 1);
  response.resolve(success(B));
  assert.equal(await first, true); assert.equal(await second, true);
});

test('validation can be corrected without creating a different submission', async () => {
  const f = fixture((_payload, count) => Promise.resolve(count === 1 ? {status: 400, data: {saved: false}} : success(B)));
  assert.equal(await f.controller.flush(), false);
  f.edit('Corrected idea'); assert.equal(await f.controller.flush(), true);
  assert.equal(f.calls[0].client_key, f.calls[1].client_key);
  assert.equal(f.calls[1].content, 'Corrected idea');
});

test('access and redirected responses preserve both draft and uncertain identity', async () => {
  for (const result of [{status: 403}, {status: 200, redirected: true}]) {
    const f = fixture(() => Promise.resolve(result));
    assert.equal(await f.controller.flush(), false);
    assert.equal(f.text(), 'Private idea');
    assert.equal(f.key(), A);
    assert.equal(f.controller.isDirty(), true);
    assert.equal(f.states.some(state => state.phase === 'saved'), false);
  }
});

test('a key collision stops retry instead of rotating into a duplicate capture', async () => {
  const f = fixture(() => Promise.resolve({status: 409}));
  assert.equal(await f.controller.flush(), false);
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.calls.length, 1);
  assert.equal(f.text(), 'Private idea');
  assert.equal(f.key(), A);
});

test('a task closed elsewhere retains the idea and allows the same safe retry after reopening', async () => {
  const f = fixture((_payload, count) => Promise.resolve(count === 1
    ? {status: 409, data: {saved: false, error: 'focus_unavailable'}} : success(B)));
  assert.equal(await f.controller.flush(), false);
  assert.equal(f.text(), 'Private idea');
  assert.equal(f.key(), A);
  assert.equal(f.controller.getState().retryable, true);
  assert.match(f.controller.getState().message, /Inbox in another tab/);
  assert.equal(await f.controller.flush(), true);
  assert.deepEqual(f.calls[0], f.calls[1]);
  assert.equal(f.key(), B);
  assert.equal(f.text(), '');
});

test('malformed acknowledgements and unsafe capture links cannot claim persistence', async () => {
  for (const data of [
    {saved: true, capture_url: '/inbox/1/', new_client_key: A},
    {saved: true, capture_url: '//other.example/', new_client_key: B},
    {saved: true, capture_url: '/\\other.example/', new_client_key: B},
    {saved: true, capture_url: '/inbox/1/', new_client_key: 'invalid'},
  ]) {
    const f = fixture(() => Promise.resolve({status: 200, data}));
    assert.equal(await f.controller.flush(), false);
    assert.equal(f.text(), 'Private idea');
    assert.equal(f.key(), A);
  }
});

test('empty navigation flush is harmless while explicit empty save explains the input', async () => {
  const f = fixture(() => Promise.resolve(success(B))); f.edit(' ');
  assert.equal(await f.controller.flush(), true);
  assert.equal(await f.controller.submit(), false);
  assert.equal(f.calls.length, 0);
  assert.equal(f.controller.isDirty(), false);
});

test('typing identifies an unsaved idea without making a capture request', () => {
  const f = fixture(() => Promise.resolve(success(B)));
  f.controller.change();
  assert.equal(f.controller.getState().phase, 'draft');
  assert.match(f.controller.getState().message, /Unsaved private idea/);
  assert.equal(f.calls.length, 0);
});

test('browser integration prevents native submission and guards unsaved parking drafts', async () => {
  const events = {}, winEvents = {}, requests = [];
  function element(value = '') { return {value, dataset: {}, hidden: false, disabled: false, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; }, getAttribute() { return '/focus/park/'; }}; }
  const ids = {focusParkingForm: element(), focusParkingText: element('Private browser idea'),
    focusParkingSave: element(), focusParkingRetry: element(), focusParkingStatus: element(), focusParkingError: element()};
  const key = element(A), csrf = element('csrf');
  ids.focusParkingForm.elements = {namedItem: name => name === 'client_key' ? key : csrf};
  const win = {document: {getElementById: id => ids[id]}, TaskerFocusNotes: {flush: () => Promise.resolve(true)},
    addEventListener: (name, callback) => { winEvents[name] = callback; },
    fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve})),
    // Accessing either browser storage is a test failure: parking stays in memory.
    get sessionStorage() { throw new Error('Unexpected storage access'); },
    get localStorage() { throw new Error('Unexpected storage access'); }};
  const controller = api.install(win);
  let prevented = false;
  ids.focusParkingForm.listeners.submit({preventDefault() { prevented = true; }}); await tick();
  assert.equal(prevented, true);
  assert.equal(ids.focusParkingText.disabled, true);
  assert.equal(requests[0].options.body.get('content'), 'Private browser idea');
  let unloadPrevented = false;
  winEvents.beforeunload({preventDefault() { unloadPrevented = true; }});
  assert.equal(unloadPrevented, true);
  requests[0].resolve({status: 200, redirected: false, json: async () => success(B).data}); await tick();
  assert.equal(controller.isDirty(), false);
  assert.equal(ids.focusParkingText.value, '');
  assert.equal(ids.focusParkingText.disabled, false);
  assert.equal(key.value, B);
  assert.equal(win.TaskerFocusParking, controller);
});
