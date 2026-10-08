# Today: new feedback and one print packet

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

**Print this feedback packet** rechecks the displayed file IDs, exports Google Docs
to PDF, reads existing PDFs, and merges the list in course order into one
packet. The Hub opens the packet in a new tab and invokes the browser print
dialog. A successful packet response proves the PDF was prepared; it does not
prove the printer produced paper.

The endpoint is read-only in Drive. It does not move files or alter Drive
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
one-student PDF by immutable feedback ID, without another model call. If Drive
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
