// Synthetic receipt/DOM checks exercise the production rendering functions.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
// The selected production declarations have no startup/navigation side effects.
const declarations = source.slice(0, source.indexOf('function issueAction('));
const summary = source.slice(source.indexOf('async function loadMorningSummary()'), source.lastIndexOf('\nloadMorningSummary();'));
const baseReasons = {flag: 0, exempt: 0, excused: 0, check: 0, name: 0, missing: 0};
const row = overrides => ({assignment: 'Fictional cell structures', class_name: 'Biology', week: 'Wk 04',
  code: 'read_back', filled: 1, raised: 2, kept: 3, left: 0, reasons: {...baseReasons}, ...overrides});
const report = items => ({day: '2032-01-07', code: 'finished', items});

function boot(items) {
  const ids = new Map();
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  }};
  const context = vm.createContext({document, Node: Element, fetch: async () => ({ok: true, json: async () => ({reports: [report(items)]})})});
  vm.runInContext(declarations + '\nlet morningReports = [], morningRecovery = []; function renderMorningAlert() {}\n' + summary +
    '\nglobalThis.ui = {morningRowReview, morningTotals, renderNeedsYou, renderMorningCockpit, loadMorningSummary};', context);
  return {ui: context.ui, document, context};
}
const allNodes = element => [element, ...element.children.filter(child => child instanceof Element).flatMap(allNodes)];
const attention = element => allNodes(element).filter(node => node.className === 'attention-list');

test('Routine explains dated student filing and separate teacher reports without adding controls', () => {
  const {context, document} = boot([]);
  const routine = source.slice(source.indexOf('function renderRoutine()'), source.indexOf('// ---------- Link check ----------'));
  vm.runInContext('function helperLine() { return el("p", {text: "Existing helper controls"}); }\n' + routine + '\nrenderRoutine();', context);
  const panel = document.getElementById('routine-body');
  assert.match(panel.textContent, /At 6:30\..*dated links in private folders by class, section and student/);
  assert.match(panel.textContent, /Original files and Form upload folders are preserved/);
  assert.match(panel.textContent, /At 7:30\..*Student feedback goes in those same student folders/);
  assert.match(panel.textContent, /Teacher summaries, growth reports and notes go in separate private teacher report folders for each class/);
  assert.match(panel.textContent, /fills blanks and raises lower scores only.*saves, reloads and verifies/);
  assert.equal(allNodes(panel).filter(node => ['button', 'input', 'select'].includes(node.tagName)).length, 0);
  assert.match(panel.textContent, /Existing helper controls/);
});

test('legacy receipt flags remain unavailable detail and cannot become teacher tasks', async () => {
  const legacy = row({class_name: undefined, week: undefined, left: 4,
    reasons: {flag: 4, excused: 0, check: 0, name: 0, missing: 6}});
  const {ui, document} = boot([legacy]);
  const panel = ui.renderNeedsYou(report([legacy]));
  assert.equal(attention(panel).length, 0);
  assert.match(panel.textContent, /older receipt.*Detailed follow-up is unavailable/);
  assert.doesNotMatch(panel.textContent, /status flag other than Exempt|Hub-listed|4 —/);
  assert.equal(ui.morningRowReview(legacy).left, null);
  assert.equal(ui.morningTotals(report([legacy])).leftKnown, false);
  await ui.loadMorningSummary();
  const routineText = document.getElementById('morning-summary').textContent;
  assert.match(routineText, /Detailed follow-up is unavailable/);
  assert.doesNotMatch(routineText, /status flag other than Exempt|4 unchanged|Hub-listed/);
});

test('Exempt and Hub-only students do not create attention rows', () => {
  const exempt = row({left: 4, reasons: {...baseReasons, exempt: 4, missing: 6}});
  const {ui} = boot([exempt]);
  const panel = ui.renderNeedsYou(report([exempt]));
  assert.equal(attention(panel).length, 0);
  assert.doesNotMatch(panel.textContent, /4 —|6 —|not present|missing work/);
  assert.equal(ui.morningTotals(report([exempt])).left, 0);
  assert.equal(Object.hasOwn(ui.morningTotals(report([exempt])).reasons, 'missing'), false);
});

