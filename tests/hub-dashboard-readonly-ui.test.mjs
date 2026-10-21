import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const extract = name => {
  const start = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source)?.index;
  assert.notEqual(start, undefined, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const prefix = source.slice(0, source.indexOf('// ---------- Hub v3'));
const walk = node => [node, ...node.children.filter(child => child instanceof Element).flatMap(walk)];
function boot(names, values = {}) {
  const nodes = new Map();
  const document = {createElement: tag => new Element(tag), querySelectorAll: () => [], getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id);
  }};
  const context = vm.createContext({Node: Element, URL, URLSearchParams, document, ...values});
  vm.runInContext(prefix + '\n' + names.map(extract).join('\n') + '\nglobalThis.ui = {' + names.join(',') + '};', context);
  return {context, ui: context.ui, nodes};
}

test('Records renders configured known links and refuses unsafe, unconfigured or unknown links', async () => {
  const calls = [];
  const h = boot(['recordURL', 'renderRecordsPrintableFeedback', 'loadRecordsPrintableFeedback', 'loadRecords'], {dashboard: {recordsSeq: 0}, fetch: async (url, options) => {
    calls.push({url, options});
    if (url === 'api/spreadsheet-feedback/print-link') return {ok: true, json: async () => ({status: 'unpublished'})};
    return {ok: true, json: async () => ({schema_version: 'hub-dashboard-records/v1', records: [
      {key: 'operations', label: 'Operations', configured: true, url: 'https://docs.google.com/spreadsheets/d/FICTIONALOPS/edit'},
      {key: 'model_trials', label: 'Models, rubrics and trials', configured: false, url: 'https://example.invalid/unconfigured'},
      {key: 'model_trials_folder', label: 'Trial files', configured: true, url: 'javascript:alert(1)'},
      {key: 'unknown', label: 'Unexpected', configured: true, url: 'https://example.invalid/extra'},
    ]})};
  }});
  await h.ui.loadRecords();
  const body = h.nodes.get('records-body'), links = walk(body).filter(node => node.tagName === 'a');
  assert.deepEqual(links.map(node => node.href), ['https://docs.google.com/spreadsheets/d/FICTIONALOPS/edit']);
  assert.match(body.textContent, /Models, rubrics and trials — link not configured.*Trial files — link not configured/);
  assert.doesNotMatch(body.textContent, /Unexpected/);
  assert.deepEqual(calls.map(call => call.url).sort(), ['api/dashboard/records', 'api/spreadsheet-feedback/print-link']);
  assert.ok(calls.every(call => call.options.method === undefined));
});


const readyPacket = {status: 'ready', date: '2032-01-07', pages: 12, reports: 8, students: 4,
  coverage: 'partial_verified_reports', url: 'https://drive.google.com/file/d/FICTIONALPACKET001/view',
  spreadsheet_url: 'https://docs.google.com/spreadsheets/d/FICTIONALFEEDBACK001/edit'};
function recordsBoot(reader = () => ({status: 'unpublished'}), directory) {
  const requests = [];
  const ordinary = directory || {schema_version: 'hub-dashboard-records/v1', records: [
    {key: 'operations', label: 'Operations', configured: true, url: 'https://docs.google.com/spreadsheets/d/FICTIONALOPS001/edit'}]};
  const h = boot(['recordURL', 'renderRecordsPrintableFeedback', 'loadRecordsPrintableFeedback', 'loadRecords'],
    {dashboard: {recordsSeq: 0}, fetch: async (url, options) => {
      requests.push({url, options});
      const result = url === 'api/dashboard/records' ? ordinary : await reader(url);
      return result instanceof Error ? {ok: false, status: 503, json: async () => ({error: result.message})}
        : {ok: true, json: async () => result};
    }});
  return {...h, requests};
}
const pdfLinks = node => walk(node).filter(child => child.tagName === 'a' && child.href.includes('/file/d/'));

