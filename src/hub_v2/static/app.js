// Teaching Hub v2: lesson plans from the Agenda, and student work found from
// each lesson's Form responses and turn-in folder. Read-only; the grade sheet
// downloads to you.
const $ = id => document.getElementById(id);
const CLASSES = [['biology', 'Biology'], ['forensics', 'Forensics'], ['anatomy', 'A&P']];
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

// ---------- Lesson plans ----------
const plans = {date: todayISO(), seq: 0, started: false};

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
  if (lesson.forms.length) {
    box.append(el('div', {class: 'forms'}, el('p', {class: 'label', text: 'Turn-in Forms'}),
      lesson.forms.map(form => el('div', {class: 'form-row'},
        link(form.label + ' ↗', form.url),
        form.form_id ? el('a', {class: 'see-work', href: `#work/${cls.key}/${encodeURIComponent(lesson.week)}`}, 'See student work →') : null,
        form.problem ? note(form.problem) : null))));
  }
  box.append(el('details', {class: 'week'}, el('summary', {text: 'Whole week'}),
    lesson.plans.map(p => el('div', {class: 'week-day'}, el('strong', {text: p.day}),
      el('p', {class: 'plan', text: p.plan || '—'}), p.note ? el('p', {class: 'teacher-note', text: p.note}) : null))));
  box.append(link('Edit this row in the Agenda ↗', lesson.edit_url, 'edit'));
  return box;
}

function renderPlans(data) {
  $('week-label').textContent = data.week ? `${data.week.label} · ${weekRange(data.week.monday)}` : 'No Agenda week for this date';
  $('week-closures').textContent = data.week?.closures || '';
  const cards = data.classes.map(cls => {
    const card = el('article', {class: 'card ' + cls.key}, el('h2', {text: cls.name}));
    if (cls.problem) card.append(note(cls.problem));
    cls.lessons.forEach(lesson => card.append(renderLesson(cls, lesson, data.weekday)));
    return card;
  });
  $('cards').replaceChildren(...data.problems.map(p => note(p)), ...cards);
}

// ---------- Student work ----------
const work = {cls: store.get('class') || 'forensics', week: null, section: store.get('section') || 'all', weeksFor: null, data: null, seq: 0};

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
  if (!work.week) { setStatus('work-status', 'No weeks were found for this class in the Agenda.', true); return; }
  history.replaceState(null, '', `#work/${work.cls}/${encodeURIComponent(work.week)}`);
  const seq = ++work.seq;
  setStatus('work-status', 'Finding student work from the Form responses…');
  $('work-body').replaceChildren();
  try {
    const data = await getJSON(`api/work?class=${work.cls}&week=${encodeURIComponent(work.week)}${fresh ? '&fresh=1' : ''}`);
    if (seq !== work.seq) return;
    work.data = data;
    renderWork();
    setStatus('work-status', `Read ${when(new Date().toISOString())}.`);
  } catch (error) {
    if (seq === work.seq) setStatus('work-status', error.message, true);
  }
}

function fileList(files) {
  if (!files.length) return el('span', {class: 'muted', text: 'Form sent with no file'});
  return el('ul', {class: 'files'}, files.map(f => el('li', {},
    link(f.name, f.url),
    f.from_folder ? el('span', {class: 'badge', title: 'In the week’s turn-in folder but not in this Form’s responses', text: 'From turn-in folder'}) : null,
    f.from_shared ? el('span', {class: 'badge', title: 'Turned in through the earlier shared Form', text: 'Earlier shared Form'}) : null,
    f.other_week ? el('span', {class: 'badge warn', title: 'The file name mentions a different week', text: `Named Week ${f.other_week}`}) : null,
    f.trashed ? el('span', {class: 'badge warn', text: 'In Drive trash'}) : null,
    f.problem ? el('span', {class: 'badge', title: f.problem, text: 'Open it yourself'}) : null)));
}

function sheetNotes(row) {
  return [row.source === 'folder' ? 'From turn-in folder' : '',
    ...row.files.filter(f => f.other_week).map(f => `Named Week ${f.other_week}`),
    row.earlier.length ? `${row.earlier.length} earlier file(s)` : ''].filter(Boolean).join('; ');
}

