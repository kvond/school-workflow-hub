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
unique `student_key`, `name`, and `section`. It updates only Student evidence and
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
