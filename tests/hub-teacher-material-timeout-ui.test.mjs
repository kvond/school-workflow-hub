// Teacher-account uploads and timed-out source reads on Today, on fictional
// data. The production rendering functions run in a VM with a fake DOM; no
// browser, network, Drive, IC or model call is made.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; this.disabled = false; this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const declarations = source.slice(0, source.indexOf('function issueAction('));
const nodes = element => [element, ...element.children.filter(child => child instanceof Element).flatMap(nodes)];
const buttons = element => nodes(element).filter(node => node.tagName === 'button');
const tick = () => new Promise(resolve => setImmediate(resolve));
const json = data => ({ok: true, json: async () => data});

function boot(extra = '') {
  const ids = new Map();
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  }};
  const context = vm.createContext({document, Node: Element, URL, fetch: async () => { throw new Error('unexpected request'); }});
  vm.runInContext(declarations + '\n' + extra +
    '\nglobalThis.ui = {renderSourceReviewContext, currentSourceReviewPanel, timedOutRead};', context);
  return {ui: context.ui, context, document};
}

const scope = {class_key: 'anatomy', week: 'Wk 07', form_id: 'FICTIONALFORM123'};
const context = (current = scope, notices = [], overrides = {}) => ({schema: 'hub-source-review-context/v1',
  checked_at: '2032-01-20T15:00:00Z', scope: {...current, class_name: 'A&P', lesson: 'Fictional tissues'},
  notices, limitations: [], status: 'review', ...overrides});
const summary = (reviews, overrides = {}) => ({schema: 'hub-source-review-context-summary/v1',
  checked_at: '2032-01-20T15:00:00Z', reviews, limitations: [], status: 'current', ...overrides});
const studentNotice = {affected_scope: 'student', student: {first: 'Ann', last: 'Avery', section: 'B3 A&P', verified: true},
  reason: 'The dashboard can’t open this file, but you can.',
  sources: [{file_id: 'FICTIONALSTUDENT1', label: 'Exact original', url: 'https://drive.google.com/open?id=FICTIONALSTUDENT1'}],
  slides: [], next_action: 'Open this exact original and check its availability; preserve existing grades.'};
const teacherEntry = {kind: 'teacher_material', issue_kind: 'teacher_material', affected_scope: 'lesson',
  student: {first: '', last: '', section: '', verified: false},
  reason: 'Uploaded through this Form from the teacher’s own school account. It is teacher material, not a student submission.',
  sources: [{file_id: 'FICTIONALTEACHER1', label: 'Fictional example deck', url: 'https://drive.google.com/open?id=FICTIONALTEACHER1'}],
  slides: [{file_id: 'FICTIONALTEACHER1', numbers: [], verified: false}], source_basis: {kind: 'current_selection'}};
const timeout = {reason: 'read_timeout', reads: ['turn_in_folders'], attempts: 2, retry: true};
const timedOutScope = {...scope, form_id: 'TIMEDOUTFORM123'};
const timedOut = context(timedOutScope, [], {status: 'unavailable', intake_status: 'unavailable', teacher_actionable: false,
  read_failure: timeout, limitations: ['Check timed out — retry. Google didn’t answer in time while reading the turn-in folders (2 attempts).']});

test('teacher material is listed as not student work, with no identity question or next action', () => {
  const {ui} = boot();
  const data = context(scope, [], {status: 'waiting', intake_status: 'waiting', teacher_actionable: false, teacher_material: [teacherEntry]});
  const rendered = ui.renderSourceReviewContext(data, scope);
  assert.match(rendered.textContent, /Teacher material · not student work.*Teacher’s own upload · not a student submission/);
  assert.match(rendered.textContent, /Fictional example deck/);
  assert.doesNotMatch(rendered.textContent, /Next action:|Student identity not verified|Confirm the student|Reason:|Waiting for original/);
  assert.equal(nodes(rendered).filter(node => node.className === 'attention-list').length, 0);
  for (const bad of [{...teacherEntry, next_action: 'Confirm the student’s name.'}, {...teacherEntry, kind: 'review'},
    {...teacherEntry, student: {...teacherEntry.student, last: 'Avery'}}, {...teacherEntry, sources: []},
    {...teacherEntry, affected_scope: 'student'}])
    assert.throws(() => ui.renderSourceReviewContext({...data, teacher_material: [bad]}, scope), /could not be confirmed/);
});

