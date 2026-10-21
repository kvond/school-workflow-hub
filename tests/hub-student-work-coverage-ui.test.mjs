// The grade view renders verified saved values; partial intake never invents zeros.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.className = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const extract = name => {
  const start = source.indexOf(`function ${name}(`); assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const functions = ['fileList', 'entryTime', 'savedGradeRows', 'verifiedSavedGrade', 'newlyGraded', 'renderSavedForm', 'renderWork'].map(extract).join('\n');
const prelude = source.slice(0, source.indexOf('// ---------- Hub v3'));
const received = {matched: true, last: 'Avery', first: 'Ann', section: 'Fictional A2', files: [{id: 'FICTIONALFILE001', name: 'Original work', url: 'https://drive.google.com/open?id=FICTIONALFILE001'}]};
const FORM = 'FICTIONALFORM001';
const saved = {file_id: received.files[0].id, status: 'saved', saved_at: '2032-01-07T15:00:00Z', notes_score: 8, revision_score: 6};
const walk = element => [element, ...element.children.filter(child => child instanceof Element).flatMap(walk)];
function boot({data = {}, form = {}, rows = [received], scores = {[received.files[0].id]: saved}, section = 'all', display = 'all', marks = {}} = {}) {
  const nodes = new Map();
  const work = {cls: 'forensics', week: 'Wk 07', section, display, scores: {[FORM]: scores}, ui: {}, marks: {[FORM]: marks}};
  work.data = {problems: [], lessons: [{topic: 'Fictional lesson', forms: [{form_id: FORM, label: 'Fictional assignment', students: rows, late: [], ...form}]}], ...data};
  const document = {createElement: tag => new Element(tag), getElementById: id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); }};
  const context = vm.createContext({document, Node: Element, work, URL,
    scoreFile: row => row.files[0], markDate: mark => mark.at,
    helperProgress: () => null, sendToHelper: () => { throw Error('No handoff on render'); },
    studentWorkCoverageIncomplete: () => Boolean(work.data.problems.length || work.data.folder_note || work.data.assessment_problem || work.data.shared_form?.problem || work.data.read_timeouts?.length || work.data.lessons.some(lesson => lesson.forms.some(form => form.problem))),
    fetch: () => { throw Error('Rendering must not request work'); }});
  vm.runInContext(prelude + '\n' + functions + '\nglobalThis.ui = {renderWork, verifiedSavedGrade, newlyGraded};', context);
  const before = JSON.stringify(work.data); context.ui.renderWork(); assert.equal(JSON.stringify(work.data), before);
  return {body: nodes.get('work-body'), ui: context.ui};
}

for (const [name, data] of [
  ['failed folder inventory', {folder_note: 'Fictional folder unavailable'}],
  ['typed response timeout', {read_timeouts: ['form_responses']}],
  ['shared response provider failure', {shared_form: {problem: 'Unavailable'}}],
  ['assessment scope unavailable', {assessment_problem: 'Exact route unknown'}],
  ['failed discovery', {problems: ['Fictional source unavailable']}],
]) test(`${name}: partial coverage preserves known saved work without absence zeros`, () => {
  const {body} = boot({data});
  assert.match(body.textContent, /Source coverage is incomplete.*No missing-work zero/);
  assert.match(body.textContent, /Avery, Ann.*Original work.*8.*6/);
  assert.doesNotMatch(body.textContent, /these are 0 now|0 until|Nothing to check|every score.*0s|Regrade|Scoring /);
});

test('saved Hub grades and a historical IC receipt remain separate claims', () => {
  const {body} = boot({marks: {configured: true, entered_at: {at: '2000-01-06T15:00:00Z'}}});
  assert.match(body.textContent, /Saved Hub completion grades are separate from verified IC entry/);
  assert.match(body.textContent, /IC entry receipt recorded.*not a new IC read/);
  assert.match(body.textContent, /Saved in Hub/);
  assert.ok(walk(body).some(node => node.href === received.files[0].url));
});

for (const status of ['stale', 'unavailable', 'not_graded']) test(`${status} saved records never expose cached numeric values`, () => {
  const {body} = boot({scores: {[received.files[0].id]: {status, saved_at: null, problem: 'Current source not verified'}}});
  assert.doesNotMatch(body.textContent, /Original work86/);
  assert.match(body.textContent, status === 'stale' ? /stale.*no current grade/ : status === 'not_graded' ? /No saved grade/ : /Current source not verified/);
  assert.equal(walk(body).filter(node => node.tagName === 'td' && ['8', '6'].includes(node.textContent)).length, 0);
});

test('an unmatched identity retains its source link but cannot borrow saved numeric grades', () => {
  const {body} = boot({rows: [{...received, matched: false, roster_note: 'Identity needs confirmation'}]});
  assert.match(body.textContent, /Identity needs confirmation.*Student identity is not verified/);
  assert.equal(walk(body).filter(node => node.tagName === 'td' && ['8', '6'].includes(node.textContent)).length, 0);
  assert.ok(walk(body).some(node => node.href === received.files[0].url));
});

test('empty intake shows no saved result and does not infer a missing-work zero', () => {
  const {body} = boot({rows: [], form: {missing: [{last: 'Baker', first: 'Bea', section: 'Fictional A2'}]}});
  assert.match(body.textContent, /No submitted work with a saved grade/);
  assert.doesNotMatch(body.textContent, /Baker|these are 0|0 until|No on-time responses/);
});

test('section filtering retains only that section’s actual saved grade', () => {
  const other = {...received, last: 'Brook', section: 'Fictional A3', files: [{...received.files[0], id: 'FICTIONALFILE002'}]};
  const {body} = boot({rows: [received, other], section: 'Fictional A2'});
  assert.match(body.textContent, /Avery, Ann/); assert.doesNotMatch(body.textContent, /Brook/);
});

test('latest filter is based on grade saved_at, never file modification or page view', () => {
  const current = {...saved, saved_at: new Date().toISOString()};
  const old = {...received, last: 'Brook', modified_at: new Date().toISOString(), files: [{...received.files[0], id: 'FICTIONALFILE002'}]};
  const {body} = boot({rows: [received, old], display: 'new', scores: {[received.files[0].id]: current,
    [old.files[0].id]: {...saved, saved_at: '1999-01-01T15:00:00Z'}}});
  assert.match(body.textContent, /Avery, Ann/); assert.doesNotMatch(body.textContent, /Brook/);
});

test('N/A remains distinct from a measured zero and malformed numeric scores fail verification', () => {
  const {body, ui} = boot({scores: {[received.files[0].id]: {...saved, notes_score: null, revision_score: 0}}});
  assert.match(body.textContent, /N\/A0/);
  assert.equal(ui.verifiedSavedGrade({...saved, notes_score: '0'}), false);
  assert.equal(ui.verifiedSavedGrade({...saved, saved_at: 'unverified'}), false);
});


for (const at of ['not-a-time', '2999-01-01T00:00:00Z']) test('invalid or future IC receipt times never imply a verified entry: ' + at, () => {
  const {body} = boot({marks: {configured: true, entered_at: {at}}});
  assert.doesNotMatch(body.textContent, /IC entry receipt recorded/);
  assert.match(body.textContent, /No verified IC entry receipt/);
});
