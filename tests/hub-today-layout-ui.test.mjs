// Synthetic layout/navigation checks use the production renderers and no network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const renderers = source.slice(0, source.indexOf('async function printTodayFeedback('));
const today = source.slice(source.indexOf('function recordURL('), source.indexOf('// ---------- Lesson plans ----------'));
const navigation = source.slice(source.indexOf('function showTab()'), source.indexOf('\nfillSelect('));
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this._text = ''; this.listeners = {};
    this.classList = {toggle() {}};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}

const lesson = title => ({week: 'Wk 09', topic: title, row: 12, forms: [],
  release: {status: 'READY', issues: [], source_resolution: {decks: [], folders: []}}});

function boot(overrides = () => undefined) {
  const nodes = new Map(), requests = [], summaries = [], planReads = [], toolsReads = [];
  const document = {createElement: tag => new Element(tag), querySelectorAll: () => [],
    getElementById: id => {
      if (!nodes.has(id)) nodes.set(id, new Element('div'));
      return nodes.get(id);
    }};
  const context = vm.createContext({document, Node: Element, location: {hash: '#today'}, URL,
    plans: {date: '2032-01-07'}, work: {seq: 0},
    fetch: async (url, options) => {
      requests.push({url, options});
      const custom = overrides(url);
      const data = custom !== undefined ? await custom : url.startsWith('api/day?')
        ? {week: {label: 'Wk 09'}} : url === 'api/morning/reports' ? {reports: []}
          : url.startsWith('api/audit?') ? {rows: url.includes('class=biology') ? [lesson('Fictional ecosystems')] : [], flags: []} : {};
      return {ok: true, json: async () => data};
    },
    rememberSummary: fn => summaries.push(fn),
    loadTodayFeedback() {}, loadWeekAhead() {}, renderTodayFeedbackArea() {},
    refreshVisiblePlans() { planReads.push('navigation'); },
    loadPlans(date, fresh) { planReads.push({date, fresh}); },
    openWork() {}, loadRoutine() { toolsReads.push('routine'); }, loadSystemNotes() { toolsReads.push('notes'); },
    loadLessonAccess(fresh = false) { toolsReads.push(fresh ? 'fresh-feedback' : 'feedback'); }, loadGrowthLink() {}, loadStudentRecords() {},
  });
  const printable = source.slice(source.indexOf('function loadPrintableFeedbackLink('), source.indexOf('function renderFeedbackTools('));
  vm.runInContext(renderers + printable + `
    function renderMorningCockpit() { return el('p', {text: 'Existing morning receipt'}); }
    function renderNeedsYou() { return el('p', {text: 'Existing teacher actions'}); }
    function currentSourceReviewPanel(active, options) {
      rememberSummary(options.onSummary);
      return cockpitDetails('Current source checks');
    }
  ` + today + navigation + '\nglobalThis.ui = {loadToday, showTab, cockpit};', context);
  const reload = source.split('\n').find(line => line.startsWith("$('reload-plans').addEventListener("));
  vm.runInContext(reload, context);
  return {ui: context.ui, context, document, requests, summaries, planReads, toolsReads};
}

test('Today shows recorded work and removes readiness and orchestration panels', () => {
  const todayHTML = html.slice(html.indexOf('<section id="page-today"'), html.indexOf('<section id="page-plans"'));
  assert.match(todayHTML, /id="today-body"/);
  assert.doesNotMatch(todayHTML, /lesson-readiness|today-week-ahead|today-next-agenda|feedback|packet|checklist/);
  assert.match(html, /data-tab="plans">Lessons/);
});

test('Today reads only the saved run projection, including Refresh', async () => {
  const value = {schema_version: 'hub-dashboard-records/v1', runs: {status: 'available', items: [{id: 'one', work: 'Fictional grading', status: 'Partial', summary: 'One class finished.', remaining: 2}]}};
  const app = boot(url => url === 'api/dashboard/records' ? value : undefined);
  await app.ui.loadToday(); await app.ui.loadToday(true);
  assert.deepEqual(app.requests.map(({url}) => url), ['api/dashboard/records', 'api/dashboard/records']);
  assert.match(app.document.getElementById('today-body').textContent, /What ran.*Fictional grading.*Partial.*One class finished.*Unfinished: 2/);
  assert.ok(app.requests.every(({options}) => !options.method));
});

test('a failed refresh clears older completion claims and permits a later recovery', async () => {
  let fail = false;
  const app = boot(url => url === 'api/dashboard/records' ? fail ? Promise.reject(new Error('Fictional unavailable'))
    : {schema_version: 'hub-dashboard-records/v1', runs: {status: 'available', items: [{id: 'one', work: 'Fictional filing', status: 'Completed', summary: 'Saved files verified.'}]}} : undefined);
  await app.ui.loadToday(); fail = true; await app.ui.loadToday();
  assert.match(app.document.getElementById('today-body').textContent, /unavailable.*No completed run is confirmed/);
  assert.doesNotMatch(app.document.getElementById('today-body').textContent, /Saved files verified/);
  fail = false; await app.ui.loadToday(); assert.match(app.document.getElementById('today-body').textContent, /Saved files verified/);
});

test('a late older run read cannot replace the newer saved results', async () => {
  let releaseOld, reads = 0;
  const old = new Promise(resolve => { releaseOld = resolve; });
  const result = summary => ({schema_version: 'hub-dashboard-records/v1', runs: {status: 'available', items: [{id: summary, work: summary, status: 'Partial', summary}]}});
  const app = boot(url => url === 'api/dashboard/records' ? reads++ ? result('New result') : old : undefined);
  const pending = app.ui.loadToday(); await tick(); await app.ui.loadToday(); releaseOld(result('Old result')); await pending;
  assert.match(app.document.getElementById('today-body').textContent, /New result/);
  assert.doesNotMatch(app.document.getElementById('today-body').textContent, /Old result/);
});

test('Lessons navigation and Reload never start hidden feedback or readiness reads', async () => {
  const app = boot(); app.context.location.hash = '#plans'; app.ui.showTab();
  app.document.getElementById('reload-plans').listeners.click();
  assert.deepEqual(app.planReads, ['navigation', {date: '2032-01-07', fresh: true}]);
  assert.deepEqual(app.toolsReads, []); assert.equal(app.requests.length, 0);
});
