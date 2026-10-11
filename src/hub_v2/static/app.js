// Teaching Hub v2: lesson plans from the Agenda, and student work found from
// each lesson's Form responses and turn-in folder, with completion scores
// counted from each deck's coded boxes. The opted-in Chrome helper writes and
// verifies eligible morning scores; this page reads receipts and offers
// optional manual tools.
const $ = id => document.getElementById(id);
const CLASSES = [['biology', 'Biology'], ['forensics', 'Forensics'], ['anatomy', 'A&P']];
const SCAN_CLASSES = ['biology', 'anatomy'];  // with an "Answers by question" view
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
const FULL_DAY = {Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday', Sun: 'Sunday'};
const TZ = 'America/New_York';

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}
const link = (text, href, cls) => el('a', {href, target: '_blank', rel: 'noopener noreferrer', class: cls}, text);
const note = (text, kind = 'warn') => el('p', {class: 'note ' + kind}, text);
const store = {
  get(key) { try { return localStorage.getItem('hub2:' + key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem('hub2:' + key, value); } catch { /* private window */ } },
};

const pad = n => String(n).padStart(2, '0');
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const at = value => new Date(value + 'T12:00:00');
const todayISO = () => new Intl.DateTimeFormat('en-CA', {timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
function addDays(value, n) { const d = at(value); d.setDate(d.getDate() + n); return iso(d); }
function mondayOf(value) { return addDays(value, -((at(value).getDay() + 6) % 7)); }
function weekRange(monday) {
  const start = at(monday), end = at(addDays(monday, 4));
  const month = d => d.toLocaleDateString('en-US', {month: 'short'});
  return `${month(start)} ${start.getDate()} – ${month(end) === month(start) ? '' : month(end) + ' '}${end.getDate()}, ${end.getFullYear()}`;
}
const when = ts => ts ? new Date(ts).toLocaleString('en-US', {timeZone: TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';

async function getJSON(url) {
  const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    throw new Error(body?.error || (response.status === 401 || response.status === 403
      ? 'Please sign in again: reload this page.' : `The server returned an error (${response.status}).`));
  }
  return body;
}
function setStatus(id, text, error = false) { const node = $(id); node.textContent = text; node.classList.toggle('error', error); }
function showAgendaLink(agenda) {
  if (!agenda?.url) return;
  const a = $('agenda-link'); a.href = agenda.url; a.title = agenda.title || 'Agenda'; a.hidden = false;
}


// ---------- Hub v3 teacher cockpit ----------
const cockpit = {seq: 0, audits: null, auditsAt: 0, feedbackData: null, learningData: null};

function cockpitPanel(title, ...children) {
  return el('section', {class: 'cockpit-panel'}, el('h2', {text: title}), ...children);
}

function releaseIssue(value) {
  if (!value) return '';
  const [code, aspect, phase] = String(value).split(':');
  if (code.startsWith('marker_inventory_')) return 'Needs embedded codes: repair the lesson’s code list and required totals.';
  if (code === 'student_copies_uncoded' || code === 'teacher_distributed_uncoded_copies') return 'Needs embedded codes in the affected student copies.';
  const words = code.replaceAll('_', ' ');
  if (code === 'critical_aspect_block_missing') return `Missing critical-aspect block: ${aspect}`;
  if (code === 'critical_aspect_phase_missing') return `${aspect}: missing ${String(phase || '').replaceAll('_', ' ')}`;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function releaseLabel(release) {
  if (!release) return 'Not checked';
  if (release.label) return release.label;
  if (release.status === 'READY') return 'Ready';
  const issues = release.issues || [];
  if (issues.some(issue => /^(marker_inventory_|duplicate_feedback_marker|feedback_marker_cycle_mismatch|draft_without_matching_notes|student_copies_uncoded|teacher_distributed_uncoded_copies)/.test(issue))) return 'Needs embedded codes';
  if (issues.some(issue => /unreadable|unavailable/.test(issue))) return 'Cannot read lesson';
  return 'Needs check';
}

function releaseBadge(release) {
  if (!release) return el('span', {class: 'release-badge neutral', text: 'Not checked'});
  const kind = release.status === 'READY' ? 'ready' : release.status === 'NOT_READY' ? 'blocked' : 'stale';
  return el('span', {class: 'release-badge ' + kind, text: releaseLabel(release)});
}

async function loadAudits(force = false) {
  if (!force && cockpit.audits && Date.now() - cockpit.auditsAt < 60_000) return cockpit.audits;
  const entries = await Promise.all(CLASSES.map(async ([key, name]) => {
    try { return [key, name, await getJSON(`api/audit?class=${encodeURIComponent(key)}`)]; }
    catch (error) { return [key, name, {rows: [], flags: [], problem: error.message}]; }
  }));
  cockpit.audits = Object.fromEntries(entries.map(([key, name, data]) => [key, {name, data}]));
  cockpit.auditsAt = Date.now();
  return cockpit.audits;
}

function releaseFor(audits, classKey, lesson) {
  const rows = audits?.[classKey]?.data?.rows || [];
  const exact = rows.find(row => row.topic === lesson.topic);
  if (exact) return exact.release || null;
  const sameWeek = lesson.week ? rows.filter(row => row.week === lesson.week) : [];
  return sameWeek.length === 1 ? (sameWeek[0].release || null) : null;
}

function allReleaseRows(audits) {
  return CLASSES.flatMap(([key, name]) => (audits?.[key]?.data?.rows || [])
    .filter(row => row.release)
    .map(row => ({classKey: key, className: name, ...row})));
}

function morningTotals(report) {
  const out = {filled: 0, raised: 0, kept: 0, left: 0, leftKnown: true, reasons: {flag: 0, excused: 0, check: 0, name: 0}};
  for (const item of report?.items || []) {
    for (const key of ['filled', 'raised', 'kept']) out[key] += item[key] || 0;
    const review = morningRowReview(item);
    if (review.left == null) out.leftKnown = false;
    else out.left += review.left;
    for (const [key, count] of review.reasons) out.reasons[key] += count;
  }
  return out;
}

const MORNING_REASONS = {
  flag: 'IC rows have a status flag other than Exempt; the helper left them unchanged',
  excused: 'New students are excused from this assignment',
  check: 'The Hub could not confirm a score from the submitted work; open Student work for this assignment and read the reason on rows marked “Check by hand”',
  name: 'IC students could not be matched to the Hub list',
};

const MORNING_COVERAGE = 'Source coverage was incomplete, so scoring and IC entry stopped for this assignment. Open Student work to see whether Form or roster access, or a submission’s lesson match, needs repair.';
const MORNING_EXCLUDED = 'This lesson was intentionally excluded by the school-year policy. No IC score comparison or entry was attempted for this assignment.';
// These checks do not establish an uncertain save. Earlier columns may still
// have confirmed results, so describe the held step rather than the whole run.
const MORNING_ENTRY_HOLDS = {
  mapping_required: 'Entry held at the assignment-mapping check. In Student work, confirm this lesson’s Form, class section and Notes/Revision column map to the exact IC assignment, then prepare a fresh list.',
  source_changed: 'Entry held at the source check. Refresh this assignment in Student work, review its current submissions and lesson-match checks, then prepare a fresh list.',
  unsupported_points_scale: 'Entry held because this IC assignment is not scored out of 10. Open this class’s correct 10-point Notes/Revision assignment and confirm the exact mapping before running the helper. Do not rescale existing scores.',
  unsupported_gradebook: 'Entry held because the IC score panel is unsupported. Open this class’s mapped assignment in the IC score-entry panel and let the helper check it again.',
  destination_mapping_changed: 'Entry held because the IC destination no longer matches the list. Confirm this class section, assignment and Notes/Revision column against the exact Hub mapping, then prepare a fresh list.',
  students_dont_match: 'Entry held because this IC assignment’s students do not match the Hub list. Open the correct class section and assignment in IC, then refresh its list in Student work.',
  several_sections: 'Entry held because the list contains several class sections. Prepare a list for this IC assignment’s one class section.',
  list_expired: 'Entry held because the Hub list expired. Refresh this assignment in Student work and prepare a new list.',
  ic_changed: 'Entry held because IC changed after the helper’s review. Review this assignment again against a fresh Hub list before entry.',
  ic_not_saved: 'Entry held because this IC assignment already has unsaved edits. Review those edits, save them if intended or reload to discard them, then let the helper review the assignment again.',
  signed_out: 'Entry held because IC requires sign-in. Sign in to IC in the school Chrome profile and reopen this class’s mapped assignment.',
  ic_unavailable: 'Entry held because the helper could not reach IC. Open one signed-in IC tab in the school Chrome profile and reopen this class’s mapped assignment.',
  hub_unavailable: 'Entry held because the helper could not reach the Hub. Open one signed-in Hub tab in the school Chrome profile, refresh it and prepare a fresh list for this assignment.',
  disabled: 'Entry held because the score helper is disabled. Open the installed helper in the school Chrome profile and check its enabled state before running this assignment.',
};
const morningContext = row => [row.class_name, row.week, row.assignment].filter(Boolean).join(' · ') || 'Assignment unavailable';

function morningSourceHold(row) {
  const checks = row.source_holds?.checks;
  const count = Number.isInteger(checks) && checks > 0
    ? `${checks} submitted-work row${checks === 1 ? '' : 's'} need${checks === 1 ? 's' : ''} source checks.`
    : 'Submitted work needs source checks.';
  return `${count} No IC score comparison or entry was attempted for this assignment. Open Student work for this class and lesson and read the reason on each row marked “Check by hand”.`;
}

function morningRowReview(row) {
  const reasons = row.reasons || {};
  const leftRecorded = Number.isInteger(row.left) && row.left >= 0;
  const exemptKnown = Number.isInteger(reasons.exempt) && reasons.exempt >= 0;
  // An old receipt may count flags on already-matching scores. Only subtract
  // Exempt when its count can actually be part of the recorded unchanged rows.
  const left = row.left === 0 ? 0 : leftRecorded && exemptKnown && reasons.exempt <= row.left
    ? row.left - reasons.exempt : null;
  const contextKnown = !!row.class_name && !!row.week;
  const detailKnown = contextKnown && exemptKnown && left != null;
  return {
    left,
    detailKnown,
    reasons: detailKnown && left > 0 ? Object.entries(reasons)
      .filter(([reason, count]) => Object.hasOwn(MORNING_REASONS, reason) && count > 0 && count <= left) : [],
    unavailable: !contextKnown
      ? 'This older receipt does not retain class/week or Exempt flag details. Detailed follow-up is unavailable.'
      : !detailKnown
        ? 'This receipt does not retain verified Exempt counts for the unchanged rows. Detailed follow-up is unavailable.' : '',
  };
}

function morningPreflight(report) {
  if (report?.items?.length) return '';
  const value = report?.preflight;
  const stages = {
    summary_publish: 'The helper stopped before reading assignments while saving its starting receipt.',
    summary_backlog: 'The helper stopped before reading assignments because an earlier receipt was still undelivered.',
    hub_connection: 'The helper stopped before reading assignments because it could not connect to one Hub tab.',
  };
  const causes = {
    hub_tab_missing: 'Open one signed-in Hub tab in this school profile.',
    hub_tab_count: 'Keep exactly one Hub tab open in this school profile.',
    hub_tab_closed: 'The Hub tab closed before the connection check. Reopen it.',
    hub_origin_changed: 'The Hub tab left the configured site. Open the configured Hub.',
    hub_bridge_missing: 'The extension was not connected to the Hub page. Refresh the Hub tab after the extension is loaded.',
    hub_bridge_closed: 'The Hub page connection closed before a reply arrived. Keep the tab open and refresh it.',
    hub_bridge_timeout: 'The Hub page did not reply before the connection timeout.',
    hub_bridge_unavailable: 'The extension could not exchange a message with the Hub page.',
    hub_signed_out: 'The Hub requires sign-in in this school profile.',
    hub_access_denied: 'The Hub refused access to the receipt request; check the signed-in school Hub.',
    hub_http_error: 'The Hub rejected the receipt request.',
    hub_request_timeout: 'The Hub receipt request timed out.',
    hub_network_error: 'The Hub receipt request failed before a usable response arrived.',
    hub_invalid_response: 'The Hub response was not usable JSON.',
    hub_report_not_acknowledged: 'The Hub response did not confirm that the receipt was recorded.',
    previous_receipt_pending: 'An earlier receipt still needs confirmed delivery.',
    unrecorded: 'The underlying connection error was not retained.',
  };
  if (!value || !Object.hasOwn(stages, value.stage) || !Object.hasOwn(causes, value.cause)) return '';
  const status = Number.isInteger(value.status) && value.status >= 100 && value.status <= 599 ? ` (HTTP ${value.status})` : '';
  return `${stages[value.stage]} ${causes[value.cause]}${status} No IC score comparison or entry was attempted. Receipt delivery retries do not run grades.`;
}

function morningProblem(report) {
  if (!report) return '';
  if (report.code !== 'finished' && !report.items?.length) return morningPreflight(report)
    || 'The run did not finish and this receipt contains no assignment results. Its original failure cause was not retained. No IC score comparison or save is confirmed; a delivered receipt does not prove grades ran.';
  if (report.code !== 'finished') return 'Morning run: ' + String(report.code || 'status unavailable').replaceAll('_', ' ') + '. Completion is not verified.';
  if (!report.items?.length) return 'The completed receipt contains no assignment results. No IC save or score comparison is verified.';
  const held = (report.items || []).filter(item => !['read_back', 'nothing_to_enter', 'no_scores', 'excluded_week'].includes(item.code));
  return held.length ? `${held.length} assignment${held.length === 1 ? '' : 's'} did not finish: ` +
    [...new Set(held.map(item => String(item.code || 'status unavailable').replaceAll('_', ' ')))].join(', ') + '.' : '';
}

function renderMorningCockpit(report, routineData) {
  if (!report) return cockpitPanel('What ran',
    note('No 7:00 a.m. score-entry receipt is recorded for today. This does not mean grades failed; it means no completed morning receipt is available.', 'info'),
    routineData?.scores_entered?.last ? el('p', {class: 'muted', text: 'Latest Scores entered record: ' + routineData.scores_entered.last}) : null);
  const confirmed = (report.items || []).filter(item => ['read_back', 'nothing_to_enter'].includes(item.code));
  const totals = morningTotals({items: confirmed});
  const handled = totals.filled + totals.raised + totals.kept;
  const problem = morningProblem(report);
  const saved = report.code === 'finished' && !problem;
  const onlyExcluded = report.items?.length && report.items.every(item => item.code === 'excluded_week');
  const detail = (report.items || []).map(item =>
    el('li', {}, el('strong', {text: morningContext(item) + ': '}),
      item.code === 'read_back'
        ? `${item.filled} new · ${item.raised} raised · ${item.kept} already matched in IC — confirmed after reload.`
        : item.code === 'nothing_to_enter'
          ? `No changes needed. ${item.kept} scores already matched in IC.`
          : item.code === 'no_scores'
            ? 'No eligible Hub scores were available. This assignment was not entered or compared in IC.'
            : item.code === 'excluded_week'
              ? MORNING_EXCLUDED
              : item.code === 'coverage_incomplete'
                ? MORNING_COVERAGE
                : item.code === 'source_scores_held'
                  ? morningSourceHold(item)
                  : Object.hasOwn(MORNING_ENTRY_HOLDS, item.code)
                    ? MORNING_ENTRY_HOLDS[item.code]
                    : `Save/readback not confirmed: ${String(item.code || 'status unavailable').replaceAll('_', ' ')}. Open this IC assignment, inspect the scores, correct/save only if needed, and reload to verify before clearing the helper verification block.`,
      !['source_scores_held', 'excluded_week'].includes(item.code) && morningRowReview(item).unavailable ? el('p', {class: 'muted', text: morningRowReview(item).unavailable}) : null));
  return cockpitPanel('What ran',
    el('p', {class: 'cockpit-number', text: String(handled)}),
    el('p', {class: 'cockpit-label', text: saved
      ? (onlyExcluded ? 'Intentionally excluded lessons; no IC comparison or entry' : `${totals.filled} new scores entered · ${totals.raised} raised · ${totals.kept} existing scores matched in IC`)
      : 'grade rows with confirmed IC results; unfinished results appear below'}),
    el('p', {class: problem ? 'needs' : 'ok', text: problem || (saved
      ? (onlyExcluded ? 'Completed without entry: these lessons were excluded by the school-year policy.' : confirmed.length ? '✓ These IC results are confirmed. Review the gradebook afterward and adjust a score only if needed.' : 'No eligible Hub scores were available. No IC entry or score comparison is confirmed.')
      : 'The run has not confirmed a completed save.')}),
    el('details', {}, el('summary', {text: 'Morning assignment details'}), el('ul', {}, ...detail)));
}

function renderNeedsYou(report) {
  const items = [], unavailable = [];
  if (!report) return cockpitPanel('Run status', note('No receipt is available yet. IC results are not verified.', 'info'));
  const problem = morningProblem(report);
  if (problem) items.push(el('li', {text: problem}));
  for (const row of report?.items || []) {
    if (row.code === 'excluded_week') {
      unavailable.push(el('p', {class: 'muted'}, el('strong', {text: morningContext(row) + ': '}), MORNING_EXCLUDED));
      continue;
    }
    if (row.code === 'source_scores_held') {
      items.push(el('li', {}, el('strong', {text: morningContext(row) + ': '}), morningSourceHold(row)));
      continue;
    }
    if (row.code === 'no_scores') {
      items.push(el('li', {}, el('strong', {text: morningContext(row) + ': '}), 'No eligible Hub scores were available; IC was not compared. This receipt does not identify a student source issue. Current source checks are shown separately.'));
      continue;
    }
    const review = morningRowReview(row);
    if (review.unavailable) unavailable.push(el('p', {class: 'muted'}, el('strong', {text: morningContext(row) + ': '}), review.unavailable));
    if (!review.detailKnown) continue;
    if (row.code === 'coverage_incomplete') {
      items.push(el('li', {}, el('strong', {text: morningContext(row) + ': '}), MORNING_COVERAGE));
      continue;
    }
    if (!review.reasons.length) {
      if (review.left && !row.reasons?.missing) unavailable.push(el('p', {class: 'muted'},
        el('strong', {text: morningContext(row) + ': '}), 'The receipt does not retain a follow-up reason for the unchanged rows. Detailed follow-up is unavailable.'));
      continue;
    }
    items.push(el('li', {}, el('strong', {text: morningContext(row) + ': '}),
      review.reasons.map(([reason, n]) => `${n} — ${MORNING_REASONS[reason]}`).join('; ')));
  }
  return cockpitPanel('Rows the helper left unchanged',
    items.length ? el('ul', {class: 'attention-list'}, ...items)
      : unavailable.length ? null : el('p', {class: 'ok', text: '✓ No grade rows need attention from this receipt.'}),
    ...unavailable,
    el('p', {class: 'muted', text: 'IC’s assignment roster determines which students belong in this run. Exempt rows do not require follow-up. The receipt contains counts; Student work gives the source details for the named class and assignment.'}));
}

// Named source checks are a separate authenticated, current view. They never
// become morning receipt fields, helper-list fields or persisted browser state.
const reviewRequests = new WeakMap();
const reviewPanels = new WeakMap();
const visibleReviewPanels = {today: null, routine: null};
let morningSummarySeq = 0;

function reviewScope(value) {
  const classKey = value?.class_key || value?.class;
  const form = value?.form_id || value?.form;
  if (!CLASSES.some(([key]) => key === classKey) || typeof value?.week !== 'string'
      || !value.week.trim() || typeof form !== 'string' || !/^[A-Za-z0-9_-]{10,200}$/.test(form)) return null;
  return {class_key: classKey, week: value.week, form_id: form};
}

function sameReviewScope(a, b) {
  return a && b && ['class_key', 'week', 'form_id'].every(key => a[key] === b[key]);
}

function reviewSourceURL(source) {
  if (typeof source?.file_id !== 'string' || !/^[A-Za-z0-9_-]{10,200}$/.test(source.file_id)
      || typeof source.url !== 'string' || /[\u0000-\u0020\u007f]/.test(source.url)) return null;
  try {
    const url = new URL(source.url);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash || url.href !== source.url) return null;
    const id = source.file_id;
    if (url.origin === 'https://docs.google.com' && !url.search
        && ['presentation', 'document', 'spreadsheets', 'forms'].some(kind => url.pathname === `/${kind}/d/${id}/edit`)) return url.href;
    if (url.origin === 'https://drive.google.com') {
      if (!url.search && url.pathname === `/file/d/${id}/view`) return url.href;
      if (!url.search && url.pathname === `/drive/folders/${id}`) return url.href;
      if (url.pathname === '/open' && [...url.searchParams.keys()].length === 1
          && url.searchParams.getAll('id').length === 1 && url.searchParams.get('id') === id) return url.href;
    }
  } catch { /* an unavailable link is displayed as a limitation */ }
  return null;
}

function renderSourceReviewNotice(notice, scope, includeContext = true) {
  const student = notice.student || {};
  const name = [student.last, student.first].filter(value => typeof value === 'string' && value.trim()).join(', ');
  const identity = notice.kind === 'lesson' || notice.affected_scope === 'lesson' ? 'Lesson-wide source check · no individual student'
    : student.verified === true && student.last && student.first
    ? name : name ? `${name} (identity unverified)` : 'Student identity not verified';
  const context = [scope.class_name || scope.class_key, student.section, scope.week, scope.lesson].filter(Boolean).join(' · ');
  const sources = Array.isArray(notice.sources) ? notice.sources : [];
  const locations = Array.isArray(notice.slides) ? notice.slides : [];
  const sourceLines = sources.map(source => {
    const url = reviewSourceURL(source);
    const matching = locations.filter(slide => slide?.file_id === source.file_id);
    const slide = matching.length === 1 ? matching[0] : null;
    const numbers = slide?.numbers;
    const verified = slide?.verified === true && Array.isArray(numbers) && numbers.length > 0
      && numbers.every(number => Number.isInteger(number) && number > 0 && number <= 1000)
      && new Set(numbers).size === numbers.length;
    const where = verified ? `${numbers.length === 1 ? 'Slide' : 'Slides'} ${numbers.join(', ')}` : 'Slides not verified';
    return el('li', {}, url ? link(source.label || 'Open exact source', url) : el('span', {text: 'Source link unavailable'}),
      ' · ', where);
  });
  return el('div', {class: 'source-review'},
    includeContext ? el('strong', {text: `${context} · ${identity}`}) : el('strong', {text: identity}),
    el('p', {text: `Reason: ${typeof notice.reason === 'string' && notice.reason ? notice.reason : 'Exact reason unavailable.'}`}),
    sourceLines.length ? el('ul', {class: 'files'}, ...sourceLines)
      : el('p', {class: 'muted', text: 'Source link unavailable · Slides not verified'}),
    el('p', {}, el('strong', {text: 'Next action: '}),
      typeof notice.next_action === 'string' && notice.next_action ? notice.next_action : 'No exact next action was returned.'));
}

function renderSourceReviewContext(data, expected, options = {}) {
  if (data?.schema !== 'hub-source-review-context/v1' || !sameReviewScope(data.scope, expected)
      || typeof data.scope.class_name !== 'string' || !data.scope.class_name
      || typeof data.scope.lesson !== 'string' || !data.scope.lesson
      || typeof data.checked_at !== 'string' || !Number.isFinite(Date.parse(data.checked_at))
      || !Array.isArray(data.notices) || !Array.isArray(data.limitations)
      || data.notices.some(notice => !notice || typeof notice !== 'object' || typeof notice.reason !== 'string'
        || !notice.student || typeof notice.student !== 'object' || typeof notice.next_action !== 'string'))
    throw new Error('Current source context could not be confirmed for this exact class and lesson.');
  const limitations = data.limitations.filter(value => typeof value === 'string' && value
    && (!options.limitationsSeen || !options.limitationsSeen.has(value)));
  if (options.limitationsSeen) limitations.forEach(value => options.limitationsSeen.add(value));
  const empty = data.status === 'waiting' ? 'Waiting for original submissions; no student-copy repair is requested.'
    : data.status === 'excluded' ? 'This scope is excluded from current grading review.'
    : 'No current selection issue was returned; assessment is not established by this view.';
  return el('div', {},
    el('p', {class: 'muted', text: `Current source context · checked ${when(data.checked_at)}. Read separately from the saved helper receipt.`}),
    data.notices.length ? el('ul', {class: 'attention-list'}, ...data.notices.map(notice =>
      el('li', {}, renderSourceReviewNotice(notice, data.scope))))
      : el('p', {class: 'muted', text: options.compactEmpty ? empty : 'No current source review notices were returned for this exact lesson. This does not establish complete coverage or a successful IC run.'}),
    ...limitations.map(value => note(value, 'info')));
}

function nextReviewRequest(body) {
  const seq = (reviewRequests.get(body) || 0) + 1;
  reviewRequests.set(body, seq);
  return seq;
}

async function loadSourceReviewContext(body, scope, options = {}) {
  const seq = nextReviewRequest(body);
  body.replaceChildren(el('p', {class: 'muted', text: 'Reading current source context…'}));
  const active = () => reviewRequests.get(body) === seq && (!options.current || options.current());
  try {
    const key = JSON.stringify(scope);
    const read = () => getJSON(`api/review-context?class=${encodeURIComponent(scope.class_key)}&week=${encodeURIComponent(scope.week)}&form=${encodeURIComponent(scope.form_id)}`);
    if (options.cache && !options.cache.has(key)) options.cache.set(key, read());
    const data = await (options.cache ? options.cache.get(key) : read());
    if (!active()) return;
    const rendered = renderSourceReviewContext(data, scope, options);
    if (options.group && !data.notices.length && data.status === 'not_assessed') {
      options.group.hidden = true;
      if (!options.limitationsSeen.has('unassessed-summary')) {
        options.limitationsSeen.add('unassessed-summary');
        options.sharedLimitations.append(note('Some original submissions have no current verified assessment details. This view did not score them; complete coverage or IC completion is not established.', 'info'));
      }
    } else if (options.group && !data.notices.length && ['waiting', 'excluded'].includes(data.status)) {
      options.group.replaceChildren(el('p', {class: 'muted', text: `${options.scopeLabel}: ${data.status === 'waiting'
        ? 'Waiting for original submissions; no student-copy repair is requested.' : 'Excluded from this current source review.'}`}));
    } else body.replaceChildren(rendered);
    return true;
  } catch (error) {
    if (active()) body.replaceChildren(note('Current source context is unavailable: ' + error.message + ' The saved helper receipt is unchanged.', 'info'));
    return false;
  }
}

function invalidateReviewPanel(panel) {
  if (!panel) return;
  const parts = reviewPanels.get(panel);
  nextReviewRequest(panel);
  if (parts) { nextReviewRequest(parts.body); parts.body.replaceChildren(); }
  panel.replaceChildren(el('p', {class: 'muted', text: 'Refreshing current source context…'}));
}

function currentSourceReviewPanel(active = true) {
  const progress = el('p', {class: 'muted', role: 'status', text: 'Reading exact current lesson scopes…'});
  const body = el('div', {'aria-live': 'polite'});
  const panel = cockpitPanel('Current source checks',
    el('p', {class: 'muted', text: 'Current source details are read separately from historical helper counts. This view does not score work; an empty review is not completed assessment or complete coverage.'}), progress, body);
  reviewPanels.set(panel, {body});
  if (!active) { progress.textContent = 'Current source checks are read when this page is opened.'; return panel; }
  const seq = nextReviewRequest(panel);
  getJSON('api/morning/catalog').then(async data => {
    if (reviewRequests.get(panel) !== seq) return;
    if (!Array.isArray(data.assignments)) throw new Error('Exact lesson scopes are unavailable.');
    const scopes = data.assignments.map(entry => ({entry, scope: reviewScope(entry)}));
    if (scopes.some(item => !item.scope) || new Set(scopes.map(item => JSON.stringify(item.scope))).size !== scopes.length)
      throw new Error('Exact lesson scopes could not be confirmed.');
    const sharedLimitations = el('div'); body.append(sharedLimitations);
    const groups = scopes.map(({entry}) => {
      const detail = el('div');
      const label = [entry.class_name || entry.class, entry.week, entry.assignment].join(' · ');
      const group = el('section', {}, el('h3', {text: label}), detail);
      body.append(group);
      return {group, detail, label};
    });
    if (data.problems?.length) body.append(note('Some lesson scopes could not be confirmed. No scope was inferred from receipt titles.', 'info'));
    let cursor = 0, complete = 0, unavailable = 0;
    const limitationsSeen = new Set();
    const current = () => reviewRequests.get(panel) === seq;
    const update = () => { if (current()) progress.textContent = scopes.length
      ? `Current source checks · ${complete} of ${scopes.length} exact lesson scopes read${unavailable ? `; ${unavailable} unavailable` : ''}.`
      : 'No exact current lesson scopes were returned; coverage is not established.'; };
    update();
    const worker = async () => {
      while (current() && cursor < scopes.length) {
        const index = cursor++;
        const ok = await loadSourceReviewContext(groups[index].detail, scopes[index].scope,
          {current, compactEmpty: true, limitationsSeen, group: groups[index].group,
            scopeLabel: groups[index].label, sharedLimitations});
        if (!current()) return;
        complete++; if (!ok) unavailable++;
        update();
      }
    };
    // Two independent exact-scope GETs at a time; never infer scopes from titles
    // or receipt counts, and never invoke Student work's automatic scoring path.
    await Promise.all([worker(), worker()]);
  }).catch(error => {
    if (reviewRequests.get(panel) === seq) { progress.textContent = ''; body.replaceChildren(note('Current source checks are unavailable: ' + error.message, 'info')); }
  });
  return panel;
}

function studentWorkReviewPanel(form, open) {
  const scope = reviewScope({class_key: work.cls, week: work.week, form_id: form.form_id});
  if (!scope) return null;
  const seq = work.seq, cache = work.reviewContexts;
  const body = el('div', {'aria-live': 'polite'});
  const panel = el('details', {class: 'earlier', open}, el('summary', {text: 'Current source checks · exact class and lesson'}), body);
  loadSourceReviewContext(body, scope, {cache, current: () => work.seq === seq && work.cls === scope.class_key && work.week === scope.week});
  return panel;
}

function scoreReviewNotice(result, formId) {
  const detail = result?.review_context;
  const expected = reviewScope({class_key: work.cls, week: work.week, form_id: formId});
  if (detail?.schema !== 'hub-source-review-context/v1' || !expected || !sameReviewScope(detail.scope, expected)
      || typeof detail.scope.class_name !== 'string' || typeof detail.scope.lesson !== 'string'
      || !detail.notice || typeof detail.notice.reason !== 'string' || !detail.notice.student
      || typeof detail.notice.next_action !== 'string') return null;
  return renderSourceReviewNotice(detail.notice, detail.scope);
}

function issueAction(release) {
  if (release?.action) return release.action;
  if (release?.problem) return release.problem;
  const issue = release?.issues?.[0];
  if (release?.status === 'READY') return release.scope === 'linked_deck'
    ? 'The linked deck’s embedded codes are ready. Student copies are checked separately before grading.' : 'No fix needed. Teach this lesson.';
  if (!issue) return 'No exact cause was returned. Open this lesson in Lessons / VT and run the link check.';
  if (issue === 'missing_teacher_reference_contract' || issue === 'teacher_reference_has_no_critical_aspects'
      || issue === 'teacher_reference_has_no_week_question') return 'Refresh to check this lesson’s embedded codes. Teacher reference contracts are not a completion-grading requirement.';
  if (issue === 'marker_inventory_missing_or_duplicated') return 'In the teacher deck, keep one complete embed-coding marker inventory and remove or repair any duplicate; then recheck readiness.';
  if (issue === 'marker_inventory_counts_unparsed' || issue === 'marker_inventory_count_mismatch') return 'Repair the required NOTES, BANK and DRAFT totals in this lesson’s marker inventory, then recheck and rerun grades. Submitted student copies must also have matching codes.';
  if (issue === 'marker_inventory_id_mismatch') return 'In the teacher deck, make the marker IDs in the inventory match the IDs embedded in the lesson slides; then recheck readiness.';
  if (issue === 'duplicate_feedback_marker') return 'In the teacher deck, remove or correct the repeated feedback marker so each marker ID appears once; then recheck readiness.';
  if (issue === 'feedback_marker_cycle_mismatch') return 'In the teacher deck, make the feedback marker cycle match the cycle named by its inventory; then recheck readiness.';
  if (issue === 'draft_without_matching_notes') return 'In the teacher deck, add a matching NOTES marker for every DRAFT marker, or remove the unmatched DRAFT marker; then recheck readiness.';
  if (issue === 'concept_bank_heading_missing') return 'In the teacher deck, add the required “Define these in your own words” heading above the concept bank; then recheck readiness.';
  if (issue === 'required_what_if_missing') return 'In the teacher deck, add the required “What if?” prompt for the scored completion item; then recheck readiness.';
  if (issue === 'agenda_prompt_contract_unavailable') return 'The Agenda prompt details could not be read, so readiness cannot be verified. Restore access to the linked Agenda prompts, then recheck.';
  if (issue === 'deck_release_unreadable') return 'Open the teacher deck from Lessons / VT and confirm the link opens; if not, correct that deck link in the Agenda.';
  if (issue.startsWith('critical_aspect_block_missing:')) return `In the teacher deck, add the lesson section for ${issue.slice('critical_aspect_block_missing:'.length)}; then recheck readiness.`;
  if (issue.startsWith('critical_aspect_phase_missing:')) {
    const [, aspect, phase] = issue.split(':');
    return `In the teacher deck, add the ${String(phase || '').replaceAll('_', ' ')} section for ${aspect}; then recheck readiness.`;
  }
  return `${releaseIssue(issue)}. Open this exact lesson in Lessons / VT, follow the issue shown there, then recheck readiness.`;
}

const MISSING_CODE_ADVISORIES = new Set(['marker_inventory_missing_or_duplicated', 'marker_inventory_counts_unparsed',
  'marker_inventory_count_mismatch', 'marker_inventory_id_mismatch', 'student_copies_uncoded', 'teacher_distributed_uncoded_copies']);
function lessonCodeAdvisory(row) {
  const release = row.release;
  return !(row.classKey === 'biology' && /photosynthesis/i.test(row.topic || '')) && !release?.readability
    && !release?.teacher_holds?.length && release?.issues?.length > 0
    && release.issues.every(issue => MISSING_CODE_ADVISORIES.has(issue));
}
function lessonReviewAction(row) {
  const photo = row.classKey === 'biology' && /photosynthesis/i.test(row.topic || '');
  if (!photo && !row.release?.teacher_holds?.length && (lessonCodeAdvisory(row) || row.release?.status === 'READY'))
    return 'Count the usable coded boxes actually present in readable, correctly matched student copies. Keep Notes and Revision separate. Missing codes alone do not hold grading; this linked-source check does not verify student copies.';
  return issueAction(row.release);
}
function lessonReviewNotice(row) {
  const release = row.release || {};
  const decks = release.source_resolution?.decks || row.decks || [];
  const sources = decks.filter(deck => typeof deck.id === 'string').map(deck => ({file_id: deck.id,
    label: deck.title || deck.name || deck.label || 'Open linked lesson source',
    url: deck.metadata?.mimeType === 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      ? `https://drive.google.com/open?id=${deck.id}` : `https://docs.google.com/presentation/d/${deck.id}/edit`}));
  for (const folder of release.source_resolution?.folders || []) sources.push({file_id: folder.id,
    label: folder.name || 'Open verified lesson folder', url: `https://drive.google.com/drive/folders/${folder.id}`});
  for (const form of row.forms || []) if (form.form_id) sources.push({file_id: form.form_id,
    label: form.student_title || form.title || form.label || 'Open exact turn-in Form', url: `https://docs.google.com/forms/d/${form.form_id}/edit`});
  return {kind: 'lesson', student: {verified: false}, reason: lessonCodeAdvisory(row)
    ? 'The linked source’s code inventory needs checking. Missing codes alone are advisory for this lesson; only usable coded items in correctly matched copies count.'
    : release.problem || (release.status === 'READY' ? 'The linked source passed its code check; student copies are checked separately.'
      : (release.issues || []).map(releaseIssue).join(' ') || 'The exact linked-source issue was not returned.'),
    sources, slides: sources.map(source => ({file_id: source.file_id, numbers: [], verified: false})),
    next_action: lessonReviewAction(row)};
}
function lessonReviewBadge(row) {
  return lessonCodeAdvisory(row) ? el('span', {class: 'release-badge neutral', text: 'Code advisory'}) : releaseBadge(row.release);
}

async function openAgendaLesson(classKey, week, row) {
  try {
    const data = await getJSON(`api/weeks?class=${encodeURIComponent(classKey)}&today=${todayISO()}`);
    const target = data.weeks.find(item => item.label === week && item.monday);
    if (!target) throw new Error('The matching Agenda week is not available.');
    plans.focusRow = `${classKey}|${row}`;
    plans.date = target.monday;
    location.hash = '#plans';
    await loadPlans(target.monday);
  } catch (error) {
    setStatus('plans-status', error.message, true);
    location.hash = '#plans';
  }
}

function renderNextLessons(audits) {
  const rows = allReleaseRows(audits);
  if (!rows.length) return cockpitPanel('Next from Agenda',
    note('No current/upcoming lesson code check is available. Open Lessons / VT to inspect the Agenda and links.', 'info'));
  const bodyRows = rows.map(row => {
    return el('tr', {},
      el('td', {text: row.className}),
      el('td', {text: row.week}),
      el('td', {text: row.topic}),
      el('td', {}, lessonReviewBadge(row)),
      el('td', {}, renderSourceReviewNotice(lessonReviewNotice(row),
        {class_name: row.className, week: row.week, lesson: row.topic}),
        el('button', {type: 'button', onclick: () => openAgendaLesson(row.classKey, row.week, row.row)}, 'Open this lesson in Lessons / VT')));
  });
  const table = el('table', {class: 'cockpit-table'},
    el('thead', {}, el('tr', {}, ...['Class', 'Week', 'Lesson', 'Release', 'Next'].map(text => el('th', {text})))),
    el('tbody', {}, ...bodyRows));
  return cockpitPanel('Next from Agenda', el('div', {class: 'table-wrap'}, table));
}

async function printTodayFeedback(data, status, button) {
  const files = data.files || [];
  button.disabled = true;
  const printWindow = window.open('about:blank', '_blank');
  if (!printWindow) {
    status.textContent = 'The print packet was not opened. Allow pop-ups for the Hub, then press Print all feedback again.';
    button.disabled = false;
    return;
  }
  status.textContent = `Preparing all-class feedback for ${data.date}…`;
  try {
    let packet = data.daily_packet;
    let response;
    if (packet?.status === 'ready' && (!files.length || data.packet_matches_sources === true)) {
      response = await fetch(packet.pdf_url, {credentials: 'same-origin', cache: 'no-store'});
    } else if (packet?.configured) {
      const finalized = await fetch('api/feedback/daily', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({date: data.date})});
      packet = await finalized.json();
      if (!finalized.ok || packet.status !== 'ready') throw new Error(packet.error || packet.problem || 'The daily feedback document was not saved.');
      response = await fetch(packet.pdf_url, {credentials: 'same-origin', cache: 'no-store'});
    } else {
      response = await fetch('api/feedback/print-today', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: files.map(file => file.id)})});
    }
    if (!response.ok) {
      let detail = 'The packet could not be prepared.';
      try { detail = (await response.json()).error || detail; } catch { /* keep the safe message */ }
      throw new Error(detail);
    }
    const blob = await response.blob();
    if (blob.type !== 'application/pdf' || !blob.size) throw new Error('The print packet was empty.');
    const url = URL.createObjectURL(blob);
    printWindow.addEventListener('load', () => setTimeout(() => printWindow.print(), 900), {once: true});
    printWindow.location = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    status.textContent = packet?.status === 'ready'
      ? `Daily feedback document opened for ${data.date}: ${packet.pages} pages across classes. Use the PDF print button (or Cmd/Ctrl+P) to print the whole document. The daily document is saved; verified individual copies remain in student folders. Printing and handing out remain your own records.`
      : `Feedback preview opened for ${data.date}. Use the PDF print button (or Cmd/Ctrl+P) to print the whole document. This preview is not a saved daily document. Printing and handing out remain your own records.`;
    if (packet?.status === 'ready') loadToday(true);
    button.disabled = false;
  } catch (error) {
    printWindow.close();
    status.textContent = error.message;
    button.disabled = false;
  }
}

