# School Workflow Hub — start here

## Teacher review context

Every request to review submitted work must identify the class/section,
lesson and week/cycle, affected student or lesson-level scope, concrete reason,
verified source links, exact verified slide numbers and the smallest next action.
State plainly when identity, links or slide numbers cannot be verified. A count
or a generic "check by hand" label is not enough; the teacher should not have
to search folders to understand the request.

The authenticated review-context read uses the exact current Agenda class,
week and Form binding. Named details are current source evidence, not a join
to historical receipt counts or lesson-title similarity. Reading this context
must not score work, invoke a model, save a grade or persist student details in
extension storage. Keep morning receipts counts-only and the IC list contract
unchanged. Exempt flags and zero-denominator N/A components do not create a
teacher review. Not-yet-submitted work is waiting for submissions.

Today and Routine use one review overview request. Discovery reuses one roster
snapshot and one scan per class/week, then rereads the roster and Agenda before
publishing names. A failed read or changed roster/Agenda discards named details;
it must not create student-identity questions from a provider failure. The exact
teacher-controlled Form title determines the Photosynthesis exception when a
weekly Agenda row contains several lesson topics; student filenames never do.

## October 8 student records

The teacher-facing Drive layout uses private class/section/student folders
with dated submitted-work copies and saved student feedback. The original
Form uploads retain their IDs and intake locations. A teacher work index joins
class, lesson, student, submitted work and feedback without requiring folder
searches. Teacher-only reports have
separate private class folders. Source uploads and Form trees remain intact;
only verified empty unused folders receive `~delete `. See
[Student records](STUDENT_RECORDS.md) for runtime configuration, identity
requirements, automatic filing, private readback and quota/coverage holds.

The Biology DTGSS workbook presents whole-class counts with explicit evidence
dates, lesson titles and assessed denominators. A separate private Biology
analysis workbook contains student-level vocabulary, conceptual-answer and
evidence observations. The opening guide explains each tab. Saved dated daily
feedback PDFs are separate from these teacher reports; the Hub source list
and print packet must use the same duplicate suppression and current source
revisions. Made, Printed and Handed to student remain separate states.

## October 5 verification continuation

The teacher approved the existing OpenAI connection and a one-page ninth-grade
feedback draft. V2's Student feedback path now generates, reviews, saves to the
existing private Hub store and serves an authenticated PDF. It reads the live
Agenda policy and requires an exact registered lesson, current source revision
and school-email roster match. It does not write grades, delivery states or
Drive permissions. Provider responses are reserved and cached durably so an
interrupted request cannot automatically bill twice. A held response requires
review; it is not silently regenerated. ZIP intake and longitudinal growth
remain separate, unverified work. Deployment and a real saved/printed draft
must still be verified separately from these implementation tests.

Morning status must show incomplete or failed receipts as unresolved. An active
schedule and a ready browser companion do not establish a completed run.

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

The teacher clarified on September 23 that the Week 04 Forensics PPTX files
were converted to native Slides and duplicate PPTX files were intentionally
deleted. Preserve the current native Slides and do not restore those duplicates
as incident recovery. Exact saved source IDs remain historical evidence;
Refresh must show an unavailable original as held without restoring it or
silently transferring its assessment to a replacement file.

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

## Assignment-specific Form intake — issue #37

The teacher clarified on September 23: **each assignment has its own Form**.
The earlier claim that the teacher chose one shared Form was incorrect. Resolve
each Form to its exact assignment, then verify collected email, uploaded-deck
evidence and the roster. Two assignments in the same week retain separate Form
bindings and assignment keys even when their files share a folder. Typed
answers are optional configured cross-checks; a typed assignment question is
not required. Missing or ambiguous Form bindings remain coverage gaps, with no
fallback to a global Form. The implementation and acceptance boundaries are in
[SUBMISSION_MONITORING.md](SUBMISSION_MONITORING.md); private runtime activation,
current Form-response access, exact private deck bindings, stopgap replacement
and dashboard acceptance remain unverified. PR #39 also changes Page 02 Refresh
to read new/changed source files into the existing Student work table without
school writes or grading. Refresh follows each verified Form's actual upload
folders and exposes source-specific repairs; new Agenda lessons receive stable
discovery identities without altering saved assignment keys. Embedded A3 roster
blocks remain A3; multiple lessons in one week stay distinct. Page 02 uses lesson
names throughout, shows A2/A3 only for Forensics, and uses each other class's one
verified section automatically. Fresh target checks protect drafts and saves from
stale lesson/section selections. Saved assessment histories remain intact. This
is not a deployment or recovery-completion claim.

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

