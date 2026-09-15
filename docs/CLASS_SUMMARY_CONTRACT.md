# Class summary: compiled feedback contract

`workflows.class_summary.build_class_summary` consumes the structured
`student-feedback-compiled/v1` records produced by `student_feedback` for **all
batches of one class and assignment**, including classes larger than twenty.
It does not ask for decks again, call a model, or judge student writing.
The host must authenticate access and verify the records' provenance before
passing private input. These records must not be embedded in public source.

Required arguments: `records`, `class_key`, `assignment_key`.
Supply `expected_learners` and, when batch coverage is known,
`expected_batch_keys`. Unknown or missing coverage is explicitly `partial`;
a complete expected student set is required for `prepared_for_review`.
Duplicate students across batches are rejected instead of double-counted.
An empty class cannot be reported as verified complete.

Output `class-summary/v1` includes:

- `coverage`: received/expected counts and missing students/batches.
- `participation.by_stage`: initial and final reported independently, with
  NOTES, BANK, DRAFT completed/required totals, complete students and unavailable
  students. Initial BANK/DRAFT are not yet due; zero denominators are N/A.
- `participation.timing`: primary evidence classification per student.
  `comparison_timing` separately counts students with known later completion,
  documented revision, genuine earlier answers, or unknown timing from their
  per-question comparisons. These counts may overlap.
- `proficiency_by_question`: Developing, Almost there, Proficient, unavailable.
- `recurring_misconceptions`: only assessor-defined ideas supported by more than
  one distinct student, preserving exact quotes and supplied timing labels.
- `instructional_contrasts`: teacher/assessor-supplied concrete case pairs,
  accepted only with references to an included recurring misconception's
  evidence. Absent advice stays `needs_assessor`; no keyword advice is invented.
- `teacher_checks`: supplied review reasons and unavailable assessment evidence.

`external_writes` is always zero and `execution_supported` false. A prepared
summary is a review artifact, not a claim of live execution or verified delivery.
The host adds its authenticated source/check time and teaching-date context in
its dashboard response envelope. Completion-policy arithmetic is reused upstream
by `student_feedback`; this reducer consumes verified counts without rescoring.

Validation: fourteen synthetic summary tests, plus a local composition check
from the student-feedback fixture into this reducer. No live school processing.
