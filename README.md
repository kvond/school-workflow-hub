# School Workflow Hub

The canonical private repository for the school's Python workflow code,
versioned contracts, prompts, synthetic tests and agent handoffs is
[kvond/school-workflow-hub](https://github.com/kvond/school-workflow-hub).
School records and credentials belong in the approved private runtime, not here.

## Status

Start with [START HERE](docs/START_HERE.md), the
[September 16 integration record](docs/PYTHON_INTEGRATION_20260916.md), and the
[capability inventory](config/capabilities.json).

The September 15 acceptance records verify hosted Agenda metadata, dated lessons,
prompts, school-account authentication, and Firestore persistence/shared claims
using fictional diagnostic data. These are historical acceptance records, not a
fresh health check. See [cloud readiness](docs/CLOUD_READINESS.md).

The Python library includes completion rules, reviewed feedback and class-summary
composition, learning-history validation, submission and Todoist intake contracts,
printable previews, and the reconciled four-page workspace and research drafts.
Live teaching-result providers, actual submission/IC readers, Agenda saves,
grade posting, filing, delivery and printing remain unconnected. Research stays
deferred. Save and Run remain disabled; publishing this code does not deploy it.

## Teaching Hub v2 (`/v2/`)

`src/hub_v2/` is a small replacement for Pages 01 and 02, served at
`/v2/` by the same Cloud Run service, sign-in and settings. **Lesson plans**
reads each class's weekly Agenda tab by header name and week label.
**Student work** reads the Form(s) linked in the lesson's "Turn it in" cell and
everything in the lesson's turn-in folders in `00 ALL TURN IN / <class>`: any
folder named for the lesson's week, and each Form's own `(File responses)`
folder wherever it sits. Files are sorted to an assignment by where they sit or,
when one folder holds several assignments, by the deck they were copied from;
anything unsortable is listed as "Not sorted". Each file is matched to the
roster by school email or by the username/name in its file name. Each student's
current file is shown (Form uploads first; trashed files and files named for
another week are never picked) with the rest as history, alongside who hasn't
turned in. A problem
with one tab, cell or Form is shown on that card; it never blanks the page.

**Editing a lesson from the dashboard.** On Lesson plans, **Edit here** opens a
lesson's plain-text cells (topic, each day's plan and note, to-do, lab / prep)
and **Save to Agenda** writes the changed ones to that row of the class tab.
Edits made in the Agenda show on the dashboard at the next read, so it works both
ways. The server re-reads the Agenda first: if the row moved, or a cell changed
there since the page loaded, nothing is written and the page shows the Agenda's
new text (saving again then replaces it). The editor also takes the lesson's
**links**: the deck (the teacher's Slides link goes in _teacherURL and, unless
that column is a formula, the student /copy link in _studentURL), the activity
guide (_extraURL), and the Turn-in Forms (the Form's own link in _turnInURL
with a linked "📤 Topic — Turn in" label in "Turn it in", and the 2nd turn-in
column). Links are written as plain URLs or linked labels, never formulas, and
a published or forms.gle Form link is refused. Formula cells are only edited in
the Agenda. Text is written as plain text, so bold or italics inside an edited
cell are not kept, and each written cell is read back. The dashboard's account
must be an Editor on the Agenda spreadsheet.

