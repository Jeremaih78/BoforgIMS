// Deterministic browser-event tests; no network, database or browser dependency.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/tasker/js/focus.js'), 'utf8');
const parkingSource = fs.readFileSync(path.join(__dirname, '../../static/tasker/js/focus_workflow.js'), 'utf8');
const A = 'a'.repeat(64), B = 'b'.repeat(64), C = 'c'.repeat(64);
const tick = () => new Promise(resolve => setImmediate(resolve));

function browser(storage = new Map(), {user = '7', task = '11', initialNotes = 'Original', initialVersion = A, storageDenied = false,
  parking = false, interval = null, now = Date.parse('2026-10-27T10:00:00Z')} = {}) {
  const scheduled = new Map(), intervals = [], requests = [], navigations = [], docEvents = {}, winEvents = {}, confirmations = [];
  let clock = 0;
  let currentTime = now;
  class ClockDate extends Date { static now() { return currentTime; } }
  class Element {
    constructor(id, tagName = 'DIV') {
      Object.assign(this, {id, tagName, value: '', dataset: {}, listeners: {}, hidden: false,
        disabled: false, textContent: '', attributes: {}, controls: [], classList: {add() {}, remove() {}, toggle() {}}});
    }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    setAttribute(name, value) { this.attributes[name] = value; }
    hasAttribute(name) { return Object.hasOwn(this.attributes, name); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    focus() { this.focused = true; }
    querySelector(selector) {
      if (selector.includes('csrf')) return {value: 'csrf'};
      return this.controls.find(control => selector.includes(control.name)) || null;
    }
    closest(selector) { return selector === 'a[href]' && this.tagName === 'A' ? this : this.form || null; }
    matches() { return ['INPUT', 'TEXTAREA', 'SELECT'].includes(this.tagName); }
    emit(name, event = {}) {
      event.target ||= this; event.defaultPrevented ||= false;
      event.preventDefault ||= function () { this.defaultPrevented = true; };
      for (const fn of this.listeners[name] || []) fn(event);
      for (const fn of docEvents[name] || []) fn(event);
      return event;
    }
    requestSubmit(submitter) {
      // Like browser requestSubmit(), replay must re-run required constraints.
      if (this.controls.some(control => control.required && control.type === 'checkbox' && !control.checked)) {
        this.invalidSubmissions = (this.invalidSubmissions || 0) + 1;
        return;
      }
      const event = this.emit('submit', {submitter});
      if (!event.defaultPrevented) this.submissions = (this.submissions || 0) + 1;
    }
  }
  const ids = {};
  for (const id of ['focusTimer', 'focusNotes', 'focusNotesForm', 'focusSaveState',
    'focusRetrySave', 'focusNotesConflict', 'focusConflictNotes', 'focusUseSavedNotes',
    'focusOverwriteNotes', 'focusNotesStatus', 'blockedModal']) ids[id] = new Element(id);
  ids.focusNotes.tagName = 'TEXTAREA'; ids.focusNotes.value = initialNotes;
  ids.focusNotesForm.tagName = 'FORM'; ids.focusNotesForm.action = '/tasks/task/focus/notes/';
  ids.focusNotesForm.dataset = {notesVersion: initialVersion, draftUser: user, draftTask: task};
  ids.focusTimer.dataset = {running: '0', baseSeconds: '0'};
  if (interval) {
    ids.focusIntervalTimer = new Element('focusIntervalTimer');
    ids.focusIntervalTimer.dataset = {...interval};
    // The server renders both timers from the same running time entry.
    ids.focusTimer.dataset.running = interval.running;
    ids.focusTimer.dataset.started = interval.started || '';
  }
  ids.focusNotesConflict.hidden = true;
  const checklist = new Element('checklist', 'FORM'), stop = new Element('stop', 'FORM');
  checklist.action = '/tasks/task/focus/checklist/1/toggle/'; checklist.className = 'focus-check';
  stop.action = '/tasks/task/focus/stop/';
  const forms = [ids.focusNotesForm, checklist, stop];
  if (parking) {
    for (const id of ['focusParkingForm', 'focusParkingText', 'focusParkingSave', 'focusParkingRetry', 'focusParkingStatus', 'focusParkingError']) ids[id] = new Element(id);
    ids.focusParkingForm.tagName = 'FORM';
    ids.focusParkingText.tagName = 'TEXTAREA';
    ids.focusParkingForm.dataset.parkingUrl = '/tasks/task/focus/park/';
    const fields = {client_key: {value: '11111111-1111-4111-8111-111111111111'}, csrfmiddlewaretoken: {value: 'csrf'}};
    ids.focusParkingForm.elements = {namedItem: name => fields[name]};
    forms.push(ids.focusParkingForm);
  }
  const document = {
    readyState: 'complete',
    visibilityState: 'visible', getElementById: id => ids[id],
    querySelector: selector => selector === '.focus-exit-form' ? stop : null,
    querySelectorAll: selector => selector === 'form' ? forms : forms.filter(f => f !== ids.focusNotesForm && f !== checklist),
    addEventListener: (name, fn) => (docEvents[name] ||= []).push(fn),
  };
  const sessionStorage = {
    getItem: key => storage.get(key) ?? null, setItem: (key, value) => {
      if (storageDenied) throw new Error('Storage disabled');
      storage.set(key, value);
    },
    removeItem: key => storage.delete(key), key: index => [...storage.keys()][index],
    get length() { return storage.size; },
  };
  const window = {
    sessionStorage, location: {href: 'https://testserver/tasks/task/focus/', assign: href => navigations.push(href), reload() {}},
    setTimeout: (fn, ms) => { const id = ++clock; scheduled.set(id, {fn, ms}); return id; },
    clearTimeout: id => scheduled.delete(id), setInterval: fn => intervals.push(fn),
    confirm: message => { confirmations.push(message); return true; },
    addEventListener: (name, fn) => (winEvents[name] ||= []).push(fn),
  };
  const fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({url, options, resolve, reject});
    options.signal?.addEventListener('abort', () => reject(new Error('Request timed out')));
  });
  Object.assign(window, {document, fetch, AbortController});
  const context = {document, window, sessionStorage, fetch, URL, URLSearchParams, Date: ClockDate, Promise, AbortController,
    HTMLFormElement: {prototype: {submit() { this.submissions = (this.submissions || 0) + 1; }}},
    bootstrap: {Modal: {getOrCreateInstance: () => ({show() {}})}}};
  vm.runInNewContext(source, context);
  if (parking) vm.runInNewContext(parkingSource, context);
  function input(value) { ids.focusNotes.value = value; ids.focusNotes.emit('input'); }
  function runTimer(ms) {
    const entry = [...scheduled].find(([, timer]) => timer.ms === ms);
    assert(entry, `No ${ms}ms timer scheduled`); scheduled.delete(entry[0]); entry[1].fn();
  }
  function reply(index, data, status = 200) {
    requests[index].resolve({ok: status >= 200 && status < 300, status, redirected: false, json: async () => data});
  }
  return {ids, requests, checklist, stop, input, runTimer, reply, storage, navigations, winEvents, window, confirmations,
    advanceTime(ms) { currentTime += ms; for (const fn of intervals) fn(); },
    form(id, {review = false} = {}) {
      const form = new Element(id, 'FORM'); forms.push(form);
      if (review) {
        const checkbox = new Element(`${id}Review`, 'INPUT');
        Object.assign(checkbox, {name: 'accept_definition', type: 'checkbox', required: true, checked: false});
        form.controls.push(checkbox);
      }
      return form;
    },
    clickLink() { const link = new Element('link', 'A'); link.href = 'https://testserver/tasks/';
      link.target = ''; link.hasAttribute = () => false; return link.emit('click', {button: 0}); }};
}

