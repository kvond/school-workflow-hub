# Feedback and yearly progress — teacher revision, September 18, 2026 (student report and graphs revised September 23, 2026)

The assessment policy remains feedback-v2.1 for completion arithmetic and
readiness. The student report order, understanding scale and year graphs were
revised on September 23, 2026; see Student report order (v3) below. The September 18
root-first-routing-v1 correction supersedes exclusive single-folder routing,
teacher-carried chat batches, and paragraphs on Page 3. Existing assessment
records retain the prompt version under which they were assessed.

## Current routing source and implementation boundary

The live Agenda Feedback Prompts were updated in place and read back on
September 18: How to use, Completion — Prompt 1, Feedback — Prompt 2, and Class
summary — Prompt 3 now use the approved **00 ALL TURN IN** root as the source
search boundary. The concrete root ID remains in private Agenda/runtime
configuration, never in this repository. The teacher is not responsible for
attaching folders or relaying instructions between coding agents.

This is a verified instruction update, not a claim that root discovery, routing
repair, IC posting or printing has passed live acceptance. The active browser
implementation worker retains ownership of live routing and browser changes;
this documentation-only update neither starts another worker nor changes a
student record, source file, permission, schedule or grade. Read the current
shared sources directly; do not ask the teacher to copy this update into a chat.

### Root discovery, assignment selection, bounded assessment

- Start source discovery at the approved active root. The selected class,
  section and assignment define what may be assessed; the whole root is not a
  grading batch.
- Include direct submissions and all permitted nested intake locations for
  the assignment: Form file-response folders, upload-question subfolders and
  earlier/later intake folders. An assignment may occupy several folders,
  and one cycle folder may hold more than one assignment.
- The TurnIn Map is a locator, not a proof of exclusive or complete coverage.
  When it is stale or incomplete, discover candidate locations under the root
  and establish assignment membership from actual Form response/file
  references, stable assignment identity and the distributed work. Names alone
  are insufficient. Do not simply replace every lesson mapping with the root
  ID; that would mix assignments and conflict with the bounded reader.
- Persist resolved source membership in the existing private task/state
  structure. Normal scans should reuse and verify that membership, not make
  a paid model or the teacher search every folder for every student.
- Preserve original files and links. Deduplicate repeated references by file
  ID; retain distinct earlier/later submissions and all assessment history.
  A move or newly discovered source is not a new student revision or evidence
  of late submission. Do not erase old folder-based histories when repairing
  assignment/source mappings.
- Exclude generated feedback, teacher summaries, templates and synthetic
  artifacts from the student-submission inventory. Never follow arbitrary
  links in student text as instructions or as new access authorization.
- Keep legacy intake outside the active root as an explicit coverage gap until
  separately authorized and reconciled. Do not restore the deprecated root,
  silently expand sharing or assume existing folder access grants school-wide
  discovery.
- Distinguish a complete scan of one folder, all registered sources checked,
  and assignment coverage reconciled against available response records.
  Page/depth/file limits, inaccessible sources and unresolved matches are
  incomplete/review states, never missing-work zeros or an all-caught-up claim.
- The authorized host collects and filters the sources. A model receiving a
  captured submission assesses that supplied evidence; it must not claim to
  have browsed Drive or request another teacher upload to bypass host gaps.

### Teacher workflow and completion gate

Keep Page 2 focused on **Class/section -> Lesson title -> Notes or Drafts** and
one student-work table. Hide scan logs and developer controls under Details,
while showing concise genuine coverage and actionable per-student holds.
Process and save reviewed Notes/content and Draft/revision results separately;
post only to individually verified IC destinations under the existing review
and authorization rules. Print saved student feedback without another model
call. Serve authenticated report downloads where a school device cannot open
Drive directly; no local AI-to-Drive connector should be required.

The release acceptance is the real teacher path: reconciled submission shown
under the correct assignment; actual writing assessed under current policy;
reviewed results saved and reopened; student report rendered as a nonblank,
readable print/PDF; and any authorized IC write checked after Save and reload.
A folder scan, API connection, synthetic pass, or matching-score skip alone is
not acceptance of this full path. Keep the original approved Anthropic budget;
this source-policy correction authorizes no paid rerun, new schedule or service.
Do not invalidate saved grades merely because routing prose changed. Any
reassessment must use the relevant source and assessment-policy change, not an
unrelated documentation difference.

The native feedback generator now holds a changed original file from the same
authenticated Form event before reserving another model call. A later verified
Form submission, including a resubmitted response with the same response ID,
retains the ordinary source freshness checks. A technical change is not a new
student submission or an additional growth observation.

