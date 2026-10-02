# Handoff for Codex: remove the Hub's Tidy and put the new routine on the Routine page

Written September 30, 2026; routine and archive requirements updated October 2. The teacher has made Codex responsible for dashboard upgrades. This goes with `docs/HELPER_AUTOSAVE_HANDOFF.md`.

## 1. Remove Tidy

The teacher's decision, in her words: "The hubs tidy finds student work but doesn't move them to one folder - drop it and off the dashboard."

Folder tidying now belongs to School ChatGPT at 6:30 a.m. on weekdays. It finds every student's work by name and lesson and moves it into that lesson's turn-in folder. The Hub stops moving or renaming anything in Drive.

Take out, following the "Document retirement" rule in `AGENTS.md` (delete; don't leave stale notes):

- `src/hub_v2/tidy.py`, the `api/tidy` GET route and the `/v2/api/tidy` POST route with `tidy_post` in `src/hub_v2/app.py`, and the `tidy` import.
- In `src/hub_v2/static/app.js`: `renderTidy`, `previewTidy`, `applyTidy`, the `tidy-box` element and its clears, the `tidy-work` button and its listener, plus the matching markup in `index.html`.
- The line on late work that says "Tidy moves these into the week's '⏰ Late — not graded' folder." Keep the late list itself, and say instead that late work stays where it is and is not graded.
- The Tidy rows in the Routine page's `mine` and `buttons` lists, and the Tidy tests in `tests/test_hub_v2.py`.
- Any mention of Tidy in `app.py`'s module docstring ("Its one write is the Tidy button…") and in `docs/`.

Check before deleting: the Hub reads `google.original_name` so that renamed files still match. Keep that reader, because School ChatGPT's tidy may rename files too.

## 2. Put the new routine on the Routine page

Replace the Monday, Thursday and Friday rows in the `mine` list with the teacher's new routine. Use her wording in `you`, and say what gets done in `done`. One line each:

| When | Who | What | What gets done |
|---|---|---|---|
| Weekdays, at home before school | You | Open the Hub and IC in Chrome's school profile, signed in, and keep the Codex app running. Bring the IC tab to the front. | The 6:30, 7:00 and 7:30 runs have what they need. |
| 6:30 a.m. weekdays | School ChatGPT | Tidies folders: finds all student work by name and lesson and moves it into the lesson's turn-in folder. | Files sit in the right lesson folder, so counts, 0s and feedback read the right work. |
| 7:00 a.m. weekdays | Chrome helper (see `HELPER_AUTOSAVE_HANDOFF.md`) | Enter eligible scores under the enabled morning setting. Roster additions and removals require review. | The morning summary reports entered, retained and held scores, with verified readback. |
| 7:30 a.m. weekdays | School ChatGPT (Codex) | Feedback for the latest closed lesson, growth records, class summary and Lesson opener in the existing private course folder. | Verified new reports appear in the class-matrix checklist. |
| Whenever a new lesson starts, at school | You | Open the class checklist tab, print and hand out the latest verified Made reports, and tick Printed and Handed out separately. | The class matrix preserves delivery history. |
| At school | You | Maintain the Agenda. | The Hub finds each lesson's deck, Form and handed-out copy. |
| Any time | Codex | Upgrades to the dashboard. | |

Keep the rows that still apply: 1st Monday (Prompt 3), copying a prompt to GitHub, installing the helper, and the helper instructions.

Update the `buttons` list to match. "Start feedback → Copy prompt for Teacher Claude" is a manual fallback for the 7:30 feedback run. Delivery checkboxes belong to the teacher in the existing class-matrix checklist.

## 3. The list of feedback printed and passed out

The existing private checklist has four class tabs, student rows and week/cycle lesson columns. Made this run means this execution created or revised and verified that student's report. Each lesson group has Made, Printed and Handed out checkboxes. Printed and Handed out are teacher-controlled. Preserve prior reports and delivery history; do not create a competing Ready to print tab or Hub delivery ledger.

## 4. Biology answers spreadsheet retired

The teacher removed the redundant “Student answers — Biology” spreadsheet on
October 2. Its daily sweep, per-assignment writer and Routine status have been
removed. Do not restore or recreate it. Feedback, growth records, class summaries
and the class-matrix checklist continue in their private destinations.

## Acceptance

Run all the tests after the removal. Load the Routine page and Student work once and confirm that no Tidy control or text remains, and that the Routine rows read as above.
