# Submission monitoring and assignment-specific Forms

## Teacher decision and implementation status

The teacher clarified on September 23 that **each assignment has its own Form**.
The earlier statement that the teacher chose one shared Form was incorrect and
must not be used to configure intake. Two assignments in the same week need two
verified assignment-to-Form bindings, even when their uploaded files share a
folder. Neither the navigation date nor a folder name identifies an assignment.

PR #39's read-only Refresh and assignment matching use that contract. This
change does **not deploy or activate** a route, schedule,
registration writer, grading operation or model call. No real stopgap rows are
removed and no test Form is submitted. Preserve the Drive recovery priority in
START_HERE before any separately authorized activation.

## V2 teacher-approved intake and school-year exclusions

An explicitly approved alternate intake does not change an assignment's Form
identity. `HUB_ACCEPTED_FORM_ROUTES` is a JSON list of exact `school_year`,
`class_key`, `week`, `source_form_id` and `assignment_form_id` values. Both Forms
must still be uniquely registered in that Agenda week. Only the existing unique
deck match can route work; mixed or unknown attachments remain unresolved. The
original Form, response ID and submission time stay on each source file. The
source list shows accepted intake as information and infers no source-assignment
zero from it. Acceptance is directional and expires outside the named year.

`HUB_WEEK_EXCLUSIONS` is a JSON list of `school_year`, `class_key`, `weeks` and
`reason` values. For example, a fictional entry may name `2026-27`, `biology`,
`["Wk 01"]` and the teacher's reason. Week labels must exactly match the Agenda;
deck week numbers and cycle numbers are not aliases. The Agenda's Monday proves
the academic year, with July beginning the year. Missing or ambiguous dates and
invalid configuration hold assessment rather than silently permitting it.

An effective exclusion removes that week from morning assessment, blocks manual
scoring before reading decks and prevents inferred missing-work zeros. It keeps
source discovery, historical filing, saved grades and feedback intact. No weeks
are excluded by default; a future-use Form alone does not activate an exclusion.

## Sources and matching

Read complete, bounded response pages from the verified Form for each selected
assignment. Preserve both its Form ID and each original upload file ID.
`respondentEmail` is separate from typed answers. Match students against the
current roster using the collected school email, configured domain/prefix and
explicit legal-name/nickname variants. A typed name, filename suffix, class
period or assignment answer does not establish identity. Ambiguous matches
remain review items.

The assignment-to-Form binding and uploaded-deck lesson evidence must agree.
Do not read one global Form as a substitute for an unresolved assignment Form,
or silently attach a single Agenda Form link to both assignments in a multi-
assignment row. Missing, ambiguous or inaccessible bindings remain explicit
coverage gaps. A student-facing response URL is not proof of a readable editable
Form/API ID.

Each catalog entry carries `form_id`, `form_url`, `form_source`,
`form_mapping_verified` and `form_review_reason`. Use its verified editable/API
ID. Published links can resolve through exact responder-URL equality with a Form
discovered inside the approved root. Otherwise published or shortened links
require an explicit API-ID binding; title similarity cannot supply it. If an Agenda row contains multiple assignments,
its one Form cell does not establish a separate binding for every assignment.

The current weekly row supplies the visible **Turn it in** link in column T and
the raw `_turnInURL` in column U. The reader checks both when populated. Either
column can provide the link alone, but conflicting or invalid populated links
hold the binding rather than silently choosing one. An explicit private mapping
cannot override contradictory T/U links. Refresh's existing coverage details
preserve the exact row-specific repair action so the teacher can verify both
cells before retrying that lesson.

The roster reader accepts explicit Last Name/First Name or First Name/Last Name
headers, headerless two-column rows with a verified column layout, and single-cell
`Last, First Middle (Nickname)` rows. Private configuration can map canonical
sections to the current exact tab title or stable sheet ID. Headerless rows default
to Last/First; an explicit First/Last override requires verified source evidence. It keeps the first student, skips blank rows, excludes
study hall and rejects malformed populated rows. It reads up to 200 rows per
configured class tab; expanding that boundary requires review. Explicit
configured section headings inside a tab switch the section; an A3 block inside
an A2 tab remains A3. Identical repeated section blocks are deduplicated against
the canonical tab; conflicting memberships or unknown headings require review.
No roster tab is rewritten. Roster IDs and section IDs are private
configuration; the previous hardcoded roster file ID was removed.