test('known unchanged counts exclude Exempt and actionable reasons retain class and lesson', async () => {
  const current = row({left: 3, reasons: {...baseReasons, exempt: 1, flag: 1, check: 1}});
  const {ui, document} = boot([current]);
  assert.equal(ui.morningRowReview(current).left, 2);
  assert.equal(ui.morningTotals(report([current])).left, 2);
  const text = ui.renderNeedsYou(report([current])).textContent;
  assert.match(text, /Biology · Wk 04 · Fictional cell structures/);
  assert.match(text, /status flag other than Exempt/);
  assert.match(text, /could not confirm a score.*Check by hand/);
  assert.match(ui.renderMorningCockpit(report([current]), {}).textContent, /Biology · Wk 04 · Fictional cell structures/);
  await ui.loadMorningSummary();
  assert.match(document.getElementById('morning-summary').textContent, /2 unchanged/);
});

test('flags counted on already handled scores cannot create attention when no rows were left', () => {
  const handled = row({left: 0, reasons: {...baseReasons, flag: 4}});
  const {ui} = boot([handled]);
  assert.equal(attention(ui.renderNeedsYou(report([handled]))).length, 0);
  assert.equal(ui.morningTotals(report([handled])).reasons.flag, 0);
});

test('an unknown or incompatible Exempt count cannot relabel legacy flags or adjust left', () => {
  for (const reasons of [{flag: 4, excused: 0, check: 0, name: 0, missing: 0}, {...baseReasons, exempt: 5, flag: 4}]) {
    const unknown = row({left: 4, reasons});
    const {ui} = boot([unknown]);
    assert.equal(ui.morningRowReview(unknown).left, null);
    assert.equal(attention(ui.renderNeedsYou(report([unknown]))).length, 0);
    assert.doesNotMatch(ui.renderNeedsYou(report([unknown])).textContent, /status flag other than Exempt/);
  }
});

test('coverage failures explain possible source access or matching repairs without inventing a naming cause', async () => {
  const held = row({code: 'coverage_incomplete'});
  const {ui, document} = boot([held]);
  for (const panel of [ui.renderNeedsYou(report([held])), ui.renderMorningCockpit(report([held]), {})]) {
    assert.match(panel.textContent, /Biology · Wk 04 · Fictional cell structures/);
    assert.match(panel.textContent, /Source coverage was incomplete/);
    assert.match(panel.textContent, /Form or roster access/);
    assert.doesNotMatch(panel.textContent, /because a submission still needs a confirmed lesson match/);
  }
  await ui.loadMorningSummary();
  assert.match(document.getElementById('morning-summary').textContent, /Form or roster access/);
});

test('unresolved submission matches prevent score requests and IC handoff of cached scores', async () => {
  const {context} = boot([]), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
  context.window = {postMessage: message => messages.push(message)};
  context.crypto = {randomUUID: () => 'fictional-request'};
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction renderWork() {}\nglobalThis.client = {work, scoreForm, helperRows, sendList, weekToEnter, sendWeek, icPanel, icStatus};', context);
  const {client} = context;
  const form = {form_id: 'FICTIONALFORM123', students: [{matched: true, last: 'Avery', first: 'Ann', section: 'A4 Biology',
    files: [{id: 'FICTIONALFILE123', mime: 'application/vnd.google-apps.presentation'}]}], missing: [], late: [], excused: [], elsewhere: []};
  const cached = {notes_score: 8, revision_score: 9};
  client.work.data = {other_files: [{files: [{id: 'UNRESOLVED123'}]}], lessons: [{forms: [form]}]};
  client.work.scores = {[form.form_id]: {FICTIONALFILE123: cached}};
  await client.scoreForm(form);
  assert.equal(requests.length, 0);
  assert.equal(client.work.scores[form.form_id].FICTIONALFILE123, cached);
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(client.weekToEnter().ready.length, 0);
  assert.equal(await client.sendList(form, {}), 'coverage_incomplete');
  const status = new Element('span');
  await client.sendWeek(status, new Element('details'));
  assert.match(status.textContent, /Scoring and IC entry are on hold/);
  assert.match(client.icPanel(form, client.work.scores[form.form_id], {}).textContent, /Scoring and IC entry are on hold/);
  assert.equal(client.icStatus(form).kind, 'held');
  assert.equal(messages.length, 0);
  client.work.data.other_files = [];
  const resumed = client.helperRows(form);
  assert.equal(resumed.length, 1);
  assert.equal(resumed[0].kind, 'score');
  assert.equal(resumed[0].notes, 8);
  assert.equal(resumed[0].revision, 9);
});

