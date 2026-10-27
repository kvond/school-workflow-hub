# School Workflow Hub — start here

## Teaching landing, recovery views, work services and records

Normal `/v2/` navigation exposes **Lessons**, with daily date/week navigation
and the existing **Open Agenda** link. Empty or unknown fragments open Lessons;
ordinary landing reads teaching data without loading dashboard run records.
Explicit **Today**, **Student work** and **Records** bookmarks remain available
for ongoing recovery, outside normal teaching navigation. The legacy
`/#feedback` interface, backend routes, shared state and Helper safeguards are
preserved. This UI migration does not retire the student-work backend or tasks.
Recovery views read existing results and link to their canonical records.
Opening a page does not start grading, lesson readiness checks, model authoring,
checklist refresh, packet generation or email delivery.

- **Today** (`/v2/#today`, recovery) shows recorded attempts, actual outcomes, output links and unfinished
  scope. Missing, failed or unconfigured reads stay explicit; configuration and
  a page refresh do not establish that a run completed.
- **Lessons** keeps the existing date/week navigation, Agenda plans, material
  links and guarded editor. Teacher notes keep their current storage and save
  rules. Checklist, folder, packet and diagnostic work belongs outside this view.
  **Prep notes** stays beneath each lesson and reads its canonical Agenda prep
  text/rich links plus explicitly labeled prep materials. BUY/PULL/PRINT and
  Biology Demo/Independent Lab labels are grouped only when present; unstructured
  text stays visible. A single **Prep for next week** section per class uses the
  exact following calendar week's rows and shows their week/date/source links
  and closures. It never skips breaks or shifts dates. Missing/ambiguous rows,
  unsupported link addresses and absent/unverified Independent Form mappings
  stay explicit. Link presence does not establish accessibility or lab release.
  These projections share the existing Agenda read; no new service, tracker,
  readiness job, model call or school write is started.
- **Student work** (`/v2/#work`, recovery) shows original work links and saved completion results with
  their verified saved time. A changed or unverified source cannot inherit
  a saved score, and a new file modification is not a new grading event. Missing
  saved results remain unavailable rather than being replaced by zeros. The
  explicit Helper handoff still prepares its current list through the existing
  guarded path; simply viewing the page does not prepare or enter grades.
- **Records** (`/v2/#records`, recovery) is a directory of the existing Agenda, WorkIndex, feedback checklist,
  teacher reports and operational Slack, plus the operations and model-trial
  spreadsheets. Old `#data`, `#routine` and `#system` bookmarks open Records.

Keep three architectural boundaries clear:

1. Each page has its own narrow reader and initialization. Removing a panel also
   removes its automatic fetch and event binding; hidden controls are not an
   independent service.
2. Existing work services handle source collection, deterministic completion,
   filing, feedback, reports and optional delivery under their own authorization.
   They write inspectable results and receipts. Preserve their guarded entry
   points while callers are migrated; retiring UI is not permission to delete
   source history, private records or live job dependencies.
3. The Hub Helper keeps the existing origin, app-shell paths, exact list and
   acknowledgement schemas, source/destination checks, higher-only entry and
   Save/reload/readback/uncertainty recovery. Dashboard simplification must not
   alter its execution or clear recovery state. The preserved bridge may record
   an already verified Helper readback receipt when the app shell opens; that
   receipt is separate from preparing a list, assessing work or entering IC.

`GET /v2/api/dashboard/records` is the authenticated read-only record directory
and run-summary projection. Existing private settings supply the first three
records. The optional `HUB_OPERATIONS_URL`, `HUB_MODEL_TRIALS_URL` and
`HUB_MODEL_TRIALS_FOLDER_URL` link the new private registers and trial folder.
The operations spreadsheet belongs in the existing Hub Drive folder. A local
XLSX or a configured URL is not proof of Drive placement or a successful read.
Use [the shared run-history format](RUN_HISTORY.md) for Codex and Claude; the
spreadsheet holds the wide-screen view and Slack holds the compact summary.