## Retired Biology answers spreadsheet

On October 2 the teacher removed the redundant “Student answers — Biology”
spreadsheet. Do not restore or recreate it. The Hub no longer searches for it,
writes final answers to it, or runs an archive sweep when opened. Retired archive
POST endpoints reject stale clients without requesting Google read/write access.
An old `HUB_BIOLOGY_ANSWERS_SPREADSHEET_ID` value has no effect. Individual feedback,
private growth records, class summaries and the class-matrix checklist continue
under their existing contracts.

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

## Weekday Chrome-helper build

The September 30 handoff's opt-in 7 a.m. path is implemented with synthetic
acceptance only. See [the handoff](HELPER_AUTOSAVE_HANDOFF.md) and the
[browser companion README](../browser-extension/README.md). Runtime deployment,
verified Form/section/component-to-IC bindings, extension update and the first
live morning remain pending. Roster sync is still status-only. The build does
not activate a schedule, change grades or verify the reported September 30 run.

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


### Exact Form teacher sources and Week 01 case-only assessment policy

Private host configuration `HUB_FORM_TEACHER_SOURCES` may bind one unique current Agenda Form to its actual existing teacher source collection. Each entry has exactly `school_year`, `agenda_id`, `class_key`, `week`, `form_id`, `source_folder_id`, and `teacher_ids` (one to four unique IDs). The source parent can be an existing Shared Drive root; no ownership-changing move is required. Both the parent and every exact teacher must be readable and explicitly untrashed, with literal ID/type/parent/version metadata bookends. If files.get omits parents, one complete bounded exact-name/MIME/declared-parent query must prove the exact declared teacher ID; it cannot enumerate a root, accept pagination, or override an explicit foreign parent. The same metadata and membership are rechecked after teacher template reads, and the consumed template metadata must agree before readiness or scoring is accepted. Metadata existence, titles, a student's filename, and a replacement found elsewhere cannot establish this binding. Discovery, completion readiness, teacher selection, and current cached-review checks consume the same declaration. A combined assignment must declare exactly the same component teachers and parent; a shared Agenda row's visible second link cannot lend another Form's teacher to it. Unconfigured Forms retain their original Agenda-source resolution.

`HUB_ASSIGNMENT_EXCLUSIONS` supports the narrow authorized `week01_cases` purpose. Each entry has exactly `school_year`, `agenda_id`, `class_key`, `week`, `form_id`, `purpose`, and `reason`. It applies only to a unique current Forensics Week 01 case Form corroborated by its meaningful teacher-controlled case/Sheppard label (or exact case topic when the label is a generic Form URL/Turn in). A Lab Safety label, other class/week/year, ambiguous current Form, or whole-week purpose cannot qualify. The current catalog and helper schema remain unchanged; the list returns the existing `excluded_week` status before any source discovery or assessment. Manual scoring/entry stamps and source-review text honor the same rule. Original files, real Form events/dates, already saved grades and teacher delivery marks remain untouched.

These declarations do not select a latest original, establish a missing Form event, clear genuine identity/history holds, change completion arithmetic, borrow a new teacher's absent question denominator, or certify every lesson graded. Separately verified manual Saved source decisions and no-entry-needed classifications require their own fresh exact original/student/IC component evidence.


## Review-only source-history acknowledgement

