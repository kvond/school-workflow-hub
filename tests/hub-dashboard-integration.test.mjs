import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

// Execute the complete production application, including boot, listeners and
// navigation, against the real HTML. Unknown element IDs remain absent so a
// listener left behind after removing a panel fails this harness.
const application = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const markup = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const ORIGIN = 'https://hub.example';
const FORM = 'FICTIONALFORM001', FILE = 'FICTIONALWORK001';

class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.attributes = {}; this.childNodes = []; this.parentElement = null;
    this.listeners = new Map(); this.dataset = {}; this.value = ''; this.hidden = false;
    this.open = false; this.disabled = false;
    this.classList = {
      contains: value => this.className.split(/\s+/).includes(value),
      add: (...values) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...values])].join(' '); },
      remove: (...values) => { this.className = this.className.split(/\s+/).filter(value => !values.includes(value)).join(' '); },
      toggle: (value, force) => {
        const enabled = force ?? !this.classList.contains(value);
        this.classList[enabled ? 'add' : 'remove'](value); return enabled;
      },
    };
  }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = String(value); }
  get children() { return this.childNodes.filter(child => child instanceof Element); }
  get textContent() { return this.childNodes.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  set textContent(value) { this.replaceChildren(String(value)); }
  get lastChild() { return this.childNodes.at(-1) || null; }
  get firstChild() { return this.childNodes[0] || null; }
  append(...children) {
    for (const child of children) {
      if (child instanceof Element) {
        child.remove(); child.parentElement = this;
      }
      this.childNodes.push(child);
    }
  }
  prepend(...children) {
    const old = this.childNodes; this.childNodes = []; this.append(...children, ...old);
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.childNodes = []; this.append(...children);
  }
  remove() {
    if (!this.parentElement) return;
    const parent = this.parentElement;
    parent.childNodes = parent.childNodes.filter(child => child !== this);
    this.parentElement = null;
  }
  replaceWith(...children) {
    const parent = this.parentElement; if (!parent) return;
    const index = parent.childNodes.indexOf(this);
    for (const child of children) if (child instanceof Element) child.parentElement = parent;
    parent.childNodes.splice(index, 1, ...children); this.parentElement = null;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    else if (['hidden', 'open', 'disabled', 'checked'].includes(name)) this[name] = true;
    else if (name !== 'class') this[name] = String(value);
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) {
    delete this.attributes[name];
    if (['hidden', 'open', 'disabled', 'checked'].includes(name)) this[name] = false;
  }
  addEventListener(name, handler) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), handler]);
  }
  async dispatch(name, values = {}) {
    const event = {target: this, preventDefault() {}, stopPropagation() {}, ...values};
    for (const handler of this.listeners.get(name) || []) await handler(event);
  }
  querySelectorAll(selector) { return this.ownerDocument.select(selector, this); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  scrollIntoView() {}
  focus() {}
}

function elements(root) { return [root, ...root.children.flatMap(elements)]; }

