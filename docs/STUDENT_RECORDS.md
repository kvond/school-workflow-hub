# Private student records

The teacher-facing Drive layout is one private class/section/student folder,
with dated actual work copies, original submission links and saved student feedback. Teacher-only growth records,
class summaries and teaching notes belong in separate private class report
folders. Section-specific Forensics reports have separate A2 and A3 folders;
reports covering both remain in the Forensics parent.

Original submissions and Form upload trees remain intact. One original link is
indexed per Drive file ID, with a separately indexed actual copy for each
verified source revision and student. Native Google Slides and Docs remain
native copies; uploaded files retain their original type. Original Form receipt dates label work;
immutable saved-feedback dates label feedback. File creation and modification
dates do not substitute for submission dates. A reachable folder-only file
without a real submission timestamp can still be linked with its actual file
creation date, explicitly labeled **file created**. Multiple dated observations of
one evolving source are retained in the private index. Class-level packets
containing several students' feedback remain at class level.

Authenticated Schoology PDF and PNG originals can be registered after an
authorized worker uploads and verifies them in an existing indexed student
folder. Registration binds the original submission URL, revision, attachment,
audited identity and receipt hashes, actual bytes and private filed location.
It creates no Form event or feedback and changes no Drive properties or access.
One atomic CAS update adds the artifact and student attachment; repeated identical
registration reuses it. The work index retains the observed local submission day,
original Schoology link and filed-work link on later refreshes. Registration time
stays separate, and worksheet feedback remains pending until separately verified.

An internal filing worker can prepare writing feedback for one such PDF or PNG
without manufacturing a Form event. It needs the actual original bytes, literal
source metadata bookends and a separately authenticated review of every rendered
page and each original response region. The worker supplies the original page
count from its independent render proof; the adapter does not count PDF pages or
perform OCR. The reviewed teacher worksheet identifies
the question, printed text and response box; its frozen digest and the response
review digest must be supplied independently again when filing. Printed prompts,
identity, pictures and supplied examples cannot provide writing quotations.

The existing vocabulary, conceptual understanding, evidence and reasoning rubric
uses exact short quotations from those authored fields. Unavailable evidence stays
unrated. These observations are feedback only, with no completion count or grade.
The original external submission date stays on the work; the actual preparation
date labels the feedback. The existing private folder and WorkIndex row are reused,
preserving the original link, row key and teacher note. There is no public review
payload endpoint, automatic OCR attribution or scheduled worksheet-feedback job
in this adapter; a trusted worker must supply and verify the review and PDF.

Teacher-confirmed former-student work can stay in a tagged private class
`Former students` container with an explicitly unconfirmed former section.
The class index's `historical_sources` binding must match the saved student,
teacher confirmation, exact original revision and verified private work copy.
Routine filing preserves that unchanged historical source without asking the
active roster to reclaim it. A changed source, copy, ownership or private
location holds for verification. This grants no active-roster, assessment or
grade authority, and never invents an IC ID or former section.

## Runtime configuration

- `HUB_STUDENT_RECORDS_ROOT_FOLDER`: private archive root, outside Turn-In.
- `HUB_STUDENT_RECORDS_IDENTITY_SCOPE_ROOT`: optional stable index namespace;
  defaults to the actual root. Keep the former root ID here when rebuilding
  folders in a private Shared Drive, so student, artifact, receipt and task
  keys retain their identity even though physical folder IDs change.
- `HUB_STUDENT_RECORDS_COURSE_FOLDERS`: JSON object with `biology`, `forensics`
  and `anatomy` folder IDs, each a direct child of that root by default.
- `HUB_STUDENT_RECORDS_RECORDS_FOLDER`: optional private direct child of the
  actual root. When set, every configured course folder must be directly inside
  this records container. Class shortcuts at the root can target the existing
  section folders without changing section/student IDs or index identities.
- `HUB_TEACHER_REPORTS_COURSE_FOLDERS`: separate private class report folders.
- `HUB_STUDENT_ARCHIVE_IC_ROSTER`: optional verified IC crosswalk, with
  class, section, school email, native student ID, names and active state.