**Moving a lesson.** In the editor, **Move this lesson to another week** works
like reordering a list: the lesson goes to the week picked and the lessons in
between slide one week to make room, skipping weeks with no class ([CLOSED …],
[SPRING BREAK …], "No class") or nothing planned. Only the lesson's cells move
(topic, plans, notes, links, Forms); every row keeps its Wk label and Dates, so
the Agenda stays in week order. Moving into a week with nothing planned moves
only that lesson. It is one Sheets request through a hidden scratch tab removed
in the same request, and it is refused if a formula in the rows reads another
row or tab. A moved lesson keeps finding its work: its
weeks are its Agenda week and the week its own decks are named for (so student
copies named for the old week aren't flagged), and its 📥 folder is found by its
title under the old number. A folder named for another lesson is never taken
for its week number alone.

**Turn-ins through the wrong Form.** Students sometimes turn in one lesson's
deck through another lesson's Form. Each week's page compares every turn-in's
deck name with its own lesson (topic, Form label, deck titles, and the words its
matching turn-ins share) and with the lessons up to two teaching weeks either
side. A deck that names nothing of its own lesson and matches exactly one other
isn't counted for its Form: the card lists it under "Not counted here", and the
lesson it belongs to counts it, marked "Via <Form>", so its score, IC list and
feedback go to the right assignment. A deck left in the wrong week's folder is
listed with the week it belongs to, to be moved there.

Each assignment (Form) stays open until the end of the day two weeks after its
first turn-in by anyone (school time), but the two weeks never start before the
Monday of the first Agenda week that links the Form, so an early turn-in doesn't
start the clock (or the 0s) for everyone. Later work is marked **Late**, listed on
its own and marked "Late — not graded" on the grade sheet. Every load also runs a **roster check**:
work from someone on another class's roster or on no roster is flagged, and the
Rosters sheet's last change is shown. A student added to the roster late gets
the date in the tab's **Added** column; every assignment that began before
that date shows them as **Excused — new student** instead of missing, and the
IC list marks them "Excused" (to be marked Exempt in IC, never 0).

Completion scoring runs by itself when a week is open on Student work (results
are cached per deck version; **Reload** reads the week again). It exports each student's current on-time
deck from Drive and compares it, box by box, with the copy linked in the
lesson's Agenda row. The required items are the codes in the deck's
`[[MARKER-INVENTORY:…]]` header that have a box to answer in (a listed code no
box carries isn't counted, so 3 answerable boxes are out of 3): Notes = 10 ×
(completed NOTES + BANK) ÷
required, Revision = 10 × completed DRAFT ÷ required, following the teacher's
Prompt 1. It counts; it never judges quality, and no model is called. A box
counts when the student wrote something new in it; the printed prompt copied
back is flagged, and writing in uncoded boxes is reported by slide. **Scores are
ready to enter in IC** lists the scores. **Send to the Chrome helper** hands
that list to the browser companion in the teacher's own Chrome. In IC's score
panel, its **Hub helper** shows every student and what it will do. It keeps the
higher score: blanks and raises only, never a lower score, and it leaves IC
flags, excused and unsure rows alone. After her tick it types, saves, reloads
and checks each score. On Monday the Claude extension does the clicks after her
OK. When every section and column has read back, the Hub adds "Entered in IC"
with the helper's counts to the Scores entered sheet. A student whose only
turn-in was another lesson's deck is "check by hand", not a 0. For a whole
class and week at once, Student work's **Copy prompt for Claude extension
(opens IC for the Chrome helper)** sends every assignment with something to
enter to the helper and copies a prompt. Pasted into the Claude extension, it
opens each IC score panel, shows her the helper's plans, and after her OK
clicks the helper's Enter. It never types scores. The page itself never
writes to IC. See [the helper's README](browser-extension/README.md). Each assignment card also shows where it stands for IC:
open (enter every score listed, 0s included; a 0 is replaced when the student
turns work in), closed and
waiting for final scores (enter every score, 0s included, then click **I entered
these in IC**), or final scores entered (the note is dated after the close, and
late work isn't graded, so there's nothing more to enter). The dashboard keeps its own notes in a Google Sheet whose name starts with
`Scores entered`, in the turn-in root; the teacher never needs to open it.
**I entered these in IC** and **Mark feedback done** each add one row for the
class and assignment, and each assignment card then shows "✓ Entered in IC
<date> · ✓ Feedback made <date>". The IC list marks students who turned in
something new since that date (a resubmission updates the same grade), and
**Start feedback (use Teacher Claude)** copies the teacher's current Prompt 2
from the Agenda's Feedback Prompts tab with the students who have revised
answers turned in since the last feedback (first name and last initial), for
her own Claude to write one Google Doc with two pages per student, saved in the
week's 📥 folder. Each assignment stays open for two weeks
from its first turn-in; later work is Late and isn't scored. While it is open,
every empty score is 0 from the first turn-in on (nothing turned in yet, or no
revised answers yet), and each 0 is replaced when the student turns work in
before the two weeks close; those zeros are listed for IC too.

**Answers by question** (Biology and A&P) shows what each student wrote in
each coded box of their on-time deck, one table per slide of the handed-out
deck, with first and revised answers side by side and blank or
copied-from-the-slide answers marked. It reads the same decks the scores come
from; no model is called.

**The Biology research sheet.** Once a Biology assignment's two weeks close,
opening it on the dashboard adds its on-time answers to a Google Sheet named
exactly `Student answers — Biology` at the top of the turn-in root, which the
teacher makes once, blank: one row per student per coded box (section, week,
lesson, slide and its title, code, the answer as written, status, turn-in and
deck times, deck link, and a row key). A box already in the sheet is never added
again, rows are never edited or deleted, and answers are written as plain text.
Copies made for coding have other names and are never written to.
Nobody has to open each week: once a day, when the dashboard is opened, it
checks in the background for closed Biology assignments with no rows yet and
adds them (weeks not yet started, and Forms already in the sheet, are skipped
without reading Drive).

**Missing-work sheets** (Student work) reads every week of the chosen class and
section and prints one page per student with missing work: each assignment
whose two weeks have begun that they haven't turned in on time (still open,
with its closing date and an "I will turn it in by" line, or closed and staying
0), how they usually turn work in (on-time count and the typical number of days
after the two weeks start), a short plan, how to turn work in, and student and
parent/guardian signature lines. It uses only the dashboard's own records.

**Link check** (Routine) reads, for every lesson row of each class tab, the Drive
titles of its Turn-in Forms and decks and flags what doesn't line up: a Form or
deck titled for another week, a hidden _turnInURL that opens a different Form
than the visible link, one Form linked in two weeks, a link the dashboard can't
read, or a lesson with no Form. It also lists every lesson's Forms and decks by
title, for comparing with Schoology. Read only.

**Routine** opens with **Copy Monday prompt (Claude extension)**: the teacher
pastes it into the Claude extension in her own Chrome, signed in to Infinite
Campus and the Hub, and it checks rosters, sends each assignment to the Chrome
helper and collects its plans, waits for her OK, then has the helper enter and
check the scores (nothing runs it on a timer). A line under it says whether the
Chrome helper is installed in that browser; the prompt
replaces anything the extension remembers from earlier weeks. **Lesson planning**
comes next: plan on Lesson plans, and right after updating a deck, **Copy
embed-coding prompt** copies the Agenda's Prompt 0 (Feedback Prompts tab, read
live, so edits there show here) to paste into Claude with the deck. The page
then shows when completion scores happen, what runs on its own (the daily
research-sheet check, scoring on open), the teacher's own tasks, who clicks
which button, and live status read from Drive: the newest "Monday run — date"
Doc (flagged when more than a week old)
and the last row of the Scores entered and research sheets. Until an answer is
added, the research line says which Biology assignment closes next and when (the
daily check notes it), since answers are only added once its two weeks close. Documents the system makes and keeps beside the work (feedback, growth
records, class growth summaries, summary ratings, Monday run logs) are never
read as student work, and a one-cell title above a roster list isn't reported
as an unreadable row.

**Tidy this week's folders** is v2's one write that moves or renames anything. It previews, then on the
teacher's confirmation moves a week's extra folders' contents, loose Form
folders and the earlier shared Form's uploads into that week's one `📥` folder,
and late work into its `⏰ Late — not graded` folder. Emptied extra folders go
to the Drive trash when Drive allows it, otherwise they get `_` in front of their
names. It also puts each on-roster student's name and assignment in front of
their files' names ("Sabrina Del Rosario Davila.Investigative Processes (Steps
of CSI) - <original name>"). The assignment name comes from the Form it counts
for, so a Drive search by name finds a student's work. The file keeps a small
Drive tag, and the Hub reads the original name from it, so matching,
turn-in times and scoring don't change. A file renamed by hand afterwards keeps
its new name. Renaming changes the deck's "last modified" time in Drive. It needs the service account shared as an Editor on the turn-in root, and
only runs from the dashboard page (same-origin POST, signed-in teacher).

