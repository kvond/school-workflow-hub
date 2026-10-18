// Synthetic Week ahead rendering through the production functions; no network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const prefix = source.slice(0, source.indexOf('function issueAction('));
const weekAhead = source.slice(source.indexOf('// ---------- Week ahead: lesson preflight'), source.indexOf('async function loadToday('));
const walk = node => [node, ...(node.children || []).filter(child => child instanceof Element).flatMap(walk)];

function boot(responses) {
  const nodes = new Map(), requests = [];
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!nodes.has(id)) nodes.set(id, new Element('div'));
    return nodes.get(id);
  }};
  const context = vm.createContext({document, Node: Element, fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => responses(url, options)};
  }});
  vm.runInContext(prefix + '\nasync function postJSON(url, body) { const r = await fetch(url, {method: "POST", body: JSON.stringify(body)}); return r.json(); }\n' +
    weekAhead + '\nglobalThis.ui = {loadWeekAhead, renderWeekAhead, weekAhead};', context);
  return {ui: context.ui, document, requests};
}

const CHECKED = '2032-01-05T12:00:00+00:00';
const item = overrides => ({kind: 'calendar_conflict', status: 'needs_decision', blocking: true, class: 'Biology',
  sections: ['A4 Biology'], week: 'Wk 09', date: '2032-01-09', lesson: 'Fictional respiration', slides: [],
  source_url: 'https://docs.google.com/spreadsheets/d/FICTIONAL/edit#gid=2&range=D5',
  reason: 'The Fri plan says the class doesn’t meet.', next_action: 'Correct the Weeks tab.',
  fingerprint: 'a'.repeat(64), first_seen: '2032-01-01T12:00:00+00:00', evidence: {}, ...overrides});
const reply = {status: 'needs_decision', start: '2032-01-05', last_successful_check: CHECKED,
  weeks: [{label: 'Wk 09', monday: '2032-01-05', checked_at: CHECKED, status: 'needs_decision'}],
  exceptions: [
    item({}),
    item({kind: 'unlinked_deck_in_folder', blocking: false, fingerprint: 'b'.repeat(64), reason: 'Old note about a spare deck.'}),
    item({kind: 'deck_outside_folder', blocking: false, fingerprint: 'c'.repeat(64), first_seen: CHECKED, reason: 'Fresh note.'}),
    item({kind: 'visible_link_not_clickable', status: 'repaired_and_verified', blocking: false, fingerprint: 'd'.repeat(64), reason: 'Resolved.'}),
    item({kind: 'visible_link_not_clickable', status: 'repairable', fingerprint: 'e'.repeat(64), slides: [], reason: 'Label L5 is plain text.'})],
  lessons: [{class: 'Biology', week: 'Wk 09', topic: 'Fictional respiration', status: 'needs_decision', reviews: {}}],
  supplies: [{item: 'Active dry yeast', also_named: ['yeast'], status: 'on order (not verified in hand)', quantity: 'quantity not stated',
              lessons: ['Fictional respiration'], use_dates: ['2032-01-05'], prep_by: null}],
  packet: {status: 'ready', pdf_url: '/v2/api/weekly-preflight/packet.pdf?id=weeklylessonpacket_' + 'f'.repeat(64)}};

test('only unresolved actionable items show; unchanged notes collapse; resolved items are only counted', async () => {
  const {ui, document, requests} = boot(() => reply);
  await ui.loadWeekAhead();
  const box = document.getElementById('today-week-ahead');
  const details = box.children[0];
  assert.equal(details.tagName, 'details');
  const lists = walk(details).filter(node => node.className === 'attention-list');
  const visible = lists[0].children.map(li => li.textContent);
  assert.equal(visible.length, 3);
  assert.match(visible.join('\n'), /The Fri plan says/);
  assert.match(visible.join('\n'), /Fresh note/);
  assert.match(visible.join('\n'), /Label L5 is plain text/);
  assert.doesNotMatch(visible.join('\n'), /Resolved\.|Old note/);
  const quiet = walk(details).find(node => node.tagName === 'summary' && /Earlier notes, unchanged \(1\)/.test(node.textContent));
  assert.ok(quiet);
  assert.match(details.textContent, /1 repaired and read back/);
  assert.ok(walk(details).some(node => node.tagName === 'a' && node.href === reply.packet.pdf_url
    && /Open Monday packet \(PDF\)/.test(node.textContent)));
  assert.ok(walk(details).some(node => node.tagName === 'a' && node.href === reply.exceptions[0].source_url));
  assert.match(details.textContent, /Biology · A4 Biology · Friday 2032-01-09 · Fictional respiration/);
  assert.match(details.textContent, /Last successful check/);
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /^api\/weekly-preflight\?start=\d{4}-\d{2}-\d{2}&weeks=1$/);
});