function studentRow(row) {
  const who = row.matched
    ? el('td', {}, el('strong', {text: `${row.last}, ${row.first}`}),
      row.matched_by && row.matched_by !== 'email' ? el('small', {class: 'muted', text: ` matched by ${row.matched_by}`}) : null)
    : el('td', {}, el('span', {class: 'badge warn', text: 'Not on roster'}), ' ', row.email || 'No email collected',
      row.typed.length ? el('small', {class: 'muted', text: ' · typed: ' + row.typed.join(' / ')}) : null);
  const earlier = row.earlier.length
    ? el('details', {class: 'earlier'}, el('summary', {text: `${row.earlier.length} earlier`}),
      row.earlier.map(e => el('div', {}, el('small', {class: 'muted', text: when(e.submitted_at)}), fileList(e.files))))
    : null;
  return el('tr', {class: row.matched ? '' : 'unmatched'}, who, el('td', {text: row.section || '—'}),
    el('td', {text: when(row.submitted_at)}),
    el('td', {}, fileList(row.files), earlier));
}

function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function downloadSheet(form, lesson) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const rows = [
    ...form.students.filter(r => r.matched && inSection(r)).map(r => [r.last, r.first, r.section, 'Yes', when(r.submitted_at), '', r.files[0]?.url || '', sheetNotes(r)]),
    ...form.missing.filter(inSection).map(m => [m.last, m.first, m.section, 'No', '', '', '', m.unsorted ? 'Has a file in "Not sorted" — check it' : '']),
  ].sort((a, b) => (a[2] + a[0] + a[1]).localeCompare(b[2] + b[0] + b[1]));
  const unmatched = form.students.filter(r => !r.matched).map(r => ['', '', '', 'Yes', when(r.submitted_at), '', r.files[0]?.url || '', 'Not matched to the roster: ' + (r.email || r.files[0]?.name || 'unknown')]);
  const lines = [['Last name', 'First name', 'Section', 'Turned in', 'Turned in at', 'Score', 'Work link', 'Notes'], ...rows, ...unmatched];
  const blob = new Blob(['﻿' + lines.map(line => line.map(csvCell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'});
  const name = [CLASSES.find(([k]) => k === work.cls)[1], work.week, form.label, section === 'all' ? 'all sections' : section]
    .join(' - ').replace(/[\\/:*?"<>|]+/g, ' ').slice(0, 120) + '.csv';
  const a = el('a', {href: URL.createObjectURL(blob), download: name});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
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
  box.append(el('p', {class: 'summary'},
    el('strong', {text: `${matched.length} of ${matched.length + missing.length}`}), ' turned in',
    unmatched.length ? el('span', {class: 'badge warn', text: `${unmatched.length} not matched to the roster`}) : null,
    form.folder_files ? el('span', {class: 'badge', text: `includes ${form.folder_files} file(s) found in the turn-in folder`}) : null,
    form.shared_files ? el('span', {class: 'badge', text: `includes ${form.shared_files} from the earlier shared Form`}) : null,
    el('button', {type: 'button', class: 'primary', onclick: () => downloadSheet(form, lesson)}, 'Download grade sheet (CSV)')));
  const shown = [...matched, ...unmatched];
  if (shown.length) {
    box.append(el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work'].map(h => el('th', {text: h})))),
      el('tbody', {}, shown.map(studentRow)))));
  } else {
    box.append(note('No responses yet.', 'info'));
  }
  if (missing.length) {
    box.append(el('div', {class: 'missing'}, el('h4', {text: `Not turned in yet (${missing.length})`}),
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
        el('tbody', {}, other.map(studentRow))))));
  }
  $('work-body').replaceChildren(...parts);
}

// ---------- Page wiring ----------
function showTab() {
  const [tab, ...rest] = location.hash.slice(1).split('/');
  const current = tab === 'work' ? 'work' : 'plans';
  for (const name of ['plans', 'work']) $('page-' + name).hidden = name !== current;
  document.querySelectorAll('.tabs a').forEach(a => {
    if (a.dataset.tab === current) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (current === 'work') openWork(rest.map(part => decodeURIComponent(part)));
  else if (!plans.started) loadPlans(plans.date);
}

fillSelect($('work-class'), CLASSES, work.cls);
$('prev-week').addEventListener('click', () => loadPlans(addDays(plans.date, -7)));
$('next-week').addEventListener('click', () => loadPlans(addDays(plans.date, 7)));
$('today').addEventListener('click', () => loadPlans(todayISO()));
$('reload-plans').addEventListener('click', () => loadPlans(plans.date, true));
$('date').addEventListener('change', e => { if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) loadPlans(e.target.value); });
$('work-class').addEventListener('change', e => { work.cls = e.target.value; store.set('class', work.cls); work.weeksFor = null; openWork(); });
$('work-week').addEventListener('change', e => { work.week = e.target.value; loadWork(); });
$('work-section').addEventListener('change', e => { work.section = e.target.value; store.set('section', work.section); renderWork(); });
$('reload-work').addEventListener('click', async () => {
  try { work.weeksFor = null; await loadWeeks(true); await loadWork(); } catch (error) { setStatus('work-status', error.message, true); }
});
window.addEventListener('hashchange', showTab);
showTab();