`HUB_SOURCE_HISTORY_DISPOSITIONS` contains private exact pointers with only `class_key`, `week`, `form_id`, `record_key`, `native_targets`, and `instruction_sha256`. A trusted host may import an immutable `hub-source-history-disposition/v1` through `source_history_dispositions.import_verified`, using absent-only CAS and exact readback. There is no browser writer. The complete receipt binds current Agenda/year/Form/source rules and IC map, unique current roster identity plus actual native section/component IDs (a person ID only if actually observed) and an exact-original school-email allocation attestation bound to its literal metadata and teacher instruction, all observed candidate history, enumerated issue kind and full reason hash, exact original metadata/content/identity hashes and actually observed original Form fields (absent fields stay absent), teacher instruction, and actual two-component 10-point Saved verification evidence/time. A changed source, new original, changed event/roster/route/Agenda/map, unknown identity, provider failure, or unrelated issue keeps its normal review notice. No source bytes are read or work scored on the review GET.

Known former-student originals may appear under `former_student_work` as informational history when the configured private student archive already has an exact teacher-confirmed former-student binding, immutable original revision and filed-copy proof, tagged private containers, and fresh metadata/record readback. This reader never constructs an archive run or writes state. It excludes any canonical name, school email, or available native-ID conflict with the current complete Hub roster, which the review endpoint bookends; a previous withdrawal confirmation is not represented as a fresh native IC scan. Changed or missing proof retains the original review; unavailable reads issue only an availability limitation. Only unmatched collection/identity notices can move to this informational section, with original/copy links and explicitly unverified slide numbers. Assessment, current identity, source selection, grading, delivery, and original permissions are unchanged.

Only exact resolved history/route/week notices can become `retained_history`, shown separately from current actions with original links and the last verification time. `manual_saved_history` is a durable acknowledgement of the teacher's completed allocation, saying “Saved entry verified [time]; source history retained.” It certifies no current IC value. `maximum_saved_as_of` requires both values exactly 10/10, exact current Notes/Revision mappings, and a native readback no older than 30 minutes on the same Eastern date; the display says no new entry was needed as of that read, never that current IC was independently reread. The trusted host must independently supply actual native/source proof; a hash-shaped value alone is not permission to import an invented capture. The exact native target pair is supplied independently in the private pointer/import basis and compared to the immutable receipt with the existing target validator; maximum assurances also require the current grading map. A teacher-confirmed manual allocation may differ from a wrong-intake Form’s grading map, without changing that map. An expired maximum assurance does not establish chronology or a latest Form event.

These acknowledgements never change candidate/revision selection, wrong-Form acceptance, grading eligibility, source-readiness holds, own-coded denominators, scores, IC comparisons, helper lists/reports, ledger stamps, scheduling, feedback, or Printed/Handed fields. Assessment/BANK/response concerns, identity conflicts, unavailable/trash sources, unverified routes, and lesson-wide provider problems remain actionable. Normal helper destination comparisons still apply before any entry. This capability alone does not complete the whole-school grading backlog or source-routing task.


An intentional partial `HUB_IC_ASSIGNMENT_MAP` (for example, one canonical Notes assignment per section and no Revision assignment) projects only the morning helper's entry list to explicitly mapped components. It does not change the original assessment, feedback, source selection or stored grades. Exact target schemas, unique section/component bindings and section identity consistency remain required; any score in an unmapped section keeps the mapping refusal. Missing-work zero rows are omitted for partial maps, while an actually measured zero remains an eligible score. Held and excused rows stay visible. Only targets with measured mapped values are sent, and the fingerprint binds both the complete source assessment and the projection. Full Notes/Revision maps retain their existing list and fingerprint behavior. Empty/unconfigured mappings do not establish partial-map authority or bypass downstream validation. A verified partial comparison records the existing counts-only receipt, never the ordinary whole-Form Entered stamp; `hub-ic-list/v1`, target, result and acknowledgement schemas are unchanged.

A review-only `manual_saved_history` receipt may acknowledge one Saved component only when its exact native destination equals the current Form's explicit single mapped component for that section. This records the inspected Saved value and time; it does not certify another component, current IC state, selected revision, intake, grading eligibility, or a whole-Form entry. Existing two-component manual receipts remain unchanged. `maximum_saved_as_of` still requires both Notes and Revision at 10/10, exact current targets, and the existing same-day 30-minute native-proof window.