test('serializes edits during a save and flushes checklist navigation after the newest edit', async () => {
  const page = browser(); page.input('First'); page.runTimer(700); await tick();
  page.input('Second'); page.runTimer(700); await tick();
  assert.equal(page.requests.length, 1, 'Only one notes request may be in flight');
  page.checklist.requestSubmit(); assert.equal(page.checklist.submissions || 0, 0);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].options.body.get('notes'), 'Second');
  assert.equal(page.requests[1].options.body.get('base_version'), B);
  assert.equal(page.checklist.submissions || 0, 0);
  page.reply(1, {saved: true, version: C}); await tick();
  assert.equal(page.checklist.submissions, 1);
});

test('retries an uncertain write without another edit and keeps the same payload/version', async () => {
  const page = browser(); page.input('First'); page.runTimer(700); await tick();
  page.requests[0].reject(new Error('Connection lost after possible commit')); await tick();
  assert.doesNotMatch(page.ids.focusSaveState.textContent, /^Notes saved/);
  page.runTimer(2000); await tick();
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].options.body.get('notes'), 'First');
  assert.equal(page.requests[1].options.body.get('base_version'), A);
  page.reply(1, {saved: true, version: B}); await tick();
  assert.match(page.ids.focusSaveState.textContent, /saved/i);
});

