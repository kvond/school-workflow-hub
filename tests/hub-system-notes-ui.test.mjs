import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.handlers = {}; this.className = ''; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const declarations = source.slice(0, source.indexOf('// ---------- Hub v3')) + source.slice(
  source.indexOf('// ---------- Shared System notes'), source.indexOf('// ---------- Routine:'));
const walk = node => [node, ...node.children.filter(child => child instanceof Element).flatMap(walk)];
function boot() {
  const box = new Element('div'), calls = [];
  let saved = {text: 'Existing teacher note', revision: 'a'.repeat(64), updated_at: '2026-10-14T12:00:00Z'};
  const context = vm.createContext({Node: Element, document: {getElementById: () => box, createElement: tag => new Element(tag)},
    fetch: async url => { calls.push({url}); return {ok: true, json: async () => saved}; },
    postJSON: async (url, payload) => { calls.push({url, payload}); saved = {...saved, ...payload, revision: 'b'.repeat(64)}; return saved; }});
  vm.runInContext(declarations + '\nglobalThis.ui = {systemNotes, loadSystemNotes, saveSystemNotes, renderSystemNotes};', context);
  const type = text => { const input = walk(box).find(n => n.tagName === 'textarea'); input.value = text; input.handlers.input(); };
  return {ui: context.ui, box, calls, context, type, setSaved: value => { saved = value; }};
}

test('routine refresh and navigation preserve a dirty draft, and a forced reload compares the saved text', async () => {
  const {ui, box, calls, type, setSaved} = boot();
  await ui.loadSystemNotes();
  type('My unfinished draft');
  await ui.loadSystemNotes();
  assert.equal(calls.length, 1);
  assert.equal(ui.systemNotes.text, 'My unfinished draft');
  setSaved({text: 'Updated on another computer', revision: 'c'.repeat(64), updated_at: '2026-10-14T13:00:00Z'});
  await ui.loadSystemNotes(true);
  assert.equal(ui.systemNotes.text, 'My unfinished draft');
  assert.equal(ui.systemNotes.comparison, 'Updated on another computer');
  assert.equal(ui.systemNotes.dirty, true);
  assert.match(box.textContent, /Your unsaved draft is preserved/);
  await ui.saveSystemNotes();
  assert.equal(calls[2].payload.revision, 'c'.repeat(64));
  assert.equal(calls[2].payload.text, 'My unfinished draft');
  assert.equal(ui.systemNotes.dirty, false);
  assert.equal(ui.systemNotes.comparison, null);
});

test('an uncertain or conflicting save retains the draft and cannot claim Saved', async () => {
  const {ui, context, box, type} = boot();
  await ui.loadSystemNotes(); type('Keep this draft');
  let posts = 0;
  context.postJSON = async () => { posts++; throw new Error('Save unconfirmed; reload saved notes.'); };
  await ui.saveSystemNotes();
  assert.equal(posts, 1);
  assert.equal(ui.systemNotes.text, 'Keep this draft');
  assert.equal(ui.systemNotes.revision, 'a'.repeat(64));
  assert.equal(ui.systemNotes.dirty, true);
  assert.match(box.textContent, /Save unconfirmed/);
  assert.doesNotMatch(box.textContent, /Saved in the Hub/);
});

test('failed initial read does not allow an empty draft to replace existing notes', async () => {
  const {ui, context, box, calls} = boot();
  context.fetch = async () => { throw new Error('Private notes unavailable'); };
  await ui.loadSystemNotes();
  await ui.saveSystemNotes();
  assert.equal(ui.systemNotes.loaded, false);
  assert.equal(calls.length, 0);
  assert.notEqual(walk(box).find(n => n.tagName === 'textarea').disabled, undefined);
  assert.match(box.textContent, /Private notes unavailable/);
});
