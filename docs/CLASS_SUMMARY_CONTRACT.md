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
- `proficiency_by_question`: Beginning, Developing, Proficient, Extending, unavailable (four-level critical-aspect scale since September 23, 2026; Almost there appears only in older records).
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

## End-of-lesson teacher review — October 18, 2026

Every completed review of one class/section and lesson includes a short
**Lesson review for tomorrow** in its existing private teacher-summary document.
This requirement is independent of how many individual student feedback sheets
are created, selected, printed or emailed, including zero or only a few. Use all
verified reviewed evidence available for the exact lesson, not just the students
selected for a printout. Do not rerun an assessment merely to write the summary.

This is the authorized host's summary-authoring and saving stage. The reducer
above still only aggregates supplied evidence; it does not create dialogue,
infer a student's ideas, call a model or save a document. Its coverage and
`prepared_for_review` gates are unchanged. A policy change or a prepared summary
is not proof that this stage executed or that a teacher report was saved.

Start with the class/section, exact lesson/week/cycle, review date and actual
reviewed coverage. Use large text, short headings, one idea per line and simple
teacher-controlled checkboxes. Make the report usable while talking at a
whiteboard, with these parts:

1. **Interesting student ideas.** One to three distinctive, promising or
   surprising ideas found in the reviewed work. Keep exact private source
   references; anonymize wording intended for the board. A teacher-created
   example is labeled as such, never attributed to a student.
2. **What needs clarification.** The most common supported misunderstandings
   or partial ideas among the reviewed students, with verified counts when
   available. Preserve earlier/current/unknown timing. One student's example
   is not a recurring or class-wide pattern; do not invent a misconception to
   fill this heading. If none is supported, say so.
3. **Before the next lesson.** Two or three concrete dialogue questions tied
   to those ideas. For each give **Write on the board**, **Ask**, and **If
   discussion stalls, say or show**: a brief explanation, contrasting cases or
   simple sketch the teacher can present directly. Keep board text short and
   practical. Connect to the next lesson only when its identity/content is
   verified; otherwise clarify this lesson without inventing tomorrow's plan.
4. **Ways of thinking about knowledge.** One or two lesson-specific epistemic
   questions when supported: what counts as evidence, observation versus
   inference, alternative explanations, assumptions, a model's limits,
   uncertainty, what would change a conclusion, or reframing the question.
   Give an ordinary-language question and a short teacher explanation. These
   are invitations to broaden how students approach the work, not evidence
   that epistemic agency or learning growth has occurred. Do not force a
   generic question when no supported connection is available.

With a small sample or incomplete review, present only the supported discussion
and label its coverage explicitly. Never turn an empty, failed or partial source
read into a conclusion about the class. Missing evidence remains unavailable;
there is no invented student quote, misconception, chronology or growth claim.

Use the existing exact class/section lesson-summary identity and verified private
teacher-report destination. Preserve previous versions and update the existing
report index/link; do not create duplicate documents, checklists or folder trees.
Filenames start with the verified cycle number, or `00` for general reports.
Keep teacher summaries out of student folders and individual/all-class student
print/email packets. Preserve teaching-plan, Printed and Handed-out marks.
Read back the saved document content, private destination and working report link
before marking the teacher summary complete. Existing model/budget, provenance,
privacy and workflow ownership limits remain in force; this adds no new sender,
grade writer, paid budget, physical print job or recurring schedule.

Validation: fourteen synthetic summary tests, plus a local composition check
from the student-feedback fixture into this reducer. No live school processing.
