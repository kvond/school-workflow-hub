// Synthetic receipt/DOM checks exercise the production rendering functions.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; this.classList = {toggle() {}}; }
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
  const context = vm.createContext({document, Node: Element, URL, fetch: async () => ({ok: true, json: async () => ({reports: [report(items)]})})});
  vm.runInContext(declarations + '\nlet morningReports = [], morningRecovery = []; function renderMorningAlert() {}\n' + summary +
    '\nglobalThis.ui = {morningRowReview, morningTotals, renderNeedsYou, renderMorningCockpit, loadMorningSummary, reviewSourceURL, renderSourceReviewNotice, renderSourceReviewContext, loadSourceReviewContext, currentSourceReviewPanel, invalidateReviewPanel, studentWorkReviewPanel, scoreReviewNotice};', context);
  return {ui: context.ui, document, context};
}
const allNodes = element => [element, ...element.children.filter(child => child instanceof Element).flatMap(allNodes)];
const attention = element => allNodes(element).filter(node => node.className === 'attention-list');

const sourceScope = {class_key: 'forensics', week: 'Wk 03', form_id: 'FICTIONALFORM123'};
const sourceNotice = overrides => ({affected_scope: 'student', student: {first: 'Ann', last: 'Avery', section: 'A2 Forensic', verified: true},
  reason: 'Slide 19: The original response needs its labeled answer box checked.',
  sources: [{file_id: 'FICTIONALFILE123', label: 'Exact received original', url: 'https://drive.google.com/open?id=FICTIONALFILE123'}],
  slides: [{file_id: 'FICTIONALFILE123', numbers: [19], verified: true}],
  next_action: 'Compare the original answer box on slide 19; preserve answers and existing grades.', ...overrides});
const sourceContext = (scope = sourceScope, notices = [sourceNotice()], overrides = {}) => ({schema: 'hub-source-review-context/v1',
  checked_at: '2032-01-20T15:00:00Z', scope: {...scope, class_name: 'Forensics', lesson: 'Observation'},
  notices, limitations: [], status: 'review', ...overrides});
const tick = () => new Promise(resolve => setImmediate(resolve));

test('source context renders full current identity/reason/action and verified source slides as literal text', () => {
  const {ui} = boot([]);
  const reason = '<script>private literal</script> ' + 'A full source-specific reason. '.repeat(15);
  const panel = ui.renderSourceReviewContext(sourceContext(sourceScope, [sourceNotice({reason})]), sourceScope);
  assert.match(panel.textContent, /Forensics · A2 Forensic · Wk 03 · Observation · Avery, Ann/);
  assert.ok(panel.textContent.includes(reason));
  assert.match(panel.textContent, /Current source context.*Read separately from the saved helper receipt/);
  assert.match(panel.textContent, /Slide 19.*Next action: Compare the original answer box/);
  const links = allNodes(panel).filter(node => node.tagName === 'a');
  assert.equal(links.length, 1); assert.equal(links[0].href, 'https://drive.google.com/open?id=FICTIONALFILE123');
  assert.equal(links[0].rel, 'noopener noreferrer');
  assert.equal(allNodes(panel).some(node => node.tagName === 'script'), false);
});

test('source context URL guard accepts only canonical exact Google resources and rejects unsafe/mismatched URLs', () => {
  const {ui} = boot([]), id = 'FICTIONALFILE123';
  for (const url of ['presentation', 'document', 'spreadsheets', 'forms'].map(kind => `https://docs.google.com/${kind}/d/${id}/edit`)
    .concat([`https://drive.google.com/open?id=${id}`, `https://drive.google.com/file/d/${id}/view`, `https://drive.google.com/drive/folders/${id}`]))
    assert.equal(ui.reviewSourceURL({file_id: id, url}), url);
  for (const url of ['javascript:alert(1)', 'data:text/html,private', `https://docs.google.com.evil.test/presentation/d/${id}/edit`,
    `https://user@docs.google.com/presentation/d/${id}/edit`, `https://docs.google.com:443/presentation/d/${id}/edit`,
    `http://docs.google.com/presentation/d/${id}/edit`, `https://docs.google.com/presentation/d/ANOTHERFILE123/edit`,
    `https://docs.google.com/presentation/d/${id}/edit#slide=id.guessed`, `https://docs.google.com/presentation/d/${id}/edit?extra=1`,
    `https://drive.google.com/open?id=${id}&id=${id}`, `https://drive.google.com/open?id=${id}&token=secret`,
    `https://drive.google.com/drive/folders/${id}?extra=1`, `https://drive.google.com/open?id=${id}\n`])
    assert.equal(ui.reviewSourceURL({file_id: id, url}), null, url);
});

test('lesson-wide checks and genuine unknown identities/slides remain distinct without invented repair requests', () => {
  const {ui} = boot([]);
  const lesson = ui.renderSourceReviewContext(sourceContext(sourceScope, [sourceNotice({affected_scope: 'lesson',
    student: {verified: false}, slides: [], reason: 'The exact lesson folder is unavailable.'})]), sourceScope);
  assert.match(lesson.textContent, /Lesson-wide source check · no individual student/);
  assert.doesNotMatch(lesson.textContent, /Student identity not verified/);
  assert.match(lesson.textContent, /Slides not verified/);
  const unknown = ui.renderSourceReviewContext(sourceContext(sourceScope, [sourceNotice({student: {verified: false},
    slides: [{file_id: 'ANOTHERFILE123', numbers: [19], verified: true}]})]), sourceScope);
  assert.match(unknown.textContent, /Student identity not verified/); assert.match(unknown.textContent, /Slides not verified/);
  assert.doesNotMatch(unknown.textContent, /· Slide 19/);
  const waiting = ui.renderSourceReviewContext(sourceContext(sourceScope, [], {status: 'waiting',
    limitations: ['Waiting for original submissions; no student-copy repair is requested.']}), sourceScope, {compactEmpty: true});
  assert.match(waiting.textContent, /Waiting for original submissions; no student-copy repair/);
  assert.doesNotMatch(waiting.textContent, /Next action:|all caught up/);
});

test('source context clears named details immediately and rejects old or mismatched scope responses', async () => {
  const {ui, context} = boot([]), body = new Element('div'), replies = [];
  context.fetch = () => new Promise(resolve => replies.push(resolve));
  body.textContent = 'Previous named student';
  const old = ui.loadSourceReviewContext(body, sourceScope);
  assert.doesNotMatch(body.textContent, /Previous named student/);
  const newer = {...sourceScope, form_id: 'SECONDARYFORM123'};
  const fresh = ui.loadSourceReviewContext(body, newer);
  replies[1]({ok: true, json: async () => sourceContext(newer, [sourceNotice({student: {first: 'Ben', last: 'Brook', section: 'A2 Forensic', verified: true}})])});
  await fresh;
  replies[0]({ok: true, json: async () => sourceContext()}); await old;
  assert.match(body.textContent, /Brook, Ben/); assert.doesNotMatch(body.textContent, /Avery, Ann/);
  context.fetch = async () => ({ok: true, json: async () => sourceContext()});
  await ui.loadSourceReviewContext(body, newer);
  assert.match(body.textContent, /could not be confirmed for this exact class and lesson/);
  assert.doesNotMatch(body.textContent, /Brook, Ben|Avery, Ann/);
});