function renderTodayFeedback(data) {
  if (data.error) return cockpitPanel('Feedback ready to print', note(data.error, 'info'));
  if (data.problem) return cockpitPanel('Feedback ready to print', note(data.problem, 'info'));
  const files = data.files || [];
  const packet = data.daily_packet;
  const panel = cockpitPanel('Daily feedback document');
  const historical = data.date && data.date < (data.requested_date || todayISO());
  const sourceNotice = data.source_problem ? note('Current feedback sources could not be fully checked. ' +
    (packet?.status === 'ready' ? 'This saved dated document remains available: ' : '') + data.source_problem, 'info') : null;
  if (!files.length && packet?.status !== 'ready') {
    panel.append(sourceNotice || note('No print-ready feedback documents were found in the connected course folders.', 'info'));
    return panel;
  }
  const counts = packet?.report_counts;
  const contentsSummary = counts && ['science_writing', 'missing_work_contract', 'critical_aspects', 'other_sources']
    .every(key => Number.isInteger(counts[key]) && counts[key] >= 0)
    ? [['science writing reports', counts.science_writing], ['current work contracts', counts.missing_work_contract],
       ['lesson feedback reports', counts.critical_aspects], ['other documents', counts.other_sources]]
        .filter(([, count]) => count).map(([label, count]) => `${count} ${label}`).join(' · ')
    : `${packet?.status === 'ready' ? packet.source_count : files.length} source documents`;
  const status = el('p', {class: 'muted', role: 'status', text: `Feedback dated ${data.date} · all classes · ${contentsSummary}. Printing and handing out remain separate.`});
  const list = el('ul', {class: 'feedback-today-list'}, ...files.map(file =>
    el('li', {}, el('strong', {text: `${file.course_name}${file.student_name ? ` · ${file.student_name}` : ' · class packet'}${file.report_type === 'science_writing' ? ' · science writing' : file.report_type === 'missing_work_contract' ? ' · current work contract' : ''}: `}),
      link(file.name, file.url), el('small', {class: 'muted', text: ` · feedback ${file.feedback_day || data.date}`}))));
  const dates = (packet?.available_dates || []).filter(item => item.date !== data.date &&
    /^\/v2\/api\/feedback\/daily\/pdf\?id=dailyfeedbackpacket_[a-f0-9]{64}$/.test(item.pdf_url || ''));
  const checklist = cockpit.learningData?.checklist_url;
  const button = el('button', {type: 'button', class: historical ? '' : 'primary'},
    `${historical ? 'Reprint saved feedback' : 'Print all feedback'} — ${data.date}`);
  button.onclick = () => printTodayFeedback(data, status, button);
  const savedURL = packet?.status === 'ready' && /^\/v2\/api\/feedback\/daily\/pdf\?id=dailyfeedbackpacket_[a-f0-9]{64}$/.test(packet.pdf_url || '') ? packet.pdf_url : null;
  const contents = [status,
    savedURL ? el('p', {}, link(`Open saved daily feedback — ${packet.date} (${packet.pages} pages)`, savedURL))
      : note(packet?.configured ? 'Printing saves this day’s all-class document. Verified student copies stay in their folders; existing class packets stay at class level.'
        : 'The daily document folder is not connected yet. The existing feedback can be printed as a preview.', 'info'),
    data.packet_matches_sources === false ? note('The saved document differs from the current verified feedback list. Print all feedback saves an updated version; the earlier PDF is preserved.', 'info') : null,
    checklist && !historical ? el('p', {}, link('Open feedback checklist · Made / Printed / Handed to student', checklist)) : null,
    el('div', {class: 'action-row'}, button),
    files.length ? el('details', {}, el('summary', {text: 'Current feedback by student'}), list) : null,
    dates.length ? el('details', {}, el('summary', {text: 'Other saved feedback dates'}),
      el('ul', {}, ...dates.map(item => el('li', {}, link(`${item.date} · ${item.pages} pages`, item.pdf_url))))) : null].filter(Boolean);
  if (historical) {
    panel.append(...[el('p', {text: `No new feedback ${data.current_sources_verified ? 'for' : 'confirmed for'} ${data.requested_date || todayISO()}. Earlier dated feedback remains available to reprint.`}),
      sourceNotice,
      checklist ? el('p', {}, link('Open feedback checklist · Made / Printed / Handed to student', checklist)) : null,
      el('details', {}, el('summary', {text: `Earlier feedback · latest saved ${data.date}`}), ...contents)].filter(Boolean));
  } else panel.append(...[sourceNotice, ...contents].filter(Boolean));
  return panel;
}

