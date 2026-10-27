import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const refresh = source.slice(source.indexOf('async function refreshVisiblePlans()'), source.indexOf('function renderDays('));
const navigation = source.slice(source.indexOf('function showTab()'), source.indexOf('\nfillSelect('));
const listeners = source.split('\n').filter(line =>
  line.startsWith("window.addEventListener('focus',") || line.startsWith("document.addEventListener('visibilitychange',")).join('\n');
const tick = () => new Promise(resolve => setImmediate(resolve));

function boot(reader = async () => ({topic: 'Updated lesson'})) {
  const requests = [], handlers = {}, pages = new Map();
  const plans = {date: '2032-01-07', data: {topic: 'Old lesson'}, editing: null,
    refreshing: false, weeks: {biology: [{topic: 'Old lesson'}]}};
  const document = {visibilityState: 'visible', querySelectorAll: () => [],
    addEventListener: (name, handler) => { handlers[name] = handler; }};
  const location = {hash: '#plans'};
  const context = vm.createContext({plans, document, location, work: {seq: 0},
    dashboard: {todaySeq: 0, recordsSeq: 0},
    window: {addEventListener: (name, handler) => { handlers[name] = handler; }},
    $: id => { if (!pages.has(id)) pages.set(id, {}); return pages.get(id); },
    loadLessonReadiness() {}, loadToday() {}, openWork() {}, loadRoutine() {}, loadSystemNotes() {}, loadLessonAccess() {},
    loadPlans: async (date, fresh) => { requests.push({date, fresh}); plans.data = await reader(); }});
  vm.runInContext(refresh + navigation + listeners + '\nglobalThis.ui = {refreshVisiblePlans, showTab};', context);
  return {plans, document, location, requests, handlers, ui: context.ui};
}

test('returning to Lessons replaces an old plan with a fresh Agenda read', async () => {
  let topic = 'Original lesson';
  const app = boot(async () => ({topic}));
  app.ui.showTab(); await tick();
  assert.equal(app.plans.data.topic, 'Original lesson');
  app.location.hash = '#today'; app.ui.showTab();
  topic = 'Changed in the Agenda';
  app.location.hash = '#plans'; app.ui.showTab(); await tick();
  assert.equal(app.plans.data.topic, topic);
  assert.deepEqual(app.requests, [{date: '2032-01-07', fresh: true}, {date: '2032-01-07', fresh: true}]);
  assert.equal(Object.keys(app.plans.weeks).length, 0);
});

test('returning from another browser tab refreshes only visible Lessons', async () => {
  const app = boot();
  app.document.visibilityState = 'hidden';
  await app.handlers.focus(); await app.handlers.visibilitychange();
  assert.equal(app.requests.length, 0);
  app.document.visibilityState = 'visible';
  await app.handlers.visibilitychange();
  assert.equal(app.plans.data.topic, 'Updated lesson');
  app.location.hash = '#work'; await app.handlers.focus();
  assert.equal(app.requests.length, 1);
});

test('automatic refresh preserves an active editor and its original save basis', async () => {
  const app = boot();
  const edit = {base: {topic: 'Original lesson'}, draft: {topic: 'Teacher draft'}, busy: false};
  app.plans.editing = edit;
  app.ui.showTab(); await app.handlers.focus(); await app.handlers.visibilitychange();
  assert.equal(app.requests.length, 0);
  assert.equal(app.plans.editing, edit);
  assert.deepEqual(edit, {base: {topic: 'Original lesson'}, draft: {topic: 'Teacher draft'}, busy: false});
  app.plans.editing = null;
  await app.handlers.focus();
  assert.equal(app.plans.data.topic, 'Updated lesson');
});

test('overlapping focus and visibility events share one pending read', async () => {
  let answer;
  const app = boot(() => new Promise(resolve => { answer = resolve; }));
  const pending = app.handlers.focus();
  await app.handlers.visibilitychange();
  assert.equal(app.requests.length, 1);
  answer({topic: 'Latest lesson'}); await pending;
  assert.equal(app.plans.data.topic, 'Latest lesson');
  assert.equal(app.plans.refreshing, false);
});

test('a failed automatic read does not prevent a later retry', async () => {
  let fail = true;
  const app = boot(async () => { if (fail) throw new Error('Unavailable'); return {topic: 'Recovered'}; });
  await assert.rejects(app.ui.refreshVisiblePlans(), /Unavailable/);
  assert.equal(app.plans.refreshing, false);
  fail = false; await app.ui.refreshVisiblePlans();
  assert.equal(app.plans.data.topic, 'Recovered');
});

test('explicit readiness refresh bypasses both browser and server Agenda caches', async () => {
  const urls = [], cockpit = {audits: {old: true}, auditsAt: Date.now()};
  const context = vm.createContext({cockpit, CLASSES: [['biology', 'Biology'], ['forensics', 'Forensics']],
    getJSON: async url => { urls.push(url); return {rows: []}; }});
  const audits = source.slice(source.indexOf('async function loadAudits('), source.indexOf('function releaseFor('));
  vm.runInContext(audits + '\nglobalThis.readAudits = loadAudits;', context);
  assert.equal((await context.readAudits()).old, true);
  assert.equal(urls.length, 0);
  await context.readAudits(true);
  assert.deepEqual(urls, ['api/audit?class=biology&fresh=1', 'api/audit?class=forensics&fresh=1']);
});