test('Records independently reads the verified saved PDF and preserves partial counts and existing links', async () => {
  const h = recordsBoot(() => readyPacket);
  await h.ui.loadRecords();
  const body = h.nodes.get('records-body'), links = walk(body).filter(node => node.tagName === 'a');
  assert.ok(links.some(node => node.href === readyPacket.url));
  assert.ok(links.some(node => node.href === readyPacket.spreadsheet_url));
  assert.ok(links.some(node => node.textContent === 'Operations'));
  assert.match(body.textContent, /2032-01-07.*12 pages/);
  assert.match(body.textContent, /8 reports.*4 students.*[Gg]aps/);
  assert.deepEqual(h.requests.map(item => item.url).sort(), ['api/dashboard/records', 'api/spreadsheet-feedback/print-link']);
  assert.ok(h.requests.every(item => item.options.method === undefined));
});

test('an unavailable record directory does not hide an independently verified PDF', async () => {
  const h = recordsBoot(() => readyPacket, new Error('Fictional record directory unavailable'));
  await h.ui.loadRecords();
  const body = h.nodes.get('records-body');
  assert.equal(pdfLinks(body).length, 1);
  assert.match(body.textContent, /Record links are unavailable/);
  assert.match(body.textContent, /Print latest feedback/);
});

for (const [name, packet] of [
  ['held', {status: 'held', problem: 'The PDF changed after publication.', url: readyPacket.url}],
  ['unpublished', {status: 'unpublished'}],
  ['failed', new Error('Fictional print-link reader unavailable')],
  ['wrong PDF host', {...readyPacket, url: 'https://drive.google.com.evil.example/file/d/FICTIONALPACKET001/view'}],
  ['credential URL', {...readyPacket, url: 'https://someone:secret@drive.google.com/file/d/FICTIONALPACKET001/view'}],
  ['wrong spreadsheet host', {...readyPacket, spreadsheet_url: 'https://example.invalid/spreadsheets/d/FICTIONALFEEDBACK001/edit'}],
  ['missing verified counts', {...readyPacket, reports: undefined}],
  ['unknown coverage', {...readyPacket, coverage: 'ready'}],
]) test('Records keeps its directory but offers no PDF fallback for ' + name, async () => {
  const h = recordsBoot(() => packet);
  await h.ui.loadRecords();
  const body = h.nodes.get('records-body');
  assert.equal(pdfLinks(body).length, 0);
  assert.ok(walk(body).some(node => node.tagName === 'a' && node.textContent === 'Operations'));
  assert.doesNotMatch(body.textContent, /Print latest feedback/);
  if (name === 'held') assert.match(body.textContent, /PDF changed after publication/);
});

test('a stale print-link response cannot replace the newer Records result', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  let reads = 0;
  const h = recordsBoot(() => ++reads === 1 ? pending : readyPacket);
  const first = h.ui.loadRecords();
  await new Promise(resolve => setImmediate(resolve));
  await h.ui.loadRecords();
  finish({status: 'held', problem: 'Old result must not replace the latest publication.'});
  await first;
  const body = h.nodes.get('records-body');
  assert.equal(pdfLinks(body).length, 1);
  assert.doesNotMatch(body.textContent, /Old result must not replace/);
});

test('actual holds, next action, owner and recorded time remain visible without inventing start or completion', () => {
  const h = boot(['recordURL', 'runTime', 'renderDashboardRuns']);
  const rendered = h.ui.renderDashboardRuns({status: 'unavailable', items: [{id: 'FICTIONAL', work: 'Feedback filing', status: 'Partial',
    summary: 'Two saved reports verified.', unfinished: 'One class source is unavailable.', next_action: 'Restore the source read', owner: 'Codex',
    recorded_at: '2032-01-07T15:00:00Z', completed: 2, remaining: null, links: []}]});
  assert.match(rendered.textContent, /Some run history could not be read.*Partial.*recorded.*Two saved reports verified.*Completed: 2/);
  assert.match(rendered.textContent, /Unfinished: One class source is unavailable.*Next: Restore the source read · Codex/);
  assert.doesNotMatch(rendered.textContent, /Unfinished: 0/);
  const unknown = h.ui.renderDashboardRuns({status: 'available', items: [{work: 'Queued task', status: 'ready', summary: 'Waiting.'}]});
  assert.match(unknown.textContent, /Unverified/); assert.doesNotMatch(unknown.textContent, /Completed/);
});

