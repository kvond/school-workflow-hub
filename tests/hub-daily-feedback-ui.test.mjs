import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this._text = ''; this.className = ''; this.disabled = false;
    this.classList = {toggle: (name, enabled) => { this[name] = enabled; }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const prefix = source.slice(0, source.indexOf('function issueAction('));
const renderer = source.slice(source.indexOf('async function printTodayFeedback('), source.indexOf('function renderLearningReport('));
const walk = node => [node, ...node.children.filter(child => child instanceof Element).flatMap(walk)];
const DAY = '2026-10-07', id = 'dailyfeedbackpacket_' + 'a'.repeat(64);
const packet = {configured: true, status: 'ready', date: DAY, source_count: 3, pages: 7, pdf_url: '/v2/api/feedback/daily/pdf?id=' + id};
function boot() {
  const requests = [], printed = [];
  const printWindow = {addEventListener(name, callback) { this.loaded = callback; }, print() { printed.push('print'); }, close() { printed.push('close'); },
    set location(value) { this.url = value; this.loaded?.(); }};
  const context = vm.createContext({document: {createElement: tag => new Element(tag)}, Node: Element,
    window: {open: () => printWindow}, setTimeout: fn => { fn(); },
    URL: {createObjectURL: () => 'blob:fictional', revokeObjectURL() {}},
    fetch: async (url, options) => { requests.push({url, options}); return url === 'api/feedback/daily'
      ? {ok: true, json: async () => packet} : {ok: true, blob: async () => ({type: 'application/pdf', size: 100})}; }});
  vm.runInContext(prefix + '\nfunction loadToday() {}\n' + renderer + '\nglobalThis.ui = {renderTodayFeedback, printTodayFeedback};', context);
  return {ui: context.ui, requests, printed};
}
const files = [{id: 'fictional-source', name: 'Saved feedback', course_name: 'Biology', created_at: DAY + 'T15:00:00Z',
  url: 'https://drive.google.com/open?id=fictional-source', course_folder_url: 'https://drive.google.com/drive/folders/FICTIONAL_CLASS'}];

test('Today links the actual saved feedback date across classes, with source links collapsed', () => {
  const {ui} = boot();
  const rendered = ui.renderTodayFeedback({date: DAY, requested_date: '2026-10-08', files, daily_packet: packet});
  assert.match(rendered.textContent, /Feedback dated 2026-10-07 · all classes/);
  assert.match(rendered.textContent, /Open saved daily feedback — 2026-10-07 \(7 pages\)/);
  assert.doesNotMatch(rendered.textContent, /created 2026-10-08|printed successfully|null|undefined/);
  assert.equal(walk(rendered).filter(node => node.tagName === 'button').length, 1);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === packet.pdf_url));
  const details = walk(rendered).find(node => node.tagName === 'details' && /Current feedback by student/.test(node.textContent));
  assert.ok(details);
  assert.equal(details.open, undefined);
  assert.match(details.textContent, /Current feedback by student.*Saved feedback/);
});

test('older feedback is a collapsed reprint, while verified new feedback keeps the current print action', () => {
  const {ui} = boot();
  const old = ui.renderTodayFeedback({date: DAY, requested_date: '2026-10-09', current_sources_verified: true, files, daily_packet: packet});
  assert.match(old.textContent, /No new feedback for 2026-10-09/);
  assert.ok(walk(old).some(node => node.tagName === 'details' && /^Earlier feedback/.test(node.children[0].textContent) && node.open === undefined));
  assert.doesNotMatch(old.textContent, /Print all feedback — 2026-10-07/);
  const current = ui.renderTodayFeedback({date: '2026-10-09', requested_date: '2026-10-09', files, daily_packet: {...packet, date: '2026-10-09'}});
  assert.match(current.textContent, /Print all feedback — 2026-10-09/);
  assert.doesNotMatch(current.textContent, /No new feedback|Earlier feedback · latest/);
});

test('teacher writing dimensions remain collapsed, labelled and absent for legacy drafts', () => {
  const {ui} = boot();
  const context = vm.createContext({document: {createElement: tag => new Element(tag)}, Node: Element});
  const helper = source.slice(source.indexOf('function teacherWritingPreview('), source.indexOf('function feedbackPanel('));
  vm.runInContext(prefix + helper + '\nglobalThis.preview = teacherWritingPreview;', context);
  assert.equal(context.preview(undefined), null);
  const rendered = context.preview([{dimension: 'Specific scientific evidence', level: 'Developing', question_id: 'Q2', stage: 'Revision', quote: 'the sample was blue', explanation: 'Include the observed measurement.'}]);
  assert.equal(rendered.open, undefined);
  assert.match(rendered.textContent, /teacher only, excluded from the student PDF.*Specific scientific evidence: Developing.*Q2 · Revision.*the sample was blue.*Include the observed measurement/);
});

