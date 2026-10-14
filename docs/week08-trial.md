# Trial run: Week 08 (Oct 19–23, 2026)

This was a read-only run against a snapshot of the real Agenda, week folders, decks and worksheets, taken Oct 14.
Schoology was not included because no API key was configured yet.

## What it caught

**Fix before class (7):** three blockers came from the Agenda calendar. Biology Wed 10/21 and Fri 10/23 have no lesson.
Forensics Fri 10/23 has no lesson, and its Friday plan says "B Friday" while the calendar says A Friday.
The Forensics Wk 08 deck link, student link and folder link all fail to open. The run named the real folders it found by name:
*Week 08 - History of Forensics* and *Week 08b*.

**Fix this week (30), for example:**
- A&P's Wk 08 row points at Week 07 materials: deck, folder and turn-in form. Its TurnIn Map key says *Skeletal System*.
  Its Thursday plan was shifted for a 10/23 in-service that isn't on the calendar.
- The Forensics turn-in form is titled *Wk 09*, the same deck is linked for Wk 08 and Wk 09, and four Worksheets-tab links are dead.
  Copies with the same names are in the week folder.
- The Forensics deck tells students to submit in Google Classroom. Its critical-aspect labels aren't in the exact form the feedback prompts read.
  The Locard aspect has no Build a Rule, Pattern break, 3-tier question or response slide. The inventory lists `CA1-MASTERY`, which the deck no longer has,
  and leaves out `Q-MASTERY-2`. The deck holds about 459 minutes of work for 2 meeting days.
- The Biology deck shows the *filled-in* rule on slide 14, right after the Build a Rule slide.
  Slide 21's speaker notes still hold an unfinished edit ("this should compare an animal to yeast…").
- The A&P deck shows editing notes to students ("[added — …]", "[corrected — …]", "[DRAFT — …]") and is still dated 10/13–10/16.

**Packet and lists:** the run listed 23 print items with copy counts. Dead links were replaced by the week-folder copies.
It also produced a supply list: buy carrots, sugar, marshmallows, …; confirm yeast, balloons and water bath arrived; pull trays, bottles, safety glasses, ….
Two Biology worksheets already exist as typed response boxes in the deck, and the Forensics *Second vote* "circle one" can become a typed choice.

## What the first pass got wrong, and the repairs

| Problem in run 1 | Repair |
|---|---|
| Two-column teacher slides mixed "PREPARE" lines into the print list | Read slide text in document order, not screen position |
| `[[DRAFT:…]]` response labels were reported as "editing notes" | Editing-note pattern ignores `[[…]]` labels |
| The A&P response slide counted as missing because it says "First answer", not "Your first answer" | A response slide is any slide with a `[[DRAFT:` box |
| Print list repeated the same worksheet 3–4 times (deck list, Worksheets tab, folder file) | Merge items with the same title; keep the one with a file |
| Dead Worksheets-tab files made unprintable packet items | Fall back to the same-named file in the week folder |
| "Skip printing" was suggested for almost everything | Suggest it only when most of a worksheet's actual questions already exist as response boxes |
| Supply list included "and a lighter", worksheets, and demo MATERIALS details | Strip leading conjunctions, drop paper items, read only BUY / PUT OUT / PULL |
| Exam and revision days were flagged as "too thin" | Skip the thin-day warning when the day holds an exam, lab or revision |
| The flow budget counted a "does not meet" Friday as a teaching day | Only days with a real plan count |
| Four separate dead-worksheet lines pushed other items past the per-course cap | Grouped into one line |

The tests in `tests/` reproduce each of these cases with synthetic decks and Agendas.
