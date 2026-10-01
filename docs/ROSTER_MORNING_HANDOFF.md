# Handoff for Codex: add roster additions to the 7:00 a.m. helper run

Written October 1, 2026 by Claude (Cowork) for the teacher. In chat today she decided: "Codex: add roster to the 7:00 run." Record that authorization in `AGENTS.md` next to the September 30 one. It covers adding new students to the Rosters sheet and nothing else.

## What the run does, before any scores

1. **Read IC's roster for each section from the open IC tab.** The IC page itself already calls `/campus/api/instruction/roster/<sectionID>` (seen October 1). Read it; never change anything in IC. The section ids belong in runtime config, not the repo: A2 Forensics, A3 Forensics, A4 Biology, B3 A&P.
2. **Compare it with the Rosters sheet.** Each section has a tab: 0476-1 Forensics = A2, 0476-2 Forensics = A3, 0422-2 Biology = A4, 0448-1 A&P = B3. Column A is First Name and column B is Last Name. Some first names carry a nickname, written as `Chloe' (Cash)`; match on the part before the nickname. Column C is "Added", the date a row was added. Use the Hub's existing roster matching (`roster.py`, nicknames and accents) and require a one-to-one match.
3. **Add only students who are in IC and not on the sheet.** Append one row per student: the IC first name in A, the IC last name in B, and today's date in C as MM/DD/YYYY. This is the same as the row added for Lester Ortez Fuentes on September 30.
   - Write each cell as plain text.
   - Never use a tab character inside a cell.
   - Read each new row back after writing it.
4. **Never remove, rename or reorder anyone.** List students who are on the sheet but no longer in IC under "Check these" in the morning summary, for the teacher to handle by hand.
5. **Hold anything uncertain and report it.** That includes a name that matches more than one row, an IC row without a section, or a section whose IC roster couldn't be read. Do not guess.

Run the roster step before score entry. The Hub's matching then sees the new students, so their work isn't reported as unmatched.

## Where it writes

The Hub server writes the sheet with its existing service identity; the browser only reads IC and hands the list to the Hub. Use the same IAP and same-origin protections as `/v2/api/morning/*`. Add a counts-only line to the morning summary, for example "Roster: 1 added (A2), 0 to check". Put no student names in the receipt rows.

## Tests and acceptance

- Synthetic tests: a new student, a nickname match, an accent match, an ambiguous name, a student who left, an unreadable section, and a rerun on the same day that adds nothing.
- The first live morning is the acceptance. The teacher checks the Rosters sheet that day.

## Not in scope

Changing the Hub's roster matching for scoring, writing anything in IC, and removing students.
