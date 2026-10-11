// Execute just the existing selector/list functions on fictional data. No DOM,
// browser, network or IC interaction is used.
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const functions = ['assessmentRows', 'helperRows'].map(name => {
  const match = source.match(new RegExp(`function ${name}\\(form\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(match, name); return match[0];
}).join('\n');
const student = {matched: true, last: 'Avery', first: 'Ann', section: 'A2 Fictional', late: false,
  combined_components: [{file_id: 'BOTH_ANCHOR'}], files: [{id: 'BOTH_ANCHOR'}]};

function fixture(closed = false) {
  const context = vm.createContext({
    work: {data: {entry_rechecks: {F: {rows: [{...student, files: [{id: 'CURRENT_ANCHOR'}]}]}}}, scores: {F: {
      BOTH_ANCHOR: {notes_score: 7.5, revision_score: 5, component_coverage: {text: 'Partial coverage'}, counts: {}},
      CURRENT_ANCHOR: {notes_score: 8, revision_score: 5, counts: {}}
    }}}, closedRecheck: () => closed, sourceMatchesHeld: () => false, weekAssessmentHold: () => false,
    entryHistoryHold: () => false, recheckSelectionHold: () => false, startedFor: () => true,
    elsewhereKeys: () => new Set(), acceptedIntakeKeys: () => new Set(),
    personKey: row => [row.section, row.last, row.first].join('|'),
    oneLine: value => String(value || ''), scoreFile: row => row.files[0]
  });
  vm.runInContext(functions, context);
  return context;
}

function form() {
  return {form_id: 'F', combined_assessment: {}, scoring_students: [student],
    students: [{...student, files: [{id: 'RAW_LATEST'}]}], grade_readiness: {status: 'READY'},
    missing: [{last: 'Example', first: 'Missing', section: 'A2 Fictional'}], late: [], excused: []};
}

test('open combined UI uses the paired anchor while ordinary Form keeps its raw selection', () => {
  const context = fixture(), value = form();
  assert.equal(context.assessmentRows(value)[0].files[0].id, 'BOTH_ANCHOR');
  delete value.combined_assessment;
  assert.equal(context.assessmentRows(value)[0].files[0].id, 'RAW_LATEST');
});
test('closed verified recheck uses current per-cycle selection supplied by the server', () => {
  assert.equal(fixture(true).assessmentRows(form())[0].files[0].id, 'CURRENT_ANCHOR');
});
test('manual helper preserves partial grade and coverage, but never fabricates the absent part zero', () => {
  const rows = fixture().helperRows(form());
  assert.equal(rows[0].kind, 'score'); assert.equal(rows[0].notes, 7.5); assert.equal(rows[0].reason, 'Partial coverage');
  assert.equal(rows[1].kind, 'none'); assert.equal(rows[1].notes, null); assert.equal(rows[1].revision, null);
});
test('represented on-time component is not re-added from a constructed raw late row', () => {
  const value = form(); value.missing = []; value.students = [];
  value.late = [{...student, late: true, files: [{id: 'RAW_LATE'}]}];
  const rows = fixture().helperRows(value);
  assert.equal(rows.length, 1); assert.equal(rows[0].kind, 'score'); assert.equal(rows[0].notes, 7.5);
  value.scoring_students.push({...student});
  assert.equal(fixture().helperRows(value)[0].kind, 'check');
});
test('no on-time eligible component is none, never a guessed zero', () => {
  const value = form(); value.missing = []; value.students = []; value.scoring_students = [];
  value.late = [{...student, late: true, files: [{id: 'RAW_LATE'}]}];
  const rows = fixture().helperRows(value);
  assert.equal(rows.length, 1); assert.equal(rows[0].kind, 'none'); assert.equal(rows[0].notes, null);
});
test('actual renderForm selection retains represented on-time row and excludes its raw late duplicate', () => {
  const block = source.match(/const selected = form\.combined_assessment[\s\S]*?const lateMatched = late\.filter\(r => r\.matched\)\.length;/);
  assert.ok(block);
  const value = form(); value.missing = []; value.students = [];
  value.late = [{...student, late: true, files: [{id: 'RAW_LATE'}]}];
  const context = fixture(); context.form = value; context.recheck = false;
  context.inSection = () => true; context.entryHistoryHold = () => '';
  context.accepted = new Set(); context.elsewhere = new Set();
  const result = vm.runInContext(`(() => { ${block[0]} return {matched, late, lateMatched}; })()`, context);
  assert.equal(result.matched.length, 1); assert.equal(result.late.length, 0); assert.equal(result.lateMatched, 0);
});