test('accepted intake is not a source-assignment zero but unresolved evidence still requires a check', () => {
  const {context} = boot([]);
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction helperProgress() { return null; }\nglobalThis.client = {work, helperRows, icPanel};', context);
  const {client} = context;
  const person = {first: 'Ann', last: 'Avery', section: 'A4 Biology'};
  const intake = {who: 'Avery, Ann', section: person.section, accepted_intake: true, to: 'Other lesson — Turn in'};
  const form = {form_id: 'FICTIONALFORM123', window: {started: true}, students: [], missing: [person], late: [], excused: [], elsewhere: [intake]};
  client.work.data = {other_files: [], lessons: [{forms: [form]}]};
  client.work.section = 'all'; client.work.scores = {[form.form_id]: {}};
  assert.equal(client.helperRows(form)[0].kind, 'none');
  assert.equal(client.helperRows(form)[0].notes, null);
  assert.match(client.icPanel(form, {}, {}).textContent, /No scores to enter yet/);
  form.missing[0] = {...person, unsorted: true};
  assert.equal(client.helperRows(form)[0].kind, 'check');
  form.missing[0] = person;
  form.elsewhere.push({...intake, accepted_intake: false});
  assert.equal(client.helperRows(form)[0].kind, 'check');
  assert.match(client.icPanel(form, {}, {}).textContent, /check by hand/);
});

test('excluded or uncertain assessment scope never scores or sends cached results or missing zeros', async () => {
  const {context} = boot([]), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
  context.window = {postMessage: message => messages.push(message)};
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction renderWork() {}\nglobalThis.client = {work, scoreForm, helperRows, sendList, weekToEnter, sendWeek, icPanel, icStatus};', context);
  const {client} = context;
  const form = {form_id: 'FICTIONALFORM123', window: {started: true}, students: [{matched: true, first: 'Ann', last: 'Avery', section: 'A4 Biology', files: [{id: 'FICTIONALFILE123'}]}],
    missing: [{first: 'Ben', last: 'Brook', section: 'A4 Biology'}], late: [], excused: [], elsewhere: []};
  client.work.scores = {[form.form_id]: {FICTIONALFILE123: {notes_score: 9, revision_score: 10}}};
  for (const policy of [{excluded: true, reason: 'This lesson was not assigned this school year.'}, {excluded: false, problem: 'scope_unconfirmed'}]) {
    client.work.data = {other_files: [], week_policy: policy, lessons: [{forms: [form]}]};
    await client.scoreForm(form);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(client.weekToEnter().ready.length, 0);
    assert.equal(await client.sendList(form, {}), 'assessment_scope_held');
    assert.equal(client.icStatus(form).kind, 'held');
    assert.doesNotMatch(client.icPanel(form, client.work.scores[form.form_id], {}).textContent, /Enter these scores|Notes \/10/);
    const status = new Element('span'); await client.sendWeek(status, new Element('details'));
    assert.match(status.textContent, /not assigned|assessment scope/);
  }
  assert.equal(requests.length, 0); assert.equal(messages.length, 0);
  client.work.data = {other_files: [], assessment_problem: 'The configured alternate route is stale.', lessons: [{forms: [form]}]};
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(await client.sendList(form, {}), 'assessment_scope_held');
  assert.match(client.icPanel(form, {}, {}).textContent, /submission route needs confirmation/);
});

test('link audit explains excluded weeks without a missing Form warning', () => {
  const {context} = boot([]);
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  const audit = source.slice(source.indexOf('const QUIET ='), source.indexOf('async function runLinkCheck()'));
  vm.runInContext(studentWork + '\n' + audit + '\nglobalThis.auditUI = {linkCheckResult, omittedFormProblem};', context);
  const policy = {excluded: true, reason: 'This lesson was not assigned this school year.'};
  const result = context.auditUI.linkCheckResult('Biology', {flags: [], exclusions: [{week: 'Wk 01', topic: 'Ecosystems', reason: policy.reason}],
    rows: [{week: 'Wk 01', topic: 'Ecosystems', forms: [], decks: [], week_policy: policy}]});
  const text = result.filter(Boolean).map(node => node.textContent).join(' ');
  assert.match(text, /Wk 01 · Ecosystems: This lesson was not assigned this school year/);
  assert.match(text, /Not assigned this school year/);
  assert.doesNotMatch(text, /no Google Form is linked|needs attention/);
  const lesson = {forms: [], problem: "This lesson has no Form link in its 'Turn it in' column."};
  assert.equal(context.auditUI.omittedFormProblem(lesson, {week_policy: policy}), true);
  assert.equal(context.auditUI.omittedFormProblem({...lesson, problem: 'The private folder is unavailable.'}, {week_policy: policy}), false);
});