## Validation

Python 3.9 or later is required; the local checks use the standard library only.

```sh
python3 scripts/hub_status.py --json
python3 scripts/cloud_validation.py
python3 scripts/preview_workflow_inputs.py --out /tmp/school-workflow-hub/input-preview
```

On macOS use `/private/tmp/school-workflow-hub/input-preview` for the last
command. It writes fictional report HTML and a receipt, repeats the workflow,
and refuses to overwrite a different existing report.

`cloud_validation.py` runs the full local test suite and two identical synthetic
previews. It does not start a hosted job or contact school systems. Without
`requirements-hosting.txt` installed, the four signed-identity tests skip and are
not exercised; the gate expects exactly those skips and fails on any other. The
separate GitHub Actions workflow is manual and requires its existing enable
variable; this code reconciliation does not change it or any cloud schedule.

For an installable Python library wheel, use an isolated environment with current
packaging tools (the older macOS system pip produced an invalid empty wheel):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

The repository checkout also contains the app assets, configuration and validation
scripts. Installing the library wheel alone is not a cloud deployment.

## Existing HTML Agenda integration

The existing frontend is in `web/`; its authenticated WSGI application is
`src/school_workflow_hub/web.py`. The deployed app can read Agenda and prompts.
Pages 2 and 3 use [hub-teaching-results/v1](docs/TEACHING_RESULTS_INTEGRATION.md),
whose private provider remains unconnected. The separate
[agenda-workspace/v1](docs/FOUR_PAGE_WORKFLOWS.md) library projection is not an HTTP
provider and does not replace that contract. Two-way Agenda saving is unfinished.

Read [agent boundaries](AGENTS.md) before continuing implementation or activation.
