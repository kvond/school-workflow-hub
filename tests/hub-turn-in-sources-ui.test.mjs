// Student work: "Where work was turned in" counts. Synthetic data, no network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const helpers = source.slice(0, source.indexOf('const store = {'));
const start = source.indexOf('// Where this week\'s work was turned in');
const block = source.slice(start, source.indexOf('\nfunction renderWork() {', start));

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const all = node => node instanceof Element ? [node, ...node.children.flatMap(all)] : [];

function load() {
  const document = {createElement: tag => new Element(tag), getElementById: () => new Element('div')};
  const context = vm.createContext({document, Node: Element});
  vm.runInContext(helpers + block + '\nglobalThis.ui = {renderTurnInSources, turnInPlace};', context);
  return context.ui;
}

const row = (section, source, file = {}) => ({matched: true, section, source, files: [{name: 'x', ...file}]});
const data = {lessons: [{topic: 'Fictional cells', forms: [{
  form_id: 'F1', label: 'Cells',
  students: [row('A1', 'form'), row('A1', 'folder', {from_folder: true}), row('A2', 'form', {from_shared: true}),
             row('A2', 'form', {via: 'Wk 06 · Tissues'}), {matched: false, section: '', source: 'form', files: []}],
  late: [row('A1', 'folder', {from_folder: true})],
  elsewhere: [{section: 'A1', accepted_intake: false}, {section: 'A2', accepted_intake: true}]}]},
  {topic: 'No form', forms: [{form_id: null, label: 'none'}]}],
  other_files: [{matched: false}, {matched: true, section: 'A2'}]};

test('counts each current submission by where it was turned in', () => {
  const box = load().renderTurnInSources(data);
  assert.equal(box.tagName, 'details');
  assert.notEqual(box.open, true);
  const summary = all(box).find(n => n.tagName === 'summary').textContent;
  assert.equal(summary, 'Where work was turned in · 6 submissions · 2 awaiting a lesson match');
  const cells = all(box).filter(n => n.tagName === 'td').map(n => n.textContent);
  assert.deepEqual(cells, ['Cells', '2', '2', '1', '1', '1']);
  assert.match(box.textContent, /Confirm these submission matches/);
});

test('a section filter keeps unmatched work and that section only', () => {
  const box = load().renderTurnInSources(data, 'A1');
  const cells = all(box).filter(n => n.tagName === 'td').map(n => n.textContent);
  assert.deepEqual(cells, ['Cells', '2', '2', '0', '0', '1']);
  assert.match(all(box).find(n => n.tagName === 'summary').textContent, /4 submissions · 1 awaiting/);
});

test('nothing is shown without a Form or waiting file', () => {
  const ui = load();
  assert.equal(ui.renderTurnInSources({lessons: [], other_files: []}), null);
  assert.equal(ui.renderTurnInSources(null), null);
});
