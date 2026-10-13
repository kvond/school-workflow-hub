# Weekly preflight

The Hub's **Next week** page checks a whole teaching week before it starts and
shows only what needs the teacher, plus the week's print packet and supply list.
It is read-only: nothing in the Agenda, Drive, Forms or Schoology is changed and
no model is called. Code: `src/hub_v2/preflight.py`; tests:
`tests/test_hub_preflight.py`.

## When to run it

Thursday or Friday of the week before. Open **Next week** (it picks the next
Agenda week; any week can be chosen), work the **Fix before Monday** list, then
choose **Print packet (PDF)** and print it with the supply list on its cover.
Run it again after fixing things; a clean week shows **Ready**.

## What it reads

For each class row of the week in the Agenda: the day plans and notes, the
lab / prep cell, the teacher deck and student copy links, the lesson folder,
the Turn-in Forms and the Agenda's Worksheets tab. It reads each teacher deck
through the Slides API (text, links, skipped slides) and the roster for copy
counts. It also reads every other Agenda row that links the same deck, because
one deck often spans two weeks.

## What it checks

- **Schedule.** Meeting days come from each class tab's day columns, the Weeks
  tab's closures (`⚠ Mon 10/19 …`, closed all week, `Mon–Tue only`) and its A/B
  Friday (`Fri 10/23 = A Friday`; Mon/Wed classes are A, Tue/Thu classes are B).
  It reports a meeting with no plan or `NEEDS CONTENT`, a meeting whose plan
  says the class doesn't meet, planned teaching on a day the class doesn't meet,
  a plan dated for another day, and a due date that falls on a non-meeting day.
- **Deck days against meetings.** Dividers (`Day 2 of 3`, `MEETING 4 OF 5`) split
  the deck into class days. Meetings take the days in order, except that a plan
  naming two words found in only one day's slides (for example "yeast" and
  "balloon") is matched to that day. A deck with more days than meetings is
  reported, and so is a DRAFT (revision) due date before the revision day or
  before the last critical aspect is first taught. A Notes due date before the
  last teaching day is reported because the Hub would score empty boxes.
- **Variation Theory.** The TEACHER REFERENCE slide's CRITICAL ASPECTS are the
  contract. Each aspect needs slides labelled `Critical aspect: <name>` that
  carry, in order, the question, contrast set, build-a-rule, pattern break and
  3-tier / mastery, plus a `DRAFT:…:CA<n>-MASTERY` revision box. It also checks
  that aspects are taught in the declared order, that revision steps
  (`Slide 17 — Critical Aspect 1`) still point at the right revision box, and
  that the teacher slide index still matches after slides were moved.
- **90 minutes.** Each deck day gets a rough minutes estimate (an explicit
  `(10 min)` wins; writing boxes, labs, readings, exams and revision steps have
  fixed weights). A day under about 55 or over about 110 minutes per meeting is
  listed under **Decide**. The per-meeting estimates are on the page.
- **Student-facing slides.** Dates that aren't the deck's meeting dates (a deck
  reused from an earlier week), authoring notes and image placeholders such as
  `[added — …]`, `[DRAFT — confirm …]` or `[ image: … ]`, a shown address that
  opens a different file, and one file linked under two different names.
- **Links across systems.** An unreadable or trashed deck, folder or Form; a
  student copy link for a different deck; a Form or folder labelled for another
  week; a Worksheets tab row with no document; and several copies of the same
  worksheet in the Worksheets tab, the deck and the folder (the packet uses the
  Worksheets tab copy).

## What it produces

- **Exceptions** in three groups: Fix before Monday, Decide, Tidy when you can.
  Each has the class, day, one sentence, the smallest fix and a link.
- **Print list and packet.** Items come from the deck's `TO PRINT`,
  `PRINT / HAND OUT` and `PREPARE` (lines starting "Print") headings and from
  exams named in the plans. Each is tied to the day that uses it (by slide
  number, `(Day 2)`, its critical aspect or its name on a slide); items for days
  taught in another week are left out. Copies use the roster: 1 per student
  plus 2 spare, 1 per group of 4, one class set (largest section) for reference
  sheets, or support copies. The PDF has a cover checklist and supply list,
  then every item that is a Google Doc or PDF; anything else is listed to print
  by hand.
- **Paper or digital.** A worksheet whose critical aspect already has answer
  boxes in the deck is reduced to support copies. A Google Doc worksheet gets a
  student `/copy` link (turned in with the lesson's Form). Labs stay on paper.
- **Supplies.** The Agenda's lab / prep cell (`BUY:`, `PULL:`, `✔ on order — …`,
  `🛒 …`, `⚠ ADD …`, `PREP by …`) and the deck's BUY AHEAD, PUT OUT and demo
  MATERIALS for this week's days, merged into Buy, On order and Pull lists. The
  Agenda's status wins over the deck's.
- **Schoology links.** The Hub has no Schoology reader. The page lists the exact
  student deck `/copy` link and Form link for each lesson to post or confirm.

## Routes

- `GET /v2/api/preflight[?week=Wk 08]` returns the JSON the page shows.
- `GET /v2/api/preflight/packet[?week=Wk 08]` returns the print packet PDF.

## Limits

- The checks read text. A deck that doesn't use the teacher-reference format
  gets one "can't check" line instead of VT results.
- Minutes are estimates for spotting outliers, not a schedule.
- The service account needs Viewer access to the decks, folders and Forms; an
  unshared file is reported as unreadable, the same as a deleted one.