- `HUB_STUDENT_WORK_INDEX_URL`: the private native Google Sheet in the
  student-records root. Its `hubStudentWorkIndex` property must bind the stable
  archive namespace. A shortcut can make this same Sheet visible in the
  teacher's existing school folder without granting wider target access.
- `HUB_FEEDBACK_DRIVE_ROOT_ID` and `HUB_FEEDBACK_DRIVE_FOLDERS`: the same
  archive root and class map when legacy class feedback packets live there.

Actual IDs, crosswalks, student records and receipts stay outside Git. The
existing runtime identity needs write access only to the configured private
archive. Parent/file permissions and locations are checked before filing and
again on readback. Source sharing is never changed by the Hub.

Science-writing presentation replacements use a distinct immutable report ID,
`presentation_version` and `supersedes_report_id`. The exact prior payload is
provided as `supersession_previous_payload` and must hash to the saved prior
artifact. Student, source observations, evidence, dimensions and target remain
unchanged. Optional presentation-only submission times are checked against the
same original Form receipts; file creation dates never establish event order.
Only verified new PDF bytes, location and owner attachment permit the prior
record's explicit successor pointer. An uncertain upload leaves the prior current
and retains its original creation fence. If the new PDF is already saved but
the prior-version pointer is unconfirmed, readers keep the prior current and
omit the pending replacement until that exact save is recovered; no second
upload is attempted.

`verify_feedback_successor` performs the read-only chain and private-file proof
needed before retiring a prior PDF from a student folder. An authorized operator
may reuse `_Run.feedback_history_folder()` inside the existing hidden class
container, move only the prior PDF with its same ID, and record its actual
`parent`, `history_original_parent` and `history_folder_key`. The folder's tag,
class parent and PDF bytes must read back; no source or assessment changes are
permitted. Student ownership/history keys stay intact. Current feedback excludes
prior versions only after their saved successor chain is verified; broken chains
hold rather than silently hide data. The work index retains the old stable rows,
original PDF URLs and teacher notes, clearly labeled as superseded history.

The identity scope is an index identifier, not a location or permission grant.
The actual root and course folders still require private parent/access checks.
An authorized migration can recreate folder shells and move archive files
individually, retaining file IDs and immutable PDFs. Folder IDs, parents,
folder URLs, class-index folder values and current receipt locators must be
reconciled privately; CAS keys, identity aliases and source IDs stay stable.

IC identity is preferred. When no verified IC crosswalk is available, filing
can use an exact unique school-email/course match from a Form and the source
roster. The teacher additionally authorized filing by a clear full name or
known school username in the original filename. This filing-only fallback
reads the selected course roster afresh, retains duplicate names for collision
checks, and requires one exact course and section student. Surname-only,
unnamed, conflicting, duplicate or unavailable cases remain in their original
location, with a specific request for a named resubmission. Each earlier
version needs its own email or independently checked filename evidence;
identity from the current version is never inherited as proof. Name-only
filing is labeled **identity unverified** and never changes grading, native
feedback identity, IC mappings, grades or missing-work decisions.

Stable course/section/name aliases reuse an existing uniquely matching email
or native-ID folder. Later verified identity can upgrade a name-created folder
without duplicating source links or history. Two competing archive folders
remain an identity-review hold; the Hub never merges or discards either.
The UI displays
**Roster unverified** until current IC membership can be confirmed. Departed
students' history is retained, with membership derived only from a verified
crosswalk.

A reachable, uniquely identified file can also be archived when its lesson
match remains unresolved. Its copy and original link preserve the original filename and
source Form/week context, with **lesson not confirmed**. This records the
student's source work without asserting an assignment or resolving any
grading/IC lesson hold. Original submissions and Form response folders stay
intact.

## Operations

`GET /v2/api/student-records?class=<class>` reads verified indexed records.
It performs no Drive writes and does not depend on Agenda availability.

The read-only directory isolates a failed student history only after its saved
class/owner/index bindings have passed stable CAS bookends. It returns
`history_coverage: partial_indexed_history`, exact private `history_holds`, and
only independently verified student folders/artifacts; a trashed folder is not
an inferred withdrawal or permission to restore, erase or replace history.
Provider-wide reads and invalid/changed indexes remain whole-read failures.
Complete-history consumers, including feedback discovery and WorkIndex refresh,
retain strict reads and cannot overwrite a Sheet with this partial directory.