Saved completion reads use the existing private CAS store and current exact
source/context evidence. The dashboard requests one bounded saved-results list
per Form, sharing discovery and metadata verification across its files. Ordinary authorized scoring can add a saved display
snapshot; failure to retain that optional display snapshot must not change the
Helper's result or grade arithmetic. Display retention is bounded and best effort on a daemon worker; container
shutdown or a storage failure can leave a result unrecorded. The dashboard then
reports no saved result. Reads do not score or export decks. Existing
results without adequate provenance, including unsupported historical cases,
remain explicitly unverified until a normal authorized grading operation records
new evidence. Never backfill a grade by relabeling an old cache entry.

Agenda week dates still come from the explicit Weeks Monday and takeover cells.
Descriptive suffixes do not change week identity; break handoffs are not teaching
weeks, and Wk16b stays distinct. Preserve separate same-week lessons and Forms.
Class week cells may prefix one complete week label with `MP1` through `MP4`
and whitespace (for example, `MP2   Wk 10`). The reader removes only that
explicit display prefix; the dated Weeks row still determines the week and
calendar. Source cells stay unchanged. Arbitrary prose, multiple week labels
and unsupported prefixes do not supply a guessed week identity.
The dated plan and Agenda topic are separate teacher-controlled fields. Returning
to Lessons rereads the selected date while preserving any active editor's basis
and unsaved draft. Explicit material links retain precedence over old hidden
slots; formula cells remain protected by the existing save contract.

Operational source review and repair continue in their separate work bucket.
Batch technical issues in the operations record and existing Slack thread rather
than reinstating a Hub readiness dashboard. Preserve exact source evidence,
originals, teaching content, private records and the before-Helper mutation
cutoff. Existing backend policies below remain relevant to authorized work;
they do not require those controls to appear on dashboard pages.

### Explicit bounded source maintenance

`SourceOperations` still defaults to the weekday before-7:00 a.m. Eastern
source window. A trusted host may pass a separately authorized maintenance
envelope to the same guard; this adds no HTTP endpoint, configuration switch,
schedule, helper action or permission to perform an operation.

Use `SourceOperations.acquire(..., operation_scope=..., maintenance_authorization=...,
read_maintenance_authorization=...)`. The exact envelope schema is
`school-source-maintenance/v1`, with `authorization_ref`, `authorization_revision`,
`status` (`authorized`), `source_scope`, `owner`, `request_id`, `input_hash`,
`operation_scope`, `starts_at` and `expires_at`. All fields are strings. References
and bindings use the existing bounded identifier format; timestamps require
explicit timezone offsets. The interval must stay within one Eastern weekday
and end no later than 10:00 p.m. Its exact operation is one of `roster_sync`,
`student_filing`, `source_repair` or `approved_access_repair`. The input hash must
bind the actual reviewed targets, source revisions and planned mutations;
the authorization does not approve additional access or invent source matches.

The trusted `read_maintenance_authorization(authorization_ref)` adapter must
freshly read the original authenticated teacher decision/current authority
record, verify its attribution and exact scope, and return the complete current
envelope. Echoing a caller's dictionary, returning a boolean, or treating an old
cached decision as current authorization is not a live integration. Keep the
reference, decision contents, targets and school configuration private. The
guard pins a detached immutable envelope and binds its hash into the existing
lease; the same request cannot be rebound to changed authority. Unknown,
revoked, changed, future or expired evidence stops writes, including after the
date rolls over. An expired maintenance guard does not fall back to the normal
morning window; a new normal operation must acquire its own fresh claim.

The original trusted activity callback must still verify the helper and feedback
workers are idle, inputs unfrozen and actual external ownership compatible.
Every native mutation uses `before_write(required_seconds=...)` with its full
bounded write/readback interval. After slow source checks, use
`validate_write_budget(required_seconds=...)` for a fresh authorization,
activity, owner/fence, lease and server-time check without renewal. Neither
operation clears uncertain state or authorizes a competing writer. A reviewed
implementation is separate from actual private authorization, native write
readback and IC acceptance.

## Spreadsheet feedback print link

