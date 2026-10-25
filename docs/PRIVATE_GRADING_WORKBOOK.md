# Private grading records on the existing dashboard

Page 2 reads the private grading workbook across all assignments. Course and
section selectors filter that list. It opens on Matched sections; Unmatched (N)
keeps files without a verified section available separately, and All records
retains the complete view. These display filters do not change records or grades.
The lesson date controls only Agenda links
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

## Recovery inside the dashboard

The existing assessment review offers **Load saved assessment for review** without
a provider key or model call. Authenticated GET `/api/assessment-save?record_id=...`
checks the original active record, exact source revision/hash and literal saved
evidence. It uses a retained reviewed packet, or an older cached packet only when
its complete persisted projection matches the saved assessment. Lossy workbook
completion flags never recreate judgments. When no full packet survives, the
same review can accept prepared packet JSON and display its proposed judgments
and feedback changes before the ordinary Save.

Save carries the original-record fingerprint and source binding. Concurrent
corrections, changed source/access metadata, protected identity/requirements or
evidence changes, and pending saves (including expired attempts) hold the new
review. It uses the existing assessment-save task, workbook append/supersession
and exact readback, retaining the full reviewed packet in immutable private
result history. An uncertain reply permits exact saved readback, not another
native batch or automatic claim takeover. Closing a view does not save it;
an interrupted Save remains held for recovery. This path changes neither
completion arithmetic nor the IC Helper and does not post any grade to IC.

**Review fresh current-source assessment** accepts a complete packet that the
teacher has already reviewed, including every explicit item judgment, literal
current-source quote, level and feedback field. It makes no model call and needs
neither a cached draft nor the lost historical packet. Select the existing
student/lesson/checkpoint, open this action and import the current packet JSON
(bare or wrapped as `{packet: ...}`). Review the full packet and its completion
and incomplete counts, then explicitly confirm teacher approval before Save.
The import does not fill fields or infer judgments from historical flags.

Authenticated GET `/api/assessment-save?record_id=...&mode=fresh_current_review`
returns the old record's fingerprint and a separate fresh source binding, with
no write or claim. Save carries both in `review_basis`, plus the exact imported
packet hash. The existing save handler validates the current source, distributed
manifest, exact student/lesson/section/checkpoint and IC mapping, then retains
the normal claim/fence, final revalidation, supersession and readback contract.
Final source captures bookend the workbook read; readback verifies the original
history fingerprint as well as the newly saved evidence and feedback. An
uncertain Save permits readback-only recovery and preserves its pending attempt.
An already-saved response also requires the exact whole-packet input hash in
the completed or matching pending save; matching worksheet rows alone do not
prove that the complete imported packet was saved.
This creates a new current-source reviewed record; it does not claim to have
reconstructed missing historical judgments or revise the grading policy.

### Known pre-write Save failure

Save checks every actual Submissions/Answers field against the native headers
before creating a claim. It checks again immediately before dispatch. Modern
packets carrying `critical_aspect` and `rubric_version` require both real
columns; older packets remain compatible with the original 18-column layout.
This check does not repair or rewrite a workbook.

A historical attempt may already have reserved its run before encountering
`assessment_column_missing`. The authenticated, same-origin POST
`/api/assessment-recovery` closes only an explicitly attested pre-write failure.
Send the exact original `{request_id, packet, review_basis}` plus
`failure_receipt` with these fields:

```json
{
  "schema": "hub-assessment-prewrite-failure/v1",
  "run_id": "<exact original run ID>",
  "fence": 2,
  "http_status": 400,
  "error_code": "assessment_column_missing",
  "evidence_ref": "<private reference to the observed original reply>",
  "prewrite_failure_attested": true
}
```

The fence above is illustrative: use the actual run's fence. Only the exact
coded 400 legacy or 409 schema-hold reply qualifies. The receipt attributes
this as operator-attested historical evidence, not server-observed proof.
The current native headers must pass a complete dry build. The deterministic
new record must be absent from both Submissions and Answers on repeated reads;
the original record, source/access metadata, task/run versions, owner, fence,
full packet hash and expired server-time lease must remain exact. Completed,
partial, changed or unknown writes stay held.

Reconciliation makes no grade write or Save call. Its atomic private update
retains all original run fields and the null original receipt, adds a
reconciliation pointer, preserves the fence/owner/expiry/last result, and clears
only the verified failed active-run pointer. A lost reply is read back without
another mutation. Replaying the same closure after a later Save returns only
the immutable historical receipt and cannot clear the newer run.

After verified reconciliation, reopen the normal fresh current-source review,
import the already approved packet and use one new Save request. Do not reuse
the failed request or delete state manually. These code and synthetic checks
are not evidence that a live attempt was reconciled.

Page 2 and Page 3 share an accessible recovery panel for failed grading reads.
The loader reads the endpoint's fixed diagnostic code instead of replacing every
failure with a generic workbook-unavailable message. It distinguishes teacher
sign-in, server reader authentication, missing connection, denied access, missing
source, workbook layout, record conflicts, concurrent structural edits, bounded
read limits and temporary service failures. Unknown failures remain unknown.
Each known failure has a concise next action and a safe diagnostic code in the
existing dashboard; arbitrary provider bodies and exception text are not shown.

Refresh remains a same-origin, no-store GET, not grading or posting. A failed
refresh removes prior records from the view, never invents missing-work counts
or zero scores, and cannot be overwritten by an older request completing late.
The recovery action is guidance, not a claim that an integration was repaired or
an automatic repair job was launched. No extra task store, service or polling
schedule is introduced.

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
node --test web/tests/grading-records.test.mjs web/tests/grading-recovery.test.mjs
node --check web/pages.mjs
```

All test data is fictional. Tests cover auth before private reads, range gaps,
reference conflicts, partial identity, held grades, exact section filtering,
single summary cards, plain-text student responses and stale-request rejection.
