# Weekly lesson-readiness preflight

Status: implemented and fixture-tested. Not deployed or live-verified; no real
Agenda, Drive folder, deck or packet has been checked by this code yet.

## What runs

The Today page's collapsible **Week ahead** section reads the last saved check
for 1–4 weeks from a chosen Monday. **Check now** runs it. Routes (signed-in
teacher; POSTs are same-origin JSON like the other Hub POSTs):

- `GET /v2/api/weekly-preflight?start=YYYY-MM-DD&weeks=1..4` — the saved
  result and saved packet, from private state only. No source is read.
- `POST /v2/api/weekly-preflight/run` `{start, weeks}` — reads the Agenda,
  roster counts and each lesson's sources with the Hub's read token.
- `POST /v2/api/weekly-preflight/review` — saves one reviewed record.
- `POST /v2/api/weekly-preflight/repair` `{fingerprint, dry_run}` — one
  Agenda link label (dry run unless `dry_run` is `false`).
- `POST /v2/api/weekly-preflight/deck-repair` `{op, deck_id, ...}` —
  `replace_text` or `sync_inventory` on a Google Slides deck (dry run unless
  `dry_run` is `false`).
- `POST /v2/api/weekly-preflight/packet` `{start, weeks}` and
  `GET /v2/api/weekly-preflight/packet.pdf?id=...` — the Monday packet.

Code: `src/hub_v2/weekly_preflight.py`, `weekly_packet.py`, `deck_repair.py`;
`google.folder_listing` reports whether a folder listing is complete.

## Deterministic checks

- **Calendar.** The Weeks tab's closures cell for the week decides which
  Friday is A or B and which dates are closed (In-Service, Schools Closed,
  OFF). A4 Biology and A2/A3 Forensics meet Mon, Wed and A Fridays; B3 A&P
  meets Tue, Thu and B Fridays. A Friday the cell doesn't name is a decision,
  not a meeting. Dates stay plain dates; the workbook timezone is untouched.
- **Plans.** A "doesn't meet" plan on a real meeting day, plan content on a day
  the section doesn't meet, a dated claim the calendar contradicts ("moved
  from Fri 10/23 — in-service"), VERIFY DAY / Check the day / NEEDS CONTENT,
  and an empty plan on a meeting day. Prep cells ("PREP by Thu 10/22 → LAB …")
  are deadlines, never meeting claims.
- **Agenda links.** A visible label that is plain text while its hidden URL
  cell holds a link is repairable when the same column's other rows share one
  `=IF($P{row}="","—",HYPERLINK($P{row},"label"))` formula. The repair rereads
  the header rows and the row, writes only that plain cell, and reads it back;
  a repeat finds nothing to do. Formula cells, linked cells and typed text are
  never written; a visible link that disagrees with its hidden URL is reported.
  The Forensics hidden 2nd-deck slot before the folder column is expected.
- **Sources** (teacher deck, the `/copy` student template — the same file,
  folder, 2nd deck, extra material, Turn-in Forms with their published
  responder IDs). Trashed or unreadable sources are `inaccessible`; a folder
  listing that hit the page limit is `incomplete`; a deck outside its folder
  and decks in the folder that no row of the week links are reported only. A
  Form shared by several rows is not an error. Submissions are never read, so
  an untaught lesson never shows missing work. `HUB_WEEK_EXCLUSIONS` weeks
  skip the Form checks only.
- **Deck content.** Completion codes via `audit._release_deck` (non-ready
  results read "Needs embedded codes" with the missing/extra IDs and slides);
  slide count, TEACHER REFERENCE / NAVIGATION slides, and their TO PRINT,
  BUY AHEAD, PREPARE and PUT OUT FOR STUDENTS blocks. An unreadable deck names
  the stage that failed.
- **Print list.** Quantities from current roster counts per section ("1 per
  student", "1 per group" = ceil(n/4), optional "if needed" / support copies),
  matched to folder PDFs and Google Docs by name, with page counts and sides.
  Key/answer files are TEACHER ONLY. Word files must be saved as PDF or Doc.
- **Supply list.** BUY, PULL, on order and deck blocks, merged by name with
  every source kept. Statuses are buy, pull, on order (not verified in hand)
  and stock unknown; nothing is marked in hand or bought.

## Reviewed parts

A person saves review records — a meeting's 90-minute flow, a VT verdict, a
print/digital decision, a visual check — bound to the source revisions in the
latest check. When a bound source changes, the record reads "needs re-review".
Reviews do not change a lesson's deterministic status. The packet prints a
reviewed flow, or says "Flow not yet reviewed".

## Statuses

Exceptions: `needs_decision`, `inaccessible`, `repairable`,
`repaired_and_verified`, each with class, sections, week, date, lesson,
slides, source link, reason, next action and a stable fingerprint. Report-only
items are `blocking: false`. Lessons: `ready`, `repaired_and_verified`,
`needs_decision`, `inaccessible`, `incomplete`. A lesson is ready only when
every required check finished and nothing blocking is open. Access evidence
is the Hub's own token; student access is not verified.

## Storage, caching and resume

Everything lives in the existing private state (`configured_store`,
Firestore). Content checks are cached by check, source ID, source revision and
rules version, and saved as each finishes; a run that runs out of time is
`incomplete` and the next run resumes from the saved checks. Week results,
reviews, repair receipts and packet manifests use content-derived keys, so
reruns write no duplicates.

## Monday packet and email

One PDF: cover (weeks, sections, generated time, source fingerprint), each
class and meeting date (flow, print list with quantities, page ranges and
sides, teacher prep), the supply list, the student handouts, then a separated
TEACHER-ONLY section. It is built only from a finished check, stored like the
daily feedback packet (reserved Drive ID in the existing private
`HUB_FEEDBACK_DAILY_PACKETS_FOLDER`, readback, manifest in private state), and
identical inputs return the saved packet.

It is not student feedback and does not enter `print_ready_email`: it is
never in a feedback folder, its name has no "feedback", and its
`hubWeeklyLessonPacket` tag is skipped by the feedback catalog. Emailing it
would need its own explicit outbox (recipient, send reservation and Sent
readback like `print_ready_email`); that is not built.

## No schedule

No scheduler or recurring job was added. When the weekly check runs (for
example Friday afternoon or Monday morning) is the teacher's decision; today
it runs when she presses **Check now**.
