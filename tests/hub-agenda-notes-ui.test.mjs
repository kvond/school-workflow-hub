import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.value = ''; this.classList = {toggle() {}}; }
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
  + section('const plans =', 'function loadClassPolicyNotes(')
  + section('function block(', '// Last week\'s lesson links')
  + section('async function postJSON(', 'async function openPanel(');
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const find = (root, tag, text = '') => walk(root).find(n => n.tagName === tag && n.textContent.includes(text));
const CLS = {key: 'biology', name: 'Biology'};
const lesson = () => ({row: 9, week: 'Wk 03', topic: 'Fictional lesson', today: {day: 'Mon', plan: 'Plan', note: ''},
  plans: [{day: 'Mon', plan: 'Plan', note: ''}, {day: 'Wed', plan: 'Second day', note: 'A note'}],
  editable: {'plan:Mon': true, 'note:Mon': true, 'plan:Wed': true, 'note:Wed': false, topic: true}, materials: [], forms: [], links: {}});
function boot(reply = body => body) {
  const calls = [], nodes = new Map();
  const context = vm.createContext({Node: Element, Intl, Date,
    document: {createElement: tag => new Element(tag), getElementById: id => {
      if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id);
    }},
    renderPlans() {}, loadClassPolicyNotes() {},
    fetch: async (url, options = {}) => {
      const body = options.body && JSON.parse(options.body);
      calls.push({url, method: options.method || 'GET', body});
      const result = await reply(body);
      return {ok: !result?.error, status: result?.status || 200, json: async () => result};
    }});
  vm.runInContext(declarations + '\nglobalThis.ui = {plans, loadPlans, renderLesson, startEdit, lessonEditor, saveLesson};', context);
  context.ui.plans.date = '2032-01-05'; context.ui.plans.data = {};
  return {ui: context.ui, calls, nodes};
}

test('empty day notes are visible with a focused editor using only mapped day fields', () => {
  const {ui} = boot(), item = lesson();
  const card = ui.renderLesson(CLS, item, 'Mon');
  assert.match(card.textContent, /Day notesNo day notes written yet/);
  find(card, 'button', 'Edit plan and day notes').fire('click');
  assert.deepEqual(Object.keys(ui.plans.editing.base), ['plan:Mon', 'note:Mon']);
  const form = ui.lessonEditor(CLS, item);
  assert.match(form.textContent, /Edit Monday plan and notes/);
  assert.doesNotMatch(form.textContent, /Move this lesson|Deck link|Topic/);
  assert.equal(walk(form).filter(n => n.tagName === 'textarea').length, 2);
});

test('protected Agenda cells stay locked in the day editor', () => {
  const {ui} = boot(), item = lesson();
  ui.startEdit(CLS, item, ['plan:Wed', 'note:Wed']);
  const form = ui.lessonEditor(CLS, item);
  assert.equal(walk(form).find(n => n.id === 'edit-biology-9-note-Wed').disabled, true);
  assert.match(form.textContent, /edit it in the Agenda/);
});

test('saving a day note uses the exact class, week, row and original note then reloads Agenda', async () => {
  const {ui, calls} = boot(body => body ? {saved: ['note:Mon'], unconfirmed: []}
    : {classes: [], problems: [], week: {label: 'Wk 03', monday: '2032-01-05'}});
  ui.startEdit(CLS, lesson(), ['plan:Mon', 'note:Mon']);
  ui.plans.editing.draft['note:Mon'] = 'Bring the lab supplies.\nSecond line.';
  await ui.saveLesson();
  assert.deepEqual(calls[0], {url: 'api/agenda', method: 'POST', body: {class: 'biology', week: 'Wk 03', row: 9,
    changes: {'note:Mon': {from: '', to: 'Bring the lab supplies.\nSecond line.'}}}});
  assert.match(calls[1].url, /api\/day\?date=2032-01-05&fresh=1/);
  assert.equal(ui.plans.editing, null);
});

test('date navigation and another editor cannot silently erase an open Agenda draft', async () => {
  const {ui, calls, nodes} = boot();
  ui.startEdit(CLS, lesson(), ['plan:Mon', 'note:Mon']);
  const draft = ui.plans.editing; draft.draft['note:Mon'] = 'Keep this draft';
  await ui.loadPlans('2032-01-12'); ui.startEdit(CLS, {...lesson(), row: 10});
  assert.equal(ui.plans.editing, draft); assert.equal(ui.plans.date, '2032-01-05');
  assert.equal(calls.length, 0); assert.match(nodes.get('plans-status').textContent, /Save or cancel/);
});

test('a concurrent Agenda edit keeps the day draft and shows the current value', async () => {
  const {ui} = boot(() => ({error: 'Changed in Agenda', status: 409, current: {'note:Mon': 'Other teacher edit'}}));
  ui.startEdit(CLS, lesson(), ['plan:Mon', 'note:Mon']);
  ui.plans.editing.draft['note:Mon'] = 'My day note';
  await ui.saveLesson();
  assert.equal(ui.plans.editing.draft['note:Mon'], 'My day note');
  const form = ui.lessonEditor(CLS, lesson());
  assert.match(form.textContent, /Other teacher edit/);
  assert.match(form.textContent, /Agenda now says/);
});