test('the print action finalizes the exact day, fetches its saved PDF, and never changes delivery state', async () => {
  const {ui, requests, printed} = boot(), status = new Element('p'), button = new Element('button');
  await ui.printTodayFeedback({date: DAY, files, daily_packet: {configured: true, status: 'not_saved'}}, status, button);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, 'api/feedback/daily');
  assert.deepEqual(JSON.parse(requests[0].options.body), {date: DAY});
  assert.equal(requests[1].url, packet.pdf_url);
  assert.ok(requests.every(request => request.options.credentials === 'same-origin'));
  assert.deepEqual(printed, ['print']);
  assert.match(status.textContent, /document opened for 2026-10-07: 7 pages across classes.*PDF print button \(or Cmd\/Ctrl\+P\).*whole document.*daily document is saved/);
  assert.doesNotMatch(status.textContent, /Print dialog opened|Printed =|Handed =|printed successfully/);
  assert.equal(button.disabled, false);
});

test('a saved dated snapshot remains reprintable when current sources are unavailable', async () => {
  const {ui, requests} = boot();
  const data = {date: DAY, files: [], daily_packet: packet, source_problem: 'Current private source access needs review.'};
  assert.match(ui.renderTodayFeedback(data).textContent, /Current feedback sources could not be fully checked.*saved dated document remains available/);
  await ui.printTodayFeedback(data, new Element('p'), new Element('button'));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, packet.pdf_url);
  assert.equal(requests[0].options.method, undefined);
});

test('a source-read failure stays visible above collapsed historical feedback', () => {
  const {ui} = boot();
  const problem = 'Fictional course source access needs review.';
  const rendered = ui.renderTodayFeedback({date: DAY, requested_date: '2026-10-09', files: [],
    daily_packet: packet, source_problem: problem});
  assert.match(rendered.textContent, /No new feedback confirmed for 2026-10-09/);
  const visible = rendered.children.find(node => node instanceof Element && node.tagName !== 'details' && node.textContent.includes(problem));
  assert.ok(visible, 'The source failure must be visible without opening history.');
  const history = rendered.children.find(node => node instanceof Element && node.tagName === 'details');
  assert.equal(history.open, undefined);
  assert.doesNotMatch(history.textContent, /Fictional course source access/);
  const unavailable = ui.renderTodayFeedback({date: '2026-10-09', files: [], source_problem: problem});
  assert.match(unavailable.textContent, /could not be fully checked/);
  assert.doesNotMatch(unavailable.textContent, /No print-ready feedback documents were found/);
});

test('two students with the same PDF title remain distinct and earlier daily PDFs stay linked', () => {
  const {ui} = boot();
  const sources = ['Rivera, Ana', 'Avery, Sam'].map((student_name, index) => ({...files[0], id: 'student-' + index,
    name: '2026-10-07 — Carbon cycle — Feedback.pdf', student_name, feedback_day: DAY}));
  const earlier = {date: '2026-10-05', pages: 1, pdf_url: '/v2/api/feedback/daily/pdf?id=dailyfeedbackpacket_' + 'b'.repeat(64)};
  const rendered = ui.renderTodayFeedback({date: DAY, files: sources, packet_matches_sources: true,
    daily_packet: {...packet, available_dates: [earlier]}});
  assert.match(rendered.textContent, /Biology · Rivera, Ana/);
  assert.match(rendered.textContent, /Biology · Avery, Sam/);
  assert.doesNotMatch(rendered.textContent, /Open course folder|Oct 8/);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === earlier.pdf_url));
});

test('fresh matching membership and revisions print the saved PDF directly, while changed sources finalize', async () => {
  const {ui, requests} = boot();
  await ui.printTodayFeedback({date: DAY, files, daily_packet: packet, packet_matches_sources: true}, new Element('p'), new Element('button'));
  assert.deepEqual(requests.map(item => item.url), [packet.pdf_url]);
  requests.length = 0;
  const data = {date: DAY, files, daily_packet: packet, packet_matches_sources: false};
  assert.match(ui.renderTodayFeedback(data).textContent, /saved document differs.*updated version.*earlier PDF is preserved/);
  await ui.printTodayFeedback(data, new Element('p'), new Element('button'));
  assert.deepEqual(requests.map(item => item.url), ['api/feedback/daily', packet.pdf_url]);
});

const response = data => ({ok: true, json: async () => data});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
function bootToday(handler) {
  const nodes = new Map(), requests = [], never = new Promise(() => {});
  const document = {createElement: tag => new Element(tag), getElementById: name => {
    if (!nodes.has(name)) nodes.set(name, new Element('div'));
    return nodes.get(name);
  }};
  const context = vm.createContext({document, Node: Element,
    fetch: (url, options) => { requests.push({url, options}); return handler(url, options) ?? never; }});
  const today = source.slice(source.indexOf('async function printTodayFeedback('), source.indexOf('async function loadLessonReadiness('));
  vm.runInContext(prefix + '\nfunction renderNextLessons() { return el("p", {text: "Deck readiness"}); }\n' + today +
    '\nglobalThis.ui = {loadToday, cockpit};', context);
  return {ui: context.ui, document, requests};
}

