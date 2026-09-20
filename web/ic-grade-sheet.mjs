// Download IC Grade Sheet — the Student work table, as a spreadsheet to type from.
//
// A manual fallback for entering grades into Infinite Campus by hand. It reads
// the records Page 02 has already loaded and filtered, and writes them to an
// .xlsx in the browser. Nothing is assessed, no API is called, no student work
// is rescanned and no second grading pipeline exists here: a value that is not
// on the table cannot appear in the sheet.
//
// Blank stays blank. Missing, held, unreadable and not-yet-due work must never
// reach a gradebook as a zero, so an unscored checkpoint writes no cell at all.
import {filterRecords, score, splitName} from './grading-records.mjs';

export {splitName};

export const HEADERS = ['Last name', 'First name', 'IC student number',
 'Notes score', 'Revision score', 'Notes status', 'Revision status', 'IC posting status'];
const WIDTHS = [20, 16, 18, 12, 14, 16, 17, 18];
const READY = 'Ready', REVIEW_NEEDED = 'Review needed', NOT_ASSESSED = 'Not assessed';
const POSTED = 'Posted', NOT_POSTED = 'Not posted';
const text = value => typeof value === 'string' ? value : '';

// Page 02 blanks a score when the record is held, unverified, superseded or
// unscored. Reuse that exact decision so the sheet cannot disagree with the
// screen, then recover the number the table rounded for display.
export function scoreValue(row, key) {
 if (score(row, key) === '—') return null;
 return Number(row[key].toFixed(2));
}
function blocked(rows) {
 return rows.some(row => row.hold_reason || ['identity_unverified', 'superseded'].includes(text(row.status)));
}
function pickScore(rows, key, preferStage) {
 const ordered = [...rows].sort((a, b) => (b.assessment_stage === preferStage) - (a.assessment_stage === preferStage));
 for (const row of ordered) { const value = scoreValue(row, key); if (value !== null) return value; }
 return null;
}
function columnStatus(rows, value) {
 if (value !== null) return READY;
 return blocked(rows) ? REVIEW_NEEDED : NOT_ASSESSED;
}
// The workbook's own recorded IC value, reduced to the two words a teacher
// needs. Anything that is not an explicit "posted" reads as not posted; this
// is a recorded value, never a verified gradebook readback.
function postingStatus(rows) {
 return rows.some(row => /^posted\b/.test(text(row.ic_posting_status).trim().toLowerCase())) ? POSTED : NOT_POSTED;
}
// One row per student for the selected class/section and lesson. Both score
// columns are always present, so a Notes-only or Drafts-only view still yields
// one sheet that covers the whole manual entry.
export function buildRows(data, classKey, sectionId = 'matched', assignmentKey = 'all') {
 const selected = filterRecords(data, classKey, sectionId, assignmentKey, 'all');
 const students = new Map();
 for (const row of selected) {
  const key = text(row.section_id) + '\0' + (text(row.count_key) || text(row.student_id) || text(row.display_name));
  if (!students.has(key)) students.set(key, []);
  students.get(key).push(row);
 }
 const rows = [];
 for (const group of students.values()) {
  const [last, first] = splitName(group.find(row => text(row.display_name))?.display_name);
  // The Infinite Campus Student Number as recorded. Blank means the record has
  // no IC identity yet — which is a posting gate, not a missing submission.
  const icNumber = text(group.find(row => text(row.student_id))?.student_id);
  const notes = pickScore(group, 'content_score', 'initial');
  const revision = pickScore(group, 'revision_score', 'final');
  rows.push([last, first, icNumber, notes, revision,
   columnStatus(group, notes), columnStatus(group, revision), postingStatus(group)]);
 }
 return rows.sort((a, b) => a[0].localeCompare(b[0], undefined, {sensitivity: 'base'})
  || a[1].localeCompare(b[1], undefined, {sensitivity: 'base'}));
}

function slug(value) { return text(value).trim().replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, ''); }
export function sheetFilename(scopeLabel, lessonLabel, today = new Date()) {
 const stamp = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
  .map((part, i) => i ? String(part).padStart(2, '0') : part).join('-');
 return `${slug(scopeLabel) || 'Class'}_${slug(lessonLabel) || 'Lesson'}_IC_Grades_${stamp}.xlsx`;
}

