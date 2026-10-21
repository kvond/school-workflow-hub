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
function boot({popup = true, navigationFails = false} = {}) {
  const requests = [], printed = [];
  const printWindow = {addEventListener(name, callback) { this.loaded = callback; }, print() { printed.push('print'); }, close() { printed.push('close'); },
    set location(value) { if (navigationFails) throw Error('In-app popup navigation unavailable'); this.url = value; this.loaded?.(); }};
  const context = vm.createContext({document: {createElement: tag => new Element(tag)}, Node: Element,
    window: {location: {href: 'https://school.invalid/v2/#today'}, open: () => popup ? printWindow : null}, setTimeout: fn => { fn(); },
    URL,
    fetch: async (url, options) => { requests.push({url, options}); return url === 'api/feedback/daily'
      ? {ok: true, json: async () => packet} : {ok: true, blob: async () => ({type: 'application/pdf', size: 100})}; }});
  vm.runInContext(prefix + '\nfunction loadToday() {}\n' + renderer + '\nglobalThis.ui = {renderFeedbackTools, printTodayFeedback};', context);
  return {ui: context.ui, requests, printed, printWindow, context};
}
const files = [{id: 'fictional-source', name: 'Saved feedback', course_name: 'Biology', created_at: DAY + 'T15:00:00Z',
  url: 'https://drive.google.com/open?id=fictional-source', course_folder_url: 'https://drive.google.com/drive/folders/FICTIONAL_CLASS'}];

test('automatic email status stays visible for historical and empty cards without altering paper state or controls', () => {
  const {ui} = boot();
  const email_delivery = {configured: true, status: 'active', recipient: 'teacher@school.example',
    counts: {ready: 2, reserved: 1, sent: 4, uncertain: 1, held: 2}};
  const before = JSON.stringify(email_delivery);
  for (const data of [{date: DAY, requested_date: '2026-10-09', files, daily_packet: packet}, {files: []}]) {
    const rendered = ui.renderFeedbackTools({...data, email_delivery});
    assert.match(rendered.textContent, /Automatic school email to teacher@school.example: 3 pending · 4 sent · 1 uncertain · 2 held/);
    assert.match(rendered.textContent, /Weekdays at 7:30; printing and handing out remain separate/);
    assert.equal(walk(rendered).filter(node => node.tagName === 'button').length, data.files.length ? 1 : 0);
    assert.doesNotMatch(rendered.textContent, /Printed =|Handed =|printed successfully|email successfully/);
  }
  assert.equal(JSON.stringify(email_delivery), before);
});

test('unverified email status never claims delivery and keeps the existing PDF fallback', () => {
  const {ui} = boot();
  const rendered = ui.renderFeedbackTools({date: DAY, files, daily_packet: packet, email_delivery: {status: 'unavailable'}});
  assert.match(rendered.textContent, /Automatic email status could not be verified.*Hub print controls remain available/);
  assert.equal(walk(rendered).filter(node => node.tagName === 'button').length, 1);
});

test('Today links the actual saved feedback date across classes, with source links collapsed', () => {
  const {ui} = boot();
  const rendered = ui.renderFeedbackTools({date: DAY, requested_date: '2026-10-08', files, daily_packet: packet});
  assert.match(rendered.textContent, /Feedback dated 2026-10-07 · all classes/);
  assert.match(rendered.textContent, /Open saved daily feedback — 2026-10-07 \(7 pages\)/);
  assert.doesNotMatch(rendered.textContent, /created 2026-10-08|printed successfully|null|undefined/);
  assert.equal(walk(rendered).filter(node => node.tagName === 'button').length, 1);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === packet.pdf_url));
  const details = walk(rendered).find(node => node.tagName === 'details' && node.children[0]?.textContent === 'Current feedback by student');
  assert.ok(details);
  assert.equal(details.open, undefined);
  assert.match(details.textContent, /Current feedback by student.*Saved feedback/);
});