test('Today never turns teacher material into a teacher action; the full view still shows it', async () => {
  const {ui, context: vmContext} = boot();
  const waiting = context(scope, [], {status: 'waiting', intake_status: 'waiting', teacher_actionable: false, teacher_material: [teacherEntry]});
  vmContext.fetch = async () => json(summary([waiting]));
  const today = ui.currentSourceReviewPanel(true, {teacherActionsOnly: true}); await tick();
  assert.match(today.children[0].textContent, /1 exact lesson scopes read · 0 teacher actions/);
  assert.match(today.textContent, /No current teacher action was returned/);
  assert.doesNotMatch(today.textContent, /Teacher’s own upload|Student identity not verified|Fictional example deck/);
  const full = ui.currentSourceReviewPanel(); await tick();
  assert.match(full.textContent, /A&P · Wk 07 · Fictional tissues.*Teacher material · not student work.*Fictional example deck/);
  assert.match(full.children[0].textContent, /1 exact lesson scopes read · 0 current source notices/);
});

test('a timed-out scope is not counted as read, offers one retry and is not missing work', async () => {
  const {ui, context: vmContext} = boot();
  const requests = [];
  const actionable = context(scope, [studentNotice], {teacher_actionable: true, intake_status: 'received'});
  const retried = context(timedOutScope, [], {status: 'waiting', intake_status: 'waiting', teacher_actionable: false});
  vmContext.fetch = async url => { requests.push(url); return json(url === 'api/review-context' ? summary([actionable, timedOut]) : retried); };
  const panel = ui.currentSourceReviewPanel(true, {teacherActionsOnly: true}); await tick();
  assert.match(panel.children[0].textContent, /1 of 2 exact lesson scopes read; 1 timed out — retry below · 1 teacher action\./);
  assert.match(panel.textContent, /Check timed out — retry\. A timed-out read is not missing student work/);
  assert.doesNotMatch(panel.textContent, /Current source context is unavailable|No current teacher action|Waiting for/);
  assert.doesNotMatch(panel.children[0].textContent, /checks2 exact lesson scopes read/);
  const retry = buttons(panel);
  assert.deepEqual(retry.map(button => button.textContent), ['Retry this check']);
  retry[0].onclick(); await tick();
  assert.deepEqual(requests, ['api/review-context', 'api/review-context?class=anatomy&week=Wk%2007&form=TIMEDOUTFORM123']);
  assert.match(panel.textContent, /No current source review notices were returned for this exact lesson/);
  assert.doesNotMatch(panel.textContent, /A timed-out read is not missing student work/);
});

test('a timed-out overview or gateway timeout says so and rereads only when pressed', async () => {
  const {ui, context: vmContext} = boot();
  let calls = 0;
  const failed = summary([timedOut], {status: 'unavailable', read_failure: {...timeout, reads: ['final_bookend']},
    limitations: ['The final Agenda or roster read timed out: Google didn’t answer in time. Try again.']});
  vmContext.fetch = async () => { calls++; return json(calls === 1 ? failed : summary([context(scope, [studentNotice])])); };
  const panel = ui.currentSourceReviewPanel(); await tick();
  assert.match(panel.children[0].textContent, /Check timed out — retry; no named review is issued/);
  assert.match(panel.textContent, /A timed-out read is not missing student work; no student identity decision is requested/);
  assert.doesNotMatch(panel.textContent, /Avery, Ann/);
  assert.equal(calls, 1);
  buttons(panel)[0].onclick(); await tick();
  assert.equal(calls, 2);
  assert.match(panel.children[0].textContent, /1 exact lesson scopes read · 1 current source notice/);
  assert.match(panel.textContent, /Avery, Ann/);

  vmContext.fetch = async () => ({ok: false, status: 504, json: async () => ({error: 'Fictional gateway timeout.'})});
  const gateway = ui.currentSourceReviewPanel(); await tick();
  assert.match(gateway.children[0].textContent, /Check timed out — retry; no named review is issued/);
  assert.match(gateway.textContent, /Current source checks timed out: Fictional gateway timeout\./);
  assert.equal(buttons(gateway).length, 1);
  vmContext.fetch = async () => ({ok: false, status: 502, json: async () => ({error: 'Fictional access failure.'})});
  const other = ui.currentSourceReviewPanel(); await tick();
  assert.match(other.children[0].textContent, /Unavailable; no named review is issued/);
});