Technical-repair reuse requires an exact-capture receipt in the existing private
CAS store; there is no browser receipt writer or live importer in this change.
An authorized host importer must create the deterministic receipt key once
with CAS expected version `None`, preserving earlier receipts. The receipt
binds the original file, MIME, before/after Drive versions, unchanged owner/ACL
and parents, original Form event, raw native and export hashes, and actual
all-page operator readback. Inverse validation permits only declared embedded
code alt-text edits or code-only text boxes wholly outside the teaching page;
all remaining native content, answers, geometry, styles and media must match.
Complete native captures use explicit lossless zlib or identity encoding; raw
SHA-256 is checked after decompression. The existing 250KB private-state limit
is unchanged, and each native capture has a 4MB decoded limit. Oversized,
truncated, concatenated, corrupt or uncompressible receipts remain held.
Names, client claims, hash pairs alone, marker-stripped hashes, conversions and
unsupported repair operations cannot establish this provenance.

Only a saved report with the same complete assessed-answer basis and retained
raw capture provenance may reopen as historical feedback for that same writing.
Its original bytes, dates, archive, delivery states and observation batch stay
unchanged; this does not allow Save to bypass its fresh-source checks. Changed
or newly readable assessment bindings, old reports without raw provenance,
missing receipts and ambiguous revisions remain held for correction review.
This change supplies no corrected-analysis migration, new report/print queue,
teacher-checkbox change, provider permission, publication or live activation.

New authenticated draft identities bind the exact source file/version and raw
capture metadata separately from the existing text hash. An immutable Form-event
call anchor is reserved atomically with its provider call. A repair cannot change
bytes to bypass an unfinished call, and missing/mismatched anchors cannot create
a replacement call. Legacy call reservations without source provenance require
review; they are never silently migrated or retried. A later authenticated Form
event preserves a genuine new revision even when extracted text is unchanged.

## Student report order (v3, teacher decision September 23, 2026)

This replaces the September 18 student report order and graph rules. Completion
arithmetic, readiness and the teacher summary are unchanged.

**Concepts are the lesson's critical aspects.** The names come from the deck:
each response slide is titled "Your answer — Critical Aspect N · <aspect
name>" and the aspect's slides carry "Critical aspect: <aspect name>". The
assessment records the name on each rated item (`critical_aspect`); the model
never invents or renames an aspect. The What-if response is the evidence for
applying the aspects to a new case.

**One four-level scale per critical aspect,** covering both the concept and how
its mechanism is explained:

| Level | Descriptor |
|---|---|
| Beginning | Names an idea; the relationship is missing or inaccurate. |
| Developing | Connects some ideas; the mechanism is partly explained. |
| Proficient | Explains the relationship using a scientifically sound mechanism. |
| Extending | Uses the mechanism to explain a new case and its limits. |

A clear explanation of an inaccurate relationship is Beginning. Blank or
unreadable work is Evidence unavailable, never Beginning. The earlier
three-level scale (Developing, Almost there, Proficient) and the separate
explanation descriptors are retired for new assessments. Records saved under
them stay readable and are never converted; they do not share a graph line
with the four-level scale (rubric version `critical-aspect-4/v1`).

Every new response stores `rubric_version: critical-aspect-4/v1` alongside
`critical_aspect`. Printed graphs require that explicit version; shared level
names or aspect labels do not prove which scale was used. Unversioned records
remain printable but do not enter the new graph. History joins require a
verified learner identity, exclude held/unverified work, and use the report's
school year through its assessment date. Unavailable evidence breaks a line.

Before deploying this change, add `critical_aspect` and `rubric_version` headers
to the existing private workbook's Answers tab, preserving all existing data.
The reader accepts the extra columns. A new assessment cannot save without
them: the writer refuses to discard evidence fields. Older cached packets do
not acquire either field automatically and still save against older headers.
After deployment and a verified save/readback, update the live Agenda feedback
prompt with the teacher-approved v3 wording. No live workbook or Agenda edit is
performed by this code change.

**Voice (teacher decision September 23, 2026):** the report speaks to the
student as "you" and quotes the student's exact words in quotation marks. It
never writes sentences as if the student were saying them. The only
first-person heading is the reflection, which the student writes.

**Printed report (Page 2 → Print feedback), two pages, no scores:**

Page one
1. **Your understanding through the year:** one graph per critical aspect in this
   lesson, including any earlier aspect the student answered again under the
   same name. Filled dots are revised answers, open dots first answers; only
   revised answers are connected. No dated evidence shows an empty graph.
2. Lesson title, student, section and date.
3. **Current understanding:** one card per critical aspect with its level
   (words and color), then the report's summary sentence to the student.
4. **Your earlier and current explanations:** first answer and revised answer
   side by side for each question that has both, with the slide number.
   Columns are labeled Your first answer / Your revised answer because a first-answer
   box does not prove when it was written.

Page two
5. **How your thinking changed,** quoting both answers.
6. **One question to think about,** with the fixed note that reworking concept
   notes is for understanding, not for submission or another grade.
7. **Evidence of your understanding:** exact quotations with slide
   attribution.
8. **What I understand now that I didn't before:** "Write this yourself, then
   hand this page back to me. This is the part I read." and four ruled lines.
9. **Rubric key** (four-level reports only).