test('older feedback is a collapsed reprint, while verified new feedback keeps the current print action', () => {
  const {ui} = boot();
  const old = ui.renderFeedbackTools({date: DAY, requested_date: '2026-10-09', current_sources_verified: true, files, daily_packet: packet});
  assert.match(old.textContent, /No new feedback for 2026-10-09/);
  assert.ok(walk(old).some(node => node.tagName === 'details' && /^Earlier feedback/.test(node.children[0].textContent) && node.open === undefined));
  assert.doesNotMatch(old.textContent, /Print all feedback — 2026-10-07/);
  const current = ui.renderFeedbackTools({date: '2026-10-09', requested_date: '2026-10-09', files, daily_packet: {...packet, date: '2026-10-09'}});
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
  const {ui, requests, printed, printWindow} = boot(), status = new Element('p'), button = new Element('button');
  await ui.printTodayFeedback({date: DAY, files, daily_packet: {configured: true, status: 'not_saved'}}, status, button);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, 'api/feedback/daily');
  assert.deepEqual(JSON.parse(requests[0].options.body), {date: DAY});
  assert.equal(requests[1].url, packet.pdf_url);
  assert.ok(requests.every(request => request.options.credentials === 'same-origin'));
  assert.deepEqual(printed, [], 'preparing the PDF does not start a physical print job');
  assert.equal(printWindow.url, 'https://school.invalid' + packet.pdf_url);
  assert.match(status.textContent, /Daily feedback PDF ready for 2026-10-07: 7 pages across classes.*daily document is saved.*Open PDF.*Download PDF.*PDF print button \(or Cmd\/Ctrl\+P\).*whole document/);
  assert.doesNotMatch(status.textContent, /Print dialog opened|document opened|Printed =|Handed =|printed successfully/);
  assert.equal(walk(status).find(node => node.textContent === 'Open PDF').href, packet.pdf_url);
  assert.equal(button.disabled, false);
});

const previewID = 'feedbackprintpreview_' + 'b'.repeat(64);
const preview = {schema: 'hub-feedback-print-preview/v1', status: 'ready', id: previewID,
  preview_only: true, daily_document_saved: false, delivery_changed: false,
  name: 'Feedback-print-preview-' + 'b'.repeat(12) + '.pdf', source_count: 1, pages: 2,
  pdf_url: '/v2/api/feedback/preview/pdf?id=' + previewID};

test('a blocked or unsupported popup still leaves a verified HTTPS pending preview to open or download', async () => {
  for (const options of [{popup: false}, {navigationFails: true}]) {
    const {ui, context, printed} = boot(options), requests = [];
    context.fetch = async (url, options) => { requests.push({url, options}); return {ok: true, json: async () => preview}; };
    const status = new Element('p'), button = new Element('button');
    await ui.printTodayFeedback({date: DAY, files: [...files, {...files[0], id: 'already-handed'}],
      pending_files: files, print_pending_only: true, daily_packet: packet}, status, button);
    assert.equal(requests.length, 1); assert.equal(requests[0].url, 'api/feedback/preview');
    assert.deepEqual(JSON.parse(requests[0].options.body), {ids: ['fictional-source']});
    assert.match(status.textContent, /Pending feedback preview ready: 1 exact report version, 2 pages.*separate from the saved daily document/);
    const links = walk(status).filter(node => node.tagName === 'a');
    assert.deepEqual(links.map(node => node.href), [preview.pdf_url, preview.pdf_url]);
    assert.equal(links[1].download, preview.name);
    assert.equal(button.disabled, false); assert.deepEqual(printed, []);
    assert.doesNotMatch(status.textContent, /preview opened|daily document is saved|Allow pop-ups|blob:/);
  }
});

test('native PDF navigation uses the same HTTPS preview route and never replaces a pending preview with the saved full packet', async () => {
  const {ui, context, printWindow, printed} = boot();
  context.fetch = async () => ({ok: true, json: async () => preview});
  const status = new Element('p');
  await ui.printTodayFeedback({date: DAY, files, pending_files: files, print_pending_only: true,
    daily_packet: packet}, status, new Element('button'));
  assert.equal(printWindow.url, 'https://school.invalid' + preview.pdf_url);
  assert.ok(walk(status).filter(node => node.tagName === 'a').every(node => node.href === preview.pdf_url));
  assert.doesNotMatch(status.textContent, /Daily feedback PDF ready|daily document is saved/);
  assert.deepEqual(printed, []);
});

test('an unavailable or malformed preview cannot advertise a PDF, change delivery or retry automatically', async () => {
  for (const descriptor of [{error: 'Private preview unavailable.'}, {...preview, preview_only: false},
    {...preview, pdf_url: packet.pdf_url}, {...preview, pdf_url: 'blob:unusable'}]) {
    const {ui, context, printed} = boot(), calls = [];
    context.fetch = async url => { calls.push(url); return {ok: !descriptor.error, json: async () => descriptor}; };
    const status = new Element('p'), button = new Element('button');
    await ui.printTodayFeedback({date: DAY, files, pending_files: files, print_pending_only: true,
      daily_packet: packet}, status, button);
    assert.equal(calls.length, 1); assert.equal(button.disabled, false);
    assert.equal(walk(status).filter(node => node.tagName === 'a').length, 0);
    assert.doesNotMatch(status.textContent, /preview ready|preview opened|Recorded|Printed =|Handed =/);
    assert.deepEqual(printed, ['close']);
  }
});

