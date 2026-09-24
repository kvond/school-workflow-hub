// Teaching Hub v2: lesson plans from the Agenda, and student work found from
// each lesson's Form responses and turn-in folder, with completion scores
// counted from each deck's coded boxes. The grade sheet downloads to you;
// scores go into Infinite Campus by you or the Claude extension, never by this page.
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
    link(f.name, f.url),
    f.from_folder ? el('span', {class: 'badge', title: 'In the week’s turn-in folder but not in this Form’s responses', text: 'From turn-in folder'}) : null,
    f.from_shared ? el('span', {class: 'badge', title: 'Turned in through the earlier shared Form', text: 'Earlier shared Form'}) : null,
    f.other_week ? el('span', {class: 'badge warn', title: 'The file name mentions a different week', text: `Named Week ${f.other_week}`}) : null,
    f.late ? el('span', {class: 'badge warn', title: 'Turned in more than two weeks after this week — not graded', text: 'Late'}) : null,
    f.trashed ? el('span', {class: 'badge warn', text: 'In Drive trash'}) : null,
    f.problem ? el('span', {class: 'badge', title: f.problem, text: 'Open it yourself'}) : null)));
}

function sheetNotes(row) {
  return [row.source === 'folder' ? 'From turn-in folder' : '',
    ...row.files.filter(f => f.other_week).map(f => `Named Week ${f.other_week}`),
    row.earlier.some(e => e.late) && !row.late ? 'A later late version was not graded' : '',
    row.earlier.length ? `${row.earlier.length} earlier file(s)` : ''].filter(Boolean).join('; ');
}

