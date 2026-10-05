// Teaching Hub v2: lesson plans from the Agenda, and student work found from
// each lesson's Form responses and turn-in folder, with completion scores
// counted from each deck's coded boxes. Scores go into Infinite Campus through
// the Chrome helper (the browser companion extension) in the teacher's own
// Chrome, after her OK there, or by her own typing; never by this page.
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
const cockpit = {seq: 0, audits: null, auditsAt: 0};

function cockpitPanel(title, ...children) {
  return el('section', {class: 'cockpit-panel'}, el('h2', {text: title}), ...children);
}

function releaseIssue(value) {
  if (!value) return '';
  const [code, aspect, phase] = String(value).split(':');
  const words = code.replaceAll('_', ' ');
  if (code === 'critical_aspect_block_missing') return `Missing critical-aspect block: ${aspect}`;
  if (code === 'critical_aspect_phase_missing') return `${aspect}: missing ${String(phase || '').replaceAll('_', ' ')}`;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function releaseBadge(release) {
  if (!release) return el('span', {class: 'release-badge neutral', text: 'Not checked'});
  const kind = release.status === 'READY' ? 'ready' : release.status === 'NOT_READY' ? 'blocked' : 'stale';
  return el('span', {class: 'release-badge ' + kind, text: release.status.replaceAll('_', ' ')});
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
  const out = {filled: 0, raised: 0, kept: 0, left: 0, reasons: {flag: 0, excused: 0, check: 0, name: 0, missing: 0}};
  for (const item of report?.items || []) {
    for (const key of ['filled', 'raised', 'kept', 'left']) out[key] += item[key] || 0;
    for (const key of Object.keys(out.reasons)) out.reasons[key] += item.reasons?.[key] || 0;
  }
  return out;
}

function renderMorningCockpit(report, routineData) {
  if (!report) return cockpitPanel('What ran',
    note('No 7:00 a.m. score-entry receipt is recorded for today. This does not mean grades failed; it means no completed morning receipt is available.', 'info'),
    routineData?.scores_entered?.last ? el('p', {class: 'muted', text: 'Latest Scores entered record: ' + routineData.scores_entered.last}) : null);
  const totals = morningTotals(report);
  const handled = totals.filled + totals.raised + totals.kept;
  const detail = (report.items || []).map(item =>
    el('li', {}, el('strong', {text: item.assignment + ': '}),
      `${item.filled} entered · ${item.raised} raised · ${item.kept} kept · ${item.left} left`));
  return cockpitPanel('What ran',
    el('p', {class: 'cockpit-number', text: String(handled)}),
    el('p', {class: 'cockpit-label', text: 'grade rows handled or preserved by the morning run'}),
    el('p', {class: totals.left ? 'needs' : 'ok', text: totals.left
      ? `${totals.left} row${totals.left === 1 ? '' : 's'} left for review.`
      : '✓ Nothing from this receipt was left unresolved.'}),
    el('details', {}, el('summary', {text: 'Morning assignment details'}), el('ul', {}, ...detail)));
}

function renderNeedsYou(report, releaseRows) {
  const items = [];
  for (const row of report?.items || []) {
    const reasons = Object.entries(row.reasons || {}).filter(([, n]) => n);
    if (!row.left && !reasons.length) continue;
    items.push(el('li', {}, el('strong', {text: row.assignment + ': '}),
      reasons.length ? reasons.map(([reason, n]) => `${n} ${reason.replaceAll('_', ' ')}`).join(' · ') : `${row.left} left`));
  }
  for (const row of releaseRows.filter(row => row.release.status !== 'READY')) {
    items.push(el('li', {}, el('strong', {text: `${row.className} · ${row.week} · ${row.topic}: `}),
      releaseIssue(row.release.issues?.[0]) || row.release.status.replaceAll('_', ' ')));
  }
  return cockpitPanel('Needs you',
    items.length ? el('ul', {class: 'attention-list'}, ...items)
      : el('p', {class: 'ok', text: '✓ No grade-review exception or current deck-release problem is recorded.'}),
    el('p', {class: 'muted', text: 'Excused/flagged rows, name mismatches, changed IC values and uncertain readback remain teacher decisions; ordinary blanks and raises do not.'}));
}

function renderTodayLessons(day, audits) {
  const rows = [];
  for (const cls of day?.classes || []) {
    for (const lesson of cls.lessons || []) {
      const release = releaseFor(audits, cls.key, lesson);
      const links = (lesson.materials || []).slice(0, 4).map(material => link(material.label + ' ↗', material.url, 'chip'));
      rows.push(el('article', {class: 'today-lesson'},
        el('div', {class: 'today-lesson-head'},
          el('div', {}, el('strong', {text: cls.name}), el('h3', {text: lesson.topic || 'Untitled lesson'})),
          releaseBadge(release)),
        release?.issues?.length ? el('p', {class: 'needs', text: releaseIssue(release.issues[0])}) : null,
        links.length ? el('div', {class: 'links'}, ...links) : null,
        el('div', {class: 'action-row'},
          el('button', {type: 'button', onclick: () => { location.hash = '#plans'; }}, 'Review / revise lesson'))));
    }
  }
  return cockpitPanel("Today's lessons",
    rows.length ? el('div', {class: 'today-lessons'}, ...rows)
      : el('p', {class: 'muted', text: day?.weekday ? `No lessons are listed for ${FULL_DAY[day.weekday] || day.weekday}.` : 'No lesson data was returned.'}));
}

function renderNextLessons(audits) {
  const rows = allReleaseRows(audits);
  if (!rows.length) return cockpitPanel('Next from Agenda',
    note('No current/upcoming final deck was release-checked. Open Lessons / VT to inspect the Agenda and link audit.', 'info'));
  return cockpitPanel('Next from Agenda',
    el('div', {class: 'table-wrap'}, el('table', {class: 'cockpit-table'},
      el('thead', {}, el('tr', {}, ['Class', 'Week', 'Lesson', 'Release', 'Next'].map(text => el('th', {text})))),
      el('tbody', {}, ...rows.map(row => el('tr', {},
        el('td', {text: row.className}),
        el('td', {text: row.week}),
        el('td', {text: row.topic}),
        el('td', {}, releaseBadge(row.release)),
        el('td', {text: row.release.status === 'READY'
          ? 'Teach; visual-only edits get a fast recheck.'
          : releaseIssue(row.release.issues?.[0]) || 'Revalidate before teaching.'}))))));
}

async function loadToday(force = false) {
  const seq = ++cockpit.seq;
  setStatus('today-status', 'Reading today’s Agenda, morning receipts and deck readiness…');
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
    const releaseRows = allReleaseRows(audits);
    $('today-body').replaceChildren(
      el('div', {class: 'cockpit-grid'},
        renderMorningCockpit(report, routineData),
        renderNeedsYou(report, releaseRows)),
      renderTodayLessons(day, audits),
      renderNextLessons(audits),
      cockpitPanel('Feedback',
        el('p', {text: 'Saved feedback still prints from Student work. The automatic Ready to print / Handed out / Needs new feedback queue is the next v3 slice.'}),
        el('div', {class: 'action-row'},
          el('button', {type: 'button', onclick: () => { location.hash = '#work'; }}, 'Open Student work'))));
    setStatus('today-status', `Updated ${when(new Date().toISOString())}.`);
  } catch (error) {
    if (seq !== cockpit.seq) return;
    setStatus('today-status', error.message, true);
    $('today-body').replaceChildren();
  }
}

async function loadLessonReadiness(force = false) {
  const box = $('lesson-readiness');
  if (!box) return;
  box.replaceChildren(el('p', {class: 'muted', text: 'Checking current and upcoming deck releases…'}));
  try {
    const audits = await loadAudits(force);
    const rows = allReleaseRows(audits);
    if (!rows.length) {
      box.replaceChildren(note('No current/upcoming deck release result is available yet.', 'info'));
      return;
    }
    box.replaceChildren(
      el('div', {class: 'readiness-head'},
        el('strong', {text: 'VT / feedback-code readiness'}),
        el('span', {class: 'muted', text: 'Actual final deck + live Prompt 0 / Prompt 2'})),
      el('div', {class: 'release-row'}, ...rows.map(row =>
        el('div', {class: 'release-item'},
          el('span', {text: `${row.className} · ${row.week}`}),
          releaseBadge(row.release),
          el('small', {text: row.release.status === 'READY' ? row.topic : (releaseIssue(row.release.issues?.[0]) || row.topic)})))));
  } catch (error) {
    box.replaceChildren(note('Deck readiness could not be checked: ' + error.message));
  }
}

// ---------- Lesson plans ----------
const plans = {date: todayISO(), seq: 0, started: false, data: null, editing: null, weeks: {}, weeksLoading: {}};

function renderDays(date) {
  const monday = mondayOf(date);
  $('days').replaceChildren(...DAYS.map((name, i) => {
    const day = addDays(monday, i);
    return el('button', {type: 'button', 'aria-current': day === date ? 'date' : null, onclick: () => loadPlans(day)},
      el('small', {text: name}), el('strong', {text: String(at(day).getDate())}));
  }));
}

async function loadPlans(date, fresh = false) {
  plans.started = true; plans.date = date; $('date').value = date; renderDays(date);
  const seq = ++plans.seq;
  setStatus('plans-status', 'Reading the Agenda…');
  try {
    const data = await getJSON(`api/day?date=${encodeURIComponent(date)}${fresh ? '&fresh=1' : ''}`);
    if (seq !== plans.seq) return;
    showAgendaLink(data.agenda);
    renderPlans(data);
    setStatus('plans-status', '');
  } catch (error) {
    if (seq !== plans.seq) return;
    setStatus('plans-status', error.message, true);
    $('cards').replaceChildren();
  }
}

function block(label, text) {
  return el('div', {class: 'block'}, el('p', {class: 'label', text: label}), el('p', {class: 'plan', text}));
}

function renderLesson(cls, lesson, weekday) {
  const box = el('div', {class: 'lesson'}, el('h3', {text: lesson.topic || 'Untitled lesson'}));
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
}

// ---------- Student work ----------
const work = {cls: store.get('class') || 'forensics', week: null, section: store.get('section') || 'all', weeksFor: null, data: null, seq: 0, scores: {}, ui: {}, marks: {}};

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
  setStatus('work-status', 'Finding student work from the Form responses…');
  $('work-body').replaceChildren();
  try {
    const data = await getJSON(`api/work?class=${work.cls}&week=${encodeURIComponent(work.week)}${fresh ? '&fresh=1' : ''}`);
    if (seq !== work.seq) return;
    work.data = data;
    work.scores = {};
    work.ui = {};
    work.marks = {};
    renderWork();
    loadMarks(data.lessons.flatMap(l => l.forms).filter(f => f.form_id), seq);
    setStatus('work-status', `Read ${when(new Date().toISOString())}.`);
    // Score every assignment right away; results are kept on the server, so this is quick after the first time.
    for (const form of data.lessons.flatMap(l => l.forms)) {
      if (seq !== work.seq) return;
      if (form.form_id && form.students.length) await scoreForm(form);
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
    f.via ? el('span', {class: 'badge warn', title: 'This lesson’s deck, turned in through another lesson’s Form; it counts here', text: `Via ${f.via}`}) : null,
    f.other_week ? el('span', {class: 'badge warn', title: 'The file name mentions a different week', text: `Named Week ${f.other_week}`}) : null,
    f.late ? el('span', {class: 'badge warn', title: 'Turned in more than two weeks after this week — not graded', text: 'Late'}) : null,
    f.trashed ? el('span', {class: 'badge warn', text: 'In Drive trash'}) : null,
    f.problem ? el('span', {class: 'badge', title: f.problem, text: 'Open it yourself'}) : null)));
}