test('a conflict keeps the draft and blocks navigation until an explicit resolution', async () => {
  const page = browser(); page.input('My draft'); page.runTimer(700); await tick();
  page.reply(0, {error: 'notes_conflict', notes: 'Other tab', version: B}, 409); await tick();
  assert.equal(page.ids.focusNotes.value, 'My draft');
  assert.equal(page.ids.focusNotesConflict.hidden, false);
  page.stop.requestSubmit(); await tick(); assert.equal(page.stop.submissions || 0, 0);
  page.ids.focusOverwriteNotes.emit('click'); await tick();
  assert.equal(page.requests[1].options.body.get('base_version'), B);
  page.reply(1, {saved: true, version: C}); await tick();
  page.stop.requestSubmit(); await tick(); assert.equal(page.stop.submissions, 1);
});

test('recovers a user/task-isolated unsaved draft and warns before an unsafe unload', () => {
  const storage = new Map(), first = browser(storage); first.input('Recovered draft');
  const second = browser(storage); assert.equal(second.ids.focusNotes.value, 'Recovered draft');
  let prevented = false;
  for (const fn of second.winEvents.beforeunload || []) fn({preventDefault() { prevented = true; }});
  assert(prevented);
  assert([...storage.keys()].some(key => key.includes(':7:11')));
});

test('waits for saved notes before following a link', async () => {
  const page = browser(); page.input('Before navigation'); const event = page.clickLink();
  await tick(); assert(event.defaultPrevented); assert.equal(page.navigations.length, 0);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.deepEqual(page.navigations, ['https://testserver/tasks/']);
});

test('account switching clears older-user drafts and another task cannot recover them', () => {
  const storage = new Map(); browser(storage).input('Private user seven task eleven');
  const otherTask = browser(storage, {task: '12'});
  assert.equal(otherTask.ids.focusNotes.value, 'Original');
  const otherUser = browser(storage, {user: '8'});
  assert.equal(otherUser.ids.focusNotes.value, 'Original');
  assert(![...storage.keys()].some(key => key.includes(':7:11')));
});

test('recovered draft against changed saved notes requires a conflict choice', () => {
  const storage = new Map(); browser(storage).input('My local draft');
  const page = browser(storage, {initialNotes: 'Changed on server', initialVersion: B});
  assert.equal(page.ids.focusNotes.value, 'My local draft');
  assert.equal(page.ids.focusNotesConflict.hidden, false);
  assert.equal(page.requests.length, 0);
  const reopened = browser(storage, {initialNotes: 'Changed on server', initialVersion: B});
  assert.equal(reopened.ids.focusNotesConflict.hidden, false, 'Reload must not silently resolve a conflict');
  assert.equal(reopened.requests.length, 0);
  page.ids.focusUseSavedNotes.emit('click');
  assert.equal(page.ids.focusNotes.value, 'Changed on server');
  assert(!storage.has('tasker:focus-draft:7:11'));
});

test('a hung request times out and retries the uncertain snapshot', async () => {
  const page = browser(); page.input('Timeout draft'); page.runTimer(700); await tick();
  page.runTimer(15000); await tick(); page.runTimer(2000); await tick();
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].options.body.get('notes'), 'Timeout draft');
});

test('storage denial does not claim draft recovery or a successful server save', async () => {
  const page = browser(new Map(), {storageDenied: true});
  page.input('Keep open'); page.runTimer(700); await tick();
  page.requests[0].reject(new Error('Offline')); await tick();
  assert.match(page.ids.focusNotesStatus.textContent, /recovery is unavailable; keep this page open/);
  assert.doesNotMatch(page.ids.focusSaveState.textContent, /^Notes saved/);
});

