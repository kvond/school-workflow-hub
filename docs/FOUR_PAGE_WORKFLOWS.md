# Four-page Agenda library contract

Reconciled September 16, 2026. `workflows.teacher_workspace` is a pure-Python
read model and proposal library in the existing backend. It is not the deployed
teaching-results provider, a second database, or a new execution service.

The existing hosted Agenda reader, sign-in and private state have September 15
acceptance evidence in [CLOUD_READINESS.md](CLOUD_READINESS.md) and
[PRIVATE_CLOUD_STATE.md](PRIVATE_CLOUD_STATE.md). Those historical receipts do
not verify this wrapper or a current live student-processing run. This
reconciliation uses synthetic tests and makes no school or cloud changes.

## Reuse the current application boundaries

The operational date comparison is Agenda versus Infinite Campus. The tracked
`adapters.dated_agenda.read_day` already reads the current dated Agenda layout.
Keep its mapping and the deployed `/api/agenda-day` route authoritative for that
view; a row-2 discovery result must not replace an inspected daily mapping.

`adapters.dashboard_navigation.read_dashboard_navigation` is an optional,
bounded discovery helper for teacher-identified Dashboard row 2. It checks
metadata, reads one rectangle (A2:Z2 by default), extracts literal/rich links and
same-spreadsheet gid fragments, and flags ambiguous or unsafe destinations.
Missing grid evidence is blocked; a correctly scoped returned empty row is an
observed empty segment. Labels such as Mon14 never create dates, lesson IDs or
proof of destination access. The helper does not follow links and has no live
acceptance receipt in this reconciliation.

The current submission-to-result composition is `due_completion` ->
`student_feedback` / `missing_work_feedback` / `class_summary` ->
`teaching_results`. The hosted teaching-results adapter validates the private
provider response; that provider remains unconnected according to the latest
recorded cloud acceptance. Integrate through that existing boundary rather than
creating another endpoint or promoting this wrapper to live by relabeling it.

## Versioned read model

`build_teacher_workspace` returns **agenda-workspace/v1**, embedding the
unchanged **agenda-operations/v1** contract under `operations`.

| Page | Library output | Remaining integration |
| --- | --- | --- |
| teaching_agenda | All supplied `DailyLesson` records, source revision/time, exact observed Slides URL and IC comparison target | Bind the existing dated reader to stable identities; source refresh; conflict-safe saving and destination readback |
| submissions_feedback | Current roster × assignments, cumulative submission evidence, deadline/feedback/delivery states | Current complete private sources and reviewed results; authorized resource resolution |
| teacher_summary | Learner-assignment case counts, delivery counts and an optional instructional-summary reference | Bind the existing source-backed class summary and lesson-completion receipts |
| research_data | Separately authorized approved references and research agenda contract | Separate access, teacher review and versioned evidence; collection remains disabled |

All action flags remain false: save_agenda, send_parent_email, file_feedback,
record_roster_grade, post_ic_grade, print_feedback and collect_research. A
prepared proposal is not an executed action or an active provider.

These are host-injected library functions. The host authenticates the teacher,
class/term scope, source provenance, freshness, receipts and resource mappings.
Browser-supplied `research_access` or `live_end_to_end` labels are not authority.
Resolve private evidence through the existing store. Shape checks cannot prove
that a receipt is genuine. The wrapper rejects incomplete coverage claiming
missing work, row timestamps relabeled by a different feed check time, duplicate
cases, future snapshots and unsupported write counts.

Every payload is private teacher data, including opaque learner keys. Serve it
over authenticated HTTPS with private cache controls; never copy it into public
HTML, repository fixtures or logs. Notes are text. Slides URLs preserve their
observed file ID/link mode and remain `link_access=not_tested`.

## Submission, deadline and feedback evidence

`build_submission_feed` reuses `EnrollmentRecord` and `SubmissionRevision`.
Pass current active membership and preserve withdrawn learners and prior grades
in the historical ledger. `SubmissionFact.submitted_at` is the actual turn-in
event, not Drive modification or observation time.

A paginated 14-day change crawl is not full history. Reconcile changes with the
cumulative ledger before setting `full_history_complete=True`. Missing work
requires both complete roster and history. Partial evidence stays unverified.
Two distinct files for the same learner/assignment require review; the newest
observation cannot choose which one to score. An earlier known on-time turn-in
remains on-time after revisions, while incomplete history cannot prove lateness.

`deadline_after_due_date` delegates to the same `due_completion.boundaries`
calendar arithmetic used by the due-assignment pipeline. Date-only deadlines
include the whole fourteenth local calendar day after the effective due date,
including DST changes. The default zone is America/New_York. `Deadline` can
also hold an authoritative aware cutoff and policy reference.

