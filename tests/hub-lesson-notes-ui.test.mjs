import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.listeners = {}; this.value = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  fire(type, event = {}) { for (const handler of this.listeners[type] || []) handler({target: this, ...event}); }
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const declarations = source.slice(0, source.indexOf('// ---------- Hub v3'))
  + section('async function postJSON(', '\nasync function openPanel(')
  + section('// ---------- Lessons → Notes', '// ---------- Shared System notes');
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const find = (root, tag, text) => walk(root).find(n => n.tagName === tag && (!text || n.textContent.includes(text)));
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

const FORM = 'FictionalForm123456789012345';
const SAVED = {summary: '', notes: 'Earlier reminder', revision: 'a'.repeat(64), updated_at: null, generated_at: null, coverage: null};

function boot(post = () => ({...SAVED, summary: 'What students conflated or misunderstood\n- One idea', cached: false,
  revision: 'b'.repeat(64), coverage: {teacher_notes: 2, reviewed_feedback: 2, students_on_form: 5}})) {
  const calls = [];
  const context = vm.createContext({Node: Element, setTimeout, Intl, Date,
    document: {getElementById: () => null, createElement: tag => new Element(tag)},
    fetch: async (url, options = {}) => {
      calls.push({url, options, body: options.body ? JSON.parse(options.body) : null});
      return {ok: true, json: async () => options.method === 'POST' ? post(url, JSON.parse(options.body)) : SAVED};
    }});
  vm.runInContext(declarations + '\nglobalThis.ui = {lessonNotesPanel, lessonNotesUnsaved, lessonNotes};', context);
  const panel = context.ui.lessonNotesPanel({key: 'biology', name: 'Biology'}, {week: 'Wk 04'},
    {form_id: FORM, label: 'Fictional carbon lesson'});
  return {calls, panel, ui: context.ui};
}

async function open(panel) { panel.open = true; panel.fire('toggle'); await tick(); await tick(); }

test('Notes stay unread until opened and expose saved editing without model generation', async () => {
  const {calls, panel} = boot(); assert.equal(calls.length, 0);
  await open(panel);
  assert.deepEqual(calls.map(c => [c.url, c.options.method || 'GET']), [[`api/lessons/summary?class=biology&form=${FORM}`, 'GET']]);
  assert.equal(find(panel, 'button', 'Summarize this lesson'), undefined);
  assert.equal(walk(panel).filter(n => n.tagName === 'textarea').length, 2);
  assert.match(panel.textContent, /Saving edits does not generate a new summary/);
});

test('editing a saved summary writes only the guarded note revision', async () => {
  const {calls, panel} = boot((url, body) => ({...SAVED, ...body, revision: 'b'.repeat(64)}));
  await open(panel);
  const [summary, own] = walk(panel).filter(n => n.tagName === 'textarea');
  summary.value = 'Teacher-edited summary'; summary.fire('input');
  own.value = 'My reminder'; own.fire('input');
  find(panel, 'button', 'Save notes').fire('click'); await tick(); await tick();
  const posts = calls.filter(c => c.options.method === 'POST'); assert.equal(posts.length, 1);
  assert.equal(posts[0].url, 'api/lessons/summary/save');
  assert.deepEqual(posts[0].body, {class: 'biology', form: FORM, summary: 'Teacher-edited summary', notes: 'My reminder', revision: 'a'.repeat(64)});
  assert.equal(calls.filter(c => c.url === 'api/lessons/summary' && c.options.method === 'POST').length, 0);
});

test('reopening Notes reuses its saved text and does not call a model', async () => {
  const {calls, panel} = boot(); await open(panel); await open(panel);
  assert.equal(calls.length, 1); assert.equal(calls[0].options.method, undefined);
  assert.equal(walk(panel).filter(n => n.tagName === 'button' && /Summariz/.test(n.textContent)).length, 0);
});

test('unsaved notes retain their draft and pause page refresh until the revision is saved', async () => {
  const {calls, panel, ui} = boot((url, body) => ({...SAVED, ...body, revision: 'c'.repeat(64)}));
  await open(panel);
  const own = walk(panel).filter(n => n.tagName === 'textarea')[1];
  own.value = 'New reminder'; own.fire('input'); assert.equal(ui.lessonNotesUnsaved(), true);
  assert.equal(calls.filter(c => c.options.method === 'POST').length, 0);
  find(panel, 'button', 'Save notes').fire('click'); await tick(); await tick();
  assert.equal(calls.find(c => c.options.method === 'POST').body.revision, 'a'.repeat(64));
  assert.equal(ui.lessonNotesUnsaved(), false);
});