Authenticated GET `/v2/api/spreadsheet-feedback/print-link` reads the last
trusted publication independently of the retired daily feedback catalog.
Records shows its print link and spreadsheet destination when the
saved PDF still has its verified private location and exact Drive revision.
Partial snapshots keep their report/student counts and visible gap notice.
An unavailable, changed, moved, trashed or publicly shared PDF yields a hold
with no clickable PDF fallback. Its independent read cannot hide other Records
links and does not restore feedback-catalog or readiness calls on Today. No
record means unpublished, not empty intake.

The trusted-host `printable_feedback_link.publish` operation verifies the full
saved PDF hash, byte count, page count, private archive ancestry and metadata
bookends before CAS-publishing counts-only metadata with immutable history.
Use the last-read publication revision; recover uncertain publication by
reading it before any retry. This is not an HTTP write route, an assessment,
an index refresh, a packet builder, or printing/delivery evidence. Existing
extension, helper, environment settings, provider and delivery state are untouched.

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

Student work links original submitted files and saved results. Named source-review
details remain available to independent authorized review workers through the
exact current class/week/Form review route. Today and Records do
not request or display the student source overview or run readiness checks.
The existing feedback checklist is linked from Records. Lesson-source readiness
does not verify submitted student copies or completed grading.

Current source discovery rereads the roster and Agenda before publishing named
details. A failed read or changed roster/Agenda discards those details; it must
not create student-identity questions from a provider failure. The exact
teacher-controlled Form title determines the Photosynthesis exception when a
weekly Agenda row contains several lesson topics; student filenames never do.

A teacher-account Form respondent does not prove that the uploaded artifact is
teacher material: a teacher may submit on behalf of a student absent from a stale
roster. A trusted host may register an exact teacher artifact only after reading
the complete original content and original Form event. Private immutable receipts
bind the Agenda/Form scope, respondent, response, file ID, metadata/version and
full content hash to the operator's content-review evidence. Registration rereads
the proof and verifies the saved private record; there is no browser endpoint.
Discovery rechecks that proof before routing signatures and student grouping.
Only that exact file revision becomes informational teacher material. Mixed
attachments, another response, newer revisions and unregistered work retain their
normal identity/source checks. Provider/state failures preserve all originals and
block assessment as availability failures. No source, roster, grade, sharing or
delivery record is changed; school proof stays in private runtime state, never Git.

An already proven uploaded PPTX can retain its informational teacher-material
classification after it is trashed only through the separate trusted-host
`teacher_material_history.prepare` / `import_verified` contract. The read-only
proposal requires the original immutable receipt and index, the same current
Agenda/Form context and exact original response, and a full binary download
whose SHA-256, MD5 and size match that receipt. The only permitted metadata
changes are an advancing numeric Drive version and `trashed: false` to `true`;
ID, parent, name, MIME, modification time and checksums must remain identical.
Native Slides, restored files, changed bytes/provenance and unavailable reads
do not qualify. This retains an established classification; it cannot decide
that a different upload is teacher material.

Publication uses an actual `SourceOperations` instance whose input hash is the
exact proposal hash and whose trusted activity callback verifies the existing
shared source ownership. The existing `FirestoreState.commit_guarded` adapter
acquires its token before the guard. Each one-shot CAS reserves 180 seconds for
final Agenda/event/metadata/store checks and 120 seconds for commit/readback;
an exact final owner/fence/expiry and server-time cutoff check follows those
reads. It neither renews a lost claim nor writes outside the guard's default
before-7:00 window or its exact separately authorized maintenance interval.
No source or old receipt/index is edited. A pending private intent blocks an
uncertain publication from retrying; an exact committed receipt, binding and
intent may only be recovered by readback. A pending-only result requires
coordinator investigation, never automatic clearing or replay.

The immutable binding lets discovery recheck the complete bytes and original
proof before retaining only that exact attachment as information. Later
restoration, revision or provenance changes stay held rather than falling back
to ordinary classification. All staged attachments remain unchanged if any
batch bookend fails; other files in a mixed response retain their normal source
and identity checks. No HTTP writer, job, helper change, student/grade authority,
empty-intake inference or partial-coverage success is introduced. Code/tests
alone do not publish a disposition or release any live assessment hold.

