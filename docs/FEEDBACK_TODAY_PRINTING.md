# Today: new feedback and one print packet

The Today page lists the latest available dated batch of feedback, preferring
the current Eastern calendar date, from the existing private Biology,
Forensics, and A&P Drive folders. It shows the actual creation date and never
infers that an older document is unprinted. It includes only Google Docs and PDFs whose names identify them as
feedback; drafts, growth records, summaries, and checklists are excluded. The
folder ID, exact course-folder name, and shared parent are rechecked before a
document is listed. The documents stay in their existing course folders.

**Print this feedback packet** rechecks the displayed file IDs, exports Google Docs
to PDF, reads existing PDFs, and merges the list in course order into one
packet. The Hub opens the packet in a new tab and invokes the browser print
dialog. A successful packet response proves the PDF was prepared; it does not
prove the printer produced paper.

The endpoint is read-only in Drive. It does not move files or alter Drive
permissions. The existing feedback routine already stores its output in the
matching course folders. To enable the Hub view, configure these Cloud Run
environment variables for the known private Drive tree:

- `HUB_FEEDBACK_DRIVE_ROOT_ID`: the existing `Student Feedback — Private
  2026–27` folder ID.
- `HUB_FEEDBACK_DRIVE_FOLDERS`: JSON mapping `biology`, `forensics`, and
  `anatomy` to the existing `🌿 Biology — Private feedback`,
  `🔬 Forensics — Private feedback`, and `🫀 A&P — Private feedback` folder
  IDs.

The runtime service account needs read access to these folders. Keep the
folders private and do not grant anyone/domain access to make this work. If the
mapping is missing, the folder tree changed, or runtime access is denied, Today
shows that blocker and does not claim that feedback is ready or printed.

## Class growth report

Today can link directly to the current private native Sheets report and its
teacher checklist. Configure `HUB_GROWTH_REPORT_URL`,
`HUB_GROWTH_TEACHING_URL` (an optional tab in the same spreadsheet), and
`HUB_GROWTH_REPORT_AS_OF` (ISO date). Only exact Google Sheets edit URLs are
accepted. IDs remain in deployment configuration, outside repository files.
The teacher opens these links through their own signed-in school profile;
the endpoint does not fetch student data or require Drive access for this
handoff. A dated report is a snapshot, not evidence of automatic regeneration.