test('failed context GET preserves saved receipt counts and writes no student data or grade request', async () => {
  const receipt = report([row({left: 1, reasons: {...baseReasons, check: 1}})]), before = JSON.stringify(receipt);
  const {ui, context} = boot([]), requests = [], writes = [], body = new Element('div');
  context.localStorage = {getItem: () => null, setItem: (...args) => writes.push(args)};
  context.fetch = async (url, options) => { requests.push({url, options}); return {ok: false, status: 503, json: async () => ({error: 'Temporary source access failure'})}; };
  await ui.loadSourceReviewContext(body, sourceScope);
  assert.match(body.textContent, /Temporary source access failure.*saved helper receipt is unchanged/);
  assert.equal(JSON.stringify(receipt), before); assert.equal(ui.morningTotals(receipt).filled, 1);
  assert.equal(requests.length, 1); assert.match(requests[0].url, /^api\/review-context\?class=forensics&week=Wk%2003&form=FICTIONALFORM123$/);
  assert.equal(requests[0].options.credentials, 'same-origin'); assert.equal(requests[0].options.cache, 'no-store');
  assert.equal(requests[0].options.method, undefined); assert.equal(requests[0].options.body, undefined);
  assert.equal(writes.length, 0);
});

test('automatic current reviews use every exact catalog ID with at most two GETs and no title/count join', async () => {
  const {ui, context} = boot([row({assignment: 'Same label', reasons: {...baseReasons, check: 99}})]);
  const entries = Array.from({length: 5}, (_, index) => ({class: 'forensics', class_name: 'Forensics', week: 'Wk 03', form: `EXACTFORMID${index}`, assignment: 'Same label'}));
  const pending = [], requests = []; let active = 0, max = 0;
  context.fetch = async url => {
    requests.push(url);
    if (url === 'api/morning/catalog') return {ok: true, json: async () => ({assignments: entries, problems: []})};
    active++; max = Math.max(max, active);
    return new Promise(resolve => pending.push(() => {
      const parsed = new URL(url, 'https://hub.example.test/');
      const scope = {...sourceScope, form_id: parsed.searchParams.get('form')}; active--;
      resolve({ok: true, json: async () => sourceContext(scope, [sourceNotice({reason: `Original for ${scope.form_id}`} )])});
    }));
  };
  const panel = ui.currentSourceReviewPanel(); await tick();
  assert.equal(pending.length, 2); assert.equal(max, 2);
  while (pending.length) { pending.shift()(); await tick(); }
  assert.equal(requests.length, 6); assert.equal(max, 2);
  for (const entry of entries) assert.ok(panel.textContent.includes(`Original for ${entry.form}`));
  assert.match(panel.textContent, /5 of 5 exact lesson scopes read/);
  assert.equal(allNodes(panel).some(node => ['select', 'input', 'button'].includes(node.tagName)), false);
  assert.ok(requests.every(url => url === 'api/morning/catalog' || url.startsWith('api/review-context?')));
});

test('refresh cancels pending named reviews and stops the remaining old catalog queue', async () => {
  const {ui, context} = boot([]), pending = [], requests = [];
  context.fetch = async url => {
    requests.push(url);
    if (url === 'api/morning/catalog') return {ok: true, json: async () => ({assignments: [0,1,2].map(i => ({class: 'forensics', week: 'Wk 03', form: `EXACTFORMID${i}`, assignment: 'Observation'}))})};
    return new Promise(resolve => pending.push(() => resolve({ok: true, json: async () => sourceContext({...sourceScope, form_id: new URL(url, 'https://hub.example.test').searchParams.get('form')})})));
  };
  const panel = ui.currentSourceReviewPanel(); await tick(); assert.equal(pending.length, 2);
  ui.invalidateReviewPanel(panel); pending.forEach(reply => reply()); await tick();
  assert.equal(requests.length, 3); assert.match(panel.textContent, /Refreshing current source context/);
  assert.doesNotMatch(panel.textContent, /Avery, Ann|Slide 19/);
});

test('automatic review hides empty unassessed groups, keeps waiting and failures scoped, and states the limitation once', async () => {
  const {ui, context} = boot([]);
  const entries = [0,1,2,3].map(index => ({class: 'forensics', class_name: 'Forensics', week: 'Wk 03', form: `EXACTFORMID${index}`, assignment: `Exact lesson ${index}`}));
  context.fetch = async url => {
    if (url === 'api/morning/catalog') return {ok: true, json: async () => ({assignments: entries})};
    const id = new URL(url, 'https://hub.example.test').searchParams.get('form');
    if (id.endsWith('3')) return {ok: false, status: 503, json: async () => ({error: 'Exact provider read failed'})};
    return {ok: true, json: async () => sourceContext({...sourceScope, form_id: id}, [], {status: id.endsWith('2') ? 'waiting' : 'not_assessed'})};
  };
  const panel = ui.currentSourceReviewPanel(); await tick();
  assert.equal(allNodes(panel).filter(node => node.tagName === 'section' && node.hidden === true).length, 2);
  assert.equal(panel.textContent.match(/Some original submissions have no current verified assessment details/g).length, 1);
  assert.match(panel.textContent, /Exact lesson 2: Waiting for original submissions; no student-copy repair/);
  assert.match(panel.textContent, /Exact lesson 3.*Exact provider read failed/);
  assert.match(panel.textContent, /4 of 4 exact lesson scopes read; 1 unavailable/);
  assert.doesNotMatch(panel.textContent, /all caught up|No grade rows need attention/);
});

test('Student work exact review reads are cached only for that refresh and stop on a changed scope', async () => {
  const {context, client, form} = recheckClient(), requests = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => sourceContext({class_key: 'biology', week: 'Wk 03', form_id: form.form_id})}; };
  const first = context.ui.studentWorkReviewPanel(form, true), second = context.ui.studentWorkReviewPanel(form, true);
  await tick(); assert.equal(requests.length, 1);
  assert.match(first.textContent, /Avery, Ann/); assert.match(second.textContent, /Avery, Ann/);
  assert.ok(requests.every(url => url.startsWith('api/review-context?class=biology&week=Wk%2003&form=')));
  let answer;
  client.work.reviewContexts = new Map();
  context.fetch = url => { requests.push(url); return new Promise(resolve => { answer = resolve; }); };
  const pending = context.ui.studentWorkReviewPanel(form, true);
  client.work.seq++; client.work.week = 'Wk 04';
  answer({ok: true, json: async () => sourceContext({class_key: 'biology', week: 'Wk 03', form_id: form.form_id})});
  await tick(); assert.doesNotMatch(pending.textContent, /Avery, Ann/);
});

test('direct scoring review notices require the caller exact Form and retain the original numeric scores', () => {
  const {context, client, form} = recheckClient();
  const score = {notes_score: null, revision_score: null, note: sourceNotice().reason,
    review_context: {schema: 'hub-source-review-context/v1', scope: sourceContext({class_key: 'biology', week: 'Wk 03', form_id: form.form_id}).scope, notice: sourceNotice()}};
  const before = JSON.stringify(score);
  const notice = context.ui.scoreReviewNotice(score, form.form_id);
  assert.match(notice.textContent, /Avery, Ann.*Slide 19/);
  assert.equal(context.ui.scoreReviewNotice(score, 'ANOTHERFORM123'), null);
  assert.equal(JSON.stringify(score), before); assert.equal(client.helperRows(form).find(row => row.first === 'Ann').kind, 'check');
});