`preview_joint_copy_reconciliation` and `reconcile_joint_copy` are trusted-host
operations for one already teacher-attributed native work copy. A Drive version
change alone is not a new teaching decision. The preview requires the saved
`native_presentation/v1` full-content proof, copied modification timestamp,
original revision, exact private folder/owner and source tags. It reads the
copy's complete native structure and image content against that frozen proof,
never against today's original. Missing methods/proof, changed content,
modified timestamps, access, ownership or concurrent CAS changes remain held.
The apply call requires the exact fresh preview digest, an attributed operator,
and the existing source-owner/cutoff callback. It atomically preserves the prior
artifact in immutable reconciliation history while changing only its verified
Drive version and history pointer; unchanged owner/index values are CAS-checked.
No Drive content, location, identity, source revision, grading or teacher decision
changes. Uncertain CAS outcomes are read back, never blindly retried. Ordinary
directory reads do not reconcile or mutate a version.

Trusted morning callers may pass `before_source_write` to `archive_work` and
its derived WorkIndex refresh. The callback is checked at mutation boundaries
with an explicit 120-second budget for a bounded mutation and immediate saved
readback. Full native-content verification is read-only; later writes reserve a
fresh budget. The guard must renew a sufficient live lease and refuse a batch
that cannot finish before the cutoff. Separate feedback callers keep their
existing ownership. Supplying this hook is
not proof that every external writer participates or that a live archive run
finished before the cutoff.

`POST /v2/api/student-records/archive` accepts only `class` and the exact
registered Agenda `week`. It requires teacher authentication and an allowed
same-origin JSON request, reloads source evidence and files eligible work.
Coverage and identity holds remain visible alongside successful filing.
The work-and-feedback index retains each held submission's exact original-file
link and filename when available, with its recorded class, week and reason.
An unconfirmed filename is labeled as a submission, never assigned a student
identity or presented as a filed work copy. Adding an original locator to an
older receipt retains its first index-row key and teacher note.
The 6:30 routine invokes this bounded operation; browsing Student work does
not start writes. A successful existing tidy can additionally file records,
and any later archive failure is reported separately from completed moves.

Filing excludes missing-Form warnings from narrowly identified calendar-only
Agenda rows: empty lesson rows with no assignment content, the introductory
short-class timing rows, and blank-topic rows containing only recognized
closure or no-meeting notes. Real lessons sharing the same week still scan
their Forms. Only an exact missing-week-folder note can be excluded when every
row is calendar-only and no source files are present. Access, roster, source
availability and genuine missing-Form problems remain holds. Grading and IC
gates are unchanged, and a filing receipt records only the scanned week.

Native feedback Save files the existing reviewed one-student PDF once by its
immutable draft ID, without another provider call. Teacher notes are excluded
from that PDF. Today printing combines indexed nested PDFs with legacy class
packets, deduplicates Drive IDs and excludes teacher reports and checklists.
Printed and Handed out remain separate teacher-controlled states.

Work copying verifies the original revision before and after the one copy
request, the private destination, identity tags and copied content. Native
Slides verification reads complete presentation content through the existing
Drive-scoped Slides API and binds page order, text, styles, notes, object IDs,
external links and each actual image's pixels at its own position. Expiring
Google image URLs are replaced only by their verified image-content proof.
Native document exports bind their document XML and resolved resources;
binary uploads require exact bytes. A lost copy
response is recovered only by its one tagged result; absent or ambiguous
results never repeat the copy request automatically. Originals are never moved
or edited. Harmless native metadata version finalization is distinguished from
a copied-file modification by the verified copy's modification timestamp.

When Drive returned a copy ID, verification reads that exact ID directly with
the same private parent, source tags and content proof; a delayed folder listing
does not invalidate a confirmed result. An unknown ID still requires exactly one
tagged result. Only the bounded HTTP error status is retained for diagnosis.

