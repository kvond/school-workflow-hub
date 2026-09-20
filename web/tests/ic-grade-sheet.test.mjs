// Fictional records only. No browser, upstream fetch, or private student data.
import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRows, buildWorkbook, sheetFilename, splitName, scoreValue, HEADERS} from '../ic-grade-sheet.mjs';

function fixture() {
 return {schema_version: 'hub-grading-records/v1', provenance: 'source_read', external_writes: 0,
  source_revision: 'fictional-read', checked_at: '2026-09-18T12:00:00Z',
  assignments: [{assignment_key: 'BIO-ECO', class_key: 'biology', assignment_title: 'Fictional ecosystems'}],
  submissions: [
   // Both checkpoints for one student: one row per student in the sheet.
   {record_id: 'r1', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's1',
    display_name: 'Ada Fictional', assignment_key: 'BIO-ECO', assignment_title: 'Fictional ecosystems',
    assessment_stage: 'initial', status: 'assessed', content_score: 8.25, revision_score: null,
    ic_posting_status: 'posted', answers: [], included_in_counts: true},
   {record_id: 'r2', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's1',
    display_name: 'Ada Fictional', assignment_key: 'BIO-ECO', assignment_title: 'Fictional ecosystems',
    assessment_stage: 'final', status: 'assessed', content_score: null, revision_score: 9,
    ic_posting_status: 'posted', answers: [], included_in_counts: true},
   // Sorts first by surname; multi-word surname must stay whole.
   {record_id: 'r3', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's2',
    display_name: 'Bo van Fictional', assignment_key: 'BIO-ECO', assignment_title: 'Fictional ecosystems',
    assessment_stage: 'initial', status: 'assessed', content_score: 6, revision_score: null, answers: [], included_in_counts: true},
   // Held work: no score, and never a zero.
   {record_id: 'r4', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's3',
    display_name: 'Cy Aardvark', assignment_key: 'BIO-ECO', assignment_title: 'Fictional ecosystems',
    assessment_stage: 'initial', status: 'assessed', hold_reason: 'Unreadable upload',
    content_score: 7, revision_score: 7, answers: [], included_in_counts: false},
   // Nothing recorded at either checkpoint.
   {record_id: 'r5', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's4',
    display_name: 'Dee Zeta', assignment_key: 'BIO-ECO', assignment_title: 'Fictional ecosystems',
    assessment_stage: 'initial', status: 'assessed', content_score: null, revision_score: null, answers: [], included_in_counts: true},
   // A different lesson must never leak into the selected one.
   {record_id: 'r6', class_key: 'biology', section_id: 'a', section_name: 'Period A', student_id: 's5',
    display_name: 'Eve Other', assignment_key: 'BIO-OTHER', assignment_title: 'Fictional other',
    assessment_stage: 'initial', status: 'assessed', content_score: 10, revision_score: 10, answers: [], included_in_counts: true}],
  summaries: []};
}

test('one row per student, both score columns, sorted by surname then first name', () => {
 const rows = buildRows(fixture(), 'biology', 'a', 'BIO-ECO');
 assert.deepEqual(rows.map(r => [r[0], r[1]]),
  [['Aardvark', 'Cy'], ['Fictional', 'Ada'], ['van Fictional', 'Bo'], ['Zeta', 'Dee']]);
 assert.equal(rows.length, 4, 'the other lesson is excluded and the two checkpoints merge into one student row');
 const ada = rows.find(r => r[1] === 'Ada');
 assert.equal(ada[2], 8.25, 'notes score comes from the notes checkpoint');
 assert.equal(ada[3], 9, 'revision score comes from the drafts checkpoint');
 assert.deepEqual([ada[4], ada[5], ada[6]], ['Ready', 'Ready', 'Posted']);
});

test('unscored, held and missing work stay blank and never become zero', () => {
 const rows = buildRows(fixture(), 'biology', 'a', 'BIO-ECO');
 const held = rows.find(r => r[1] === 'Cy');
 assert.equal(held[2], null, 'a held record is suppressed on the page, so it is blank here too');
 assert.equal(held[3], null);
 assert.deepEqual([held[4], held[5]], ['Review needed', 'Review needed']);
 const none = rows.find(r => r[1] === 'Dee');
 assert.deepEqual([none[2], none[3], none[4], none[5]], [null, null, 'Not assessed', 'Not assessed']);
 assert.equal(none[6], 'Not posted', 'no recorded IC value reads as not posted');
 const partial = rows.find(r => r[1] === 'Bo');
 assert.deepEqual([partial[2], partial[3], partial[4], partial[5]], [6, null, 'Ready', 'Not assessed']);
 assert.ok(rows.every(row => row.slice(2, 4).every(value => value === null || typeof value === 'number')));
});

test('blanking follows the same rule the table uses', () => {
 const [ok, held] = [{content_score: 5, status: 'assessed'}, {content_score: 5, hold_reason: 'held'}];
 assert.equal(scoreValue(ok, 'content_score'), 5);
 assert.equal(scoreValue(held, 'content_score'), null);
 assert.equal(scoreValue({content_score: null}, 'content_score'), null);
 assert.equal(scoreValue({content_score: 6.666}, 'content_score'), 6.67, 'matches the two-decimal display');
});

test('names split into surname and first name without inventing either', () => {
 assert.deepEqual(splitName('Ada Fictional'), ['Fictional', 'Ada']);
 assert.deepEqual(splitName('Bo van Fictional'), ['van Fictional', 'Bo']);
 assert.deepEqual(splitName('Cy Ortiz-Fictional'), ['Ortiz-Fictional', 'Cy']);
 assert.deepEqual(splitName('Mononym'), ['Mononym', '']);
 assert.deepEqual(splitName(''), ['', '']);
});

test('filename is human readable and dated', () => {
 assert.equal(sheetFilename('A4 Biology', 'Ecosystems', new Date(2026, 8, 18)),
  'A4_Biology_Ecosystems_IC_Grades_2026-09-18.xlsx');
 assert.equal(sheetFilename('Biology', 'Cycle 02 · Ecosystems & Feeding', new Date(2026, 8, 18)),
  'Biology_Cycle_02_Ecosystems_Feeding_IC_Grades_2026-09-18.xlsx');
});

test('the workbook is a real xlsx package with a frozen bold header', () => {
 const rows = buildRows(fixture(), 'biology', 'a', 'BIO-ECO');
 const bytes = buildWorkbook(rows, new Date(2026, 8, 18, 9, 0, 0));
 assert.ok(bytes instanceof Uint8Array && bytes.length > 0);
 assert.deepEqual([...bytes.slice(0, 4)], [0x50, 0x4b, 0x03, 0x04], 'ZIP local header');
 const body = Buffer.from(bytes).toString('latin1');
 for (const part of ['[Content_Types].xml', 'xl/workbook.xml', 'xl/worksheets/sheet1.xml', 'xl/styles.xml'])
  assert.ok(body.includes(part), 'package contains ' + part);
 assert.ok(body.includes('state="frozen"') && body.includes('ySplit="1"'), 'top row frozen');
 for (const header of HEADERS) assert.ok(body.includes(header));
 assert.ok(body.includes('<v>8.25</v>'), 'scores are numeric cells, not text');
 assert.ok(!/<c r="C\d+"[^>]*><v>0<\/v>/.test(body), 'no fabricated zero scores');
});