test('readiness review links name exact sources and missing codes stay advisory outside Photosynthesis', () => {
  const {context} = boot([]);
  const helpers = source.slice(source.indexOf('function issueAction('), source.indexOf('async function openAgendaLesson('));
  vm.runInContext(helpers + '\nglobalThis.lessonReviews = {lessonReviewNotice, lessonReviewBadge};', context);
  const release = {status: 'NOT_READY', issues: ['marker_inventory_count_mismatch'],
    problem: 'Needs embedded codes.', action: 'Repair codes. Unmarked copies are held without scores.'};
  const base = {classKey: 'biology', className: 'Biology', week: 'Wk 07', topic: 'Evolution', release,
    decks: [{id: 'FICTIONALFILE123', title: 'Current linked original'}]};
  const advisory = context.lessonReviews.lessonReviewNotice(base);
  const panel = context.ui.renderSourceReviewNotice(advisory, {class_name: 'Biology', week: 'Wk 07', lesson: base.topic});
  assert.equal(context.lessonReviews.lessonReviewBadge(base).textContent, 'Code advisory');
  assert.match(panel.textContent, /Lesson-wide source check.*Missing codes alone.*do not hold grading/);
  assert.match(panel.textContent, /Slides not verified/); assert.equal(allNodes(panel).filter(node => node.tagName === 'a').length, 1);
  assert.doesNotMatch(panel.textContent, /Repair codes|unmarked copies are held/i);
  const photo = context.lessonReviews.lessonReviewNotice({...base, topic: 'Photosynthesis'});
  assert.match(photo.next_action, /Repair codes/);
  assert.equal(context.lessonReviews.lessonReviewBadge({...base, topic: 'Photosynthesis'}).textContent, 'Needs embedded codes');
  const trueHold = context.lessonReviews.lessonReviewNotice({...base, release: {...release, issues: ['duplicate_feedback_marker'], action: 'Review the duplicate marker.'}});
  assert.equal(trueHold.next_action, 'Review the duplicate marker.');
});

test('readiness labels name embedded-code repairs while keeping access failures distinct', () => {
  const {context} = boot([]);
  vm.runInContext('globalThis.readinessBadge = releaseBadge;', context);
  assert.equal(context.readinessBadge({status: 'NOT_READY', issues: ['marker_inventory_counts_unparsed']}).textContent, 'Needs embedded codes');
  assert.equal(context.readinessBadge({status: 'NOT_READY', label: 'Needs embedded codes', issues: ['teacher_distributed_uncoded_copies']}).textContent, 'Needs embedded codes');
  assert.equal(context.readinessBadge({status: 'UNKNOWN', issues: ['deck_release_unreadable']}).textContent, 'Cannot read lesson');
  assert.equal(context.readinessBadge({status: 'READY', issues: []}).textContent, 'Ready');
});

test('a pending or failed calendar read cannot show the previous date’s lessons or notes', async () => {
  const {context, document} = boot([]);
  let answer;
  context.fetch = () => new Promise(resolve => { answer = resolve; });
  const calendar = source.slice(source.indexOf('const plans ='), source.indexOf('function renderNextWeekNotes('));
  vm.runInContext(calendar + '\nglobalThis.calendarClient = {loadPlans, plans};', context);
  for (const id of ['week-label', 'week-closures', 'cards', 'next-week-notes']) document.getElementById(id).textContent = 'Previous week';
  context.calendarClient.plans.data = {old: true};
  const pending = context.calendarClient.loadPlans('2032-01-14');
  assert.match(document.getElementById('week-label').textContent, /Jan 14, 2032/);
  for (const id of ['week-closures', 'cards', 'next-week-notes']) assert.equal(document.getElementById(id).textContent, '');
  assert.equal(context.calendarClient.plans.data, null);
  answer({ok: false, status: 503, json: async () => ({error: 'Agenda access temporarily unavailable'})});
  await pending;
  assert.match(document.getElementById('plans-status').textContent, /temporarily unavailable/);
  assert.match(document.getElementById('week-label').textContent, /Jan 14, 2032/);
  assert.equal(document.getElementById('cards').textContent, '');
});

