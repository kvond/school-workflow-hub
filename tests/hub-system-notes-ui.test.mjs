import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const declarations = source.slice(0, source.indexOf('// ---------- Hub v3')) + source.slice(
  source.indexOf('// ---------- Shared System notes'), source.indexOf('// ---------- Routine:'));
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
function boot(failNotes = false) {
  const box = new Element('div'), calls = [];
  const context = vm.createContext({Node: Element, document: {getElementById: () => box, createElement: tag => new Element(tag)},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (url === 'api/system/notes' && failNotes) throw new Error('Unavailable');
      return {ok: true, json: async () => url === 'api/system/notes'
        ? {text: 'Preserve entire earlier teacher note', revision: 'a'.repeat(64)}
        : {system_notes_url: 'https://app.slack.com/client/T123ABC/C123ABC'}};
    }});
  vm.runInContext(declarations + '\nglobalThis.ui = {systemNotes, loadSystemNotes};', context);
  return {box, calls, ui: context.ui};
}
test('Slack is the operational note destination and earlier private notes stay read-only and collapsed', async () => {
  const {box, calls, ui} = boot(); await ui.loadSystemNotes();
  assert.equal(walk(box).find(n => n.tagName === 'a').href, 'https://app.slack.com/client/T123ABC/C123ABC');
  const history = walk(box).find(n => n.tagName === 'details');
  assert.equal(history.open, undefined);
  assert.match(history.textContent, /Preserve entire earlier teacher note/);
  assert.equal(walk(box).filter(n => ['textarea', 'button'].includes(n.tagName)).length, 0);
  assert.ok(calls.every(c => !c.options.method || c.options.method === 'GET'));
  await ui.loadSystemNotes(); assert.equal(calls.length, 2);
});
test('failed private-note read leaves the Slack link usable and never writes or claims migration', async () => {
  const {box, calls, ui} = boot(true); await ui.loadSystemNotes();
  assert.equal(ui.systemNotes.loaded, false);
  assert.match(box.textContent, /saved contents are unchanged/);
  assert.equal(walk(box).filter(n => n.tagName === 'a').length, 1);
  assert.ok(calls.every(c => !c.options.method || c.options.method === 'GET'));
});
