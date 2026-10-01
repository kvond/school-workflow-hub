# Handoff for Codex: remove the Hub's Tidy and put the new routine on the Routine page

Written September 30, 2026 by Claude (Cowork) for the teacher. This is a dashboard change, and the teacher has made Codex responsible for dashboard upgrades. It goes with `docs/HELPER_AUTOSAVE_HANDOFF.md` on the same branch.

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
| Weekdays, at home before school | You | Open the Hub, IC, the School Claude desktop app and the School Drive turn-in folder, in Chrome's school profile, signed in. Bring the IC tab to the front. | The 6:30, 7:00 and 8:00 runs have what they need. |
| 6:30 a.m. weekdays | School ChatGPT | Tidies folders: finds all student work by name and lesson and moves it into the lesson's turn-in folder. | Files sit in the right lesson folder, so counts, 0s and feedback read the right work. |
| 7:00 a.m. weekdays | Chrome helper (see `HELPER_AUTOSAVE_HANDOFF.md`) | Roster additions and scores to IC. Until that build is live and switched on, this stays the Monday prompt with the teacher's OK. | Scores are in IC; the morning summary says what was entered and what was left. |
| 8:00 a.m. weekdays | School ChatGPT (Codex) | Feedback for the latest closed lesson and the class summary, dropped in that lesson's turn-in folder. | Feedback is ready to print. |
| Whenever a new lesson starts, at school | You | Print and hand out the cumulative feedback. Click Mark feedback done. | Mark feedback done is the list of what was printed and passed out (see below). |
| At school | You | Maintain the Agenda. | The Hub finds each lesson's deck, Form and handed-out copy. |
| Any time | Codex | Upgrades to the dashboard. | |

Keep the rows that still apply: 1st Monday (Prompt 3), copying a prompt to GitHub, installing the helper, and August (the new research sheet).

Update the `buttons` list to match. "Start feedback → Copy prompt for Teacher Claude" becomes the 8:00 run's job (or the teacher's, by hand). "Mark feedback done" becomes "You, after handing feedback out."

## 3. The list of feedback printed and passed out

The teacher wants a list of the latest feedback printed and passed out. Rather than a separate list, show one on the Routine page, built from the Mark feedback done records the Hub already keeps: for each class, the latest lesson marked done and the date. Read only.

## 4. Biology answers to Sheets: already the Hub's job

The teacher pointed to "Student answers — Biology" (in the turn-in root, created September 24, still empty). `src/hub_v2/answers.py` already fills that sheet: when a Biology assignment's two-week window closes, the dashboard's daily sweep adds one row per student per coded box. So School ChatGPT does not need to write it.

Find out why it is still empty and say so on the Routine page's research line. It could be that no Biology assignment has closed yet. Or the dashboard's service account may not be an Editor on the sheet or the turn-in root, in which case `_add` raises "The dashboard can't add rows…". If it's the second, the fix is a share by the teacher, so name the exact account to share with.

## Acceptance

Run all the tests after the removal. Load the Routine page and Student work once and confirm that no Tidy control or text remains, and that the Routine rows read as above.