test('Routine explains dated student filing and separate teacher reports without adding controls', () => {
  const {context, document} = boot([]);
  const routine = source.slice(source.indexOf('function renderRoutine()'), source.indexOf('// ---------- Link check ----------'));
  vm.runInContext('function helperLine() { return el("p", {text: "Existing helper controls"}); }\n' + routine + '\nrenderRoutine();', context);
  const panel = document.getElementById('routine-body');
  assert.match(panel.textContent, /At 6:30\..*dated copies in private folders by class, section and student/);
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

test('mapping, source and scale holds give the named assignment’s repair instead of uncertain-save recovery', () => {
  const cases = [
    ['mapping_required', /Form, class section and Notes\/Revision column.*exact IC assignment/],
    ['source_changed', /Refresh this assignment in Student work.*current submissions and lesson-match checks.*fresh list/],
    ['unsupported_points_scale', /correct 10-point Notes\/Revision assignment.*exact mapping.*Do not rescale existing scores/],
  ];
  for (const [code, action] of cases) {
    const held = row({code, filled: 0, raised: 0, kept: 0});
    const {ui} = boot([held]);
    const text = ui.renderMorningCockpit(report([held]), {}).textContent;
    assert.match(text, /Biology · Wk 04 · Fictional cell structures/);
    assert.match(text, action);
    assert.doesNotMatch(text, /Save\/readback not confirmed|inspect the scores|correct\/save|clearing the helper verification block/);
  }
});

test('a later source hold preserves earlier confirmed results without claiming no entry occurred', () => {
  const confirmed = row({assignment: 'Fictional energy flow'});
  const held = row({code: 'source_changed', filled: 0, raised: 0, kept: 0});
  const {ui} = boot([confirmed, held]);
  const panel = ui.renderMorningCockpit(report([confirmed, held]), {});
  assert.match(panel.textContent, /Fictional energy flow: 1 new · 2 raised · 3 already matched in IC.*confirmed after reload/);
  const sourceDetail = allNodes(panel).find(node => node.tagName === 'li' && node.textContent.includes(held.assignment));
  assert.match(sourceDetail.textContent, /Entry held at the source check/);
  assert.doesNotMatch(sourceDetail.textContent, /nothing was typed|no IC entry|no scores were entered|clearing the helper verification block/);
});

test('pre-entry roster, panel and list checks do not direct the teacher to clear a verification block', () => {
  const cases = [
    ['students_dont_match', /correct class section and assignment in IC/],
    ['several_sections', /one class section/],
    ['destination_mapping_changed', /class section, assignment and Notes\/Revision column/],
    ['unsupported_gradebook', /mapped assignment in the IC score-entry panel/],
    ['list_expired', /prepare a new list/],
    ['ic_changed', /Review this assignment again against a fresh Hub list/],
    ['signed_out', /Sign in to IC in the school Chrome profile/],
    ['ic_unavailable', /one signed-in IC tab/],
    ['hub_unavailable', /one signed-in Hub tab/],
    ['disabled', /check its enabled state/],
  ];
  for (const [code, action] of cases) {
    const held = row({code, filled: 0, raised: 0, kept: 0});
    const {ui} = boot([held]);
    const text = ui.renderMorningCockpit(report([held]), {}).textContent;
    assert.match(text, /Biology · Wk 04 · Fictional cell structures/);
    assert.match(text, action);
    assert.doesNotMatch(text, /Save\/readback not confirmed|correct\/save|clearing the helper verification block/);
  }
});

test('existing unsaved IC edits have their own review direction while uncertain saves retain recovery', () => {
  const pending = row({code: 'ic_not_saved', filled: 0, raised: 0, kept: 0});
  const {ui} = boot([pending]);
  const text = ui.renderMorningCockpit(report([pending]), {}).textContent;
  assert.match(text, /already has unsaved edits.*save them if intended or reload to discard them/);
  assert.doesNotMatch(text, /Save\/readback not confirmed|clearing the helper verification block/);
  for (const code of ['save_unconfirmed', 'readback_incomplete', 'readback_mismatch', 'uncertain_blocked', 'panel_unusable']) {
    const held = row({code});
    const recovery = ui.renderMorningCockpit(report([held]), {}).textContent;
    assert.match(recovery, /Biology · Wk 04 · Fictional cell structures/);
    assert.match(recovery, /Save\/readback not confirmed.*inspect the scores, correct\/save only if needed/);
    assert.match(recovery, /reload to verify before clearing the helper verification block/);
  }
});

test('source-only score holds retain assignment context and never become IC results or save recovery', async () => {
  const held = row({code: 'source_scores_held', filled: 0, raised: 0, kept: 0,
    source_holds: {checks: 4}, reasons: {check: 0}});
  const receipt = {...report([held]), code: 'source_scores_held'};
  const {ui, context, document} = boot([held]);
  for (const panel of [ui.renderNeedsYou(receipt), ui.renderMorningCockpit(receipt, {})]) {
    assert.match(panel.textContent, /Biology · Wk 04 · Fictional cell structures/);
    assert.match(panel.textContent, /4 submitted-work rows need source checks/);
    assert.match(panel.textContent, /No IC score comparison or entry was attempted for this assignment/);
    assert.match(panel.textContent, /Open Student work.*reason on each row marked “Check by hand”/);
    assert.doesNotMatch(panel.textContent, /✓ No grade rows need attention|✓ These IC results are confirmed|Save\/readback not confirmed|clearing the helper verification block|Exempt counts.*unavailable/);
  }
  context.fetch = async () => ({ok: true, json: async () => ({reports: [receipt]})});
  await ui.loadMorningSummary();
  const text = document.getElementById('morning-summary').textContent;
  assert.match(text, /4 submitted-work rows need source checks/);
  assert.match(text, /No IC score comparison or entry was attempted for this assignment/);
  assert.doesNotMatch(text, /0 entered|0 kept|Exempt counts.*unavailable/);
});

test('a source hold cannot invent a count and prevents all-clear even on a legacy finished receipt', () => {
  const {ui} = boot([]);
  for (const source_holds of [undefined, {checks: 0}, {checks: -1}, {checks: '4'}]) {
    const receipt = report([row({code: 'source_scores_held', source_holds, filled: 0, raised: 0, kept: 0})]);
    const text = ui.renderMorningCockpit(receipt, {}).textContent;
    assert.match(text, /Submitted work needs source checks/);
    assert.match(text, /1 assignment did not finish/);
    assert.doesNotMatch(text, /\d+ submitted-work rows?|✓ These IC results are confirmed|clearing the helper verification block/);
  }
});

test('an excluded-week receipt completes without IC entry or a save-recovery task in Today and Routine', async () => {
  const excluded = row({code: 'excluded_week', assignment: 'Fictional day-one exercise', week: 'Wk 01',
    filled: 0, raised: 0, kept: 0, left: 0, reasons: {...baseReasons}});
  const {ui, document} = boot([excluded]);
  for (const panel of [ui.renderNeedsYou(report([excluded])), ui.renderMorningCockpit(report([excluded]), {})]) {
    assert.match(panel.textContent, /Biology · Wk 01 · Fictional day-one exercise/);
    assert.match(panel.textContent, /intentionally excluded by the school-year policy/);
    assert.match(panel.textContent, /No IC score comparison or entry was attempted for this assignment/);
    assert.doesNotMatch(panel.textContent, /did not finish|Save\/readback not confirmed|correct\/save|clearing the helper verification block|These IC results are confirmed/);
    assert.equal(attention(panel).length, 0);
  }
  assert.match(ui.renderMorningCockpit(report([excluded]), {}).textContent, /Completed without entry/);
  await ui.loadMorningSummary();
  const text = document.getElementById('morning-summary').textContent;
  assert.match(text, /Biology · Wk 01 · Fictional day-one exercise/);
  assert.match(text, /No IC score comparison or entry was attempted for this assignment/);
  assert.doesNotMatch(text, /0 entered|0 kept|clearing the helper verification block/);
});

test('an excluded lesson cannot inflate confirmed results or suppress another assignment’s save recovery', () => {
  const excluded = row({code: 'excluded_week', assignment: 'Fictional day-one exercise', week: 'Wk 01',
    filled: 0, raised: 0, kept: 0, left: 0, reasons: {...baseReasons}});
  const confirmed = row({assignment: 'Fictional confirmed lesson'});
  const {ui} = boot([excluded, confirmed]);
  const completed = ui.renderMorningCockpit(report([excluded, confirmed]), {});
  assert.match(completed.textContent, /1 new scores entered · 2 raised · 3 existing scores matched in IC/);
  assert.doesNotMatch(completed.textContent, /did not finish|Save\/readback not confirmed/);
  for (const code of ['save_unconfirmed', 'readback_incomplete', 'readback_mismatch', 'panel_unusable']) {
    const held = row({code});
    const panel = ui.renderMorningCockpit(report([excluded, held]), {});
    assert.match(panel.textContent, /1 assignment did not finish/);
    const recovery = allNodes(panel).find(node => node.tagName === 'li' && node.textContent.includes(held.assignment));
    assert.match(recovery.textContent, /Save\/readback not confirmed.*reload to verify before clearing the helper verification block/);
    const skipped = allNodes(panel).find(node => node.tagName === 'li' && node.textContent.includes(excluded.assignment));
    assert.doesNotMatch(skipped.textContent, /Save\/readback|correct\/save|clearing the helper verification block/);
  }
});

test('empty preflight receipts show the precise safe cause in Today and Routine without an IC hand check', async () => {
  const held = {day: '2032-01-07', code: 'receipt_unconfirmed', items: [],
    preflight: {stage: 'summary_publish', cause: 'hub_bridge_missing'}};
  const {ui, context, document} = boot([]);
  for (const panel of [ui.renderNeedsYou(held), ui.renderMorningCockpit(held, {})]) {
    assert.match(panel.textContent, /before reading assignments/);
    assert.match(panel.textContent, /Refresh the Hub tab after the extension is loaded/);
    assert.match(panel.textContent, /No IC score comparison or entry was attempted/);
    assert.doesNotMatch(panel.textContent, /✓ No grade rows need attention/);
  }
  context.fetch = async () => ({ok: true, json: async () => ({reports: [held]})});
  await ui.loadMorningSummary();
  assert.match(document.getElementById('morning-summary').textContent, /Refresh the Hub tab after the extension is loaded/);
  assert.match(document.getElementById('morning-summary').textContent, /Receipt delivery retries do not run grades/);
});
test('an old empty receipt admits missing failure context without inventing sign-out or a save failure', async () => {
  const held = {day: '2032-01-07', code: 'receipt_unconfirmed', items: []};
  const {ui, context, document} = boot([]);
  const text = ui.renderNeedsYou(held).textContent;
  assert.match(text, /original failure cause was not retained/);
  assert.match(text, /No IC score comparison or save is confirmed/);
  assert.doesNotMatch(text, /signed out|timeout|uncertain save|✓ No grade rows need attention/);
  context.fetch = async () => ({ok: true, json: async () => ({reports: [held]})});
  await ui.loadMorningSummary();
  assert.match(document.getElementById('morning-summary').textContent, /original failure cause was not retained/);
  assert.ok(!ui.renderNeedsYou({...held, preflight: {stage: 'secret student', cause: 'https://secret.example.test/'}}).textContent.includes('secret.example'));
});

test('unresolved submission matches prevent score requests and IC handoff of cached scores', async () => {
  const {context} = boot([]), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
  context.window = {postMessage: message => messages.push(message)};
  context.crypto = {randomUUID: () => 'fictional-request'};
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction renderWork() {}\nglobalThis.client = {work, scoreForm, helperRows, sendList, weekToEnter, sendWeek, icPanel, icStatus};', context);
  const {client} = context;
  const form = {form_id: 'FICTIONALFORM123', grade_readiness: {status: 'READY'}, students: [{matched: true, last: 'Avery', first: 'Ann', section: 'A4 Biology',
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
  const form = {form_id: 'FICTIONALFORM123', grade_readiness: {status: 'READY'}, window: {started: true}, students: [], missing: [person], late: [], excused: [], elsewhere: [intake]};
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

test('unverified assignment codes and one unmarked copy block cached scores and every missing zero', async () => {
  const {context} = boot([]), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
  context.window = {postMessage: message => messages.push(message)};
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction renderWork() {}\nglobalThis.client = {work, scoreForm, helperRows, sendList, sendWeek, weekToEnter, icPanel, icStatus};', context);
  const {client} = context;
  const form = {form_id: 'FICTIONALFORM123', label: 'Photosynthesis', window: {started: true},
    students: [{matched: true, first: 'Ann', last: 'Avery', section: 'A4 Biology', files: [{id: 'FICTIONALFILE123'}]}],
    missing: [{first: 'Ben', last: 'Brook', section: 'A4 Biology'}], late: [], excused: [], elsewhere: []};
  client.work.data = {other_files: [], lessons: [{forms: [form]}]};
  client.work.scores = {[form.form_id]: {FICTIONALFILE123: {notes_score: 9, revision_score: 10}}};
  for (const readiness of [undefined, {status: 'NOT_READY', action: 'Embed the codes in the student copies.'}]) {
    form.grade_readiness = readiness;
    await client.scoreForm(form);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(await client.sendList(form, {}), 'deck_not_ready');
    assert.equal(client.icStatus(form).kind, 'held');
    assert.doesNotMatch(client.icPanel(form, {}, {}).textContent, /Enter these scores|Notes \/10/);
    const status = new Element('span'); await client.sendWeek(status, new Element('details'));
    assert.match(status.textContent, /codes|readiness/);
  }
  assert.equal(requests.length, 0); assert.equal(messages.length, 0);
  form.grade_readiness = {status: 'READY'};
  client.work.scores[form.form_id].FICTIONALFILE123 = {code: 'deck_not_ready', problem: 'Required markers are missing.'};
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(await client.sendList(form, {}), 'deck_not_ready');
  assert.equal(client.weekToEnter().ready.length, 0);
  assert.match(client.icPanel(form, {}, {}).textContent, /no missing-work zero/i);
  assert.equal(messages.length, 0);
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

function recheckClient() {
  const {context, document} = boot([]);
  const now = Date.parse('2032-01-20T15:00:00Z');
  context.Date = class extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  };
  context.window = {postMessage() {}};
  context.history = {replaceState() {}};
  context.crypto = {randomUUID: () => 'fictional-request'};
  const studentWork = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('const COLUMN ='));
  vm.runInContext(studentWork + '\nfunction renderWork() {}\nfunction closeSheets() {}\nfunction helperProgress() { return null; }\n' +
    'globalThis.client = {work, helper, closedRecheck, assessmentRows, scoreForm, helperRows, helperNeed, sendList, weekToEnter, icPanel, icStatus, loadWork};', context);
  const person = (first, last, id, matched = true) => ({first, last, section: 'A4 Biology', matched,
    submitted_at: '2032-01-05T12:00:00Z', late: false, earlier: [], typed: [], files: id ? [{id, name: id, mime: 'application/vnd.google-apps.presentation'}] : []});
  const form = {form_id: 'FICTIONALFORM123', label: 'Fictional ecosystems — Turn in', grade_readiness: {status: 'READY'},
    window: {from: '2032-01-01T00:00:00Z', at: '2032-01-10T23:59:00Z', opened: 'Jan 1', label: 'Jan 10'},
    students: [person('Ann', 'Avery', 'SAMEFILE123')],
    late: [{...person('Ben', 'Brook', 'LATEFILE123'), late: true}, {...person('Unknown', 'Owner', 'UNMATCHED123', false), late: true}],
    missing: [person('Cara', 'Cedar', null), {...person('Dan', 'Dale', null), unsorted: true}, person('Erin', 'Elm', null)],
    excused: [{...person('Finn', 'Fir', null), added: 'Jan 15'}],
    elsewhere: [{who: 'Elm, Erin', section: 'A4 Biology', accepted_intake: false}]};
  const mark = {configured: true, entered_at: {at: '2032-01-12T13:00:00Z'}};
  const {client} = context;
  Object.assign(client.work, {cls: 'biology', week: 'Wk 03', section: 'all', seq: 1,
    data: {other_files: [], problems: [], lessons: [{topic: 'Fictional ecosystems', forms: [form]}],
      entry_rechecks: {[form.form_id]: {rows: structuredClone([form.students[0], form.late[0]])}}},
    marks: {[form.form_id]: mark}, scores: {}, ui: {}});
  return {context, document, client, form, mark, now};
}

test('missing-code advisory keeps actual numeric scores eligible while other notes remain held', () => {
  const {context, client, form} = recheckClient();
  const warning = 'This copy is missing 10 of its 32 codes. Its score uses only the coded boxes found.';
  const score = {notes_score: 5.3, revision_score: 10, note: null, missing_code_warning: warning,
    counts: {NOTES: {done: 10, required: 19}, BANK: {done: 0, required: 0}, DRAFT: {done: 3, required: 3}}, items: []};
  client.work.scores[form.form_id] = {SAMEFILE123: score, LATEFILE123: {notes_score: 8, revision_score: 9}};
  const find = () => client.helperRows(form).find(row => row.first === 'Ann');
  assert.equal(find().kind, 'score');
  assert.equal(find().notes, 5.3);
  assert.equal(find().revision, 10);
  vm.runInContext('globalThis.scoreCellsForTest = scoreCells;', context);
  const text = context.scoreCellsForTest(form.students[0], client.work.scores[form.form_id]).map(cell => cell.textContent).join(' ');
  assert.match(text, /5\.3.*10 of 19/);
  assert.match(text, /3 of 3/);
  assert.ok(text.includes(warning));
  for (const change of [{note: 'The activity response match needs review.'}, {problem: 'Source access failed.'},
    {notes_score: null, revision_score: null}]) {
    client.work.scores[form.form_id].SAMEFILE123 = {...score, ...change};
    assert.equal(find().kind, 'check');
    assert.equal(find().notes, null);
    assert.equal(find().revision, null);
  }
});

test('an explicit found-code N/A copy stays visible without a coding hold or fabricated zero', () => {
  const {client, form} = recheckClient();
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: null, revision_score: null,
    scoring_basis: 'found_codes', counts: {NOTES: {required: 0}, BANK: {required: 0}, DRAFT: {required: 0}}}};
  const row = client.helperRows(form).find(row => row.first === 'Ann');
  assert.equal(row.kind, 'none');
  assert.equal(row.notes, null);
  assert.equal(row.revision, null);
  assert.match(row.reason, /N\/A.*no zero/);
  assert.equal(client.helperNeed([row]).length, 0);
});

test('a closed recorded assignment queues current on-time and matched late files regardless of old event dates', async () => {
  const {context, client, form} = recheckClient(), requests = [];
  const before = JSON.stringify(form);
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({notes_score: 9, revision_score: 10})}; };
  assert.equal(client.closedRecheck(form), true);
  await client.scoreForm(form);
  assert.deepEqual(requests.map(url => new URL(url, 'https://example.test/').searchParams.get('file')).sort(), ['LATEFILE123', 'SAMEFILE123']);
  assert.ok(requests.every(url => !url.includes('include_late')));
  assert.equal(JSON.stringify(form), before, 'rechecking must not change Form event dates, late grouping or originals');
  assert.equal(client.work.scores[form.form_id].SAMEFILE123.notes_score, 9);
  assert.equal(client.work.scores[form.form_id].LATEFILE123.revision_score, 10);
});

test('a validated server view selects a replacement formerly in earlier history without inventing or changing its provenance', async () => {
  const {context, client, form} = recheckClient(), requests = [];
  const original = form.students[0];
  const replacement = {submitted_at: '2032-01-18T12:00:00Z', source: 'form', email: 's.ann.avery@example.test', matched_by: 'email', late: true,
    files: [{id: 'REPLACEMENT123', name: 'Replacement deck', mime: 'application/vnd.google-apps.presentation',
      source_form_id: form.form_id, source_response_id: 'ORIGINALRESPONSE123', source_submitted_at: '2032-01-18T12:00:00Z'}]};
  original.earlier = [replacement];
  // This separate derived view is returned by api/work; the client does not rank history itself.
  const selected = {...structuredClone(original), ...structuredClone(replacement),
    earlier: [{submitted_at: original.submitted_at, source: original.source, files: structuredClone(original.files), late: original.late}]};
  client.work.data.entry_rechecks[form.form_id].rows = [selected, structuredClone(form.late[0])];
  const before = JSON.stringify(form);
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 6, revision_score: 7}};
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({notes_score: 10, revision_score: 10})}; };
  await client.scoreForm(form);
  assert.deepEqual(requests.map(url => new URL(url, 'https://example.test/').searchParams.get('file')).sort(), ['LATEFILE123', 'REPLACEMENT123']);
  const rows = client.helperRows(form).filter(row => row.first === 'Ann');
  assert.equal(rows.length, 1); assert.equal(rows[0].notes, 10);
  assert.equal(client.assessmentRows(form)[0].files[0].source_response_id, 'ORIGINALRESPONSE123');
  assert.equal(client.assessmentRows(form)[0].submitted_at, replacement.submitted_at);
  const render = source.slice(source.indexOf('function studentRow('), source.indexOf('function renderWork('));
  vm.runInContext(render + '\nglobalThis.renderReplacementForTest = renderForm;', context);
  const result = client.work.scores[form.form_id].REPLACEMENT123;
  Object.assign(result, {items: [], counts: {NOTES: {done: 1, required: 1}, BANK: {done: 0, required: 0}, DRAFT: {done: 1, required: 1}}});
  Object.assign(client.work.scores[form.form_id].LATEFILE123, result);
  const rendered = context.renderReplacementForTest(form, {});
  const selectedRow = allNodes(rendered).find(node => node.tagName === 'tr' && node.textContent.includes('Avery, Ann'));
  assert.ok(selectedRow);
  assert.equal(allNodes(selectedRow).filter(node => node.tagName === 'a' && node.textContent === 'Replacement deck').length, 1);
  const history = allNodes(selectedRow).find(node => node.tagName === 'details');
  assert.match(history.textContent, /SAMEFILE123/);
  assert.doesNotMatch(history.textContent, /Replacement deck/);
  assert.match(rendered.textContent, /original response history is retained/);
  assert.equal(JSON.stringify(form), before);
  const unknownHistory = {submitted_at: '2032-01-19T12:00:00Z', source: 'folder', late: true,
    files: [{id: 'UNVERIFIED123', name: 'Unconfirmed history file', mime: 'application/vnd.google-apps.presentation'}]};
  original.earlier.push(unknownHistory);
  client.work.data.entry_rechecks[form.form_id].rows[0].earlier.push(structuredClone(unknownHistory));
  client.work.data.entry_rechecks[form.form_id].rows[0].selection_problem = 'The replacement source needs confirmation.';
  const heldOriginal = JSON.stringify(form);
  const held = context.renderReplacementForTest(form, {});
  assert.match(held.textContent, /The replacement source needs confirmation.*These students remain unscored/);
  assert.doesNotMatch(held.textContent, /No scores are prepared for this assignment/);
  assert.match(held.textContent, /Unconfirmed history file/);
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(JSON.stringify(form), heldOriginal);
});

