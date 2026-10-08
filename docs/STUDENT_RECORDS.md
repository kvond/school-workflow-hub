# Private student records

The teacher-facing Drive layout is one private class/section/student folder,
with dated work links and saved student feedback. Teacher-only growth records,
class summaries and teaching notes belong in separate private class report
folders. Section-specific Forensics reports have separate A2 and A3 folders;
reports covering both remain in the Forensics parent.

Original submissions and Form upload trees remain intact. One shortcut is
indexed per original Drive file ID. Original Form receipt dates label work;
immutable saved-feedback dates label feedback. File creation and modification
dates do not substitute for submission dates. Multiple dated observations of
one evolving source are retained in the private index. Class-level packets
containing several students' feedback remain at class level.

## Runtime configuration

- `HUB_STUDENT_RECORDS_ROOT_FOLDER`: private archive root, outside Turn-In.
- `HUB_STUDENT_RECORDS_COURSE_FOLDERS`: JSON object with `biology`, `forensics`
  and `anatomy` folder IDs, each a direct child of that root.
- `HUB_TEACHER_REPORTS_COURSE_FOLDERS`: separate private class report folders.
- `HUB_STUDENT_ARCHIVE_IC_ROSTER`: optional verified IC crosswalk, with
  class, section, school email, native student ID, names and active state.
- `HUB_FEEDBACK_DRIVE_ROOT_ID` and `HUB_FEEDBACK_DRIVE_FOLDERS`: the same
  archive root and class map when legacy class feedback packets live there.

Actual IDs, crosswalks, student records and receipts stay outside Git. The
existing runtime identity needs write access only to the configured private
archive. Parent/file permissions and locations are checked before filing and
again on readback. Source sharing is never changed by the Hub.

IC identity is preferred. When no verified IC crosswalk is available, filing
can use an exact unique school-email/course match from a Form and the source
roster. Names and filenames alone do not authorize automatic filing. Every
earlier Form version needs its own matching email evidence. The UI displays
**Roster unverified** until current IC membership can be confirmed. Departed
students' history is retained, with membership derived only from a verified
crosswalk.

## Operations

`GET /v2/api/student-records?class=<class>` reads verified indexed records.
It performs no Drive writes and does not depend on Agenda availability.

`POST /v2/api/student-records/archive` accepts only `class` and the exact
registered Agenda `week`. It requires teacher authentication and an allowed
same-origin JSON request, reloads source evidence and files eligible work.
Coverage and identity holds remain visible alongside successful filing.
The 6:30 routine invokes this bounded operation; browsing Student work does
not start writes. A successful existing tidy can additionally file records,
and any later archive failure is reported separately from completed moves.

Native feedback Save files the existing reviewed one-student PDF once by its
immutable draft ID, without another provider call. Teacher notes are excluded
from that PDF. Today printing combines indexed nested PDFs with legacy class
packets, deduplicates Drive IDs and excludes teacher reports and checklists.
Printed and Handed out remain separate teacher-controlled states.

Reserved Drive IDs and Firestore compare-and-swap records prevent duplicate
creates. Unconfirmed creation/readback holds for inspection rather than
creating a replacement. A backend storage-quota failure preserves the saved
Hub PDF and reports the filing blocker; it is not verified Drive delivery or
permission to mark Made.

## Retired folders

Use the literal prefix `~delete ` only for verified empty unused folders.
Do not trash files or folders. An empty Form response folder may still be an
active or future upload destination and must never receive this label merely
because it is empty. Retain original IDs, source links, distinct revisions and
named-user access during any authorized reorganization.
