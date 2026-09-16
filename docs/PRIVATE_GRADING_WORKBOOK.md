# Private grading records on the existing dashboard

Page 2 reads the private grading workbook across all assignments. Course and
section selectors filter that list; the lesson date controls only Agenda links
and the optional lesson-folder inventory. Each submission expands to the actual
written answers, separate conceptual/explanation descriptions, completion status,
feedback, and recorded IC posting state. Page 3 has exactly one concise teaching
summary per class/section. It does not average the descriptive levels or scores.

`GET /api/grading-records` authenticates the teacher's signed IAP assertion before
obtaining the existing attached service-account identity. The service reads the
configured Google Sheet server-side with the Sheets read-only scope. The browser
cannot choose an arbitrary spreadsheet, and receives `Cache-Control: no-store`.
No source data, spreadsheet ID, credentials or fixture fallback is packaged.
The endpoint performs no grading, grade posting, email, or school-source writes.

The existing `hub-teaching-results/v1` composition and endpoint remain available
for a dated lesson provider. They are not the all-assignment source used by these
two pages. The private workbook is an explicit teacher-requested record source,
not an additional task store. Shared run coordination remains in Firestore.

## Workbook contract

Four exact tab titles, row 1 headers, one record per later row. Blank rows are
allowed: the reader scans the whole bounded grid rather than stopping at a gap.
Extra columns are preserved, including exact fractions, evidence and provenance.
Do not put titles above the header row or merge cells in these record tables.

| Tab | Required columns |
| --- | --- |
| Assignments | assignment_key, class_key, assignment_title |
| Submissions | record_id, assignment_key, section_id, section_name, student_id, display_name, source_url, source_revision, status, content_score, revision_score, hold_reason, feedback, checked_at, ic_posting_status |
| Answers | record_id, question_id, stage, response, conceptual_level, explanation_level |
| Class summaries | class_key, section_id, section_name, summary, coverage, checked_at |

`class_key` is `biology`, `forensics`, or `anatomy`. Section IDs and student IDs
are explicit source identities. They are never inferred from a display name.
Every submission has a unique `record_id` and references an assignment; every
answer references a submission. Multiple answer observations are preserved.
Assignments have unique `assignment_key` values. Summaries are unique by
`(class_key, section_id)` and contain one reviewed paragraph up to 1,600 characters.
The `coverage` cell describes the actual evidence coverage and any limitations.

`checked_at` is an ISO8601 timestamp including timezone. Scores are numeric
0–10 or blank; a blank remains unscored. Numeric formula outputs use the stored
`effectiveValue.numberValue`, not a rounded formatted display. Exact fraction
columns `content_score_exact` and `revision_score_exact` are shown inside the
expandable work details. The concise table displays scores rounded to two
decimals; the API preserves the unrounded value and exact fraction. This display
choice does not change the IC posting calculation or rounding policy.
Nonempty `hold_reason`, status
`identity_unverified`, or status `superseded` suppress numeric score display.
Those two statuses and any row without a section or resolved identity are excluded
from class counts but remain visible for review. A supplied `learner_key` from a unique
private roster match also supports counts when `status` is `assessed` or
`roster_matched`; absence of an IC student ID alone is a posting gate, not absence
of submitted work. This never fills or invents an IC ID. Counts use distinct
resolved identities within the class/section; multiple artifacts do not create extra students.
The summary text is supplied by the reviewed record, never invented from counts.

Optional answer fields shown in the UI are `prompt`, `meaningful_complete`,
`evidence_note`, and `source_url` (or `Source URL`). Student responses are rendered
as literal text, never HTML. IC status is a recorded workbook value; displaying
it does not perform or independently verify a grade post.

The reader rejects duplicate identities, missing references, invalid scores,
missing headers, grids over 10,000 rows or 40 columns, more than 128 bounded range
reads, or more than 4 MB of cell text. Resize each tab to a sensible used grid to
keep reads quick. It checks tab properties again after reading and rejects
structural changes. A read has an observed-content hash; this is not a native
Google document revision or a transaction covering concurrent cell edits.
Publish a reviewed workbook batch before refreshing the dashboard.

## Deployment handoff

The repository is deployed through its existing Cloud Build integration on
matching pushes to `main`, using `/Dockerfile`. No separate site or new service
is needed. The existing WSGI entry point automatically exposes the new route.

In the existing Cloud Run service, preserve its identity, IAP, allowed emails,
Agenda variables and Firestore variables. Add only
`HUB_GRADING_SPREADSHEET_ID` with the approved private workbook ID. Grant the
existing `HUB_READER_SERVICE_ACCOUNT` Viewer access to that workbook, subject to
the school account's sharing controls; do not enable public-link sharing.
No endpoint presently changes runtime configuration. Cloud Console or an
authorized `gcloud run services update ... --update-env-vars ...` is the existing
configuration path. Keep actual IDs, account addresses and commands containing
them in the private task handoff, outside the repository.

After deployment, verify the authenticated API returns `hub-grading-records/v1`,
record the exact Cloud Run revision and content hash, and compare Page 2 section
rows plus Page 3 class cards with the private workbook. Reload and switch classes
to verify the data remains correctly scoped. An unavailable source clears the
view and is never interpreted as missing submissions. A code test is not a live
workbook, hosted read, or IC posting verification.

## Local checks

```sh
PYTHONPATH=src python3 -m unittest tests.test_grading_records tests.test_google_sheets_reader tests.test_teaching_results tests.test_web_hosting
node --test web/tests/grading-records.test.mjs
node --check web/pages.mjs
```

All test data is fictional. Tests cover auth before private reads, range gaps,
reference conflicts, partial identity, held grades, exact section filtering,
single summary cards, plain-text student responses and stale-request rejection.