const row = {matched: true, section: 'Fictional section', last: 'Avery', first: 'Ann', files: [{id: 'FICTIONALFILE001'}]};
const form = {form_id: 'FICTIONALFORM001', students: [row], late: []};
const saved = {status: 'saved', saved_at: '2032-01-07T15:00:00Z', notes_score: 8, revision_score: 6};
const savedBatch = results => ({schema_version: 'hub-saved-completion-list/v1', status: 'available', checked_at: '2032-01-07T16:00:00Z', results});
function savedBoot(read) {
  const requests = [], renders = [];
  const work = {seq: 1, cls: 'biology', week: 'Wk 07', scores: {}, data: {}};
  const h = boot(['savedGradeRows', 'verifiedSavedGrade', 'loadSavedScores'], {work, scoreFile: row => row.files[0],
    renderWork: () => renders.push('render'), fetch: async (url, options) => {
      requests.push({url, options}); return {ok: true, json: async () => read(url)};
    }});
  return {...h, work, requests, renders};
}

test('saved grades use one exact class/week/Form batch without scoring or exposing malformed values', async () => {
  const h = savedBoot(() => savedBatch({[row.files[0].id]: {...saved, notes_score: '8'}}));
  await h.ui.loadSavedScores(form, 1);
  const url = new URL(h.requests[0].url, 'https://hub.example/');
  assert.equal(url.pathname, '/api/scores/saved');
  assert.deepEqual(Object.fromEntries(url.searchParams), {class: 'biology', week: 'Wk 07', form: form.form_id});
  assert.equal(h.work.scores[form.form_id][row.files[0].id].status, 'unavailable');
  assert.equal(h.requests[0].options.method, undefined);
});

test('a failed saved-result read stays unavailable and does not call assessment as fallback', async () => {
  const h = savedBoot(() => { throw Error('Fictional unavailable'); });
  await h.ui.loadSavedScores(form, 1);
  assert.equal(h.requests.length, 1);
  assert.equal(h.work.scores[form.form_id][row.files[0].id].status, 'unavailable');
  assert.match(h.work.scores[form.form_id][row.files[0].id].problem, /No new assessment was run/);
});

test('a late saved-grade response cannot overwrite another selected week', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const h = savedBoot(() => pending), reading = h.ui.loadSavedScores(form, 1);
  await new Promise(resolve => setImmediate(resolve));
  h.work.seq = 2; h.work.scores = {}; finish(savedBatch({[row.files[0].id]: saved})); await reading;
  assert.deepEqual(h.work.scores, {}); assert.deepEqual(h.renders, []);
});

test('manual Helper preparation uses the unchanged canonical list instead of saved-view values', async () => {
  const canonical = {schema: 'hub-ic-list/v1', list_id: 'FICTIONAL-LIST', sent_at: '2032-01-07T15:00:00Z', rows: [{kind: 'score', notes: 9, revision: 7}]};
  const calls = [], sent = [], helper = {waiting: new Map(), sent: {}};
  const work = {cls: 'biology', week: 'Wk 07', scores: {[form.form_id]: {unrelated: {notes_score: 0}}}};
  const h = boot(['sendList'], {work, helper, HELPER: {send: 'hub-ic-entry'}, location: {origin: 'https://hub.example'},
    crypto: {randomUUID: () => 'FICTIONAL-REQUEST'}, setTimeout() {}, helperNeed: () => [['Fictional section', 'notes']], saveSent() {},
    window: {postMessage: message => { sent.push(message); helper.waiting.get(message.request_id)('list_ready'); }},
    fetch: async url => { calls.push(url); return {ok: true, json: async () => ({code: 'ready', list: canonical})}; }});
  assert.deepEqual(calls, []); assert.deepEqual(sent, []);
  assert.equal(await h.ui.sendList(form, {}), 'list_ready');
  assert.match(calls[0], /^api\/morning\/list\?/);
  assert.equal(sent[0].list, canonical);
  assert.equal(helper.sent[form.form_id].sent_at, canonical.sent_at);
});


test('many submitted files share one read and an omitted ID remains explicitly unavailable', async () => {
  const second = {...row, last: 'Brook', files: [{id: 'FICTIONALFILE002'}]};
  const h = savedBoot(() => savedBatch({[row.files[0].id]: saved}));
  await h.ui.loadSavedScores({...form, students: [row, second]}, 1);
  assert.equal(h.requests.length, 1);
  assert.equal(h.work.scores[form.form_id][row.files[0].id].status, 'saved');
  assert.equal(h.work.scores[form.form_id][second.files[0].id].status, 'unavailable');
  assert.notEqual(h.work.scores[form.form_id][second.files[0].id].notes_score, 0);
});

