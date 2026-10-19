// Render the production Student work UI with fictional discovery results.
// No browser, API, grading or student-data operation is performed.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child instanceof Element ? child.textContent : String(child)).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const functions = ['fileList', 'studentRow', 'scoreCells', 'assessmentRows', 'icStatus', 'marksLine',
  'renderForm', 'teacherMaterialList', 'feedbackBackup', 'renderWork', 'renderRosterCheck']
  .map(name => {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
  }).join('\n');
const declarations = source.slice(0, source.indexOf('function issueAction('));
const availability = source.slice(source.indexOf('const work ='), source.indexOf('function omittedFormProblem('));
const sourceMatchHold = source.match(/^const SOURCE_MATCH_HOLD = .*;$/m)[0];
const allNodes = element => [element, ...element.children.filter(child => child instanceof Element).flatMap(allNodes)];
const window = {opened: '2032-01-05', label: 'Jan 19', from: '2032-01-05T00:00:00Z', at: '2032-01-19T00:00:00Z'};
const missing = [
  {last: 'Baker', first: 'Bea', section: 'A2 Fictional'},
  {last: 'Cedar', first: 'Cam', section: 'A3 Fictional'},
];
const received = {matched: true, last: 'Avery', first: 'Ann', section: 'A2 Fictional',
  submitted_at: '2032-01-06T15:00:00Z', late: false, earlier: [], typed: [],
  files: [{id: 'FICTIONALWORK123', name: 'Fictional received deck', url: 'https://drive.google.com/open?id=FICTIONALWORK123'}]};
const unmatched = {...received, matched: false, last: '', first: '', section: '',
  email: 'fictional@example.invalid', roster_note: 'Identity needs confirmation',
  files: [{id: 'FICTIONALUNKNOWN1', name: 'Fictional unmatched deck', url: 'https://drive.google.com/open?id=FICTIONALUNKNOWN1'}]};

function boot({form = {}, data = {}, section = 'all', scores = null, panel = null, closed = false, marks = {}} = {}) {
  const ids = new Map();
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  }};
  const value = {form_id: 'FICTIONALFORM123', label: 'Fictional lesson', url: 'https://docs.google.com/forms/d/FICTIONALFORM123/edit',
    students: [], missing, late: [], elsewhere: [], excused: [], window, grade_readiness: {status: 'READY'}, ...form};
  const lesson = {topic: 'Fictional lesson', forms: [value]};
  const found = {problems: [], roster_size: 2, lessons: [lesson], other_files: [], folder_files: 0,
    folders: [{name: 'Fictional lesson folder', url: 'https://drive.google.com/drive/folders/FICTIONALFOLDER1'}],
    roster_check: {sections: {'A2 Fictional': 1, 'A3 Fictional': 1}, flags: []}, ...data};
  const context = vm.createContext({document, Node: Element, URL,
    fetch: () => { throw new Error('No network expected'); },
    closedRecheck: () => false, closedFor: () => closed, startedFor: form => Boolean(form.window),
    entryHistoryHold: () => '', recheckSelectionHold: () => '', gradeReadinessHold: () => '',
    recordProblem: value => value, markDate: () => 'Jan 6',
    acceptedIntakeKeys: () => new Set(), elsewhereKeys: () => new Set(),
    personKey: row => [row.section, row.last, row.first].join('|'), scoreFile: row => row.files[0],
    shown: score => [score.notes_score, score.revision_score], STATUS: {},
    studentWorkReviewPanel: () => null, uncodedReport: () => null,
    icPanel: () => { throw new Error('An incomplete read must not render an absence-zero IC table'); },
    weekEntryBox: () => new Element('week-entry'),
  });
  vm.runInContext(declarations + '\n' + availability + '\n' + sourceMatchHold + '\n' + functions +
    '\nglobalThis.ui = {work, renderWork, renderForm};', context);
  Object.assign(context.ui.work, {data: found, section, week: 'Wk 07', cls: 'forensics',
    scores: scores ? {[value.form_id]: scores} : {}, ui: {[value.form_id]: {open: panel}}, marks: {[value.form_id]: marks}});
  const snapshot = JSON.stringify({value, found, scores, marks});
  context.ui.renderWork();
  // UI changes must not alter response, roster, assessment or score records.
  assert.equal(JSON.stringify({value, found, scores, marks}), snapshot);
  return {element: document.getElementById('work-body'), value, found};
}