test('a saved dated snapshot remains reprintable when current sources are unavailable', async () => {
  const {ui, requests} = boot();
  const data = {date: DAY, files: [], daily_packet: packet, source_problem: 'Current private source access needs review.'};
  assert.match(ui.renderFeedbackTools(data).textContent, /Current feedback sources could not be fully checked.*saved dated document remains available/);
  await ui.printTodayFeedback(data, new Element('p'), new Element('button'));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, packet.pdf_url);
  assert.equal(requests[0].options.method, undefined);
});

test('a source-read failure stays visible above collapsed historical feedback', () => {
  const {ui} = boot();
  const problem = 'Fictional course source access needs review.';
  const rendered = ui.renderFeedbackTools({date: DAY, requested_date: '2026-10-09', files: [],
    daily_packet: packet, source_problem: problem});
  assert.match(rendered.textContent, /Current feedback for 2026-10-09 could not be checked/);
  const visible = rendered.children.find(node => node instanceof Element && node.tagName !== 'details' && node.textContent.includes(problem));
  assert.ok(visible, 'The source failure must be visible without opening history.');
  const history = rendered.children.find(node => node instanceof Element && node.tagName === 'details');
  assert.equal(history.open, undefined);
  assert.doesNotMatch(history.textContent, /Fictional course source access/);
  const unavailable = ui.renderFeedbackTools({date: '2026-10-09', files: [], source_problem: problem});
  assert.match(unavailable.textContent, /could not be fully checked/);
  assert.doesNotMatch(unavailable.textContent, /No print-ready feedback documents were found/);
});

test('two students with the same PDF title remain distinct and earlier daily PDFs stay linked', () => {
  const {ui} = boot();
  const sources = ['Rivera, Ana', 'Avery, Sam'].map((student_name, index) => ({...files[0], id: 'student-' + index,
    name: '2026-10-07 — Carbon cycle — Feedback.pdf', student_name, feedback_day: DAY}));
  const earlier = {date: '2026-10-05', pages: 1, pdf_url: '/v2/api/feedback/daily/pdf?id=dailyfeedbackpacket_' + 'b'.repeat(64)};
  const rendered = ui.renderFeedbackTools({date: DAY, files: sources, packet_matches_sources: true,
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
  assert.match(ui.renderFeedbackTools(data).textContent, /saved document differs.*updated version.*earlier PDF is preserved/);
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
  const today = source.slice(source.indexOf('async function printTodayFeedback('), source.indexOf('function renderLessonReadiness('));
  vm.runInContext(prefix + '\nfunction renderAgendaDiagnostics() { return el("p", {text: "Source checks"}); }\nfunction renderNextLessons() { return el("p", {text: "Deck readiness"}); }\n' + today +
    '\nglobalThis.ui = {loadToday, cockpit};', context);
  return {ui: context.ui, document, requests};
}

test('Today waits for recorded results without starting feedback or packet reads', async () => {
  const saved = deferred(); const h = bootToday(url => url === 'api/dashboard/records' ? saved.promise : undefined);
  const pending = h.ui.loadToday(); await tick();
  assert.match(h.document.getElementById('today-status').textContent, /Reading saved run results/);
  assert.deepEqual(h.requests.map(request => request.url), ['api/dashboard/records']);
  saved.resolve(response({schema_version: 'hub-dashboard-records/v1', runs: {status: 'available', items: []}}));
  await pending;
  assert.match(h.document.getElementById('today-body').textContent, /No verified run record/);
});

test('an unavailable saved run read does not fall back to feedback generation', async () => {
  const h = bootToday(url => url === 'api/dashboard/records' ? Promise.reject(new Error('Fictional unavailable')) : undefined);
  await h.ui.loadToday();
  assert.match(h.document.getElementById('today-body').textContent, /No completed run is confirmed/);
  assert.deepEqual(h.requests.map(request => request.url), ['api/dashboard/records']);
});

test('the refreshed run record supersedes a delayed earlier result', async () => {
  const old = deferred(); let reads = 0;
  const result = name => ({schema_version: 'hub-dashboard-records/v1', runs: {status: 'available', items: [{id: name, work: name, status: 'Completed', summary: name}]}});
  const h = bootToday(url => url === 'api/dashboard/records' ? reads++ ? response(result('New saved run')) : old.promise : undefined);
  const pending = h.ui.loadToday(); await tick(); await h.ui.loadToday(); old.resolve(response(result('Old saved run'))); await pending;
  assert.match(h.document.getElementById('today-body').textContent, /New saved run/);
  assert.doesNotMatch(h.document.getElementById('today-body').textContent, /Old saved run/);
});

test('a malformed records projection remains unavailable without creating any output', async () => {
  const h = bootToday(url => url === 'api/dashboard/records' ? response({runs: {status: 'available', items: []}}) : undefined);
  await h.ui.loadToday();
  assert.match(h.document.getElementById('today-body').textContent, /unavailable/);
  assert.ok(h.requests.every(request => !request.options.method));
});
