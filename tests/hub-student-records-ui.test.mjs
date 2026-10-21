import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; this.open = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const prelude = source.slice(0, source.indexOf('// ---------- Hub v3'));
const records = source.slice(source.indexOf('// ---------- Student work ----------'), source.indexOf('function fillSelect('));
const walk = node => [node, ...node.children.filter(child => child instanceof Element).flatMap(walk)];
const response = (key = 'biology', overrides = {}) => ({schema: 'hub-student-archive/v1', configured: true, status: 'verified', class_key: key,
  class_name: key === 'biology' ? 'Biology' : 'Forensics', teacher_reports_url: 'https://drive.google.com/drive/folders/TEACHER_REPORTS',
  students: [{name: 'Ann Avery', section: 'A4 Fictional', status: 'current', folder_url: 'https://drive.google.com/drive/folders/STUDENT_CURRENT', latest_record_date: '2032-01-07'},
    {name: 'Ben Brook', section: 'A4 Fictional', status: 'retained', folder_url: 'https://drive.google.com/drive/folders/STUDENT_RETAINED', latest_record_date: '2031-11-20'}],
  ...overrides});

function boot(fetchData = async () => response()) {
  const ids = new Map(), requests = [];
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!ids.has(id)) ids.set(id, new Element(id === 'student-records' ? 'details' : 'div'));
    return ids.get(id);
  }};
  const context = vm.createContext({document, Node: Element, URL, fetch: async (url, options) => {
    requests.push({url, options}); return {ok: true, json: async () => fetchData(url)};
  }});
  vm.runInContext(prelude + '\n' + records + '\nglobalThis.ui = {work, studentRecords, loadStudentRecords, renderStudentRecords};', context);
  context.ui.work.cls = 'biology';
  const panel = document.getElementById('student-records'), body = document.getElementById('student-records-body');
  return {ui: context.ui, context, document, panel, body, requests};
}

test('Records is a link-only page and no longer mounts archive controls', () => {
  const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
  assert.equal((html.match(/id="page-records"/g) || []).length, 1);
  assert.match(html, /id="records-body"/);
  assert.doesNotMatch(html, /id="student-records"|id="records-class"/);
  const records = source.slice(source.indexOf('async function loadRecords('), source.indexOf('function renderLessonReadiness('));
  assert.match(records, /api\/dashboard\/records/);
  assert.doesNotMatch(records, /api\/student-records|archive|postJSON/);
});

test('selected class records show current and retained students, dates, and separate teacher reports', async () => {
  const h = boot(); h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /Open Biology teacher reports/);
  assert.match(h.body.textContent, /Ann AveryA4 FictionalCurrentJan 7, 2032/);
  assert.match(h.body.textContent, /Ben BrookA4 FictionalRetained historyNov 20, 2031/);
  const links = walk(h.body).filter(node => node.tagName === 'a');
  assert.deepEqual(links.map(node => node.href), ['https://drive.google.com/drive/folders/TEACHER_REPORTS',
    'https://drive.google.com/drive/folders/STUDENT_CURRENT', 'https://drive.google.com/drive/folders/STUDENT_RETAINED']);
  assert.ok(links.every(node => node.target === '_blank' && node.rel === 'noopener noreferrer'));
  assert.equal(walk(h.body).filter(node => ['button', 'input'].includes(node.tagName)).length, 0);
});

test('an unverified roster never labels a student Current', async () => {
  const data = response(); data.students[0].status = 'unverified';
  const h = boot(() => data); h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /Ann AveryA4 FictionalRoster unverified/);
  assert.doesNotMatch(h.body.textContent, /Ann AveryA4 FictionalCurrent/);
  assert.match(h.body.textContent, /Ben BrookA4 FictionalRetained history/);
});

test('a name-filed folder stays visibly identity unverified without adding controls', async () => {
  const data = response(); data.students[0].status = 'identity_unverified';
  data.students[0].identity_basis = 'student_named_filename';
  const h = boot(() => data); h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /Ann AveryA4 FictionalFiled by name · identity unverified/);
  assert.doesNotMatch(h.body.textContent, /Ann AveryA4 FictionalCurrent/);
  assert.ok(walk(h.body).some(node => node.tagName === 'a' && node.href.endsWith('/STUDENT_CURRENT')));
  assert.equal(walk(h.body).filter(node => ['button', 'input'].includes(node.tagName)).length, 0);
});