test('a repair is previewed before it is applied, and the supply list never claims stock in hand', async () => {
  const posts = [];
  const {ui, document} = boot((url, options) => {
    if (options?.method === 'POST') {
      const body = JSON.parse(options.body); posts.push(body);
      return body.dry_run ? {status: 'planned', cell: 'L5', formula: '=IF($P5="","—",HYPERLINK($P5,"Student copy"))'}
        : {status: 'repaired_and_verified', changed: true};
    }
    return reply;
  });
  await ui.loadWeekAhead();
  const find = label => walk(document.getElementById('today-week-ahead')).find(node => node.tagName === 'button' && node.textContent === label);
  await find('Preview repair').listeners.click();
  assert.deepEqual(posts, [{fingerprint: 'e'.repeat(64), dry_run: true}]);
  assert.match(document.getElementById('today-week-ahead').textContent, /Writes =IF\(\$P5.*into L5 only/);
  await find('Apply this repair').listeners.click();
  assert.deepEqual(posts[1], {fingerprint: 'e'.repeat(64), dry_run: false});
  assert.match(document.getElementById('today-week-ahead').textContent, /Repaired and read back/);
  await find('Supply list').listeners.click();
  const text = document.getElementById('today-week-ahead').textContent;
  assert.match(text, /Active dry yeast \(also: yeast\)on order \(not verified in hand\)/);
  assert.doesNotMatch(text.replaceAll('(not verified in hand)', ''), /in hand/);
});

test('an unfinished check says so and cannot prepare a packet', async () => {
  const {ui, document} = boot(() => ({...reply, status: 'incomplete', packet: {status: 'not_built'}}));
  await ui.loadWeekAhead();
  const box = document.getElementById('today-week-ahead');
  assert.match(box.textContent, /did not finish, so nothing in it counts as ready/);
  const prepare = walk(box).find(node => node.tagName === 'button' && node.textContent === 'Prepare Monday packet');
  assert.equal(prepare.disabled, '');
});

test('the print list shows a web page as a print instruction with its link', async () => {
  const web = 'https://science.example.org/activities/yeast-inflated-balloons';
  const prints = [
    {text: 'Science World — Yeast-Inflated Balloons (1 per group)', class: 'Biology', lesson: 'Fictional respiration',
     per_section: {'A4 Biology': 6}, basis: '1 per group of 4: 1 x ceil(students/4)', optional: false, teacher_only: false,
     file: null, web: {url: web, from: "the deck's link list"}, instruction: 'print from web: ' + web},
    {text: 'CER worksheet (1 per student)', class: 'Biology', lesson: 'Fictional respiration', per_section: {'A4 Biology': 22},
     basis: '1 per student (current roster)', optional: false, teacher_only: false,
     file: {name: 'Fictional CER worksheet.pdf', pages: 1, sides: 'single-sided'}}];
  const {ui, document} = boot(() => ({...reply, print_items: prints}));
  await ui.loadWeekAhead();
  const box = document.getElementById('today-week-ahead');
  const find = label => walk(box).find(node => node.tagName === 'button' && node.textContent === label);
  await find('Print list').listeners.click();
  assert.match(box.textContent, /Print from web: https:\/\/science\.example\.org\/activities\/yeast-inflated-balloons/);
  assert.ok(walk(box).some(node => node.tagName === 'a' && node.href === web));
  assert.match(box.textContent, /A4 Biology: 6 \(1 per group of 4/);
  assert.match(box.textContent, /Fictional CER worksheet\.pdf · 1 page, single-sided/);
  assert.ok(find('Hide print list'));
});

test('the print list has one row per lesson, each file linked, and one link to the lesson folder', async () => {
  const folder = 'https://drive.google.com/drive/folders/FictionalLessonFolder';
  const fileURL = 'https://drive.google.com/file/d/FictionalWorksheet/view';
  const lesson = {class_key: 'biology', class: 'Biology', week: 'Wk 09', row: 12, topic: 'Fictional respiration', status: 'ready',
    reviews: {}, sources: [{id: 'FictionalLessonFolder', roles: ['folder'], accessible: true, trashed: false, url: folder}]};
  const other = {class_key: 'forensics', class: 'Forensics', week: 'Wk 09', row: 7, topic: 'Fictional fibers', status: 'ready',
    reviews: {}, sources: []};
  const base = {class_key: 'biology', class: 'Biology', week: 'Wk 09', row: 12, lesson: 'Fictional respiration',
    dates: ['2032-01-06'], per_section: {'A4 Biology': 22}, basis: '1 per student (current roster)', optional: false, teacher_only: false};
  const prints = [
    {...base, text: 'CER worksheet', file: {name: 'Fictional CER worksheet.pdf', url: fileURL, pages: 2, sides: 'double-sided'}},
    {...base, text: 'Answer key', teacher_only: true, file: {name: 'Fictional key.docx', url: fileURL, print_from_folder: true}},
    {...base, class_key: 'forensics', class: 'Forensics', row: 7, lesson: 'Fictional fibers', text: 'Fiber chart', file: null}];
  const {ui, document} = boot(() => ({...reply, lessons: [lesson, other], print_items: prints}));
  await ui.loadWeekAhead();
  const box = document.getElementById('today-week-ahead');
  await walk(box).find(node => node.tagName === 'button' && node.textContent === 'Print list').listeners.click();
  const table = walk(box).find(node => node.className === 'cockpit-table print-list');
  const rows = walk(table).filter(node => node.tagName === 'tr').slice(1);
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /Biology · Fictional respiration/);
  assert.match(rows[0].textContent, /CER worksheet — A4 Biology: 22 \(1 per student \(current roster\)\) — Fictional CER worksheet\.pdf · 2 pages, double-sided/);
  assert.match(rows[0].textContent, /Answer key · TEACHER ONLY/);
  assert.ok(walk(rows[0]).some(node => node.tagName === 'a' && node.href === fileURL && node.textContent === 'Fictional CER worksheet.pdf'));
  assert.ok(walk(rows[0]).some(node => node.tagName === 'a' && node.href === folder && /Open folder/.test(node.textContent)));
  assert.match(rows[1].textContent, /Fiber chart — .*No file in the lesson folder/);
  assert.match(rows[1].textContent, /No lesson folder linked/);
});