function renderLearningReport(data) {
  if (!data?.configured) return null;
  return cockpitPanel('Biology growth and DTGSS',
    el('p', {text: data.as_of ? `Biology report · initial review ${data.as_of}.` : 'Saved Biology learning report.'}),
    el('div', {class: 'action-row'}, link('Biology report · start here', data.url), link('Biology teaching actions', data.teaching_url),
      data.analysis_url ? link('Biology analysis', data.analysis_url) : null,
      data.work_index_url ? link('All student work · index', data.work_index_url) : null),
    el('p', {class: 'muted', text: 'Current-roster percentages, Beginning and Developing counts, and paired comparisons. The report states its evidence dates.'}));
}

function renderTodayFeedbackArea(seq) {
  if (seq !== cockpit.seq) return;
  $('today-feedback').replaceChildren(...[renderLearningReport(cockpit.learningData),
    cockpit.feedbackData ? renderTodayFeedback(cockpit.feedbackData) : null].filter(Boolean));
}

async function loadTodayFeedback(seq) {
  // Saved snapshots have their own verified read and do not need an Agenda or
  // a fresh inventory of every student folder before they can be reprinted.
  const packet = await getJSON('api/feedback/daily').catch(error => ({error: error.message}));
  if (seq !== cockpit.seq) return;
  if (packet.status === 'ready') {
    cockpit.feedbackData = {date: packet.date, files: [], daily_packet: packet};
  } else if (!cockpit.feedbackData) {
    cockpit.feedbackData = packet.error ? {error: packet.error} : {date: packet.date, files: [], daily_packet: packet};
  }
  renderTodayFeedbackArea(seq);
  const data = await getJSON('api/feedback/today').catch(error => ({error: error.message}));
  if (seq !== cockpit.seq) return;
  const problem = data.error || data.problem;
  const saved = cockpit.feedbackData?.daily_packet;
  cockpit.feedbackData = problem && saved?.status === 'ready'
    ? {date: saved.date, files: [], daily_packet: saved, source_problem: problem} : data;
  renderTodayFeedbackArea(seq);
}

async function loadToday(force = false) {
  const seq = ++cockpit.seq;
  invalidateReviewPanel(visibleReviewPanels.today);
  invalidateReviewPanel(visibleReviewPanels.routine);
  setStatus('today-status', 'Reading today’s Agenda, morning receipts and deck readiness…');
  if (cockpit.feedbackData?.daily_packet?.status === 'ready') {
    const packet = cockpit.feedbackData.daily_packet;
    cockpit.feedbackData = {date: packet.date, files: [], daily_packet: packet};
    renderTodayFeedbackArea(seq);
  }
  loadTodayFeedback(seq);
  getJSON('api/learning-report').catch(() => ({configured: false})).then(data => {
    if (seq !== cockpit.seq) return;
    cockpit.learningData = data;
    renderTodayFeedbackArea(seq);
  });
  try {
    if (force) { cockpit.audits = null; cockpit.auditsAt = 0; }
    const [day, reportsData, routineData, audits] = await Promise.all([
      getJSON(`api/day?date=${encodeURIComponent(todayISO())}${force ? '&fresh=1' : ''}`),
      getJSON('api/morning/reports'),
      getJSON('api/routine'),
      loadAudits(force),
    ]);
    if (seq !== cockpit.seq) return;
    showAgendaLink(day.agenda);
    const report = (reportsData.reports || []).find(item => item.day === todayISO()) || null;
    visibleReviewPanels.today = currentSourceReviewPanel();
    $('today-body').replaceChildren(
      el('div', {class: 'cockpit-grid'},
        renderMorningCockpit(report, routineData),
        renderNeedsYou(report)),
      visibleReviewPanels.today,
      renderNextLessons(audits));
    setStatus('today-status', `Updated ${when(new Date().toISOString())}.`);
  } catch (error) {
    if (seq !== cockpit.seq) return;
    setStatus('today-status', error.message, true);
    $('today-body').replaceChildren();
  }
}

async function loadLessonReadiness(force = false) {
  const box = $('lesson-readiness-body');
  if (!box) return;
  box.replaceChildren(el('p', {class: 'muted', text: 'Checking embedded codes in current and upcoming lessons…'}));
  try {
    const audits = await loadAudits(force);
    const rows = allReleaseRows(audits);
    if (!rows.length) {
      box.replaceChildren(note('No current/upcoming lesson code check is available yet.', 'info'));
      return;
    }
    box.replaceChildren(
      el('p', {class: 'muted', text: 'Linked lesson’s embedded codes; submitted copies are verified before grading.'}),
      el('div', {class: 'release-row'}, ...rows.map(row =>
        el('div', {class: 'release-item'},
          el('span', {text: `${row.className} · ${row.week}`}),
          lessonReviewBadge(row),
          renderSourceReviewNotice(lessonReviewNotice(row), {class_name: row.className, week: row.week, lesson: row.topic})))));
  } catch (error) {
    box.replaceChildren(note('Deck readiness could not be checked: ' + error.message));
  }
}

// ---------- Lesson plans ----------
const plans = {date: todayISO(), calendarMonday: mondayOf(todayISO()), seq: 0, started: false, data: null, editing: null, focusRow: null, weeks: {}, weeksLoading: {}};

function renderDays(date) {
  if (date < plans.calendarMonday || date > addDays(plans.calendarMonday, 27)) plans.calendarMonday = mondayOf(date);
  const calendarDays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  $('days').replaceChildren(...Array.from({length: 4}, (_, week) => {
    const monday = addDays(plans.calendarMonday, week * 7);
    const end = addDays(monday, 6);
    return el('div', {class: 'calendar-week'},
      el('p', {class: 'calendar-caption', text: `${at(monday).toLocaleDateString('en-US', {month: 'short', day: 'numeric'})} – ${at(end).toLocaleDateString('en-US', {month: 'short', day: 'numeric'})}`}),
      el('div', {class: 'calendar-days'}, ...calendarDays.map((name, i) => {
        const day = addDays(monday, i);
        return el('button', {type: 'button', class: i > 4 ? 'weekend-date' : '',
          'aria-label': at(day).toLocaleDateString('en-US', {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}),
          'aria-current': day === date ? 'date' : null, onclick: () => loadPlans(day)},
          el('small', {text: name}), el('strong', {text: String(at(day).getDate())}));
      })));
  }));
}

async function loadPlans(date, fresh = false) {
  plans.started = true; plans.date = date; $('date').value = date; renderDays(date);
  const seq = ++plans.seq;
  plans.data = null;
  $('week-label').textContent = at(date).toLocaleDateString('en-US', {weekday: 'long', month: 'short', day: 'numeric', year: 'numeric'});
  $('week-closures').textContent = '';
  $('cards').replaceChildren();
  $('next-week-notes').replaceChildren();
  setStatus('plans-status', 'Reading the Agenda…');
  try {
    const data = await getJSON(`api/day?date=${encodeURIComponent(date)}${fresh ? '&fresh=1' : ''}`);
    if (seq !== plans.seq) return;
    showAgendaLink(data.agenda);
    renderPlans(data);
    renderNextWeekNotes(data);
    setStatus('plans-status', '');
  } catch (error) {
    if (seq !== plans.seq) return;
    setStatus('plans-status', error.message, true);
    $('cards').replaceChildren();
  }
}

function renderNextWeekNotes(data) {
  const box = $('next-week-notes');
  if (!box) return;
  const monday = addDays(data.week?.monday || mondayOf(plans.date), 7);
  const heading = `Notes for next week · ${weekRange(monday)}`;
  const classes = data.classes || [];
  box.replaceChildren(el('h2', {text: heading}),
    el('p', {class: 'muted', text: 'Private notes saved in this browser. One note per class.'}),
    ...classes.map(cls => {
      const key = `next-week-note:${cls.key}:${monday}`;
      const textarea = el('textarea', {rows: 2, 'aria-label': `${cls.name} notes for next week`, placeholder: `${cls.name}: what needs planning?`});
      textarea.value = store.get(key) || '';
      const status = el('small', {class: 'muted', text: ''});
      textarea.addEventListener('input', () => {
        store.set(key, textarea.value);
        status.textContent = 'Saved in this browser';
      });
      return el('label', {class: 'next-week-note'}, el('strong', {text: cls.name}), textarea, status);
    }));
}

function block(label, text) {
  return el('div', {class: 'block'}, el('p', {class: 'label', text: label}), el('p', {class: 'plan', text}));
}

function renderLesson(cls, lesson, weekday) {
  const box = el('div', {class: 'lesson', id: `lesson-${cls.key}-${lesson.row}`, tabindex: '-1'}, el('h3', {text: lesson.topic || 'Untitled lesson'}));
  const today = lesson.today;
  if (today) {
    box.append(el('div', {class: 'today'},
      el('p', {class: 'label', text: FULL_DAY[weekday] + ' plan'}),
      el('p', {class: 'plan', text: today.plan || 'No plan written for this day yet.'}),
      today.note ? el('p', {class: 'teacher-note'}, el('strong', {text: 'Note: '}), today.note) : null));
  } else if (weekday === 'Sat' || weekday === 'Sun') {
    box.append(note('Pick a weekday to see its plan.', 'info'));
  } else {
    box.append(note(`No ${FULL_DAY[weekday]} plan column: ${cls.name} doesn't meet on ${FULL_DAY[weekday]}s.`, 'info'));
  }
  if (lesson.todo) box.append(block('To-do', lesson.todo));
  if (lesson.prep) box.append(block('Lab / prep', lesson.prep));
  if (lesson.materials.length) {
    box.append(el('div', {class: 'links'}, lesson.materials.map(m => link(m.label + ' ↗', m.url, 'chip'))));
  }
  box.append(el('details', {class: 'week'}, el('summary', {text: 'Whole week'}),
    lesson.plans.map(p => el('div', {class: 'week-day'}, el('strong', {text: p.day}),
      el('p', {class: 'plan', text: p.plan || '—'}), p.note ? el('p', {class: 'teacher-note', text: p.note}) : null))));
  if (Object.keys(lesson.editable || {}).length) {
    box.append(el('div', {class: 'action-row'}, el('button', {type: 'button', onclick: () => startEdit(cls, lesson)}, 'Edit here')));
  }
  return box;
}

// ---------- Editing a lesson and saving it to the Agenda ----------
// A lesson's plain-text cells and its links (deck, activity, Turn-in Forms)
// are edited here; cells that are formulas stay in the Agenda. The server
// writes a field only if the Agenda still says what this page started from.
function lessonValues(lesson) {
  const values = {topic: lesson.topic, todo: lesson.todo, prep: lesson.prep};
  for (const p of lesson.plans) { values['plan:' + p.day] = p.plan; values['note:' + p.day] = p.note; }
  return Object.fromEntries([
    ...Object.keys(lesson.editable || {}).map(key => [key, values[key] ?? '']),
    ...Object.entries(lesson.links || {}).flatMap(([key, url]) => [['link:' + key, url || ''],
      ...(key in (lesson.link_labels || {}) ? [['label:' + key, (lesson.link_labels[key] || '').replace(/^📤\s*/, '')]] : [])])]);
}

const LINK_LABELS = {'link:deck': 'Deck link (your Google Slides link; students get a copy link)',
  'link:deck2': '2nd deck link (a second lesson this week)',
  'link:activity': 'Activity / guide link', 'link:turnin': 'Turn-in Form link (the Form’s own editor link)',
  'label:turnin': 'Its name (what students and IC call it)',
  'link:turnin2': '2nd Turn-in Form link (the second lesson’s)', 'label:turnin2': 'Its name (what students and IC call it)'};
const fieldLabel = key => ({topic: 'Topic', todo: 'To-do', prep: 'Lab / prep'})[key] || LINK_LABELS[key]
  || `${FULL_DAY[key.split(':')[1]]} ${key.startsWith('plan:') ? 'plan' : 'note'}`;

function startEdit(cls, lesson) {
  const base = lessonValues(lesson);
  plans.editing = {key: `${cls.key}|${lesson.row}`, cls: cls.key, week: lesson.week, row: lesson.row,
                   base, draft: {...base}, current: null, error: null, busy: false};
  renderPlans(plans.data);
}

function lessonEditor(cls, lesson) {
  const edit = plans.editing;
  const form = el('form', {class: 'lesson-edit', onsubmit: event => { event.preventDefault(); saveLesson(); }},
    el('h3', {text: `Edit ${lesson.topic || 'this lesson'}`}));
  if (edit.error) form.append(note(edit.error));
  for (const key of Object.keys(edit.base)) {
    const locked = lesson.editable[key] === false;
    const id = `edit-${cls.key}-${lesson.row}-${key.replace(':', '-')}`;
    const input = key === 'topic' || key.startsWith('link:') || key.startsWith('label:')
      ? el('input', {id, type: key.startsWith('link:') ? 'url' : 'text', disabled: locked || edit.busy,
                     placeholder: key.startsWith('link:') ? 'Paste a link, or leave empty' : ''})
      : el('textarea', {id, rows: key.startsWith('plan:') ? 4 : 2, disabled: locked || edit.busy});
    input.value = edit.draft[key];
    input.addEventListener('input', () => { edit.draft[key] = input.value; });
    form.append(...[el('label', {class: 'field', for: id, text: fieldLabel(key)}), input,
      locked ? el('small', {class: 'muted', text: 'This cell has a link or formula, so edit it in the Agenda.'}) : null,
      edit.current && key in edit.current
        ? el('p', {class: 'note warn'}, 'The Agenda now says: ', el('q', {text: edit.current[key] || '(empty)'})) : null,
    ].filter(Boolean));
  }
  form.append(el('div', {class: 'action-row'},
    el('button', {type: 'submit', class: 'primary', disabled: edit.busy}, edit.busy ? 'Saving…' : 'Save to Agenda'),
    el('button', {type: 'button', disabled: edit.busy, onclick: () => { plans.editing = null; renderPlans(plans.data); }}, 'Cancel'),
    link('Open this row in the Agenda ↗', lesson.edit_url, 'edit')));
  form.append(moveSection(cls, lesson));
  return form;
}

