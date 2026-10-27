import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const page = name => html.split(`<section id="page-${name}"`)[1]?.split('<section id="page-')[0];

test('normal navigation exposes Lessons and retains explicit recovery page containers', () => {
  const nav = html.split('<nav class="tabs"')[1].split('</nav>')[0];
  assert.match(nav, /href="#plans" data-tab="plans">Lessons/);
  assert.equal((nav.match(/<a /g) || []).length, 1);
  for (const tab of ['today', 'work', 'records']) assert.ok(!nav.includes(`data-tab="${tab}"`));
  assert.match(html, /id="agenda-link"/);
  assert.match(page('today'), /id="today-body"/);
  assert.match(page('records'), /id="records-body"/);
  assert.match(page('work'), /id="work-display"/);
  assert.doesNotMatch(page('plans'), /id="next-week-notes"/);
  assert.match(source, /card\.append\(classNotesPanel\(cls\)\)/);
  for (const id of ['lesson-readiness', 'today-week-ahead', 'today-next-agenda', 'today-feedback', 'week-packet', 'student-records', 'system-notes', 'tidy-work'])
    assert.ok(!html.includes(`id="${id}"`), id);
});

test('old operational bookmarks route to Records and each page calls only its own loader', () => {
  const nodes = new Map(), calls = [], tabs = ['today', 'plans', 'work', 'records'].map(tab => ({dataset: {tab},
    setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; }}));
  const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); }, querySelectorAll() { return tabs; }};
  const location = {hash: '#today'};
  const context = vm.createContext({document, work: {seq: 0}, dashboard: {todaySeq: 0, recordsSeq: 0}, location,
    history: {replaceState(_, __, hash) { location.hash = hash; }}, loadToday: () => calls.push('today'),
    loadRecords: () => calls.push('records'), refreshVisiblePlans: () => calls.push('plans'), openWork: parts => calls.push(['work', ...parts])});
  vm.runInContext('const $ = id => document.getElementById(id);\n' + source.slice(source.indexOf('function showTab()'), source.indexOf('\nfillSelect(')), context);
  for (const hash of ['#records', '#data', '#routine', '#system']) {
    calls.length = 0; context.location.hash = hash; context.showTab();
    assert.deepEqual(calls, ['records']); assert.equal(nodes.get('page-records').hidden, false);
  }
  calls.length = 0; context.location.hash = '#plans'; context.showTab(); assert.deepEqual(calls, ['plans']);
  calls.length = 0; context.location.hash = '#work/biology/Wk%2003'; context.showTab();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [['work', 'biology', 'Wk 03']]);
  calls.length = 0; context.location.hash = '#today'; context.showTab(); assert.deepEqual(calls, ['today']);
  for (const hash of ['', '#unknown', '#plans', '#plans']) {
    calls.length = 0; context.location.hash = hash; context.showTab();
    assert.deepEqual(calls, ['plans']); assert.equal(context.location.hash, '#plans');
    assert.equal(nodes.get('page-plans').hidden, false);
  }
});
