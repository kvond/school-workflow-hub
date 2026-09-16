# Research agenda: conceptual understanding first

## Teacher-approved direction

The primary question is **Are students getting better at conceptualizing science
concepts?** Engaged agency, epistemic agency and authorial agency are separate
secondary dimensions, supported by examples from students' written work.
Neither completion credit nor agency evidence substitutes for conceptual
understanding. Longer writing alone is not evidence of improvement.

The current decision-draft book has now been supplied. Its relevant passages
were read and key rendered pages checked; see `RESEARCH_CODEBOOK_DRAFT.md`.
Definitions are source-grounded paraphrases (conceptual understanding is a
cross-passage synthesis). Observable indicators are explicit proposals, not
an author-approved rubric. The older separate research document remains unread.

## Implemented handoff for the existing HTML Agenda

`build_teacher_workspace` now includes a data-free `research-agenda/v1` contract
at `pages.research_data.agenda`. Its ordered dimensions put the primary question
first. The existing research access gate and approved-reference-only records
remain unchanged. No separate dashboard, database, model call, research crawl
or student-work processor was created.

The HTML owner should show the primary question above the three agency sections,
with the status **Source-grounded draft awaiting teacher review**. The contract's
`last_checked_at` records the local manuscript review, not a school-data crawl.
Its evidence records the book fingerprint and review scope. No student finding
is supplied. This local backend change is not a rendered or deployed UI change.

The finding/comparison contracts specify required fields and review rules; they
are not executable validators or proof that an excerpt was actually checked.
Do not enable coding or collection by merely filling a dictionary field.

## Evidence and comparison rules

- Each provisional finding needs its concept, dimension, versioned criterion,
  exact student excerpt, document revision, slide/response location, assignment,
  prompt, interpretation, alternatives, uncertainty and teacher-review state.
- Preserve both observation time and actual writing-date evidence. A file's
  modification timestamp or later-filled first-answer box does not establish
  when the student first held an idea. Unknown timing remains unknown.
- Within-lesson first/revised-answer comparisons require distinct evidence and
  verified response order. Across-lesson growth requires distinct assignments,
  the same learner, shared concepts/criteria, verified writing order and a
  reviewed explanation of task comparability. Preserve known instruction and
  AI/other support context; do not infer authorship or causal effects from style.
- Missing earlier writing means **insufficient evidence of growth**, not no
  growth. Unknown order means change direction unverified. Noncomparable tasks
  are not comparable, even when both are complete.
- An isolated excerpt may support a provisional observation about current work,
  not a growth claim or stable label for the student. Later class summaries must
  show eligible, missing and excluded cases rather than silently dropping them.
- Keep coding and teacher reviews versioned and append-only. Book changes require
  a new codebook version and explicit re-review; preserve prior interpretations.

## Next steps after source review

1. Review `RESEARCH_CODEBOOK_DRAFT.md` and its source locators. Extraction and
   source-grounded proposals are complete; teacher approval remains pending.
2. The teacher approved recording emerging student-designed inquiries separately
   from sustained independent learning paths. The contract carries separate
   evidence, teacher review and an optional link to an earlier inquiry; no
   automatic promotion, fixed duration or rank. Accept or revise the remaining
   proposed conceptual indicators before student coding.
3. Preserve an open case index, not a fixed agency ranking or personality label.
4. Implement evidence validation and review against a small authorized private
   sample; preserve uncertainty and compare reviewer interpretations.
5. Only then connect approved findings to the existing research page, retaining
   separate access controls and the shared evidence ledger. No student quotations,
   real source IDs or private reports belong in GitHub or public HTML.

Research findings never alter completion grades or erase grade/submission
history. Routine collection, external writes and cloud activation remain off.

## Tests

`tests/test_research_agenda.py` covers ordering, separate dimensions, reviewed
source versus unapproved criteria, evidence requirements, comparison distinctions, disabled actions,
restricted-record non-disclosure and independent payload copies. These tests
verify the structural contract, not the scientific validity of a rubric or any
live student's conceptual development.

Earlier structural validation: **369 tests passed and identical synthetic
replay**, including eight new research-agenda contract tests. School reads,
private inputs, external writes and live model calls were all zero. Nothing was
deployed or published by this continuation.
