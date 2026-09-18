# Hosted submission monitoring

Page 2's **Submission monitoring** panel watches each mapped turn-in folder
throughout the post-due window and records what actually changed, durably, in
the existing private cloud state. It is deterministic hosted Python behind
`GET/POST /api/submission-monitor`; it does not depend on a chat session, a
local machine or an open browser tab, and it never assigns a grade or a zero.

## What one scan does

1. Resolves the lesson's specific turn-in folder from the live Agenda TurnIn
   Map (`date` + `class_key`), exactly like the existing inventory check: no
   browser-supplied folder IDs, no shortcuts, bounded pages/depth/records, and
   any incomplete read fails the whole scan.
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
   `{workflow: submission-monitor, class_key, assignment_key: folder-<id>}`,
   short lease, immutable per-run records (`state`, `events`), baseline
   checkpoint compare-and-set, plus one small CAS attempt pointer per stream
   for last-attempted / last-successful scan.

## The rules the implementation encodes

- **Assignment identity is the folder, never the due date.** A deadline change
  emits `due_date_changed` and keeps the same stream and history.
- **Date-only deadlines include the whole local day**, and the grace window
  includes the full fourteenth day, in the school timezone, reusing the
  existing `due_completion.boundaries` rules. Verified individual extensions
  move a matched student's window; no extension source is integrated yet, so
  none are applied silently.
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

647 backend tests (including the monitoring state machine: new, late-evidence,
modified, replacement, ambiguous identity, extension, deadline change, partial
page, permission failure, interrupted/concurrent worker) and 36 frontend tests
pass with identical synthetic replay. Live acceptance requires the runtime
reader to hold Viewer on the active submissions folder tree; until that grant,
hosted scans fail with `submission_folder_access_required` and the recorded
attempt says exactly that. Scheduled execution is not enabled; enabling it is a
separate explicit approval (cadence + scope) that would point an authorized
scheduler at the same endpoint.