The reader scans the approved root and the exact upload folders returned by a
verified assignment Form, including nested folders and complete bounded pages.
A verified response can authorize reading its exact original outside those
trees; the reader checks that file without scanning arbitrary parent folders.
Unreadable originals and missing or trashed upload folders remain visible with
a next action. Location alone does not establish identity or lesson matching. **A router copy
with a different file ID is not silently equated to the Form upload**; verified
copy provenance is still required before those copies can be substituted. A
read-only browser check of the four Anatomy originals established upload-folder
activity followed by moves/renames preserving their file IDs. This does not
establish which Form response created those uploads.

The bounded Agenda student-deck catalog is read for lesson candidates. A Refresh
reads the selected class catalog. A week can contain multiple assignments. Private
`HUB_INTAKE_DECK_ASSIGNMENTS` bindings map exact deck IDs to existing registry
keys. They explicitly identify an additional O-column Slides link as an
assignment; an unbound O link remains a resource. New P-column lessons receive
a stable source-derived `discovery:<class>:<hash>` display identity. Repeated
rows with the same deck and Form remain one lesson; distinct decks in one week
remain separate. Existing registry keys require exact deck bindings and are never
reassigned by week or title. Discovery identities are not grading approvals. Lesson matching
requires a unique exact response-marker signature and every unchanged source
question paragraph, with at least three markers and three question paragraphs.
Native Slides are exported read-only as PPTX; uploaded PPTX files use the same
bounded extractor. Markers alone, titles and typed links are insufficient. This
is conservative content evidence, not Drive copy-lineage proof: older, unmarked,
image-only or changed-question decks remain held. Multiple catalog lessons using
the same template remain ambiguous. An unreadable catalog source blocks
uniqueness rather than being silently discarded.

Typed name, period and assignment answers, where configured, are cross-checks only. A mismatched
value (including a free-text assignment or unrelated pasted link) remains a
Needs review item even when email/deck/roster establish routing. Question IDs
belong to their actual Form; do not assume different assignment Forms share
question IDs or require an assignment question solely to recover a binding that
the verified Form already supplies.

## Private runtime configuration

Retain existing root, reader, grading-workbook and Agenda settings. Supply these
through the deployment owner's existing approved private configuration only:

- `HUB_ROSTER_SPREADSHEET_ID`: the intended current roster
- `HUB_SCHOOL_EMAIL_DOMAINS`: comma-separated school domains
- `HUB_EMAIL_USERNAME_PREFIX`: exact prefix, default `s.`
- `HUB_ROSTER_SECTION_IDS`: JSON mapping canonical section names to verified IC IDs
- `HUB_ROSTER_TABS`: optional JSON mapping canonical section names to current
  exact tab titles or numeric sheet IDs
- `HUB_ROSTER_COLUMN_LAYOUTS`: optional JSON mapping canonical section names to
  `first_last_columns` or `last_first_columns` for verified headerless layouts
- `HUB_INTAKE_DECK_ASSIGNMENTS`: JSON mapping reviewed Agenda P/O deck IDs to existing assignment keys
- `HUB_ASSIGNMENT_FORM_IDS`: optional JSON mapping an exact assignment key to
  its editable Form/API ID, for unresolved published links or assignments in a
  multi-assignment Agenda row
- `HUB_ASSIGNMENT_FORM_QUESTION_IDS`: optional JSON mapping each assignment key
  to its own configured `name`, `section` and/or `assignment` question IDs;
  only configured roles are cross-checked, and an assignment question is not
  mandatory
- `HUB_INTAKE_WORKBOOK_WRITES=true`: explicit registration activation;
  absent/other values or `HUB_DRY_RUN=true` leave workbook writes disabled