A typed read-only Google timeout (`GoogleError.reason == "read_timeout"`) during
review discovery or the shared feedback catalog is repeated once, only within the
first 20 seconds of the request. A timeout that remains returns `read_failure`
(review) or `source_check.status == "timed_out"` (feedback). The independent
operation records that availability limitation; it does not count the scope as
read or draw missing-work, identity, new-feedback or printed conclusions.

Current/upcoming Agenda readiness checks each registered Turn-in Form against
its own exact teacher-source collection and current folder metadata. Separate
Forms do not borrow decks, native-read access or a teacher hold from one another.
After reading teacher contents, the existing metadata/folder bookend must still
match. The display lists each Form's readiness separately and identifies verified
sources omitted from the Hub's current Agenda lesson links as a separate link
discrepancy. This read-only check does not change scoring, assignment selection,
embedded codes or distributed student copies.

### Exact source roles in readiness

A trusted host may import a teacher-confirmed role through
`readiness_source_roles.import_verified` into the existing private CAS store.
There is no HTTP writer, new setting or schedule. The record binds the current
Agenda/year/class/week/row, complete Form/material fingerprint, exact linked
source metadata/content and a complete stable folder-membership fingerprint
where applicable. A source may be a literal Agenda material or an exact direct
member of one currently linked folder; titles, shortcut targets and recursive
or future descendants cannot establish that membership. Imports require an attributed instruction hash, CAS and saved
readback, with immutable history. Changed scope, metadata, membership, policy
or unavailable reads retain ordinary checks; another known folder cannot replace
an unreadable Agenda pointer.

The axes are independent: `assessment=ungraded` skips only that material's VT
code inventory; `modality=paper` does not imply ungraded. Independent readiness results
show the exact exemption as **VT codes not required**, never as a passed code
check. Other graded sources in the same row/Form retain their checks. Paper
materials keep access, links, print, supply and content-readability checks. An
exact verified paper-only folder needs no VT deck or digital submission Form;
a missing or unreadable folder remains held. A role does not change assessment
eligibility, feedback, Forms, submissions, grades, delivery marks or permissions.
Private roles and source IDs never belong in GitHub task text.

## VT readiness repair loop

