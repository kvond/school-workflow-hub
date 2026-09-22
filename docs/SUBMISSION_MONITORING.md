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
tab title), headerless two-column Last/First rows, and single-cell
`Last, First Middle (Nickname)` rows. It keeps the first student, skips blank
rows, excludes study hall and rejects malformed populated rows. It reads up to
200 rows per configured class tab; expanding that boundary requires review.
No roster tab is rewritten. Roster IDs and section IDs are private configuration;
the previous hardcoded roster file ID was removed.

Every Form-upload reference is checked under the approved root before reading
its content. Off-root originals remain coverage/recovery items. **A router copy
with a different file ID is not silently equated to the Form upload**; verified
copy provenance is still required before those copies can be substituted. This
is a live acceptance blocker if the four Anatomy stopgaps reference only copies.

The full bounded Agenda student-deck catalog is read for lesson candidates.
Lesson matching requires a unique exact response-marker signature and every
unchanged source question paragraph, with at least three markers and three
question paragraphs. Native Slides are exported read-only as PPTX; uploaded
PPTX files use the same bounded extractor. Markers alone, titles and typed links
are insufficient. This is conservative content evidence, not Drive copy-lineage
proof: older, unmarked, image-only or changed-question decks remain held.
Multiple catalog lessons using the same template remain ambiguous. An unreadable
catalog source blocks uniqueness rather than being silently discarded.

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
- `HUB_TURNIN_QUESTION_IDS`: JSON mapping `name`, `section`, `assignment` to Form question IDs
- `HUB_INTAKE_WORKBOOK_WRITES=true`: explicit registration activation;
  absent/other values or `HUB_DRY_RUN=true` leave workbook writes disabled

Do not commit real values. Reader permissions must cover the existing shared
Form, approved root, roster, Agenda and candidate teacher/student template decks.
Workbook registration additionally needs the existing authorized Sheets writer.
No new credential, sharing or schedule is supplied by this implementation.

## Dashboard registration and holds

`adapters/intake_records.py` prepares unscored intake rows and missing Assignments
entries. Existing keys are reused; new canonical keys use class + week. A
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

## Open live acceptance and source discovery

The router is not present in this repository. A school-account Drive metadata
search found no Apps Script project or exact shared Form title; broader indexed
searches did not establish the router's source. This does not prove absence,
especially for container-bound Apps Script. Its location and copy-provenance
contract remain UNVERIFIED. No setup/reset function was run.

The origin of the students' shared Form link and any affected Schoology pages
remain UNVERIFIED. No deck links need changing for option A. No Schoology pages
or Form settings were changed.

Keep issue #37 open until the live dashboard shows the four Anatomy Week 02
submissions under the verified section without duplicate stopgaps; a permitted
end-to-end wrong-period/free-text case lands with review holds; Biology and
Forensics are checked unchanged; and the verified Cloud Run revision is recorded.
No signed-in dashboard browser or deployment-owner runtime configuration was
available to this implementation pass. Existing recovery and school-write
boundaries are not bypassed to manufacture acceptance.
