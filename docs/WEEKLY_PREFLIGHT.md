# Weekly lesson-readiness preflight

Implementation and fixture tests cover the checks below. Deployment and live
source acceptance are separate release checks; a local test pass is not evidence
of current source or student access.

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

- **Lesson names.** The weekly folder's name is the lesson's name; the Agenda
  topic can be shorthand. A lesson is named by its linked weekly folder
  ("Cycle 06 — Cellular Respiration & Fermentation (Unit 1)" → "Cellular
  Respiration & Fermentation"), joined with the weekly folder of any linked deck
  kept elsewhere ("History of Forensics + Crime Scene to Courtroom (Mock
  Trial)"). Only "Week/Wk/Cycle/Unit NN …" folders count; a folder named only by
  its week ("Week 07") or a general folder leaves the Agenda topic. The Agenda
  topic is kept as `agenda_topic`.
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
  `=IF($P{row}="","—",HYPERLINK($P{row},"label"))` formula. When the column
  shares no formula (the Forensics 2nd-deck display column), the plan writes
  the same label text as a linked label to the hidden URL, the way a Hub
  lesson save writes one. The repair rereads the header rows and the row,
  writes only that plain cell, and reads it back; a repeat finds nothing to
  do. Formula cells, linked cells and typed prose are never overwritten; a
  visible link that disagrees with its hidden URL is reported.
  The Forensics hidden 2nd-deck slot before the folder column is expected.
  A narrowly bounded exception permits an empty or dash placeholder in the
  exact second-deck display column to become **2nd deck ↗**, using its existing
  same-row hidden Google Slides URL. This needs one unambiguous column pair
  and one exact URL; no title or different source is inferred. Both preview
  and apply verify current identity, Slides type, untrashed status and complete
  revision metadata, with unchanged header/lesson-row/URL bookends. Only the
  display cell is written. Exact link, unchanged row and source readback are
  required for a verified receipt; uncertain readback remains unconfirmed.
  Other blank cells with no established column formula remain unchanged.
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
  only exact teacher-deck and second-deck links in the current Agenda row may
  use the existing native teacher reader after a Slides export returns HTTP
  403. It checks the actual embedded codes and preserves missing/extra-code
  failures. A student-only link, a folder neighbour, an extra-material link or
  a similar title does not grant teacher-source authority. Native identity,
  geometry and completeness requirements remain unchanged. Other export
  failures remain unavailable;
  slide count, TEACHER REFERENCE / NAVIGATION slides, and their TO PRINT,
  BUY AHEAD, PREPARE and PUT OUT FOR STUDENTS blocks; the deck's web links
  (linked text, typed URLs, a URL under the line that names it). A bracketed
  authoring note on a slide students see — one opening with DRAFT, TODO,
  NOTE, TEACHER, added, corrected or verify and a dash or colon, such as
  “[DRAFT — confirm …]” or “[verify verbatim …]” — is one
  `authoring_note_on_student_slide` decision per slide, quoting the first
  ~80 characters: remove it or move it to speaker notes. Teacher slides,
  sentence-case prompts (“[Note: use mL]”), `[[codes]]` and boxes holding a
  code are never read for notes. An unreadable deck names the stage that
  failed.
- **Explicit deck addresses (bounded G2).** Native Slides and PPTX text runs
  retain their actual hyperlinks separately from displayed text. A whole
  displayed Google file URL whose link opens a different exact Google file ID
  produces `deck_link_target_mismatch`, with both addresses, source revision,
  lesson, slide number and native slide link where available. PPTX findings use
  the verified complete presentation order and existing file link; no viewer fragment
  is invented. Missing/malformed presentation relationships or unresolved slide
  references make the outline unreadable, rather than falling back to filename order. Split style runs with one target join; adjacent different links or
  partly linked addresses are never combined. Same-ID edit/copy/fragment
  variants, ordinary labels and unsupported addresses do not imply a mismatch.
  Findings stay in repair-worker diagnostics, outside Next from Agenda's teaching
  choices. Neither target is automatically selected or rewritten. This does not
  establish full G2 coverage, G1/G3/G4/G5 parity or retirement acceptance.
- **Worksheets index integrity (bounded G5).** Only an explicit Week ahead run
  lazily reads the native Worksheets tab. Ordinary Agenda/day/feedback/roster
  reads and saved-result GETs do not load it. One dimensions read and one grid
  read locate the named Course, Wk, Topic, Document, Type, Link and _fileID
  columns, with exact row/cell addresses. Missing, ambiguous, unreadable or
  malformed tables stay unverified; a successfully read empty index is distinct.
  Reads cover at most 1,000 rows and 26 columns, with explicit truncated coverage;
  at most 80 selected index rows are audited per run, with the rest explicitly
  deferred. Metadata uses the existing per-run exact-ID cache across selected
  weeks, at most 40 new reads and the existing time budget.
  Course aliases are explicit. W08, Wk 08 and Week 08 share a historical index
  locator; W08b remains distinct. These locators never bind a worksheet to a
  current lesson. Zero matching index rows does not mean that lesson has no
  worksheets. Unsupported course/week labels leave scope unverified.
  Blank or conflicting references, malformed backing IDs, unreadable exact IDs
  and trashed files produce worker diagnostics. A valid Google Link with a blank
  _fileID is checked by its visible exact ID and reports only the missing index
  field, not a missing file. Unsupported public/short addresses remain unverified
  and are not fetched. Folder references and unresolved shortcuts are unverified
  worksheet targets; members/targets are not followed. Metadata verifies identity
  and type/access only, not document content or student access.
  Repeated exact IDs, or identical explicit Document titles (case/whitespace
  normalized, within a course) with differing IDs, are nonblocking candidates
  retaining all checked source rows. No canonical copy is selected or deleted.
  Findings use source-cell fingerprints and live metadata, and stay as independent
  week exceptions outside Next from Agenda. `coverage.worksheet_index` records
  table, row and metadata coverage separately from lesson readiness, so an
  unavailable index never inherits a lesson's Ready result. The index does not
  change lesson/Form identity, print selection, packet membership or existing
  packet readiness gates. G3 timing bindings and full parity remain unfinished.
- **Print list.** Quantities from current roster counts per section ("1 per
  student", "1 per group" = ceil(n/4), optional "if needed" / support copies),
  matched to folder PDFs and Google Docs by name, with page counts and sides.
  When equally best candidates tie, the unique exact extra-material reference
  in that current Agenda row wins over folder-only copies. Same-ID references
  count as one file, ranked using the current Agenda metadata before matching;
  a stale same-ID folder/shortcut title cannot promote a worse current source.
  A better folder match is unchanged; multiple explicit
  choices or remaining ties still need resolution. The selected file still
  goes through normal metadata/page reads; a failure never chooses another
  copy. Both originals and teacher-controlled printing marks are preserved.
  A line with no folder file whose own text, or its deck's link list, gives
  an http(s) page is “print from web: URL” — a print instruction in the
  packet and in the Week ahead **Print list**, not a decision; with no URL it
  stays a decision. Key/answer files are TEACHER ONLY. Word files print from the
  lesson folder.
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

Rules v6 discard earlier cached checks and add explicit independent worksheet-index
coverage. Outlines retain hyperlink spans. Teacher readiness has a
separate cache identity from ordinary/student-only readiness, even for the same
file and revision. Before and after a new check, and before reusing cached
readiness or outlines, fresh Drive metadata must match the complete source snapshot from
the current run. A teacher read must also prove that its consumed source
metadata matches that snapshot. Incomplete metadata or changed identity, name,
parents, revision or modification time produces an unavailable check, never a
cached current Ready. These checks read sources only; no source, code or grade
is changed to make a lesson appear ready.

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

This module adds no scheduler or recurring job. Existing authorized routines
can use the same check endpoint; **Check now** is the manual entry point.