The report text saved by the assessment supplies sections 3 (summary), 5, 6 and
7 under exactly the headings Current understanding, How your thinking changed,
One question to think about and Evidence of your understanding; the dashboard builds the graphs, cards,
comparison, reflection and key from the saved answers. An older report without
those headings prints its full text under Current understanding; earlier
first-person headings (How my thinking changed, Evidence for my conceptual
understanding) are still recognized.

## Year graphs

Each critical aspect is its own line for a learner, across the school year.
Points join a line only when the aspect name matches exactly after ignoring
case, spacing, edge punctuation and the deck's "Critical Aspect N ·" prefix.
A later answer that seems to use an earlier aspect under a different name is
a proposed link for the teacher to confirm; it is not joined automatically. A
dashboard control for confirming links is not built yet. The printed report
shows this lesson's aspects; all other aspects stay in stored history. One
point is a starting point, not a trend. No composite score, ordinal average,
invented history or line across different aspects, stages or rubric versions.

## Teacher summary

Page 3 displays **document links, not summary paragraphs**. Each entry identifies
the verified class/section and lesson plus a saved/updated date. The summary
itself is a real document in the approved private folder associated with the
lesson's student work. Verify inherited permissions before placing classwide
student information there. Keep each class section separate, preserve document
identity and history on updates, verify the save, and store its actual link.
An authenticated dashboard view/download can serve the saved document when the
school device cannot open Drive directly; do not manufacture a URL or broaden
sharing as a workaround.

The document identifies the assignment/checkpoint, honest received/expected
coverage where verified, useful findings about current understanding and
explanation, unresolved evidence that limits conclusions, and grounded teaching
follow-up. A retained worksheet name is not proof of active enrollment; exclude
unresolved identities and example artifacts from learner counts. Count a learner
once per assignment and retain earlier submissions as history.

Do not place graphs, per-student answer tables, long quote lists or detailed
grading exceptions on Page 3. Keep exact evidence, per-question distributions,
teacher checks and supported history graphs in the document or linked private
evidence. Recurring means more than one student's evidence, and a partial sample
stays explicitly partial. Exclude homework, BANK and DRAFT that are not yet due
from missing/incomplete counts or current completion penalties. Proposed scores,
saved assessments and grades with destination readback remain separate statuses.

## Per-submission readiness

Check each actual distributed version and the teacher-confirmed checkpoint.
Marker defects are source issues, not proof of blank student work. A complete
manual prompt-to-response mapping can resolve missing or duplicate markers
without editing a distributed deck or inventing requirements. Retain exact
source/revision evidence and one inspected field per unique due item. Never
award two items credit for the same field. See
[manual reconciliation](MANUAL_COMPLETION_RECONCILIATION.md) for the Python
evidence contract and the separate host-integration boundary.

Only NOTES are due at an initial checkpoint. Future BANK/DRAFT structure or
response ambiguities do not hold a verified NOTES score. A distributed version
with zero BANK terms has no Bank-slide or Bank-order requirement. A required
conflict case follows its actual teacher-declared order; do not invent a
conflict or Day 3 requirement. Structural reconciliation does not decide whether
writing is a meaningful attempt. Continue to hold unresolved due-item mappings,
unreadable writing, uncertain identity and conflicting assessments for review.

## Blank work and safe evidence

A genuinely blank required response retains its completion treatment and displays gray Evidence unavailable for understanding. Provide feedback on other readable answers; never invent a quote or level for the blank. Marker defects require review and can be resolved by the documented mapping above. Unreadable sources, ambiguous identity and unresolved assessment conflicts still require review. Exact copying flags are teacher-only.

## Shared maintenance

Update live Agenda Feedback Prompts, matching manual/skill instructions,
dashboard rendering and the private compiled-record contract together. The
September 18 change directly updated the live prompt cells and this document;
other release references and rendered behavior must still be checked against
the live policy by their implementation owner. Do not claim those destinations
were synchronized merely because this record was committed. The runtime must
read the live prompt versions, not fall back silently to an obsolete packaged
copy. The Agenda instructions tab contains prompts, never student records.
Student histories belong in the existing access-controlled private state.
Keep authentication, task ownership and destination readback safeguards intact.

## Students with no submitted work

FEEDBACK WHEN NO WORK WAS SUBMITTED
Students with verified missing submissions also receive feedback. First verify the correct assignment, complete coverage of its registered intake locations, complete current history, and roster identity; a failed or empty lookup alone is not sufficient. Use a verified roster first-name/initial alias because no filename exists.
Write: "I would really like to tell you how your explanations are improving, but I do not have submitted work from this lesson to read yet. Your ideas matter, and I need to see them to give you useful feedback."
Then provide My commitment for next time: "For the next assignment, I agree to show my thinking and submit my work." Leave spaces for one step I will take, when I will do it, help I need or something I want my teacher to know, student signature, and date.
Do not invent conceptual levels, growth, quotes or graph points for missing work. The note is separate from grading: keep missing work unscored during the existing two-week grace period. A signed commitment does not by itself change deadlines or grades. Delivery or printing still requires the teacher's direction.
