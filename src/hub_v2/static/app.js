// Teaching Hub v2: lesson plans from the Agenda, and student work found from
// each lesson's Form responses and turn-in folder, with completion scores
// counted from each deck's coded boxes. The grade sheet downloads to you;
// scores go into Infinite Campus by you or the Claude extension, never by this page.
// Biology answers are copied into the teacher's research sheet once an assignment closes.
const $ = id => document.getElementById(id);
const CLASSES = [['biology', 'Biology'], ['forensics', 'Forensics'], ['anatomy', 'A&P']];
const SCAN_CLASSES = ['biology', 'anatomy'];  // with an "Answers by question" view
const RESEARCH_CLASSES = ['biology'];         // whose final answers go to the research sheet
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
// Only the lesson's plain-text cells are edited here; cells with links or
// formulas stay in the Agenda. The server writes a field only if the Agenda
// still says what this page started from.
function lessonValues(lesson) {
  const values = {topic: lesson.topic, todo: lesson.todo, prep: lesson.prep};
  for (const p of lesson.plans) { values['plan:' + p.day] = p.plan; values['note:' + p.day] = p.note; }
  return Object.fromEntries(Object.keys(lesson.editable || {}).map(key => [key, values[key] ?? '']));
}

const fieldLabel = key => ({topic: 'Topic', todo: 'To-do', prep: 'Lab / prep'})[key]
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
    const input = key === 'topic'
      ? el('input', {id, type: 'text', disabled: locked || edit.busy})
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
// The lessons trade weeks in the Agenda: only their Wk and Dates cells change,
// so plans, notes, deck links and Forms travel with each row.
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
    others.map(w => el('option', {value: w.label, text: `${w.label} · ${short(w.topic)}`})));
  const here = weeks.findIndex(w => w.label === lesson.week);
  const next = weeks[here + 1] || weeks[here - 1];
  if (next && next.label !== lesson.week) select.value = next.label;
  box.append(el('div', {class: 'action-row'}, select,
      el('button', {type: 'button', disabled: edit.busy, onclick: () => moveLesson(cls, lesson, select.value)}, 'Move')),
    el('p', {class: 'muted', text: 'The two lessons trade weeks: this one takes the week you pick, and the lesson there takes this one’s. Plans, notes, deck links and the Turn-in Form move with each lesson.'}));
  return box;
}

async function moveLesson(cls, lesson, toWeek) {
  const target = (plans.weeks[cls.key] || []).find(w => w.label === toWeek);
  const other = target?.topic ? `“${target.topic}” will move to ${lesson.week}.` : `Nothing is planned in ${toWeek} yet, so only this lesson moves.`;
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
    if (done.swapped.length) said.push(`“${done.swapped.join('”, “')}” moved to ${done.moved.from}`);
    if (done.dates_left) said.push(`its Dates cell still shows ${done.moved.from}’s dates, so fix that in the Agenda`);
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
    if (value !== edit.base[key]) changes[key] = {from: edit.base[key], to: value};
  }
  if (!Object.keys(changes).length) { plans.editing = null; renderPlans(plans.data); return; }
  edit.busy = true; edit.error = null;
  renderPlans(plans.data);
  try {
    const done = await postJSON('api/agenda', {class: edit.cls, week: edit.week, row: edit.row, changes});
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
// Once an assignment's first turn-in appears (its two weeks have begun), every
// empty score is 0, and a 0 is replaced when the student turns work in before
// the two weeks close. Before anyone has turned it in, nothing is scored.
const closedFor = form => Boolean(form.window && new Date(form.window.at) < new Date());
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
  if (RESEARCH_CLASSES.includes(work.cls) && closedFor(form)) saveAnswers(form, seq);
}