// ---------- Completion scores (Notes and Revision, out of 10) ----------
const DECKS = ['application/vnd.google-apps.presentation', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'];
const scoreFile = row => row.files.find(f => DECKS.includes(f.mime)) || row.files[0];
const fmt = value => value == null ? 'N/A' : String(value);
// Once an assignment's first turn-in appears (its two weeks have begun), every
// empty score is 0, and a 0 is replaced when the student turns work in before
// the two weeks close. Before anyone has turned it in, nothing is scored.
const closedFor = form => Boolean(form.window && new Date(form.window.at) < new Date());
// Its two weeks have begun (they never begin before the lesson's own week): only then do 0s count.
const startedFor = form => Boolean(form.window && (!form.window.from || new Date(form.window.from) <= new Date()));
const shown = (score, started = true) => !score ? (started ? ['0', '0'] : ['', ''])
  : [score.notes_score, score.revision_score].map(fmt);
const STATUS = {blank: 'blank', copied: 'looks copied from the slide', 'not found': 'its code isn’t in this copy'};

function scoreCells(row, scores) {
  if (!scores) return [];
  const result = scores[scoreFile(row)?.id];
  if (!row.files.length) return [el('td', {text: '—'}), el('td', {text: '—'})];
  if (!result) return [el('td', {class: 'muted', text: '…'}), el('td', {class: 'muted', text: '…'})];
  if (result.problem) return [el('td', {colspan: 2}, el('small', {class: 'muted', text: result.problem}))];
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
    detail, result.note ? el('small', {class: 'note warn', text: result.note}) : null),
    el('td', {}, cell(revisionShown, c.DRAFT.done, c.DRAFT.required, 'revised answers'))];
}