Verify each assignment's editable Form/API ID and any Form-specific question
mapping in private configuration. New Refresh discovery has no fallback to
`HUB_TURNIN_FORM_ID` or global question IDs. Do not select the legacy shared-Form
route to enable assignment-specific discovery.

Do not commit real values. Reader permissions must cover each selected assignment
Form, approved root, roster, Agenda and candidate teacher/student template decks.
Workbook registration additionally needs the existing authorized Sheets writer.
No new credential, sharing or schedule is supplied by this implementation.

## Dashboard registration and holds

`adapters/intake_records.py` prepares unscored intake rows and missing Assignments
entries. Existing keys are reused through verified bindings; a new class + week key is allowed only when no existing same-week registry conflicts. A
conflicting mapping blocks rather than renaming referenced keys. Required
`learner_key` headers must already exist; the adapter does not alter schemas.

One atomic Sheets batch registers new rows and supersedes only same-file,
same-assignment `intake-` stopgaps that remain identity_unverified, unscored and
not_posted. Reviewed rows and distinct versions retain history. Deterministic
record IDs prevent duplicates. Changed existing evidence raises review.
The writer fresh-reads the workbook, holds a shared intake lease, and rereads
through the dashboard's grading-record reader after writing. It never assesses,
posts IC grades, writes zeros or calls a model. Process work continues through
the existing assessment flow; this change is not an assessment acceptance.

Global unmatched-deck and inaccessible-original items remain visible for review, never
arbitrarily assigned to the class that initiated the scan. Known typed conflicts
are retained in workbook holds. Existing monitor claims, baseline CAS,
first-observed semantics and deadline calculation remain in place. Intake
receipts distinguish dry-run from verified workbook registration; the monitor's
legacy `external_writes=0` field is observation-protocol compatibility and is not
a claim that an activated intake registration made no Sheets write.

## Read-only Refresh submissions

The existing Page 02 **Refresh submissions** action calls authenticated,
same-origin `POST /api/submission-monitor/refresh` with `class_key` and
`assignment_key` (`all`, an existing class assignment, or a discovered lesson). It does not use the
navigation date as an assignment identity. It then rereads the saved grading
workbook. These have separate discovery and workbook-read timestamps.

The reader walks every bounded page and nested folder under the privately
configured approved root, including files absent from saved records. It reads
current references from each assignment's Form when available, verifies current roster identity, and
matches each deck's content separately. A verified Form also supplies its actual
file-upload folder IDs; stale TurnIn Map destinations do not replace them.
Folder membership alone never assigns
all files to one lesson. A2 and A3 retain their verified separate section IDs.
An unavailable assignment Form or catalog is a visible coverage gap; root-visible files
remain review observations, not fabricated Form matches or missing work.
Coverage includes `forms_checked`, `responses_checked`, `form_complete` and a
`form_results` entry for each assignment with its status and reason. One
assignment's readable Form does not establish another assignment's coverage.
Each failed source includes an allowlisted next repair; source structure is
reread before returning the result.

The same Student work table shows new ungraded files, changed sources and
review holds alongside saved assessments. Numeric Drive versions are compared
with stored capture versions. Legacy incomparable revisions remain held unless
a later source timestamp proves a change requiring reinspection; neither a
Drive version nor a timestamp claims the student's writing improved. Distinct
files remain distinct; competing learner/assignment versions require review,
and explicit superseded records are not silently reactivated.

Discovery is a display-only projection. Saved scores, feedback, histories,
export, printing and posting inputs remain the saved workbook records. This
endpoint cannot register rows, grade, call a model, post to IC or advance a
baseline, even when the separate intake writer is enabled. Partial pagination,
inaccessible candidates and unmatched identities never support an all-caught-up
or missing-work claim. Unknown class/lesson rows remain explicitly unmatched.
Unreferenced nondeck Docs/Sheets/PDF files are excluded from the student preview;
unsupported sources named by the Form or saved records remain review items.
Each request has a 180-second read deadline, at most 256 Drive reads and 80 deck
exports, and a 64 MB streamed-byte budget (20 MB per file). Budget exhaustion
returns partial coverage and explicit remaining holds; it never advances a
successful baseline.