// ---------- The research sheet (Biology) ----------
// Once an assignment's two weeks close, its answers are final: the server adds
// any not already in the sheet, so opening the week again adds nothing twice.
async function saveAnswers(form, seq) {
  const ui = work.ui[form.form_id] = work.ui[form.form_id] || {};
  ui.research = {saving: true};
  renderWork();
  try {
    const done = await postJSON('api/answers', {class: work.cls, week: work.week, form: form.form_id});
    if (seq === work.seq) ui.research = done;
  } catch (error) {
    if (seq === work.seq) ui.research = {error: error.message};
  }
  if (seq === work.seq) renderWork();
}

// Once a day, in the background, the dashboard adds any closed assignment's
// answers the research sheet doesn't have yet, so no week has to be opened.
async function sweepResearch() {
  const today = todayISO();
  if (store.get('research-sweep') === today) return;
  for (const cls of RESEARCH_CLASSES) {
    for (let round = 0; round < 8; round++) {
      let done;
      try { done = await postJSON('api/answers/sweep', {class: cls}); } catch { return; }  // try again next load
      if (!done.more) break;
      if (done.busy) await new Promise(resolve => setTimeout(resolve, 5000));
    }
  }
  store.set('research-sweep', today);
}

function researchLine(form) {
  if (!RESEARCH_CLASSES.includes(work.cls) || !form.window) return null;
  if (!closedFor(form)) {
    return el('p', {class: 'muted', text: `Answers go to the research sheet when the two weeks close (${form.window.label}).`});
  }
  const r = work.ui[form.form_id]?.research;
  if (!r) return null;
  if (r.saving) return el('p', {class: 'muted', text: 'Saving answers to the research sheet…'});
  if (r.error) return note(`The answers couldn’t be saved to the research sheet: ${r.error}`);
  if (r.configured === false) return note(r.setup, 'info');
  return el('p', {class: 'muted'}, r.saved
    ? `Research sheet: added ${r.saved} answer${r.saved === 1 ? '' : 's'}. `
    : 'Research sheet: every answer is already saved. ', r.url ? link('Open it ↗', r.url) : null);
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
    const done = await postJSON('api/record', {class: work.cls, week: work.week, form: form.form_id, kind});
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
      .filter(x => x.s && !x.s.problem).map(x => ({...x, show: shown(x.s)})),
    // A student with a file in "Not sorted" may have turned it in: they're left
    // off the list (and named below) so a 0 isn't entered for them unchecked.
    ...(form.window ? [...form.missing.filter(m => !m.unsorted), ...form.late.filter(r => r.matched)].filter(inSection)
      .map(r => ({r, s: null, zero: true, show: shown(null)})) : []),
  ].sort((a, b) => (a.r.section + a.r.last + a.r.first).localeCompare(b.r.section + b.r.last + b.r.first));
  // New since you last entered this assignment: turned in after that, or, for
  // a student with nothing, a 0 not entered yet or due again at the close.
  for (const x of all) {
    x.fresh = !entered || (x.zero ? closed && new Date(form.window.at) > entered : new Date(x.r.submitted_at) > entered);
  }
  const fresh = all.filter(x => x.fresh);
  const dash = v => v === '' ? '— (leave blank)' : v;
  const claudeText = list => [
    'Please enter these completion scores in Infinite Campus. I am signed in and have the grade book open in this Chrome window.',
    `Class: ${cls}${section === 'all' ? '' : ' ' + section} · ${work.week} · Assignment: ${form.label}${lesson.topic ? ` (${lesson.topic})` : ''}`,
    ...(icStatus(form) ? [icStatus(form).text] : []),
    'Each score is out of 10. "Notes" goes in this assignment\'s notes/content score and "Revision" in its revision score.',
    'Before you save, show me each student, assignment and score you are about to enter, and wait for my OK.',
    'Do not change any other grade. If a student already has a different score for this assignment, it is an update from a resubmission: show me the old and new score and replace it only after I say OK. If a student or the assignment is not there, stop and ask me. "— (leave blank)" means leave it blank.',
    'After saving, reload the page and read the saved scores back to me.',
    '', 'Last, First — Section — Notes /10 — Revision /10',
    ...list.map(({r, show, zero}) => `${r.last}, ${r.first} — ${r.section} — Notes ${dash(show[0])} — Revision ${dash(show[1])}${zero ? ` (nothing turned in ${closed ? 'on time' : 'yet'})` : ''}`)].join('\n');
  const status = el('span', {class: 'muted'});
  const panel = el('div', {class: 'ic-panel'}, el('h4', {text: 'Enter these scores in Infinite Campus'}));
  if (ui.busy) { panel.append(el('p', {class: 'muted', text: ui.busy})); return panel; }
  if (ui.error) panel.append(note(ui.error));
  if (sheet && !sheet.configured) panel.append(note(sheet.setup, 'info'));
  const standing = icStatus(form);
  if (standing) panel.append(el('p', {class: 'ic-status ' + standing.kind, text: standing.text}));
  if (!all.length) { panel.append(note('No scores to enter yet.', 'info')); return panel; }
  panel.append(el('p', {}, el('strong', {text: 'Open Infinite Campus first'}), entered
    ? ` and go to this assignment. You entered it on ${markDate(sheet.entered_at)}; ${fresh.length ? `the ${fresh.length} marked “new since” turned in after that.` : 'nobody has turned in anything new since.'} Type them in yourself, or copy them for the Claude extension, which enters them while you watch and asks before it saves.`
    : ' and go to this assignment in your grade book. Type the scores in yourself, or copy them for the Claude extension, which enters them while you watch and asks before it saves.'));
  panel.append(el('div', {class: 'action-row'},
    fresh.length ? el('button', {type: 'button', class: 'primary', onclick: () => copyText(claudeText(fresh), status)},
      entered ? `Copy for Claude (${fresh.length} new since)` : 'Copy for Claude') : null,
    entered ? el('button', {type: 'button', onclick: () => copyText(claudeText(all), status)}, 'Copy all for Claude') : null,
    sheet?.configured ? el('button', {type: 'button', onclick: () => recordMark(form, 'ic')},
      'I entered these in IC (click adds to Scores entered)') : null,
    status));
  if (sheet?.configured) {
    panel.append(el('p', {class: 'muted'}, 'Click “I entered these in IC” only after the scores are in IC. It adds a dated row to ',
      link(`“${sheet.name || 'Scores entered'}”`, sheet.url), '. From then on, only work turned in later is marked new; once the two weeks have closed, it marks this assignment’s final scores done, and nothing more is entered for it.'));
  }
  panel.append(el('div', {class: 'table-wrap'}, el('table', {},
    el('thead', {}, el('tr', {}, ['Student', 'Section', 'Notes /10', 'Revision /10', ''].map(h => el('th', {text: h})))),
    el('tbody', {}, all.map(({r, show, zero, fresh: isNew}) => el('tr', {}, el('td', {text: `${r.last}, ${r.first}`}), el('td', {text: r.section}),
      el('td', {text: show[0] || '—'}), el('td', {text: show[1] || '—'}),
      el('td', {}, zero ? el('span', {class: 'badge', text: closed ? 'nothing on time' : 'nothing yet'}) : null,
        entered && isNew ? el('span', {class: 'badge warn', text: `new since ${markDate(sheet.entered_at)}`}) : null)))))));
  const unsorted = form.window ? form.missing.filter(m => m.unsorted && inSection(m)) : [];
  if (unsorted.length) {
    panel.append(note(`Not listed: ${unsorted.map(m => `${m.last}, ${m.first}`).join('; ')}. `
      + 'Each has a file in “Not sorted” that may be this assignment. Check that box before giving them a 0.', 'info'));
  }
  panel.append(el('p', {class: 'muted', text: closed
    ? 'The two weeks are over, so students with nothing on time (including late work) are listed as 0. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.'
    : `Students with nothing turned in yet, and empty boxes, are listed as 0; each 0 is replaced when the student turns work in before ${form.window?.label || 'the two weeks close'}. Not listed: work not matched to the roster and decks that couldn’t be scored. Low scores are worth a look in the deck before you enter them.`}));
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

