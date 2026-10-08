# Today: new feedback and one print packet

The Today page can list feedback documents created on the current Eastern
calendar date from the existing private Biology, Forensics, and A&P Drive
folders. It includes only Google Docs and PDFs whose names identify them as
feedback; drafts, growth records, summaries, and checklists are excluded. The
folder ID, exact course-folder name, and shared parent are rechecked before a
document is listed. The documents stay in their existing course folders.

**Print all new feedback** rechecks the displayed file IDs, exports Google Docs
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