// ---------- Moving a lesson to another week ----------
// Like reordering a list: the lesson goes to the week picked and the lessons
// between slide one week to make room, skipping weeks with no class or
// nothing planned. Each week keeps its dates, so the Agenda stays in order.
const closedWeek = topic => /^\[/.test(topic || '') && !/^\[\s*empty/i.test(topic);
const freeWeek = topic => !(topic || '').trim() || /^\[\s*empty/i.test(topic);
async function loadClassWeeks(key) {
  if (plans.weeks[key] || plans.weeksLoading[key]) return;
  plans.weeksLoading[key] = true;
  try { plans.weeks[key] = (await getJSON(`api/weeks?class=${key}&today=${todayISO()}`)).weeks; }
  catch { plans.weeks[key] = []; }
  plans.weeksLoading[key] = false;
  if (plans.editing?.cls === key) renderPlans(plans.data);
}

function moveSection(cls, lesson) {
  const edit = plans.editing;
  const weeks = plans.weeks[cls.key];
  const box = el('div', {class: 'move-lesson'}, el('p', {class: 'label', text: 'Move this lesson to another week'}));
  if (!weeks) { loadClassWeeks(cls.key); box.append(el('p', {class: 'muted', text: 'Reading the weeks…'})); return box; }
  const others = weeks.filter(w => w.label !== lesson.week);
  if (!others.length) { box.append(el('p', {class: 'muted', text: 'No other weeks were found for this class.'})); return box; }
  const short = t => (t || 'nothing planned yet').length > 60 ? t.slice(0, 57) + '…' : (t || 'nothing planned yet');
  const select = el('select', {'aria-label': 'Week to move this lesson to', disabled: edit.busy},
    others.map(w => el('option', {value: w.label, text: `${w.label} · ${short(w.topic)}`, disabled: closedWeek(w.topic)})));
  const here = weeks.findIndex(w => w.label === lesson.week);
  const next = weeks[here + 1] || weeks[here - 1];
  if (next && next.label !== lesson.week && !closedWeek(next.topic)) select.value = next.label;
  box.append(el('div', {class: 'action-row'}, select,
      el('button', {type: 'button', disabled: edit.busy, onclick: () => moveLesson(cls, lesson, select.value)}, 'Move')),
    el('p', {class: 'muted', text: 'The lessons in between slide one week to make room, like reordering a list; weeks with no class or nothing planned are skipped, and every week keeps its dates. Moving into a week with nothing planned moves only this lesson. Plans, notes, links and Turn-in Forms move with each lesson.'}));
  return box;
}

async function moveLesson(cls, lesson, toWeek) {
  const target = (plans.weeks[cls.key] || []).find(w => w.label === toWeek);
  const other = freeWeek(target?.topic) ? `Nothing is planned in ${toWeek}, so only this lesson moves.`
    : `The lessons between ${lesson.week} and ${toWeek} slide one week to make room.`;
  if (!window.confirm(`Move “${lesson.topic || 'this lesson'}” to ${toWeek}? ${other}\n\nTitles that say a week number (Schoology, decks, Forms) don’t change.`)) return;
  const edit = plans.editing;
  edit.busy = true; edit.error = null;
  renderPlans(plans.data);
  try {
    const done = await postJSON('api/agenda/move', {class: cls.key, week: lesson.week, row: lesson.row, to_week: toWeek});
    if (plans.editing === edit) plans.editing = null;
    plans.weeks[cls.key] = null;
    await loadPlans(plans.date, true);
    const said = [`Moved “${done.moved.topic || 'the lesson'}” to ${done.moved.to}`];
    if (done.shifted.length) said.push(`slid one week: ${done.shifted.map(s => `“${s.topic}” to ${s.to}`).join(', ')}`);
    if (done.skipped.length) said.push(`skipped ${done.skipped.join(', ')}`);
    if (done.unconfirmed.length) said.push('some cells didn’t read back as expected, so check the Agenda');
    setStatus('plans-status', said.join('; ') + '.', Boolean(done.unconfirmed.length));
  } catch (error) {
    edit.busy = false; edit.error = error.message;
    if (plans.editing === edit) renderPlans(plans.data);
  }
}

async function saveLesson() {
  const edit = plans.editing;
  const changes = {};
  for (const [key, value] of Object.entries(edit.draft)) {
    if (key.startsWith('label:')) continue;
    if (value !== edit.base[key]) changes[key] = {from: edit.base[key], to: value};
  }
  // A new name for the 2nd Turn-in goes with its link, whether or not the link changed.
  for (const key of Object.keys(edit.draft).filter(k => k.startsWith('label:'))) {
    const link = 'link:' + key.slice(6);
    if (edit.draft[key] !== edit.base[key]) changes[link] = {from: edit.base[link], to: edit.draft[link], label: edit.draft[key]};
    else if (changes[link]) changes[link].label = edit.draft[key];
  }
  if (!Object.keys(changes).length) { plans.editing = null; renderPlans(plans.data); return; }
  edit.busy = true; edit.error = null;
  renderPlans(plans.data);
  try {
    const done = await postJSON('api/agenda', {class: edit.cls, week: edit.week, row: edit.row, changes});
    plans.weeks[edit.cls] = null;  // a new topic shows in the Move list
    if (plans.editing === edit) plans.editing = null;
    await loadPlans(plans.date, true);
    setStatus('plans-status', done.unconfirmed.length
      ? `Saved, but the Agenda didn’t read back as expected for: ${done.unconfirmed.map(fieldLabel).join(', ')}. Check the Agenda.`
      : 'Saved to the Agenda.', Boolean(done.unconfirmed.length));
  } catch (error) {
    edit.busy = false;
    const current = error.status === 409 ? error.body?.current || {} : {};
    if (Object.keys(current).length) {
      // Saving again replaces what the Agenda says now with what's typed here.
      edit.current = current;
      Object.assign(edit.base, current);
      edit.error = 'Part of this lesson changed in the Agenda since you opened it; its new text is under each field. '
        + 'Save again to replace it with what you typed here, or Cancel to keep the Agenda’s.';
    } else {
      edit.error = error.message;
    }
    if (plans.editing === edit) renderPlans(plans.data);
  }
}

function renderPlans(data) {
  plans.data = data;
  const editing = plans.editing;
  if (editing && !data.classes.some(cls => cls.lessons.some(l => `${cls.key}|${l.row}` === editing.key))) plans.editing = null;
  $('week-label').textContent = data.week ? `${data.week.label} · ${weekRange(data.week.monday)}` : 'No Agenda week for this date';
  $('week-closures').textContent = data.week?.closures || '';
  const cards = data.classes.map(cls => {
    const card = el('article', {class: 'card ' + cls.key}, el('h2', {text: cls.name}));
    if (cls.problem) card.append(note(cls.problem));
    cls.lessons.forEach(lesson => card.append(plans.editing?.key === `${cls.key}|${lesson.row}`
      ? lessonEditor(cls, lesson) : renderLesson(cls, lesson, data.weekday)));
    return card;
  });
  $('cards').replaceChildren(...data.problems.map(p => note(p)), ...cards);
  if (plans.focusRow) {
    const target = document.getElementById('lesson-' + plans.focusRow.replace('|', '-'));
    plans.focusRow = null;
    if (target) requestAnimationFrame(() => { target.scrollIntoView({behavior: 'smooth', block: 'center'}); target.focus?.(); });
  }
}

// ---------- Student work ----------
const work = {cls: store.get('class') || 'forensics', week: null, section: store.get('section') || 'all', weeksFor: null, data: null, seq: 0, scores: {}, ui: {}, marks: {}, reviewContexts: new Map()};
const sourceMatchesHeld = () => !!work.data?.other_files?.length;
function weekAssessmentHold(data = work.data) {
  if (data?.assessment_problem) return 'This week’s submission route needs confirmation before scoring or IC entry.';
  const policy = data?.week_policy;
  if (policy?.problem) return 'This week’s assessment scope needs confirmation before scoring or IC entry.';
  if (policy?.excluded === true) return recordProblem(policy.reason || 'This week was not assigned this school year.');
  return '';
}
function omittedFormProblem(lesson, data = work.data) {
  return data?.week_policy?.excluded === true && !data.week_policy.problem && !lesson.forms?.length
    && lesson.problem === "This lesson has no Form link in its 'Turn it in' column.";
}
const SOURCE_MATCH_HOLD = 'Scoring and IC entry are on hold until every submission in this week’s turn-in folders has a confirmed lesson match. Resolve “Confirm these submission matches” below, then refresh Student work.';

const studentRecords = {cls: null, seq: 0, data: null, readAt: 0};
function recordFolderURL(value) {
  try {
    const url = new URL(value);
    return url.origin === 'https://drive.google.com' && !url.username && !url.password
      && /^\/drive\/folders\/[A-Za-z0-9_-]+$/.test(url.pathname) ? url.href : null;
  } catch { return null; }
}
function recordDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return 'No dated records yet';
  const date = at(value);
  return Number.isNaN(date.getTime()) ? 'No dated records yet'
    : date.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'});
}
function recordProblem(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  return text.length <= 600 && /[A-Za-z]{2,}\s+[A-Za-z]{2,}/.test(text)
    && !/[_\u0000-\u001f]|https?:\/\/|Traceback|Bearer\s|\{[^}]*\}/i.test(text)
    ? text : 'A record could not be confirmed. Existing records are preserved.';
}
function recordLabel(value) {
  return typeof value === 'string' && value.trim().length <= 160 && !/[\u0000-\u001f]/.test(value) ? value.trim() : '';
}
function studentFilingNote(data) {
  const filing = data.filing;
  if (!filing || filing.coverage === 'unverified') return {brief: note('Filing coverage has not been verified. Available student folders do not confirm that all submissions have been filed.', 'info')};
  const scans = Array.isArray(filing.scans) ? filing.scans.filter(scan => scan?.class_key === data.class_key) : [];
  const pending = [];
  for (const scan of scans.filter(scan => scan.status === 'held' || scan.held_count > 0)) {
    const context = [CLASSES.find(([key]) => key === data.class_key)?.[1] || recordLabel(data.class_name),
      recordLabel(scan.week) || 'Week unavailable'].filter(Boolean).join(' · ');
    const held = Array.isArray(scan.held) ? scan.held : [];
    for (const row of held) pending.push([context, recordLabel(row?.name), recordLabel(row?.section), recordLabel(row?.assignment), recordLabel(row?.source_file_names)].filter(Boolean).join(' · ') + ': ' + recordProblem(row?.problem));
    for (const problem of Array.isArray(scan.problems) ? scan.problems : []) pending.push(context + ': ' + recordProblem(problem));
    if (!held.length && !scan.problems?.length) pending.push(context + ': The filing check did not finish. Existing records are preserved.');
  }
  if (filing.coverage === 'holds_remain' || filing.held_count > 0 || pending.length) {
    const count = Number.isInteger(filing.held_count) && filing.held_count > 0 ? filing.held_count : null;
    return {
      brief: el('p', {class: 'note info', text: count ? `${count} filing ${count === 1 ? 'hold remains' : 'holds remain'}. Checks cover recorded weeks only.` : 'Student filing is pending. Checks cover recorded weeks only.'}),
      summary: `Filing checks · ${count ? count + (count === 1 ? ' record' : ' records') : 'records'} awaiting review`,
      detail: el('ul', {}, [...new Set(pending.length ? pending : ['The pending filing reason is not available. Existing records are preserved.'])].map(text => el('li', {text}))),
    };
  }
  return {brief: el('p', {class: 'muted', text: 'Filing checks cover recorded weeks only; they do not confirm that all submissions have been filed.'})};
}
function renderStudentRecords(data) {
  const body = $('student-records-body');
  body.replaceChildren();
  const teacherURL = recordFolderURL(data.teacher_reports_url);
  if (teacherURL) body.append(el('p', {}, link(`Open ${data.class_name} teacher reports`, teacherURL)));
  if (!data.configured || data.status === 'unconfigured') {
    body.append(note('Student records are not available for this class yet.', 'info')); return;
  }
  const problems = data.status === 'held' && Array.isArray(data.problems) ? data.problems : [];
  if (data.status === 'held') {
    if (problems.length || !(data.filing?.coverage === 'holds_remain' || data.filing?.held_count > 0))
      body.append(note('Some student records could not be confirmed.', 'info'));
  }
  const filing = studentFilingNote(data);
  body.append(filing.brief);
  body.append(el('p', {class: 'muted', text: 'Open a student’s folder to check dated work or reprint saved feedback. Teacher reports stay in the class’s separate folder.'}));
  if (!data.students.length) {
    body.append(el('p', {class: 'muted', text: data.status === 'held' ? 'No student folders could be confirmed.' : 'No student records have been filed for this class yet.'}));
  } else body.append(el('div', {class: 'table-wrap'}, el('table', {class: 'student-records-table'},
    el('thead', {}, el('tr', {}, ['Student', 'Section', 'Status', 'Latest record', 'Folder'].map(text => el('th', {text})))),
    el('tbody', {}, data.students.map(student => {
      const folderURL = recordFolderURL(student.folder_url);
      return el('tr', {}, el('td', {text: student.name}), el('td', {text: student.section || '—'}),
        el('td', {text: studentRecordStatus(student)}),
        el('td', {text: recordDate(student.latest_record_date)}),
        el('td', {}, folderURL ? link('Open folder', folderURL) : el('span', {class: 'muted', text: 'Folder unavailable'})));
    })))));
  if (filing.detail || problems.length) body.append(el('details', {},
    el('summary', {text: filing.summary || 'Filing checks · records awaiting review'}),
    problems.length ? el('ul', {class: 'muted'}, [...new Set(problems.map(recordProblem))].map(text => el('li', {text}))) : null,
    filing.detail));
}
function studentRecordStatus(student) {
  const roster = student.roster_status;
  if (roster?.verified === true && roster.status === 'withdrawn' && roster.active === false)
    return `Withdrawn${/^\d{4}-\d{2}-\d{2}$/.test(roster.end_date || '') ? ' · ' + recordDate(roster.end_date) : ''} · history retained`;
  if (roster?.verified === true && roster.status === 'active' && roster.active === true) return 'Active in IC';
  return student.status === 'current' ? 'Current' : student.status === 'retained' ? 'Retained history'
    : student.status === 'identity_unverified' ? 'Filed by name · identity unverified'
    : student.status === 'unverified' ? 'Roster unverified' : 'Status unavailable';
}
async function loadStudentRecords(fresh = false) {
  const panel = $('student-records');
  if (!panel) return;
  const cls = work.cls;
  if (studentRecords.cls !== cls) {
    studentRecords.cls = cls; studentRecords.data = null; studentRecords.readAt = 0; studentRecords.seq++;
    $('student-records-body').replaceChildren();
  }
  $('student-records-summary').textContent = `Student records · ${CLASSES.find(([key]) => key === cls)?.[1] || 'Class'}`;
  if (!panel.open) return;
  if (!fresh && studentRecords.data && Date.now() - studentRecords.readAt < 60_000) { renderStudentRecords(studentRecords.data); return; }
  const seq = ++studentRecords.seq;
  $('student-records-body').replaceChildren(el('p', {class: 'muted', text: 'Reading student records…'}));
  try {
    const data = await getJSON(`api/student-records?class=${encodeURIComponent(cls)}`);
    if (seq !== studentRecords.seq || work.cls !== cls) return;
    if (data?.schema !== 'hub-student-archive/v1' || data.class_key !== cls || !Array.isArray(data.students))
      throw new Error('Student records could not be confirmed for this class. Reload to try again.');
    renderStudentRecords(data);
    studentRecords.data = data; studentRecords.readAt = Date.now();
  } catch (error) {
    if (seq === studentRecords.seq && work.cls === cls) $('student-records-body').replaceChildren(note(
      /^Please sign in/.test(error.message) ? error.message : 'Student records could not be loaded for this class. Reload to try again.', 'info'));
  }
}

function fillSelect(select, options, value) {
  select.replaceChildren(...options.map(([v, label]) => el('option', {value: v, text: label})));
  if (options.some(([v]) => v === value)) select.value = value;
}

async function loadWeeks(fresh = false) {
  setStatus('work-status', 'Reading the Agenda…');
  const data = await getJSON(`api/weeks?class=${work.cls}&today=${todayISO()}${fresh ? '&fresh=1' : ''}`);
  showAgendaLink(data.agenda);
  work.weeksFor = data.class_key;
  const weeks = data.weeks;
  if (!weeks.some(w => w.label === work.week)) work.week = data.current || weeks.at(-1)?.label || null;
  fillSelect($('work-week'), weeks.map(w => [w.label, `${w.label} · ${w.topic || 'no topic'}${w.forms ? '' : ' (no Form)'}`]), work.week);
  if (!data.sections.includes(work.section)) work.section = 'all';
  fillSelect($('work-section'), [['all', 'All sections'], ...data.sections.map(s => [s, s])], work.section);
}

async function openWork([cls, week] = []) {
  closeSheets();
  if (CLASSES.some(([key]) => key === cls)) work.cls = cls;
  if (week) work.week = week;
  $('work-class').value = work.cls;
  loadStudentRecords(true);
  try {
    if (work.weeksFor !== work.cls) await loadWeeks();
    else $('work-week').value = work.week;
    await loadWork();
  } catch (error) { setStatus('work-status', error.message, true); }
}

async function loadWork(fresh = false) {
  closeSheets();
  if (!work.week) { setStatus('work-status', 'No weeks were found for this class in the Agenda.', true); return; }
  history.replaceState(null, '', `#work/${work.cls}/${encodeURIComponent(work.week)}`);
  const seq = ++work.seq;
  // Background helper replies must not restore the previous week's cards.
  work.data = null;
  work.scores = {};
  work.ui = {};
  work.marks = {};
  work.reviewContexts = new Map();
  setStatus('work-status', 'Finding student work from the Form responses…');
  $('work-body').replaceChildren();
  try {
    const data = await getJSON(`api/work?class=${work.cls}&week=${encodeURIComponent(work.week)}${fresh ? '&fresh=1' : ''}`);
    if (seq !== work.seq) return;
    work.data = data;
    work.scores = {};
    work.ui = {};
    work.marks = Object.fromEntries(data.lessons.flatMap(l => l.forms).filter(f => f.form_id)
      .map(form => [form.form_id, {pending: true}]));
    renderWork();
    await loadMarks(data.lessons.flatMap(l => l.forms).filter(f => f.form_id), seq);
    if (seq !== work.seq) return;
    setStatus('work-status', `Read ${when(new Date().toISOString())}.`);
    // Score every assignment right away; results are kept on the server, so this is quick after the first time.
    for (const form of data.lessons.flatMap(l => l.forms)) {
      if (seq !== work.seq) return;
      if (form.form_id && assessmentRows(form).some(row => row.files.length)) await scoreForm(form);
    }
  } catch (error) {
    if (seq === work.seq) setStatus('work-status', error.message, true);
  }
}

function fileList(files) {
  if (!files.length) return el('span', {class: 'muted', text: 'Form sent with no file'});
  return el('ul', {class: 'files'}, files.map(f => el('li', {},
    link(f.shown || f.name, f.url),
    f.from_folder ? el('span', {class: 'badge', title: 'In the week’s turn-in folder but not in this Form’s responses', text: 'From turn-in folder'}) : null,
    f.from_shared ? el('span', {class: 'badge', title: 'Turned in through the earlier shared Form', text: 'Earlier shared Form'}) : null,
    f.via ? el('span', {class: f.accepted_intake === true ? 'badge' : 'badge warn', title: 'This lesson’s deck, turned in through another lesson’s Form; it counts here', text: `Via ${f.via}`}) : null,
    f.other_week ? el('span', {class: 'badge warn', title: 'The file name mentions a different week', text: `Named Week ${f.other_week}`}) : null,
    f.late ? el('span', {class: 'badge warn', title: 'Turned in after this assignment’s original two-week window', text: 'Late'}) : null,
    f.trashed ? el('span', {class: 'badge warn', text: 'In Drive trash'}) : null,
    f.problem ? el('span', {class: 'badge', title: f.problem, text: 'Open it yourself'}) : null)));
}

// ---------- Completion scores (Notes and Revision, out of 10) ----------
const DECKS = ['application/vnd.google-apps.presentation', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'];
const scoreFile = row => row.files.find(f => DECKS.includes(f.mime)) || row.files[0];
const fmt = value => value == null ? 'N/A' : String(value);
// Once an assignment's first turn-in appears (its two weeks have begun), every
// empty score is 0, and a 0 is replaced when the student turns work in before
// the two weeks close. A confirmed entry after the close is rechecked using
// current submitted files only; it never creates a new missing-work zero.
const closedFor = form => Boolean(form.window && new Date(form.window.at) < new Date());
function entryTime(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);
  if (!match) return NaN;
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()
      || hour > 23 || minute > 59 || second > 59) return NaN;
  return Date.parse(text);
}
function closedRecheck(form) {
  const mark = work.marks[form.form_id];
  if (mark?.configured !== true || mark.problem) return false;
  const cutoff = entryTime(form.window?.at), entered = entryTime(mark.entered_at?.at), now = Date.now();
  return Number.isFinite(cutoff) && Number.isFinite(entered) && cutoff < now && cutoff <= entered && entered <= now;
}
function assessmentRows(form) {
  if (!closedRecheck(form)) return form.combined_assessment ? form.scoring_students || [] : form.students;
  const rows = work.data?.entry_rechecks?.[form.form_id]?.rows;
  return Array.isArray(rows) && rows.every(row => row?.matched === true && Array.isArray(row.files)) ? rows : [];
}
function entryHistoryHold(form) {
  const mark = work.marks[form.form_id];
  if (mark?.pending) return 'Checking this assignment’s recorded IC entry before preparing scores…';
  if (!closedFor(form) || !mark) return '';
  const entered = entryTime(mark.entered_at?.at);
  if (mark.configured !== true || mark.problem || (mark.entered_at && (!Number.isFinite(entered) || entered > Date.now()))) {
    return 'This closed assignment’s entry history could not be verified. Reload Student work before preparing an IC list; no new missing-work zero is prepared.';
  }
  const view = work.data?.entry_rechecks?.[form.form_id];
  if (closedRecheck(form) && (!Array.isArray(view?.rows) || view.rows.some(row => row?.matched !== true || !Array.isArray(row.files)))) {
    return 'Current submitted files for this closed assignment could not be verified. Reload Student work before preparing an IC list; no new missing-work zero is prepared.';
  }
  return '';
}
function recheckSelectionHold(form) {
  if (!closedRecheck(form) && !form.combined_assessment) return '';
  const issue = assessmentRows(form).find(row => row.selection_problem);
  if (!issue) return '';
  const context = `${CLASSES.find(([key]) => key === work.cls)?.[1] || work.cls} · ${work.week} · ${form.label || 'this assignment'}`;
  const student = `${issue.last}, ${issue.first}${issue.section ? ` (${issue.section})` : ''}`;
  return `${context} · ${student}: ${issue.selection_problem} Scoring and IC entry are held for this assignment; no new missing-work zero is prepared.`;
}
// Its two weeks have begun (they never begin before the lesson's own week): only then do 0s count.
const startedFor = form => Boolean(form.window && (!form.window.from || new Date(form.window.from) <= new Date()));
const shown = (score, started = true) => !score ? (started ? ['0', '0'] : ['', ''])
  : [score.notes_score, score.revision_score].map(fmt);
const STATUS = {blank: 'blank', copied: 'looks copied from the slide', 'not found': 'its code isn’t in this copy'};