With the explicit records container, new original-work shortcuts are kept in
each course's `Original work links` folder. The operator
`retire_source_links(token, write_token, config, class_key)` moves existing
shortcuts there only after proving a real copy for the same student, source and
current revision has identical content in its private student folder. It moves
the shortcut itself, preserving its ID, original Form dates and private history;
the separately stored retirement revision describes the current proof. It does
not move or edit the submitted source. The index retains the source link but
removes it from student navigation, and later filing reuses that same central
link. Missing, changed or ambiguous copies remain held with their links intact.

An explicitly authorized operator can call `file_work_export` with an existing
student key, original source ID, exact expected source revision and the unresolved
native-copy reservation key. This creates a separately identified **Exported work
snapshot** in PPTX format; it does not retry, alter or certify the uncertain native
copy. The original revision and native content are verified before and after
export/upload, and a separately reserved Drive ID binds the exact exported bytes.
Unknown upload outcomes remain fenced. Normal filing never creates this fallback
automatically; it may reuse an already verified export for the unchanged revision.
The snapshot retains original Form dates and source links, can pair with feedback,
and can support source-navigation cleanup only after both byte and native-content
proofs pass. No source, grade or complete-week receipt is rewritten.

`refresh_work_index` runs after normal work filing and feedback filing when
configured. The Records tab has one stable record key per source/report row,
typed source dates, actual-copy/original/feedback/student-folder hyperlinks,
and an editable Teacher note preserved by key through sorting and refreshes.
Existing notes, constrained fields or unknown row keys are held rather than
overwritten. Matching requires exact student/source/assignment IDs; similar
lesson titles never pair a report with work. Historical feedback with no
proven matching submission remains visible as **work not located**. Recorded
filing holds and source-only rows remain explicit. Index coverage describes
verified archived histories and recorded scans, not every possible submission.

Teacher-authorized science-writing reports use a distinct source-bound artifact
identity and reuse an existing verified student folder. Each included source
revision is checked and its actual archive submission date/Form/lesson context
retained. This creates feedback PDFs without grade, identity, source, delivery
state or model writes. A report spanning multiple verified submissions may link
from each corresponding index row while remaining one printable report.

Feedback printing verifies the private archive index, student/section folders
and each selected PDF's current parent, private access, identity tag and
checksum. It does not fetch unrelated work shortcuts. A missing work shortcut
therefore cannot prevent reprinting a verified PDF; the full student-records
view still reads and checks every work shortcut. This print-specific read does
not change filing receipts or assert complete source coverage.

An independently audited legacy packet can use
`filing_person_for_legacy_label(..., section=<verified source section>)` for
filing only. The label must contain an explicit roster first name or recorded
nickname plus a full surname or surname initial, unique within the selected
course and source section. It never infers a missing first name. The importer
must retain the original packet, bind every extracted PDF to its source ID,
verified version or content hash, original date and exact audited student span,
and use a distinct legacy artifact identity. It must never fabricate a native
saved-feedback draft ID. Such filing is marked **identity unverified** even
when its stable name alias reuses an existing verified student's folder.

Reserved Drive IDs and Firestore compare-and-swap records prevent duplicate
creates. Unconfirmed creation/readback holds for inspection rather than
creating a replacement. A backend storage-quota failure preserves the saved
Hub PDF and reports the filing blocker; it is not verified Drive delivery or
permission to mark Made.

Only a precise PDF storage-quota rejection followed by an exact reserved-ID
404 records a known quota failure and the verified parent Drive ID. This is
evidence for explicit reconciliation after migration, never an automatic
retry. Unknown create outcomes retain the same inspection hold.

## Retired folders

Use the literal prefix `~delete ` only for verified empty unused folders.
Do not trash files or folders. An empty Form response folder may still be an
active or future upload destination and must never receive this label merely
because it is empty. Retain original IDs, source links, distinct revisions and
named-user access during any authorized reorganization.

For recovered worksheet feedback, the reviewed page/question label is shown to the student; original object IDs and exact quote/source digests remain private provenance. A trusted Root filing worker may explicitly defer the normal WorkIndex refresh (default unchanged), then guard the existing five source keys and full native sheet before updating only feedback links/status/dates. Submission dates remain original.