test('missing or malformed server recheck selection cannot fall back to an older file or create an IC list', async () => {
  const {context, client, form} = recheckClient(), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({notes_score: 10, revision_score: 10})}; };
  context.window.postMessage = message => messages.push(message);
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 9, revision_score: 10}, LATEFILE123: {notes_score: 9, revision_score: 10}};
  for (const view of [undefined, {}, {rows: {}}, {rows: [{matched: false, files: []}]}, {rows: [{matched: true, files: null}]}]) {
    client.work.data.entry_rechecks[form.form_id] = view;
    await client.scoreForm(form);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(client.weekToEnter().ready.length, 0);
    assert.equal(await client.sendList(form, {}), 'receipt_unconfirmed');
    assert.match(client.icPanel(form, {}, {}).textContent, /Current submitted files.*could not be verified/);
  }
  assert.equal(requests.length, 0); assert.equal(messages.length, 0);
});

test('one server-held replacement holds every score request and numeric handoff in the assignment', async () => {
  const {context, client, form} = recheckClient(), requests = [];
  const rows = client.work.data.entry_rechecks[form.form_id].rows;
  rows[0].selection_problem = 'Two different submitted files have the same latest Form time; check the source.';
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 10, revision_score: 10}};
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({notes_score: 9, revision_score: 10})}; };
  await client.scoreForm(form);
  assert.equal(requests.length, 0);
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(client.weekToEnter().ready.length, 0);
  assert.equal(await client.sendList(form, {}), 'coverage_incomplete');
  assert.match(client.icPanel(form, client.work.scores[form.form_id], {}).textContent, /same latest Form time/);
  assert.match(client.icStatus(form).text, /Biology · Wk 03 · Fictional ecosystems/);
  assert.match(client.icStatus(form).text, /Avery, Ann \(A4 Biology\)/);
});