function scoreCells(row, scores, formId) {
  if (!scores) return [];
  const result = scores[scoreFile(row)?.id];
  if (!row.files.length) return [el('td', {text: '—'}), el('td', {text: '—'})];
  if (!result) return [el('td', {class: 'muted', text: '…'}), el('td', {class: 'muted', text: '…'})];
  if (result.problem) return [el('td', {colspan: 2}, scoreReviewNotice(result, formId) || el('small', {class: 'muted', text: result.problem}))];
  const [notesShown, revisionShown] = shown(result);
  const c = result.counts;
  // Each score is the share of this deck's own coded boxes that were done, on a 10-point scale.
  const cell = (value, done, required, what) => el('span', {}, el('strong', {text: value}),
    required ? el('small', {class: 'muted', text: ` · ${Math.round(100 * done / required)}% (${done} of ${required} ${what})`}) : null);
  const todo = result.items.filter(i => i.status !== 'done' && i.category !== 'OPTIONAL');
  const detail = el('details', {class: 'earlier'},
    el('summary', {text: todo.length ? `What’s not counted yet (${todo.length})` : 'Everything counted'}),
    todo.length
      ? el('ul', {class: 'files'}, todo.map(i => el('li', {}, `${i.category} ${i.id}${i.slide ? ` (slide ${i.slide})` : ''} — ${STATUS[i.status] || i.status}`)))
      : el('p', {class: 'muted', text: 'Every coded box has writing.'}));
  return [el('td', {}, cell(notesShown, c.NOTES.done + c.BANK.done, c.NOTES.required + c.BANK.required, 'notes boxes and Concept Bank terms'),
    detail, result.missing_code_warning ? el('small', {class: 'muted', text: result.missing_code_warning}) : null,
    result.note ? scoreReviewNotice(result, formId) || el('small', {class: 'note warn', text: result.note}) : null),
    el('td', {}, cell(revisionShown, c.DRAFT.done, c.DRAFT.required, 'revised answers'))];
}

async function scoreForm(form) {
  if (sourceMatchesHeld() || weekAssessmentHold() || entryHistoryHold(form) || recheckSelectionHold(form) || form.grade_readiness?.status !== 'READY') return;
  const rows = assessmentRows(form).filter(r => r.files.length && !r.selection_problem);
  const scores = work.scores[form.form_id] = work.scores[form.form_id] || {};
  const queue = rows.filter(r => !scores[scoreFile(r).id] || scores[scoreFile(r).id].code === 'deck_not_ready');
  const seq = work.seq;
  let done = rows.length - queue.length;
  work.ui[form.form_id] = {...work.ui[form.form_id], progress: `Scoring ${done} of ${rows.length}…`};
  renderWork();
  const worker = async () => {
    while (queue.length && seq === work.seq && !sourceMatchesHeld() && !weekAssessmentHold()) {
      const id = scoreFile(queue.shift()).id;
      try {
        scores[id] = await getJSON(`api/score?class=${work.cls}&week=${encodeURIComponent(work.week)}&form=${encodeURIComponent(form.form_id)}&file=${encodeURIComponent(id)}`);
      } catch (error) { scores[id] = {problem: error.message}; }
      if (seq !== work.seq) return;
      done += 1;
      work.ui[form.form_id].progress = `Scoring ${done} of ${rows.length}…`;
      renderWork();
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  if (seq !== work.seq) return;
  work.ui[form.form_id].progress = '';
  renderWork();
}

// ---------- Answers by question (Biology and A&P) ----------
const KIND = {NOTES: 'Notes', DRAFT: 'Revised', BANK: 'Concept Bank', OPTIONAL: 'Optional'};
const KIND_ORDER = ['NOTES', 'DRAFT', 'BANK', 'OPTIONAL'];

function answerCell(item) {
  if (!item || item.status === 'not found') return el('td', {class: 'muted', text: 'code missing'});
  if (item.status === 'blank' || !item.answer) return el('td', {class: 'muted', text: '—'});
  return el('td', {}, item.answer,
    item.status === 'copied' ? el('span', {class: 'badge warn', text: 'copied from the slide'}) : null);
}

function answersPanel(form, scores) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const students = assessmentRows(form).filter(r => r.matched && !r.selection_problem && inSection(r))
    .map(r => ({r, s: scores[scoreFile(r)?.id]})).filter(x => x.s && !x.s.problem && x.s.items);
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Answers by question'}));
  if (!students.length) { panel.append(note('No scored decks to show yet.', 'info')); return panel; }
  // Every coded box, grouped by the slide of the handed-out deck it sits on.
  const boxes = new Map();
  for (const {s} of students) {
    for (const item of s.items) {
      const key = `${item.category}|${item.id}`;
      const box = boxes.get(key) || {category: item.category, id: item.id, at: null, question: ''};
      if (box.at == null && item.at != null) Object.assign(box, {at: item.at, question: item.question || ''});
      boxes.set(key, box);
    }
  }
  const slides = new Map();
  for (const box of boxes.values()) {
    const where = box.at ?? Infinity;
    if (!slides.has(where)) slides.set(where, {at: where, question: box.question, boxes: []});
    slides.get(where).boxes.push(box);
  }
  panel.append(el('p', {class: 'muted', text: `What ${students.length} student${students.length === 1 ? '' : 's'} wrote in each coded box of their ${closedRecheck(form) ? 'current submitted' : 'on-time'} deck${section === 'all' ? '' : ` (${section})`}. ${closedRecheck(form) ? 'Verified late submissions are included in this recheck. ' : 'Late work isn’t included. '}Decks that couldn’t be scored aren’t included.`}));
  for (const slide of [...slides.values()].sort((a, b) => (a.at === b.at ? 0 : a.at < b.at ? -1 : 1))) {
    slide.boxes.sort((a, b) => KIND_ORDER.indexOf(a.category) - KIND_ORDER.indexOf(b.category) || a.id.localeCompare(b.id));
    panel.append(el('h5', {class: 'answers-slide', text: slide.at === Infinity
      ? 'Codes no student’s copy has'
      : `Slide ${slide.at}${slide.question ? ' · ' + slide.question : ''}`}));
    panel.append(el('div', {class: 'table-wrap'}, el('table', {class: 'answers'},
      el('thead', {}, el('tr', {}, el('th', {text: 'Student'}),
        slide.boxes.map(b => el('th', {text: `${KIND[b.category] || b.category} · ${b.id}`})))),
      el('tbody', {}, students.map(({r, s}) => el('tr', {},
        el('td', {}, el('strong', {text: `${r.last}, ${r.first}`}),
          section === 'all' ? el('small', {class: 'muted', text: ' · ' + r.section}) : null),
        slide.boxes.map(b => answerCell(s.items.find(i => i.category === b.category && i.id === b.id)))))))));
  }
  return panel;
}

function uncodedReport(scores, form) {
  const rows = assessmentRows(form).filter(row => row.files.length && !row.selection_problem).map(row => ({
    row, result: scores[scoreFile(row)?.id],
  })).filter(item => item.result?.uncoded?.length);
  if (!rows.length) return null;
  const slides = [...new Set(rows.flatMap(({result}) => result.uncoded.map(item => item.slide)))].sort((a, b) => a - b);
  const key = `embed-coded:${work.cls}:${work.week}:${form.form_id}`;
  const checked = store.get(key) === 'done';
  const checkbox = el('input', {type: 'checkbox', checked, 'aria-label': `Mark embed coding done for ${form.label}`});
  const status = el('small', {class: 'muted', text: checked ? 'Marked done in this browser.' : 'Reminder saved in this browser.'});
  checkbox.addEventListener('change', () => {
    store.set(key, checkbox.checked ? 'done' : 'todo');
    status.textContent = checkbox.checked ? 'Marked done in this browser.' : 'Reminder saved in this browser.';
  });
  return el('section', {class: 'note info coding-followup'},
    el('p', {text: `Some writing was not scored because its boxes have no code. Affected submissions: ${rows.map(({row, result}) => `${row.last}, ${row.first} (${[...new Set(result.uncoded.map(item => item.slide))].sort((a, b) => a - b).map(n => `slide ${n}`).join(', ')})`).join('; ')}. Slide(s) ${slides.join(', ')}.`}),
    el('label', {class: 'task-check'}, checkbox, ' Run the embed-coding program on this deck before the next handout.'),
    status);
}


async function postJSON(url, body) {
  const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const error = new Error(data?.error || `The server returned an error (${response.status}).`);
    error.status = response.status; error.body = data;
    throw error;
  }
  return data;
}

async function openPanel(form, which) {
  const seq = work.seq;
  const ui = work.ui[form.form_id] = {...work.ui[form.form_id]};
  if (ui.open === which) { ui.open = null; renderWork(); return; }
  ui.open = which; ui.error = null; ui.busy = which === 'feedback' ? 'Finding students with revised answers…' : null;
  renderWork();
  if (which !== 'feedback') return;
  try {
    ui.feedback = await getJSON(`api/feedback/native?class=${work.cls}&week=${encodeURIComponent(work.week)}&form=${encodeURIComponent(form.form_id)}`);
  } catch (error) { ui.error = error.message; }
  ui.busy = null;
  if (seq === work.seq && work.ui[form.form_id] === ui) renderWork();
}

async function loadMarks(forms, seq) {
  await Promise.all(forms.map(async form => {
    try {
      const sheet = await getJSON(`api/ledger?class=${work.cls}&week=${encodeURIComponent(work.week)}&form=${encodeURIComponent(form.form_id)}`);
      if (seq === work.seq) work.marks[form.form_id] = sheet;
    } catch {
      if (seq === work.seq) work.marks[form.form_id] = {configured: false, problem: 'Entry history could not be read.'};
    }
  }));
  if (seq === work.seq) renderWork();
}

async function recordMark(form, kind) {
  const ui = work.ui[form.form_id];
  ui.busy = 'Noting it…'; ui.error = null; renderWork();
  try {
    const done = await postJSON('api/record', {class: work.cls, week: work.week, form: form.form_id, kind,
      note: kind === 'ic' ? 'Clicked on the Hub' : ''});
    work.marks[form.form_id] = done.sheet;
    const where = done.sheet?.name || 'Scores entered';
    ui.saved = kind === 'ic' ? `Added to “${where}”: this assignment is entered in IC.`
      : `Added to “${where}”: feedback made for this assignment.`;
    ui.open = null; ui.busy = null;
  } catch (error) { ui.error = error.message; ui.busy = null; }
  renderWork();
}

async function copyText(textValue, status) {
  try { await navigator.clipboard.writeText(textValue); status.textContent = ' Copied.'; }
  catch { status.textContent = ' Couldn’t copy; select the text below instead.'; }
}

const markDate = mark => mark ? when(mark.at) : '';

// Where an assignment stands for Infinite Campus. While its two weeks are
// open, every score goes in, 0s included; a 0 is replaced when the student
// turns work in. A confirmed entry after the close permits rechecking current
// submitted files, including late ones, without inferring new absence zeros.
function icStatus(form) {
  if (weekAssessmentHold()) return {kind: 'held', text: weekAssessmentHold()};
  if (sourceMatchesHeld()) return {kind: 'held', text: SOURCE_MATCH_HOLD};
  if (gradeReadinessHold(form)) return {kind: 'held', text: gradeReadinessHold(form)};
  if (entryHistoryHold(form)) return {kind: 'held', text: entryHistoryHold(form)};
  if (recheckSelectionHold(form)) return {kind: 'held', text: recheckSelectionHold(form)};
  if (form.combined_assessment) return {kind: 'open', text: 'IC: combine the actual coded work submitted for each lesson part. Missing parts stay visible and add no missing-work zero. The helper keeps higher scores.'};
  if (!form.window) return null;
  const sheet = work.marks[form.form_id];
  if (!startedFor(form)) {
    return {kind: 'open', text: `IC: its two weeks start ${form.window.opened}. Enter only the scores listed; 0s for work not turned in start then.`};
  }
  if (!closedFor(form)) {
    return {kind: 'open', text: `IC: open until ${form.window.label}. Enter every score listed, including the 0s; a 0 is replaced when the student turns work in before it closes.`};
  }
  if (closedRecheck(form)) {
    return {kind: 'recheck', text: `IC: entry recorded ${markDate(sheet.entered_at)}. Recheck current submitted files, including verified late submissions and revisions. The helper fills blanks and raises lower scores only; no new missing-work zero is prepared.`};
  }
  return {kind: 'due', text: `IC: closed ${form.window.label}. Final scores are due: enter every score, including the 0s, then click “I entered these in IC”.`};
}

function marksLine(form) {
  const sheet = work.marks[form.form_id];
  if (!sheet?.configured) return null;
  const parts = [sheet.entered_at ? `✓ ${gradeReadinessHold(form) ? 'Previously entered' : 'Entered'} in IC ${markDate(sheet.entered_at)}` : '',
    sheet.feedback_at ? `✓ Feedback made ${markDate(sheet.feedback_at)}` : ''].filter(Boolean);
  return parts.length ? el('p', {class: 'marks', text: parts.join(' · ')}) : null;
}

function icPanel(form, scores, lesson) {
  if (weekAssessmentHold()) return el('div', {class: 'ic-panel'}, note(weekAssessmentHold(), 'info'));
  if (sourceMatchesHeld()) return el('div', {class: 'ic-panel'}, note(SOURCE_MATCH_HOLD, 'info'));
  if (gradeReadinessHold(form)) return el('div', {class: 'ic-panel'}, note(gradeReadinessHold(form), 'info'));
  if (entryHistoryHold(form)) return el('div', {class: 'ic-panel'}, note(entryHistoryHold(form), 'info'));
  if (recheckSelectionHold(form)) return el('div', {class: 'ic-panel'}, note(recheckSelectionHold(form), 'info'));
  const section = work.section;
  const ui = work.ui[form.form_id] || {};
  const sheet = work.marks[form.form_id];
  const entered = sheet?.entered_at ? new Date(sheet.entered_at.at) : null;
  const closed = closedFor(form);
  const recheck = closedRecheck(form);
  const inSection = r => section === 'all' || r.section === section;
  const elsewhere = elsewhereKeys(form), accepted = acceptedIntakeKeys(form);
  const all = [
    // Unresolved source identity or response interpretation stays for review.
    ...assessmentRows(form).filter(r => r.matched && inSection(r)).map(r => ({r, s: scores[scoreFile(r)?.id]}))
      .filter(x => x.r.selection_problem || (x.s && !x.s.problem)).map(x => ({...x, unsure: Boolean(x.r.selection_problem || x.s?.note),
        show: x.r.selection_problem || x.s?.note ? ['check by hand', 'check by hand'] : shown(x.s)})),
    // A student whose submitted file still needs a lesson match is left
    // off the list (and named below) so a 0 isn't entered for them unchecked.
    // A student whose only turn-in was another lesson's deck (through this Form) is
    // checked by hand, not given a 0 unseen.
    ...(startedFor(form) && !form.combined_assessment ? [...form.missing.filter(m => !m.unsorted), ...(recheck ? [] : form.late.filter(r => r.matched))]
      .filter(r => inSection(r) && (!accepted.has(personKey(r)) || elsewhere.has(personKey(r))) && (!recheck || elsewhere.has(personKey(r))))
      .map(r => elsewhere.has(personKey(r)) ? {r, s: null, other: true, show: ['check by hand', 'check by hand']}
        : {r, s: null, zero: true, show: shown(null)}) : []),
    // Added to the roster after this assignment began: excused, never a 0.
    ...(form.excused || []).filter(inSection).map(r => ({r, s: null, excused: true, show: ['Excused', 'Excused']})),
  ].sort((a, b) => (a.r.section + a.r.last + a.r.first).localeCompare(b.r.section + b.r.last + b.r.first));
  // New since you last entered this assignment: turned in after that, or, for
  // a student with nothing, a 0 not entered yet or due again at the close.
  for (const x of all) {
    x.fresh = !entered || (x.excused ? false : x.zero ? closed && new Date(form.window.at) > entered
      : new Date(x.r.submitted_at) > entered);
  }
  const fresh = all.filter(x => x.fresh);
  const status = el('span', {class: 'muted'});
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Enter these scores in Infinite Campus'}));
  if (ui.busy) { panel.append(el('p', {class: 'muted', text: ui.busy})); return panel; }
  if (ui.error) panel.append(note(ui.error));
  if (sheet && !sheet.configured) panel.append(note(sheet.setup, 'info'));
  const standing = icStatus(form);
  if (standing) panel.append(el('p', {class: 'ic-status ' + standing.kind, text: standing.text}));
  if (!all.length) { panel.append(note('No scores to enter yet.', 'info')); return panel; }
  const canSend = helper.state === 'ready' && helperNeed(helperRows(form)).length;
  panel.append(el('p', {}, helper.state === 'ready'
    ? 'To check all mapped assignments, keep Infinite Campus signed in in School Chrome, open the School Workflow Hub extension, and choose “Run all mapped assignments now”. The helper keeps the higher score, saves eligible changes and checks them after reloading. The weekday morning routine uses the same mappings. For this assignment alone, choose “Send to the Chrome helper”, open its IC score panel ((F) for Notes or (S) for Revision), then choose “Hub helper” and review its list.'
    : 'The Chrome helper is not connected in this browser. Open the Hub and Infinite Campus in School Chrome with the School Workflow Hub extension, then reload the Hub. Manual entry is available in this assignment’s IC score panel.',
    entered ? ` Entry was recorded on ${markDate(sheet.entered_at)}; ${fresh.length ? `the ${fresh.length} marked “submitted since” turned in after that. ` : ''}Submission dates do not show later edits to the same file. Compare current scores in IC before confirming any entry.` : ''));
  if (ui.helper) panel.append(note(ui.helper, 'info'));
  const progress = helperProgress(form);
  if (progress) panel.append(progress);
  panel.append(el('div', {class: 'action-row'},
    canSend ? el('button', {type: 'button', class: 'primary', onclick: () => sendToHelper(form, lesson)}, 'Send to the Chrome helper') : null,
    sheet?.configured ? el('button', {type: 'button', onclick: () => recordMark(form, 'ic')},
      'I entered these in IC (click adds to Scores entered)') : null,
    status));
  if (sheet?.configured) {
    panel.append(el('p', {class: 'muted'}, 'The Hub adds “Entered in IC” to ',
      link(`“${sheet.name || 'Scores entered'}”`, sheet.url), ' by itself once the Chrome helper has entered every section and column. Click “I entered these in IC” only if you typed the scores into IC yourself. The recorded date is entry history; current submitted files can still be rechecked after the window closes.'));
  }
  panel.append(el('div', {class: 'table-wrap'}, el('table', {},
    el('thead', {}, el('tr', {}, ['Student', 'Section', 'Notes /10', 'Revision /10', ''].map(h => el('th', {text: h})))),
    el('tbody', {}, all.map(({r, s: result, show, zero, other, excused, unsure, fresh: isNew}) => el('tr', {}, el('td', {text: `${r.last}, ${r.first}`}), el('td', {text: r.section}),
      el('td', {text: show[0] || '—'}), el('td', {text: show[1] || '—'}),
      el('td', {}, zero ? el('span', {class: 'badge', text: closed ? 'nothing on time' : 'nothing yet'}) : null,
        other ? el('span', {class: 'badge warn', text: 'turned in another lesson’s deck through this Form'}) : null,
        excused ? el('span', {class: 'badge', title: `Added to the roster ${r.added}, after this assignment began`,
                              text: 'new student: mark Exempt in IC'}) : null,
        unsure ? el('span', {class: 'badge warn', title: r.selection_problem || result.note,
                             text: r.selection_problem || `Hub counted ${fmt(result.notes_score)} / ${fmt(result.revision_score)}; not sure`}) : null,
        entered && isNew ? el('span', {class: 'badge warn', text: `submitted since ${markDate(sheet.entered_at)}`}) : null)))))));
  const unsorted = startedFor(form) ? form.missing.filter(m => m.unsorted && inSection(m)) : [];
  if (unsorted.length) {
    panel.append(note(`Not listed: ${unsorted.map(m => `${m.last}, ${m.first}`).join('; ')}. `
      + 'Each has a submitted file awaiting a confirmed lesson match. Resolve “Confirm these submission matches” below; no new zero is sent while scoring is held.', 'info'));
  }
  panel.append(el('p', {class: 'muted', text: recheck
    ? 'This recheck uses current verified submissions, including late files and revisions. Students without a verified submission receive no new zero. Unmatched work and decks that couldn’t be scored are left for review. IC keeps the higher score.'
    : closed
    ? 'The two weeks are over, so students with nothing on time (including late work) are listed as 0. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.'
    : `Students with nothing turned in yet, and empty boxes, are listed as 0; each 0 is replaced when the student turns work in before ${form.window?.label || 'the two weeks close'}. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.`}));
  return panel;
}