function documentFromHTML(html) {
  const document = {visibilityState: 'visible', listeners: new Map()};
  document.createElement = tag => new Element(tag, document);
  document.root = document.createElement('document');
  document.select = (selector, root = document.root) => {
    const segments = selector.trim().split(/\s+/);
    const matches = (node, part) => part.startsWith('#') ? node.id === part.slice(1)
      : part.startsWith('.') ? node.classList.contains(part.slice(1))
      : node.tagName === part.toUpperCase();
    return elements(root).filter(node => {
      if (!matches(node, segments.at(-1))) return false;
      let ancestor = node.parentElement;
      for (const segment of segments.slice(0, -1).reverse()) {
        while (ancestor && !matches(ancestor, segment)) ancestor = ancestor.parentElement;
        if (!ancestor) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    });
  };
  document.getElementById = id => elements(document.root).find(node => node.id === id) || null;
  document.querySelectorAll = selector => document.select(selector);
  document.querySelector = selector => document.select(selector)[0] || null;
  document.addEventListener = (name, handler) => {
    document.listeners.set(name, [...(document.listeners.get(name) || []), handler]);
  };
  const stack = [document.root];
  const voids = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
  for (const token of html.matchAll(/<!--[\s\S]*?-->|<![^>]*>|<\/?[\w-]+\b[^>]*>|[^<]+/g)) {
    const text = token[0];
    if (text.startsWith('<!')) continue;
    if (text.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
    if (!text.startsWith('<')) { if (text.trim()) stack.at(-1).append(text); continue; }
    const tag = text.match(/^<([\w-]+)/)[1];
    const node = document.createElement(tag);
    const attributes = text.slice(tag.length + 1, -1);
    for (const attribute of attributes.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      node.setAttribute(attribute[1], attribute[2] ?? attribute[3] ?? attribute[4] ?? '');
    }
    stack.at(-1).append(node);
    if (!voids.has(tag.toLowerCase()) && !text.endsWith('/>')) stack.push(node);
  }
  return document;
}

const savedScore = {
  status: 'saved', saved_at: '2032-01-07T15:00:00Z', file_id: FILE,
  notes_score: 8, revision_score: 5,
  counts: {NOTES: {done: 4, required: 5}, BANK: {done: 0, required: 0}, DRAFT: {done: 1, required: 2}},
  computed_at: '2032-01-07T15:00:00Z', file_updated: '2032-01-06T15:00:00Z',
};

function fixtures() {
  const submission = {
    matched: true, first: 'Ann', last: 'Avery', section: 'A4 Fictional',
    email: 'fictional@school.example', submitted_at: '2032-01-06T15:00:00Z', earlier: [], typed: [],
    files: [{id: FILE, name: 'Fictional cell work', url: 'https://drive.google.com/open?id=' + FILE,
      mime: 'application/vnd.google-apps.presentation', updated: '2032-01-06T15:00:00Z'}],
  };
  const form = {form_id: FORM, label: 'Fictional cells', url: 'https://docs.google.com/forms/d/' + FORM + '/edit',
    students: [submission], late: [], missing: [], excused: [], elsewhere: []};
  return {
    dashboard: {schema_version: 'hub-dashboard-records/v1', checked_at: '2032-01-07T15:01:00Z',
      records: [{key: 'operations', label: 'Operations', configured: true, url: 'https://docs.google.com/spreadsheets/d/FICTIONALOPS001/edit'}],
      runs: {status: 'available', items: [{id: 'fictional-run-1', work: 'Fictional filing',
        started_at: '2032-01-07T14:50:00Z', finished_at: '2032-01-07T15:00:00Z',
        status: 'partial', summary: 'Saved two fictional reports.', completed: 2, remaining: 1,
        unfinished: 'One exact source is unavailable.', next_action: 'Restore source access.', owner: 'Maintenance worker',
        links: [{label: 'Fictional output', url: 'https://drive.google.com/drive/folders/FICTIONALOUTPUT001'}]}]}},
    day: {date: '2032-01-07', weekday: 'Tue', week: {label: 'Wk 03', monday: '2032-01-05'}, problems: [],
      agenda: {title: 'Fictional Agenda', url: 'https://docs.google.com/spreadsheets/d/FICTIONALAGENDA001/edit'},
      classes: [{key: 'biology', name: 'Biology', lessons: [{row: 2, week: 'Wk 03', topic: 'Fictional cell structures',
        today: {plan: 'Compare two fictional cell models.', note: ''}, todo: '', prep: '',
        materials: [{label: 'Fictional lesson deck', url: 'https://docs.google.com/presentation/d/FICTIONALDECK001/edit'}],
        plans: [{day: 'Tue', plan: 'Compare two fictional cell models.', note: ''}], forms: [],
        editable: {topic: true, 'plan:Tue': true}, links: {}}]}]},
    weeks: {class_key: 'biology', current: 'Wk 03', weeks: [{label: 'Wk 03', topic: 'Fictional cells', forms: 1}],
      sections: ['A4 Fictional'], agenda: {url: 'https://docs.google.com/spreadsheets/d/FICTIONALAGENDA001/edit'}},
    work: {class_key: 'biology', class_name: 'Biology', week: 'Wk 03', problems: [], other_files: [],
      roster_size: 1, folders: [], lessons: [{topic: 'Fictional cell structures', forms: [form]}]},
    marks: {configured: true, url: null, entered_at: null, feedback_at: null, problem: null},
  };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

async function boot(hash = '#today', overrides = {}) {
  const document = documentFromHTML(markup), data = {...fixtures(), ...overrides};
  const requests = [], posts = [], errors = [], handlers = new Map(), storage = new Map();
  const location = {hash, origin: ORIGIN, href: ORIGIN + '/v2/' + hash};
  let pending = 0;
  const window = {
    addEventListener(name, handler) { handlers.set(name, [...(handlers.get(name) || []), handler]); },
    postMessage(message, origin) { posts.push({message, origin}); },
    location, confirm() { throw Error('Opening a page must not request a write confirmation'); },
    open() { throw Error('Opening a dashboard page must not open a generated document'); },
  };
  const fetch = async (url, options = {}) => {
    const request = {url: String(url), method: options.method || 'GET'};
    requests.push(request); pending++;
    try {
      if (request.method !== 'GET') throw Error('Unexpected mutation: ' + request.method + ' ' + request.url);
      const parsed = new URL(request.url, ORIGIN + '/v2/'), path = parsed.pathname;
      let value;
      if (path === '/v2/api/dashboard/records') value = data.dashboard;
      else if (path === '/v2/api/day') value = data.day;
      else if (path === '/v2/api/weeks') value = data.weeks;
      else if (path === '/v2/api/work') value = data.work;
      else if (path === '/v2/api/ledger' || path === '/v2/api/record') value = data.marks;
      else if (path === '/v2/api/scores/saved') {
        if (parsed.searchParams.has('file')) throw Error('Dashboard must read saved grades once per assignment');
        const form = data.work.lessons.flatMap(lesson => lesson.forms).find(item => item.form_id === parsed.searchParams.get('form'));
        const rows = [...(form?.students || []), ...(form?.late || []), ...(form?.scoring_students || []),
          ...(data.work.entry_rechecks?.[form?.form_id]?.rows || [])];
        const files = [...new Set(rows.flatMap(row => row.files || []).map(file => file.id))];
        value = data.savedBatch || {schema_version: 'hub-saved-completion-list/v1', status: 'available', checked_at: '2032-01-07T15:01:00Z',
          results: Object.fromEntries(files.map(file => [file, data.savedScores?.[file] || data.savedScore || {...savedScore, file_id: file}]))};
      }
      else throw Error('Unexpected page-open request: ' + path);
      if (value instanceof Error) return {ok: false, status: 503, json: async () => ({error: value.message})};
      return {ok: true, status: 200, json: async () => structuredClone(value)};
    } catch (error) { errors.push(error); throw error; }
    finally { pending--; }
  };
  const context = vm.createContext({document, window, location, fetch, Node: Element,
    history: {replaceState(_, __, url) { location.hash = new URL(url, ORIGIN + '/v2/').hash; }},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value))},
    URL, URLSearchParams, Date, Intl, crypto: webcrypto, console,
    setTimeout, clearTimeout, AbortController, TextDecoder,
    requestAnimationFrame: callback => callback(),
    navigator: {clipboard: {writeText() { throw Error('Opening a page must not copy a prompt'); }}},
  });
  vm.runInContext(application + '\nglobalThis.__dashboardState = {work, plans, helper};', context,
    {filename: 'production-app.js', timeout: 2000});
  async function settle() {
    // Chained promise continuations render after fetch itself has completed.
    for (let i = 0; i < 12; i++) await tick();
    assert.equal(pending, 0);
    assert.deepEqual(errors.map(error => error.message), []);
  }
  async function navigate(next) {
    location.hash = next;
    for (const handler of handlers.get('hashchange') || []) await handler({type: 'hashchange'});
    await settle();
  }
  async function message(value, origin = ORIGIN) {
    for (const handler of handlers.get('message') || []) await handler({source: window, origin, data: value});
    await settle();
  }
  await settle();
  return {document, context, requests, posts, errors, data, navigate, message, settle};
}

