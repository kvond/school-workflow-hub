# Existing HTML Agenda: Python integration contract

Contract: **agenda-operations/v1**. This is the backend handoff for the chat
building the existing HTML Agenda. The repository contains no connected Agenda
HTML application, Agenda preview URL, HTTP status endpoint or edit-saving API.
The report-preview HTML elsewhere in this repo is a fictional printable report.

The HTML owner's frontend handoff was read on September 14 and reconciled in
`docs/FRONTEND_REQUIREMENTS_ALIGNMENT.md`. That document maps its requested
fields and controls to the implemented contract and identifies missing services.
Do not add those requested fields to this strict v1 payload until a versioned
extension is implemented. Local folders are development locations only. The
finished interface, instructions and shared records require authenticated HTTPS;
no hosted service URL has been verified in this backend checkout.

## Ownership and current connection state

- HTML owner: integrate into the existing Agenda page. Keep all classes editable
  together, lesson notes and links together, one date/week selector, and a
  compact operations area with evidence and “Needs you” actions.
- Python owner: shared models, comparison/planning, source readers, baseline
  checkpointing, note routing and the status read model described here.
- Cloud owner: runtime credentials, hosting, transport, persistent private state
  configuration and activation. Reuse the existing ledger and services.
- Live-audit owner: supplies actual source observations and verified receipts.
  First compare current Agenda links with parent assignment pages, then Schoology.

This handoff has local synthetic-test evidence only. Source credentials, actual
Agenda layout mapping, live comparisons, UI transport and saving still require
connection and verification. Do not display automatic synchronization as active
from a test pass, a successful GET, or code being present.

## Implemented entry point

```python
from school_workflow_hub.workflows.operations_status import build_operations_snapshot

payload = build_operations_snapshot(
    records,                     # list of operation records below
    generated_at=aware_iso_time,
    connection_state="preview_only",
    sync_check=None,              # defaults to not checked / inactive
)
```

Run with the package installed or `PYTHONPATH=src`. A synthetic JSON example is
provided at `examples/agenda_operations.preview.json`. It can seed an explicitly
labelled UI preview. Its dates and identities are fictional.

The host calls this function after adapting its genuine workflow receipts. It
may serialize the result over its chosen authenticated transport. No HTTP path
or server has been implemented or reserved by this contract. Shape validation
cannot authenticate evidence: the host must validate receipt provenance, scope,
freshness and permissions before labelling a result live.

### Top-level response

| Field | Meaning |
| --- | --- |
| `schema_version` | Exactly `agenda-operations/v1` |
| `generated_at` | Aware ISO timestamp when this snapshot was assembled; not a source check time |
| `connection_state` | `not_connected`, `preview_only`, `connected_readonly`, or `connected_live` |
| `edit_saving_supported` | Always `false` in this implementation |
| `work_queue` | `{system: "todoist", project: "AIHS", create_tasks: false}` |
| `automatic_sync` | Validated check fields below plus `active`; defaults to false |
| `operations` | Stable-ID records, sorted by ID; an empty list means no supplied results |

### Each operation

| Field | Accepted value / use |
| --- | --- |
| `id` | Generated stable identity from operation kind and scope; caller does not set it |
| `kind` | `assignment_links`, `date_alignment`, `grading_review`, `roster_changes` |
| `scope` | Exactly `{class_key, assignment_key, week_key}`; class required, assignment required except roster, week optional |
| `status` | `not_checked`, `in_progress`, `passed`, `needs_review`, `blocked`, `failed`, `not_applicable` |
| `verification` | `unverified`, `synthetic_test`, `live_read`, `live_end_to_end` |
| `last_checked_at` | Aware ISO source/test check time or `null`; never substitute page refresh time |
| `evidence` | List of `{kind, reference, observed_at, summary}` |
| `next_action` | `null` or `{label, kind, todoist_task_id}`; action kind is `review`, `recheck`, `connect`, `open_task` |

Keys and evidence references are 1–160 ASCII letters/digits/colon/underscore/
hyphen. Use the existing private stable-identity mapping; these are not raw
URLs. If an existing native ID needs translation, store a stable mapping; never
derive an assignment identity from its title, cell location or due date.

