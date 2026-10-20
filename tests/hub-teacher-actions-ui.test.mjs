import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  addEventListener() {}
  setAttribute(name, value) { this[name] = value; }
}
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const DAY = '2032-01-05';
function boot() {
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : ['2032-01-06T03:00:00Z'])); }
    static now() { return new Date('2032-01-06T03:00:00Z').getTime(); }
  }
  const context = vm.createContext({Date: Clock, Node: Element, document: {createElement: tag => new Element(tag)}});
  vm.runInContext(source.slice(0, source.indexOf('function issueAction(')).replace(/const todayISO = .*?;\n/, `const todayISO = () => '${DAY}';\n`) +
    source.slice(source.indexOf('const TEACHER_AGENDA_CHOICES'), source.indexOf('async function printTodayFeedback(')) +
    source.slice(source.indexOf('function renderTodayFeedback('), source.indexOf('function renderFeedbackTools(')) +
    `\nglobalThis.ui = {renderTodayFeedback, renderNextLessons, cockpit};`, context);
  context.ui.cockpit.teacherLinks = {feedback_list_url: 'https://docs.google.com/spreadsheets/d/FICTIONALCHECKLIST/edit'};
  return context.ui;
}
const report = {status: 'needs_decision', last_successful_check: DAY + 'T12:00:00Z', exceptions: [
  {kind: 'deck_unreadable', status: 'inaccessible', reason: 'A source failed.', next_action: 'Repair link.', source_url: 'https://example.test/deck'},
  {kind: 'needs_embedded_codes', status: 'needs_decision', reason: 'Code repair.', next_action: 'Run embed tool.', source_url: 'https://example.test/deck'},
  {kind: 'needs_content', status: 'needs_decision', class: 'Biology', sections: ['A4'], week: 'Wk 09', lesson: 'Fictional cells',
    date: DAY, reason: 'Tuesday plan has two alternatives.', next_action: 'Choose microscopy or the cell-model activity.', source_url: 'https://example.test/agenda#cell'},
]};
test('only exact teaching choices appear, with context and one concrete action; system faults stay out', () => {
  const ui = boot(), before = JSON.stringify(report);
  const rendered = ui.renderNextLessons({}, null, undefined, null, report);
  assert.match(rendered.textContent, /1 teaching decision.*Biology · A4 · Wk 09 · Fictional cells · 2032-01-05.*Your action: Choose microscopy or the cell-model activity/);
  assert.equal(walk(rendered).filter(n => n.tagName === 'a')[0].href, 'https://example.test/agenda#cell');
  assert.doesNotMatch(rendered.textContent, /Run embed|Repair link|A source failed/);
  assert.equal(JSON.stringify(report), before);
});
test('failed, stale or incomplete checks cannot claim that no teacher action is needed', () => {
  const ui = boot();
  for (const data of [null, {...report, status: 'incomplete'}, {...report, last_successful_check: '2032-01-04T12:00:00Z'}]) {
    const rendered = ui.renderNextLessons({}, null, undefined, null, data);
    assert.match(rendered.textContent, /Teacher-action check pending/);
    assert.doesNotMatch(rendered.textContent, /Nothing needs your decision|Choose microscopy|No fix needed/);
  }
});
test('fresh technical-only results do not make a teacher repair checklist', () => {
  const ui = boot();
  assert.match(ui.renderNextLessons({}, null, undefined, null, {...report, exceptions: report.exceptions.slice(0, 2)}).textContent,
    /Nothing needs your decision.*Technical checks and link repairs are handled by the repair worker/);
});
test('freshness uses Eastern dates, rejects malformed or future timestamps, and retains Friday decisions', () => {
  const ui = boot();
  const friday = {...report.exceptions[2], kind: 'calendar_friday_unknown', next_action: 'Confirm whether Friday is an A or B day.'};
  const fresh = {...report, last_successful_check: '2032-01-06T02:00:00Z', exceptions: [friday]};
  assert.match(ui.renderNextLessons({}, null, undefined, null, fresh).textContent, /Your action: Confirm whether Friday is an A or B day/);
  for (const timestamp of ['2032-01-05T03:00:00Z', '2032-01-06T04:00:00Z', 'invalid', null]) {
    const rendered = ui.renderNextLessons({}, null, undefined, null, {...fresh, last_successful_check: timestamp});
    assert.match(rendered.textContent, /Teacher-action check pending/);
    assert.doesNotMatch(rendered.textContent, /Nothing needs your decision|Your action/);
  }
});
test('Lessons keeps one navigable checklist link without reporting freshness or delivery', () => {
  const ui = boot(), data = {requested_date: DAY, date: DAY, current_sources_verified: true, files: [{id: 'report1'}],
    delivery: {printed_file_ids: ['report1'], handed_file_ids: ['report1']}, email_delivery: {status: 'sent'}};
  ui.cockpit.feedbackData = data;
  const before = JSON.stringify(data), rendered = ui.renderTodayFeedback();
  const links = walk(rendered).filter(n => n.tagName === 'a');
  assert.equal(links.length, 1);
  assert.equal(links[0].textContent, 'Feedback checklist');
  assert.equal(links[0].href, ui.cockpit.teacherLinks.feedback_list_url);
  assert.equal(walk(rendered).filter(n => n.tagName === 'button').length, 0);
  assert.doesNotMatch(rendered.textContent, /Daily feedback document|Updated|Not updated|Checking|not verified|Print|Handed|email|pending report/);
  assert.equal(JSON.stringify(data), before);
});
test('Lessons retains the existing checklist fallback and marks an unavailable link explicitly', () => {
  const ui = boot();
  ui.cockpit.teacherLinks = null;
  ui.cockpit.learningData = {checklist_url: 'https://example.test/existing-checklist'};
  const rendered = ui.renderTodayFeedback();
  assert.equal(walk(rendered).find(n => n.tagName === 'a').href, ui.cockpit.learningData.checklist_url);
  ui.cockpit.learningData = null;
  const unavailable = ui.renderTodayFeedback();
  assert.equal(walk(unavailable).filter(n => n.tagName === 'a').length, 0);
  assert.equal(unavailable.textContent, 'Feedback checklist link unavailable');
});