test('HTML permission errors stop automatic retries and retain unsaved text', async () => {
  const page = browser(); page.input('Access revoked'); page.runTimer(700); await tick();
  page.requests[0].resolve({ok: false, status: 403, redirected: false, json: async () => { throw new Error('HTML error page'); }});
  await tick();
  assert.match(page.ids.focusNotesStatus.textContent, /Check your sign-in, access/);
  assert.equal(page.ids.focusNotes.value, 'Access revoked');
  assert.throws(() => page.runTimer(2000), /No 2000ms timer/);
});

test('exposes the versioned notes flush seam used by private idea parking', async () => {
  const page = browser();
  assert.equal(page.window.TaskerFocusNotes.isDirty(), false);
  page.input('Shared task note');
  assert.equal(page.window.TaskerFocusNotes.isDirty(), true);
  const saved = page.window.TaskerFocusNotes.flush();
  assert.equal(page.requests.length, 1);
  page.reply(0, {saved: true, version: B});
  assert.equal(await saved, true);
  assert.equal(page.window.TaskerFocusNotes.isDirty(), false);
});

test('generic form navigation waits for private parking and replays only once', async () => {
  const page = browser();
  let resolveParking, calls = 0, dirty = true;
  page.window.TaskerFocusParking = {isDirty: () => dirty, flush: () => {
    calls += 1;
    return new Promise(resolve => { resolveParking = value => { dirty = !value; resolve(value); }; });
  }};
  page.checklist.requestSubmit();
  page.checklist.requestSubmit();
  assert.equal(page.checklist.submissions || 0, 0);
  assert.equal(calls, 1, 'Repeated submission cannot race a second navigation');
  assert.equal(page.requests.length, 0);
  resolveParking(true); await tick();
  assert.equal(page.checklist.submissions, 1);
  assert.equal(calls, 1);
});

test('denied private parking blocks generic form mutations and links', async () => {
  const page = browser();
  let calls = 0;
  page.window.TaskerFocusParking = {isDirty: () => true, flush: async () => { calls += 1; return false; }};
  page.stop.requestSubmit(); await tick();
  assert.equal(page.stop.submissions || 0, 0);
  const event = page.clickLink(); await tick();
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(page.navigations, []);
  assert.equal(calls, 2);
  assert.equal(page.requests.length, 0, 'A rejected idea save does not bypass into another mutation');
});

test('link navigation waits for both private parking and newest shared notes', async () => {
  const page = browser();
  let resolveParking, dirty = true;
  page.window.TaskerFocusParking = {isDirty: () => dirty,
    flush: () => new Promise(resolve => { resolveParking = () => { dirty = false; resolve(true); }; })};
  page.input('Before leaving');
  const event = page.clickLink();
  assert.equal(event.defaultPrevented, true);
  assert.equal(page.requests.length, 0, 'The parking guard owns its notes-before-capture ordering');
  resolveParking(); await tick();
  assert.equal(page.requests.length, 1);
  assert.deepEqual(page.navigations, []);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.deepEqual(page.navigations, ['https://testserver/tasks/']);
});

test('private parking form saves notes then capture without native submit or timer mutation', async () => {
  const page = browser(new Map(), {parking: true});
  page.input('Shared progress');
  page.ids.focusParkingText.value = 'A private business idea';
  page.ids.focusParkingText.emit('input');
  page.ids.focusParkingForm.requestSubmit(); await tick();
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, '/tasks/task/focus/notes/');
  assert.equal(page.ids.focusParkingForm.submissions || 0, 0);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].url, '/tasks/task/focus/park/');
  assert.equal(page.requests[1].options.body.get('content'), 'A private business idea');
  page.reply(1, {saved: true, capture_url: '/tasks/inbox/idea/', new_client_key: '22222222-2222-4222-8222-222222222222'});
  await tick();
  assert.equal(page.ids.focusParkingForm.submissions || 0, 0, 'Document submit guard must not replay the AJAX parking form');
  assert.equal(page.ids.focusParkingText.value, '');
  assert.equal(page.window.TaskerFocusParking.isDirty(), false);
  assert.equal(page.ids.focusTimer.dataset.running, '0');
  assert.deepEqual(page.navigations, []);
});

