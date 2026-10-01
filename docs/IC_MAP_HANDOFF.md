# Handoff for Codex: fill and maintain the helper's IC assignment map, add Run now

Written October 1, 2026 by Claude (Cowork) for the teacher, after the 0.4.0 morning build was merged. Read `browser-extension/README.md` ("Opt-in weekday mornings (0.4.0)") first.

## 1. The map is built. Load it; don't commit it

The teacher asked Claude to build `HUB_IC_ASSIGNMENT_MAP`. Claude read it from Infinite Campus's own data in the signed-in school browser. Nothing in IC was typed or saved. Two sources were used:

- `/campus/api/instruction/curriculum/section/alignmentSummaries?sectionID=<id>` gives every assignment in a section: its id (`curriculumAlignmentID`), its code (`abbrev`, for example C04F), its dates and its name.
- `/campus/api/instruction/curriculum/section/content/<assignment id>` gives `gradingAlignments[0]`: the score group (`alignmentID`), `taskID` (13 for every assignment), `termID` (726 to 729, depending on the assignment's dates) and `totalPoints`.

One live score panel was checked against these values: Carbon Cycle (F), A4 Biology. Its inputs are `as_alignmentMultiScore_201723-*`, its label is `C04F MP: …`, and its progress marker is `progressScore_726_13_26716`. All three match the API.

The map is a private file in the teacher's school Drive, inside "Student Feedback — Private 2026–27": **Hub IC assignment map (HUB_IC_ASSIGNMENT_MAP) — 2026-10-01.json**. Per `AGENTS.md`, live identifiers stay out of the repository. Ask the deployment owner to load that file's contents into the Hub's `HUB_IC_ASSIGNMENT_MAP` runtime setting.

The map covers the 7 catalog Forms that matched one-to-one:

- Biology A4: Wk 02 Ecosystems (C02F, C02S), Wk 03 Energy Flow (C03F, C03S), Wk 05 Carbon Cycle (C04F, C04S).
- Forensics A2 and A3: Wk 04 Eyewitness (W04F, W04S), Wk 05 Types of Evidence (ToE(F, ToE(S) and Wk 05 Investigative Processes (W05F, W05S).
- A&P B3: Wk 02 Introduction to Anatomy (W02F, W02S).

Each target's `term_id` is that assignment's own term from IC, not always 726.

Left out because the match isn't one-to-one. These are held until the teacher decides:

- Forensics Wk 01: the catalog row has no title. In IC, A3 has "Sheppard Case Study" (SCS) and A2 has nothing.
- Forensics Wk 02 "Intro to Forensics": IC has only W02S "Lab Safety, Equipment & the Microscope", with no (F) column, and the title doesn't match.
- Forensics Wk 03, two Forms (Inductive and Deductive Reasoning; Observation Skills): IC has W03S and W03F "Observation & Deductive Reasoning" plus "Induction Deduction Notes" (IDN). The teacher said to leave inductive/deductive alone for now.
- A&P Wk 01 "Course Overview & First Day": IC has W01F "Lab Safety Stations" only.
- A&P Wk 03 "Introduction to Anatomy (cont.)": there is no W03 assignment in IC.
- A&P Wk 05 "Cell Structure and Function": IC has both W04 "Cell Structure and Function" and W05 "Cell Structure and Function (cont.)", so it's ambiguous.

## 2. Two code fixes the map needs

- **Label codes with "(".** The Types of Evidence assignments are coded `ToE(F` and `ToE(S` in IC. `morning.js` `target()` only accepts `^[A-Za-z0-9_-]{1,80}$`, so those two Forms will be held. The code is still matched exactly against the rendered input label, so allowing `(` and `)` is safe. The alternative is for the teacher to rename the codes in IC to ToEF and ToES.
- **Silent helper on the wrong panel.** If the score panel is opened from the Curriculum list's "Scored" count, the route is `multiscore/curriculumList/...`. `surface()` fails with `destination_context_changed`, and the launcher just never appears. The teacher lost most of a morning to this. When an IC score panel is open on that route and a list is waiting, show a short note instead: "Open this assignment from its title (then Score) for the Hub helper."

## 3. Build the map every morning instead of by hand

The teacher wants opening the Hub and IC to be enough. Use the same two read-only IC endpoints from the IC tab, inside the morning worker, before any entry:

1. For each section in `roster.SECTIONS`, list the assignments with `alignmentSummaries?sectionID=`. The IC section ids belong in runtime config, not in the repo.
2. Match a catalog Form to IC assignments by course and week number, using the `abbrev` codes: C<nn> for Biology cycles, W<nn> for Forensics and A&P weeks, an F suffix for notes and S for revision. Confirm with name words shared between the Agenda topic and the IC name. Exactly one notes and one revision assignment per section, or the Form is held with the reason.
3. Read the score group, task and term from `content/<id>`. Require `totalPoints` 10 and scoring type `p`.
4. Compare the result with the loaded `HUB_IC_ASSIGNMENT_MAP`. If they agree, proceed. If they disagree, hold that Form and report both values; never silently prefer the generated one. A Form that has no loaded row but has an unambiguous generated match may proceed only if the teacher turns on a second popup setting, "Use matches found in IC". That setting is off by default.

Report every held Form on the Routine page with its reason.

## 4. Run now

Add a **Run now** button to the helper's popup. It runs the same morning path once, with the same rules: one run per day counts toward the daily attempt, it stops on sign-in pages, and it never retries. That way the teacher can open the Hub and IC at any time and press one button, without waiting for 7:00.

## 5. One Hub message to correct

When the Agenda's linked deck exists but the Hub's service account can't open it, Student work says "No handed-out copy of this deck is linked in the lesson's Agenda row." That sent the teacher looking for a missing link that wasn't missing. Say instead: "The linked deck can't be opened by the Hub (share it with the Hub's account as Viewer)." On October 1, Claude shared the Agenda's Biology decks with the service account as Viewer.
