import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.open = false; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const walk = node => [node, ...node.children.filter(child => child instanceof Element).flatMap(walk)];
const report = (type, day, suffix) => ({kind: 'feedback', name: 'Same lesson title', date: '1999-01-01',
  feedback_date: day, feedback_date_basis: 'saved_at', report_type: type, drive_file_id: 'FictionalReport' + suffix,
  url: 'https://drive.google.com/open?id=FictionalReport' + suffix});
const index = (cls, students = [], overrides = {}) => ({schema: 'hub-student-archive/v1', class_key: cls,
  configured: true, status: 'verified', filing: {coverage: 'scanned_weeks_only'}, students, ...overrides});
const student = (name, records = []) => ({name, section: 'Fictional Section', status: 'current', records,
  folder_url: 'https://drive.google.com/drive/folders/FictionalFolder' + name.replaceAll(' ', '')});
function boot(handler = cls => index(cls)) {
  const nodes = new Map(), requests = [], dailyReads = [];
  const document = {createElement: tag => new Element(tag), getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new Element(id.endsWith('-body') ? 'div' : 'details'));
    return nodes.get(id);
  }};
  const context = vm.createContext({document, Node: Element, URL, loadTodayFeedback: seq => dailyReads.push(seq), renderTodayFeedbackArea() {}, fetch: async (url, options) => {
    requests.push({url, options}); const cls = new URL(url, 'https://example.invalid/').searchParams.get('class');
    return {ok: true, json: async () => url === 'api/teacher-links' ? {feedback_list_url: 'https://example.invalid/checklist'} : handler(cls)};
  }});
  const prelude = source.slice(0, source.indexOf('function issueAction('));
  const archive = source.slice(source.indexOf('// Saved artifacts across all connected'), source.indexOf('// ---------- Week ahead:'));
  const helpers = source.slice(source.indexOf('function recordFolderURL('), source.indexOf('async function loadStudentRecords('));
  vm.runInContext(prelude + archive + helpers + '\nglobalThis.ui = {loadFeedbackArchive, renderFeedbackArchive, feedbackArchive, feedbackArtifact};', context);
  return {ui: context.ui, context, document, requests, dailyReads, dates: document.getElementById('saved-feedback-body'), folders: document.getElementById('all-student-folders-body')};
}

test('collapsed lists make no request, share one all-class read, and reuse that read on the second expansion', async () => {
  const h = boot();
  await h.ui.loadFeedbackArchive(); assert.equal(h.requests.length, 0);
  h.document.getElementById('saved-feedback').open = true;
  await h.ui.loadFeedbackArchive();
  assert.deepEqual(h.requests.map(request => request.url), ['api/student-records?class=biology', 'api/student-records?class=forensics', 'api/student-records?class=anatomy']);
  h.document.getElementById('all-student-folders').open = true;
  await h.ui.loadFeedbackArchive(); assert.equal(h.requests.length, 3);
  assert.ok(h.requests.every(request => request.options.credentials === 'same-origin' && !request.options.method));
});

test('actual saved dates and explicit X/W/C types stay separate from lesson titles, source dates and history', async () => {
  const h = boot(cls => index(cls, cls === 'biology' ? [student('Ann Avery', [
    report('critical_aspects', '2032-01-07', 'X'), report('science_writing', '2032-01-09', 'W'),
    {...report('missing_work_contract', '2032-01-08', 'C'), historical: true},
    {...report(undefined, null, 'Undated'), feedback_date_basis: 'unverified'},
    {kind: 'work', name: 'Science writing contract', date: '2040-12-31'},
  ])] : cls === 'anatomy' ? [student('Cy Cole')] : []));
  h.document.getElementById('saved-feedback').open = true; await h.ui.loadFeedbackArchive();
  assert.match(h.dates.textContent, /2032-01-09.*W · Science writing.*2032-01-08.*C · Contract · previous version.*2032-01-07.*X · General.*Date unverified/);
  assert.doesNotMatch(h.dates.textContent, /1999|2040/);
  assert.match(h.dates.textContent, /Saved file: Same lesson title/);
  assert.match(h.folders.textContent, /Ann Avery.*Biology.*2032-01-09.*Cy Cole.*A&P.*No saved feedback confirmed/);
  assert.ok(walk(h.folders).some(node => node.href?.includes('FictionalFolderCyCole')));
  assert.equal(walk(h.dates).filter(node => node.tagName === 'a').length, 4);
  assert.match(h.dates.textContent, /newest submission is covered/);
});