Katherine's October 18 workflow starts with a fresh read of the current exact
Agenda lesson, Form and teacher-source checks. Keep separate assignments scoped
to their own sources under [Lesson pages and turn-in folders](#lesson-pages-and-turn-in-folders).
For a newly observed problem, first find its existing GitHub task; update that
task or create one bounded Agent task with file claims under
[Parallel agents](PARALLEL_AGENTS.md). GitHub records the reproducible technical
problem and verification, without student data or private source IDs.

Inspect and repair the existing native VT deck within the authorized scope,
using the current `vt-bio-skill` shipping/renewal standards (§16–17) and
`renew-lesson-slides` instructions rather than inventing a second standard.
Preserve original lesson material, graphics/media, source IDs, student answers,
submission dates and assessment history. Then verify the existing lesson links
and connections (Agenda, lesson page, Form and source routing) under their
existing setup and access authorizations.

Identity, teaching-content or new-access decisions go in one compact `#kathy`
thread with exact class/lesson/week, affected scope, concrete reason, verified
links and slide numbers when available, and the smallest next action. State
what is unverified. This routing applies to a decision that needs Katherine;
it does not turn every routine repair into a new approval step.

After repair, reread the native deck and refresh the Hub's exact lesson checks.
Record the actual readback and any remaining hold. Marker/inventory `READY`
confirms only that bounded code check; full curriculum/renewal release still
requires the existing shipping standard, content, media, links and access
verification. This loop adds no automation, schedule, grading permission or
blanket permission to publish decks.

## Weekly lesson-readiness preflight

The independent weekly-preflight service can check upcoming weeks against
the Weeks-tab calendar, Agenda links, lesson sources, deck codes, print lists
and supplies and build the existing private Monday packet. It no longer belongs
on Today. Current activation and live acceptance require their own evidence;
this dashboard change creates no schedule. See
[Weekly preflight](WEEKLY_PREFLIGHT.md).

## October 8 student records

The teacher-facing Drive layout uses private class/section/student folders
with dated submitted-work copies and saved student feedback. The original
Form uploads retain their IDs and intake locations. A teacher work index joins
class, lesson, student, submitted work and feedback without requiring folder
searches. Refresh retains unkeyed manual historical rows in their existing Sheet
positions, including notes, formatting and validation; they are not counted as
verified student records. Generated records refresh around them, while duplicate
keys and missing keyed records with teacher notes remain held. Teacher-only reports have
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

## Student science-writing presentation

The trusted science-writing author includes every verified submitted original
in the source list, including pieces without a writing observation. Each exact
source revision gets a point in all four writing charts; an unassessed piece
remains an explicit gap. Multiple submissions to the same lesson remain separate
samples. Only verified original submission times may order same-day samples.
Never infer chronology from lesson order, filenames, edits or feedback dates.

Use the actual author pipeline `science_writing_display.prepare_presentation`
for corrected or later presentations of an existing report. It selects the
latest actually reviewed source, requires teacher-reviewed practice, prepares
the immutable replacement payload and renders its PDF. The existing imported
worksheet author also accepts `cer_practice` and produces the same current CER
format; legacy report reproduction stays unchanged.

Use `science_writing_display.context` with teacher-reviewed CER practice tied
to the latest actually reviewed lesson. Supply one relatively easy question,
exactly three scientifically valid evidence statements, and the actual lesson
reference. The function defaults to latest-work narration. For the teacher's
explicit initial correction only, `comparison=True` describes both adjacent
reviewed pieces and explains changed levels using their saved quotations and
dimension-specific explanations. These are observations of different tasks,
not a diagnosis of student ability. The charts retain all source history in
either narrative mode, with additional readable pages when necessary.

The final page provides separate Claim, Evidence and Reasoning writing lines.
The given facts are practice material, never attributed student writing. A paper
CER is not completed or rated until its authentic returned artifact is reviewed.
Keep the saved target, quotations, levels and older PDF immutable. File a new
presentation version through the existing private archive with exact predecessor
proof and readback; do not alter Printed or Handed-to-student state.

## Automatic teacher email for new print-ready work

The private `print_ready_email` outbox connects verified new individual feedback,
science-writing reports and current work contracts to the existing weekday
teacher worker's school Gmail connector. Cloud Run has no Gmail credential and
does not send mail itself. Keep the existing schedule; do not add a sender job.

Activate once using the explicit allowed school recipient, the preserved complete
initial catalog and its exact fresh metadata bookend. All historical file/report
versions and saved times at or before that baseline remain excluded. A separately
verified manual email may seed its exact logical report, private file, student,
PDF digest and actual raw school Sent message. Identical report aliases do not
create another email event. Never use Printed/Handed or unprinted history as an
email selector. Original assessments, copies and delivery marks remain immutable.

Authenticated same-origin JSON POST `/v2/api/feedback/email/outbox` supports:

- `activate`: `recipient`, `manual_seed`, `initial_baseline` (all explicit).
- `prepare`: no additional fields. This recovers new events and prepares one
  private incremental PDF of up to 200 reports within the existing 10 MB bound.
  Oversized runs use successive bounded parts; a later A+B packet emails only B
  after A has an exact Sent receipt. Teachers normally receive one attachment.
- `begin_send`: `id`. Only its first exact successful CAS reply grants
  `send_allowed_once`; retain its unique `claim_token`. A lost reply is held,
  never automatically reclaimed, including on a later run.
- `confirm_sent`: `id`, `claim_token`, `message_id`, `labels`, `raw_base64` from
  the actual Gmail raw Sent readback. The host validates the school sender and
  recipient, unchanged subject and exactly one decoded matching PDF attachment.
- `hold_uncertain`: `id`, `claim_token`. Never retry an uncertain provider send.

Authenticated GET of that outbox returns counts, the exact active reservation
and manifest, and bounded manual-seed proof. Batch replies include `pdf_url`,
original source dates/revisions/hashes, `expires_at` and the unchanged MIME
attachment name/subject. GET `/v2/api/feedback/email/pdf?id=...` reads the private
verified batch PDF. The authorized host may call these same module functions
with the existing approved service identity; never forge IAP, copy browser
credentials or manufacture a successful receipt. Use the actual school Gmail
connector profile, no CC/BCC, one send, then full actual Sent readback. Check the
real UTC clock against `expires_at` immediately before calling Gmail.

Missing-work contracts must still be open and current, with their complete plan
published and checked time at or before now, and expiry strictly after now.
An expired/closed prepared batch may be retired only while CAS proves no Gmail
send was fenced; unrelated valid sources can then form a new packet. A reserved
or uncertain send remains fenced. Source availability, identity or private
readback failures stay explicit holds. Multi-chunk Sent confirmation may be
reconciled against the same exact message without another provider call.

Independent feedback records keep school-email counts separate from paper states.
Hub Print/download remains the fallback. Enabled configuration and a schedule
are not proof of a first unattended send; report actual mail acceptance separately.

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

October 17 feedback direction: Biology keeps the approved source-comparison
format with four writing-area graphics and source-based practice. Anatomy and
Forensics keep concise student reports; their next feedback question should
further the student's actual revision using the supplied lesson relationship,
contrast or transfer case. The course-specific provider guidance applies to new
work only. It does not regenerate saved reports, create another charge for an
existing submission, add rubric criteria, or change any assessment or delivery
record. Deployment and live acceptance of that guidance remain separate checks.

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

**Work-service contract:** an independently requested or already-authorized
scheduled grading operation supplies the verified class/section, lesson and
component scope. The Student work dashboard reads its saved results; it does
not start grading on page load. Reuse the existing deterministic operation
and guarded Helper handoff rather than a second grading implementation.

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

**Independent work-service contract:** feedback uses the exact class/section,
lesson, selected work and current policy through the existing assessment, review,
save and print workflow. Student work retains the existing closed per-Form
feedback backup panel. Opening the page does not generate or save feedback;
the explicit Generate/reuse and Save controls retain the existing guarded
feedback routes and source/identity holds. Keep any future ZIP intake in its
own work bucket, preserving the same assessment/save/print contracts.

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

The October 18 teacher update requires a **Lesson review for tomorrow** at the
end of every completed class/section lesson review, even when zero, one or only
a few new individual student feedback sheets are produced. It uses the actual
verified reviewed evidence, not the print selection. Include interesting student
ideas, supported misunderstandings, short whiteboard **Write / Ask / If discussion
stalls, say or show** prompts, and lesson-specific questions about evidence,
assumptions or ways of knowing. Keep partial coverage explicit. Use the existing
private teacher-summary document and report index, large text and one idea per
line; no extra Today panel or student packet. Follow the authoring and saved-file
verification rules in [Class summary contract](CLASS_SUMMARY_CONTRACT.md).

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

### Lesson pages and turn-in folders

Schoology pages and storage folders organize lessons; they are not assignment
identities. Existing assignments may share a weekly turn-in folder without
being merged, and sharing that parent alone does not require moving files.
For new setup, use one Schoology assignment page and turn-in folder per lesson.
A second assignment may be listed on the same lesson page, with each assessment
retaining its exact Form binding or explicitly approved combined-component
binding. Do not split a working page or create another folder just because a
lesson has a second assignment.

When the schedule changes, reuse the lesson's page, folder, Form and source IDs;
update the dates and links and freshly verify the exact bindings. Do not recreate
submissions, replace original submission dates, renumber grade history or infer
new ownership from a week label. This setup policy does not establish that a
changed live mapping has already been verified.

Readiness, scoring and native feedback resolve teacher-source ownership for the
exact Form before reading lesson sources. Separate Forms in one Agenda row do
not inherit each other's decks. Existing exact teacher-source declarations or
the validated deck-to-assignment-to-Form mappings supply that ownership; labels,
link order and a shared week do not. Missing or conflicting ownership returns a
Form-specific assignment-setup action. A verified sibling can proceed without
reading another Form's inaccessible teacher. Mapped source metadata and consumed
teacher versions are checked again after reads. A single unconfigured Form keeps
its existing row/folder source rules, and explicitly combined assessments retain
their declared component sources. This does not register new assignment keys,
write grades, or alter original submissions and delivery records.

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

The simplified Student work page selects class, week and section and displays
original submitted files and verified saved completion grades. Its newly graded
filter uses the saved-result time, not the source modification time. Explicit
Helper preparation uses the current canonical list and leaves entry/readback to
the unchanged companion. The dashboard does not generate a grade sheet or run
grading as part of opening this view.

Keep raw scans, IDs, prompts and developer controls out of the normal view.
Lesson and exact Form identity, not navigation date or title similarity, select
an assignment. The confirmed third-meeting final deadline still applies to the
independent grading operation: actual start is meeting 1; unfinished teaching
does not extend it. Use the verified section calendar and America/New_York and
keep the 14-day window separate.

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

October 22 ownership: Codex executes and integrates the current repair, obtains
independent exact-commit and combined-flow review, and verifies the established
deployment. Claude is consulting-only when Katherine requests it. Preserve
other writers' branches and reconcile their actual claims; this instruction
does not disable an external worker or change a schedule by itself. The current
Chrome helper remains the only IC writer and is not edited, reloaded or
reconfigured as part of this repair.

Before repeating a teacher question, read the existing operational Slack thread,
private checklist and linked issue for the exact decision and any later change.
Accepted joint-work attribution survives a technical version mismatch; verify
the frozen copy proof rather than asking for the teaching decision again.
Keep recurring technical failures on their existing issue through regression,
review, deployment and native readback. Report implementation, fixture tests,
deployment and live execution separately; a merged repair is not a repaired
student record or a confirmed IC save.

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

When an exact Form appears in multiple Agenda contexts, the morning catalog and direct list requests require exactly one valid current-year `HUB_FORM_TEACHER_SOURCES` declaration across those contexts. The declaration selects its existing nonfuture class/week/Form context; row order, latest week, names and titles do not. Missing, multiple, malformed, stale or excluded contexts hold that Form while independent assignments continue. Excluded and future duplicates are examined before filtering, so a copied link cannot resurrect an excluded lesson. A held catalog entry is diagnostic only; its direct list refuses before intake, scoring or missing-work rows. Original Agenda rows, source files, Form events and history remain unchanged. Current context and the complete candidate context evidence join the existing list fingerprint; old direct requests cannot bypass the selection. Registration requires fresh exact Agenda/Form/source/content/parent proof and independent review, then normal source/readiness/identity and IC mapping gates still apply. This does not grant grading authority to a teacher deck or change the Chrome helper.

`HUB_ASSIGNMENT_EXCLUSIONS` supports the narrow authorized `week01_cases` purpose. Each entry has exactly `school_year`, `agenda_id`, `class_key`, `week`, `form_id`, `purpose`, and `reason`. It applies only to a unique current Forensics Week 01 case Form corroborated by its meaningful teacher-controlled case/Sheppard label (or exact case topic when the label is a generic Form URL/Turn in). A Lab Safety label, other class/week/year, ambiguous current Form, or whole-week purpose cannot qualify. The current catalog and helper schema remain unchanged; the list returns the existing `excluded_week` status before any source discovery or assessment. Manual scoring/entry stamps and source-review text honor the same rule. Original files, real Form events/dates, already saved grades and teacher delivery marks remain untouched. The separately authorized `reasoning_dealt_with` purpose skips only a unique current Forensics Week 03 Form in 2026–27 whose meaningful Form label is exactly Inductive and Deductive Reasoning (allowing the Turn in suffix). A raw Form URL, generic label, mixed topic, unavailable Form, other year/class/week or duplicate scope cannot establish this exclusion. Current source review returns non-actionable `excluded`; normal catalog/list schemas, Notes-only destination mapping and all original/Saved history remain unchanged. Activating the private declaration requires deploying this policy support first.

These declarations do not select a latest original, establish a missing Form event, clear genuine identity/history holds, change completion arithmetic, borrow a new teacher's absent question denominator, or certify every lesson graded. Separately verified manual Saved source decisions and no-entry-needed classifications require their own fresh exact original/student/IC component evidence.


## Review-only source-history acknowledgement

`HUB_SOURCE_HISTORY_DISPOSITIONS` contains private exact pointers with only `class_key`, `week`, `form_id`, `record_key`, `native_targets`, and `instruction_sha256`. A trusted host may import an immutable `hub-source-history-disposition/v1` through `source_history_dispositions.import_verified`, using absent-only CAS and exact readback. There is no browser writer. The complete receipt binds current Agenda/year/Form/source rules and IC map, unique current roster identity plus actual native section/component IDs (a person ID only if actually observed) and an exact-original school-email allocation attestation bound to its literal metadata and teacher instruction, all observed candidate history, enumerated issue kind and full reason hash, exact original metadata/content/identity hashes and actually observed original Form fields (absent fields stay absent), teacher instruction, and actual two-component 10-point Saved verification evidence/time. A changed source, new original, changed event/roster/route/Agenda/map, unknown identity, provider failure, or unrelated issue keeps its normal review notice. No source bytes are read or work scored on the review GET.

Known former-student originals may appear under `former_student_work` as informational history when the configured private student archive already has an exact teacher-confirmed former-student binding, immutable original revision and filed-copy proof, tagged private containers, and fresh metadata/record readback. This reader never constructs an archive run or writes state. It excludes any canonical name, school email, or available native-ID conflict with the current complete Hub roster, which the review endpoint bookends; a previous withdrawal confirmation is not represented as a fresh native IC scan. Changed or missing proof retains the original review; unavailable reads issue only an availability limitation. Only unmatched collection/identity notices can move to this informational section, with original/copy links and explicitly unverified slide numbers. Assessment, current identity, source selection, grading, delivery, and original permissions are unchanged.

Only exact resolved history/route/week notices can become `retained_history`, shown separately from current actions with original links and the last verification time. `manual_saved_history` is a durable acknowledgement of the teacher's completed allocation, saying “Saved entry verified [time]; source history retained.” It certifies no current IC value. `maximum_saved_as_of` requires both values exactly 10/10, exact current Notes/Revision mappings, and a native readback no older than 30 minutes on the same Eastern date; the display says no new entry was needed as of that read, never that current IC was independently reread. The trusted host must independently supply actual native/source proof; a hash-shaped value alone is not permission to import an invented capture. The exact native target pair is supplied independently in the private pointer/import basis and compared to the immutable receipt with the existing target validator; maximum assurances also require the current grading map. A teacher-confirmed manual allocation may differ from a wrong-intake Form’s grading map, without changing that map. An expired maximum assurance does not establish chronology or a latest Form event.

These acknowledgements never change candidate/revision selection, wrong-Form acceptance, grading eligibility, source-readiness holds, own-coded denominators, scores, IC comparisons, helper lists/reports, ledger stamps, scheduling, feedback, or Printed/Handed fields. Assessment/BANK/response concerns, identity conflicts, unavailable/trash sources, unverified routes, and lesson-wide provider problems remain actionable. Normal helper destination comparisons still apply before any entry. This capability alone does not complete the whole-school grading backlog or source-routing task.


An intentional partial `HUB_IC_ASSIGNMENT_MAP` (for example, one canonical Notes assignment per section and no Revision assignment) projects only the morning helper's entry list to explicitly mapped components. It does not change the original assessment, feedback, source selection or stored grades. Exact target schemas, unique section/component bindings and section identity consistency remain required; any score in an unmapped section keeps the mapping refusal. Missing-work zero rows are omitted for partial maps, while an actually measured zero remains an eligible score. Held and excused rows stay visible. Only targets with measured mapped values are sent, and the fingerprint binds both the complete source assessment and the projection. Full Notes/Revision maps retain their existing list and fingerprint behavior. Empty/unconfigured mappings do not establish partial-map authority or bypass downstream validation. A verified partial comparison records the existing counts-only receipt, never the ordinary whole-Form Entered stamp; `hub-ic-list/v1`, target, result and acknowledgement schemas are unchanged.

A review-only `manual_saved_history` receipt may acknowledge one Saved component only when its exact native destination equals the current Form's explicit single mapped component for that section. This records the inspected Saved value and time; it does not certify another component, current IC state, selected revision, intake, grading eligibility, or a whole-Form entry. Existing two-component manual receipts remain unchanged. `maximum_saved_as_of` still requires both Notes and Revision at 10/10, exact current targets, and the existing same-day 30-minute native-proof window.
