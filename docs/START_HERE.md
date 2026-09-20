# School Workflow Hub — start here

## Current teacher decision: simplify, do not restart (September 18, 2026)

The teacher needs two usable operations, not more prompts or exposed machinery:
**roster/completion grading, available on schedule and through a manual button**;
and **printable student feedback, using collected submissions or one ZIP upload**.
The daily Agenda and the existing cloud service, workbook, state, approved
Anthropic connection and browser companion remain the system. Do not rebuild.

This document is the delivery contract, not a statement that these operations
are complete. The teacher currently reports that posting and printing do not
work for her. Earlier component tests and matching-grade checks do not override
that report. A docs change, provider connection or scan is not live acceptance.

The active implementation worker owns the live browser, router and recovery.
Other agents must not compete for those tabs, move the same files, or start
another runtime. Use shared sources directly; do not make the teacher relay
instructions, logs, artifacts or implementation decisions between agents.

## Immediate priority: preserve student work

The active worker reports finding student submissions in Drive trash after an
older intake tree was trashed. Treat this as a recovery incident before further
folder cleanup, model calls or interface polishing. It is a report requiring
file-by-file readback, not proof that recovery has completed.

- Record the affected file IDs, original parents and trash state privately.
- Recover verified student artifacts into verified active private destinations
  while preserving their IDs, contents, response links and assessment history.
  Verify each file is untrashed, not beneath a trashed ancestor, readable by the
  intended runtime, and accounted for in the submission list.
- Do not restore an entire obsolete system to recover a few files; do not
  permanently delete anything, empty trash, copy away provenance, or broaden
  inherited permissions. If file-only recovery cannot succeed without another
  change, diagnose and request only the necessary bounded action.
- Stop additional mass moves, destructive setup/reset functions and trigger
  changes until recovery and routing effects are reconciled. Do not rerun
  SETUP merely because it ran without throwing an exception.
- Distinguish distinct files, student/assignment submissions, versions and log
  events. A recovered file is not a newly submitted or late assignment.

## Operation 1: roster and completion grading

**Teacher-facing contract:** choose Class/section, Lesson title, and Notes or
Drafts; use the existing Student work table; press **Run grading now**. The
scheduled routine must invoke the same operation with its verified scope, not
an AI prompt, a second grading implementation, or a button that copies text.

Retain the requested weekday routine and the fuller Friday roster check for
all configured sections. Recover the exact existing cadence and timezone from
private configuration rather than inventing another schedule. Reuse the same
claims, idempotency, reviewed records and receipt model for manual/scheduled
runs. Do not create duplicate chat reminders or scheduler jobs.

The operation must read current roster evidence, collect the selected work,
assess meaningful completion under the live Prompt 1, calculate scores in Python,
and hand the exact permitted grades to the verified IC destination executor.
Keep notes/content and draft/revision components separate. At the initial
checkpoint only NOTES are due; final content includes required NOTES + BANK,
while Revision uses required DRAFT. Optional work is excluded and zero
denominators are N/A. Do not replace meaningful-attempt review with a nonempty
textbox count. Preserve source versions, earlier scores and teacher overrides.

**Execution truth:** Cloud Run can host the processing and schedule entry point;
cloud hosting does not supply an Infinite Campus session, API, or grade writer.
The existing Mac companion has a recorded read/verify/skip/receipt acceptance,
not a general unattended write acceptance. A working manual confirmation must
not be bypassed or silently relabeled scheduled posting.

Use an existing authorized IC integration if one is actually available;
otherwise use the accepted school-browser path, including the teacher's
approved home-Mac interim option where technically supported. Verify the
actual worker invocation and supervision limits. An idle chat or open tab is
not a worker. No copying cookies, passwords, browser profiles or tokens.

Show simple actual outcomes: **Running / Needs your confirmation / Blocked /
Finished**. When confirmation or authentication is required, the dashboard
must say so. Retain the requested unattended outcome as unfinished until a
real scheduled execution with required authority and destination readback is
proved. Do not call a queued proposal or saved receipt an IC update.

Roster discrepancies and uncertain identities require review; no automatic
roster deletion or conflicting-grade overwrite is newly authorized here.
Perform exact save/reload/readback for each authorized IC write and reconcile
uncertain outcomes before retries. A first real needed write is the test, never
a change to an already-correct grade manufactured for acceptance.

## Operation 2: student feedback, automatic intake or ZIP

**Teacher-facing contract:** use the same Class/section and Lesson title;
choose the revised work; **Generate feedback -> review/save -> Print or download
feedback**. When automatic intake is unavailable, **Upload ZIP** is an explicit
accepted fallback in this same dashboard. The teacher selects the class and
lesson, uploads one archive of the actual student files, and uses the SAME
assessment/save/print path. Do not require a local AI-to-Drive connector or a
separate AI conversation. Do not postpone usable ZIP intake until every folder
or router in Drive has been repaired.

ZIP intake is a requested implementation, not verified existing functionality.
Authenticate it, limit archive/file/expanded size, reject path traversal,
symlinks and unsupported/link-only entries, and keep actual source content in
the existing approved private storage. Preserve uploaded-file hashes and source
provenance separately from Google Drive revision evidence. Re-uploading the
same content must not duplicate grading or model calls. Do not claim an uploaded
file is an exact live Drive revision without verifying that linkage. Imported
work does not establish complete class coverage or its original turn-in time.