An optional `Deadline.learner_key` binds an approved individual extension;
it overrides the class cutoff only for that learner. Each row exposes its
effective cutoff and policy reference. Use all current approved extensions.
Classifying overdue status uses the source check time, not a later page refresh.
The host must revalidate freshness before any proposed delivery or grade change.

Verified non-submitters have `feedback_state=missing_work_feedback_needed`,
following feedback-v2.1. Use the existing `missing_work_feedback` builder
for the teacher's note and commitment form. This wrapper does not
invent quotes, conceptual levels or submission revisions for missing work.
This state does not change grades, bypass the grace period or trigger delivery.

Overdue non-submitters route to parent-draft review only with a scoped IC contact
reference. No email is fetched or sent. A future executor must bind a notice to
fresh verified absence and the effective due policy, validate guardian details,
and preserve receipts against duplicate delivery. The existing `Outcome` shape
binds to a submitted revision, so it cannot claim a current missing-work notice.

Submitted feedback is available only when class/assignment/learner, current
submission revision, prompt version and expected folder match. Document content
revision remains separate. A verified delivery needs a scoped live readback;
an older submission outcome stays `prior_revision`.

## Current prompt and completion contracts

The editable Agenda Feedback Prompts tab is authoritative. The reviewed release
reference is `web/teacher-prompts.json` (September 16 feedback-v2.1); see
[AGENDA_PROMPTS.md](AGENDA_PROMPTS.md). The September 14 prompt text is historical.
Do not copy its superseded report order into current outputs.

`completion_policy.assess_completion` remains the shared arithmetic over
assessed writing. No grading semantics changed in this reconciliation. Verified
inventory IDs and declared lesson sequence are required; blank, copied,
meaningful and unreadable responses are supplied by the authorized assessor.
Markers or nonempty strings alone do not assess student writing.

- Final Content/notes includes required NOTES and BANK once each; Revision
  includes required DRAFT only. NOTES-only and conflict items add no DRAFT credit.
- Initial Content/notes includes NOTES only; BANK and DRAFT are not yet due.
- Zero denominators are N/A; optional work stays outside both scores.
- Missing inventory, ambiguous mappings or unreadable required work hold scores
  for review. Later completion earns the same credit without inferred growth.

`plan_completion_delivery` is an optional roster-before-IC proposal helper,
not a new requirement to read GB attendance pages before the current due
pipeline. Its identity binds scores, class/assignment/learner, submission,
stage/policy, current-roster reference and IC mapping reference. Only a live
roster readback for that exact proposal allows an IC proposal. Changed scores,
roster revision or IC mapping invalidate the prior readback. Both executors
remain unsupported, and earlier grade history is preserved.

Parts 1/2 batch at twenty; Part 3 uses the full class or explicitly partial
compiled evidence. The implemented feedback-v2.1 and summary builders follow
[FEEDBACK_V2.md](FEEDBACK_V2.md): separate evidence-backed understanding and
explanation graphs; current understanding; source-backed explanation changes;
one question; exact evidence; three blank reflection lines. Missing evidence is
gray/unavailable. Teacher discussion ideas remain outside the student report.
The wrapper's operational counts do not generate those judgments or report
content. Actual source assessment, filing, provider binding and live acceptance
remain separate work.

## Printing and research boundaries

`plan_feedback_print` requires a teacher-request reference, printer mapping,
exact expected learners and submission revisions, matching folder/prompt and
revisioned feedback documents. Missing/duplicate/stale documents block the
proposal. Batch identity ignores reread timestamps and receipts but changes
with content revision, selected documents, destination or teacher request.

The future executor revalidates document revisions and printer, uses the shared
private claim ledger, and reconciles uncertain spooler outcomes before retrying.
A proposal or saved report is not evidence of printing; no printer is selected.

Research consumes separately approved records. See [RESEARCH_AGENDA.md](RESEARCH_AGENDA.md)
and [RESEARCH_CODEBOOK_DRAFT.md](RESEARCH_CODEBOOK_DRAFT.md) for conceptual
understanding and separate engaged, epistemic and authorial agency contracts.
Do not infer authorship, timing or conceptual growth from later-filled boxes.
Research collection remains deferred and does not change completion grades.

## Verification and handoff

Focused reconciliation check:

```
PYTHONPATH=src python3 -m unittest tests.test_teacher_workspace tests.test_dashboard_navigation tests.test_completion_policy tests.test_due_completion -v
```

Result: **65 tests passed**, including individual extensions, ambiguous files/turn-in times,
partial evidence, stale row timestamps, mapped delivery receipts and empty-grid
coverage. Full-repository acceptance is recorded in the integration handoff.
Tests use fictional data, with no school reads, private records, external writes
or paid model calls. A local test is not a hosted live acceptance run.
