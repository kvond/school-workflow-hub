# Submissions and teacher-summary pages

The existing HTML app now provides compact pages 2 and 3 with shared class/date selection, the existing authenticated Agenda lesson read, teacher Slides and the Agenda-mapped turn-in folder. Fictional workspace demos are no longer mounted. Research rendering is preserved.

`hub-agenda-day/v1` has additive optional `turnin_folder` and `assignment_label` fields per lesson. The read remains IAP authenticated, read-only and uncached. It optionally reads `🗂 TurnIn Map!A1:D160` within the existing bounded Sheets request. Only an exact course/week match with unique mapping and a syntactically valid folder ID yields a folder link. This is source routing, not proof of folder contents, file access, a stable assignment identity or submitted work. The folder may serve several lessons. Missing, malformed or duplicate mappings yield no link.

Page 2 reserves one student-work table for submitted work, deadline, feedback document and completion state. Page 3 separates NOTES/BANK/DRAFT completion from question-level understanding, exact misconceptions, instructional contrast and student follow-up. Unavailable counts are shown as unavailable, not zero. No students are declared missing, assessed, graded or classified by these empty views.

Copy-instruction buttons retain the exact teacher-supplied prompt, with a clearly separated selected-lesson context. They do not fetch student work or create a task/run. Printing, parent email, completion grade delivery and summary generation remain disabled.

## Still required

- Confirm the current roster and report locations; verify access from the hosted identity (connector access alone does not establish runtime access).
- Supply stable roster/learner/assignment mapping and complete cumulative submissions with actual submitted times and authoritative due dates.
- Obtain revision-bound completion/feedback assessments and document references in the right lesson folder; use the existing backend feed/planning models rather than reconstructing them in the browser.
- Provide whole-class assessed evidence or the teacher's compiled report for Prompt 3; no instructional conclusions from filenames or metadata.
- Establish the canonical private persistent record, authenticated executor, duplicate-run claims and destination-specific readbacks before enabling actions.

A source inspection in the connected school account found a current roster workbook and a Feedback and Summaries folder. Bounded folder searches returned no matching children; that is not proof that a class has submitted nothing. Private source IDs remain outside repository files.

## Verification

348 synthetic backend tests passed. Browser checks cover cross-page class/date scope, source links, scoped prompt text, absence of fictional demos, disabled actions, stale-read/date-mismatch handling, mobile width and research navigation. These do not verify real hosted submissions, feedback or teacher-summary generation.
