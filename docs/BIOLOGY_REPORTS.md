# Biology teacher reports

The DTGSS workbook contains class aggregates only. The separate Biology analysis
contains exact student excerpts, original source dates and per-dimension teacher
interpretations. Neither changes completion scores or Infinite Campus grades.

`HUB_GROWTH_REPORT_URL` points to the existing DTGSS Guide tab.
`HUB_BIOLOGY_ANALYSIS_URL` points to the separate private native spreadsheet.
The runtime verifies named-user privacy and existing edit access to both files.

`teaching_report.save_observations(config, observations, baseline=True)` seeds the
immutable initial source analysis once. Future batches use four validated
`teacher_dimensions` from one **saved** native feedback identity. Old reports
without these dimensions remain unassessed; they are never regenerated to fill
the columns. The original provider policy/fingerprint remains readable, preventing
a new bill for unchanged old feedback.

`native_observations(saved, student_key, source_date, lesson_key=reference_id,
date_basis='source_modified', submitted_at=original_form_timestamp)` normalizes
those future observations. The host must independently verify the archived
student key, exact registered lesson reference and original Form context. A
source-modified date identifies a work revision, not a new submission or growth.
The saved assessment date is displayed separately. A higher verified revision
of the same original file may supersede its older observation; conflicting files
on the same date remain held.

Saving new native Biology feedback with these dimensions also persists its
four observations and refreshes the connected teacher tables, after checking
the fresh roster against verified archive folder keys. A separate report hold
is returned without undoing the saved student feedback or retrying a provider.

`refresh_writing_tables(read_token, write_token, config, active_students)` reads
the immutable private batches and receives a fresh, complete current roster with
unique `student_key`, `name`, and `section`. It updates Student evidence and
Whole class in the analysis, plus Report and Lesson measures in DTGSS. Teaching
plan checkboxes and teacher notes are preserved. No student files, feedback PDFs,
delivery states, original submissions, paid providers or grades are touched.

Each current student counts once per lesson and dimension. Evidence unavailable
and Not assessed remain distinct from Beginning. Saved critical-aspect totals
retain their dated provenance; if their roster projection no longer matches the
current class denominator, refresh holds for a grounded roster projection rather
than presenting stale totals as current.

A lost Sheet response triggers one exact readback, never a blind POST retry.
Unconfirmed tables leave saved feedback and original work available. Private CAS
claims serialize source-batch saves and refreshes, and server ownership is renewed
immediately before mutations.

The same refresh returns a separate `growth` result for the existing Student
growth and Student figure data tabs, and DTGSS Across lessons, Growth figure data
and writing evidence limits. Original source IDs, dates, levels, rubric versions
and answer fields bind the old table and chart blocks before updating them.
Existing charts retain their styles and positions; new numeric dimensions use
the reviewed four-dimension palette. Levels retain the fixed 1–4 axis and blanks
break lines. Ordinal categories are never averaged. New rubric/field chart groups,
ambiguous ownership, teacher edits and changed source bookends hold growth without
undoing saved feedback. Dated conceptual/critical tables and manual notes remain
unchanged. These synthetic tests are implementation evidence, not live acceptance.
Before the first growth POST, the existing private CAS store reserves hashes of
both expected table/chart sets and protected workbook content. A crash or
unconfirmed write keeps automatic growth updates held across later runs. Recovery
fresh-reads both books before ordinary teacher-table refresh and clears the hold
only when every expected value, chart and protected content matches. Partial
two-workbook updates never automatically replay the remaining write. Until that
pending update is reconciled, both ordinary teacher-table and growth mutations
remain held so the protected snapshot can still be checked. Student feedback is
already saved and remains printable.

## Student science-writing PDF

`feedback_pdf.render_science_writing` renders two pages without model calls or
storage writes. Page one preserves the four source examples and explanations,
colors the observed understanding labels, and plots each writing area separately.
Charts use only the payload's source-bound observations. Unavailable or unassessed
samples are gaps, a single rated sample is labeled "No trend", and different
lessons are not presented as a measured growth score. Original source dates remain
separate from the preparation date.

Optional `presentation_context.original_submission_times` contains only exact
source ID, revision and original submission timestamp triples. The filing caller
must verify these against the original archive source records. The renderer also
checks their source identities and Eastern dates. Without original timestamps,
same-day lessons remain unconnected instead of implying a guessed order.

Page two keeps each student's original `target.title` and `target.text`, adds an
invitation to explain the challenge, and provides writing lines. This does not
replace a distinct student's challenge with a shared class prompt. Presentation
version 2 requires a new immutable report ID; verified earlier PDFs and delivery
history remain preserved. The daily packet must select the verified successor
rather than printing both presentation versions.