// --- Minimal .xlsx writer (no dependency; the project ships none) ------------
const encoder = new TextEncoder();
const bytes = value => encoder.encode(value);
const escapeXml = value => String(value).replace(/[<>&"']/g, c =>
 ({'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;'}[c]));
const columnName = index => { let name = '', n = index; while (n > 0) { const r = (n - 1) % 26; name = String.fromCharCode(65 + r) + name; n = (n - r - 1) / 26; } return name; };

let crcTable;
function crc32(data) {
 if (!crcTable) { crcTable = new Uint32Array(256); for (let i = 0; i < 256; i++) { let c = i; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; crcTable[i] = c >>> 0; } }
 let c = 0xFFFFFFFF;
 for (let i = 0; i < data.length; i++) c = crcTable[(c ^ data[i]) & 0xFF] ^ (c >>> 8);
 return (c ^ 0xFFFFFFFF) >>> 0;
}
// Stored (uncompressed) entries only — a spreadsheet this small does not need
// deflate, and stored entries keep the writer short enough to audit.
function zip(files, now = new Date()) {
 const time = ((now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1)) & 0xFFFF;
 const date = (((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate()) & 0xFFFF;
 const locals = [], centrals = [];
 let offset = 0;
 for (const file of files) {
  const name = bytes(file.name), crc = crc32(file.data), size = file.data.length;
  const local = new Uint8Array(30 + name.length), lv = new DataView(local.buffer);
  lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true); lv.setUint16(6, 0, true);
  lv.setUint16(8, 0, true); lv.setUint16(10, time, true); lv.setUint16(12, date, true);
  lv.setUint32(14, crc, true); lv.setUint32(18, size, true); lv.setUint32(22, size, true);
  lv.setUint16(26, name.length, true); lv.setUint16(28, 0, true);
  local.set(name, 30);
  const central = new Uint8Array(46 + name.length), cv = new DataView(central.buffer);
  cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true);
  cv.setUint16(8, 0, true); cv.setUint16(10, 0, true); cv.setUint16(12, time, true); cv.setUint16(14, date, true);
  cv.setUint32(16, crc, true); cv.setUint32(20, size, true); cv.setUint32(24, size, true);
  cv.setUint16(28, name.length, true); cv.setUint32(42, offset, true);
  central.set(name, 46);
  locals.push(local, file.data); centrals.push(central);
  offset += local.length + size;
 }
 const end = new Uint8Array(22), ev = new DataView(end.buffer);
 const centralSize = centrals.reduce((total, part) => total + part.length, 0);
 ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
 ev.setUint32(12, centralSize, true); ev.setUint32(16, offset, true);
 const parts = [...locals, ...centrals, end];
 const out = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
 let at = 0;
 for (const part of parts) { out.set(part, at); at += part.length; }
 return out;
}

function sheetXml(rows) {
 const cells = (values, rowNumber) => values.map((value, i) => {
  if (value === null || value === undefined || value === '') return '';   // blank stays blank
  const ref = columnName(i + 1) + rowNumber;
  const style = rowNumber === 1 ? ' s="1"' : '';
  return typeof value === 'number'
   ? `<c r="${ref}"${style}><v>${value}</v></c>`
   : `<c r="${ref}"${style} t="inlineStr"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
 }).join('');
 const cols = WIDTHS.map((width, i) => `<col min="${i + 1}" max="${i + 1}" width="${width}" customWidth="1"/>`).join('');
 const body = [HEADERS, ...rows].map((values, i) => `<row r="${i + 1}">${cells(values, i + 1)}</row>`).join('');
 return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
  + `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
  + `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`
  + `<cols>${cols}</cols><sheetData>${body}</sheetData></worksheet>`;
}

export function buildWorkbook(rows, now = new Date()) {
 const files = [
  {name: '[Content_Types].xml', data: bytes(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`)},
  {name: '_rels/.rels', data: bytes(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`)},
  {name: 'xl/workbook.xml', data: bytes(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="IC Grades" sheetId="1" r:id="rId1"/></sheets></workbook>`)},
  {name: 'xl/_rels/workbook.xml.rels', data: bytes(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`)},
  {name: 'xl/styles.xml', data: bytes(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`)},
  {name: 'xl/worksheets/sheet1.xml', data: bytes(sheetXml(rows))},
 ];
 return zip(files, now);
}

// --- Page 02 button ---------------------------------------------------------
export function mountIcGradeSheet(target, {state, scope, doc = document}) {
 const wrap = doc.createElement('div');
 wrap.className = 'action-row';
 const button = doc.createElement('button');
 button.type = 'button';
 button.textContent = 'Download IC Grade Sheet';
 const status = doc.createElement('p');
 status.className = 'hint';
 status.setAttribute('role', 'status');
 button.addEventListener('click', () => {
  const records = state();
  if (records.status !== 'loaded') { status.textContent = 'Refresh the submissions first, then download.'; return; }
  const {classKey, sectionId, assignmentKey, classLabel, sectionLabel, lessonLabel} = scope();
  if (!assignmentKey || assignmentKey === 'all') { status.textContent = 'Choose a specific lesson name first, then download.'; return; }
  const rows = buildRows(records.data, classKey, sectionId, assignmentKey);
  if (!rows.length) { status.textContent = 'No saved records for this selection, so there is nothing to download. This does not establish missing work.'; return; }
  const name = sheetFilename(sectionLabel || classLabel, lessonLabel);
  const blob = new Blob([buildWorkbook(rows)], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
  const url = URL.createObjectURL(blob);
  const link = doc.createElement('a');
  link.href = url; link.download = name;
  doc.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  status.textContent = `${name} · ${rows.length} student${rows.length === 1 ? '' : 's'}. Scores are the saved ones shown above; nothing was re-assessed.`;
 });
 wrap.append(button);
 target.append(wrap, status);
 return {button, status};
}