test('unknown dates and types remain unverified despite suggestive file names and source timestamps', async () => {
  const unknown = {...report(undefined, '2032-01-07', 'Unknown'), name: 'Science writing contract feedback 2032-01-07',
    feedback_date_basis: 'file_created', saved_at: '2032-01-07T13:00:00Z'};
  const h = boot(cls => index(cls, cls === 'forensics' ? [student('Ben Brook', [unknown])] : []));
  h.document.getElementById('all-student-folders').open = true; await h.ui.loadFeedbackArchive();
  assert.match(h.dates.textContent, /Date unverified.*\? · Feedback type unverified.*actual feedback date unverified/);
  const groups = walk(h.dates).filter(node => node.tagName === 'summary');
  assert.equal(groups.length, 1);
  assert.match(groups[0].textContent, /^Date unverified/);
  assert.doesNotMatch(groups[0].textContent, /2032-01-07|1999-01-01/);
  assert.match(h.dates.textContent, /Saved file: Science writing contract feedback 2032-01-07/);
  assert.equal(h.ui.feedbackArtifact({...unknown, report_type: '__proto__'}).code, '?');
});

test('failed and partial classes keep known folders while unread coverage never becomes empty or complete', async () => {
  const h = boot(cls => {
    if (cls === 'anatomy') throw new Error('Unavailable');
    return index(cls, cls === 'biology' ? [student('Ann Avery', [report('critical_aspects', '2032-01-07', 'Held')])] : [],
      {status: cls === 'biology' ? 'held' : 'verified'});
  });
  h.document.getElementById('saved-feedback').open = true; await h.ui.loadFeedbackArchive();
  for (const body of [h.dates, h.folders]) {
    assert.match(body.textContent, /A&P: source unavailable.*unverified/);
    assert.match(body.textContent, /Biology: partial \/ held; complete coverage unverified/);
    assert.match(body.textContent, /Ann Avery/);
    assert.doesNotMatch(body.textContent, /up to date|all caught up|No student folders could be confirmed/);
  }
});

test('pending reads and a failed refresh discard earlier data instead of presenting it as current', async () => {
  let fail = false, release;
  const delayed = new Promise(resolve => { release = resolve; });
  const h = boot(async cls => {
    if (fail) { await delayed; throw new Error('Unavailable'); }
    return index(cls, cls === 'biology' ? [student('Ann Avery')] : []);
  });
  h.document.getElementById('saved-feedback').open = true; await h.ui.loadFeedbackArchive();
  fail = true; const refreshing = h.ui.loadFeedbackArchive(true);
  assert.match(h.dates.textContent, /Reading saved feedback.*unverified until the read finishes/);
  assert.doesNotMatch(h.folders.textContent, /Ann Avery/);
  release(); await refreshing;
  assert.match(h.dates.textContent, /source unavailable/);
  assert.doesNotMatch(h.folders.textContent, /Ann Avery/);
});

test('existing Lessons Reload refreshes expanded dates, folders and checklist sources without scanning collapsed lists', async () => {
  let generation = 'Earlier';
  const h = boot(cls => index(cls, cls === 'biology' ? [student(generation, [report('critical_aspects', '2032-01-07', generation)])] : []));
  h.document.getElementById('saved-feedback').open = true;
  await h.ui.loadFeedbackArchive();
  generation = 'Fresh';
  const plans = [];
  h.context.plans = {date: '2032-01-07'};
  h.context.loadPlans = (date, fresh) => plans.push({date, fresh});
  vm.runInContext(source.split('\n').find(line => line.startsWith("$('reload-plans').addEventListener(")), h.context);
  h.document.getElementById('reload-plans').listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(h.dates.textContent, /Fresh/);
  assert.doesNotMatch(h.dates.textContent, /Earlier/);
  assert.match(h.folders.textContent, /Fresh/);
  assert.equal(h.requests.filter(request => request.url.startsWith('api/student-records')).length, 6);
  assert.equal(h.requests.filter(request => request.url === 'api/teacher-links').length, 1);
  assert.equal(h.dailyReads.length, 1);
  assert.deepEqual(plans, [{date: '2032-01-07', fresh: true}]);
  h.document.getElementById('saved-feedback').open = false;
  h.document.getElementById('reload-plans').listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.filter(request => request.url.startsWith('api/student-records')).length, 6);
  assert.equal(h.dailyReads.length, 2);
});

test('a fresh archive read replaces a pending snapshot and ignores its eventual stale response', async () => {
  let generation = 'Earlier', release;
  const delayed = new Promise(resolve => { release = resolve; });
  const h = boot(async cls => {
    const name = generation;
    if (name === 'Earlier') await delayed;
    return index(cls, cls === 'biology' ? [student(name)] : []);
  });
  h.document.getElementById('all-student-folders').open = true;
  const older = h.ui.loadFeedbackArchive();
  await new Promise(resolve => setImmediate(resolve));
  generation = 'Fresh'; await h.ui.loadFeedbackArchive(true);
  assert.match(h.folders.textContent, /Fresh/);
  release(); await older;
  assert.match(h.folders.textContent, /Fresh/);
  assert.doesNotMatch(h.folders.textContent, /Earlier/);
});