const paths = app => app.requests.map(request => new URL(request.url, ORIGIN + '/v2/').pathname);

test('complete application boots Today and reads run records without starting work', async () => {
  const app = await boot();
  assert.equal(app.document.getElementById('page-today').hidden, false);
  assert.ok(paths(app).length > 0);
  assert.deepEqual([...new Set(paths(app))], ['/v2/api/dashboard/records']);
  assert.match(app.document.getElementById('today-body').textContent, /Fictional filing|Saved two fictional reports/);
  assert.match(app.document.getElementById('today-body').textContent, /Partial.*Saved two fictional reports.*Completed: 2.*Unfinished: 1/);
  assert.match(app.document.getElementById('today-body').textContent, /One exact source is unavailable.*Restore source access.*Maintenance worker/);
  assert.ok(elements(app.document.getElementById('today-body')).some(node => node.tagName === 'A'
    && node.getAttribute('href')?.includes('FICTIONALOUTPUT001')));
  assert.ok(app.posts.some(({message, origin}) => message.type === 'status' && origin === ORIGIN),
    'the preserved Helper bridge announces its status request');
});

test('Records aliases and Lessons execute only their own readers with the real page DOM', async () => {
  const app = await boot('#records');
  for (const hash of ['#records', '#data', '#routine', '#system']) {
    app.requests.length = 0; await app.navigate(hash);
    assert.equal(app.document.getElementById('page-records').hidden, false, hash);
    assert.equal(app.document.getElementById('page-today').hidden, true, hash);
    assert.deepEqual([...new Set(paths(app))], ['/v2/api/dashboard/records'], hash);
    assert.match(app.document.getElementById('records-body').textContent, /Operations/);
  }
  app.requests.length = 0; await app.navigate('#plans');
  assert.equal(app.document.getElementById('page-plans').hidden, false);
  assert.deepEqual([...new Set(paths(app))], ['/v2/api/day']);
  assert.match(app.document.getElementById('cards').textContent, /Fictional cell structures/);
  assert.match(app.document.getElementById('cards').textContent, /Compare two fictional cell models/);
});