## Lesson and section controls

Page 02 uses the selected lesson name and stable assignment key throughout its
assessment controls. The navigation date and due date do not select the target.
The roster reader supplies verified section choices: Forensics shows A2/A3;
Biology and Anatomy automatically use their one verified section. Missing or
conflicting mapping keeps assessment unavailable without hiding saved records.
Known other-section work is excluded, while unresolved new work remains visible
for review and cannot enter assessment or exports.

Refresh preserves a still-valid class/lesson/section selection. Switching scope
or receiving changed-source holds invalidates stale draft/save controls; late
responses cannot populate a different scope. The assessment backend independently
checks the exact saved assignment, section and file identity before capture,
model access or save. Draft caches and assessment claims are partitioned by the
exact source version, class, stable assignment key, section and Notes/Drafts
checkpoint. Draft recovery sends that complete target and revalidates it before
returning a saved draft. Reopening the matching saved draft reuses it; moving from
Notes to Drafts can require a separate assessment even when the source is unchanged.
These controls preserve separate A2/A3 histories; they do not rewrite old rows.

Opening Page 02 with a known exact lesson, or selecting one, starts the read-only
submission check automatically. `GET /api/submission-catalog?class_key=…` reads
only assignment-registry and Agenda metadata to list current lessons, including
source-derived discovery keys before any upload exists. It does not read student
answers, roster rows, Form responses or submitted decks. A restored exact discovery
key is revalidated against the current Agenda by the submission reader.
Only the class, stable lesson key, section and
checkpoint filters survive a tab reload; student data is never stored in browser
storage. Recent previews stay in a bounded memory cache. During a recheck or
failure the same lesson's earlier preview stays visible and is explicitly held
as unverified. Pending requests are deduplicated and late responses cannot
replace the selected scope. All lessons shows saved records plus the previously
read preview; Refresh in that view updates lesson names without starting a broad
student-source scan. Choose a lesson to refresh its submissions. Sign-in failure
removes the private preview cache and saved records from the visible view.

The table counts matched students, current source files and saved records
separately. A readable-file count requires active metadata and a successful
content read with unchanged metadata afterward (`source_availability=active`,
`content_read=true`). Exact trashed or inaccessible saved sources remain listed
as unavailable historical references, separate from readable files. Other
unverified or unread content stays in its own count. A cached preview labels
these observations as previous checks; none imply a current assessment.
Files with no established source link to the selected lesson remain
in an expandable, counted review list. Unverified identity within an established
lesson source remains in the main table; known other-section records stay out.
The saved-record projection accepts canonical `initial` / `final` checkpoints
and the documented legacy `initial_NOTES_checkpoint` as Notes. It preserves the
original value in `assessment_stage_source`. Unknown historical checkpoints stay
visible for review, held and uncounted; free text never becomes Drafts by guess.
Unscored `roster_matched` intake can remain unclassified before its first assessment.

## Open live acceptance and source discovery

PR #39 contains code and synthetic regression tests, not a deployment. The
read-only evidence audit found two different Forensics Week 03 assignments in
one folder and newer files absent from saved dashboard records. Private IDs,
student data, content and detailed reports remain outside the repository.

The previously inspected router trigger target did not establish the editable
Forms for the assignments. Do not treat it as a shared source for those uploads.
Current per-assignment Form-response access, exact private deck bindings,
runtime source permissions and teacher-path acceptance still require verification.
On September 23, the current Agenda-linked Anatomy Form opened under the school
account, but its response view showed zero responses and its editor reported a
missing file-upload folder. The four previously verified uploaded decks remain
evidence; their response linkage is unresolved. The repair prompt was dismissed
without restoring or changing anything. No setup/reset function, Form submission,
folder move, school-record edit or grade post was performed.

Keep issue #37 open until the deployed revision's Refresh discovers the four
Anatomy originals with verified Form/roster matching, shows both Forensics
assignments under separate A2/A3 sections, preserves prior assessments and
versions, and exposes concrete coverage gaps. Report source-access and mapping
results before deployment; verify the new dashboard path after a separately
authorized deployment. Grading remains off. Code tests and a stored preview are
not evidence of completed live school operations.