test('verified selected IC withdrawal shows its end date and retains the folder', async () => {
  const data = response();
  data.students[0].status = 'withdrawn';
  data.students[0].roster_status = {verified: true, status: 'withdrawn', active: false, end_date: '2032-01-03'};
  data.students[1].status = 'identity_unverified';
  data.students[1].roster_status = {verified: true, status: 'active', active: true};
  const h = boot(() => data); h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /Ann AveryA4 FictionalWithdrawn · Jan 3, 2032 · history retained/);
  assert.match(h.body.textContent, /Ben BrookA4 FictionalActive in IC/);
  assert.equal(walk(h.body).filter(node => node.tagName === 'a' && node.href.includes('/STUDENT_')).length, 2);
  assert.equal(walk(h.body).filter(node => ['button', 'input'].includes(node.tagName)).length, 0);
});

test('switching class suppresses an older reply and immediately clears the old class links', async () => {
  const pending = new Map(), h = boot(url => new Promise(resolve => pending.set(url, resolve)));
  h.panel.open = true;
  const older = h.ui.loadStudentRecords();
  h.ui.work.cls = 'forensics';
  const newer = h.ui.loadStudentRecords();
  assert.doesNotMatch(h.body.textContent, /Ann Avery|teacher reports/);
  await new Promise(resolve => setImmediate(resolve));
  pending.get('api/student-records?class=forensics')(response('forensics', {students: [{name: 'Cy Cole', section: 'A2 Fictional', status: 'current', folder_url: 'https://drive.google.com/drive/folders/FORENSICS_STUDENT', latest_record_date: null}]}));
  await newer;
  pending.get('api/student-records?class=biology')(response()); await older;
  assert.match(h.body.textContent, /Cy Cole/);
  assert.doesNotMatch(h.body.textContent, /Ann Avery|Open Biology/);
  assert.equal(h.ui.studentRecords.data.class_key, 'forensics');
});

test('wrong-class and failed API replies do not expose records or internal error codes', async () => {
  const h = boot(() => response('forensics')); h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /could not be loaded for this class/);
  assert.doesNotMatch(h.body.textContent, /Ann Avery|teacher reports/);
  h.context.fetch = async () => ({ok: false, status: 503, json: async () => ({error: 'student_archive_missing_parent'})});
  await h.ui.loadStudentRecords(true);
  assert.doesNotMatch(h.body.textContent, /student_archive_missing_parent/);
  assert.equal(h.ui.studentRecords.data, null);
});

test('held and unconfigured lists stay visibly incomplete, and invalid folder links never become clickable', async () => {
  const h = boot(() => response('biology', {status: 'held', problems: ['internal_code'], students: [
    {name: 'Fictional student', section: 'A4', status: 'current', folder_url: 'javascript:alert(1)', latest_record_date: null}]}));
  h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /could not be confirmed/);
  assert.match(h.body.textContent, /No dated records yet.*Folder unavailable/);
  assert.doesNotMatch(h.body.textContent, /internal_code/);
  assert.ok(walk(h.body).filter(node => node.tagName === 'a').every(node => node.href.startsWith('https://drive.google.com/drive/folders/')));
  h.ui.renderStudentRecords(response('biology', {configured: false, status: 'unconfigured', students: []}));
  assert.match(h.body.textContent, /not available for this class yet/);
  assert.doesNotMatch(h.body.textContent, /No student records have been filed/);
});

test('held records retain safe folder links and show teacher-readable reasons as plain text', async () => {
  const problems = ['The Biology teacher reports folder needs verified private access.', 'student_archive_missing_parent',
    'Traceback in private_source', {error: 'internal_code'}];
  const h = boot(() => response('biology', {status: 'held', problems}));
  h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /The Biology teacher reports folder needs verified private access/);
  assert.match(h.body.textContent, /A record could not be confirmed. Existing records are preserved/);
  assert.doesNotMatch(h.body.textContent, /student_archive_missing_parent|Traceback|internal_code|\[object Object\]/);
  assert.equal(walk(h.body).filter(node => node.tagName === 'a').length, 3);
  assert.ok(walk(h.body).find(node => node.tagName === 'li' && node.textContent === problems[0]));
});