test('Student work reads exact saved grades and latest-work links without calculating scores', async () => {
  const app = await boot('#work/biology/Wk%2003');
  assert.equal(app.document.getElementById('page-work').hidden, false);
  assert.ok(paths(app).includes('/v2/api/scores/saved'));
  assert.equal(paths(app).filter(path => path === '/v2/api/scores/saved').length, 1,
    'one saved summary read serves this assignment');
  assert.ok(paths(app).every(path => ['/v2/api/weeks', '/v2/api/work', '/v2/api/ledger', '/v2/api/record', '/v2/api/scores/saved'].includes(path)));
  assert.ok(app.requests.every(request => request.method === 'GET'));
  const saved = app.context.__dashboardState.work.scores[FORM][FILE];
  assert.equal(saved.notes_score, 8); assert.equal(saved.revision_score, 5);
  const body = app.document.getElementById('work-body');
  assert.match(body.textContent, /Avery/);
  assert.ok(elements(body).some(node => node.tagName === 'A' && node.getAttribute('href')?.includes(FILE)));
  assert.doesNotMatch(body.textContent, /Scoring \d+ of/);
});

test('missing saved results remain unavailable and do not become zeros or trigger grading', async () => {
  const app = await boot('#work/biology/Wk%2003', {savedScore: {status: 'not_graded',
    problem: 'No saved completion result is available for this exact source.'}});
  assert.ok(paths(app).includes('/v2/api/scores/saved'));
  assert.ok(!paths(app).includes('/v2/api/score'));
  const record = app.context.__dashboardState.work.scores[FORM][FILE];
  assert.equal(record.notes_score, undefined); assert.equal(record.revision_score, undefined);
  assert.match(app.document.getElementById('work-body').textContent, /No saved completion result|not.*graded|not.*saved|unavailable/i);
});

