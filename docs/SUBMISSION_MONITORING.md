# Submission monitoring and shared-Form intake

## Teacher decision and implementation status

Issue #37 selected **A: retain the shared Form students already use**. The new
adapter is implemented with synthetic tests. It is **not deployed or live
accepted** by this change. No real stopgap rows were removed, no live test Form
was submitted, and no Cloud Run revision was verified. The deployment owner
must retain the Drive recovery priority in START_HERE before activation.

The existing route remains unchanged unless private configuration selects
`HUB_INTAKE_ROUTE=shared`. This permits a reviewed rollout without changing
Biology/Forensics pages on code publication alone. No schedule is created.

## Sources and matching

`adapters/shared_form_intake.py` reads the configured shared Form's complete,
bounded response pages. `respondentEmail` is preserved separately from typed
answers. Students are matched against the current roster using the collected
school email, configured domain/prefix and explicit legal-name/nickname
variants. No typed name, filename suffix, class period or assignment establishes
identity in the shared route. Ambiguous matches remain review items.

The roster reader accepts Last Name/First Name headers (with an optional exact
tab title), headerless two-column Last/First rows, and single-cell `Last, First
Middle (Nickname)` rows. It keeps the first student, skips blank rows, excludes
study hall and rejects malformed populated rows. It reads up to 200 rows per
configured class tab; expanding that boundary requires review. Explicit
configured section headings inside a tab switch the section; an A3 block inside
an A2 tab remains A3. Identical repeated section blocks are deduplicated against
the canonical tab; conflicting memberships or unknown headings require review.
No roster tab is rewritten. Roster IDs and section IDs are private
configuration; the previous hardcoded roster file ID was removed.

Every Form-upload reference is checked under the approved root before reading
its content. Off-root originals remain coverage/recovery items. **A router copy
with a different file ID is not silently equated to the Form upload**; verified
copy provenance is still required before those copies can be substituted. A
read-only browser check of the four Anatomy originals established upload-folder
activity followed by moves/renames preserving their file IDs. This does not
establish the current editable Form ID or response-email access.

The bounded Agenda student-deck catalog is read for lesson candidates. A Refresh
reads the selected class catalog; the existing shared intake scan reads all
classes. A week can contain multiple assignments. Private
`HUB_INTAKE_DECK_ASSIGNMENTS` bindings map exact deck IDs to existing registry
keys. They explicitly identify an additional O-column Slides link as an
assignment; an unbound O link remains a resource. Multiple existing same-week
keys require bindings rather than inventing a third week key. Lesson matching
requires a unique exact response-marker signature and every unchanged source
question paragraph, with at least three markers and three question paragraphs.
Native Slides are exported read-only as PPTX; uploaded PPTX files use the same
bounded extractor. Markers alone, titles and typed links are insufficient. This
is conservative content evidence, not Drive copy-lineage proof: older, unmarked,
image-only or changed-question decks remain held. Multiple catalog lessons using
the same template remain ambiguous. An unreadable catalog source blocks
uniqueness rather than being silently discarded.

Typed name, period and assignment answers are cross-checks only. A mismatched
value (including a free-text assignment or unrelated pasted link) remains a
Needs review item even when email/deck/roster establish routing. Unconfigured
question IDs also produce review items, not silently skipped comparisons.

## Private runtime configuration

Retain existing root, reader, grading-workbook and Agenda settings. Supply these
through the deployment owner's existing approved private configuration only:

- `HUB_INTAKE_ROUTE=shared`
- `HUB_TURNIN_FORM_ID`: editable/API ID of the actual shared Form
- `HUB_ROSTER_SPREADSHEET_ID`: the intended current roster
- `HUB_SCHOOL_EMAIL_DOMAINS`: comma-separated school domains
- `HUB_EMAIL_USERNAME_PREFIX`: exact prefix, default `s.`
- `HUB_ROSTER_SECTION_IDS`: JSON mapping roster tab names to verified IC IDs
- `HUB_INTAKE_DECK_ASSIGNMENTS`: JSON mapping reviewed Agenda P/O deck IDs to existing assignment keys
- `HUB_TURNIN_QUESTION_IDS`: JSON mapping `name`, `section`, `assignment` to Form question IDs
- `HUB_INTAKE_WORKBOOK_WRITES=true`: explicit registration activation;
  absent/other values or `HUB_DRY_RUN=true` leave workbook writes disabled

Do not commit real values. Reader permissions must cover the existing shared
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

Global unmatched-deck and off-root items remain visible for review, never
arbitrarily assigned to the class that initiated the scan. Known typed conflicts
are retained in workbook holds. Existing monitor claims, baseline CAS,
first-observed semantics and deadline calculation remain in place. Intake
receipts distinguish dry-run from verified workbook registration; the monitor's
legacy `external_writes=0` field is observation-protocol compatibility and is not
a claim that an activated intake registration made no Sheets write.

## Read-only Refresh submissions

The existing Page 02 **Refresh submissions** action calls authenticated,
same-origin `POST /api/submission-monitor/refresh` with `class_key` and
`assignment_key` (`all` or an existing class assignment). It does not use the
navigation date as an assignment identity. It then rereads the saved grading
workbook. These have separate discovery and workbook-read timestamps.

The reader walks every bounded page and nested folder under the privately
configured approved root, including files absent from saved records. It reads
current Form references when available, verifies current roster identity, and
matches each deck's content separately. Folder membership alone never assigns
all files to one lesson. A2 and A3 retain their verified separate section IDs.
An unavailable Form or catalog is a visible coverage gap; root-visible files
remain review observations, not fabricated Form matches or missing work.

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

## Open live acceptance and source discovery

PR #39 contains code and synthetic regression tests, not a deployment. The
read-only evidence audit found two different Forensics Week 03 assignments in
one folder and newer files absent from saved dashboard records. Private IDs,
student data, content and detailed reports remain outside the repository.

The installed router trigger's target could not be opened as an editable Form;
that does not prove it is the Form used for the observed uploads. Current
Form-response access, exact private deck bindings, runtime source permissions
and teacher-path acceptance still require verification. No setup/reset function,
Form submission, folder move, school-record edit or grade post was performed.

Keep issue #37 open until the deployed revision's Refresh discovers the four
Anatomy originals with verified Form/roster matching, shows both Forensics
assignments under separate A2/A3 sections, preserves prior assessments and
versions, and exposes concrete coverage gaps. Report that result before any
separately authorized deployment or grading activation. Code tests and a stored
preview are not evidence of completed live school operations.