for (const response of [{schema_version: 'hub-saved-completion-list/v1', status: 'unavailable', results: {}},
  {schema_version: 'other', status: 'available', checked_at: '2032-01-07T16:00:00Z', results: {[row.files[0].id]: saved}}])
  test('an unavailable or mismatched batch cannot expose a cached grade: ' + response.status, async () => {
    const h = savedBoot(() => response); await h.ui.loadSavedScores(form, 1);
    assert.equal(h.work.scores[form.form_id][row.files[0].id].status, 'unavailable');
    assert.equal(h.requests.length, 1);
  });

function navigationBoot(reader) {
  const calls = [], work = {seq: 0, cls: 'biology', week: 'Biology Week', weeksFor: 'biology', section: 'all'};
  const location = {hash: '#work/biology/Biology%20Week'};
  const h = boot(['fillSelect', 'loadWeeks', 'openWork', 'showTab'], {work, location,
    showAgendaLink() {}, loadToday: () => calls.push('today'), loadRecords() {}, refreshVisiblePlans() {},
    loadWork: async () => { calls.push({class: work.cls, week: work.week}); location.hash = '#work/' + work.cls + '/' + work.week; },
    fetch: async url => ({ok: true, json: async () => reader(url)})});
  return {...h, work, location, calls};
}

test('a delayed class-week read cannot overwrite a newer class selection', async () => {
  let finish;
  const old = new Promise(resolve => { finish = resolve; });
  const h = navigationBoot(() => old);
  const pending = h.ui.openWork(['anatomy']);
  await new Promise(resolve => setImmediate(resolve));
  await h.ui.openWork(['biology']);
  finish({class_key: 'anatomy', weeks: [{label: 'Anatomy Only Week', forms: 1}], current: 'Anatomy Only Week', sections: []});
  await pending;
  assert.equal(h.work.cls, 'biology'); assert.equal(h.work.week, 'Biology Week');
  assert.equal(h.work.weeksFor, 'biology'); assert.deepEqual(h.calls, [{class: 'biology', week: 'Biology Week'}]);
});

test('leaving Student work immediately invalidates pending week discovery and cannot pull navigation back', async () => {
  let finish;
  const old = new Promise(resolve => { finish = resolve; });
  const h = navigationBoot(() => old), pending = h.ui.openWork(['anatomy']);
  await new Promise(resolve => setImmediate(resolve));
  h.location.hash = '#today'; h.ui.showTab();
  finish({class_key: 'anatomy', weeks: [{label: 'Anatomy Only Week', forms: 1}], current: 'Anatomy Only Week', sections: []});
  await pending;
  assert.equal(h.location.hash, '#today'); assert.deepEqual(h.calls, ['today']);
  assert.notEqual(h.work.week, 'Anatomy Only Week');
});

test('a newly selected class clears old students, saved values and Helper controls before its weeks arrive', async () => {
  let finish;
  const old = new Promise(resolve => { finish = resolve; });
  const h = navigationBoot(() => old);
  h.work.data = {class_key: 'biology', students: ['Old student']};
  h.work.scores = {old: saved}; h.work.ui = {old: {helper: 'Old controls'}};
  const body = h.context.document.getElementById('work-body'); body.append(new Element('old-helper-button'));
  const pending = h.ui.openWork(['anatomy']);
  assert.equal(h.work.data, null); assert.equal(Object.keys(h.work.scores).length, 0);
  assert.equal(Object.keys(h.work.ui).length, 0); assert.equal(body.children.length, 0);
  finish({class_key: 'anatomy', weeks: [{label: 'Anatomy Week', forms: 1}], current: 'Anatomy Week', sections: []});
  await pending;
  assert.deepEqual(h.calls, [{class: 'anatomy', week: 'Anatomy Week'}]);
});

test('a response for a different class cannot supply the selected class’s week list', async () => {
  const h = navigationBoot(() => ({class_key: 'biology', weeks: [{label: 'Wrong Week'}], current: 'Wrong Week', sections: []}));
  await h.ui.openWork(['anatomy']);
  assert.deepEqual(h.calls, []); assert.notEqual(h.work.week, 'Wrong Week');
  assert.match(h.nodes.get('work-status').textContent, /weeks could not be verified/);
});