test('Next from Agenda shows a timed-out intake check without waiting, missing-work or repair claims', () => {
  const helpers = source.slice(source.indexOf('function issueAction('), source.indexOf('async function openAgendaLesson('));
  const next = source.slice(source.indexOf('function agendaIntakeStatus('), source.indexOf('async function printTodayFeedback('));
  const {context: vmContext} = boot(helpers + '\n' + next + '\nglobalThis.nextAgenda = {renderNextLessons, agendaIntakeStatus};');
  const row = {classKey: 'anatomy', className: 'A&P', week: 'Wk 07', topic: 'Fictional tissues', forms: [{form_id: 'TIMEDOUTFORM123'}],
    release: {status: 'NOT_READY', issues: ['lesson_folder_unverified'], problem: 'Audit folder needs repair.', action: 'Repair this folder.'}};
  assert.equal(vmContext.nextAgenda.agendaIntakeStatus(row, summary([timedOut])), 'timed_out');
  const panel = vmContext.nextAgenda.renderNextLessons({anatomy: {data: {rows: [row]}}}, summary([timedOut]));
  assert.match(panel.textContent, /Check timed out.*The intake check timed out\. Retry it in Current source checks; no missing work is inferred/);
  assert.doesNotMatch(panel.textContent, /Waiting for|Intake unavailable|Audit folder needs repair|Repair this folder/);
});

// ---------- Today feedback card ----------
const DAY = '2026-10-11', id = 'dailyfeedbackpacket_' + 'a'.repeat(64);
const packet = {configured: true, status: 'ready', date: DAY, source_count: 3, pages: 7, pdf_url: '/v2/api/feedback/daily/pdf?id=' + id};
const timedOutFeedback = {configured: true, date: DAY, requested_date: '2026-10-14', files: [], problem: null,
  current_sources_verified: false, source_check: {status: 'timed_out', reason: 'read_timeout', attempts: 2, retry: true},
  source_problem: 'The feedback source check timed out (2 attempts). Retry it; new, pending and printed feedback are not known until it finishes.',
  daily_packet: packet};

function bootToday(handler) {
  const today = source.slice(source.indexOf('async function printTodayFeedback('), source.indexOf('function renderLessonReadiness('));
  const requests = [], never = new Promise(() => {});
  const {ui, context: vmContext, document} = boot('function renderNextLessons() { return el("p", {text: "Deck readiness"}); }\n' + today +
    '\nglobalThis.today = {loadToday, renderTodayFeedback, cockpit};');
  vmContext.fetch = (url, options) => { requests.push(url); return handler(url, options) ?? never; };
  return {today: vmContext.today, document, requests};
}

test('a timed-out feedback source check keeps the saved PDF, claims nothing new and offers a retry', () => {
  const {today} = bootToday(() => null);
  const rendered = today.renderTodayFeedback(timedOutFeedback);
  assert.match(rendered.textContent, /New feedback for 2026-10-14 was not checked because the source check timed out/);
  assert.match(rendered.textContent, /Feedback source check timed out — retry\. This saved dated document remains available/);
  assert.doesNotMatch(rendered.textContent, /No new feedback|No print-ready feedback|could not be fully checked|Printed =|printed successfully/);
  assert.deepEqual(buttons(rendered).map(button => button.textContent), ['Retry feedback check', 'Reprint saved feedback — 2026-10-11']);
  const unsaved = today.renderTodayFeedback({...timedOutFeedback, date: '2026-10-14',
    daily_packet: {configured: true, status: 'not_saved', date: '2026-10-14'}});
  assert.match(unsaved.textContent, /Feedback source check timed out — retry\. The feedback source check timed out \(2 attempts\)/);
  assert.doesNotMatch(unsaved.textContent, /No print-ready feedback documents were found/);
  assert.deepEqual(buttons(unsaved).map(button => button.textContent), ['Retry feedback check']);
});

