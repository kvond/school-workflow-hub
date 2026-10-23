import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.value = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return (this._text || '') + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this[key] = key === 'disabled' ? true : value; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  fire(type) { for (const handler of this.listeners[type] || []) handler({preventDefault() {}}); }
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const declarations = source.slice(0, source.indexOf('// ---------- Hub v3'))
  + section('function loadClassPolicyNotes(', 'function block(')
  + section('async function postJSON(', 'async function openPanel(');
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const find = (root, tag, text = '') => walk(root).find(n => n.tagName === tag && n.textContent.includes(text));
const tick = () => new Promise(resolve => setImmediate(resolve));
const saved = (text, rev = 'a') => ({text, revision: rev.repeat(64), updated_at: '2032-01-05T12:00:00Z'});
const BIO = {key: 'biology', name: 'Biology'};

function boot(reader = () => saved('Initial policy'), writer = body => saved(body.text, 'b'), legacy = {}) {
  const calls = [], keys = Object.keys(legacy);
  const context = vm.createContext({Node: Element, Intl, Date,
    document: {createElement: tag => new Element(tag)},
    localStorage: {length: keys.length, key: i => keys[i], getItem: key => legacy[key], setItem() { throw new Error('Do not migrate browser notes'); }},
    fetch: async (url, options = {}) => {
      const method = options.method || 'GET', body = options.body && JSON.parse(options.body);
      calls.push({url, method, body});
      const result = await (method === 'GET' ? reader(url) : writer(body));
      return {ok: !result.error, status: result.status || 200, json: async () => result};
    }});
  vm.runInContext(declarations + '\nglobalThis.ui = {classNoteState, classNotesPanel, loadClassPolicyNotes, loadClassNotes, saveClassNotes, classNotesUnsaved};', context);
  return {ui: context.ui, calls};
}
function type(state, text) { const input = find(state.body, 'textarea'); input.value = text; input.fire('input'); }

test('class notes read passively, survive week rerenders, and save only on the teacher action', async () => {
  const {ui, calls} = boot();
  ui.classNotesPanel(BIO);
  const state = ui.classNoteState(BIO);
  await ui.loadClassNotes(state);
  assert.equal(find(state.body, 'textarea').value, 'Initial policy');
  assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET');
  type(state, 'New policy\nKeep the originals.');
  ui.classNotesPanel(BIO); // the same class beneath a different week
  ui.loadClassPolicyNotes({classes: [BIO]}); await tick();
  assert.equal(calls.length, 1);
  assert.equal(find(state.body, 'textarea').value, 'New policy\nKeep the originals.');
  assert.equal(ui.classNotesUnsaved(), true);
  await ui.saveClassNotes(state);
  assert.deepEqual(calls[1], {url: 'api/classes/notes', method: 'POST', body: {class: 'biology', text: state.draft, revision: 'a'.repeat(64)}});
  assert.match(state.body.textContent, /Saved and read back/);
  assert.equal(ui.classNotesUnsaved(), false);
});

test('unavailable initial notes stay unknown and cannot be overwritten as empty', async () => {
  const {ui, calls} = boot(() => ({error: 'Private store unavailable', status: 503}));
  ui.classNotesPanel(BIO); const state = ui.classNoteState(BIO);
  await ui.loadClassNotes(state); await ui.saveClassNotes(state);
  assert.equal(state.loaded, false);
  assert.equal(find(state.body, 'textarea').disabled, true);
  assert.match(state.body.textContent, /Private store unavailable/);
  assert.equal(calls.length, 1);
});

test('a changed saved version requires explicit comparison before replacing it', async () => {
  let current = saved('Initial');
  const {ui, calls} = boot(() => current);
  ui.classNotesPanel(BIO); const state = ui.classNoteState(BIO);
  await ui.loadClassNotes(state); type(state, 'My draft');
  current = saved('Another tab policy', 'c');
  await ui.loadClassNotes(state);
  assert.equal(state.draft, 'My draft');
  assert.match(state.body.textContent, /Another tab policy/);
  await ui.loadClassNotes(state); // Repeated reads cannot waive the unresolved comparison.
  assert.equal(state.comparison.text, 'Another tab policy');
  await ui.saveClassNotes(state);
  assert.equal(calls.filter(c => c.method === 'POST').length, 0);
  await ui.saveClassNotes(state, true);
  assert.equal(calls.at(-1).body.revision, 'c'.repeat(64));
  assert.equal(calls.at(-1).body.text, 'My draft');
});

test('an uncertain save keeps the draft and requires readback before any retry', async () => {
  let current = saved('Initial');
  const {ui, calls} = boot(() => current, body => {
    current = saved(body.text, 'd'); return {error: 'Save unconfirmed', status: 503};
  });
  ui.classNotesPanel(BIO); const state = ui.classNoteState(BIO);
  await ui.loadClassNotes(state); type(state, 'New policy');
  await ui.saveClassNotes(state); await ui.saveClassNotes(state);
  assert.equal(state.draft, 'New policy'); assert.equal(state.needsReload, true);
  assert.equal(calls.filter(c => c.method === 'POST').length, 1);
  await ui.loadClassNotes(state);
  assert.equal(state.draft, 'New policy'); assert.equal(state.comparison, null);
  assert.equal(ui.classNotesUnsaved(), false);
  await ui.saveClassNotes(state);
  assert.equal(calls.filter(c => c.method === 'POST').length, 1);
});

test('typing while a read is delayed preserves the newer draft', async () => {
  let finish;
  const delayed = boot(() => new Promise(resolve => { finish = resolve; }));
  delayed.ui.classNotesPanel(BIO); const pendingState = delayed.ui.classNoteState(BIO);
  pendingState.loaded = true; pendingState.saved = saved('Initial'); pendingState.draft = 'Initial';
  const pending = delayed.ui.loadClassNotes(pendingState);
  type(pendingState, 'Typed during read'); finish(saved('Other tab', 'c')); await pending;
  assert.equal(find(pendingState.body, 'textarea').value, 'Typed during read');
  assert.equal(pendingState.comparison.text, 'Other tab');
});

test('legacy notes remain read-only, class-scoped, and never automatically copied or removed', async () => {
  const {ui, calls} = boot(() => saved('Current'), undefined, {
    'hub2:next-week-note:biology:2032-01-05': 'Old Biology note',
    'hub2:next-week-note:forensics:2032-01-05': 'Other class note',
    'hub2:next-week-note:biology:invalid': 'Ignore invalid key',
    'unrelated': 'Other private value'});
  const panel = ui.classNotesPanel(BIO); const state = ui.classNoteState(BIO);
  assert.match(panel.textContent, /Old Biology note/);
  assert.doesNotMatch(panel.textContent, /Other class note|Other private value|Ignore invalid key/);
  await ui.loadClassNotes(state);
  assert.equal(state.draft, 'Current');
  assert.equal(calls.filter(c => c.method === 'POST').length, 0);
});