// ---------- Chrome helper: enters an assignment's IC list in Infinite Campus ----------
// The browser companion extension in the teacher's own Chrome. This page hands
// it the list shown above (every roster student, with what to enter); in IC the
// optional manual panel keeps its trusted checkbox and Enter action before it
// types, saves, reloads and checks. Authorized entry does not need a repeated
// chat approval. It tells this page counts, never names. Once every section
// and column is done, the Hub notes "Entered in IC".
const HELPER = {send: 'hub-ic-entry', reply: 'hub-ic-entry-result'};
const helper = {state: 'unknown', version: '', results: {}, waiting: new Map(), sent: {}};
try { helper.sent = JSON.parse(store.get('helper-sent') || '{}'); } catch { helper.sent = {}; }
const saveSent = () => {
  const day = Date.now() - 86400000;
  for (const [id, sent] of Object.entries(helper.sent)) if (Date.parse(sent.sent_at) < day) delete helper.sent[id];
  store.set('helper-sent', JSON.stringify(helper.sent));
};
const fold = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const personKey = r => `${fold(r.last)}|${fold(r.first)}|${r.section}`;
const oneLine = (value, max = 200) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
function elsewhereKeys(form) {
  return new Set((form.elsewhere || []).filter(e => e.accepted_intake !== true).map(e => {
    const [last, ...first] = String(e.who).split(',');
    return personKey({last, first: first.join(','), section: e.section});
  }));
}
function acceptedIntakeKeys(form) {
  return new Set((form.elsewhere || []).filter(e => e.accepted_intake === true).map(e => {
    const [last, ...first] = String(e.who).split(',');
    return personKey({last, first: first.join(','), section: e.section});
  }));
}

// Every roster student of the class (all sections) with what to enter, the
// same rules as the table above.
function gradeReadinessHold(form) {
  const readiness = form.grade_readiness;
  const [fileId, held] = Object.entries(work.scores[form.form_id] || {}).find(([, score]) => score?.code === 'deck_not_ready') || [];
  if (readiness?.status === 'READY' && !held) return '';
  const context = `${CLASSES.find(([key]) => key === work.cls)?.[1] || work.cls} · ${work.week} · ${form.label || 'this assignment'}`;
  if (readiness?.status === 'READY' && held) {
    const copy = held.readiness || {label: held.label || 'Needs embedded codes'};
    const row = assessmentRows(form).find(row => row.files.some(file => file.id === fileId));
    const student = row ? `${row.last}, ${row.first}${row.section ? ` (${row.section})` : ''}` : 'student copy';
    const label = releaseLabel(copy);
    const problem = copy.problem || held.problem || '';
    const detail = [problem.startsWith(label + '. ') ? problem.slice(label.length + 2) : problem, held.action || copy.action].filter(Boolean).join(' ')
      || 'Recheck this student copy’s embedded codes before rerunning grades.';
    return `${context} · ${student}: ${label}. ${detail} Scoring and IC entry are held; no missing-work zero is prepared.`;
  }
  return `${context}: ${releaseLabel(readiness || held?.readiness)}. ${readiness?.action || held?.readiness?.action || readiness?.problem || held?.problem || 'Embedded codes are not verified. Recheck the lesson and student copies before rerunning grades.'} Scoring and IC entry are held; no missing-work zero is prepared.`;
}

function helperRows(form) {
  if (sourceMatchesHeld() || weekAssessmentHold()) return [];
  if (entryHistoryHold(form)) return [];
  if (recheckSelectionHold(form)) return [];
  if (form.grade_readiness?.status !== 'READY') return [];
  const scores = work.scores[form.form_id] || {};
  if (Object.values(scores).some(score => score?.code === 'deck_not_ready')) return [];
  const started = startedFor(form), elsewhere = elsewhereKeys(form), accepted = acceptedIntakeKeys(form);
  const rows = new Map();
  const add = (r, kind, notes = null, revision = null, reason = '') => {
    const key = personKey(r);
    if (rows.has(key)) { Object.assign(rows.get(key), {kind: 'check', notes: null, revision: null, reason: 'listed twice on the roster'}); return; }
    if (!oneLine(r.last) || !oneLine(r.section)) return;
    rows.set(key, {last: oneLine(r.last, 100), first: oneLine(r.first, 100), section: oneLine(r.section, 100), kind, notes, revision, reason});
  };
  const recheck = closedRecheck(form);
  const represented = new Set(form.combined_assessment
    ? assessmentRows(form).filter(row => row.combined_components?.length && !row.selection_problem).map(personKey) : []);
  for (const r of assessmentRows(form).filter(r => r.matched)) {
    const s = scores[scoreFile(r)?.id];
    if (r.selection_problem) add(r, 'check', null, null, 'current submission needs review');
    else if (!s) add(r, 'check', null, null, 'not scored yet');
    else if (s.problem) add(r, 'check', null, null, 'deck couldn’t be scored');
    else if (s.note) add(r, 'check', null, null, 'the Hub isn’t sure of this score');
    else if (s.notes_score == null && s.revision_score == null && s.scoring_basis === 'found_codes'
      && ['NOTES', 'BANK', 'DRAFT'].every(category => s.counts?.[category]?.required === 0))
      add(r, 'none', null, null, 'N/A: no countable coded boxes; no zero prepared');
    else if (s.notes_score == null && s.revision_score == null) add(r, 'check', null, null, 'no codes to count');
    else add(r, 'score', s.notes_score ?? null, s.revision_score ?? null, (s.component_coverage?.text || '').slice(0, 200));
  }
  for (const r of [...form.missing, ...(recheck ? [] : (form.late || []).filter(r => r.matched))]) {
    if (represented.has(personKey(r))) continue;
    if (form.combined_assessment) add(r, 'none', null, null, 'No eligible submitted component; no combined missing-work zero prepared');
    else if (!started) add(r, 'none', null, null, 'its two weeks haven’t started');
    else if (r.unsorted) add(r, 'check', null, null, 'submission awaiting a confirmed lesson match; scoring held');
    else if (elsewhere.has(personKey(r))) add(r, 'check', null, null, 'turned in another lesson’s deck through this Form');
    else if (accepted.has(personKey(r))) add(r, 'none', null, null, 'accepted intake for another assignment; not assessed here');
    else if (recheck) add(r, 'none', null, null, 'previously entered assignment; no missing-work zero in this recheck');
    else add(r, 'zero', 0, 0);
  }
  for (const r of form.excused || []) add(r, 'excused', null, null, 'new student');
  return [...rows.values()];
}

// The IC score panels that must be done: each section with something to enter, and each column it has.
function helperNeed(rows) {
  const need = [];
  for (const section of [...new Set(rows.map(r => r.section))].sort()) {
    for (const column of ['notes', 'revision']) {
      if (rows.some(r => r.section === section && ['score', 'zero'].includes(r.kind) && r[column] != null)) need.push([section, column]);
    }
  }
  return need;
}

// The assignment's own name: its Form's "📤 Name — Turn in" label, so two lessons
// in one week (two Forms on one Agenda row) never reach the helper under one name.
function formName(form, lesson) {
  const named = oneLine(String(form.label || '').replace(/^📤\s*/u, '').replace(/\s*[—–-]\s*turn\s*in\s*$/i, ''));
  return named && !/^(?:2nd\s+)?turn\s*(?:it\s*)?in$/i.test(named) ? named : oneLine(lesson?.topic || work.week);
}

const HELPER_ERRORS = {invalid_list: 'The helper couldn’t read this list. Reload the page and send it again.',
  receipt_unconfirmed: 'This assignment’s entry history could not be verified. Reload Student work before preparing an IC list.',
  coverage_incomplete: SOURCE_MATCH_HOLD,
  deck_not_ready: 'Needs embedded codes. Embed or repair the required codes in this lesson and the affected student copies, recheck, then rerun grading. No missing-work zero was sent.',
  assessment_scope_held: 'This week is not eligible for scoring or IC entry. Check its assessment scope note.',
  helper_not_configured: 'The helper is not connected to this Hub. Open it in School Chrome with the School Workflow Hub extension, check its Hub connection, then reload this page.'};
const helperError = code => HELPER_ERRORS[code]
  || 'The Chrome helper didn’t answer. Check that it’s turned on at chrome://extensions, then reload this page.';

async function sendToHelper(form, lesson) {
  const ui = work.ui[form.form_id] = {...work.ui[form.form_id], error: null, helper: null};
  const code = await sendList(form, lesson);
  if (code === 'list_ready') {
    ui.helper = `Sent to the Chrome helper at ${when(helper.sent[form.form_id].sent_at)}. In Infinite Campus, open this assignment’s score panel for each section, (F) for Notes and (S) for Revision, and click “Hub helper” at the bottom right.`;
  } else ui.error = helperError(code);
  renderWork();
}

// Hand one assignment's list to the helper; returns the helper's code.
async function sendList(form, lesson) {
  if (weekAssessmentHold()) return 'assessment_scope_held';
  if (sourceMatchesHeld()) return 'coverage_incomplete';
  if (gradeReadinessHold(form)) return 'deck_not_ready';
  if (entryHistoryHold(form)) return 'receipt_unconfirmed';
  if (recheckSelectionHold(form)) return 'coverage_incomplete';
  const rows = helperRows(form);
  const list = {schema: 'hub-ic-list/v1', list_id: crypto.randomUUID(), class_key: work.cls,
    class_name: CLASSES.find(([key]) => key === work.cls)[1], week: oneLine(work.week), form_id: form.form_id,
    assignment: formName(form, lesson), form_label: oneLine(form.label),
    status: !startedFor(form) ? 'before' : closedFor(form) ? 'closed' : 'open',
    status_text: oneLine(icStatus(form)?.text, 400), sent_at: new Date().toISOString(), rows};
  const request_id = crypto.randomUUID();
  const code = await new Promise(resolve => {
    helper.waiting.set(request_id, resolve);
    setTimeout(() => { if (helper.waiting.delete(request_id)) resolve('helper_unavailable'); }, 5000);
    window.postMessage({channel: HELPER.send, version: 1, request_id, type: 'send', list}, location.origin);
  });
  if (code === 'list_ready') {
    helper.sent[form.form_id] = {class_key: work.cls, week: work.week, label: form.label, need: helperNeed(rows), sent_at: list.sent_at};
    saveSent();
  }
  return code;
}

// ---------- Optional manual handoff for one class and week ----------
// One button sends every assignment of this class and week that has something
// to enter and copies instructions for the already-authorized helper path.
// Exact panels, source checks, keep-higher and save/readback stay unchanged.
function weekToEnter() {
  const data = work.data;
  if (!data || sourceMatchesHeld() || weekAssessmentHold()) return {ready: [], waiting: false};
  const all = data.lessons.flatMap(lesson => lesson.forms.map(form => ({form, lesson})))
    .filter(({form}) => form.form_id && form.window);
  const waiting = all.some(({form}) => assessmentRows(form).some(row => row.files.length && !row.selection_problem) && (!work.scores[form.form_id] || work.ui[form.form_id]?.progress));
  const ready = all.map(x => ({...x, need: helperNeed(helperRows(x.form))})).filter(x => x.need.length);
  return {ready, waiting, held: all.map(({form}) => gradeReadinessHold(form) || entryHistoryHold(form) || recheckSelectionHold(form)).filter(Boolean)};
}

function entryPrompt(items) {
  const cls = CLASSES.find(([key]) => key === work.cls)[1];
  const lines = items.map(({form, lesson, need}, i) => {
    const sections = [...new Set(need.map(([section]) => section))];
    return `${i + 1}. "${formName(form, lesson)}" — ${sections.map(section => `${section}: ${need.filter(([s]) => s === section).map(([, column]) => column === 'notes' ? '(F) = Notes' : '(S) = Revision').join(', ')}`).join('; ')}`;
  });
  return `Enter only these authorized Hub scores in Infinite Campus (IC) with the Chrome helper in School Chrome. No further approval is needed for these verified lists.

RULES
- Use the existing school sign-in if needed. Stop if the school session cannot be established.
- Never type scores yourself. The Chrome helper (the green "Hub helper" button in IC) enters them: it keeps the higher score, fills blanks and raises only, and leaves IC flags alone.
- Don't change anything else in IC: no flags, due dates or new assignments.
- The Hub has already sent these lists to the helper (${when(new Date().toISOString())}).

ASSIGNMENTS — ${cls}, ${work.week}
${lines.join('\n')}

STEPS
1. Open each exact assignment, section and column listed above, then click "Hub helper". Check its assignment, column, roster match and proposed counts. If the assignment is missing, the source changed or the destination does not match, stop that assignment and report the exact issue; do not create or guess a destination.
2. For each verified list, tick the helper's existing checkbox and click "Enter … and Save". Wait for "Saved and checked after reloading". When nothing needs entering, no Save is needed. If a save stops or cannot be confirmed, report the exact message and do not retry it.
3. Report each assignment, section and column: entered, raised, kept higher and left with its reason. Preserve Exempt flags and comments. List new students for the teacher to mark Exempt; do not change flags yourself.`;
}

async function sendWeek(status, promptBox) {
  if (weekAssessmentHold()) { status.textContent = weekAssessmentHold(); return; }
  if (sourceMatchesHeld()) { status.textContent = SOURCE_MATCH_HOLD; return; }
  const {ready, held = []} = weekToEnter();
  if (!ready.length && held.length) { status.textContent = held.join(' '); return; }
  status.textContent = ' Sending to the Chrome helper…';
  const failed = [];
  for (const {form, lesson} of ready) {
    const code = await sendList(form, lesson);
    if (code !== 'list_ready') failed.push(`${formName(form, lesson)}: ${helperError(code)}`);
  }
  if (failed.length) { status.textContent = ' ' + failed.join(' '); return; }
  const text = entryPrompt(ready);
  promptBox.replaceChildren(el('summary', {text: 'Show manual instructions'}), el('pre', {class: 'prompt-text', text}));
  promptBox.hidden = false;
  try { await navigator.clipboard.writeText(text); status.textContent = ` Prepared ${ready.length} assignment lists in the helper. Manual instructions copied.`; }
  catch { status.textContent = ` Prepared ${ready.length} assignment lists in the helper. Open “Show manual instructions” to copy them.`; }
}

function weekEntryBox() {
  if (helper.state !== 'ready') return null;
  const {ready, waiting, held = []} = weekToEnter();
  if (!ready.length && held.length) return note(held.join(' '), 'info');
  if (!ready.length && !waiting) return null;
  const status = el('span', {class: 'muted'});
  const promptBox = el('details', {hidden: true});
  return el('details', {class: 'roster-check manual'},
    el('summary', {text: 'Manual fallback: prepare this week’s IC lists'}),
    el('p', {}, waiting ? 'Wait until every assignment below has finished scoring.'
      : `For all mapped assignments, use the extension’s “Run all mapped assignments now”. This optional fallback prepares ${ready.map(({form, lesson}) => `“${formName(form, lesson)}”`).join(' and ')} in the helper and copies instructions for entering only these verified lists.`),
    el('div', {class: 'action-row'},
      el('button', {type: 'button', class: 'primary', disabled: waiting || !ready.length, onclick: () => sendWeek(status, promptBox)},
        'Prepare this week’s helper lists'),
      status),
    promptBox);
}

const COLUMN = {notes: 'Notes', revision: 'Revision'};
const goodResult = (r, since) => r && ['entered', 'checked'].includes(r.outcome) && r.certain && !r.counts.not_saved && r.at >= since;
function resultText(r) {
  const n = r.counts;
  if (r.outcome === 'checked') return `✓ nothing needed (${when(r.at)})`;
  if (r.outcome === 'entered') return `✓ ${n.filled} entered, ${n.raised} raised${n.left ? `, ${n.left} left for you` : ''} (${when(r.at)})`;
  if (r.outcome === 'partial') return `⚠ saved, but ${n.not_saved} didn’t read back: check in IC (${when(r.at)})`;
  return `⚠ not confirmed: check this assignment in IC (${when(r.at)})`;
}
function helperProgress(form) {
  const sent = helper.sent[form.form_id];
  const done = helper.results[form.form_id] || [];
  if (!sent && !done.length) return null;
  const pairs = sent ? sent.need : done.map(r => [r.section, r.column]);
  return el('ul', {class: 'files'}, pairs.map(([section, column]) => {
    const r = done.find(d => d.section === section && d.column === column);
    const current = r && (!sent || r.at >= sent.sent_at);
    return el('li', {}, el('strong', {text: `Chrome helper · ${section} ${COLUMN[column]}: `}),
      current ? `${resultText(r)} in IC’s “${r.ic_title}”` : 'not entered yet');
  }));
}
function helperNote(sent, done) {
  return oneLine('Chrome helper — ' + sent.need.map(([section, column]) => {
    const n = done.find(d => d.section === section && d.column === column).counts;
    return `${section} ${COLUMN[column]}: ${n.filled} entered, ${n.raised} raised, ${n.left} left`;
  }).join('; '), 480);
}
function noteResult(result) {
  const list = helper.results[result.form_id] = (helper.results[result.form_id] || [])
    .filter(r => r.section !== result.section || r.column !== result.column || r.at > result.at);
  if (!list.some(r => r.section === result.section && r.column === result.column)) list.push(result);
}
// Every section and column done in this round, each read back: the Hub notes it once.
async function maybeRecord(formId) {
  const sent = helper.sent[formId];
  const done = helper.results[formId] || [];
  if (!sent || sent.recorded || !sent.need.length
      || !sent.need.every(([section, column]) => goodResult(done.find(d => d.section === section && d.column === column), sent.sent_at))) return;
  sent.recorded = true; saveSent();
  const ui = work.ui[formId] = {...work.ui[formId]};
  try {
    const out = await postJSON('api/record', {class: sent.class_key, week: sent.week, form: formId, kind: 'ic', note: helperNote(sent, done)});
    work.marks[formId] = out.sheet;
    ui.saved = `The Chrome helper entered every section: added “Entered in IC” to “${out.sheet?.name || 'Scores entered'}”.`;
  } catch (error) {
    sent.recorded = false; saveSent();
    ui.error = `The helper finished, but the Hub couldn’t note it (${error.message}). Click “I entered these in IC”.`;
  }
  if (work.data) renderWork();
}
function helperLine() {
  return el('p', {id: 'helper-status', class: helper.state === 'ready' ? 'ok' : 'note warn'},
    helper.state === 'ready'
      ? `✓ Score helper ready${helper.version ? ' · ' + helper.version : ''}.`
      : 'Score helper isn’t connected in this browser. The morning review can’t run until it is ready.');
}
window.addEventListener('message', event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== HELPER.reply) return;
  const data = event.data;
  if (data.type === 'sent') { helper.waiting.get(data.request_id)?.(data.code); helper.waiting.delete(data.request_id); return; }
  if (data.type === 'status') {
    helper.state = data.code === 'helper_ready' ? 'ready' : data.code;
    helper.version = data.helper_version || '';
    helper.morning = data.morning || null;
    renderMorningAlert();
    (data.results || []).forEach(noteResult);
    Object.keys(helper.sent).forEach(maybeRecord);  // finished while this page was closed
  } else if (data.type === 'entered' && data.result) {
    noteResult(data.result);
    maybeRecord(data.result.form_id);
  } else return;
  $('helper-status')?.replaceWith(helperLine());
  if (work.data) renderWork();
});
window.postMessage({channel: HELPER.send, version: 1, request_id: crypto.randomUUID(), type: 'status'}, location.origin);

async function feedbackAction(form, student, action) {
  const ui = work.ui[form.form_id];
  const scope = {class: work.cls, week: work.week, form: form.form_id, file: student.file_id};
  const seq = work.seq;
  ui.busy = action === 'draft' ? `Generating feedback for ${student.name}…` : 'Saving the private draft…';
  ui.error = null; renderWork();
  try {
    const body = action === 'draft' ? scope : {...scope, id: student.draft.id};
    const result = await postJSON(`api/feedback/${action}`, body);
    if (seq !== work.seq || ui !== work.ui[form.form_id]) return;
    student.draft = result;
  } catch (error) {
    if (seq === work.seq && ui === work.ui[form.form_id]) ui.error = error.message;
  } finally {
    if (seq === work.seq && ui === work.ui[form.form_id]) { ui.busy = null; renderWork(); }
  }
}

function teacherWritingPreview(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  return el('details', {}, el('summary', {text: 'Science writing observations · teacher only, excluded from the student PDF'}),
    ...rows.map(row => el('section', {}, el('p', {}, el('strong', {text: `${row.dimension}: ${row.level}`})),
      el('p', {class: 'muted', text: `${row.question_id} · ${row.stage}`}),
      row.quote ? el('blockquote', {text: row.quote}) : null, el('p', {text: row.explanation}))));
}