test('a ready teacher deck cannot label a named held student copy Ready or lend it the teacher action', async () => {
  const {client, form} = recheckClient();
  form.grade_readiness = {status: 'READY', label: 'Ready', problem: 'Linked completion codes are ready.', action: 'Teacher source action must not mask a copy hold.'};
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 8, revision_score: 10}, LATEFILE123: {code: 'deck_not_ready',
    readiness: {status: 'NOT_READY', label: 'Needs embedded codes', problem: 'BANK lists14terms but this copy carries11markers.'},
    action: 'Restore only the missing codes in this student copy; preserve its responses.'}};
  for (const text of [client.icStatus(form).text, client.icPanel(form, client.work.scores[form.form_id], {}).textContent,
    ...client.weekToEnter().held]) {
    assert.match(text, /Biology · Wk 03 · Fictional ecosystems/);
    assert.match(text, /Brook, Ben \(A4 Biology\): Needs embedded codes/);
    assert.match(text, /copy carries11markers/);
    assert.match(text, /Restore only the missing codes/);
    assert.doesNotMatch(text, /: Ready|Linked completion codes are ready|Teacher source action/);
  }
  assert.equal(client.icStatus(form).kind, 'held');
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(client.weekToEnter().ready.length, 0);
  assert.equal(await client.sendList(form, {}), 'deck_not_ready');
});

