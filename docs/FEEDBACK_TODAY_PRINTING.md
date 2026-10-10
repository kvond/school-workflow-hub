# Today: a saved daily feedback document across all classes

**Print all feedback — YYYY-MM-DD** finalizes one private dated PDF from the
existing student feedback for that Eastern date. It includes every configured
class, keeps the individual reports in their student folders, and connects the
saved long document to the Today daily-report panel. **Open saved daily
feedback** reopens that exact snapshot. Reprinting does not generate feedback,
change grades, or mark Printed or Handed to student.

Saved student science-writing reports belong in this same daily document.
Their first page uses colored writing levels and four separate source-backed
growth charts; their second page keeps the student's own next-step challenge
and lined practice space. Presentation updates preserve the underlying ratings,
work dates, source evidence, and earlier PDF versions. A single observation is
shown as one point; unavailable or unrelated work is not turned into a trend.

Teacher writing observations use the selected file's verified original Form
receipt date. The assessment date remains separate. Repairing embedded codes
does not make the repair date a new student-work date; missing or conflicting
receipt evidence holds teacher reporting while the saved student PDF stays
available. This date check does not change feedback generation identities,
source freshness checks, or teacher-controlled delivery checkboxes.
Existing observation batches remain immutable; an earlier misdated batch
requires a separately verified correction rather than an automatic rewrite.

Current missing-work contracts also belong in the daily student document after
a complete fresh source check against the current IC roster, assignment flags,
recorded due dates, and private student folders. Each student gets one dated
page covering their verified missing lessons and space for a supportive plan.
Exempt, unassigned, newly joined, extended, future-due, ambiguous, or incompletely
checked work is not silently classified as missing. A historical contract or
a zero score does not establish that work is still missing. Existing extensions,
grades and teacher-controlled Printed/Handed states stay unchanged.

New daily snapshots report separate science-writing and current-contract
counts. Earlier manifests remain immutable and can show their original generic
source count. A saved packet proves print-ready bytes, not physical printing.

The packet date is the original native Save date, or the audited original date
of an imported report. A Shared Drive migration or a new copy's creation date
does not make old feedback newly generated. The saved manifest retains both
the original feedback date and actual Drive creation date, plus the packet's
separate compilation time. When today has no new feedback, Today can show the
latest actual feedback date explicitly.

Each manifest reserves one Drive file ID before upload. An unchanged manifest
reuses the saved PDF; new or changed sources create another immutable version.
The earlier documents and their private manifests remain available. The
manifest binds source Drive IDs, revisions, checksums, report identities and
dates, and records the merged page count and PDF hash. Source changes during
export hold the operation before upload. An uncertain creation is recovered by
exact readback or remains held; it never retries as another file. An explicit
quota failure plus a confirmed missing reserved file stays held while the
destination is unchanged. Its same reserved ID can resume only after folder
metadata proves migration into a different actual Shared Drive. A reservation
interrupted before upload can re-export unchanged sources using its same ID.

Today normally compares the saved source revisions with the current catalog.
If only a binary PDF's Drive version changed, it can keep the saved document
current after a fresh complete download matches the snapshot's SHA-256, MD5,
size and modification time, with unchanged report identities, dates and
membership and verified private file/parent readback. Missing proof, changed
native Docs, changed bytes or changed context do not use this fallback. This
read-only check neither rewrites the historical manifest nor creates another
identical PDF.

Native feedback must be verified in the individual student folder before it
can enter a daily document. Its immutable ID uses `feedbackdraft_<hash>`.
Losslessly imported, audited legacy student reports use the separate
`feedbacksource_<hash>` namespace; they are never represented as native model
drafts. No provider call is involved in importing or compiling existing reports.

Existing multi-student packets remain class-level sources unless individual
student boundaries have been proved. Daily inclusion requires audited
student-only contents. Known packet membership suppresses a class packet when
all of its members already have individual verified copies. With partial proven
membership, the complete audited class packet is printed and its overlapping
individual PDFs are omitted from that print document; the student copies stay
filed. This needs no guessed split. Two non-identical class packets with partially
overlapping members hold finalization. A proved pixel-identical duplicate can
name `canonical_source_id` with `duplicate_basis:
"identical_student_feedback_pages"` in its private source manifest; both
originals remain intact and only the canonical original supplies printed pages.
Teacher notes, class summaries, growth records and checklists never belong in
the daily student packet.

The new configured destination is `HUB_FEEDBACK_DAILY_PACKETS_FOLDER`, a
private folder outside Turn-In source discovery. Use the existing approved
Shared Drive when service-account My Drive quota is unavailable. The runtime
verifies its named-user privacy, ancestry, write capability and uploaded bytes;
it never broadens sharing or uses a different account's credentials.

Authenticated operations:

- `POST /v2/api/feedback/daily` with `{"date":"YYYY-MM-DD"}` finalizes that
  exact date using the authorized writer and returns the saved document link.
- `GET /v2/api/feedback/daily?date=YYYY-MM-DD` reads that date's latest confirmed
  version; omitting the date reads the latest recorded actual day.