Use the current Agenda feedback policy. Match Initial and Revision boxes by
question and actual labeled response fields, not export order. Conceptual and
explanation judgments are separate from completion scores. A blank Initial does
not cancel meaningful Revision credit. Do not invent growth, timing or quotes.

Save reviewed student reports and a readable class packet. Printing and
reopening saved feedback must not bill another model call. Serve the actual
PDF through the authenticated dashboard so direct school-Drive access on the
work laptop is not a prerequisite. Verify nonblank print output, sensible page
breaks and the exact reflection prompt, with teacher-only notes excluded.

Create the teacher summary document in the verified private Drive location
associated with the student work, after checking inherited access. Page 03 is
**Class/lesson -> Open summary document** (and a direct authenticated download
where needed), not an inline paragraph. Preserve document identity and history;
exclude generated reports/summaries from student-submission discovery.

## Source collection: one root, not one assumed folder

The approved **00 ALL TURN IN** root is the default discovery boundary; its
actual ID remains in private configuration. Read the current Agenda Feedback
Prompts and [FEEDBACK_V2.md](FEEDBACK_V2.md). The assignment may occupy loose
files, multiple direct-upload folders and nested Form upload folders. The
TurnIn Map is a locator, not a completeness guarantee or the assignment's ID.

Reconcile actual Forms responses, file references and verified assignment /
student identities across authorized sources. Legacy intake outside the root
is an explicit recovery/coverage item, not permission to expand sharing. Do not
restore the deprecated root. Preserve original links and all earlier/later
submissions; deduplicate references, not distinct versions. No zeros or
all-caught-up claims from incomplete coverage. Repair future routing while
keeping recovered work and ZIP feedback usable; directory tidiness is not the
delivery milestone.

## Screen scope and acceptance

Page 02 contains the three selectors, one Student work table, and the actions
above. Directly below that table, **Download IC Grade Sheet** writes the selected
class/section and lesson to an .xlsx for entering grades in Infinite Campus by
hand: last and first name, both scores, both statuses and the recorded IC posting
status, one row per student, sorted by surname. It reads the records already
loaded — it never assesses, calls an API or rescans work — and an unscored, held
or unreadable checkpoint stays an empty cell, never a zero.
Hide raw scans, changes-since-scan logs, IDs, prompts and developer
controls under Details. Show only concise coverage and actionable student holds.
Do not add more competing panels. Lesson title, not navigation date, selects
an assignment. The confirmed third-meeting final deadline still applies:
actual start is meeting 1; unfinished teaching does not extend it; use the
verified section calendar and America/New_York; keep the 14-day window separate.

First recover the endangered submissions. Then prove one real class/lesson can
produce saved completion scores and printable feedback using automatic intake
OR the ZIP fallback. Verify both necessary IC components through authorized
writes. Test the actual manual button and scheduled path separately. Complete
remaining scope without replacing working components. No fake Form submissions,
student edits or grade changes to manufacture acceptance.

Track honestly: **implemented / tested with fixtures / observed live / teacher
path verified**. The present report of failed posting and printing remains open
until the actual normal controls pass. Windows reading/printing and Windows IC
posting are separate checks; do not block home-Mac delivery on Windows companion
installation or claim it works without checking.

## Budget, ownership and durable continuation

Development stays on the existing paid Claude allowance. Runtime AI calls use
only the approved Anthropic workspace/key through protected secret storage.
Retain the original $5 total pilot/batch budget INCLUDING prior calls and retries,
plus the workspace cap; no purchases, auto-reload, new providers or budget
increases. A scheduled-grading requirement does not replenish or expand that
API approval. Account for actual usage; reuse saved work for verification.

The existing live implementation worker handles the permitted deployment and
browser work. Reconcile older code-only/no-model wording in AGENTS.md with the
teacher's subsequently approved, budget-bounded hosted provider; do not disable
it or use stale prose to commission another architecture. Preserve all actual
security, school-write, human-confirmation and credential boundaries.

Update this entry point in place, retain detailed evidence in approved private
state, and commit tested code without student records or school identifiers.
Do not ask the teacher to commission each next engineering step. A genuinely
new permission, paid scope or unclear teaching fact may require one precise
approval; routine navigation and debugging do not.

Detailed references (read only as needed): [AGENTS.md](../AGENTS.md),
[BROWSER_RUNS.md](BROWSER_RUNS.md), [SUBMISSION_MONITORING.md](SUBMISSION_MONITORING.md),
[FEEDBACK_V2.md](FEEDBACK_V2.md), [AGENDA_SAVE.md](AGENDA_SAVE.md),
[TEACHING_RESULTS_INTEGRATION.md](TEACHING_RESULTS_INTEGRATION.md),
[ROSTER_SYNC_INTEGRATION.md](ROSTER_SYNC_INTEGRATION.md), and
[capability evidence](../config/capabilities.json). Prior checkpoints remain in
Git history; this file is the current delivery contract, not another parallel
handoff or a claim that changing these instructions repaired the application.
