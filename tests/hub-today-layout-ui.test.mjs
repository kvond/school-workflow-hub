// Synthetic layout/navigation checks use the production renderers and no network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const renderers = source.slice(0, source.indexOf('async function printTodayFeedback('));
const today = source.slice(source.indexOf('async function loadToday('), source.indexOf('// ---------- Lesson plans ----------'));
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
    plans: {date: '2032-01-07'},
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
  vm.runInContext(renderers + `
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

test('Today owns the single collapsed readiness block and Week ahead precedes Next from Agenda', () => {
  const todayHTML = html.slice(html.indexOf('<section id="page-today"'), html.indexOf('<section id="page-plans"'));
  const plansHTML = html.slice(html.indexOf('<section id="page-plans"'), html.indexOf('<section id="page-work"'));
  assert.equal((html.match(/id="lesson-readiness"/g) || []).length, 1);
  assert.match(todayHTML, /<details class="readiness-strip" id="lesson-readiness">/);
  assert.match(todayHTML, /<summary>VT readiness and feedback-code checks<\/summary>/);
  assert.doesNotMatch(plansHTML, /lesson-readiness/);
  assert.ok(todayHTML.indexOf('id="today-week-ahead"') < todayHTML.indexOf('id="today-next-agenda"'));
  assert.match(plansHTML, /id="next-week-notes" aria-label="Next week notes"/);
  assert.doesNotMatch(plansHTML, /id="system-notes"/);
  assert.doesNotMatch(todayHTML, /id="hub-tools"|id="today-body"/);
  assert.match(html, /data-tab="plans">Lessons/);
});

test('Today shares one audit result between readiness and the separate Agenda panel, including Refresh', async () => {
  const app = boot();
  await app.ui.loadToday();
  const readiness = app.document.getElementById('lesson-readiness-body');
  const agenda = app.document.getElementById('today-next-agenda');
  assert.match(readiness.textContent, /Biology · Wk 09.*Ready/);
  assert.match(agenda.textContent, /Next from Agenda.*Teacher-action check pending/);
  assert.doesNotMatch(app.document.getElementById('today-body').textContent, /Next from Agenda/);
  assert.equal(app.requests.filter(({url}) => url.startsWith('api/audit?')).length, 3);
  assert.equal(app.summaries.length, 0);
  assert.equal(app.requests.filter(({url}) => url === 'api/review-context').length, 0);
  assert.doesNotMatch(app.document.getElementById('today-body').textContent, /Current source checks/);
  await app.ui.loadToday(true);
  assert.equal(agenda.children.length, 1);
  assert.deepEqual(app.requests.filter(({url}) => url.startsWith('api/audit?') && url.includes('fresh=1')).map(({url}) => url), [
    'api/audit?class=biology&fresh=1', 'api/audit?class=forensics&fresh=1', 'api/audit?class=anatomy&fresh=1']);
  assert.ok(app.requests.every(({options}) => !options.method || options.method === 'GET'));
});

test('a failed Today refresh removes the earlier Agenda and readiness result, then allows recovery', async () => {
  let fail = false;
  const app = boot(url => url.startsWith('api/day?') && fail ? Promise.reject(new Error('Fictional Agenda unavailable')) : undefined);
  await app.ui.loadToday();
  fail = true;
  await app.ui.loadToday(true);
  assert.equal(app.document.getElementById('today-next-agenda').children.length, 0);
  assert.match(app.document.getElementById('lesson-readiness-body').textContent, /could not be checked.*Fictional Agenda unavailable/);
  assert.doesNotMatch(app.document.getElementById('lesson-readiness-body').textContent, /Ready/);
  fail = false;
  await app.ui.loadToday(true);
  assert.match(app.document.getElementById('lesson-readiness-body').textContent, /Fictional ecosystems/);
});

test('a delayed earlier load cannot replace the newer readiness or Next from Agenda', async () => {
  let releaseOld, audits = 0;
  const slowDay = new Promise(resolve => { releaseOld = resolve; });
  let dayReads = 0;
  const app = boot(url => {
    if (url.startsWith('api/day?') && !dayReads++) return slowDay;
    if (url.startsWith('api/audit?class=biology')) return {rows: [lesson(++audits === 1 ? 'Old fictional lesson' : 'New fictional lesson')], flags: []};
  });
  const old = app.ui.loadToday();
  await tick();
  await app.ui.loadToday(true);
  releaseOld({week: {label: 'Wk 09'}});
  await old;
  assert.match(app.document.getElementById('agenda-diagnostics').textContent, /New fictional lesson/);
  assert.doesNotMatch(app.document.getElementById('today-next-agenda').textContent, /Old fictional lesson/);
  assert.match(app.document.getElementById('lesson-readiness-body').textContent, /New fictional lesson/);
});

test('Lessons navigation and Reload read plans without starting a hidden readiness request', async () => {
  const app = boot();
  app.context.location.hash = '#plans';
  app.ui.showTab();
  app.document.getElementById('reload-plans').listeners.click();
  assert.deepEqual(app.planReads, ['navigation', {date: '2032-01-07', fresh: true}]);
  assert.equal(app.requests.length, 0);
  assert.deepEqual(app.toolsReads, ['feedback', 'fresh-feedback']);
  assert.equal(app.document.getElementById('page-today').hidden, true);
  app.context.location.hash = '#today';
  app.ui.showTab();
  await tick();
  assert.equal(app.document.getElementById('page-today').hidden, false);
  assert.deepEqual(app.toolsReads, ['feedback', 'fresh-feedback']);
  assert.equal(app.document.getElementById('page-plans').hidden, true);
  assert.match(app.document.getElementById('lesson-readiness-body').textContent, /Biology · Wk 09/);
});

test('verified source-role exemption is neutral and never shown as Ready or a coding repair', () => {
  const app = boot();
  vm.runInContext(`globalThis.roleView = {
    badge: releaseBadge({status: 'NOT_REQUIRED', label: 'Paper activity — VT codes not required'}),
    notice: lessonReviewNotice({release: {status: 'NOT_REQUIRED', label: 'Paper activity — VT codes not required',
      action: 'Keep existing links.', issues: []}, decks: [], forms: []})
  };`, app.context);
  assert.equal(app.context.roleView.badge.className, 'release-badge neutral');
  assert.match(app.context.roleView.badge.textContent, /Paper activity.*not required/);
  assert.doesNotMatch(app.context.roleView.badge.textContent, /Ready/);
  assert.match(app.context.roleView.notice.reason, /Paper activity/);
  assert.equal(app.context.roleView.notice.next_action, 'Keep existing links.');
});
