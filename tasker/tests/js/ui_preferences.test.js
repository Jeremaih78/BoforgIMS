const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const api = require('../../static/tasker/js/ui_preferences.js');
const prefsSource = fs.readFileSync(path.join(__dirname, '../../static/tasker/js/ui_preferences.js'), 'utf8');
const uiSource = fs.readFileSync(path.join(__dirname, '../../static/tasker/js/ui.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function preferenceBrowser(user = '7', store = new Map(), {blocked = false, dark = false} = {}) {
  const events = {}, mediaEvents = {}, emitted = [];
  const document = {documentElement: {dataset: {taskerUser: user}}, dispatchEvent: event => emitted.push(event)};
  const window = {
    document, CustomEvent: class { constructor(type, data) { this.type = type; this.detail = data.detail; } },
    matchMedia: () => ({matches: dark, addEventListener: (name, handler) => { mediaEvents[name] = handler; }}),
    addEventListener: (name, handler) => { events[name] = handler; },
    get localStorage() {
      if (blocked) throw new Error('Browser storage denied');
      return {getItem: key => store.get(key) ?? null, setItem: (key, value) => { store.set(key, value); }};
    },
  };
  vm.runInNewContext(prefsSource, {window});
  return {api: window.TaskerUIPreferences, window, document, events, mediaEvents, emitted, store};
}
const key = (value, extra = {}) => ({key: value, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...extra});

test('malformed preferences retain safe defaults without accepting unknown fields', () => {
  for (const candidate of [null, 4, [], 'bad', {theme: 'javascript:alert(1)', shortcuts_enabled: 'no'}]) {
    assert.deepEqual(api.normalizePrefs(candidate), api.defaults());
  }
  const prefs = api.normalizePrefs({theme: 'light', shortcuts_enabled: false, privateNotes: 'must not store', shortcuts: {capture: 'alt+q'}});
  assert.equal(prefs.theme, 'light');
  assert.equal(prefs.shortcuts_enabled, false);
  assert.equal(prefs.shortcuts.capture, 'alt+q');
  assert.equal(Object.hasOwn(prefs, 'privateNotes'), false);
});
test('duplicate and reserved shortcuts are rejected rather than silently reassigned', () => {
  const duplicates = {...api.DEFAULT_KEYS, now: 'n'};
  assert.equal(api.validateKeymap(duplicates).valid, false);
  assert.ok(api.validateKeymap(duplicates).errors.capture);
  for (const binding of ['ctrl+n', 'meta+w', 'alt+f', 'tab', 'enter', 'escape', 'space', 'alt+alt+n']) {
    assert.equal(api.parseShortcut(binding, 'capture'), null, binding);
  }
  assert.equal(api.validateKeymap({...api.DEFAULT_KEYS, focus_block: 'n'}).valid, true);
});
test('question-mark aliases collide and punctuation matches actual keyboard events', () => {
  assert.equal(api.parseShortcut('shift+/', 'help').binding, '?');
  assert.equal(api.validateKeymap({...api.DEFAULT_KEYS, search: 'shift+/'}).valid, false);
  assert.equal(api.actionForEvent(api.defaults(), key('?', {shiftKey: true})), 'help');
  assert.equal(api.actionForEvent(api.defaults(), key('/')), 'search');
});
test('disabled or editable/dialog/IME/repeated keyboard events do not trigger actions', () => {
  const prefs = api.defaults();
  assert.equal(api.actionForEvent({...prefs, shortcuts_enabled: false}, key('n')), null);
  for (const context of [{editable: true}, {dialogOpen: true}]) assert.equal(api.actionForEvent(prefs, key('n'), context), null);
  for (const extra of [{ctrlKey: true}, {metaKey: true}, {isComposing: true}, {repeat: true}, {defaultPrevented: true}, {altKey: true}]) {
    assert.equal(api.actionForEvent(prefs, key('n', extra)), null);
  }
  assert.equal(api.isEditableTarget({isContentEditable: true}), true);
  assert.equal(api.isEditableTarget({closest: () => ({tagName: 'TEXTAREA'})}), true);
  assert.equal(api.isEditableTarget({closest: () => null}), false);
});
test('normal and Focus shortcut scopes do not execute each other and remapping is real', () => {
  const prefs = api.defaults();
  assert.equal(api.actionForEvent(prefs, key('b')), null);
  assert.equal(api.actionForEvent(prefs, key('b'), {scope: 'focus'}), 'focus_block');
  assert.equal(api.actionForEvent(prefs, key(' '), {scope: 'focus'}), 'focus_pause');
  assert.equal(api.actionForEvent(prefs, key('Escape'), {scope: 'focus'}), 'focus_exit');
  assert.equal(api.actionForEvent(prefs, key('n'), {scope: 'focus'}), null);
  prefs.shortcuts.capture = 'alt+q'; prefs.shortcuts.focus_block = 'alt+v';
  assert.equal(api.actionForEvent(prefs, key('n')), null);
  assert.equal(api.actionForEvent(prefs, key('q', {altKey: true})), 'capture');
  assert.equal(api.actionForEvent(prefs, key('v', {altKey: true}), {scope: 'focus'}), 'focus_block');
});
test('blank individual shortcut disables only that action', () => {
  const prefs = api.defaults(); prefs.shortcuts.capture = '';
  assert.equal(api.validateKeymap(prefs.shortcuts).valid, true);
  assert.equal(api.actionForEvent(prefs, key('n')), null);
  assert.equal(api.actionForEvent(prefs, key('t')), 'now');
  assert.equal(api.formatShortcut(''), 'Off');
  assert.equal(api.formatShortcut('alt+shift+q'), 'Alt + Shift + Q');
});
test('head initialization resolves system preference and follows OS changes', () => {
  const browser = preferenceBrowser('7', new Map(), {dark: true});
  assert.equal(browser.document.documentElement.dataset.bsTheme, 'dark');
  browser.mediaEvents.change({matches: false});
  assert.equal(browser.document.documentElement.dataset.bsTheme, 'light');
  browser.api.save({...browser.api.defaults(), theme: 'dark'});
  browser.mediaEvents.change({matches: false});
  assert.equal(browser.document.documentElement.dataset.bsTheme, 'dark');
});
test('preferences persist per account and survive a simulated page reload', () => {
  const store = new Map(), first = preferenceBrowser('7', store);
  const candidate = first.api.defaults(); candidate.theme = 'dark'; candidate.shortcuts_enabled = false;
  const result = first.api.save(candidate);
  assert.equal(result.ok, true); assert.equal(result.persisted, true);
  assert.equal(preferenceBrowser('7', store).api.get().shortcuts_enabled, false);
  assert.equal(preferenceBrowser('8', store).api.get().shortcuts_enabled, true);
  assert.equal(preferenceBrowser('8', store).api.get().theme, 'system');
  assert.equal(store.size, 1);
});
test('storage denial applies in memory without falsely claiming persistence', () => {
  const browser = preferenceBrowser('7', new Map(), {blocked: true});
  const result = browser.api.save({...browser.api.defaults(), theme: 'light'});
  assert.equal(result.ok, true); assert.equal(result.persisted, false);
  assert.equal(browser.api.get().theme, 'light');
  assert.equal(browser.document.documentElement.dataset.bsTheme, 'light');
});
test('corrupt stored JSON falls back and cross-tab events are account-scoped', () => {
  const store = new Map([['tasker:ui:v1:7', '{corrupt']]);
  const browser = preferenceBrowser('7', store);
  assert.equal(browser.api.get().theme, 'system');
  store.set('tasker:ui:v1:7', JSON.stringify({...api.defaults(), theme: 'dark'}));
  browser.events.storage({key: 'tasker:ui:v1:8'});
  assert.equal(browser.api.get().theme, 'system');
  browser.events.storage({key: 'tasker:ui:v1:7'});
  assert.equal(browser.api.get().theme, 'dark');
  assert.equal(browser.api.reset().persisted, true);
  assert.equal(browser.api.get().theme, 'system');
});

function interactionBrowser({editable = false, serverErrors = false} = {}) {
  const docEvents = {}, winEvents = {}, loaderClasses = new Set();
  function element(tagName) {
    return {tagName, dataset: {}, attributes: {}, children: [], disabled: false,
      setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
      getAttribute(name) { return this.attributes[name] ?? null; },
      prepend(node) { node.parent = this; this.children.unshift(node); },
      remove() { this.parent.children = this.parent.children.filter(node => node !== this); },
    };
  }
  const button = element('BUTTON'); button.name = 'action'; button.value = 'save';
  const form = element('FORM'); form.action = 'https://testserver/tasks/'; form.method = 'post'; form.target = '';
  const formEvents = {};
  const status = {textContent: 'Changes loaded.'};
  form.addEventListener = (name, handler) => { (formEvents[name] ||= []).push(handler); };
  form.querySelector = selector => selector.includes('form-status') ? status : selector.includes('errorlist') ? serverErrors ? {} : null : button;
  form.querySelectorAll = selector => selector.includes('busy-button') ? button.dataset.taskerBusyButton ? [button] : [] : button.children;
  const loader = {classList: {add: name => loaderClasses.add(name), remove: name => loaderClasses.delete(name)}};
  const document = {
    getElementById: id => id === 'taskerRouteLoader' ? loader : null,
    querySelectorAll: selector => selector === 'form' || editable && selector === '[data-tasker-edit-form]' ? [form] : [], querySelector: () => null,
    addEventListener: (name, handler) => { (docEvents[name] ||= []).push(handler); }, createElement: element,
  };
  const window = {location: {href: 'https://testserver/tasks/', origin: 'https://testserver', assign() {}}, addEventListener: (name, handler) => { winEvents[name] = handler; }};
  vm.runInNewContext(uiSource, {window, document, URL, Promise});
  function submit(lateCancel = false) {
    const event = {target: form, submitter: button, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
    for (const handler of formEvents.submit) handler(event);
    if (lateCancel) event.preventDefault();
    return event;
  }
  function click(overrides = {}, lateCancel = false) {
    const link = {href: 'https://testserver/planner/', target: '', hasAttribute: () => false};
    const event = {target: {closest: () => link}, button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...overrides};
    for (const handler of docEvents.click || []) handler(event);
    if (lateCancel) event.preventDefault();
  }
  function input() { for (const handler of formEvents.input || []) handler({target: form}); }
  return {button, form, loaderClasses, winEvents, submit, click, input, status};
}
test('late cancelled notes/confirmation submission never leaves a busy form or loader', async () => {
  const browser = interactionBrowser(); browser.submit(true); await tick();
  assert.equal(browser.form.dataset.taskerSubmitting, undefined);
  assert.equal(browser.button.disabled, false);
  assert.equal(browser.loaderClasses.size, 0);
});
test('normal form submission preserves named submitter and blocks duplicate submission', async () => {
  const browser = interactionBrowser(); browser.submit(); await tick();
  assert.equal(browser.form.dataset.taskerSubmitting, 'true');
  assert.equal(browser.button.disabled, false);
  assert.equal(browser.button.name, 'action'); assert.equal(browser.button.value, 'save');
  assert.equal(browser.submit().defaultPrevented, true);
  browser.winEvents.pageshow({persisted: true});
  assert.equal(browser.form.dataset.taskerSubmitting, undefined);
  assert.equal(browser.button.children.length, 0);
  assert.equal(browser.loaderClasses.size, 0);
});
test('cancelled and modified link navigation does not leave a route loader', async () => {
  const browser = interactionBrowser();
  browser.click({}, true); await tick(); assert.equal(browser.loaderClasses.size, 0);
  browser.click({ctrlKey: true}); await tick(); assert.equal(browser.loaderClasses.size, 0);
  browser.click(); await tick(); assert.equal(browser.loaderClasses.has('is-active'), true);
  browser.winEvents.pageshow({}); assert.equal(browser.loaderClasses.size, 0);
});
test('marked edit forms announce dirty state, warn on leaving and retain drafts after cancelled save', async () => {
  const browser = interactionBrowser({editable: true});
  browser.input(); assert.equal(browser.status.textContent, 'Unsaved changes.');
  const before = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
  browser.winEvents.beforeunload(before); assert.equal(before.defaultPrevented, true);
  browser.submit(true); await tick();
  assert.equal(browser.status.textContent, 'Unsaved changes.');
});
test('saving marked forms suppresses unload warning and restored forms become usable', async () => {
  const browser = interactionBrowser({editable: true}); browser.input(); browser.submit(); await tick();
  assert.equal(browser.status.textContent, 'Saving changes…');
  const event = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
  browser.winEvents.beforeunload(event); assert.equal(event.defaultPrevented, false);
  browser.winEvents.pageshow({persisted: true});
  assert.equal(browser.form.dataset.taskerSubmitting, undefined);
  assert.match(browser.status.textContent, /Review before saving/);
  browser.winEvents.beforeunload(event); assert.equal(event.defaultPrevented, true);
});
test('ordinary action forms do not trigger unsaved-change warnings', () => {
  const browser = interactionBrowser(); browser.input();
  const event = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
  browser.winEvents.beforeunload(event); assert.equal(event.defaultPrevented, false);
});

test('named promotion controls cannot suppress save state or trigger a false unload warning', async () => {
  const browser = interactionBrowser({editable: true});
  browser.form.target = {value: 'task'};
  browser.form.action = {value: 'promote'};
  browser.input(); browser.submit(); await tick();
  assert.equal(browser.form.dataset.taskerSubmitting, 'true');
  const event = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
  browser.winEvents.beforeunload(event);
  assert.equal(event.defaultPrevented, false);
});