function assertNoAbsence(element) {
  assert.doesNotMatch(element.textContent, /\d+ of \d+ turned in|No (?:student |on-time )?responses|Not turned in yet|Nothing turned in on time|these are 0 now|0 until they turn it in|including the 0s|Nothing to check: everyone/);
  assert.equal(allNodes(element).filter(node => node.className === 'missing').length, 0);
  assert.equal(allNodes(element).filter(node => node.tagName === 'week-entry').length, 0);
}

for (const [name, data] of [
  ['failed folder inventory', {folder_note: 'The exact upload folder could not be read (fictional 403).'}],
  ['partial folder coverage', {folder_note: 'Only readable Form uploads were read.', folder_files: 1}],
  ['typed response timeout', {read_timeouts: ['form_responses']}],
  ['shared response provider failure', {shared_form: {problem: 'The earlier Form provider is unavailable.'}}],
  ['assessment source scope unavailable', {assessment_problem: 'Exact source routing could not be verified.'}],
  ['discovery provider failure', {problems: ['The source provider returned fictional 429.']}],
]) {
  test(`${name}: unknown coverage cannot become empty intake or missing students`, () => {
    const {element} = boot({data});
    assert.match(element.textContent, /Source coverage is incomplete.*Submission totals not verified/);
    assertNoAbsence(element);
    assert.doesNotMatch(element.textContent, /Baker, Bea|Cedar, Cam|Read the Form uploads and 0 file/);
    if (data.folder_note) assert.ok(element.textContent.includes(data.folder_note));
    if (data.problems) assert.ok(element.textContent.includes(data.problems[0]));
  });
}

test('failed exact Form responses preserve readable rows, real identity holds, original links and saved marks', () => {
  const problem = 'This Form’s responses could not be read (fictional provider failure).';
  const {element} = boot({form: {problem, students: [received, unmatched]},
    marks: {configured: true, entered_at: {at: '2032-01-06T15:00:00Z'}}});
  assert.ok(element.textContent.includes(problem));
  assert.match(element.textContent, /1 known matched submission.*1 not matched to the roster/);
  assert.match(element.textContent, /Avery, Ann.*Fictional received deck.*Identity needs confirmation.*Fictional unmatched deck/);
  assert.match(element.textContent, /Entered in IC Jan 6/);
  assert.ok(allNodes(element).some(node => node.href === received.files[0].url));
  assert.ok(allNodes(element).some(node => node.href === unmatched.files[0].url));
  assertNoAbsence(element);
});

test('zero-readable-row provider failure still explains unavailable coverage, not no responses', () => {
  const {element} = boot({form: {problem: 'This Form’s responses could not be read.'}});
  assert.match(element.textContent, /could not be read.*Source coverage is incomplete.*Submission totals not verified/);
  assertNoAbsence(element);
});

test('verified teacher examples stay informational during incomplete student intake reads', () => {
  const {element} = boot({data: {read_timeouts: ['turn_in_folders']}, form: {teacher_uploads: [
    {submitted_at: '2032-01-06T15:00:00Z', files: [{id: 'FICTIONALTEACHER1', name: 'Fictional teacher example',
      url: 'https://drive.google.com/open?id=FICTIONALTEACHER1'}]},
  ]}});
  assert.match(element.textContent, /Teacher material — not student work \(1\).*exact original files were verified as teacher examples.*Fictional teacher example/);
  assert.doesNotMatch(element.textContent, /school account|resubmit/);
  assertNoAbsence(element);
});

test('section filter retains its known submissions and unresolved identity while hiding unverified missing names', () => {
  const other = {...received, last: 'Dover', first: 'Dee', section: 'A3 Fictional'};
  const {element} = boot({section: 'A2 Fictional', data: {folder_note: 'Folder coverage is partial.'},
    form: {students: [received, other, unmatched]}});
  assert.match(element.textContent, /1 known matched submission.*Avery, Ann.*Identity needs confirmation/);
  assert.doesNotMatch(element.textContent, /Dover, Dee|Baker, Bea|Cedar, Cam/);
  assertNoAbsence(element);
});

