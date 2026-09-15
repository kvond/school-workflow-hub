# Frontend requirements alignment

Reviewed against the HTML owner's **Agenda frontend handoff, version 0.1,
September 14, 2026**. This is a requirements crosswalk, not a new API or a claim
of a connected service. The implemented Python contract remains
`agenda-operations/v1`, documented in `HTML_AGENDA_INTEGRATION.md`.

## Shared ownership and deployment

The HTML chat owns the existing Agenda's presentation and interactions. The
backend owns workflow logic, normalized models and persistence operations. The
cloud chat owns hosting, authentication, credentials, private storage setup and
activation. Reuse the existing ledgers; do not create competing state stores.

The final interface and service must use **authenticated HTTPS**. Authorized
school AI sessions must be able to retrieve versioned instructions and canonical
records without a local path or prior chat history. A private GitHub code link
provides source and documentation to authorized collaborators; it is not the
deployed Agenda service, an execution API or private student-record storage.
No service base URL, authentication flow or live endpoint is verified here.

## Task/status projection

| Frontend requirement | Implemented v1 mapping | Gap / interpretation |
| --- | --- | --- |
| Stable task identity | `operations[].id` is a deterministic operation identity; `next_action.todoist_task_id` may reference an existing task | These IDs are different. Null task mapping is allowed; do not create a task automatically. |
| Process | `kind` | Four kinds: assignment links, date alignment, grading review, roster changes. |
| Class/week scope | `scope.class_key`, `scope.assignment_key`, `scope.week_key` | Keys come from existing stable mappings; week can be null. |
| Queued | No queue-acceptance state in v1 | `not_checked` means unchecked, not proof that a run is queued. |
| Working | `status=in_progress` | Live execution requires a genuine active run record; local loading is not a workflow run. |
| Needs you | `needs_review`, `blocked`, `failed`, and applicable `next_action` | Retain the specific reason and teacher action. |
| Verified | `passed` plus verification level and supporting evidence | Synthetic results display Test passed. A live read proves only its stated read/check. Destination changes require matching readback. |
| Last check | `last_checked_at` | Null is Not checked; `generated_at` is only the snapshot assembly time. |
| Evidence | `evidence[].reference`, `kind`, `observed_at`, `summary` | The host resolves authorized private receipts. The UI must not construct source URLs or infer missing evidence. |
| Next action | `next_action` | An existing Todoist task may be opened; this projection does not execute it. |
| Instruction version | Not implemented | A future versioned instruction service must bind workflow, scope, authorized steps and record destination. |
| Record revision | Not implemented | Operation ID is identity, not a revision. Existing observation checkpoints are not UI-edit revision tokens. |

The v1 parser rejects extra operation fields. Do not silently insert
`instruction_version` or `record_revision` into it. Their implementation needs
a versioned extension and tests; the HTML can use clearly labelled fixtures
while those services are absent. The same applies to mismatch detail, last
verified week, last verified grade entry and roster changes: summarize only
actual supplied receipts for the selected scope, with their check time.

Todoist **AIHS remains the work queue**. Canonical execution outcomes and
evidence belong to the shared backend record. Todoist links/comments may refer
to that record; its display is not an independently editable execution status.
Preserve existing tasks and stable mappings to prevent duplication.

## Lesson read/save behavior still to implement

The current Agenda normalizer accepts an explicitly configured tabular layout.
It has not been mapped to the live Agenda's weekly and list sheets. There is
no lesson read/save HTTP contract or verified sheet writer in this handoff.

Required before enabling Save:

1. Inspect the current weekly/list layout and record exact stable file, sheet
   and lesson mappings. Preserve resource IDs, copy/edit intent and grade history.
2. Return a lesson's current editable fields with an opaque source revision and
   its verified destination mapping. Do not use title, due date or row position
   as its identity. Inspect fresh locations when rows move.
3. Require the expected revision and a stable request idempotency key on save.
   Authenticate and authorize the exact lesson/fields server-side. A stale
   revision must return a conflict and current revision without overwriting it.
4. Preserve the draft across failure and conflict. Let the teacher review the
   current version and their draft; do not overwrite either automatically.
5. Record a durable outcome and reread every intended weekly/list destination.
   Show Saved only for the exact submitted edit and matching readback. If
   only part succeeds, show the unresolved destinations and recovery action;
   never report the entire edit saved or blindly retry all destinations.

The UI may build these interaction states against synthetic fixtures now.
Actual Save stays disabled or clearly local-draft-only until its verified
service exists. The current payload always reports `edit_saving_supported=false`.

## Portable instructions and capabilities still to implement

Required service responses must identify the authenticated principal's allowed
workflow/scope, instruction version, immutable instruction content reference,
canonical record destination and runtime verification evidence. Copy instructions
should include only the task/workflow identity, scope, version, authorized steps
and authenticated record destination. Exclude credentials and student records.

The repository capability inventory describes code and remaining work; it is
not an authenticated authorization response. Run must remain disabled until the
server confirms permission and verified execution for that workflow and scope.
The server must enforce the same rules even when a client bypasses the button.
Unknown capabilities or a failed check must never enable Run or Save.

## Shared execution and duplicate-run protection still to implement

Existing deterministic run/action IDs, proposal idempotency and atomic Agenda
observation checkpoints are useful foundations. They do **not** establish a
leased, concurrent manual/automatic execution service. In particular,
`core.runs.create_or_get_run` is a synthetic run ledger helper, not a distributed
run-claim API; it has no lease-expiration or worker-recovery protocol.

The backend and cloud owner must connect one persistent claim mechanism used by
both manual and scheduled execution. Required behavior:

- Atomically claim the same canonical task/workflow, scope and input/instruction
  revisions; return the existing run for duplicate requests using its durable
  idempotency key. Do not rely on a browser lock.
- Record owner, claim generation, lease deadline and run state in approved
  persistent state. Lease renewal/recovery must be conditional on current
  ownership; a stale worker must be fenced from committing further actions.
- On lease expiry or an uncertain write result, reconcile recorded intent and
  destination readback before retrying. Expiry alone is not permission to repeat
  a possibly completed change. Hold ambiguous outcomes for review.
- Report start/status/recovery against that same record, with authenticated
  evidence access. An expired lease must not appear as an active healthy run.

These are implementation requirements, not available endpoints. Deployment and
activation remain with the cloud owner; no schedules or credentials were changed
to satisfy this handoff.

## Acceptance order

1. Review the existing all-classes layout with explicitly synthetic data: one
   date/week selector, spacious notes with their resource links, operations and
   Needs you together.
2. Verify authenticated HTTPS access and authorized instruction/record retrieval.
3. Audit one current week: Agenda links against parent assignment pages, then
   Schoology. Save scope/revision-specific evidence in the shared record.
4. Separately verify one permitted lesson edit and its weekly/list readbacks;
   test failed-save recovery, stale revisions and partial destination outcomes.
5. Verify concurrent-run rejection, duplicate retries and lease recovery before
   enabling Run. Automatic synchronization requires its own live end-to-end
   proof; a fixture pass or successful source read is insufficient.

Current backend evidence: 322 local tests passed at commit `0b76222`.
This document adds requirements alignment only; it does not add a frontend,
endpoint, save service or execution capability.
