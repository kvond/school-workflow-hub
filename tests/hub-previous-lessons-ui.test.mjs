// Lessons: last week's lesson links per class. Synthetic data, no network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const renderers = source.slice(0, source.indexOf('async function printTodayFeedback('));
const start = source.indexOf('function renderPreviousLessons(');
const previous = source.slice(start, source.indexOf('\nfunction renderPlans(', start));

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.listeners = {}; this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const all = node => node instanceof Element ? [node, ...node.children.flatMap(all)] : [];

function load() {
  const document = {createElement: tag => new Element(tag), querySelectorAll: () => [], getElementById: () => new Element('div')};
  const context = vm.createContext({document, Node: Element, location: {hash: '#plans'}, URL});
  vm.runInContext(renderers + previous + '\nglobalThis.ui = {renderPreviousLessons};', context);
  return context.ui;
}

test('last week opens to every lesson link, closed by default', () => {
  const box = load().renderPreviousLessons({previous: {week: 'Wk 07', monday: '2032-01-05', lessons: [
    {row: 4, topic: 'Fictional cells', edit_url: 'https://docs.google.com/spreadsheets/d/FICTIONAL/edit#gid=2&range=A4',
     materials: [{label: 'Teacher deck', url: 'https://docs.google.com/presentation/d/FICTIONALDECK/edit'}],
     forms: [{label: 'Cells', url: 'https://docs.google.com/forms/d/FICTIONALFORM/viewform'}]},
    {row: 5, topic: 'Fictional tissues', materials: [], forms: [], edit_url: ''}]}});
  assert.equal(box.tagName, 'details');
  assert.notEqual(box.open, true);
  assert.match(box.textContent, /Last week’s lessons · Wk 07 · Fictional cells \/ Fictional tissues/);
  const hrefs = all(box).map(n => n.href).filter(Boolean);
  assert.ok(hrefs.includes('https://docs.google.com/presentation/d/FICTIONALDECK/edit'));
  assert.ok(hrefs.includes('https://docs.google.com/forms/d/FICTIONALFORM/viewform'));
  assert.match(box.textContent, /No links in the Agenda for this lesson\./);
});

test('nothing is shown when there is no earlier lesson', () => {
  const ui = load();
  assert.equal(ui.renderPreviousLessons({}), null);
  assert.equal(ui.renderPreviousLessons({previous: null}), null);
  assert.equal(ui.renderPreviousLessons({previous: {week: 'Wk 07', lessons: []}}), null);
});