function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function downloadSheet(form, lesson) {
  const section = work.section;
  const inSection = r => section === 'all' || r.section === section;
  const scores = work.scores[form.form_id] || {};
  const scored = r => {
    const s = scores[scoreFile(r)?.id];
    return s && !s.problem ? shown(s) : ['', ''];
  };
  const rows = [
    ...form.students.filter(r => r.matched && inSection(r)).map(r => [r.last, r.first, r.section, 'Yes', when(r.submitted_at), ...scored(r), r.files[0]?.url || '', sheetNotes(r)]),
    ...form.late.filter(r => r.matched && inSection(r)).map(r => [r.last, r.first, r.section, 'Late', when(r.submitted_at), ...shown(null), r.files[0]?.url || '', `Turned in after ${form.window?.label || 'the two-week window'} — not graded`]),
    ...form.missing.filter(inSection).map(m => [m.last, m.first, m.section, 'No', '', ...shown(null, Boolean(form.window)), '', m.unsorted ? 'Has a file in "Not sorted" — check it' : '']),
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
  const standing = icStatus(form);
  if (standing) box.append(el('p', {class: 'ic-status ' + standing.kind, text: standing.text}));
  const research = researchLine(form);
  if (research) box.append(research);
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
    scoredAll && SCAN_CLASSES.includes(work.cls) ? el('button', {type: 'button', onclick: () => openPanel(form, 'answers')},
      ui.open === 'answers' ? 'Hide answers' : 'Answers by question') : null,
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
  if (missing.length) {
    box.append(el('div', {class: 'missing'}, el('h4', {text: closedFor(form) ? `Nothing turned in on time (${missing.length}) — these are 0 now`
      : form.window ? `Not turned in yet (${missing.length}) — 0 until they turn it in` : `Not turned in yet (${missing.length})`}),
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
        if (!form.window || seen.has(form.form_id)) continue;  // not begun, or already counted from another week
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
// Keep this list and the Todoist list the same: "Copy for Todoist" copies it.
const ROUTINE = {
  scheduled: [
    {who: 'School Claude', when: 'Every Monday, 7:23 AM', key: 'monday_run',
     what: 'Monday grades and roster: checks each Infinite Campus roster against your roster sheet, enters scores in IC (0s from the first turn-in; final scores once the two weeks close), clicks “I entered these in IC”, and leaves a “Monday run — date” Doc at the top of 00 REAL ALL TURN IN. Nothing is saved until you reply OK.',
     needs: 'The schedule itself fires from Claude’s servers, but the run works in your home computer’s Chrome, so at 7:23 AM that computer needs: the Claude desktop app open (with this task set to run on that computer), the computer awake, and you signed in to Chrome, Infinite Campus and the Teaching Hub.'},
    {who: 'Teaching Hub', when: 'Once a day, when the dashboard is opened', key: 'research',
     what: 'Adds the answers from Biology assignments whose two weeks have closed to “Student answers — Biology”.',
     needs: 'The dashboard opened once that day, on any tab and in any browser. The Monday run counts.'},
    {who: 'Teaching Hub', when: 'Whenever a week is opened on Student work',
     what: 'Scores every deck, finds missing and late work, and shows each assignment’s IC status.',
     needs: 'Nothing but opening it.'},
  ],
  mine: [
    {when: 'Every Monday, 7:15 AM', todoist: 'every monday at 7:15am', what: 'On your home computer: open the Claude desktop app, sign in to Chrome, Infinite Campus and the Teaching Hub, and leave the computer awake with Chrome and the app open.', note: 'School Claude’s Monday run starts at 7:23 AM and can’t open the app, wake the computer or sign in for you.'},
    {when: 'Every Monday, 8:00 AM', todoist: 'every monday at 8am', what: 'Review school Claude’s Monday list (roster and scores) and reply OK; skim the “Monday run” Doc.'},
    {when: 'Every Thursday', todoist: 'every thursday', what: 'Feedback: on Student work, for each assignment with new revisions, Start feedback → paste into Teacher Claude → check the three Docs in the week’s 📥 folder → print → click “Mark feedback done”.', note: 'Click Mark feedback done right after the run, or the same students are listed again.'},
    {when: 'Every Friday', todoist: 'every friday', what: 'Hand back the printed feedback and collect the “What I understand now” pages.'},
    {when: 'Every Friday', todoist: 'every friday', what: 'Plan next week on Lesson plans (Edit here), and check each lesson row has its Turn-in Form and Student copy links.', note: 'Without those links the Hub can’t find or score the work.'},
    {when: 'Every Friday', todoist: 'every friday', what: 'Run the embed-coding program on next week’s new decks before handing them out.'},
    {when: 'Every Friday', todoist: 'every friday', what: 'On Student work, click “Tidy this week’s folders” for each class.'},
    {when: 'First Monday of the month', todoist: 'every first monday', what: 'Run Prompt 3 (Biology teaching summary) in Teacher Claude, and glance at “Student answers — Biology” for the recently closed weeks.'},
    {when: 'When you change a prompt in the Agenda', todoist: 'every last friday', what: 'Ask Claude Code to copy the changed prompt to GitHub.'},
    {when: 'Every August', todoist: 'every august 1', what: 'New school year: rename “Student answers — Biology” to add the year, make a new blank one with the exact name, and update the rosters.'},
  ],
};

const routine = {seq: 0};

function todoistText() {
  return ['Add these to my Todoist project "Teaching Hub". Use each "when" as the due date and each "note" as the description.', '',
    ...ROUTINE.mine.map(t => `- ${t.what} | when: ${t.todoist}${t.note ? ` | note: ${t.note}` : ''}`)].join('\n');
}

function routineStatus(key, data) {
  const found = data?.[key];
  if (found?.problem) return note(found.problem);
  if (key === 'monday_run') {
    if (!found) return el('p', {class: 'muted', text: 'No “Monday run” Doc yet, so it hasn’t run (or hasn’t been set up).'});
    const days = (Date.now() - new Date(found.at)) / 86400000;
    return el('p', {class: days > 8 ? 'note warn' : 'muted'}, `Last run: ${when(found.at)}. `, link(found.name + ' ↗', found.url),
      days > 8 ? ' That’s more than a week ago: check that school Claude’s Monday schedule is still set.' : '');
  }
  if (key === 'research') {
    const checked = store.get('research-sweep');
    if (!found) return note('No “Student answers — Biology” sheet was found at the top of 00 REAL ALL TURN IN.', 'info');
    return el('p', {class: 'muted'}, found.last ? `Answers last added ${found.last}. ` : 'No answers added yet. ',
      checked ? `Checked in this browser on ${checked}. ` : '', link('Open the sheet ↗', found.url));
  }
  return null;
}

function renderRoutine(data) {
  const body = $('routine-body');
  // Folded by default so the teacher's own tasks come first; the heading warns
  // when the Monday run is missing or more than a week old.
  const run = data?.monday_run;
  const late = data && (!run || run.problem || (Date.now() - new Date(run.at)) / DAY > 8);
  const scheduled = el('details', {class: 'routine routine-scheduled', open: store.get('routine-scheduled-open') === '1',
    ontoggle: e => store.set('routine-scheduled-open', e.target.open ? '1' : '0')},
    el('summary', {}, el('h2', {text: 'Done for you, on schedule'}),
      late ? el('span', {class: 'badge warn', text: run?.problem ? 'Can’t check the Monday run' : 'Monday run: not in the last week'}) : null),
    ROUTINE.scheduled.map(t => el('div', {class: 'routine-item'},
      el('p', {class: 'routine-when'}, el('strong', {text: t.when}), ` · ${t.who}`),
      el('p', {text: t.what}),
      el('p', {class: 'routine-needs'}, el('strong', {text: 'Needs: '}), t.needs),
      t.key ? routineStatus(t.key, data) : null)));
  const mine = el('section', {class: 'routine'}, el('h2', {text: 'Your routine'}),
    el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['When', 'What', 'Why it matters'].map(h => el('th', {text: h})))),
      el('tbody', {}, ROUTINE.mine.map(t => el('tr', {}, el('td', {text: t.when}), el('td', {text: t.what}),
        el('td', {class: 'muted', text: t.note || ''})))))));
  const sheet = data?.scores_entered;
  const sheets = el('section', {class: 'routine'}, el('h2', {text: 'Where the records are'}),
    el('ul', {class: 'files'},
      el('li', {}, sheet && !sheet.problem
        ? [link(`“${sheet.name}” ↗`, sheet.url), ` — the dashboard adds a row when “I entered these in IC” or “Mark feedback done” is clicked${sheet.last ? `; last row ${sheet.last}` : ''}.`]
        : sheet?.problem ? sheet.problem : 'No “Scores entered” sheet yet: make one at the top of 00 REAL ALL TURN IN.'),
      el('li', {}, 'Feedback, Growth record and Class growth summary Docs: in each week’s 📥 folder.'),
      el('li', {}, '“Monday run — date” Docs and Prompt 3 summaries: at the top of 00 REAL ALL TURN IN.')));
  body.replaceChildren(mine, scheduled, sheets);
}

// ---------- Link check ----------
const KIND_NOTE = {'shared-form': 'info', 'no-form': 'info'};

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
      part.lastChild.remove();
      if (data.problem) part.append(note(data.problem));
      part.append(data.flags.length
        ? el('ul', {class: 'link-flags'}, data.flags.map(f => el('li', {class: KIND_NOTE[f.kind] ? 'muted' : ''},
            el('strong', {text: `${f.week} · ${f.topic}: `}), f.message)))
        : el('p', {class: 'ok', text: `✓ Every ${name} lesson's Forms and decks line up.`}));
      part.append(el('details', {}, el('summary', {text: `Every ${name} lesson's Forms and decks (${data.rows.length})`}),
        el('div', {class: 'table-wrap'}, el('table', {},
          el('thead', {}, el('tr', {}, ['Week', 'Topic', 'Turn-in Forms', 'Decks'].map(h => el('th', {text: h})))),
          el('tbody', {}, data.rows.map(r => el('tr', {}, el('td', {text: r.week}), el('td', {text: r.topic}),
            el('td', {text: r.forms.join(' · ') || '—'}), el('td', {text: r.decks.join(' · ') || '—'}))))))));
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
  const current = ['work', 'routine'].includes(tab) ? tab : 'plans';
  for (const name of ['plans', 'work', 'routine']) $('page-' + name).hidden = name !== current;
  document.querySelectorAll('.tabs a').forEach(a => {
    if (a.dataset.tab === current) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (current === 'work') openWork(rest.map(part => decodeURIComponent(part)));
  else if (current === 'routine') loadRoutine();
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
$('work-section').addEventListener('change', e => { work.section = e.target.value; store.set('section', work.section); closeSheets(); renderWork(); });
$('reload-routine').addEventListener('click', loadRoutine);
$('run-link-check').addEventListener('click', runLinkCheck);
$('copy-todoist').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(todoistText()); setStatus('routine-status', 'Copied. Paste it into ChatGPT to add these to Todoist.'); }
  catch { setStatus('routine-status', 'Couldn’t copy in this browser.', true); }
});
$('reload-work').addEventListener('click', async () => {
  try { work.weeksFor = null; await loadWeeks(true); await loadWork(); } catch (error) { setStatus('work-status', error.message, true); }
});
$('tidy-work').addEventListener('click', () => { if (work.week) previewTidy(); });
$('missing-sheets').addEventListener('click', missingWorkSheets);
window.addEventListener('hashchange', showTab);
showTab();
sweepResearch();