test('reviewed completion keeps required browser acceptance and skips the second confirmation', async () => {
  const page = browser();
  const complete = page.form('complete', {review: true});
  complete.setAttribute('data-confirm-complete', '');
  complete.setAttribute('data-definition-review', '');
  complete.requestSubmit();
  assert.equal(complete.invalidSubmissions, 1);
  assert.equal(complete.submissions || 0, 0);
  assert.deepEqual(page.confirmations, []);
  complete.controls[0].checked = true;
  page.input('Work verified');
  complete.requestSubmit(); await tick();
  assert.equal(complete.submissions || 0, 0);
  assert.equal(page.requests.length, 1);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.equal(complete.submissions, 1);
  assert.deepEqual(page.confirmations, [], 'Explicit definition review replaces the redundant confirm dialog');
});

test('async completion replay revalidates acceptance instead of bypassing browser constraints', async () => {
  const page = browser();
  const complete = page.form('complete', {review: true});
  complete.setAttribute('data-confirm-complete', '');
  complete.setAttribute('data-definition-review', '');
  complete.controls[0].checked = true;
  page.input('Completion draft'); complete.requestSubmit(); await tick();
  complete.controls[0].checked = false;
  page.reply(0, {saved: true, version: B}); await tick();
  assert.equal(complete.invalidSubmissions, 1);
  assert.equal(complete.submissions || 0, 0, 'Native form.submit() would bypass the unchecked required control');
  page.input('A newer note after failed validation');
  complete.controls[0].checked = true; complete.requestSubmit(); await tick();
  assert.equal(complete.submissions || 0, 0, 'Invalid replay must not leave stale approval that skips a newer unsaved draft');
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].options.body.get('notes'), 'A newer note after failed validation');
  page.reply(1, {saved: true, version: C}); await tick();
  assert.equal(complete.submissions, 1);
});

test('completion without definition review still asks for confirmation once', async () => {
  const page = browser();
  const complete = page.form('complete'); complete.setAttribute('data-confirm-complete', '');
  page.input('Finished'); complete.requestSubmit(); await tick();
  assert.equal(page.confirmations.length, 1);
  page.reply(0, {saved: true, version: B}); await tick();
  assert.equal(complete.submissions, 1);
  assert.equal(page.confirmations.length, 1, 'Approved replay cannot prompt or submit twice');
});

test('advisory interval counts remaining time and reaching it never completes a task', () => {
  const page = browser(new Map(), {interval: {minutes: '25', baseSeconds: '60', started: '2026-10-27T09:59:00Z', running: '1'}});
  assert.equal(page.ids.focusIntervalTimer.textContent, '23:00 remaining');
  assert.equal(page.ids.focusIntervalTimer.dataset.reached, '0');
  page.advanceTime(23 * 60 * 1000);
  assert.equal(page.ids.focusIntervalTimer.textContent, 'Interval target reached — continue, pause or finish when ready.');
  assert.equal(page.ids.focusIntervalTimer.dataset.reached, '1');
  assert.equal(page.requests.length, 0);
  assert.equal(page.stop.submissions || 0, 0);
  assert.deepEqual(page.navigations, []);
});

test('paused intervals stay still and a target set mid-session accounts for its negative base', () => {
  const paused = browser(new Map(), {interval: {minutes: '25', baseSeconds: '120', started: '2026-10-27T09:00:00Z', running: '0'}});
  assert.equal(paused.ids.focusIntervalTimer.textContent, '23:00 remaining');
  paused.advanceTime(60 * 60 * 1000);
  assert.equal(paused.ids.focusIntervalTimer.textContent, '23:00 remaining');
  const reset = browser(new Map(), {interval: {minutes: '25', baseSeconds: '-600', started: '2026-10-27T09:50:00Z', running: '1'}});
  assert.equal(reset.ids.focusIntervalTimer.textContent, '25:00 remaining');
  reset.advanceTime(60 * 1000);
  assert.equal(reset.ids.focusIntervalTimer.textContent, '24:00 remaining');
});