test('compact held-copy warnings retain access errors and unresolved teacher-source holds distinctly', () => {
  const {client, form} = recheckClient();
  client.work.scores[form.form_id] = {SAMEFILE123: {code: 'deck_not_ready', label: 'Cannot read lesson',
    problem: 'This exact student deck could not be read.', action: 'Check this copy’s access before rerunning.'}};
  assert.match(client.icStatus(form).text, /Avery, Ann \(A4 Biology\): Cannot read lesson/);
  assert.match(client.icStatus(form).text, /Check this copy’s access/);
  assert.doesNotMatch(client.icStatus(form).text, /Needs embedded codes/);
  form.grade_readiness = {status: 'NOT_READY', label: 'Needs embedded codes', problem: 'Teacher inventory is missing.', action: 'Repair the teacher inventory first.'};
  assert.match(client.icStatus(form).text, /Needs embedded codes.*Repair the teacher inventory first/);
  assert.doesNotMatch(client.icStatus(form).text, /Cannot read lesson|Check this copy’s access/);
});

test('held code readiness never describes missing submissions as zeros or a prior IC entry as current', async () => {
  for (const closed of [false, true]) for (const hold of ['unknown', 'teacher', 'copy']) {
    const {context, client, form, mark} = recheckClient(), requests = [], messages = [];
    context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
    context.window.postMessage = message => messages.push(message);
    const render = source.slice(source.indexOf('function studentRow('), source.indexOf('function renderWork('));
    vm.runInContext(render + '\nglobalThis.renderHeldForTest = renderForm;', context);
    form.window.at = closed ? '2032-01-10T23:59:00Z' : '2032-01-30T23:59:00Z';
    form.missing = Array.from({length: 17}, (_, index) => ({first: `Student ${index + 1}`, last: 'Fictional', section: 'A4 Biology'}));
    form.grade_readiness = hold === 'unknown' ? undefined : hold === 'teacher'
      ? {status: 'NOT_READY', label: 'Needs embedded codes', action: 'Verify the teacher inventory first.'} : {status: 'READY'};
    client.work.scores = hold === 'copy' ? {[form.form_id]: {SAMEFILE123: {code: 'deck_not_ready',
      problem: 'This exact student copy needs embedded codes.'}}} : {};
    const before = JSON.stringify({form, mark});
    const rendered = context.renderHeldForTest(form, {});
    const missing = allNodes(rendered).find(node => node.className === 'missing');
    assert.equal(missing.children[0].textContent, 'Not turned in yet (17)', `${hold}, closed=${closed}`);
    assert.doesNotMatch(missing.textContent, /zero|\b0\b/);
    assert.match(rendered.textContent, /Scoring and IC entry are held; no missing-work zero is prepared/);
    assert.match(allNodes(rendered).find(node => node.className === 'marks').textContent, /^✓ Previously entered in IC /);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(await client.sendList(form, {}), 'deck_not_ready');
    if (hold !== 'copy') await client.scoreForm(form);
    assert.equal(requests.filter(url => url.startsWith('api/score?')).length, 0);
    assert.equal(requests.length, 1);
    assert.match(requests[0], /^api\/review-context\?class=biology&week=Wk%2003&form=FICTIONALFORM123$/);
    assert.equal(messages.length, 0);
    assert.equal(JSON.stringify({form, mark}), before, 'rendering preserves source rows and the original IC entry receipt');
  }
});

test('ready code inventory retains existing open and closed missing-work policy and IC history wording', () => {
  for (const closed of [false, true]) {
    const {context, client, form} = recheckClient();
    const render = source.slice(source.indexOf('function studentRow('), source.indexOf('function renderWork('));
    vm.runInContext(render + '\nglobalThis.renderReadyForTest = renderForm;', context);
    form.window.at = closed ? '2032-01-10T23:59:00Z' : '2032-01-30T23:59:00Z';
    form.missing = [{first: 'Cara', last: 'Cedar', section: 'A4 Biology'}];
    client.work.marks = {[form.form_id]: {configured: true}};
    const rendered = context.renderReadyForTest(form, {});
    const missing = allNodes(rendered).find(node => node.className === 'missing');
    assert.equal(missing.children[0].textContent, closed
      ? 'Nothing turned in on time (1) — these are 0 now' : 'Not turned in yet (1) — 0 until they turn it in');
    client.work.marks[form.form_id].entered_at = {at: '2032-01-12T13:00:00Z'};
    assert.match(allNodes(context.renderReadyForTest(form, {})).find(node => node.className === 'marks').textContent, /^✓ Entered in IC /);
  }
});

test('a closed recheck prepares actual scores, preserves review and Exempt rows, and never creates an absence zero', () => {
  const {client, form} = recheckClient();
  client.helper.state = 'ready';
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 8, revision_score: 10}, LATEFILE123: {notes_score: 0, revision_score: 7}};
  const rows = client.helperRows(form), find = first => rows.find(row => row.first === first);
  assert.equal(find('Ann').kind, 'score');
  assert.equal(find('Ben').kind, 'score');
  assert.equal(find('Ben').notes, 0, 'a measured submitted-file zero is still a valid score');
  assert.equal(find('Cara').kind, 'none'); assert.equal(find('Cara').notes, null);
  assert.equal(find('Dan').kind, 'check'); assert.equal(find('Erin').kind, 'check');
  assert.equal(find('Finn').kind, 'excused'); assert.equal(find('Finn').notes, null);
  assert.ok(!rows.some(row => row.kind === 'zero' || row.first === 'Unknown'));
  assert.equal(client.icStatus(form).kind, 'recheck');
  assert.equal(client.weekToEnter().ready.length, 1);
  const panel = client.icPanel(form, client.work.scores[form.form_id], {});
  assert.match(panel.textContent, /Send to the Chrome helper/);
  assert.match(panel.textContent, /Submission dates do not show later edits to the same file/);
  assert.match(panel.textContent, /no new missing-work zero|receive no new zero/);
  assert.doesNotMatch(panel.textContent, /nothing more|final scores entered|nobody has turned in anything new|including late work.*listed as 0/);
  const cara = allNodes(panel).find(node => node.tagName === 'tr' && node.textContent.includes('Cedar, Cara'));
  assert.equal(cara, undefined, 'no inferred missing score appears in the entry table');
});

test('manual handoff keeps the closed recheck list limited to measured current scores and non-entry rows', async () => {
  const {context, client, form} = recheckClient(), messages = [];
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 8, revision_score: 10}, LATEFILE123: {notes_score: 9, revision_score: 7}};
  context.location = {origin: 'https://example.test'};
  context.setTimeout = () => 0;
  context.window.postMessage = message => { messages.push(message); client.helper.waiting.get(message.request_id)('list_ready'); };
  assert.equal(await client.sendList(form, {topic: 'Fictional ecosystems'}), 'list_ready');
  assert.equal(messages.length, 1);
  const list = messages[0].list;
  assert.equal(list.status, 'closed');
  assert.match(list.status_text, /Recheck current submitted files/);
  assert.equal(list.rows.filter(row => row.kind === 'score').length, 2);
  assert.ok(!list.rows.some(row => row.kind === 'zero'));
  assert.equal(list.rows.find(row => row.first === 'Cara').notes, null);
  assert.equal(client.helper.sent[form.form_id].need.length, 2);
});