- `GET /v2/api/feedback/daily/pdf?id=dailyfeedbackpacket_<hash>` verifies and
  serves a specific saved version through the Hub authentication boundary.

POST requires the teacher's authenticated same-origin JSON request. Packet
preparation is bounded to 300 source documents, 1,500 pages and a 10 MB PDF.
The old read-only `/feedback/print-today` preview remains usable when the daily
folder is unconfigured; the UI identifies it as a preview, not a saved daily
document. A confirmed saved snapshot remains reprintable if current source
access later fails, with that source-coverage limitation shown separately.

Large audited legacy memberships belong in private CAS, because Drive custom
properties allow only 124 bytes for a key/value pair. The file's
`hubFeedbackManifest` points to `feedbackpacketmanifest_<hash>` with schema
`hub-feedback-source-manifest/v1`, `drive_file_id`, current `source_revision`
(`version`, `modifiedTime`, `md5Checksum`, `size`), `student_only: true`,
`feedback_date`, and `feedback_ids`. The manifest must match the current Drive
revision. A short single-member `hubFeedbackIds` JSON value is also accepted;
`hubStudentFeedbackOnly=true` and `hubFeedbackDate=YYYY-MM-DD` are the smaller
audited content/date markers. These markers do not infer or assign students.
The Drive size limit is documented in the
[Google Drive custom-properties guide](https://developers.google.com/workspace/drive/api/guides/properties).

## Existing sources and private folder configuration

The Today page lists the latest available dated batch of feedback, preferring
the current Eastern calendar date, from the configured private Student records
tree. It combines verified indexed PDFs in class/section/student folders with
existing class-level feedback packets, deduplicating Drive file IDs. It shows
the recorded creation date and never infers that an older document is unprinted.
Drafts, growth records, summaries, and checklists are excluded. Private locations
and the archive index are rechecked before listing or printing.

Student feedback belongs in each student's records folder as a dated PDF. Teacher-only
growth records, class summaries and teaching notes belong in the separate
private Teacher reports class folders. Existing packets containing several
students remain at class level; they are never attributed to one student's
folder. Original submissions and Form upload trees remain intact. See
[Private student records](STUDENT_RECORDS.md) for filing, identity and history
rules.

The read-only preview rechecks the displayed file IDs, exports Google Docs
to PDF, reads existing PDFs, and merges the list in course order into one
packet. The Hub opens the packet in a new tab and invokes the browser print
dialog. A successful packet response proves the PDF was prepared; it does not
prove the printer produced paper.

The preview endpoint is read-only in Drive. It does not move files or alter Drive
permissions. The separate filing operation verifies saved feedback in the
student folders. To enable the Hub view, configure these Cloud Run environment
variables for the known private trees:

- `HUB_STUDENT_RECORDS_ROOT_FOLDER`: the `Student records — Private 2026–27`
  root, outside Turn-In discovery.
- `HUB_STUDENT_RECORDS_COURSE_FOLDERS`: JSON mapping `biology`, `forensics`
  and `anatomy` to direct class folders under that root.
- `HUB_TEACHER_REPORTS_COURSE_FOLDERS`: the separate private teacher-report
  class folders.
- `HUB_FEEDBACK_DRIVE_ROOT_ID`: the same Student records root when it also
  contains existing class-level feedback packets.
- `HUB_FEEDBACK_DRIVE_FOLDERS`: JSON mapping `biology`, `forensics`, and
  `anatomy` to those same class folders for packet compatibility. The legacy
  reader also supports an explicitly configured old private feedback tree;
  old folder names alone do not select a destination.

The runtime service account needs read access to these folders. Keep the
folders private and do not grant anyone/domain access to make this work. If the
mapping is missing, the folder tree changed, or runtime access is denied, Today
shows that blocker and does not claim that feedback is ready or printed.

Native feedback Save preserves the reviewed Hub draft and files its existing
one-student PDF by immutable feedback ID, without another model call. After
verified filing it can update the original Save day's configured daily document.
If that packet update is held, the reviewed individual PDF stays saved and
the reply identifies the packet issue. If Drive
reports that the backend account has no storage quota, the Hub PDF remains
saved and downloadable, but student-folder filing is held. This is not verified
Drive delivery: do not mark Made, generate a replacement draft, or keep retrying
an unchanged quota failure. Print the existing saved Hub PDF if needed; the
Today Drive packet includes only verified Drive files. Printed and Handed out
remain separate teacher-controlled states.

## Class growth report

Today can link directly to the current private native Sheets report and its
teacher checklist. Configure `HUB_GROWTH_REPORT_URL`,
`HUB_GROWTH_TEACHING_URL` (an optional tab in the same spreadsheet), and
`HUB_GROWTH_REPORT_AS_OF` (ISO date). Only exact Google Sheets edit URLs are
accepted. IDs remain in deployment configuration, outside repository files.
The teacher opens these links through their own signed-in school profile;
the endpoint does not fetch student data or require Drive access for this
handoff. A dated report is a snapshot, not evidence of automatic regeneration.