Evidence kinds are `test_receipt`, `source_snapshot`, `readback`, `error_receipt`.
`reference` names a receipt in the host's approved private store. The host must
provide authorized evidence navigation; the frontend must not guess a URL from
the reference. Keep `summary` short and free of student details or secrets.
All text is untrusted display content; use text rendering, not raw HTML.

`passed` requires a check time, evidence and the matching verification receipt:
test receipt for `synthetic_test`, source snapshot for `live_read`, readback for
`live_end_to_end`. `not_checked` requires unverified, null time and no evidence.
Needs-review, blocked and failed results require a next action. Evidence cannot
postdate its check, and checks cannot postdate the snapshot. Duplicate
kind/scope records are rejected. Invalid input raises `ValueError`; present the
connection error and retain the last valid view, clearly showing its age.

`automatic_sync` uses the five check fields: status, verification, check time,
evidence and next action. Active requires an explicit successful end-to-end
check with a readback receipt and `connection_state="connected_live"`. The host
must clear a stale receipt when the runtime changes or stops. A displayed
status is evidence about an authorized run; it does not authorize a write.

## Display behavior and queue mapping

1. Join results beside their existing class/assignment using the shared stable
   keys. Filter with the Agenda's single date/week selector; never recreate the
   lesson list inside a separate dashboard.
2. Show status, verification level, last checked time, evidence and next action
   together. Display absent times as “Not checked.” Show test passes as “Test
   passed,” with “Preview data” visible. Show live-read verification separately
   from synchronization verification.
3. Derive “Needs you” from `needs_review`, `blocked`, `failed` and any explicit
   next action requiring the teacher. Missing results mean “Not checked.”
4. `todoist_task_id` refers only to an existing AIHS task. Resolve and open that
   task through the authenticated queue integration. A null ID means unmapped;
   never create a task to fill the gap. Existing task IDs live in private runtime
   configuration, not this code repository.
5. Keep edited class text/notes/links labelled “Unsynced edits” while saving is
   unsupported or pending. A local draft is not saved to Agenda. Do not show
   “Saved” until a separately implemented save service returns a successful
   durable receipt and reread for the same object revision. No save payload,
   conflict-resolution API or frontend draft store is implemented here.

## Reuse the existing backend

| Need | Existing Python module / contract |
| --- | --- |
| Bounded Agenda read, map, checkpoint | `workflows.agenda_observer.observe_agenda`; requires the actual inspected layout |
| Parent assignment/link comparison | `adapters.private_input` and `workflows.curriculum_pipeline`; actual source snapshots still required |
| Schoology/IC date proposals | `workflows.schoology_gradebook`; validated target identity and current source evidence still required |
| Grading review | `workflows.feedback_pipeline`, shared review/proposal ledger; no automatic grade application |
| Roster status/history planning | `workflows.roster_reconciliation`; retain grade history and stable identities |
| Notes source and class/day routing | `adapters.todoist_notes`, `workflows.daily_notes`; journal append/readback still pending |
| Observation history | `core.observation_baseline` in the existing SQLite source-revision ledger |

See `docs/WORKFLOW_INPUTS_20260914.md` for exact input boundaries. Several
workflow builders still accept fixtures only; never relabel live data as a
fixture to bypass their protections. The HTML owner should consume adapted
receipts rather than building another comparator, queue or state database.

## Handoff acceptance checklist

- Existing Agenda page, all classes, shared date/week selector, notes and links.
- All four operation kinds expose status/time/evidence/next action.
- Preview, unverified and live evidence visibly distinguished.
- Synthetic pass and read-only pass cannot show synchronization active.
- Unsynced edits stay labelled until actual saving and reread are verified.
- Existing AIHS tasks reused; no duplicates created.
- Stable source/file IDs and grade history preserved.
- Current Agenda-to-parent link checks precede Schoology checks.

Backend contract tests: `tests/test_operations_status.py`. This checklist is
not a claim that the HTML interface has been implemented or accepted.