test('the late-submission table shows recheck scores and honest holds without changing lateness or inferring missing zeros', () => {
  const {context, client, form} = recheckClient();
  const render = source.slice(source.indexOf('function studentRow('), source.indexOf('function renderWork('));
  vm.runInContext(render + '\nglobalThis.renderFormForTest = renderForm;', context);
  const scored = value => ({notes_score: value, revision_score: 7, items: [], counts: {NOTES: {done: 1, required: 1}, BANK: {done: 0, required: 0}, DRAFT: {done: 1, required: 1}}});
  client.work.scores[form.form_id] = {SAMEFILE123: scored(8), LATEFILE123: scored(9)};
  form.missing = form.missing.filter(row => !row.unsorted);
  const before = JSON.stringify(form);
  const panel = context.renderFormForTest(form, {});
  const late = allNodes(panel).find(node => node.className === 'late');
  assert.match(late.textContent, /Late submissions — retained for recheck/);
  assert.match(late.textContent, /Notes \/10Revision \/10/);
  assert.match(late.textContent, /Brook, Ben/);
  assert.match(late.textContent, /Not matched to the roster/);
  assert.match(panel.textContent, /No verified submitted file.*no new zero prepared/);
  assert.doesNotMatch(panel.textContent, /these are 0 now|nothing more|Anything later is Late and isn’t graded/);
  assert.equal(JSON.stringify(form), before);
  client.work.scores[form.form_id].LATEFILE123 = {code: 'deck_not_ready', problem: 'Required markers are missing.'};
  const held = context.renderFormForTest(form, {});
  assert.match(held.textContent, /Required markers are missing/);
  assert.match(held.textContent, /Scoring and IC entry are held/);
  assert.doesNotMatch(held.textContent, /current files rechecked/);
});

test('missing-work print wording permits later reviewed increases without changing its selected history', () => {
  const {context} = recheckClient();
  const print = source.slice(source.indexOf('const shortDate ='), source.indexOf('async function missingSheets('));
  // Only the pure production print functions are needed; no navigation or printing runs.
  const pure = print.slice(0, print.indexOf('async function'));
  vm.runInContext(pure + '\nglobalThis.sheetForTest = sheetFor;', context);
  const history = {first: 'Ann', last: 'Avery', section: 'A4 Biology', total: 1, onTime: [],
    missing: [{week: 'Wk 03', title: 'Fictional ecosystems', close: new Date('2032-01-10T23:59:00Z'), open: false, late: true}]};
  const before = JSON.stringify(history);
  const panel = context.sheetForTest(history, 'Biology');
  assert.match(panel.textContent, /later verified submission or revision can earn a higher score after review/);
  assert.match(panel.textContent, /Past the original window — check current work/);
  assert.doesNotMatch(panel.textContent, /stays 0|these stay 0/);
  assert.equal(JSON.stringify(history), before);
});

test('late opt-in requires a configured valid recorded entry after the closed window and not in the future', async () => {
  const {context, client, form, mark, now} = recheckClient(), requests = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({notes_score: 9, revision_score: 10})}; };
  for (const altered of [{configured: false}, {configured: 'true'}, {problem: 'unavailable'},
    {entered_at: {at: 'bad timestamp'}}, {entered_at: {at: 'Jan 12, 2032'}}, {entered_at: {at: '2032-01-12T24:00:00Z'}},
    {entered_at: {at: '2032-01-09T12:00:00Z'}},
    {entered_at: {at: new Date(now + 1000).toISOString()}}]) {
    client.work.marks[form.form_id] = {...mark, ...altered};
    assert.equal(client.closedRecheck(form), false);
    client.work.scores = {}; requests.length = 0;
    await client.scoreForm(form);
    assert.ok(!requests.some(url => url.includes('LATEFILE123')));
    if (altered.configured !== undefined || altered.problem || ['bad timestamp', 'Jan 12, 2032', '2032-01-12T24:00:00Z', new Date(now + 1000).toISOString()].includes(altered.entered_at?.at)) {
      assert.equal(client.helperRows(form).length, 0);
      assert.equal(await client.sendList(form, {}), 'receipt_unconfirmed');
    }
  }
  client.work.marks[form.form_id] = mark;
  const closedAt = form.window.at;
  for (const at of ['bad timestamp', new Date(now + 1000).toISOString()]) {
    form.window.at = at; assert.equal(client.closedRecheck(form), false);
  }
  form.window.at = closedAt;
  client.work.marks[form.form_id] = {...mark, entered_at: {at: '2032-01-09T12:00:00Z'}};
  assert.equal(client.helperRows(form).find(row => row.first === 'Ben').kind, 'zero', 'the existing pre-final-entry path is unchanged');
});

test('rechecking a closed assignment preserves source, assessment and student-copy readiness holds', async () => {
  const {context, client, form} = recheckClient(), requests = [], messages = [];
  context.fetch = async url => { requests.push(url); return {ok: true, json: async () => ({})}; };
  context.window.postMessage = message => messages.push(message);
  client.work.scores[form.form_id] = {SAMEFILE123: {notes_score: 9, revision_score: 10}, LATEFILE123: {notes_score: 10, revision_score: 10}};
  for (const hold of ['source', 'assessment', 'teacher', 'copy']) {
    client.work.data.other_files = hold === 'source' ? [{files: [{id: 'UNRESOLVED123'}]}] : [];
    client.work.data.week_policy = hold === 'assessment' ? {excluded: true, reason: 'Not assigned this year.'} : null;
    form.grade_readiness = {status: hold === 'teacher' ? 'NOT_READY' : 'READY'};
    client.work.scores[form.form_id].LATEFILE123 = hold === 'copy' ? {code: 'deck_not_ready', problem: 'Required code missing.'} : {notes_score: 10, revision_score: 10};
    if (hold !== 'copy') await client.scoreForm(form);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(client.weekToEnter().ready.length, 0);
    assert.equal(client.icStatus(form).kind, 'held');
    assert.notEqual(await client.sendList(form, {}), 'list_ready');
  }
  assert.equal(requests.length, 0); assert.equal(messages.length, 0);
});

test('loading Student work waits for current entry history before scoring or offering an IC list', async () => {
  const {context, client, form, mark} = recheckClient(), requests = [];
  const currentWorkReply = client.work.data;
  let ledgerAnswer;
  context.fetch = async url => {
    requests.push(url);
    if (url.startsWith('api/work?')) return {ok: true, json: async () => currentWorkReply};
    if (url.startsWith('api/ledger?')) return new Promise(resolve => { ledgerAnswer = resolve; });
    return {ok: true, json: async () => ({notes_score: 8, revision_score: 9})};
  };
  const loading = client.loadWork(true);
  while (!ledgerAnswer) await Promise.resolve();
  assert.equal(client.helperRows(form).length, 0);
  assert.equal(client.weekToEnter().ready.length, 0);
  assert.equal(requests.filter(url => url.startsWith('api/score?')).length, 0);
  ledgerAnswer({ok: true, json: async () => mark});
  await loading;
  assert.equal(requests.filter(url => url.startsWith('api/score?')).length, 2);
  assert.equal(client.helperRows(form).find(row => row.first === 'Ben').kind, 'score');
});

test('a failed or superseded entry-history read cannot opt into late files or send absence zeros', async () => {
  for (const stale of [false, true]) {
    const {context, client, form, mark} = recheckClient(), requests = [];
    const currentWorkReply = client.work.data;
    let ledgerAnswer;
    context.fetch = async url => {
      requests.push(url);
      if (url.startsWith('api/work?')) return {ok: true, json: async () => currentWorkReply};
      if (url.startsWith('api/ledger?')) return new Promise(resolve => { ledgerAnswer = resolve; });
      return {ok: true, json: async () => ({notes_score: 9, revision_score: 10})};
    };
    const loading = client.loadWork();
    while (!ledgerAnswer) await Promise.resolve();
    if (stale) client.work.seq += 1;
    ledgerAnswer(stale ? {ok: true, json: async () => mark} : {ok: false, status: 503, json: async () => ({error: 'Unavailable'})});
    await loading;
    assert.equal(requests.filter(url => url.startsWith('api/score?')).length, 0);
    assert.equal(client.closedRecheck(form), false);
    assert.equal(client.helperRows(form).length, 0);
    assert.equal(client.work.scores[form.form_id], undefined);
  }
});