function feedbackPanel(form) {
  const ui = work.ui[form.form_id] || {}, data = ui.feedback;
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Student feedback'}));
  if (ui.busy) { panel.append(el('p', {role: 'status', text: ui.busy})); return panel; }
  if (ui.error) panel.append(note(ui.error));
  if (!data) return panel;
  if (data.problem) panel.append(note(data.problem));
  panel.append(el('p', {class: 'muted', text: 'Generate a one-page draft, review it, then save and open its PDF. Drafts stay private in the Hub. Grades and delivery checkboxes do not change. Reopening or printing saved feedback makes no new AI call.'}));
  if (!data.students.length) panel.append(note('No current, roster-matched submissions are available for this lesson.', 'info'));
  for (const student of data.students) {
    const d = student.draft;
    const card = el('section', {class: 'surface'}, el('h4', {text: `${student.name} · ${student.section}`}));
    if (student.hold) card.append(note(student.hold));
    if (d) {
      card.append(el('p', {text: d.report.student_summary}), el('p', {}, el('strong', {text: 'One question: '}), d.report.question),
        el('ul', {}, d.report.aspects.map(a => el('li', {text: `${a.name}: ${a.current_level}`}))),
        el('p', {class: 'muted', text: `Draft based on work modified ${when(d.source_modified)}. ${d.saved_at ? 'Saved privately in the Hub.' : 'Ready to review and save.'}`}));
      if (d.report.teacher_notes.length) card.append(el('details', {}, el('summary', {text: 'Teacher notes — excluded from the student PDF'}),
        el('ul', {}, d.report.teacher_notes.map(n => el('li', {text: n})))));
      const writing = teacherWritingPreview(d.report.teacher_dimensions);
      if (writing) card.append(writing);
    }
    const actions = el('div', {class: 'action-row'});
    if (!student.hold) actions.append(el('button', {type: 'button', onclick: () => feedbackAction(form, student, 'draft')},
      d ? 'Check current work / reuse draft' : 'Generate feedback'));
    if (d && !d.saved_at && !student.hold) actions.append(el('button', {type: 'button', class: 'primary', onclick: () => feedbackAction(form, student, 'save')}, 'Save private draft'));
    if (d?.pdf_url) actions.append(link('Open saved PDF / print', d.pdf_url, 'primary'));
    card.append(actions); panel.append(card);
  }
  return panel;
}

function studentRow(row, scores, formId) {
  const who = row.matched
    ? el('td', {}, el('strong', {text: `${row.last}, ${row.first}`}),
      row.matched_by && row.matched_by !== 'email' ? el('small', {class: 'muted', text: ` matched by ${row.matched_by}`}) : null)
    : el('td', {}, el('span', {class: 'badge warn', text: row.roster_note || 'Not on roster'}), ' ', row.email || 'No email collected',
      row.typed.length ? el('small', {class: 'muted', text: ' · typed: ' + row.typed.join(' / ')}) : null);
  const earlier = row.earlier.length
    ? el('details', {class: 'earlier'}, el('summary', {text: `${row.earlier.length} earlier`}),
      row.earlier.map(e => el('div', {}, el('small', {class: 'muted', text: when(e.submitted_at)}), fileList(e.files))))
    : null;
  return el('tr', {class: row.matched ? '' : 'unmatched'}, who, el('td', {text: row.section || '—'}),
    el('td', {text: when(row.submitted_at)}),
    el('td', {}, fileList(row.files), earlier), ...scoreCells(row, scores, formId));
}

function renderForm(form, lesson) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const box = el('section', {class: 'form-work'},
    el('div', {class: 'form-head'}, el('h3', {text: form.label}), link('Open Form ↗', form.url, 'chip')));
  if (form.problem) box.append(note(form.problem));
  if (!form.form_id || (form.problem && !form.students.length)) return box;
  const recheck = closedRecheck(form);
  const selected = form.combined_assessment || (recheck && !entryHistoryHold(form)) ? assessmentRows(form) : form.students;
  const matched = selected.filter(r => r.matched && !r.late && inSection(r));
  const unmatched = form.students.filter(r => !r.matched);
  const accepted = acceptedIntakeKeys(form), elsewhere = elsewhereKeys(form);
  const missing = form.missing.filter(r => inSection(r) && (!accepted.has(personKey(r)) || r.unsorted || elsewhere.has(personKey(r))));
  const late = (form.combined_assessment || (recheck && !entryHistoryHold(form))
    ? [...selected.filter(r => r.late), ...(form.late || []).filter(r => !r.matched)] : form.late || [])
    .filter(r => !r.matched || inSection(r));
  const lateMatched = late.filter(r => r.matched).length;
  box.append(el('p', {class: 'summary'},
    el('strong', {text: recheck ? `${matched.length + lateMatched} matched submission${matched.length + lateMatched === 1 ? '' : 's'}`
      : `${matched.length} of ${matched.length + lateMatched + missing.length}`}),
    recheck ? ' retained for the current recheck; original response history is preserved' : form.window ? ' turned in on time' : ' turned in',
    late.length ? el('span', {class: 'badge warn', text: `${late.length} late${recheck ? ' — retained for recheck' : ' — not graded'}`}) : null,
    unmatched.length ? el('span', {class: 'badge warn', text: `${unmatched.length} not matched to the roster`}) : null,
    form.folder_files ? el('span', {class: 'badge', text: `includes ${form.folder_files} file(s) found in the turn-in folder`}) : null,
    form.shared_files ? el('span', {class: 'badge', text: `includes ${form.shared_files} from the earlier shared Form`}) : null));
  if (form.window) {
    box.append(el('p', {class: 'muted', text: `Open ${form.window.opened} until ${form.window.label}: two weeks from the first turn-in, never starting before the lesson’s week. ${recheck ? 'Later submissions retain their Late label; their current files can be reconsidered once source checks are clear.' : 'Anything later is Late and isn’t graded.'}`}));
  }
  if (form.elsewhere?.length) {
    for (const accepted of [true, false]) {
      const entries = form.elsewhere.filter(e => (e.accepted_intake === true) === accepted);
      if (!entries.length) continue;
      box.append(el('div', {class: 'note info'},
      el('strong', {text: accepted ? `Accepted intake (${entries.length}): ` : `Not counted here (${entries.length}): another lesson’s deck. `}),
      entries.map((e, i) => [i ? '; ' : '', `${e.who}${e.section ? ` (${e.section})` : ''} — `,
        e.url ? link(e.file || 'file', e.url) : e.file,
        !accepted && e.source === 'folder' ? ` is in this week’s folder but belongs to ${e.to}: move it to that week’s 📥 folder`
          : ` counts for ${e.to}`]).flat(),
      '.'));
    }
  }
  const marks = marksLine(form);
  if (marks) box.append(marks);
  const standing = icStatus(form);
  if (standing) box.append(el('p', {class: 'ic-status ' + standing.kind, text: standing.text}));
  if (work.ui[form.form_id]?.saved) box.append(note(work.ui[form.form_id].saved, 'info'));
  const scores = weekAssessmentHold() || recheckSelectionHold(form) ? null : work.scores[form.form_id];
  const ui = work.ui[form.form_id] || {};
  const scoredAll = scores && !ui.progress && !sourceMatchesHeld();
  box.append(el('div', {class: 'action-row'}, el('button', {type: 'button', onclick: () => openPanel(form, 'feedback')},
    ui.open === 'feedback' ? 'Hide feedback' : 'Student feedback')));
  if (ui.open === 'feedback') box.append(feedbackPanel(form));
  box.append(...[
    ui.progress ? el('p', {class: 'muted', text: ui.progress}) : null,
    scoredAll ? el('details', {class: 'manual', open: Boolean(ui.open)},
      el('summary', {text: 'If manual is needed'}),
      el('div', {class: 'action-row'},
        el('button', {type: 'button', class: 'primary', onclick: () => openPanel(form, 'ic')},
          ui.open === 'ic' ? 'Hide IC scores' : 'Scores are ready to enter in IC'),
        SCAN_CLASSES.includes(work.cls) ? el('button', {type: 'button', onclick: () => openPanel(form, 'answers')},
          ui.open === 'answers' ? 'Hide answers' : 'Answers by question') : null)) : null,
  ].filter(Boolean));
  if (scores) {
    box.append(el('p', {class: 'muted', text: 'Scores are out of 10: the share of this deck’s own coded boxes each student completed, so a deck can have any number of boxes. Notes counts the notes boxes and Concept Bank terms; Revision counts the revised answers.'}));
  }
  const selectionHolds = assessmentRows(form).filter(row => row.selection_problem && inSection(row));
  if (selectionHolds.length) box.append(note(`Current submissions need review: ${selectionHolds.map(row => `${row.last}, ${row.first}: ${row.selection_problem}`).join('; ')}. These students remain unscored until their source checks are resolved.`, 'info'));
  const sourceReview = studentWorkReviewPanel(form, selectionHolds.length > 0 || Boolean(scores && Object.values(scores).some(score => score?.problem || score?.note)));
  if (sourceReview) box.append(sourceReview);
  if (scores && !ui.progress) {
    const failedRows = assessmentRows(form).filter(row => row.files.length && scores[scoreFile(row)?.id]?.problem)
      .map(row => `${row.last}, ${row.first}${work.section === 'all' ? ` (${row.section})` : ''}: ${scores[scoreFile(row)?.id].problem}`);
    if (failedRows.length) box.append(note(`Couldn’t score ${failedRows.length} deck${failedRows.length === 1 ? '' : 's'}: ${failedRows.join('; ')}`, 'info'));
    const report = uncodedReport(scores, form);
    if (report) box.append(report);
    const listNote = Object.values(scores).map(r => r.inventory_note).find(Boolean);
    if (listNote) box.append(note(`Deck code list: ${listNote}`, 'info'));
    const coding = Object.values(scores).map(r => r.coding).find(c => c && (c.missing.length || c.extra.length));
    if (coding) {
      box.append(note(['Deck codes to fix before next time:',
        coding.missing.length ? ` on the code list but no box has it — ${coding.missing.join(', ')}.` : '',
        coding.extra.length ? ` A box has a code that isn’t on the list — ${coding.extra.join(', ')}.` : ''].join(''), 'info'));
    }
    if (ui.open === 'ic') box.append(icPanel(form, scores, lesson));
    if (ui.open === 'answers') box.append(answersPanel(form, scores));
  }
  const listed = [...matched, ...unmatched];
  if (listed.length) {
    box.append(el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work', ...(scores ? ['Notes /10', 'Revision /10'] : [])].map(h => el('th', {text: h})))),
      el('tbody', {}, listed.map(r => studentRow(r, scores, form.form_id))))));
  } else {
    box.append(note(recheck && late.length ? 'Current selected submissions are listed below; their original response history is retained.'
      : late.length ? 'No on-time responses.' : 'No responses yet.', 'info'));
  }
  if (late.length) {
    box.append(el('div', {class: 'late'}, el('h4', {text: `${recheck ? 'Late submissions — retained for recheck' : 'Late — not graded'} (${late.length})`}),
      el('p', {class: 'muted', text: `Turned in after ${form.window?.label}. ${recheck ? 'Matched submitted files can be reconsidered once source checks are clear; the original submission date and Late label are preserved.' : 'Tidy moves these into the week’s “⏰ Late — not graded” folder.'}`}),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work', ...(recheck && scores ? ['Notes /10', 'Revision /10'] : [])].map(h => el('th', {text: h})))),
        el('tbody', {}, late.map(r => {
          const row = studentRow(r, recheck && r.matched ? scores : null, form.form_id);
          if (recheck && scores && !r.matched) row.append(el('td', {colspan: 2, text: 'Not matched to the roster'}));
          return row;
        }))))));
  }
  const excused = (form.excused || []).filter(inSection);
  if (excused.length) {
    box.append(el('p', {class: 'muted'}, el('strong', {text: `Excused — new students (${excused.length}): `}),
      excused.map(m => `${m.last}, ${m.first} (added ${m.added})`).join('; '),
      '. They joined after this assignment began, so it isn’t counted for them.'));
  }
  if (missing.length && !weekAssessmentHold()) {
    const held = sourceMatchesHeld() || missing.some(m => m.unsorted);
    box.append(el('div', {class: 'missing'}, el('h4', {text: held ? `No confirmed submission for this assignment (${missing.length}) — scoring held`
      : gradeReadinessHold(form) ? `Not turned in yet (${missing.length})`
      : recheck || entryHistoryHold(form) ? `No verified submitted file (${missing.length}) — no new zero prepared`
      : closedFor(form) ? `Nothing turned in on time (${missing.length}) — these are 0 now`
      : startedFor(form) ? `Not turned in yet (${missing.length}) — 0 until they turn it in` : `Not turned in yet (${missing.length})`}),
      el('ul', {}, missing.map(m => el('li', {}, `${m.last}, ${m.first}${section === 'all' ? ' · ' + m.section : ''}`,
        m.unsorted ? el('span', {class: 'badge warn', text: 'Submission awaiting a lesson match — scoring held'}) : null)))));
  }
  return box;
}

function renderWork() {
  const data = work.data;
  if (!data) return;
  const parts = data.problems.map(p => note(p));
  if (weekAssessmentHold(data)) parts.push(note(weekAssessmentHold(data), 'info'));
  if (!data.roster_size) parts.push(note('No roster students were found for this class, so nobody can be listed as missing.'));
  if (data.folders?.length) {
    parts.push(el('p', {class: 'muted'}, `Read the Form uploads and ${data.folder_files} file(s) in `,
      data.folders.flatMap((f, i) => [i ? ', ' : '', link(f.name + ' ↗', f.url)]), '.'));
  }
  if (data.folder_note) parts.push(note(data.folder_note, 'info'));
  if (data.roster_check) parts.push(renderRosterCheck(data.roster_check));
  if (data.folder_reviews?.length) {
    parts.push(el('section', {class: 'folder-review'},
      el('h3', {text: 'Check this shared lesson folder'}),
      ...data.folder_reviews.map(review => el('p', {class: 'note info'},
        review.message, ' ', link(review.folder_name + ' ↗', review.folder_url)))));
  }
  for (const lesson of data.lessons) {
    parts.push(el('div', {class: 'lesson-head'}, el('h2', {text: lesson.topic || data.week})));
    if (lesson.problem && !omittedFormProblem(lesson, data)) parts.push(note(lesson.problem));
    lesson.forms.forEach(form => parts.push(renderForm(form, lesson)));
  }
  const other = (data.other_files || []).filter(r => !r.matched || work.section === 'all' || r.section === work.section);
  if (other.length) {
    parts.push(el('section', {class: 'form-work'},
      el('div', {class: 'form-head'}, el('h3', {text: 'Confirm these submission matches'})),
      note(`These ${other.length === 1 ? 'file is' : 'files are'} already in the ${data.week} turn-in folders. The Hub couldn’t safely match ${other.length === 1 ? 'it' : 'them'} to one assignment, so scoring is held until the match is clear. Open the linked work and confirm the assignment; nothing was moved.`, 'info'),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work'].map(h => el('th', {text: h})))),
        el('tbody', {}, other.map(r => studentRow(r)))))));
  }
  const entry = weekEntryBox();
  if (entry) parts.push(entry);
  $('work-body').replaceChildren(...parts);
}

function renderRosterCheck(check) {
  const sections = Object.entries(check.sections).map(([name, n]) => `${name} ${n}`).join(' · ');
  const updated = check.updated_at ? `Rosters sheet last changed ${when(check.updated_at)}.` : '';
  const flags = check.flags;
  const box = el('section', {class: 'roster-check'},
    el('h3', {text: 'Roster check'}),
    el('p', {class: 'muted', text: [`Students: ${sections}.`, updated].filter(Boolean).join(' ')}));
  if (check.stale) box.append(note(`The Rosters sheet hasn’t changed in over ${check.stale_days} days. Check that your roster job is still running.`));
  if (!flags.length) {
    box.append(el('p', {class: 'ok', text: '✓ Nothing to check: everyone who turned in work in the last week is on this class’s roster.'}));
    return box;
  }
  const say = {
    other_class: f => [el('strong', {text: f.who}), ` is on the ${f.section} roster but turned in to this class`, f.assignment ? ` (${f.assignment})` : '', '. Wrong class?'],
    no_roster: f => [el('strong', {text: f.who}), ' isn’t on any class roster', f.assignment ? ` (${f.assignment})` : '', '. New, left, or a visitor?'],
    twice: f => [el('strong', {text: f.who}), ` is listed twice (${f.section}).`],
  };
  box.append(el('ul', {class: 'flags'}, flags.map(f => el('li', {}, ...say[f.kind](f),
    f.url ? [' ', link('Open ↗', f.url)] : []))));
  return box;
}

// ---------- Tidy: one 📥 folder per course per week ----------
function renderTidy(box, p) {
  const parts = [el('h3', {text: `Tidy ${p.week} folders`})];
  const renames = p.renames || [];
  const moving = p.moves.length || p.create || p.empty_after.length;
  if (!moving && !renames.length) {
    parts.push(note(p.keep ? `Already tidy: everything for ${p.week} is in ${p.keep.name}, and every student file starts with the student’s name.` : 'Nothing to tidy.', 'info'));
    if (p.problem && p.problem.indexOf("can't move") < 0) parts.push(note(p.problem));
    box.replaceChildren(...parts);
    return;
  }
  if (moving) {
    parts.push(el('p', {}, p.keep ? ['Keep ', link(p.keep.name + ' ↗', p.keep.url), ' as this week’s one folder.']
      : `Create “${p.create}” as this week’s one folder.`));
  }
  const item = m => el('li', {}, (m.folder ? '📁 ' : '') + m.name,
    m.from_name ? el('small', {class: 'muted', text: ' — from ' + m.from_name}) : null);
  const toWeek = p.moves.filter(m => m.to !== 'late'), toLate = p.moves.filter(m => m.to === 'late');
  if (toWeek.length) {
    parts.push(el('p', {class: 'label', text: `Move into it (${toWeek.length})`}), el('ul', {class: 'files'}, toWeek.map(item)));
  }
  if (toLate.length) {
    parts.push(el('p', {class: 'label', text: `Move into “${p.create_late || p.late_folder?.name}” inside it (${toLate.length}) — turned in after their assignment’s two weeks`}),
      el('ul', {class: 'files'}, toLate.map(item)));
  }
  if (p.empty_after.length) {
    parts.push(el('p', {class: 'muted', text: (p.can_trash
      ? 'Then these emptied extra folders go to the Drive trash (you can restore them): '
      : 'Then these emptied extra folders get “_” in front of their names so you know to skip them: ')
      + p.empty_after.map(e => e.name).join(', ')}));
  }
  if (renames.length) {
    // Name and lesson first, so a Drive search by a student's name finds their work.
    parts.push(el('p', {class: 'label', text: `Put the student’s name and lesson in front of ${renames.length} file name(s)`}),
      el('p', {class: 'muted', text: `For example: ${renames[0].to}`}),
      el('details', {}, el('summary', {text: 'See every new name'}),
        el('ul', {class: 'files'}, renames.map(r => el('li', {text: r.to})))));
  }
  if (p.problem) parts.push(note(p.problem));
  const label = moving && renames.length ? 'Move and rename' : moving ? 'Move them' : 'Rename them';
  if ((moving && p.can_move) || (!moving && p.can_rename)) {
    parts.push(el('div', {class: 'action-row'},
      el('button', {type: 'button', class: 'primary', onclick: () => applyTidy(box, p)}, label),
      el('button', {type: 'button', onclick: () => box.replaceChildren()}, 'Cancel')));
  }
  box.replaceChildren(...parts);
}

async function previewTidy() {
  const box = $('tidy-box');
  box.replaceChildren(el('p', {class: 'muted', text: 'Checking this week’s folders… nothing moves until you confirm.'}));
  try {
    renderTidy(box, await getJSON(`api/tidy?class=${work.cls}&week=${encodeURIComponent(work.week)}`));
  } catch (error) { box.replaceChildren(note(error.message)); }
}

async function applyTidy(box, p) {
  box.replaceChildren(el('p', {class: 'muted', text: 'Moving…'}));
  try {
    const response = await fetch('api/tidy', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({class: work.cls, week: work.week, plan_id: p.plan_id})});
    let body = null;
    try { body = await response.json(); } catch { /* not JSON */ }
    if (!response.ok) throw new Error(body?.error || `The server returned an error (${response.status}).`);
    const parts = [el('h3', {text: `Tidy ${p.week} folders`}),
      note(body.moves.length ? `Moved ${body.moved} of ${body.moves.length} item(s).` : 'Nothing needed moving.', 'info')];
    if (body.moved_items?.length) parts.push(el('ul', {class: 'files tidy-receipt'}, body.moved_items.map(item =>
      el('li', {text: `Moved “${item.name}” from ${item.from_name || 'a source folder whose name Drive did not return'} to ${item.to_name || `📥 ${p.week}`}.`}))));
    if (body.renames?.length) parts.push(note(`Renamed ${body.renamed} of ${body.renames.length} student file(s) to start with the student’s name and lesson.`, 'info'));
    if (body.failed.length) parts.push(note(`${body.failed.length} couldn’t be moved: ` + body.failed.map(f => `${f.name} (${f.why})`).join('; ')));
    if (body.rename_failed?.length) parts.push(note(`${body.rename_failed.length} couldn’t be renamed: ` + body.rename_failed.map(f => `${f.name} (${f.why})`).join('; ')));
    if (body.trashed.length) parts.push(el('p', {class: 'muted', text: `Sent ${body.trashed.length} empty extra folder(s) to the Drive trash; you can restore them from there.`}));
    if (body.marked.length) parts.push(el('p', {class: 'muted', text: `Put “_” in front of ${body.marked.length} empty extra folder(s): ` + body.marked.map(e => e.name).join(', ')}));
    if (body.left_empty.length) {
      parts.push(el('p', {class: 'muted', text: 'These extra folders couldn’t be trashed or renamed. You can delete them:'}),
        el('ul', {}, body.left_empty.map(e => el('li', {}, link(e.name + ' ↗', e.url)))));
    }
    box.replaceChildren(...parts);
    await loadWork(true);
  } catch (error) { box.replaceChildren(note(error.message)); }
}

