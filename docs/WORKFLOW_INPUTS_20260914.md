# Python workflow inputs and report continuation

## Ownership and evidence

The separate cloud chat owns hosting, deployment, runtime identity/storage and
activation. This change implements application code and tests; it does not edit
Actions workflows, enable variables, billing, schedules or source school files.

Validation: **314 tests passed**, zero failures/errors/skips, using
`python3 -B scripts/run_all_tests.py`. New input coverage adds 46 tests to the
268-test baseline. All service responses are mocked and school records are
fictional. The integrated input demo runs twice with identical receipts,
preserves the baseline after a simulated source failure, and produces the same
printable report without duplicate proposals. These are local tests, not a
hosted or live-source verification.

## Application contracts for the cloud owner

| Component | Entry point | What the host still supplies |
| --- | --- | --- |
| Agenda read/map/checkpoint | `workflows.agenda_observer.observe_agenda` | Authorized Sheets reader, exact spreadsheet/sheet/layout, existing private SQLite connection, stable private 32-byte HMAC key, timezone-aware clock |
| Baseline state | `core.observation_baseline.read_baseline` / `checkpoint_observation` | Existing initialized ledger; logical stream key; prior checkpoint token; fresh source time |
| Todoist class-note source | `adapters.todoist_notes.TodoistNoteReader.read_notes` | Runtime token and explicit project/section; optional exact label |
| Today's note routing | `workflows.daily_notes.plan_daily_notes` | Declared local day/timezone, class-tag map, actual meeting classes, holiday flag and already-accepted note versions |
| 14-day metadata collection | `adapters.submission_pages.collect_submission_window` | Actual provider callback, stable class/assignment IDs and aware as-of timestamp |
| Printable report | `workflows.print_packet.render_print_packet` | Existing validated synthetic reporting preview; later real-report acceptance requires its own private-data path |

### Agenda mapping and stable identity

`AgendaLayout` explicitly identifies the sheet, bounded data rows and five
columns: assignment stable ID, class stable ID, title, due date and deck link.
Rows/columns are zero-based; ending rows are exclusive. Date formats are
explicit ISO dates or US dates with four-digit years. There is no year guessing
or inference of an identity from row number or title.

`normalize_agenda_rows` returns the shared `AssignmentRecord` plus exact deck
reference and source fingerprint. The observer constructs the bounded A1
request from the layout and checks the returned sheet ID/title. Missing or
duplicate identities, ambiguous links, malformed dates, overlapping/partial
coverage and metadata-only responses block checkpointing.

Direct cell hyperlinks, rich-text links and literal `HYPERLINK` first arguments
are recognized. Computed formulas are held; none are evaluated. Existing URL
canonicalization preserves copy/edit and meaningful fragments. A normalized URL
does not prove reachability or student access. External platform/deck/Form IDs
remain unknown until separately mapped.

This first layout supports an explicit tabular assignment source. It does not
claim to match the current live Agenda's layout. If that layout uses merged
weekly grids, multiple lessons per cell or no stable ID column, provide an
explicit mapping adapter after inspecting it; do not squeeze real data into a
fictional schema or create IDs from row positions.

### Atomic baseline behavior

The existing `source_revisions` ledger stores only the opaque observation
schema, its timestamps and content history. No new database or migration was
created. The transaction helper now supports nested savepoints so reading the
baseline and recording a revision occur within one writer transaction.

The initial observation establishes a baseline without claiming a change.
Identical content reuses its existing source revision; changed content retains
history. Key/source/range changes, stale observations and conflicting timestamps
do not replace the last good baseline. A checkpoint token includes observation
time as well as revision, preventing an old worker from passing comparison
after content changes and later returns to an earlier version. Caller rollback
also rolls back a successful nested checkpoint.

Only `observe_agenda`'s complete validated read advances its checkpoint. Calling
the lower-level API requires the same completeness discipline. Host connection
and storage placement remain the cloud owner's responsibility.

### Notes: read and route without changing Todoist

The Todoist adapter uses GET on the active-task endpoint, requires both project
and section, limits each page to 100 tasks and defaults to ten pages. It keeps
the same scope across cursors, deduplicates identical tasks and refuses changed
duplicates, wrong scope, deleted tasks, malformed pages and unfinished
pagination. It keeps only fields needed for note routing. Cursors stay in
memory; no task is completed, edited, commented on or moved by the adapter.

Todoist's cursor listing is not an atomic snapshot. The result says so; do not
infer that a note was deleted merely because it was absent. These API choices
follow the [official Todoist API reference](https://developer.todoist.com/api/v1/).

The router uses explicit dated class tags and actual meeting classes. It skips
closed tasks and defers past/future dates, nonmeeting classes and holidays.
Missing dates, ambiguous tags and conflicting source copies go to review.
Timezone-aware times are converted to the declared local timezone; unresolved
floating times are held. The source title/description remain observation text,
not an invented interpretation. Note versions deduplicate only after the host
records successful journal append/readback; edits produce new versions.

### Submission collection and reporting

The submission provider must return `SubmissionPage` with one class and a
consistent snapshot revision across pages. The collector bounds page/record
counts, preserves distinct revisions, deduplicates identical records, checks
assignment/learner identities and requires timestamps in the exact 14-day
window. Any failed, incomplete, mismatched or looping page returns no records
and no successful receipt. A failed collection cannot become evidence of
missing student work. The callback owns source authentication and transport
timeouts; an actual Schoology/Drive submission provider is not supplied here.

The fixture integration feeds these validated metadata records into the existing
feedback pipeline. Agenda dates are joined by stable assignment/class IDs while
preserving deck/Form/platform identities. Existing rubric/evidence controls
remain in force. No real student prose is graded by these tests.

`render_print_packet` wraps the existing validated report in self-contained,
escaped HTML with print CSS and a stable content digest. It loads no external
resources or scripts, and does not print, email or file a report. The demo reuses
the existing atomic create-only artifact writer; edited artifacts are preserved.

## Reproduce the complete new slice

```sh
python3 -B scripts/run_all_tests.py
python3 -B scripts/preview_workflow_inputs.py --out /tmp/school-workflow-hub/input-preview
```

On macOS use `/private/tmp/school-workflow-hub/input-preview`. Use a new exact
output directory after source/config changes. Results are `REPORT_PREVIEW.html`,
`INPUT_RECEIPT.json` and fictional `state.sqlite3`. The script runs the slice
twice and checks equality. It needs no login and makes no real source/model calls.

## Remaining live work

Runtime credentials/storage and first source acceptance; verified actual Agenda
layout and link baseline; real submission provider/current rubric; teacher report
review; journal append/readback, email/folder/printing adapters; and separately
authorized Schoology/IC execution. Keep those Todoist tasks open. Research
collection remains deferred until the teaching workflows are verified.