// ---------- Completion scores (Notes and Revision, out of 10) ----------
const DECKS = ['application/vnd.google-apps.presentation', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'];
const scoreFile = row => row.files.find(f => DECKS.includes(f.mime)) || row.files[0];
const fmt = value => value == null ? 'N/A' : String(value);
// While an assignment's two weeks are open an empty result stays blank; once
// they close it is 0. Mirrors ledger.shown_scores on the server.
const closedFor = form => Boolean(form.window && new Date(form.window.at) < new Date());
const shown = (score, closed) => !score ? (closed ? ['0', '0'] : ['', ''])
  : [score.notes_score, score.revision_score].map(v => v === 0 && !closed ? '' : fmt(v));
const STATUS = {blank: 'blank', copied: 'looks copied from the slide', 'not found': 'its code isn’t in this copy'};

function scoreCells(row, scores, closed) {
  if (!scores) return [];
  const result = scores[scoreFile(row)?.id];
  if (!row.files.length) return [el('td', {text: '—'}), el('td', {text: '—'})];
  if (!result) return [el('td', {class: 'muted', text: '…'}), el('td', {class: 'muted', text: '…'})];
  if (result.problem) return [el('td', {colspan: 2}, el('small', {class: 'muted', text: result.problem}))];
  const [notesShown, revisionShown] = shown(result, closed);
  const c = result.counts;
  // Each score is the share of this deck's own coded boxes that were done, on a 10-point scale.
  const cell = (value, done, required, what) => value === ''
    ? el('span', {class: 'muted', title: 'Blank until the two weeks are over', text: '—'})
    : el('span', {}, el('strong', {text: value}),
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
  if (!response.ok) throw new Error(data?.error || `The server returned an error (${response.status}).`);
  return data;
}

async function openPanel(form, which) {
  const ui = work.ui[form.form_id] = {...work.ui[form.form_id]};
  if (ui.open === which) { ui.open = null; renderWork(); return; }
  ui.open = which; ui.error = null; ui.busy = which === 'ic' ? null : 'Finding students with revised answers…';
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
    const done = await postJSON('api/record', {class: work.cls, week: work.week, form: form.form_id, kind});
    work.marks[form.form_id] = done.sheet;
    ui.saved = kind === 'ic' ? 'Noted: this assignment is entered in IC.' : 'Noted: feedback made for this assignment.';
    ui.open = null; ui.busy = null;
  } catch (error) { ui.error = error.message; ui.busy = null; }
  renderWork();
}

async function copyText(textValue, status) {
  try { await navigator.clipboard.writeText(textValue); status.textContent = ' Copied.'; }
  catch { status.textContent = ' Couldn’t copy; select the text below instead.'; }
}

const markDate = mark => mark ? when(mark.at) : '';

function marksLine(form) {
  const sheet = work.marks[form.form_id];
  if (!sheet?.configured) return null;
  const parts = [sheet.entered_at ? `✓ Entered in IC ${markDate(sheet.entered_at)}` : '',
    sheet.feedback_at ? `✓ Feedback made ${markDate(sheet.feedback_at)}` : ''].filter(Boolean);
  return parts.length ? el('p', {class: 'marks', text: parts.join(' · ')}) : null;
}

function icPanel(form, lesson, scores) {
  const section = work.section;
  const cls = CLASSES.find(([k]) => k === work.cls)[1];
  const ui = work.ui[form.form_id] || {};
  const sheet = work.marks[form.form_id];
  const entered = sheet?.entered_at ? new Date(sheet.entered_at.at) : null;
  const closed = closedFor(form);
  const inSection = r => section === 'all' || r.section === section;
  const all = [
    ...form.students.filter(r => r.matched && inSection(r)).map(r => ({r, s: scores[scoreFile(r)?.id]}))
      .filter(x => x.s && !x.s.problem).map(x => ({...x, show: shown(x.s, closed)})),
    ...(closed ? [...form.missing, ...form.late.filter(r => r.matched)].filter(inSection)
      .map(r => ({r, s: null, zero: true, show: shown(null, true)})) : []),
  ].filter(x => x.show.some(v => v !== ''))
    .sort((a, b) => (a.r.section + a.r.last + a.r.first).localeCompare(b.r.section + b.r.last + b.r.first));
  // New since you last entered this assignment: turned in after that, or a 0
  // that appeared because the two weeks closed after it.
  for (const x of all) {
    x.fresh = !entered || (x.zero ? new Date(form.window.at) > entered : new Date(x.r.submitted_at) > entered);
  }
  const fresh = all.filter(x => x.fresh);
  const dash = v => v === '' ? '— (leave blank)' : v;
  const claudeText = list => [
    'Please enter these completion scores in Infinite Campus. I am signed in and have the grade book open in this Chrome window.',
    `Class: ${cls}${section === 'all' ? '' : ' ' + section} · ${work.week} · Assignment: ${form.label}${lesson.topic ? ` (${lesson.topic})` : ''}`,
    'Each score is out of 10. "Notes" goes in this assignment\'s notes/content score and "Revision" in its revision score.',
    'Before you save, show me each student, assignment and score you are about to enter, and wait for my OK.',
    'Do not change any other grade. If a student already has a different score for this assignment, it is an update from a resubmission: show me the old and new score and replace it only after I say OK. If a student or the assignment is not there, stop and ask me. "— (leave blank)" means leave it blank.',
    'After saving, reload the page and read the saved scores back to me.',
    '', 'Last, First — Section — Notes /10 — Revision /10',
    ...list.map(({r, show, zero}) => `${r.last}, ${r.first} — ${r.section} — Notes ${dash(show[0])} — Revision ${dash(show[1])}${zero ? ' (nothing turned in on time)' : ''}`)].join('\n');
  const status = el('span', {class: 'muted'});
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Enter these scores in Infinite Campus'}));
  if (ui.busy) { panel.append(el('p', {class: 'muted', text: ui.busy})); return panel; }
  if (ui.error) panel.append(note(ui.error));
  if (sheet && !sheet.configured) panel.append(note(sheet.setup, 'info'));
  if (!all.length) { panel.append(note('No scores to enter yet.', 'info')); return panel; }
  panel.append(el('p', {}, el('strong', {text: 'Open Infinite Campus first'}), entered
    ? ` and go to this assignment. You entered it on ${markDate(sheet.entered_at)}; ${fresh.length ? `the ${fresh.length} marked “new since” turned in after that.` : 'nobody has turned in anything new since.'} Type them in yourself, or copy them for the Claude extension, which enters them while you watch and asks before it saves.`
    : ' and go to this assignment in your grade book. Type the scores in yourself, or copy them for the Claude extension, which enters them while you watch and asks before it saves.'));
  panel.append(el('div', {class: 'action-row'},
    fresh.length ? el('button', {type: 'button', class: 'primary', onclick: () => copyText(claudeText(fresh), status)},
      entered ? `Copy for Claude (${fresh.length} new since)` : 'Copy for Claude') : null,
    entered ? el('button', {type: 'button', onclick: () => copyText(claudeText(all), status)}, 'Copy all for Claude') : null,
    sheet?.configured ? el('button', {type: 'button', onclick: () => recordMark(form, 'ic')}, 'I entered these in IC') : null,
    status));
  panel.append(el('div', {class: 'table-wrap'}, el('table', {},
    el('thead', {}, el('tr', {}, ['Student', 'Section', 'Notes /10', 'Revision /10', ''].map(h => el('th', {text: h})))),
    el('tbody', {}, all.map(({r, show, zero, fresh: isNew}) => el('tr', {}, el('td', {text: `${r.last}, ${r.first}`}), el('td', {text: r.section}),
      el('td', {text: show[0] || '—'}), el('td', {text: show[1] || '—'}),
      el('td', {}, zero ? el('span', {class: 'badge', text: 'nothing on time'}) : null,
        entered && isNew ? el('span', {class: 'badge warn', text: `new since ${markDate(sheet.entered_at)}`}) : null)))))));
  panel.append(el('p', {class: 'muted', text: closed
    ? 'The two weeks are over, so students with nothing on time (including late work) are listed as 0. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.'
    : 'The two weeks are still open, so empty scores stay blank and students with nothing turned in aren’t listed yet. Low scores are worth a look in the deck before you enter them.'}));
  return panel;
}

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
      data.sheet?.configured ? el('button', {type: 'button', onclick: () => recordMark(form, 'feedback')}, 'Mark feedback done') : null,
      data.prompt_url ? link('Edit the prompt in the Agenda ↗', data.prompt_url) : null, status),
    data.prompt ? el('details', {}, el('summary', {text: 'Show the prompt'}), el('pre', {class: 'for-claude', text: data.prompt})) : null);
  return panel;
}