// ---------- Page wiring ----------
// ---------- Missing-work sheets (a contract for students and families) ----------
// One printable page per student with missing work in this class (and section):
// every assignment whose two weeks have begun that they haven't turned in on
// time, whether it can still replace its 0, how they usually turn work in, and a
// short plan with signature lines. Built only from the dashboard's own records.
const sheets = {seq: 0};
const DAY = 86400000;
const shortDate = d => d.toLocaleDateString('en-US', {timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric'});

function studentHistory(weekResults, section) {
  const people = new Map();
  const seen = new Set();
  const person = r => {
    const key = `${r.section}|${r.last}|${r.first}`;
    if (!people.has(key)) people.set(key, {last: r.last, first: r.first, section: r.section, missing: [], onTime: [], total: 0});
    return people.get(key);
  };
  for (const {week, data} of weekResults) {
    if (weekAssessmentHold(data)) continue;
    for (const lesson of data.lessons) {
      for (const form of lesson.forms) {
        if (!startedFor(form) || seen.has(form.form_id)) continue;  // not begun, or already counted from another week
        seen.add(form.form_id);
        const close = new Date(form.window.at);
        const opened = new Date(close.getTime() - 15 * DAY + 60000);  // the start of the first turn-in day
        const item = {week, title: lesson.topic || form.label, close, open: close > new Date()};
        const inSection = r => section === 'all' || r.section === section;
        for (const r of form.students.filter(r => r.matched && inSection(r))) {
          const p = person(r); p.total += 1;
          p.onTime.push(Math.max(0, Math.floor((new Date(r.submitted_at) - opened) / DAY)));
        }
        const accepted = acceptedIntakeKeys(form), elsewhere = elsewhereKeys(form);
        for (const r of [...form.missing, ...form.late.filter(r => r.matched)]
          .filter(r => inSection(r) && (!accepted.has(personKey(r)) || r.unsorted || elsewhere.has(personKey(r))))) {
          const p = person(r); p.total += 1;
          p.missing.push({...item, late: Boolean(r.submitted_at)});
        }
      }
    }
  }
  return [...people.values()].filter(p => p.missing.length)
    .sort((a, b) => (a.section + a.last + a.first).localeCompare(b.section + b.last + b.first));
}

function habitLine(p) {
  const n = p.onTime.length;
  if (!n) return p.total === 1 ? 'You haven’t turned in this assignment yet.'
    : `So far this year you haven’t turned in any of your ${p.total} assignments on time.`;
  const days = [...p.onTime].sort((a, b) => a - b)[Math.floor((n - 1) / 2)];
  const when = days === 0 ? 'on the day the two weeks start' : `about ${days} day${days === 1 ? '' : 's'} after the two weeks start`;
  return `So far you’ve turned in ${n} of ${p.total} assignments on time. When you turn work in, it’s usually ${when}.`;
}

function sheetFor(p, cls) {
  const open = p.missing.filter(m => m.open).sort((a, b) => a.close - b.close);
  const closed = p.missing.filter(m => !m.open).sort((a, b) => a.close - b.close);
  return el('article', {class: 'sheet-page'},
    el('p', {class: 'sheet-top'}, `${cls} · ${p.section}`, el('span', {text: shortDate(new Date())})),
    el('h2', {text: `Missing work — ${p.first.replace(/\s*\(.*\)\s*/, ' ').trim()} ${p.last}`}),
    el('p', {}, `Missing work may have a 0 in IC. A later verified submission or revision can earn a higher score after review, including after the original two-week window closes.`),
    open.length ? el('h3', {text: `Still open — turn these in (${open.length})`}) : null,
    open.length ? el('table', {class: 'sheet-table'},
      el('thead', {}, el('tr', {}, ['Assignment', 'Closes', 'I will turn it in by'].map(h => el('th', {text: h})))),
      el('tbody', {}, open.map(m => el('tr', {}, el('td', {text: `${m.week} · ${m.title}`}),
        el('td', {text: `${shortDate(m.close)}, 11:59 PM`}), el('td', {class: 'blank-line'}))))) : null,
    closed.length ? el('h3', {text: `Past the original window — check current work (${closed.length})`}) : null,
    closed.length ? el('ul', {}, closed.map(m => el('li', {text: `${m.week} · ${m.title} (closed ${shortDate(m.close)})${m.late ? ' — turned in after it closed' : ''}`}))) : null,
    el('h3', {text: 'How you turn work in'}),
    el('p', {text: habitLine(p)}),
    el('h3', {text: 'My plan'}),
    el('p', {class: 'write-line', text: 'One step I will take:'}),
    el('p', {class: 'write-line', text: 'When I will do my homework:'}),
    el('p', {class: 'write-line', text: 'Help I need, or something I want my teacher to know:'}),
    el('p', {class: 'sheet-rule'}, 'To turn work in: finish it in your own copy, then use the lesson’s Turn-in Form. Changed it after turning it in? Turn it in again with the same Form. Your receipt email is your record.'),
    el('div', {class: 'signatures'},
      el('p', {text: 'Student signature ______________________  Date ________'}),
      el('p', {text: 'Parent or guardian signature ______________________  Date ________'})));
}

function closeSheets() {
  sheets.seq += 1;
  $('sheets-box').hidden = true;
  $('sheets-box').replaceChildren();
  $('work-body').hidden = false;
}

async function missingWorkSheets() {
  const seq = ++sheets.seq;
  const cls = CLASSES.find(([k]) => k === work.cls)[1];
  const section = work.section;
  const box = $('sheets-box');
  box.hidden = false; $('work-body').hidden = true; $('tidy-box').replaceChildren();
  const head = el('div', {class: 'sheets-head'}, el('p', {class: 'muted', text: 'Reading every week of this class…'}),
    el('div', {class: 'action-row'}, el('button', {type: 'button', onclick: closeSheets}, 'Back to student work')));
  box.replaceChildren(head);
  try {
    const weeks = (await getJSON(`api/weeks?class=${work.cls}&today=${todayISO()}`)).weeks
      .filter(w => w.forms && w.monday && w.monday <= todayISO());
    const results = [];
    for (const [i, w] of weeks.entries()) {
      if (seq !== sheets.seq) return;
      head.firstChild.textContent = `Reading ${w.label} (${i + 1} of ${weeks.length})…`;
      results.push({week: w.label, data: await getJSON(`api/work?class=${work.cls}&week=${encodeURIComponent(w.label)}`)});
    }
    if (seq !== sheets.seq) return;
    const people = studentHistory(results, section);
    head.firstChild.textContent = people.length
      ? `${people.length} student${people.length === 1 ? '' : 's'} in ${cls}${section === 'all' ? '' : ' ' + section} with missing work, one page each. Print from here; each student starts on a new page.`
      : `Nobody in ${cls}${section === 'all' ? '' : ' ' + section} has missing work.`;
    if (people.length) head.lastChild.prepend(el('button', {type: 'button', class: 'primary', onclick: () => window.print()}, 'Print'));
    box.append(...people.map(p => sheetFor(p, cls)));
  } catch (error) {
    if (seq === sheets.seq) head.firstChild.replaceWith(note(error.message));
  }
}

// ---------- Routine: the morning score handoff ----------
function renderRoutine() {
  $('routine-body').replaceChildren(el('section', {class: 'routine routine-morning'},
    el('h2', {text: 'Morning score check'}),
    el('ol', {},
      el('li', {}, el('strong', {text: 'Before 6:30.'}), ' Leave the home Mac awake. Keep the school Chrome profile signed in with one Teaching Hub tab and one Infinite Campus tab.'),
      el('li', {}, el('strong', {text: 'At 6:30.'}), ' The scheduled roster check runs first, including Study Hall. Verified submitted work is filed as dated copies in private folders by class, section and student. Original files and Form upload folders are preserved; submission tidy finishes before score entry.'),
      el('li', {}, el('strong', {text: 'At or after 7:00.'}), ' With weekday entry enabled, the Chrome helper processes mapped assignments across classes, fills blanks and raises lower scores only, then saves, reloads and verifies. Review the gradebook afterward; adjust any score that needs correction.'),
      el('li', {}, el('strong', {text: 'At 7:30.'}), ' Student feedback goes in those same student folders. Teacher summaries, growth reports and notes go in separate private teacher report folders for each class.'),
    ),
    el('p', {class: 'muted', text: 'An enabled schedule, ready helper or attempted run is not proof of saved grades. Confirm the helper receipt and IC readback. If a save or readback is uncertain, stop further writes and inspect the named assignment in IC before clearing the recovery block.'}),
    helperLine()));
}

// ---------- Link check ----------
// Problems first, one line each; Forms shared by weeks and decks the Hub
// can't open are summed up underneath, since they're usually fine.
const QUIET = new Set(['shared-form', 'deck-unreadable', 'no-form']);

function linkCheckResult(name, data) {
  const problems = data.flags.filter(f => !QUIET.has(f.kind));
  const shared = data.flags.filter(f => f.kind === 'shared-form');
  const decks = data.flags.filter(f => f.kind === 'deck-unreadable');
  const noAgendaForm = data.flags.filter(f => f.kind === 'no-form');
  const releases = data.rows.filter(r => r.release);
  const notReady = releases.filter(r => r.release.status !== 'READY');
  const releaseText = row => {
    const release = row.release;
    if (!release) return '—';
    if (release.status === 'READY') return `Ready · revision ${release.revision || 'verified'}`;
    const context = {...row, className: name, classKey: CLASSES.find(([, label]) => label === name)?.[0]};
    return `${lessonCodeAdvisory(context) ? 'Code advisory' : releaseLabel(release)} · ${lessonReviewAction(context)}`;
  };
  return [
    data.problem ? note(data.problem) : null,
    data.exclusions?.length ? el('p', {class: 'muted', text: data.exclusions.map(e => `${e.week} · ${e.topic}: ${recordProblem(e.reason)}`).join(' ')}) : null,
    problems.length
      ? el('ul', {class: 'link-flags'}, problems.map(f => el('li', {}, el('strong', {text: `${f.week} · ${f.topic}: `}), f.message)))
      : el('p', {class: 'ok', text: `✓ No other Agenda link problems found for ${name}.`}),
    noAgendaForm.length ? el('p', {class: 'muted', text: `${noAgendaForm.map(f => `${f.week} · ${f.topic}`).join('; ')}: no Google Form is linked in the Agenda. If students turn work in through Schoology, this is expected; the Hub can’t check Schoology.`}) : null,
    releases.length ? (notReady.length
      ? el('p', {class: 'muted', text: `Embedded-code check: ${releases.length - notReady.length} ready · ${notReady.length} need attention in the current/upcoming window. Each lesson below gives its exact status and action.`})
      : el('p', {class: 'ok', text: `✓ Current/upcoming ${name} decks passed the deterministic content/code gate.`})) : null,
    shared.length ? el('details', {class: 'muted'},
      el('summary', {text: `${shared.length} Form${shared.length === 1 ? ' is' : 's are'} used for more than one week (fine when a lesson spans those weeks)`}),
      el('ul', {class: 'link-flags'}, shared.map(f => el('li', {}, el('strong', {text: `${f.week}: `}), f.message)))) : null,
    decks.length ? el('p', {class: 'muted', text: `${decks.length} deck link${decks.length === 1 ? '' : 's'} couldn’t be checked: the Hub can’t open ${decks.length === 1 ? 'it' : 'them'}.`}) : null,
    el('details', {}, el('summary', {text: `See every ${name} lesson’s Forms and decks`}),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Week', 'Topic', 'Turn-in Forms', 'Decks', 'Release'].map(h => el('th', {text: h})))),
        el('tbody', {}, data.rows.map(r => el('tr', {}, el('td', {text: r.week}), el('td', {text: r.topic}),
          el('td', {text: r.forms.filter(f => f.where !== '_turnInURL').map(f => f.student_title || f.title || f.label).join(' · ')
            || (r.week_policy?.excluded === true && !r.week_policy.problem ? 'Not assigned this school year' : '—')}),
          el('td', {text: r.decks.map(d => d.title || d.label).join(' · ') || '—'}),
          el('td', {}, el('p', {text: releaseText(r)}), r.release ? renderSourceReviewNotice(
            lessonReviewNotice({...r, className: name, classKey: CLASSES.find(([, label]) => label === name)?.[0]}),
            {class_name: name, week: r.week, lesson: r.topic}) : null)))))))];
}

async function runLinkCheck() {
  const body = $('link-check-body');
  const button = $('run-link-check');
  button.disabled = true;
  body.replaceChildren();
  for (const [key, name] of CLASSES) {
    const part = el('div', {class: 'link-class'}, el('h3', {text: name}), el('p', {class: 'muted', text: 'Checking…'}));
    body.append(part);
    try {
      const data = await getJSON(`api/audit?class=${key}`);
      part.lastChild.replaceWith(...linkCheckResult(name, data).filter(Boolean));
    } catch (error) {
      part.lastChild.replaceWith(note(error.message));
    }
  }
  button.disabled = false;
}

function loadRoutine() {
  renderRoutine();
  loadMorningSummary();
  window.postMessage({channel: HELPER.send, version: 1, request_id: crypto.randomUUID(), type: 'status'}, location.origin);
}
function showTab() {
  const [tab, ...rest] = location.hash.slice(1).split('/');
  const current = ['today', 'plans', 'work', 'routine'].includes(tab) ? tab : 'today';
  for (const name of ['today', 'plans', 'work', 'routine']) $('page-' + name).hidden = name !== current;
  document.querySelectorAll('.tabs a').forEach(a => {
    if (a.dataset.tab === current) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (current === 'today') loadToday();
  else if (current === 'work') openWork(rest.map(part => decodeURIComponent(part)));
  else if (current === 'routine') loadRoutine();
  else {
    loadLessonReadiness();
    if (!plans.started) loadPlans(plans.date);
  }
}

fillSelect($('work-class'), CLASSES, work.cls);
$('prev-week').addEventListener('click', () => loadPlans(addDays(plans.date, -7)));
$('next-week').addEventListener('click', () => loadPlans(addDays(plans.date, 7)));
$('today').addEventListener('click', () => loadPlans(todayISO()));
$('reload-plans').addEventListener('click', () => { loadLessonReadiness(true); loadPlans(plans.date, true); });
$('date').addEventListener('change', e => { if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) loadPlans(e.target.value); });
$('work-class').addEventListener('change', e => { work.cls = e.target.value; store.set('class', work.cls); work.weeksFor = null; $('tidy-box').replaceChildren(); openWork(); });
$('work-week').addEventListener('change', e => { work.week = e.target.value; $('tidy-box').replaceChildren(); loadWork(); });
$('work-section').addEventListener('change', e => { work.section = e.target.value; store.set('section', work.section); closeSheets(); renderWork(); });
$('student-records').addEventListener('toggle', () => { if ($('student-records').open) loadStudentRecords(true); });
$('reload-today').addEventListener('click', () => loadToday(true));
$('reload-routine').addEventListener('click', loadRoutine);
$('run-link-check').addEventListener('click', runLinkCheck);
$('reload-work').addEventListener('click', async () => {
  try { work.weeksFor = null; await loadWeeks(true); await loadWork(); await loadStudentRecords(true); } catch (error) { setStatus('work-status', error.message, true); }
});
$('tidy-work').addEventListener('click', () => { if (work.week) previewTidy(); });
$('missing-sheets').addEventListener('click', missingWorkSheets);
window.addEventListener('hashchange', showTab);
showTab();

let morningReports = [], morningRecovery = [];
function renderMorningAlert() {
  const box = $('morning-alert');
  if (!box) return;
  const live = helper.morning, latest = morningReports[0];
  const uncertain = new Set(['readback_incomplete', 'save_unconfirmed', 'readback_mismatch', 'uncertain_blocked']);
  const affected = latest?.code === 'uncertain_blocked' ? morningReports.find(r => r.items.some(i => uncertain.has(i.code))) || latest : latest;
  const verified = affected && morningRecovery.find(r => r.type === 'verified' && r.run_id === affected.run_id);
  const held = affected?.items.filter(i => uncertain.has(i.code)).map(i => i.assignment) || [];
  box.replaceChildren(); box.hidden = false;
  if (live?.blocked || (!live && latest && !verified && (held.length || uncertain.has(latest.code)))) {
    const names = live?.assignments?.length ? live.assignments : held;
    box.append(note(`Automatic score entry is blocked${names.length ? ': ' + names.join('; ') : ''}. Check IC, then use “Mark verified” in the helper popup. No further automatic entry will run until the block is cleared.`));
    if (live?.session === 'signed_out') box.append(note('Infinite Campus is also signed out. Sign in to IC in this school Chrome profile.'));
  } else if (live?.session === 'signed_out' || (!live && latest?.code === 'signed_out' && !verified)) {
    box.append(note('Infinite Campus is signed out. Sign in to IC in this school Chrome profile before the morning run.'));
  } else if (live && live.session !== 'ready') {
    box.append(note('Infinite Campus connection is unavailable. Keep one signed-in IC tab open in this school Chrome profile.', 'info'));
  } else if ((live?.verified && (live.verified.run_id === live.run_id || latest?.code === 'uncertain_blocked')) || verified) {
    const v = live?.verified || verified;
    box.append(note(`Manually verified by ${v.by} at ${new Date(v.at).toLocaleString()}. ${live?.enabled === false ? 'Automatic mornings are off.' : 'The readback block is cleared.'}`, 'info'));
  } else box.hidden = true;
}

// The counts-only morning receipt is shared through the existing private ledger,
// so it can be read from school even when this Mac is no longer reachable.
async function loadMorningSummary() {
  const box = $('morning-summary');
  if (!box) return;
  const seq = ++morningSummarySeq;
  invalidateReviewPanel(visibleReviewPanels.routine);
  invalidateReviewPanel(visibleReviewPanels.today);
  try {
    const data = await getJSON('api/morning/reports');
    if (seq !== morningSummarySeq) return;
    const reports = data.reports || [];
    morningReports = reports; morningRecovery = data.recovery || []; renderMorningAlert();
    box.replaceChildren(el('h2', {text: 'Morning helper review'}));
    visibleReviewPanels.routine = currentSourceReviewPanel($('page-routine')?.hidden !== true);
    box.append(visibleReviewPanels.routine);
    if (!reports.length) { box.append(note('No score-entry receipt is recorded yet. The weekday helper runs at 7:00 when enabled. An enabled schedule is not proof of entry; Today shows the saved-and-reloaded results when a receipt arrives.', 'info')); return; }
    const words = value => String(value).replaceAll('_', ' ');
    box.append(note('IC is the roster source of truth. The Hub has not synced roster changes for this run; normal additions and departures should not be treated as student-work exceptions.', 'info'));
    reports.forEach((report, i) => {
      const lines = el('ul', {}, report.items.map(row => {
        if (row.code === 'source_scores_held') return el('li', {}, el('strong', {text: morningContext(row) + ': '}), morningSourceHold(row));
        if (row.code === 'excluded_week') return el('li', {}, el('strong', {text: morningContext(row) + ': '}), MORNING_EXCLUDED);
        const review = morningRowReview(row);
        const reasons = review.reasons.map(([reason, count]) => `${count} — ${MORNING_REASONS[reason]}.`);
        if (row.code === 'coverage_incomplete' && review.detailKnown) reasons.unshift(MORNING_COVERAGE);
        return el('li', {}, el('strong', {text: morningContext(row)}),
          ` — ${row.filled} entered, ${row.raised} raised, ${row.kept} kept${review.detailKnown ? `, ${review.left} unchanged` : ''} — ${words(row.code)}. `,
          review.unavailable || reasons.join(' '));
      }));
      const details = morningRecovery.filter(r => r.run_id === report.run_id).map(r =>
        r.type === 'verified' ? el('p', {text: `Manually verified by ${r.by} at ${new Date(r.at).toLocaleString()}: ${r.note}`})
          : el('p', {text: `${r.assignment} · ${r.section} · ${words(r.column)} — students requiring readback: ${r.names.length ? r.names.join('; ') : 'details unavailable; check the full panel'}.`}));
      const emptyResult = report.items.length ? null : el('p', {text: morningProblem(report)});
      box.append(el('details', {open: i === 0}, el('summary', {text: `${report.day}: ${words(report.code)}`}), emptyResult, lines, ...details));
    });
  } catch { if (seq === morningSummarySeq) box.replaceChildren(el('h2', {text: 'Morning helper review'}), note('The morning summary could not be read. No successful IC save is confirmed.')); }
}
loadMorningSummary();