test('pending filing retains dated folders and names the exact class, week and identity reason', async () => {
  const h = boot(() => response('biology', {status: 'held', roster_verified: false, students: [
    {name: 'Ann Avery', section: 'A4 Fictional', status: 'unverified', folder_url: 'https://drive.google.com/drive/folders/STUDENT_CURRENT', latest_record_date: '2032-01-07'}],
    filing: {coverage: 'holds_remain', held_count: 1, scans: [
      {class_key: 'biology', class_name: 'Biology', week: 'Wk 03', status: 'held', held_count: 1, held: [
        {name: 'Rivera, Ana', section: 'A4 Fictional', assignment: 'Energy Flow — Turn In', source_file_names: 'Untitled presentation.pptx', problem: 'This record needs a verified school email or IC student identity; a name alone is not enough.'}], problems: []},
      {class_key: 'biology', week: 'Wk 06', status: 'verified', held_count: 0, held: [], problems: []},
      {class_key: 'forensics', week: 'Wk 99', status: 'held', held_count: 1, held: [], problems: ['Do not show another class.']}]
    }}));
  h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /1 filing hold remains. Checks cover recorded weeks only/);
  assert.match(h.body.textContent, /Biology · Wk 03 · Rivera, Ana · A4 Fictional · Energy Flow — Turn In · Untitled presentation.pptx: This record needs a verified school email or IC student identity/);
  assert.doesNotMatch(h.body.textContent, /Forensics · Wk 99|Do not show another class|all caught up/);
  assert.match(h.body.textContent, /Ann AveryA4 FictionalRoster unverifiedJan 7, 2032/);
  assert.ok(walk(h.body).some(node => node.tagName === 'a' && node.href.endsWith('/STUDENT_CURRENT')));
  assert.equal(walk(h.body).filter(node => ['button', 'input'].includes(node.tagName)).length, 0);
});

test('unverified filing and machine hold reasons cannot become a complete-source claim', async () => {
  const h = boot(() => response('biology', {filing: {coverage: 'unverified', held_count: 0, scans: []}}));
  h.panel.open = true; await h.ui.loadStudentRecords();
  assert.match(h.body.textContent, /Filing coverage has not been verified/);
  h.ui.renderStudentRecords(response('biology', {filing: {coverage: 'holds_remain', held_count: 1,
    scans: [{class_key: 'biology', week: 'Wk 02', status: 'held', held_count: 1, held: [], problems: ['private_archive_identity_missing']}]} }));
  assert.match(h.body.textContent, /Biology · Wk 02: A record could not be confirmed. Existing records are preserved/);
  assert.doesNotMatch(h.body.textContent, /private_archive_identity_missing|all caught up/);
});

test('folder access stays above collapsed filing details while exact hold counts and context remain available', async () => {
  const held = Array.from({length: 45}, (_, index) => ({name: 'Fictional student ' + index % 36,
    section: 'A2 Fictional', assignment: 'Evidence — Turn In', problem: 'A verified school email is needed.'}));
  const h = boot(() => response('forensics', {status: 'held', students: [{name: 'Ann Avery', section: 'A2 Fictional',
    status: 'unverified', folder_url: 'https://drive.google.com/drive/folders/STUDENT_CURRENT', latest_record_date: '2032-01-07'}],
    filing: {coverage: 'holds_remain', held_count: 45, scans: [{class_key: 'forensics', week: 'Wk 05', status: 'held', held_count: 45,
      held, problems: []}]}}));
  h.ui.work.cls = 'forensics'; h.panel.open = true; await h.ui.loadStudentRecords();
  const tableWrap = h.body.children.find(node => node.className === 'table-wrap');
  const checks = h.body.children.find(node => node.tagName === 'details');
  assert.ok(tableWrap);
  assert.ok(checks);
  assert.ok(h.body.children.indexOf(tableWrap) < h.body.children.indexOf(checks));
  assert.equal(checks.open, false);
  assert.match(checks.children[0].textContent, /Filing checks · 45 records awaiting review/);
  assert.match(h.body.children.filter(node => node !== checks).map(node => node.textContent).join(''), /45 filing holds remain. Checks cover recorded weeks only/);
  assert.doesNotMatch(h.body.children.filter(node => node !== checks).map(node => node.textContent).join(''), /A verified school email is needed/);
  assert.equal(walk(checks).filter(node => node.tagName === 'li').length, 36);
  for (let index = 0; index < 36; index++) assert.ok(walk(checks).some(node => node.tagName === 'li'
    && node.textContent === `Forensics · Wk 05 · Fictional student ${index} · A2 Fictional · Evidence — Turn In: A verified school email is needed.`));
  assert.match(tableWrap.textContent, /Ann AveryA2 FictionalRoster unverifiedJan 7, 2032/);
  assert.ok(walk(tableWrap).some(node => node.tagName === 'a' && node.href.endsWith('/STUDENT_CURRENT')));
  assert.match(h.body.textContent, /Open Forensics teacher reports/);
  assert.equal(walk(h.body).filter(node => ['button', 'input'].includes(node.tagName)).length, 0);
});