function studentRow(row, scores, closed) {
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
    el('td', {}, fileList(row.files), earlier), ...scoreCells(row, scores, closed));
}

function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function downloadSheet(form, lesson) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const scores = work.scores[form.form_id] || {};
  const closed = closedFor(form);
  const scored = r => {
    const s = scores[scoreFile(r)?.id];
    return s && !s.problem ? shown(s, closed) : ['', ''];
  };
  const rows = [
    ...form.students.filter(r => r.matched && inSection(r)).map(r => [r.last, r.first, r.section, 'Yes', when(r.submitted_at), ...scored(r), r.files[0]?.url || '', sheetNotes(r)]),
    ...form.late.filter(r => r.matched && inSection(r)).map(r => [r.last, r.first, r.section, 'Late', when(r.submitted_at), ...shown(null, closed), r.files[0]?.url || '', `Turned in after ${form.window?.label || 'the two-week window'} — not graded`]),
    ...form.missing.filter(inSection).map(m => [m.last, m.first, m.section, 'No', '', ...shown(null, closed), '', m.unsorted ? 'Has a file in "Not sorted" — check it' : '']),
  ].sort((a, b) => (a[2] + a[0] + a[1]).localeCompare(b[2] + b[0] + b[1]));
  const unmatched = form.students.filter(r => !r.matched).map(r => ['', '', '', 'Yes', when(r.submitted_at), ...scored(r), r.files[0]?.url || '', 'Not matched to the roster: ' + (r.email || r.files[0]?.name || 'unknown')]);
  const lines = [['Last name', 'First name', 'Section', 'Turned in', 'Turned in at', 'Notes /10', 'Revision /10', 'Work link', 'Notes'], ...rows, ...unmatched];
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
    box.append(el('p', {class: 'muted', text: `Open until ${form.window.label} — two weeks after the first turn-in (${form.window.opened}). Anything later is Late and isn’t graded.`}));
  }
  const marks = marksLine(form);
  if (marks) box.append(marks);
  if (work.ui[form.form_id]?.saved) box.append(note(work.ui[form.form_id].saved, 'info'));
  const scores = work.scores[form.form_id];
  const ui = work.ui[form.form_id] || {};
  const scoredAll = scores && !ui.progress;
  box.append(el('div', {class: 'action-row'},
    el('button', {type: 'button', class: 'primary', onclick: () => downloadSheet(form, lesson)}, 'Download grade sheet (CSV)'),
    form.students.length ? el('button', {type: 'button', disabled: Boolean(ui.progress), onclick: () => scoreForm(form)},
      scores ? 'Score again' : 'Score completion') : null,
    scoredAll ? el('button', {type: 'button', class: 'primary', onclick: () => openPanel(form, 'ic')},
      ui.open === 'ic' ? 'Hide IC scores' : 'Scores are ready to enter in IC') : null,
    scoredAll ? el('button', {type: 'button', onclick: () => openPanel(form, 'feedback')},
      ui.open === 'feedback' ? 'Hide feedback' : 'Start feedback (use Teacher Claude)') : null,
    ui.progress ? el('span', {class: 'muted', text: ui.progress}) : null));
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
    if (ui.open === 'ic') box.append(icPanel(form, lesson, scores));
    if (ui.open === 'feedback') box.append(feedbackPanel(form));
  }
  const listed = [...matched, ...unmatched];
  if (listed.length) {
    box.append(el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['Student', 'Section', 'Turned in', 'Work', ...(scores ? ['Notes /10', 'Revision /10'] : [])].map(h => el('th', {text: h})))),
      el('tbody', {}, listed.map(r => studentRow(r, scores, closedFor(form)))))));
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
  if (missing.length) {
    box.append(el('div', {class: 'missing'}, el('h4', {text: closedFor(form) ? `Nothing turned in on time (${missing.length}) — these are 0 now` : `Not turned in yet (${missing.length})`}),
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
    box.append(el('p', {class: 'ok', text: '✓ Everyone who turned in work this week is on this class’s roster.'}));
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
  if (!p.moves.length && !p.create && !p.empty_after.length) {
    parts.push(note(p.keep ? `Already tidy: everything for ${p.week} is in ${p.keep.name}.` : 'Nothing to tidy.', 'info'));
    if (p.problem && p.problem.indexOf("can't move files") < 0) parts.push(note(p.problem));
    box.replaceChildren(...parts);
    return;
  }
  parts.push(el('p', {}, p.keep ? ['Keep ', link(p.keep.name + ' ↗', p.keep.url), ' as this week’s one folder.']
    : `Create “${p.create}” as this week’s one folder.`));
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
  if (p.problem) parts.push(note(p.problem));
  if (p.can_move) {
    parts.push(el('div', {class: 'action-row'},
      el('button', {type: 'button', class: 'primary', onclick: () => applyTidy(box, p)}, 'Move them'),
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
    if (body.failed.length) parts.push(note(`${body.failed.length} couldn’t be moved: ` + body.failed.map(f => `${f.name} (${f.why})`).join('; ')));
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
$('work-class').addEventListener('change', e => { work.cls = e.target.value; store.set('class', work.cls); work.weeksFor = null; $('tidy-box').replaceChildren(); openWork(); });
$('work-week').addEventListener('change', e => { work.week = e.target.value; $('tidy-box').replaceChildren(); loadWork(); });
$('work-section').addEventListener('change', e => { work.section = e.target.value; store.set('section', work.section); renderWork(); });
$('reload-work').addEventListener('click', async () => {
  try { work.weeksFor = null; await loadWeeks(true); await loadWork(); } catch (error) { setStatus('work-status', error.message, true); }
});
$('tidy-work').addEventListener('click', () => { if (work.week) previewTidy(); });
window.addEventListener('hashchange', showTab);
showTab();