async function scoreForm(form) {
  const rows = form.students.filter(r => r.files.length);
  const scores = work.scores[form.form_id] = work.scores[form.form_id] || {};
  const queue = rows.filter(r => !scores[scoreFile(r).id]);
  const seq = work.seq;
  let done = rows.length - queue.length;
  work.ui[form.form_id] = {...work.ui[form.form_id], progress: `Scoring ${done} of ${rows.length}…`};
  renderWork();
  const worker = async () => {
    while (queue.length && seq === work.seq) {
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
  const students = form.students.filter(r => r.matched && inSection(r))
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
  panel.append(el('p', {class: 'muted', text: `What ${students.length} student${students.length === 1 ? '' : 's'} wrote in each coded box of their on-time deck${section === 'all' ? '' : ` (${section})`}. Late work and decks that couldn’t be scored aren’t included.`}));
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

function uncodedReport(scores) {
  const slides = {};
  for (const result of Object.values(scores)) {
    for (const slide of new Set((result.uncoded || []).map(u => u.slide))) slides[slide] = (slides[slide] || 0) + 1;
  }
  const list = Object.entries(slides).sort((a, b) => a[0] - b[0]);
  if (!list.length) return null;
  return note(`Writing in boxes with no code (not scored): ${list.map(([slide, n]) => `slide ${slide} (${n} student${n > 1 ? 's' : ''})`).join(', ')}. Run your embed-coding program on this deck before you hand it out next time.`, 'info');
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
  const ui = work.ui[form.form_id] = {...work.ui[form.form_id]};
  if (ui.open === which) { ui.open = null; renderWork(); return; }
  ui.open = which; ui.error = null; ui.busy = which === 'feedback' ? 'Finding students with revised answers…' : null;
  renderWork();
  if (which !== 'feedback') return;
  try {
    ui.feedback = await getJSON(`api/feedback?class=${work.cls}&week=${encodeURIComponent(work.week)}&form=${encodeURIComponent(form.form_id)}`);
  } catch (error) { ui.error = error.message; }
  ui.busy = null;
  renderWork();
}

async function loadMarks(forms, seq) {
  await Promise.all(forms.map(async form => {
    try {
      const sheet = await getJSON(`api/ledger?class=${work.cls}&week=${encodeURIComponent(work.week)}&form=${encodeURIComponent(form.form_id)}`);
      if (seq === work.seq) work.marks[form.form_id] = sheet;
    } catch { /* the card simply shows no marks */ }
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
// turns work in. Once they close, the final scores go in. After final scores
// are entered (the "I entered these in IC" note is dated after the close),
// late work isn't graded, so there's nothing more to enter.
function icStatus(form) {
  if (!form.window) return null;
  const sheet = work.marks[form.form_id];
  const entered = sheet?.entered_at ? new Date(sheet.entered_at.at) : null;
  if (!startedFor(form)) {
    return {kind: 'open', text: `IC: its two weeks start ${form.window.opened}. Enter only the scores listed; 0s for work not turned in start then.`};
  }
  if (!closedFor(form)) {
    return {kind: 'open', text: `IC: open until ${form.window.label}. Enter every score listed, including the 0s; a 0 is replaced when the student turns work in before it closes.`};
  }
  if (entered && entered >= new Date(form.window.at)) {
    return {kind: 'final', text: `IC: final scores entered ${markDate(sheet.entered_at)}. Late work isn’t graded, so there’s nothing more to enter.`};
  }
  return {kind: 'due', text: `IC: closed ${form.window.label}. Final scores are due: enter every score, including the 0s, then click “I entered these in IC”.`};
}

function marksLine(form) {
  const sheet = work.marks[form.form_id];
  if (!sheet?.configured) return null;
  const parts = [sheet.entered_at ? `✓ Entered in IC ${markDate(sheet.entered_at)}` : '',
    sheet.feedback_at ? `✓ Feedback made ${markDate(sheet.feedback_at)}` : ''].filter(Boolean);
  return parts.length ? el('p', {class: 'marks', text: parts.join(' · ')}) : null;
}

function icPanel(form, scores, lesson) {
  const section = work.section;
  const ui = work.ui[form.form_id] || {};
  const sheet = work.marks[form.form_id];
  const entered = sheet?.entered_at ? new Date(sheet.entered_at.at) : null;
  const closed = closedFor(form);
  const inSection = r => section === 'all' || r.section === section;
  const elsewhere = elsewhereKeys(form);
  const all = [
    // A score the Hub isn't sure of (the copy lacks many of the deck's codes) is
    // never entered automatically: the teacher checks it by hand.
    ...form.students.filter(r => r.matched && inSection(r)).map(r => ({r, s: scores[scoreFile(r)?.id]}))
      .filter(x => x.s && !x.s.problem).map(x => ({...x, unsure: Boolean(x.s.note),
        show: x.s.note ? ['check by hand', 'check by hand'] : shown(x.s)})),
    // A student with a file in "Not sorted" may have turned it in: they're left
    // off the list (and named below) so a 0 isn't entered for them unchecked.
    // A student whose only turn-in was another lesson's deck (through this Form) is
    // checked by hand, not given a 0 unseen.
    ...(startedFor(form) ? [...form.missing.filter(m => !m.unsorted), ...form.late.filter(r => r.matched)].filter(inSection)
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
  const canSend = helper.state === 'ready' && icStatus(form)?.kind !== 'final' && helperRows(form).length;
  panel.append(el('p', {}, helper.state === 'ready'
    ? 'The Chrome helper enters these in IC. Click “Send to the Chrome helper”, then in Infinite Campus open this assignment’s score panel ((F) for Notes, (S) for Revision, in each section) and click “Hub helper”. It shows what it will do and enters nothing until you tick its box. On Monday the Claude extension does these clicks after your OK.'
    : 'The Chrome helper isn’t installed in this browser, so it can’t enter these (see Routine). To enter them yourself, open this assignment in Infinite Campus and type them in.',
    entered ? ` You entered it on ${markDate(sheet.entered_at)}; ${fresh.length ? `the ${fresh.length} marked “new since” turned in after that.` : 'nobody has turned in anything new since.'}` : ''));
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
      link(`“${sheet.name || 'Scores entered'}”`, sheet.url), ' by itself once the Chrome helper has entered every section and column. Click “I entered these in IC” only if you typed the scores into IC yourself. From then on, only work turned in later is marked new; once the two weeks have closed, it marks this assignment’s final scores done, and nothing more is entered for it.'));
  }
  panel.append(el('div', {class: 'table-wrap'}, el('table', {},
    el('thead', {}, el('tr', {}, ['Student', 'Section', 'Notes /10', 'Revision /10', ''].map(h => el('th', {text: h})))),
    el('tbody', {}, all.map(({r, s: result, show, zero, other, excused, unsure, fresh: isNew}) => el('tr', {}, el('td', {text: `${r.last}, ${r.first}`}), el('td', {text: r.section}),
      el('td', {text: show[0] || '—'}), el('td', {text: show[1] || '—'}),
      el('td', {}, zero ? el('span', {class: 'badge', text: closed ? 'nothing on time' : 'nothing yet'}) : null,
        other ? el('span', {class: 'badge warn', text: 'turned in another lesson’s deck through this Form'}) : null,
        excused ? el('span', {class: 'badge', title: `Added to the roster ${r.added}, after this assignment began`,
                              text: 'new student: mark Exempt in IC'}) : null,
        unsure ? el('span', {class: 'badge warn', title: result.note,
                             text: `Hub counted ${fmt(result.notes_score)} / ${fmt(result.revision_score)}; not sure`}) : null,
        entered && isNew ? el('span', {class: 'badge warn', text: `new since ${markDate(sheet.entered_at)}`}) : null)))))));
  const unsorted = startedFor(form) ? form.missing.filter(m => m.unsorted && inSection(m)) : [];
  if (unsorted.length) {
    panel.append(note(`Not listed: ${unsorted.map(m => `${m.last}, ${m.first}`).join('; ')}. `
      + 'Each has a file in “Not sorted” that may be this assignment. Check that box before giving them a 0.', 'info'));
  }
  panel.append(el('p', {class: 'muted', text: closed
    ? 'The two weeks are over, so students with nothing on time (including late work) are listed as 0. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.'
    : `Students with nothing turned in yet, and empty boxes, are listed as 0; each 0 is replaced when the student turns work in before ${form.window?.label || 'the two weeks close'}. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.`}));
  return panel;
}

// ---------- Chrome helper: enters an assignment's IC list in Infinite Campus ----------
// The browser companion extension in the teacher's own Chrome. This page hands
// it the list shown above (every roster student, with what to enter); in IC the
// helper shows what it will do, and only after her tick and click there does it
// type, save, reload and check the scores. It tells this page the counts, never
// names. Once every section and column is done, the Hub notes "Entered in IC".
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
  return new Set((form.elsewhere || []).map(e => {
    const [last, ...first] = String(e.who).split(',');
    return personKey({last, first: first.join(','), section: e.section});
  }));
}

// Every roster student of the class (all sections) with what to enter, the
// same rules as the table above.
function helperRows(form) {
  const scores = work.scores[form.form_id] || {};
  const started = startedFor(form), elsewhere = elsewhereKeys(form);
  const rows = new Map();
  const add = (r, kind, notes = null, revision = null, reason = '') => {
    const key = personKey(r);
    if (rows.has(key)) { Object.assign(rows.get(key), {kind: 'check', notes: null, revision: null, reason: 'listed twice on the roster'}); return; }
    if (!oneLine(r.last) || !oneLine(r.section)) return;
    rows.set(key, {last: oneLine(r.last, 100), first: oneLine(r.first, 100), section: oneLine(r.section, 100), kind, notes, revision, reason});
  };
  for (const r of form.students.filter(r => r.matched)) {
    const s = scores[scoreFile(r)?.id];
    if (!s) add(r, 'check', null, null, 'not scored yet');
    else if (s.problem) add(r, 'check', null, null, 'deck couldn’t be scored');
    else if (s.note) add(r, 'check', null, null, 'the Hub isn’t sure of this score');
    else if (s.notes_score == null && s.revision_score == null) add(r, 'check', null, null, 'no codes to count');
    else add(r, 'score', s.notes_score ?? null, s.revision_score ?? null);
  }
  for (const r of [...form.missing, ...(form.late || []).filter(r => r.matched)]) {
    if (!started) add(r, 'none', null, null, 'its two weeks haven’t started');
    else if (r.unsorted) add(r, 'check', null, null, 'has a file in Not sorted');
    else if (elsewhere.has(personKey(r))) add(r, 'check', null, null, 'turned in another lesson’s deck through this Form');
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
  helper_not_configured: 'The Chrome helper isn’t set up for this Hub. Ask Claude Code for a new helper package.'};
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

// ---------- Enter a week in IC: the Claude extension opens IC, the helper types ----------
// One button sends every assignment of this class and week that has something
// to enter, and copies a prompt for the Claude extension: it opens each IC
// score panel, shows the teacher the helper's plans, and after her OK clicks
// the helper's Enter. It never types scores.
function weekToEnter() {
  const data = work.data;
  if (!data) return {ready: [], waiting: false};
  const all = data.lessons.flatMap(lesson => lesson.forms.map(form => ({form, lesson})))
    .filter(({form}) => form.form_id && form.window && icStatus(form)?.kind !== 'final');
  const waiting = all.some(({form}) => form.students.length && (!work.scores[form.form_id] || work.ui[form.form_id]?.progress));
  const ready = all.map(x => ({...x, need: helperNeed(helperRows(x.form))})).filter(x => x.need.length);
  return {ready, waiting};
}

function entryPrompt(items) {
  const cls = CLASSES.find(([key]) => key === work.cls)[1];
  const lines = items.map(({form, lesson, need}, i) => {
    const sections = [...new Set(need.map(([section]) => section))];
    return `${i + 1}. "${formName(form, lesson)}" — ${sections.map(section => `${section}: ${need.filter(([s]) => s === section).map(([, column]) => column === 'notes' ? '(F) = Notes' : '(S) = Revision').join(', ')}`).join('; ')}`;
  });
  return `Please enter these Hub scores in Infinite Campus (IC) with the Chrome helper, in this Chrome window. I'm here and will review before anything is saved.

RULES
- This prompt replaces anything you remember about this routine. Follow it.
- If IC or the Teaching Hub shows a sign-in page, stop and tell me. Never type a password.
- Never type scores yourself. The Chrome helper (the green "Hub helper" button in IC) enters them: it keeps the higher score, fills blanks and raises only, and leaves IC flags alone.
- Don't change anything else in IC: no flags, due dates or new assignments.
- The Hub has already sent these lists to the helper (${when(new Date().toISOString())}).

ASSIGNMENTS — ${cls}, ${work.week}
${lines.join('\n')}

STEPS
1. In IC, for each assignment and section above, open the assignment named like it (by name, not week number; (F) or (S) as listed) so its score panel shows, and click "Hub helper" at the bottom right. Check that its "Hub list" is this assignment and its column is right (fix them in its panel if not), note its counts and every row it leaves with the reason, and don't tick its box yet. If an assignment isn't in IC, list it; don't create it.
2. Show me one list: for each assignment, section and column, the counts, each raise old → new, and each row left with the reason. Then stop and wait for my OK. I may approve only some.
3. After my OK: for each approved one, open its score panel, click "Hub helper", tick its box and click "Enter … and Save". Wait until it says "Saved and checked after reloading". If it says it stopped or couldn't confirm, tell me exactly what it says and don't try again.
4. Tell me what was entered, raised and left. For students the helper lists as new students, tell me so I can mark them Exempt.`;
}

async function sendWeek(status, promptBox) {
  const {ready} = weekToEnter();
  status.textContent = ' Sending to the Chrome helper…';
  const failed = [];
  for (const {form, lesson} of ready) {
    const code = await sendList(form, lesson);
    if (code !== 'list_ready') failed.push(`${formName(form, lesson)}: ${helperError(code)}`);
  }
  if (failed.length) { status.textContent = ' ' + failed.join(' '); return; }
  const text = entryPrompt(ready);
  promptBox.replaceChildren(el('summary', {text: 'Show the prompt'}), el('pre', {class: 'prompt-text', text}));
  promptBox.hidden = false;
  try { await navigator.clipboard.writeText(text); status.textContent = ` Sent ${ready.length} to the helper and copied. Paste it into the Claude extension.`; }
  catch { status.textContent = ` Sent ${ready.length} to the helper. Couldn’t copy: open “Show the prompt” and copy it.`; }
}

function weekEntryBox() {
  if (helper.state !== 'ready') return null;
  const {ready, waiting} = weekToEnter();
  if (!ready.length && !waiting) return null;
  const status = el('span', {class: 'muted'});
  const promptBox = el('details', {hidden: true});
  return el('details', {class: 'roster-check manual'},
    el('summary', {text: 'If manual is needed: enter this week in IC now'}),
    el('p', {}, waiting ? 'Wait until every assignment below has finished scoring.'
      : `Sends ${ready.map(({form, lesson}) => `“${formName(form, lesson)}”`).join(' and ')} to the Chrome helper and copies a prompt. Paste it into the Claude extension: it opens each IC assignment, shows you what the helper will enter, and after your OK has the helper enter and check them.`),
    el('div', {class: 'action-row'},
      el('button', {type: 'button', class: 'primary', disabled: waiting || !ready.length, onclick: () => sendWeek(status, promptBox)},
        'Copy prompt for Claude extension (opens IC for the Chrome helper)'),
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
    helper.state === 'ready' ? `✓ The Chrome helper${helper.version ? ' ' + helper.version : ''} is installed in this browser.`
      : helper.state === 'helper_not_configured' ? 'The Chrome helper here isn’t set up for this Hub. Ask Claude Code for a new helper package.'
        : 'The Chrome helper isn’t installed in this browser. The Monday run needs it to enter scores: install it once on each computer (chrome://extensions → Developer mode → Load unpacked).');
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

function feedbackPanel(form) {
  const ui = work.ui[form.form_id] || {};
  const data = ui.feedback;
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Start feedback (use Teacher Claude)'}));
  if (ui.busy) { panel.append(el('p', {class: 'muted', text: ui.busy})); return panel; }
  if (ui.error) panel.append(note(ui.error));
  if (!data) return panel;
  if (!data.sheet?.configured) panel.append(note(data.sheet?.setup || '', 'info'));
  if (data.problem) panel.append(note(data.problem));
  const since = data.sheet?.feedback_at ? markDate(data.sheet.feedback_at) : '';
  const counts = [data.done_before ? `${data.done_before} turned in before the last feedback (${since})` : '',
    data.no_revision ? `${data.no_revision} have no revised answers yet` : ''].filter(Boolean).join(' · ');
  if (!data.students.length) {
    panel.append(note('Nobody new has revised answers to give feedback on.' + (counts ? ` (${counts}.)` : ''), 'info'));
    return panel;
  }
  const status = el('span', {class: 'muted'});
  panel.append(
    el('p', {}, `Feedback for ${data.students.length} student(s) with revised answers${since ? ` turned in since ${since}` : ''}${counts ? ` (${counts})` : ''}. Copy the prompt, paste it into Teacher Claude, and it will make one Google Doc with two pages per student for printing double-sided. Students are named by first name and last initial.`),
    el('ul', {class: 'files'}, data.students.map(s => el('li', {text: `${s.name} — ${s.section} — revised ${s.draft_done} of ${s.draft_required}`}))),
    el('div', {class: 'action-row'},
      data.prompt ? el('button', {type: 'button', class: 'primary', onclick: () => copyText(data.prompt, status)}, 'Copy prompt for Teacher Claude') : null,
      data.sheet?.configured ? el('button', {type: 'button', onclick: () => recordMark(form, 'feedback')},
        'Mark feedback done (click adds to Scores entered)') : null,
      data.prompt_url ? link('Edit the prompt in the Agenda ↗', data.prompt_url) : null, status),
    data.sheet?.configured ? el('p', {class: 'muted'}, 'Click “Mark feedback done” right after the feedback Doc is made. It adds a dated row to ',
      link(`“${data.sheet.name || 'Scores entered'}”`, data.sheet.url), '. Next time, only students who turn in something new after that are listed, so nobody’s feedback is printed twice.') : null,
    data.prompt ? el('details', {}, el('summary', {text: 'Show the prompt'}), el('pre', {class: 'for-claude', text: data.prompt})) : null);
  return panel;
}

function studentRow(row, scores) {
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
    el('td', {}, fileList(row.files), earlier), ...scoreCells(row, scores));
}

function renderForm(form, lesson) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const box = el('section', {class: 'form-work'},
    el('div', {class: 'form-head'}, el('h3', {text: form.label}), link('Open Form ↗', form.url, 'chip')));
  if (form.problem) box.append(note(form.problem));
  if (!form.form_id || (form.problem && !form.students.length)) return box;
  const matched = form.students.filter(r => r.matched && inSection(r));
  const unmatched = form.students.filter(r => !r.matched);
  const missing = form.missing.filter(inSection);
  const late = (form.late || []).filter(r => !r.matched || inSection(r));
  const lateMatched = late.filter(r => r.matched).length;
  box.append(el('p', {class: 'summary'},
    el('strong', {text: `${matched.length} of ${matched.length + lateMatched + missing.length}`}),
    form.window ? ' turned in on time' : ' turned in',
    late.length ? el('span', {class: 'badge warn', text: `${late.length} late — not graded`}) : null,
    unmatched.length ? el('span', {class: 'badge warn', text: `${unmatched.length} not matched to the roster`}) : null,
    form.folder_files ? el('span', {class: 'badge', text: `includes ${form.folder_files} file(s) found in the turn-in folder`}) : null,
    form.shared_files ? el('span', {class: 'badge', text: `includes ${form.shared_files} from the earlier shared Form`}) : null));
  if (form.window) {
    box.append(el('p', {class: 'muted', text: `Open ${form.window.opened} until ${form.window.label}: two weeks from the first turn-in, never starting before the lesson’s week. Anything later is Late and isn’t graded.`}));
  }
  if (form.elsewhere?.length) {
    // Another lesson's deck turned in through this Form (or left in this week's folder): not counted here.
    box.append(el('div', {class: 'note info'},
      el('strong', {text: `Not counted here (${form.elsewhere.length}): another lesson’s deck. `}),
      form.elsewhere.map((e, i) => [i ? '; ' : '', `${e.who}${e.section ? ` (${e.section})` : ''} — `,
        e.url ? link(e.file || 'file', e.url) : e.file,
        e.source === 'folder' ? ` is in this week’s folder but belongs to ${e.to}: move it to that week’s 📥 folder`
          : ` counts for ${e.to}`]).flat(),
      '.'));
  }
  const marks = marksLine(form);
  if (marks) box.append(marks);
  const standing = icStatus(form);
  if (standing) box.append(el('p', {class: 'ic-status ' + standing.kind, text: standing.text}));
  if (work.ui[form.form_id]?.saved) box.append(note(work.ui[form.form_id].saved, 'info'));
  const scores = work.scores[form.form_id];
  const ui = work.ui[form.form_id] || {};
  const scoredAll = scores && !ui.progress;
  box.append(...[
    ui.progress ? el('p', {class: 'muted', text: ui.progress}) : null,
    scoredAll ? el('details', {class: 'manual', open: Boolean(ui.open)},
      el('summary', {text: 'If manual is needed'}),
      el('div', {class: 'action-row'},
        el('button', {type: 'button', class: 'primary', onclick: () => openPanel(form, 'ic')},
          ui.open === 'ic' ? 'Hide IC scores' : 'Scores are ready to enter in IC'),
        el('button', {type: 'button', onclick: () => openPanel(form, 'feedback')},
          ui.open === 'feedback' ? 'Hide feedback' : 'Start feedback'),
        SCAN_CLASSES.includes(work.cls) ? el('button', {type: 'button', onclick: () => openPanel(form, 'answers')},
          ui.open === 'answers' ? 'Hide answers' : 'Answers by question') : null)) : null,
  ].filter(Boolean));
  if (scores) {
    box.append(el('p', {class: 'muted', text: 'Scores are out of 10: the share of this deck’s own coded boxes each student completed, so a deck can have any number of boxes. Notes counts the notes boxes and Concept Bank terms; Revision counts the revised answers.'}));
  }
  if (scores && !ui.progress) {
    const failed = Object.values(scores).filter(s => s.problem).length;
    if (failed) box.append(note(`${failed} deck(s) couldn’t be scored — see the note in their row.`, 'info'));
    const report = uncodedReport(scores);
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
    if (ui.open === 'feedback') box.append(feedbackPanel(form));
    if (ui.open === 'answers') box.append(answersPanel(form, scores));
  }
  const listed = [...matched, ...unmatched];
  if (listed.length) {
    box.append(el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work', ...(scores ? ['Notes /10', 'Revision /10'] : [])].map(h => el('th', {text: h})))),
      el('tbody', {}, listed.map(r => studentRow(r, scores))))));
  } else {
    box.append(note(late.length ? 'No on-time responses.' : 'No responses yet.', 'info'));
  }
  if (late.length) {
    box.append(el('div', {class: 'late'}, el('h4', {text: `Late — not graded (${late.length})`}),
      el('p', {class: 'muted', text: `Turned in after ${form.window?.label}. Tidy moves these into the week’s “⏰ Late — not graded” folder.`}),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work'].map(h => el('th', {text: h})))),
        el('tbody', {}, late.map(r => studentRow(r)))))));
  }
  const excused = (form.excused || []).filter(inSection);
  if (excused.length) {
    box.append(el('p', {class: 'muted'}, el('strong', {text: `Excused — new students (${excused.length}): `}),
      excused.map(m => `${m.last}, ${m.first} (added ${m.added})`).join('; '),
      '. They joined after this assignment began, so it isn’t counted for them.'));
  }
  if (missing.length) {
    box.append(el('div', {class: 'missing'}, el('h4', {text: closedFor(form) ? `Nothing turned in on time (${missing.length}) — these are 0 now`
      : startedFor(form) ? `Not turned in yet (${missing.length}) — 0 until they turn it in` : `Not turned in yet (${missing.length})`}),
      el('ul', {}, missing.map(m => el('li', {}, `${m.last}, ${m.first}${section === 'all' ? ' · ' + m.section : ''}`,
        m.unsorted ? el('span', {class: 'badge warn', text: 'has a file in Not sorted'}) : null)))));
  }
  return box;
}

function renderWork() {
  const data = work.data;
  if (!data) return;
  const parts = data.problems.map(p => note(p));
  if (!data.roster_size) parts.push(note('No roster students were found for this class, so nobody can be listed as missing.'));
  if (data.folders?.length) {
    parts.push(el('p', {class: 'muted'}, `Read the Form uploads and ${data.folder_files} file(s) in `,
      data.folders.flatMap((f, i) => [i ? ', ' : '', link(f.name + ' ↗', f.url)]), '.'));
  }
  if (data.folder_note) parts.push(note(data.folder_note, 'info'));
  if (data.roster_check) parts.push(renderRosterCheck(data.roster_check));
  const entry = weekEntryBox();
  if (entry) parts.push(entry);
  for (const lesson of data.lessons) {
    parts.push(el('div', {class: 'lesson-head'}, el('h2', {text: lesson.topic || data.week}), link('Edit in the Agenda ↗', lesson.edit_url, 'edit')));
    if (lesson.problem) parts.push(note(lesson.problem));
    lesson.forms.forEach(form => parts.push(renderForm(form, lesson)));
  }
  const other = (data.other_files || []).filter(r => !r.matched || work.section === 'all' || r.section === work.section);
  if (other.length) {
    parts.push(el('section', {class: 'form-work'},
      el('div', {class: 'form-head'}, el('h3', {text: 'Not sorted to an assignment'})),
      note('These files are in the lesson’s turn-in folders, but their names don’t say which assignment they are. Open each one to check.', 'info'),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work'].map(h => el('th', {text: h})))),
        el('tbody', {}, other.map(r => studentRow(r)))))));
  }
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
        for (const r of [...form.missing, ...form.late.filter(r => r.matched)].filter(inSection)) {
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
    el('p', {}, `Each missing assignment counts as 0. If you turn it in before its two weeks close, your score replaces the 0. After that it stays 0.`),
    open.length ? el('h3', {text: `Still open — turn these in (${open.length})`}) : null,
    open.length ? el('table', {class: 'sheet-table'},
      el('thead', {}, el('tr', {}, ['Assignment', 'Closes', 'I will turn it in by'].map(h => el('th', {text: h})))),
      el('tbody', {}, open.map(m => el('tr', {}, el('td', {text: `${m.week} · ${m.title}`}),
        el('td', {text: `${shortDate(m.close)}, 11:59 PM`}), el('td', {class: 'blank-line'}))))) : null,
    closed.length ? el('h3', {text: `Closed — these stay 0 (${closed.length})`}) : null,
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

// ---------- Routine: what runs on its own, and what you do ----------
// The Monday prompt: pasted into the Claude extension in the teacher's own
// Chrome (signed in to IC and the Hub). It prepares, waits for her OK, then
// saves. Nothing here runs it on a timer.
const mondayPrompt = () => `Please do my "Monday grades and roster" check now, in this Chrome window. I'm here and will review before anything is saved.

GROUND RULES
- This prompt replaces anything you remember about this routine from earlier weeks. Where your memory differs, follow this prompt.
- If Infinite Campus (IC) or the Teaching Hub shows a sign-in page, stop and tell me. Never type a password or choose a sign-in for me.
- Nothing is saved in IC or in my roster sheet until I say OK. Prepare everything, show me one review list, and wait.
- Never type scores into IC yourself. The Chrome helper ("Hub helper" in IC) enters them: it keeps the higher score (fills blanks and raises lower scores, never lowers one), leaves IC flags alone, and after Save reloads IC and checks every score. If the Hub has no "Send to the Chrome helper" button, the helper isn't installed in this Chrome: stop and tell me.
- Never delete anything, never create an IC assignment, and never change a due date that isn't on the list I approve.
- Never type in the "Scores entered (On Dashboard)" sheet. The Hub adds its rows itself.
- My sections: A2 Forensics and A3 Forensics (class: Forensics), A4 Biology (Biology), B3 A&P (A&P).

PART A — ROSTER CHECK (read only)
1. In IC, list each section's students as Last, First.
2. Open my Google Sheet named "Rosters" (one tab per section). For each section list: students in IC but not in the tab, students in the tab but not in IC, and names spelled differently.
3. In the Teaching Hub (${location.origin}/v2/#work), open Student work for each class and note anything its "Roster check" box flags.

PART B — SCORES (read only)
4. Check today's week and the three weeks before it in each class, with Section set to "All sections". Only work on assignments whose two weeks are still open, or closed within the last 8 days.
5. For each of those assignments, wait until "Scoring N of M…" is gone and read its line that starts with "IC:". Skip it if it says "IC: final scores entered …" or has no "IC:" line. Otherwise click "Scores are ready to enter in IC", then "Send to the Chrome helper".
6. In IC, find that assignment by its name, not its week number. For each section, open its (F) assignment (Notes) and its (S) assignment (Revision) so the score panel shows, and click "Hub helper" at the bottom right. Check the helper picked the right Hub lesson and column (fix them in its panel if not), but don't tick its box yet. Note its counts (to enter, raises, kept, left) and every row it leaves, with the reason. If an assignment isn't in IC at all, list it; don't create it. Also compare IC's due date with the Hub's "open until" or "closed" date and list any difference, old → new.

PART C — REVIEW (STOP)
7. Show me one list: roster additions, students who may have left, and spelling differences; for each section and assignment, the helper's counts, each raise old → new, and each row it leaves with the reason; and each due-date change. Then stop and wait for my OK. I may approve only some items.

PART D — SAVE WHAT I APPROVE
8. Roster: add only the students I approve, as new rows in the same format, with today's date (MM/DD/YYYY) in the tab's "Added" column. If the tab has no "Added" column, type Added in the first empty cell of row 1 to the right of the names. Don't delete rows or change other cells.
9. IC: for each approved assignment and section, open its score panel, click "Hub helper", tick its box and click "Enter … and Save". Wait for it to say it saved and checked the scores. If it says it stopped or couldn't confirm, tell me exactly what it says and don't try again. For students it lists as new students, mark the assignment Exempt in IC. Enter approved due dates and click Save.
10. The Hub notes "Entered in IC" by itself once the helper has done every section and column of an assignment. If an assignment doesn't show "✓ Entered in IC" afterward, tell me which.

PART E — LOG
11. Make a Google Doc named "Monday run — ${todayISO()}" at the top of 00 REAL ALL TURN IN with the roster changes made, what the helper entered, raised and left per section and assignment, anything that didn't read back, and a short note on anything confusing or slow.`;

const ROUTINE = {
  // How a completion score gets from a student's deck into IC, in order.
  scoring: [
    {when: 'Any day, any time', what: 'Students turn in their deck through the lesson’s Turn-in Form.'},
    {when: '7:00 a.m. weekdays', what: 'The Chrome helper scores every class’s open assignments and enters them in IC, with a 0 for work not turned in. It keeps the higher score and never lowers one.'},
    {when: 'Two weeks after the first turn-in', what: 'The assignment closes. Late work isn’t graded.'},
  ],
  // What runs without you. "needs" is what has to be open for it to run.
  scheduled: [
    {when: '6:30 a.m. weekdays', who: 'School ChatGPT (Codex)',
     what: 'Checks rosters and tidies verified student work into each lesson’s turn-in folder before 7:00.',
     needs: 'Nothing to click.'},
    {when: '7:00 a.m. weekdays', who: 'Chrome helper',
     what: 'Enters eligible scores in IC and checks them. Roster additions and removals still need review.',
     needs: 'The Mac awake, school Chrome open with the Hub and IC tabs signed in, and the helper’s morning setting on.'},
    {when: '7:30 a.m. weekdays', who: 'School ChatGPT (Codex)',
     what: 'Writes feedback for the latest closed lesson, saves it privately and adds it to the feedback checklist. Then reads the class growth summaries and writes a one-page Lesson opener: what students are tending to miss, plus information and questions to bring up at the start of the next lesson.',
     needs: 'Nothing to click.'},
  ],
  // Start-up: what to open each weekday morning, in order.
  startup: [
    'Mac on, plugged in, and set not to sleep before 7:00.',
    'Chrome → the SCHOOL profile (katherine.vonduyke@redclay.k12.de.us). Not your home profile.',
    'Tab 1: the Teaching Hub, signed in.',
    'Tab 2: Infinite Campus (through ClassLink), signed in. Any IC page is fine.',
    'Click the IC tab last, so it is the tab in front, and leave that Chrome window on top. Don’t minimize it.',
    'Open the ChatGPT app on a school account (AIHS or the teacher account).',
    'Leave everything open. Don’t close Chrome or log out of IC before 8:00.',
  ],
  // What the teacher does (you) and what that gets done (done). One line each.
  mine: [
    {when: 'Weekdays, before school', you: 'Do the start-up list above.',
     done: 'The 6:30, 7:00 and 7:30 runs have what they need.'},
    {when: 'At school, when a new lesson starts', you: 'Open your class tab in the feedback checklist. Print the latest Made entries with unchecked Printed and Handed out boxes, then tick each box as you finish that step.',
     done: 'Each student gets two pages of feedback, and the checklist shows who has it.'},
    {when: 'At school, before the next lesson', you: 'Open the Lesson opener in the private course folder. Start class with its information and questions.',
     done: 'The class goes back over what most students missed, before new material.'},
    {when: 'At school', you: 'Keep the Agenda up to date.',
     done: 'The Hub finds each lesson’s deck and Turn-in Form.'},
    {when: 'When “Done for you” shows something held', you: 'Read the reason. Most need a fix, not grading. If unsure, ask Claude.',
     done: 'Nothing is skipped without you knowing.'},
  ],
  // Lesson planning, usually before Friday or on Sunday night.
  planning: [
    {when: 'Planning a week', you: 'Lesson plans → Edit here: topic, day plans, the deck and its Turn-in Form → Save to Agenda.',
     done: 'The lesson shows on the dashboard, and the Hub can find and score its work.'},
    {when: 'Right after you update a deck', you: 'Copy embed-coding prompt (below) → paste it into Claude with the deck attached.',
     done: 'Every answer box has a code, so the Hub can score it.'},
    {when: 'After you move or add a lesson', you: 'Click Check links (below). Schoology needs nothing: students find lessons by folder name.',
     done: 'Students’ links open the right Form and deck.'},
  ],
  // Only when an automatic run didn't do its job. Where to go, what to click.
  manual: [
    {need: 'Enter scores now, without waiting for 7:00',
     steps: 'Hub tab → Student work → pick class and week → under the assignment, open “If manual is needed” → “Scores are ready to enter in IC” → “Send to the Chrome helper”. Then IC tab, in front → that class → Curriculum → click the assignment’s TITLE (not the “Scored” number) → Score → green “Hub helper” at the bottom right → tick its box → Enter and Save. Do the (F) Notes assignment and the Revision assignment, in each section.'},
    {need: 'Let Claude do the score entry instead',
     steps: 'Copy the Monday prompt below → Chrome, IC tab in front → Claude extension side panel → paste → review its list → reply OK.'},
    {need: 'You typed scores into IC yourself',
     steps: 'Hub tab → Student work → class and week → “I entered these in IC”. Skip this if the helper did it; the Hub notes it by itself.'},
    {need: 'IC said a save was not confirmed',
     steps: 'Check that assignment in IC. Then Chrome toolbar → puzzle-piece icon → School Workflow Hub → tick the recovery box → “Allow future mornings”.'},
    {need: 'Turn the 7:00 run on or off',
     steps: 'Chrome toolbar → puzzle-piece icon → School Workflow Hub → “Enter automatically on weekday mornings”.'},
    {need: 'Feedback by hand',
     steps: 'Hub tab → Student work → class and week → under the assignment, open “If manual is needed” → Start feedback → Copy prompt → paste into ChatGPT (school account). Then “Mark feedback done”.'},
    {need: 'Files are in the wrong folder',
     steps: 'Hub tab → Student work → class and week → top “If manual is needed” → “Tidy this week’s folders” → Move and rename. Once per class.'},
    {need: 'Reminder sheets or reading answers',
     steps: 'Hub tab → Student work → open “If manual is needed”: “Missing-work sheets” at the top, “Answers by question” under each assignment.'},
  ],
};

const routine = {seq: 0};

function routineStatus(key, data) {
  const found = data?.[key];
  if (found?.problem) return note(found.problem);
  if (key === 'monday_run') {
    if (!found) return el('p', {class: 'muted', text: 'No “Monday run” Doc yet.'});
    const days = (Date.now() - new Date(found.at)) / 86400000;
    return el('p', {class: days > 8 ? 'note warn' : 'muted'}, `Last run: ${when(found.at)}. `, link(found.name + ' ↗', found.url),
      days > 8 ? ' That’s more than a week ago: run the Monday prompt.' : '');
  }
  return null;
}

function renderRoutine(data) {
  const body = $('routine-body');
  const startup = el('section', {class: 'routine'}, el('h2', {text: 'Every weekday morning: open these'}),
    el('ol', {}, ROUTINE.startup.map(s => el('li', {text: s}))),
    helperLine());
  const mine = el('section', {class: 'routine'}, el('h2', {text: 'Your routine'}),
    el('div', {class: 'table-wrap'}, el('table', {class: 'routine-table'},
      el('thead', {}, el('tr', {}, ['When', 'You do', 'Gets done'].map(h => el('th', {text: h})))),
      el('tbody', {}, ROUTINE.mine.map(t => el('tr', {}, el('td', {class: 'routine-when', text: t.when}),
        el('td', {text: t.you}), el('td', {text: t.done})))))));
  const scheduled = el('section', {class: 'routine routine-scheduled'}, el('h2', {text: 'Done for you'}),
    ROUTINE.scheduled.map(t => el('div', {class: 'routine-item'},
      el('p', {class: 'routine-when'}, el('strong', {text: t.when}), ` · ${t.who}`),
      el('p', {text: t.what}),
      el('p', {class: 'routine-needs'}, el('strong', {text: 'Needs: '}), t.needs),
      t.key ? routineStatus(t.key, data) : null)));
  const copied = el('span', {class: 'muted'});
  const manual = el('details', {class: 'routine', open: store.get('routine-manual-open') === '1',
    ontoggle: e => store.set('routine-manual-open', e.target.open ? '1' : '0')},
    el('summary', {}, el('h2', {text: 'If manual is needed'})),
    el('p', {class: 'muted', text: 'Only when a morning run didn’t do its job. Everything here is in the school Chrome profile.'}),
    el('div', {class: 'table-wrap'}, el('table', {class: 'routine-table'},
      el('thead', {}, el('tr', {}, ['When you need to', 'Where and what to click'].map(h => el('th', {text: h})))),
      el('tbody', {}, ROUTINE.manual.map(m => el('tr', {}, el('td', {class: 'routine-when', text: m.need}),
        el('td', {text: m.steps})))))),
    el('div', {class: 'action-row'},
      el('button', {type: 'button', onclick: () => copyText(mondayPrompt(), copied)}, 'Copy Monday prompt (Claude extension)'),
      copied),
    el('details', {}, el('summary', {text: 'Show the prompt'}), el('pre', {class: 'prompt-text', text: mondayPrompt()})),
    data ? routineStatus('monday_run', data) : null);
  const embed = data?.embed_prompt;
  const embedCopied = el('span', {class: 'muted'});
  const planning = el('section', {class: 'routine'}, el('h2', {text: 'Lesson planning (before Friday, or Sunday night)'}),
    el('div', {class: 'table-wrap'}, el('table', {class: 'routine-table'},
      el('thead', {}, el('tr', {}, ['When', 'You do', 'Gets done'].map(h => el('th', {text: h})))),
      el('tbody', {}, ROUTINE.planning.map(t => el('tr', {}, el('td', {class: 'routine-when', text: t.when}),
        el('td', {text: t.you}), el('td', {text: t.done})))))),
    embed?.text ? el('div', {class: 'action-row'},
      el('button', {type: 'button', class: 'primary', onclick: () => copyText(embed.text, embedCopied)}, 'Copy embed-coding prompt'),
      link('Edit it in the Agenda ↗', embed.url), embedCopied) : null,
    embed?.problem ? note(embed.problem, 'info') : null,
    embed?.text ? el('details', {}, el('summary', {text: 'Show the prompt'}), el('pre', {class: 'prompt-text', text: embed.text})) : null);
  const sheet = data?.scores_entered;
  const sheets = el('section', {class: 'routine'}, el('h2', {text: 'Where the records are'}),
    el('ul', {class: 'files'},
      el('li', {}, sheet && !sheet.problem
        ? [link(`“${sheet.name}” ↗`, sheet.url), ` — a row for each IC entry and feedback round${sheet.last ? ` (last: ${sheet.last})` : ''}.`]
        : sheet?.problem ? sheet.problem : 'No “Scores entered” sheet yet: make one at the top of 00 REAL ALL TURN IN.'),
      el('li', {}, 'Feedback, growth records, class summaries and the feedback checklist: in My Drive → Student Feedback — Private 2026–27.')));
  body.replaceChildren(startup, mine, scheduled, manual, planning, sheets);
}

// ---------- Link check ----------
// Problems first, one line each; Forms shared by weeks and decks the Hub
// can't open are summed up underneath, since they're usually fine.
const QUIET = new Set(['shared-form', 'deck-unreadable']);

function linkCheckResult(name, data) {
  const problems = data.flags.filter(f => !QUIET.has(f.kind));
  const shared = data.flags.filter(f => f.kind === 'shared-form');
  const decks = data.flags.filter(f => f.kind === 'deck-unreadable');
  const releases = data.rows.filter(r => r.release);
  const notReady = releases.filter(r => r.release.status !== 'READY');
  const releaseText = release => {
    if (!release) return '—';
    if (release.status === 'READY') return `READY · revision ${release.revision || 'verified'}`;
    const first = (release.issues || [])[0];
    return `${release.status}${first ? ' · ' + first.replaceAll('_', ' ') : ''}`;
  };
  return [
    data.problem ? note(data.problem) : null,
    problems.length
      ? el('ul', {class: 'link-flags'}, problems.map(f => el('li', {}, el('strong', {text: `${f.week} · ${f.topic}: `}), f.message)))
      : el('p', {class: 'ok', text: `✓ Every ${name} lesson links a Turn-in Form for its own week.`}),
    releases.length ? (notReady.length
      ? el('p', {class: 'muted', text: `Deck release gate: ${releases.length - notReady.length} READY · ${notReady.length} needs attention in the current/upcoming window.`})
      : el('p', {class: 'ok', text: `✓ Current/upcoming ${name} decks passed the deterministic content/code gate.`})) : null,
    shared.length ? el('details', {class: 'muted'},
      el('summary', {text: `${shared.length} Form${shared.length === 1 ? ' is' : 's are'} used for more than one week (fine when a lesson spans those weeks)`}),
      el('ul', {class: 'link-flags'}, shared.map(f => el('li', {}, el('strong', {text: `${f.week}: `}), f.message)))) : null,
    decks.length ? el('p', {class: 'muted', text: `${decks.length} deck link${decks.length === 1 ? '' : 's'} couldn’t be checked: the Hub can’t open ${decks.length === 1 ? 'it' : 'them'}.`}) : null,
    el('details', {}, el('summary', {text: `See every ${name} lesson’s Forms and decks`}),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Week', 'Topic', 'Turn-in Forms', 'Decks', 'Release'].map(h => el('th', {text: h})))),
        el('tbody', {}, data.rows.map(r => el('tr', {}, el('td', {text: r.week}), el('td', {text: r.topic}),
          el('td', {text: r.forms.filter(f => f.where !== '_turnInURL').map(f => f.student_title || f.title || f.label).join(' · ') || '—'}),
          el('td', {text: r.decks.map(d => d.title || d.label).join(' · ') || '—'}),
          el('td', {text: releaseText(r.release)})))))))];
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

async function loadRoutine() {
  const seq = ++routine.seq;
  renderRoutine(null);
  setStatus('routine-status', 'Checking what has run…');
  try {
    const data = await getJSON('api/routine');
    if (seq !== routine.seq) return;
    renderRoutine(data);
    setStatus('routine-status', '');
  } catch (error) {
    if (seq === routine.seq) setStatus('routine-status', error.message, true);
  }
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
$('reload-today').addEventListener('click', () => loadToday(true));
$('reload-routine').addEventListener('click', loadRoutine);
$('run-link-check').addEventListener('click', runLinkCheck);
$('reload-work').addEventListener('click', async () => {
  try { work.weeksFor = null; await loadWeeks(true); await loadWork(); } catch (error) { setStatus('work-status', error.message, true); }
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
  try {
    const data = await getJSON('api/morning/reports');
    const reports = data.reports || [];
    morningReports = reports; morningRecovery = data.recovery || []; renderMorningAlert();
    box.replaceChildren(el('h2', {text: 'Morning score entry'}));
    if (!reports.length) { box.append(note('No morning run recorded yet. Enable weekday mornings in the Chrome helper on your home Mac after its assignment mappings are verified.', 'info')); return; }
    const words = value => String(value).replaceAll('_', ' ');
    box.append(note('Roster additions and removals still need review; automatic roster sync is not available.', 'info'));
    reports.forEach((report, i) => {
      const lines = el('ul', {}, report.items.map(row => el('li', {}, el('strong', {text: row.assignment + ': '}),
        `${row.filled} entered, ${row.raised} raised, ${row.kept} kept, ${row.left} left — ${words(row.code)}. `,
        Object.entries(row.reasons).filter(([, n]) => n).map(([reason, n]) => `${n} ${words(reason)}`).join('; '))));
      const details = morningRecovery.filter(r => r.run_id === report.run_id).map(r =>
        r.type === 'verified' ? el('p', {text: `Manually verified by ${r.by} at ${new Date(r.at).toLocaleString()}: ${r.note}`})
          : el('p', {text: `${r.assignment} · ${r.section} · ${words(r.column)} — students requiring readback: ${r.names.length ? r.names.join('; ') : 'details unavailable; check the full panel'}.`}));
      box.append(el('details', {open: i === 0}, el('summary', {text: `${report.day}: ${words(report.code)}`}), lines, ...details));
    });
  } catch { box.replaceChildren(el('h2', {text: 'Morning score entry'}), note('The morning summary could not be read. No successful run is confirmed.')); }
}
loadMorningSummary();
$('reload-routine')?.addEventListener('click', loadMorningSummary);
