import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const page = name => html.split(`<section id="page-${name}"`)[1]?.split('<section id="page-')[0];

test('teaching pages keep checks at the top, growth at the bottom and records in Data', () => {
  assert.match(html, /href="#data" data-tab="data">Data/);
  assert.doesNotMatch(page('today'), /growth|DTGSS|runtime|Hub tools|Morning score check|today-feedback|today-body/);
  assert.doesNotMatch(html, /provider-status|ai-runtime|routine-body|hub-tools/);
  assert.ok(page('plans').indexOf('id="today-feedback"') < page('plans').indexOf('id="week-label"'));
  assert.ok(page('plans').indexOf('id="link-check"') < page('plans').indexOf('id="week-label"'));
  assert.match(page('plans'), /<details class="routine" id="saved-feedback">/);
  assert.match(page('plans'), /<details class="routine" id="all-student-folders">/);
  assert.match(page('plans'), /id="week-packet"/);
  assert.ok(page('work').indexOf('id="student-growth"') > page('work').indexOf('id="work-body"'));
  for (const id of ['student-records', 'system-notes', 'morning-summary', 'agenda-diagnostics', 'today-feedback-tools', 'today-body']) {
    assert.match(page('data'), new RegExp(`id="${id}"`));
    assert.doesNotMatch(page('today'), new RegExp(`id="${id}"`));
    assert.doesNotMatch(page('work'), new RegExp(`id="${id}"`));
  }
});

test('navigation selects Data for old operational bookmarks and starts only the intended readers', () => {
  const nodes = new Map(), calls = [], tabs = ['today', 'plans', 'work', 'data'].map(tab => ({dataset: {tab},
    setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; }}));
  const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); }, querySelectorAll() { return tabs; }};
  const context = vm.createContext({document, location: {hash: '#today'},
    loadToday: () => calls.push('today'), loadRoutine: () => calls.push('receipts'), loadSystemNotes: () => calls.push('notes'),
    loadStudentRecords: () => calls.push('records'), loadLessonAccess: () => calls.push('feedback'), refreshVisiblePlans: () => calls.push('plans'),
    openWork: parts => calls.push(['work', ...parts]), loadGrowthLink: () => calls.push('growth')});
  vm.runInContext('const $ = id => document.getElementById(id);\n' + source.slice(source.indexOf('function showTab()'), source.indexOf('\nfillSelect(')), context);
  for (const hash of ['#data', '#routine', '#system']) {
    calls.length = 0; context.location.hash = hash; context.showTab();
    assert.deepEqual(calls, ['today', 'receipts', 'notes', 'records']);
    assert.equal(nodes.get('page-data').hidden, false);
    assert.equal(nodes.get('page-today').hidden, true);
    assert.equal(tabs.find(tab => tab.dataset.tab === 'data')['aria-current'], 'page');
  }
  calls.length = 0; context.location.hash = '#plans'; context.showTab();
  assert.deepEqual(calls, ['feedback', 'plans']);
  calls.length = 0; context.location.hash = '#work/biology/Wk%2003'; context.showTab();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [['work', 'biology', 'Wk 03'], 'growth']);
  calls.length = 0; context.location.hash = '#today'; context.showTab();
  assert.deepEqual(calls, ['today']);
});
