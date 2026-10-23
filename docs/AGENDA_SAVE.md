# Teacher-directed Agenda editing

The teacher authorized implementation and deployment of live Agenda saves on
September 16. This supersedes the historical code-only restriction for this
specific workflow. It does not activate grade posting, emails or printing.

GET `/api/agenda-save?date=YYYY-MM-DD&class_key=forensics` verifies signed IAP,
resolves the weekly row/day, reads exact values, verifies Sheets edit access
using a bounded find/replace of a marker with itself, and checks private state
access. That permission probe leaves cell content unchanged. It supplies the revision
used by POST `/api/agenda-save`. The browser sends date, class_key, revision,
values (title, plan, notes), and a stable request_id. No target coordinates or
credentials are accepted from the browser.

The writer obtains a workbook-scoped Firestore claim, rereads the mapped source,
rejects stale input, writes changed literal values only, and rereads the same
destination. Saved is returned only after matching readback and a durable receipt.
Repeated completed requests verify the destination without another write.
Uncertain writes retain their claim and require reconciliation instead of blind
retries. Both same-origin HTTPS and signed IAP are required for POST. Source
spreadsheet and service account come from existing private runtime configuration.

The weekly sheet is the write target. Existing Dashboard/list formulas remain
intact. Literal Dashboard overrides and days without a weekly column are held
for mapping review. Extra resource links are not yet writable. Sheets direct
edits do not share the Firestore lock: the fresh comparison catches prior edits,
but Sheets offers no atomic revision precondition for a cell update. Avoid
simultaneous direct-Sheets editing of the same cell during a save.

Local synthetic tests are not live acceptance. Verify Editor permission and one
supervised save/readback in the hosted service before reporting this operational.

## Dashboard authoring direction

The teacher writes the title, date plan and teaching notes on the HTML dashboard
and chooses **Save to Agenda**. The server copies those fields to the resolved
weekly Agenda cells; subsequent reads reload that saved content. This direction
is the September 16 preference. The title is shared across the weekly row; plans
and notes use the selected class's mapped day columns.

Capability status is shown for the selected date and each class, rather than
calling the entire dashboard read-only or leaving an earlier date's successful
access check on screen. A delayed capability response from an older load cannot
replace current access. The Plan field, like title and notes, waits for its source
to load before accepting a draft. This avoids creating a draft without a base
revision. A successful access probe alone never displays a saved confirmation.

Synthetic mapping regressions cover Wednesday Forensics, Thursday Anatomy,
nonmeeting days, literal Dashboard overrides, formula protection and revisions
that change when teacher notes change. Live save/readback remains a separate check.

Run `python3 scripts/cloud_validation.py` for the Python source/write checks and
`node --test tests/agenda-editor.test.mjs` for the browser-state checks. The latter
imports the production app with fictional fetch responses and a minimal DOM;
it verifies stale success/failure responses cannot replace the save revision,
selected-date changes reset readiness, and typing during a save preserves the
newer draft. Node is an explicit frontend check so the existing Python-only cloud
validation retains its standard-library runtime contract.


## Current v2 Lessons page (October 23)

The deployed v2 UI uses `GET /v2/api/day` and `POST /v2/api/agenda`,
implemented in `hub_v2/agenda.py` and `hub_v2/agenda_edit.py`. The earlier
`/api/agenda-save` contract above documents the legacy dashboard.

Each mapped lesson day shows **Day notes**, including an empty placeholder,
with **Edit plan and day notes** beside the selected day's plan and in Whole
week. The compact editor uses only that day's mapped plan/note fields. **Edit
whole week and links** retains the larger editor. Formula and rich-link cells
stay locked. The writer compares the exact class/week/row and original cell
values, changes only mapped literal cells, and rereads them. Agenda edits are
loaded back into the Hub on a fresh read. Save or cancel an open editor before
navigating to another date or starting a different editor.

**Class notes** live below each class. They persist independently of week,
lesson and Form, in the existing private store under the configured Agenda
and class. Only an authenticated teacher's explicit Save writes notes. A save
uses compare-and-swap, preserves an immutable previous-version history, and
reads back both current and history documents. A conflict or uncertain save
retains the draft; Reload saved notes compares the current version before any
replacement. A late read cannot erase typing. Opening Lessons is read-only.
The API is `GET /v2/api/classes/notes?class=biology` and same-origin authenticated
`POST /v2/api/classes/notes` with exactly `class`, `text`, and `revision`.

The old top Next week notes box is removed. Existing browser-only notes remain
under their class in **Earlier week notes in this browser**, with their original
week labels. They are not deleted, copied to another class or silently migrated
into saved policy notes. No changes are made to lesson summaries, grading,
feedback, the Chrome helper, schedules or source permissions.

Synthetic coverage: `tests/test_class_notes.py`,
`tests/hub-class-notes-ui.test.mjs`, `tests/hub-agenda-notes-ui.test.mjs`, plus
existing Agenda writer tests. Source/tests, deployment and live save/readback
must be reported separately.
