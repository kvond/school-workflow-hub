import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.className = ''; this.disabled = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
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
  assert.doesNotMatch(rendered.textContent, /created 2026-10-08|printed successfully/);
  assert.equal(walk(rendered).filter(node => node.tagName === 'button').length, 1);
  assert.ok(walk(rendered).some(node => node.tagName === 'a' && node.href === packet.pdf_url));
  const details = walk(rendered).find(node => node.tagName === 'details');
  assert.ok(details);
  assert.equal(details.open, undefined);
  assert.match(details.textContent, /Feedback sources and student copies.*Saved feedback/);
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
  assert.match(status.textContent, /2026-10-07: 7 pages across classes.*daily document is saved/);
  assert.doesNotMatch(status.textContent, /Printed =|Handed =|printed successfully/);
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