test('saved daily controls render while Agenda, audits and source catalog are still unresolved', async () => {
  const catalog = deferred();
  const {ui, document, requests} = bootToday(url => {
    if (url === 'api/feedback/daily') return response(packet);
    if (url === 'api/feedback/today') return catalog.promise;
  });
  let finished = false;
  ui.loadToday().then(() => { finished = true; });
  await tick();
  const rendered = document.getElementById('today-feedback');
  assert.equal(finished, false);
  assert.match(document.getElementById('today-status').textContent, /Reading today’s Agenda/);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === packet.pdf_url));
  assert.ok(walk(rendered).some(node => node.tagName === 'button' && /Reprint saved feedback — 2026-10-07/.test(node.textContent)));
  assert.match(rendered.textContent, /7 pages/);
  assert.doesNotMatch(rendered.textContent, /Printed =|Handed =|printed successfully/);
  assert.ok(requests.some(request => request.url.startsWith('api/audit?')));
  assert.ok(requests.findIndex(request => request.url === 'api/feedback/daily') < requests.findIndex(request => request.url === 'api/feedback/today'));
  assert.ok(requests.every(request => !request.options.method || request.options.method === 'GET'));
});

test('a current-catalog failure retains the verified saved PDF without becoming a main run result', async () => {
  const catalog = deferred();
  const {ui, document} = bootToday(url => {
    if (url === 'api/feedback/daily') return response(packet);
    if (url === 'api/feedback/today') return catalog.promise;
  });
  ui.loadToday();
  await tick();
  catalog.reject(new Error('Fictional source access unavailable'));
  await tick();
  const rendered = document.getElementById('today-feedback');
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === packet.pdf_url));
  assert.match(rendered.textContent, /saved dated document remains available.*Fictional source access unavailable/);
  assert.match(document.getElementById('today-status').textContent, /Reading today’s Agenda/);
  assert.equal(document.getElementById('today-body').children.length, 0);
});

test('a late catalog and learning refresh cannot replace a newer Today saved document', async () => {
  const catalogs = [deferred(), deferred()], learning = [deferred(), deferred()];
  const newer = {...packet, date: '2026-10-08', pages: 9, pdf_url: '/v2/api/feedback/daily/pdf?id=dailyfeedbackpacket_' + 'b'.repeat(64)};
  let reads = 0, catalogsRead = 0, learningRead = 0;
  const {ui, document} = bootToday(url => {
    if (url === 'api/feedback/daily') return response(reads++ ? newer : packet);
    if (url === 'api/feedback/today') return catalogs[catalogsRead++].promise;
    if (url === 'api/learning-report') return learning[learningRead++].promise;
  });
  ui.loadToday(); await tick();
  ui.loadToday(true); await tick();
  assert.match(document.getElementById('today-feedback').textContent, /Open saved daily feedback — 2026-10-08 \(9 pages\)/);
  learning[1].resolve(response({configured: true, as_of: '2026-10-08', url: 'https://example.test/current-growth', teaching_url: 'https://example.test/current-teaching'}));
  await tick();
  catalogs[0].resolve(response({date: DAY, files, daily_packet: packet}));
  learning[0].resolve(response({configured: true, as_of: '1999-01-01', url: 'https://example.test/old-growth', teaching_url: 'https://example.test/old-teaching'}));
  await tick();
  const rendered = document.getElementById('today-feedback');
  assert.match(rendered.textContent, /2026-10-08 \(9 pages\)/);
  assert.doesNotMatch(rendered.textContent, /2026-10-07|1999-01-01|Saved feedback/);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === newer.pdf_url));
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === 'https://example.test/current-growth'));
});

test('an earlier saved-packet response cannot overwrite a newer Today or start its stale catalog', async () => {
  const older = deferred();
  const newer = {...packet, date: '2026-10-08', pages: 9};
  let reads = 0, catalogs = 0;
  const {ui, document} = bootToday(url => {
    if (url === 'api/feedback/daily') return reads++ ? response(newer) : older.promise;
    if (url === 'api/feedback/today') { catalogs++; return new Promise(() => {}); }
  });
  ui.loadToday();
  ui.loadToday(true); await tick();
  older.resolve(response(packet)); await tick();
  assert.match(document.getElementById('today-feedback').textContent, /2026-10-08 \(9 pages\)/);
  assert.doesNotMatch(document.getElementById('today-feedback').textContent, /2026-10-07/);
  assert.equal(catalogs, 1);
});
