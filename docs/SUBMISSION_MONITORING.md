# Hosted submission monitoring

Page 2's **Submission monitoring** panel watches each mapped submission source
throughout the post-due window and records what actually changed, durably, in
the existing private cloud state. It is deterministic hosted Python behind
`GET/POST /api/submission-monitor`; it does not depend on a chat session, a
local machine or an open browser tab, and it never assigns a grade or a zero.

## What one scan does

1. Resolves the lesson’s Form and exact assignment selector from the Agenda
   and private configuration, or its specific legacy turn-in folder when there
   is no Form. Form responses supply uploaded file IDs directly; folder
   sources retain bounded traversal. See [intake configuration](SUBMISSION_INVENTORY.md).
   No browser-supplied source IDs are accepted and incomplete reads fail the scan.
2. Reads the reviewed grading workbook's own submission rows and takes their
   `source_file_id` references as the only verified student↔file matches.
   Display names are never used to infer identity. Conflicting rows for one
   file leave it unmatched for review.
3. Advances the stream state in `workflows/submission_monitor.py`: cumulative
   per-file history keyed by Drive file ID, first-observed labeling on the
   baseline scan, and a closed event vocabulary — `first_observed`, `appeared`,
   `modified`, `inaccessible`, `reappeared`, `matched`, `due_date_changed`,
   `possible_replacement`.
4. Persists through the existing `CloudState` task/claim/record model
   (`adapters/submission_monitor.py`): scope
   `{workflow: submission-monitor, class_key, assignment_key: <source-key>}`,
   short lease, immutable per-run records (`state`, `events`), baseline
   checkpoint compare-and-set, plus one small CAS attempt pointer per stream
   for last-attempted / last-successful scan.

## The rules the implementation encodes

- **Assignment identity includes its source and selector, never the due date.**
  Form streams use `form-<hash>` over the canonical Form ID, assignment question
  ID and exact assignment answer. Different assignments in one Form cannot share
  a baseline. Legacy folder streams keep `folder-<id>` unchanged. A deadline
  change emits `due_date_changed` and keeps the same stream and history.
- **Migration retains old records; it does not relabel them.** New Form streams
  establish a first-observed baseline. Old folder histories, deadlines and
  receipts remain in private state, and workbook assessments retain their file
  IDs. Course-wide folder history cannot be safely auto-imported into a single
  Form assignment. Existing historical topic starts need an explicit reviewed
  reconciliation before deadline continuity is claimed; until then the Form
  stream reports no verified start. No old task, record, score or receipt is deleted.
- **The deadline is the third actual class meeting, never the navigation
  date.** The teacher declares an actual start by writing it in the day's Plan
  or Notes ("Started Energy today" — past tense; "Plan to start" is only an
  intention) either through the dashboard's Save to Agenda flow or directly in
  Sheets, where a later scan of that day picks it up; the selected dashboard
  date is meeting 1 even for a backdated note. Meetings 2 and 3 come from the
  Agenda's own Weeks calendar (`workflows/topic_deadline.py`): fixed weekday
  patterns per class, ⚠ closure lines, and the per-week A/B Friday
  designation. The final revised submission is due at the end of the third
  meeting's local day whether or not the topic is finished; only an explicit
  teacher deadline exception ("deadline/due <date>" in the day's text)
  overrides it. Repeated saves never duplicate a start, a contradictory start
  is flagged for review instead of resolved, an unfilled calendar makes the
  deadline provisional instead of guessed, and every derived deadline is
  labeled calculated — never verified in Infinite Campus. Date-only deadlines
  still include the whole local day and the grace window still includes the
  full fourteenth day via the existing `due_completion.boundaries` rules;
  verified individual extensions move a matched student's window, and no
  extension source is integrated yet, so none are applied silently.
- **Assessment currency is revision-bound, not date-bound.** A file edited at
  3 p.m. after a 9 a.m. assessment returns to awaiting reassessment the same
  day: a full workbook check timestamp is compared as an instant, a date-only
  marker can never prove currency for a file modified on or after that day,
  and unknown evidence proves nothing. Earlier assessments and grades are
  never altered by this.
- **Derived state is versioned.** `DERIVATION_VERSION` travels with each
  stored state; an unchanged rerun only replays a stored result when the
  derivation logic that produced it is current, otherwise the state is
  recomputed from the retained evidence with history, events and receipts
  preserved.
- **First scan = baseline.** Existing files are `first_observed`, never "just
  submitted" and never late.
- **Metadata is evidence, not judgment.** `modifiedTime`/`version` changes mean
  inspect or reassess. Timing labels are conservative:
  `content_predates_due_turnin_unverified` (a folder move does not update
  modifiedTime, so turn-in moments stay unverified) or
  `modified_after_due_needs_review`. Nothing here proves who submitted,
  proves improvement, or supports a zero.
- **Disappearance is an access event.** A file missing from a complete scan
  becomes `inaccessible` and keeps its history; it is never treated as a
  withdrawal, and a failed or partial scan never replaces the last complete
  baseline (it only records a failed attempt).
- **Unmatched files stay visible for review**; the roster basis is the
  workbook's verified mappings with its checked time recorded. The live IC
  roster reader is not integrated, and the panel says so instead of calling a
  cached view current.
- **Reruns do not duplicate.** An unchanged folder + mappings + due date
  returns the stored result with no new run, records or events; concurrent
  workers serialize on the task lease and baseline CAS. A dashboard refresh
  only reads; scans start from the explicit button (or an approved scheduler
  calling the same endpoint).

## Dashboard

`web/submission-monitor.mjs` renders: whether automatic monitoring is active
(honest `manual_scans_only` until a schedule is approved), last attempted and
last successful scan, scope/coverage/roster basis, per-file stages kept
distinct — `Detected · identity unmatched`, `Awaiting assessment`, `Assessed`
(the workbook's recorded IC posting note is shown as a recorded claim, never as
gradebook verification) — the changes the last scan proved, and a needs-review
list where every failure carries one specific recovery action.

## Verification state

The September 22 Form/PPTX change is implemented and tested with synthetic
inputs only. The folder-based acceptance below is historical and does not
verify Form response access, assignment bindings or the new capture path.


648 backend tests (including the monitoring state machine: new, late-evidence,
modified, replacement, ambiguous identity, extension, deadline change, partial
page, permission failure, interrupted/concurrent worker) and 36 frontend tests
pass with identical synthetic replay.

Live acceptance, September 18, 2026: with the teacher-approved Viewer grant for
the runtime reader on the turn-in root and the project's Drive API enabled
(both were required; an unconfigured API and a missing grant produce the same
Drive 403, which is why the earlier inventory check never succeeded live), the
hosted scan completed for all three classes' current lessons. One class's tree
held five real student files: all were labeled first-observed at baseline, one
carried a verified workbook match and assessed stage, and four stayed visible
as unmatched/awaiting review. The other two trees scanned complete with a Form
and no loose files, recorded as exactly that. A rerun with unchanged inputs
returned the stored result (`unchanged_rerun`) with no new documents, a failed
pre-grant scan left a durable failed attempt without touching any baseline, and
a baseline written by one service revision read back intact from the next
revision. The dashboard panel rendered the same stored state after reload.

Live change detection (a student actually modifying or adding a document
between scans) has not been demonstrated against real work — no student file
was edited to manufacture an event — and remains covered by the synthetic
cases only. Scheduled execution is not enabled; enabling it is a separate
explicit approval (cadence + scope), and the scan endpoint's same-origin gate
means a scheduler integration will also need its own authorized entry path.