test('late received work and source-selection holds remain visible despite incomplete reads', () => {
  const held = {...received, selection_problem: 'The exact response region is ambiguous.'};
  const {element} = boot({data: {folder_note: 'Folder coverage is partial.'},
    form: {students: [held], late: [{...received, last: 'Late', late: true}]}});
  assert.match(element.textContent, /exact response region is ambiguous/);
  assert.match(element.textContent, /Late — not graded \(1\).*Late, Ann/);
  assertNoAbsence(element);
});

test('previously open manual IC panel cannot display absence zeros on a new partial result', () => {
  const score = {notes_score: 5, revision_score: 7, items: [],
    counts: {NOTES: {done: 1, required: 2}, BANK: {done: 0, required: 0}, DRAFT: {done: 2, required: 3}}};
  const {element} = boot({data: {folder_note: 'Folder coverage is partial.'}, form: {students: [received]},
    scores: {[received.files[0].id]: score}, panel: 'ic'});
  assert.match(element.textContent, /Avery, Ann.*50% \(1 of 2 notes boxes/);
  assert.doesNotMatch(element.textContent, /Scores are ready to enter in IC/);
  assertNoAbsence(element);
});

test('a complete truly empty intake retains normal zero response counts and missing policy', () => {
  const {element} = boot({form: {window: null}});
  assert.match(element.textContent, /0 of 2 turned in/);
  assert.match(element.textContent, /No responses yet/);
  assert.match(element.textContent, /Not turned in yet \(2\).*Baker, Bea.*Cedar, Cam/);
  assert.doesNotMatch(element.textContent, /0 until they turn it in|these are 0 now/);
  assert.doesNotMatch(element.textContent, /Source coverage is incomplete|Submission totals not verified/);
  assert.equal(allNodes(element).filter(node => node.tagName === 'week-entry').length, 1);
});

test('a complete read with received work retains the ordinary open-window missing zero rule', () => {
  const {element} = boot({form: {students: [received]}});
  assert.match(element.textContent, /1 of 3 turned in on time/);
  assert.match(element.textContent, /Not turned in yet \(2\) — 0 until they turn it in/);
});

test('a failed neighboring Form cannot make the shared week coverage look complete', () => {
  const {element} = boot({data: {lessons: [{topic: 'Another fictional lesson', forms: [{
    form_id: 'FICTIONALFAILED2', label: 'Another fictional Form', students: [], missing, late: [],
    problem: 'Neighboring Form response read failed.',
  }]}]}});
  assert.match(element.textContent, /Neighboring Form response read failed.*Source coverage is incomplete/);
  assert.doesNotMatch(element.textContent, /Nothing to check: everyone/);
  assertNoAbsence(element);
});

test('incomplete coverage retains genuine roster flags without claiming no roster students', () => {
  const {element} = boot({data: {folder_note: 'The folder read failed.', roster_size: 0,
    roster_check: {sections: {}, flags: [{kind: 'no_roster', who: 'Fictional unmatched respondent',
      assignment: 'Fictional lesson', url: unmatched.files[0].url}]}}});
  assert.match(element.textContent, /Roster flags to check \(1\).*Fictional unmatched respondent/);
  assert.doesNotMatch(element.textContent, /No roster students were found|Nothing to check/);
  assertNoAbsence(element);
});

test('complete section-filtered reads keep the normal received denominator and closed-window policy', () => {
  const {element} = boot({section: 'A2 Fictional', closed: true, form: {students: [received]}});
  assert.match(element.textContent, /1 of 2 turned in on time/);
  assert.match(element.textContent, /Nothing turned in on time \(1\) — these are 0 now.*Baker, Bea/);
  assert.doesNotMatch(element.textContent, /Cedar, Cam|Source coverage is incomplete/);
});

test('complete reads do not hide genuine unmatched work or unresolved lesson holds', () => {
  const {element} = boot({form: {students: [unmatched]}, data: {other_files: [received]}});
  assert.match(element.textContent, /Identity needs confirmation.*Fictional unmatched deck/);
  assert.match(element.textContent, /No confirmed submission for this assignment \(2\) — scoring held/);
  assert.match(element.textContent, /Confirm these submission matches.*Fictional received deck/);
});