test('Helper status messages survive page simplification without starting a writer', async () => {
  const app = await boot('#records');
  // Use the exact channel from the real production declaration rather than
  // assuming its spelling in the behavior assertion.
  const channel = vm.runInContext('HELPER.reply', app.context);
  await app.message({channel, type: 'status', code: 'helper_ready', helper_version: '0.5.34', results: [], morning: null});
  assert.equal(app.context.__dashboardState.helper.state, 'ready');
  assert.equal(app.context.__dashboardState.helper.version, '0.5.34');
  assert.ok(app.requests.every(request => request.method === 'GET'));
  assert.deepEqual([...new Set(paths(app))], ['/v2/api/dashboard/records']);
});

test('newly graded filter uses the actual saved grading date without a new read or assessment', async () => {
  const data = fixtures(), form = data.work.lessons[0].forms[0];
  const earlier = structuredClone(form.students[0]);
  earlier.first = 'Bea'; earlier.last = 'Birch'; earlier.files[0].id = 'FICTIONALWORK002';
  earlier.files[0].url = 'https://drive.google.com/open?id=FICTIONALWORK002';
  // Both source files have the same old modification time. Only the saved
  // grading time differs, so neither source freshness nor viewing can pass.
  form.students.push(earlier);
  const app = await boot('#work/biology/Wk%2003', {work: data.work, savedScores: {
    [FILE]: {...savedScore, saved_at: new Date().toISOString()},
    FICTIONALWORK002: {...savedScore, file_id: 'FICTIONALWORK002', saved_at: '2001-01-01T15:00:00Z'},
  }});
  assert.match(app.document.getElementById('work-body').textContent, /Avery.*Birch/);
  assert.equal(paths(app).filter(path => path === '/v2/api/scores/saved').length, 1,
    'multiple submitted files share one assignment read');
  const before = app.requests.length;
  const filter = app.document.getElementById('work-display');
  filter.value = 'new'; await filter.dispatch('change'); await app.settle();
  const text = app.document.getElementById('work-body').textContent;
  assert.match(text, /Avery/); assert.doesNotMatch(text, /Birch/);
  assert.equal(app.requests.length, before);
});

test('stale saved grades never display historical numbers as current results', async () => {
  const app = await boot('#work/biology/Wk%2003', {savedScore: {...savedScore,
    status: 'stale', problem: 'The exact original revision changed after this saved result.'}});
  const table = elements(app.document.getElementById('work-body')).find(node => node.tagName === 'TABLE');
  assert.match(table.textContent, /stale/i);
  const cells = elements(table).filter(node => node.tagName === 'TD').map(node => node.textContent);
  assert.ok(!cells.includes('8') && !cells.includes('5'), 'stale numerical scores are not shown as current grades');
  assert.ok(!paths(app).includes('/v2/api/score'));
});

test('an unavailable saved-grade batch cannot lend a result from another source', async () => {
  for (const overrides of [
    {savedBatch: {schema_version: 'hub-saved-completion-list/v1', status: 'unavailable',
      checked_at: '2032-01-07T15:01:00Z', results: {[FILE]: savedScore}}},
    {savedScore: {...savedScore, file_id: 'FICTIONALOTHER001'}},
  ]) {
    const app = await boot('#work/biology/Wk%2003', overrides);
    const body = app.document.getElementById('work-body');
    assert.match(body.textContent, /could not be verified/);
    const cells = elements(body).filter(node => node.tagName === 'TD').map(node => node.textContent);
    assert.ok(!cells.includes('8') && !cells.includes('5'));
    assert.equal(paths(app).filter(path => path === '/v2/api/scores/saved').length, 1);
    assert.ok(!paths(app).includes('/v2/api/score'));
  }
});

test('an unavailable run-record read does not claim a completed run', async () => {
  const app = await boot('#today', {dashboard: new Error('Run records unavailable')});
  const text = app.document.getElementById('today-body').textContent + app.document.getElementById('today-status').textContent;
  assert.match(text, /unavailable|could not|couldn.t/i);
  assert.doesNotMatch(text, /all.*completed|successfully completed/i);
  assert.deepEqual([...new Set(paths(app))], ['/v2/api/dashboard/records']);
});