## Completion coding readiness

Completion grading checks the submitted copy and the selected handed-out deck
before using any cached score. Each must have one readable inventory on slide 1,
all four numeric category totals, matching marker counts/declared IDs, unique
markers, one cycle and a NOTES partner for each DRAFT. A diagnostic marker census
never approves a missing or inconsistent inventory. The reader accepts inventory
text split over lines or text boxes and `OPTIONAL_IDS=-` only when OPTIONAL is zero.
Unmarked copies produce a contextual class/week/lesson hold: embed codes in that
existing copy while preserving answers, then rerun grading. They produce no zero
scores and do not overwrite prior results. Cache keys also bind the Drive version.

The lesson-readiness panel verifies the linked deck's completion codes only; it
explicitly does not certify distributed student copies. Teacher-reference and VT
curriculum checks remain a separate optional deck audit and are not prerequisites
for completion scores. A metadata HTTP 404 is a link/access failure before content
parsing, not evidence that every PowerPoint must be converted. Uploaded PPTX and
native Slides remain supported without modifying source files.

The public status for a confirmed marker/inventory problem or an explicitly
uncoded-copy teacher hold is **Needs embedded codes**. Numerical diagnostics and
the exact class/week/lesson/copy explain the repair; internal issue identifiers
are not teacher instructions. Access or file-reading failures are **Cannot read
lesson**; an unverified decision is **Needs check**, never a claim that codes are
missing. These labels do not weaken any grading hold.

Readiness applies to the whole assignment before preparing missing-work zeros.
Student-work responses carry `form.grade_readiness`; morning entry freshly checks
linked-source coding and the teacher decision before reading scores into an IC
list. One submitted copy marked `deck_not_ready` stops the assignment list. The
IC-receipt endpoint independently repeats linked/copy coding checks before
recording entry, so a cached or forged browser READY value cannot bypass a hold.
These checks do not compute grades, call a model, alter source files, or affect
saved feedback/delivery history. A ready assignment retains the existing verified
missing-work policy.

`HUB_COMPLETION_READINESS_HOLDS` optionally registers exact private descriptors
`{school_year, class_key, week, form_id}`. Their existing CAS decision key is
`completionreadinesshold_<hash(Agenda ID, year, class, exact week, exact Form)>`.
The `hub-completion-teacher-hold/v1` record binds that scope plus `record_key`,
`status`, `reason`, `teacher_action` and `updated_at`. Missing, malformed, unavailable
or mismatched registered decisions fail closed. A held distributed-copy decision
remains held even when the linked teacher deck is READY; Today preserves the
linked-source status/counts and displays the separate teacher action. Discovery,
filing and historical source records remain available.

No browser clearance endpoint exists. A trusted host may change the exact decision
with an expected-version CAS after explicit teacher confirmation and actual copy
readback. A `cleared` record must retain the original scope/reason/action and include
`clearance={basis:"teacher_confirmed_distributed_copies_coded", teacher_confirmed_at,
verified_at, copies:[{file_id, version, status:"READY"}]}` with nonempty verified
copy evidence. Neither a zero-response Form nor a changed teacher deck automatically
clears the teacher's hold. Registration remains in place after clearance.

Source lookup reads the current Agenda's exact linked IDs and verifies the actual
Lesson folder before listing it. An unavailable/incomplete folder read is not an
empty folder. Trash and links outside the current verified folder are reported as
stale pointers, never restored or required. A differently numbered title may be
intentionally taught in the current Agenda week; exact links/folder membership
take precedence over its title. No global lesson-name search selects replacements.
Supported PPTX/native siblings remain available in actual source folders. The
reserved hidden second-deck URL slot and explicit `_deck2URL` headers preserve the
second-deck role. Inventory parsing uses inventory-bearing text boxes, excluding
adjacent lesson prose; an otherwise valid summary on a later slide asks the teacher
to move that existing summary to slide 1, rather than invent missing question IDs.