test('a still-running feedback check is shown as checking, never as nothing new', () => {
  const {today} = bootToday(() => null);
  const empty = today.renderTodayFeedback({date: '2026-10-14', files: [], checking: true,
    daily_packet: {configured: true, status: 'not_saved', date: '2026-10-14'}});
  assert.match(empty.textContent, /Checking current feedback sources…/);
  assert.doesNotMatch(empty.textContent, /No print-ready feedback documents were found/);
  const saved = today.renderTodayFeedback({date: DAY, files: [], checking: true, daily_packet: packet});
  assert.match(saved.textContent, /Checking current feedback for \d{4}-\d{2}-\d{2}… Earlier dated feedback remains available/);
  assert.doesNotMatch(saved.textContent, /No new feedback/);
});

test('a gateway timeout on the feedback read becomes a retry that rereads only the feedback sources', async () => {
  let reads = 0;
  const {today, document, requests} = bootToday(url => {
    if (url === 'api/feedback/daily') return Promise.resolve(json(packet));
    if (url === 'api/feedback/today') return Promise.resolve(reads++ === 0
      ? {ok: false, status: 504, json: async () => ({error: 'Fictional gateway timeout.'})}
      : json({configured: true, date: DAY, requested_date: '2026-10-14', current_sources_verified: true, files: [], daily_packet: packet}));
    return null;
  });
  today.loadToday(); await tick(); await tick();
  const card = document.getElementById('today-feedback');
  assert.match(card.textContent, /Feedback source check timed out — retry\. This saved dated document remains available: Fictional gateway timeout\./);
  assert.match(card.textContent, /New feedback for \d{4}-\d{2}-\d{2} was not checked because the source check timed out/);
  const retry = buttons(card).find(button => button.textContent === 'Retry feedback check');
  const before = requests.length;
  retry.onclick(); await tick(); await tick();
  assert.equal(retry.disabled, true);
  assert.deepEqual(requests.slice(before), ['api/feedback/daily', 'api/feedback/today']);
  assert.match(card.textContent, /No new feedback for 2026-10-14/);
  assert.doesNotMatch(card.textContent, /timed out/);
});

test('Student work lists teacher uploads as teacher material with their links', () => {
  const list = source.slice(source.indexOf('function fileList('), source.indexOf('// ---------- Completion scores'));
  const material = source.slice(source.indexOf('function teacherMaterialList('), source.indexOf('function renderForm('));
  const {context: vmContext} = boot(list + '\n' + material + '\nglobalThis.work = {teacherMaterialList};');
  const rendered = vmContext.work.teacherMaterialList([{submitted_at: '2026-10-13T15:38:00Z', to: '',
    files: [{id: 'FICTIONALTEACHER1', name: 'Fictional example deck', url: 'https://drive.google.com/open?id=FICTIONALTEACHER1'}]}]);
  assert.match(rendered.textContent, /Teacher material — not student work \(1\)/);
  assert.match(rendered.textContent, /not counted as a student submission, a missing student or an unmatched student.*originals stay where they are/);
  assert.ok(nodes(rendered).some(node => node.tagName === 'a' && node.href === 'https://drive.google.com/open?id=FICTIONALTEACHER1'));
  assert.doesNotMatch(rendered.textContent, /Not on roster|not matched to the roster/);
});

for (const failure of [{checking: true}, {source_problem: 'Permission read unavailable'},
  {source_problem: 'Timeout', source_check: {status: 'timed_out'}}]) {
  test('current-source availability precedes an old Printed packet: ' + JSON.stringify(failure), () => {
    const {today} = bootToday(() => null);
    const delivery = {status: 'verified', pending_file_ids: [], handed_file_ids: ['old']};
    const rendered = today.renderTodayFeedback({date: DAY, requested_date: '2026-10-15', files: [],
      daily_packet: {...packet, delivery}, ...failure});
    assert.doesNotMatch(rendered.textContent, /No pending printing|No new feedback|No print-ready/);
    assert.match(rendered.textContent, /Checking current feedback|could not be checked|source check timed out/);
    assert.match(rendered.textContent, /Open saved daily feedback/);
  });
}
