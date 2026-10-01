# Handoff for Codex: Chrome helper enters the IC lists on its own at 7:00 a.m.

Written September 30, 2026 by Claude (Cowork) for the teacher. Read `AGENTS.md` and `browser-extension/README.md` first. This document adds one teacher authorization and describes the build it allows.

## Build status — October 1, 2026

Implemented on `feat/helper-autosave`: the off-by-default popup setting,
Eastern weekday alarm, exact-route entry, same score planner/executor, source
rechecks, durable once-per-day and uncertain-save guards, and counts-only
morning receipts shown in the Hub. See `browser-extension/README.md` for the
runtime mapping schema, tests and deployment requirements.

Two assumptions in this handoff were not present in the code: v2 has no
verified Form-to-IC route binding, and roster sync is status-only. Missing
bindings now hold the assignment; roster changes are explicitly reported as
unavailable. No mapping was guessed and no roster write was enabled. No live
extension installation, opt-in, morning grade entry or September 30 ledger
verification is claimed by this build. The first live morning remains pending.

## The teacher's authorization (September 30, 2026)

Asked whether the 7:00 a.m. score entry should wait for her approval, the teacher answered in chat: "prepare and run without my approval, I will check at schools and change scores as needed." She then chose to have the Chrome helper do the saving itself, rather than an AI assistant clicking Save.

This replaces, for this one path only, the September 28 requirement that "Enter N scores and Save" needs a trusted click after her OK. It does not authorize any other school write. Record it in `AGENTS.md` next to the September 28 paragraph, and keep the rest of that paragraph's limits.

Why the helper and not an assistant: `AGENTS.md` already says unattended workflows must not depend on an assistant session. An assistant can't click Save without a yes in that session, and a scheduled assistant run has no one there to give one. Deterministic code in the helper has neither problem.

## What the morning should look like

On weekdays (Monday to Friday) the teacher's Mac is on, with Chrome open in the school profile, signed in to ClassLink and Infinite Campus, and a Hub tab open. She is not there. At 7:00 a.m. Eastern, after the 6:30 a.m. folder tidy, the following happens with no input from her:

1. The Hub sends every assignment whose scores are ready ("Scores are ready to enter in IC") to the helper. This is the same `hub-ic-list/v1` message that "Send to the Chrome helper" sends now.
2. The helper opens each matching assignment's score panel in IC itself. Today she opens the panel by hand, so this step is new. It needs the IC section and assignment for each list, taken from the Hub's existing assignment mapping and not guessed from titles alone.
3. The helper runs the same review it shows now, then enters and saves without the checkbox or the trusted click. Unattended entry happens only when the teacher has turned on a setting in the helper's popup ("Enter automatically on weekday mornings"). That setting is off by default.
4. Results go back to the Hub as they do now. The "Entered in IC" rows reach the "Scores entered" sheet only after every section and column reads back.
5. The Hub shows a morning summary she can read at school, one line per assignment: what was entered, what was left, and why it was left (flag, excused, check by hand, name not matched, changed since the review, Save not confirmed).

## Rules that do not change

Every rule in the 0.3.0 README stays in force on the unattended path:

- Keep the higher score: fill blanks and raise lower scores, never lower one.
- Leave flagged rows, excused students, "check by hand" rows, and names that don't match one to one.
- Refuse a panel that isn't scored out of 10, has more than one score group or label code, has under 60% of its rows matched, or spans sections.
- Re-read IC immediately before typing, and stop if anything changed since the review.
- Use native Save, reload, and read every score back. Report a Save that can't be confirmed as uncertain, and never retry automatically.
- Never create an IC assignment, change a due date, mark Exempt, or touch anything other than the score cells on the list.

## New stop conditions for the unattended path

- If IC, ClassLink or the Hub shows a sign-in page, stop and report "signed out" in the morning summary. Never sign in, and never store or copy credentials, cookies or profiles.
- If the IC tab can't be brought into a usable state (the launcher renders only when the tab is visible), report it and stop rather than looping.
- If the Mac was asleep at 7:00, run once when Chrome next wakes before noon on the same weekday. Otherwise skip the day and say so in the summary.
- At most one unattended run per weekday.

## Roster additions (same morning, before the scores)

Today an assistant compares the IC roster with the roster sheet and, after her OK, adds new students (for example, Lester Ortez Fuentes, added to "0476-1 Forensics" on September 30 with the date in column C). If the Hub's roster sync (`docs/ROSTER_SYNC_INTEGRATION.md`) can make that addition itself, put it in the same 7:00 run, ahead of the scores. Only add rows, put the date in column C, and never remove or rename a student; list removals in the morning summary for her to handle. Doing this keeps the whole morning free of any assistant.

## Scheduling note

MV3 service workers sleep, so use `chrome.alarms`, which adds the `alarms` permission to the manifest. The package currently asks only for `storage`, so say so in the README's install section. Chrome must be running, and the alarm does not fire while the Mac is asleep.

## Acceptance

- Run the unit and messaging tests, plus a mock-panel dry run of the unattended path: blanks, a raise, a kept higher score, a flagged row, a sign-in page, and a change made after the review.
- The first live unattended morning is the acceptance. The teacher checks IC at school that day, and the README records the result the way September 29 is recorded.
- The September 30 supervised run entered scores across several assignments and sections from the Hub lists. Update the README's statement that raises and several sections "have not yet been seen live" once you confirm that from the "Scores entered" rows.

## Out of scope

The 6:30 a.m. folder tidy, the 8:00 a.m. feedback run, and the printed feedback are separate pieces of the teacher's routine and are not part of this change.
