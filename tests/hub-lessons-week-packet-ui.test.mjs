import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../src/hub_v2/static/index.html', import.meta.url), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const declarations = source.slice(0, source.indexOf('// ---------- Hub v3'))
  + section('// ---------- Lessons → Week packet', '// ---------- Lessons → Notes');
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const find = (root, tag, text) => walk(root).find(n => n.tagName === tag && (!text || n.textContent.includes(text)));

const READY = {status: 'ready', id: 'weeklylessonpacket_' + 'a'.repeat(64), start: '2026-10-19', weeks: 1,
  name: '2026-10-19 - Weekly lesson packet - aaaaaaaaaa.pdf', pages: 23, compiled_at: '2026-10-18T15:00:00Z',
  pdf_url: '/v2/api/weekly-preflight/packet.pdf?id=weeklylessonpacket_' + 'a'.repeat(64)};

function boot(reply) {
  const calls = [];
  const box = new Element('section');
  const context = vm.createContext({Node: Element, setTimeout, Intl, Date,
    document: {getElementById: id => id === 'week-packet' ? box : null, createElement: tag => new Element(tag)},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET'});
      const body = reply(url);
      return body instanceof Error ? {ok: false, status: 503, json: async () => ({error: body.message})}
        : {ok: true, json: async () => body};
    }});
  vm.runInContext(declarations + '\nglobalThis.ui = {loadLessonPacket};', context);
  return {calls, box, ui: context.ui};
}

test('Lessons has a Week packet slot above the notes', () => {
  assert.ok(html.indexOf('id="week-packet"') > html.indexOf('id="page-plans"'));
  assert.ok(html.indexOf('id="week-packet"') < html.indexOf('id="system-notes"'));
});

test('The saved packet for the shown week links straight to its PDF with one read-only GET', async () => {
  const {calls, box, ui} = boot(() => ({status: 'complete', packet: READY}));
  await ui.loadLessonPacket('2026-10-19');
  assert.deepEqual(calls, [{url: 'api/weekly-preflight?start=2026-10-19&weeks=1', method: 'GET'}]);
  const a = find(box, 'a', 'Open week packet');
  assert.equal(a.href, READY.pdf_url);
  assert.match(box.textContent, /23 pages/);
});

test('A 2-week packet from the previous Monday also counts for this week', async () => {
  const two = {...READY, start: '2026-10-12', weeks: 2};
  const {calls, box, ui} = boot(url => url.includes('start=2026-10-12&weeks=2')
    ? {packet: two} : {packet: {status: 'not_built'}});
  await ui.loadLessonPacket('2026-10-19');
  assert.equal(calls.length, 3);
  assert.ok(calls.every(c => c.method === 'GET'));
  assert.ok(find(box, 'a', 'Open week packet'));
  assert.match(box.textContent, /covers 2 weeks/);
});

test('No saved packet says so and points to Today → Week ahead; nothing is built', async () => {
  const {calls, box, ui} = boot(() => ({packet: {status: 'not_built'}}));
  await ui.loadLessonPacket('2026-10-19');
  assert.ok(calls.every(c => c.method === 'GET' && !c.url.includes('/packet')));
  assert.match(box.textContent, /No week packet is saved for Oct 19 – 23, 2026 yet/);
  assert.equal(find(box, 'a', 'Today → Week ahead').href, '#today');
});

const NO_REBUILD = /No week packet is saved|Prepare Monday packet/;

test('A failed read says the packet is unknown, never "not saved", and asks for no rebuild', async () => {
  const {calls, box, ui} = boot(() => new Error('The Hub\'s private state is unavailable. Nothing was checked or changed.'));
  await ui.loadLessonPacket('2026-10-19');
  assert.equal(calls.length, 3);
  assert.match(box.textContent, /Couldn't check for a saved week packet for Oct 19 – 23, 2026: The Hub's private state is unavailable/);
  assert.match(box.textContent, /One may already be saved/);
  assert.doesNotMatch(box.textContent, NO_REBUILD);
});

test('Mixed reads: one failure plus "not built" elsewhere is still unknown, not absent', async () => {
  const {box, ui} = boot(url => url.includes('start=2026-10-12&weeks=2')
    ? new Error('Read timed out.') : {packet: {status: 'not_built'}});
  await ui.loadLessonPacket('2026-10-19');
  assert.match(box.textContent, /Couldn't check .*Read timed out/);
  assert.doesNotMatch(box.textContent, NO_REBUILD);
});

test('Mixed reads: a failure does not hide a packet found by another read', async () => {
  const two = {...READY, start: '2026-10-12', weeks: 2};
  const {box, ui} = boot(url => url.includes('start=2026-10-12&weeks=2')
    ? {packet: two} : new Error('Read timed out.'));
  await ui.loadLessonPacket('2026-10-19');
  assert.ok(find(box, 'a', 'Open week packet'));
  assert.doesNotMatch(box.textContent, /Couldn't check/);
});

test('An unexpected PDF link is never rendered and is not reported as absent', async () => {
  const {box, ui} = boot(() => ({packet: {...READY, pdf_url: 'https://example.com/x.pdf'}}));
  await ui.loadLessonPacket('2026-10-19');
  assert.equal(find(box, 'a', 'Open week packet'), undefined);
  assert.match(box.textContent, /not a Hub packet link/);
  assert.doesNotMatch(box.textContent, NO_REBUILD);
});
