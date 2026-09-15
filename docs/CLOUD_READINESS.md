# Cloud integration: remaining work and acceptance

Updated September 15, 2026. This is the implementation and verification checklist
for the existing Python backend and Agenda interface. It supersedes older
handoffs that say hosting or the dated Agenda reader does not exist. It is not
the operational student/task database. Research remains owned by the other chat.

## Current evidence

- The accepted feedback and teacher-summary layouts, completion rules, missing-work
  note, commitment form, and separate learning graphs are implemented in Python
  and the existing HTML frontend. Prompts are versioned and readable from Agenda.
- Cloud Build deployed `d49d379` successfully. On September 15, authenticated HTTPS
  checks returned 200 for Agenda metadata, three dated class lessons, and prompts.
  Completion, feedback and summary prompts matched the deployed bundle.
- A correctly scoped teaching-results request returned 503
  `teaching_results_not_connected`. `web.application` has a provider injection
  point but the deployed entry point does not supply a private provider.
- No hosted Agenda writer, IC reader/writer, submission assessor, shared task
  claims, persistent private state or delivery adapter is connected by this release.
- Prior documentation records one supervised browser IC test score being saved,
  reread and removed. That is narrower than a server-side IC connector and was
  not repeated by this audit.

## Runtime status

`GET /api/readiness` now returns `hub-hosting-readiness/v2`. It verifies the
request's signed IAP assertion and then performs a bounded Agenda metadata read.
It reports those checks independently: a source failure does not erase a
successful sign-in. Configuration alone never establishes verification.

`school_connected=true` is explicitly scoped by
`school_connection_scope=agenda_metadata_only`. It does not prove dated lesson
mapping, submissions, IC, persistence or writes. Dated lessons and prompts require
their own endpoints and remain `not_checked` in this response. A supplied teaching
provider is `configured_not_verified` until a scoped result is read and validated.
The response contains no source IDs, accounts, tokens or student content.

HTTP 200 means the status request was handled; inspect the check states for its
outcome. `HEAD /api/readiness` and `/healthz` perform no school-source reads.
Save and Run remain false. No fixture data is promoted to live status.

## Next milestone: one lesson completes the cloud workflow

| Order | Python/cloud work | Evidence required before calling it complete |
| --- | --- | --- |
| 1 | Connect one private durable store to the existing task/run, baseline, result and observation contracts. | Restart/redeploy preserves data; concurrent claims have exactly one owner; an expired owner cannot overwrite the successor; failed scans preserve the last successful baseline; repeat requests do not duplicate results. |
| 2 | Connect the actual Agenda/Drive and IC readers. | Correct due assignment and assigned roster; active mapped folder; all submission pages/history; exact document revision and stable student match; failed or partial scans cannot produce missing-work zeros. |
| 3 | Bind actual-work assessment to the accepted prompts and lesson inventory. | Completion arithmetic remains deterministic Python. Reviewed conceptual interpretation uses exact writing and the current versioned rubric; no invented timing, improvement, quotes or assessment. A model, if later configured, supplies constrained reviewed evidence, not permission to post grades. |
| 4 | Persist a reviewed result and inject its provider into the existing HTTP entry point. | Authenticated Page 2 and Page 3 show the same stored class/assignment/revision as the private record, including history across dates. Refresh is not a new run. |
| 5 | Complete the original two-way Agenda editing requirement. | One edited plan/note updates its authoritative weekly/list cells, preserves formulas and unrelated cells, survives reload, rejects a concurrent edit, and has exact destination readback. |
| 6 | Connect completion-grade posting to IC. | Exact assignment/student target, approved score, preserved earlier grade, idempotent posting and matching IC readback. Missing work stays unscored through the full 14th day; a fresh complete scan is required before proposing zero. GB attendance pages are not a prerequisite. |
| 7 | File feedback and teacher summaries; connect parent contact and requested printing. | Correct private lesson folder and working document links; current IC guardian contacts; delivery/print receipt; retries cannot send or print duplicates. Printing occurs only when directed. |
| 8 | Enable unattended runs for the accepted scope. | An entire supervised lesson succeeds including readback, interruption/retry, prompt drift, partial scans and duplicate-run checks. Each workflow uses the same shared task/status record across Claude and ChatGPT. Coordinate the existing Friday roster scan rather than creating another schedule. |

Use a bounded fictional private record first for persistence and claim/retry
checks, then one teacher-reviewed real lesson for read-only assessment. Test each
write destination against the explicit authorized scope and retain receipts.
Credentials stay in runtime secret/identity facilities; school records stay in
approved private storage, never GitHub, instruction cells, fixtures or logs.

## Persistence is a real remaining dependency

The existing SQLite migrations and helpers are exercised locally. The current
Cloud Run image copies application code and web assets only; it does not deploy
or initialize the SQL migrations or connect a durable database. Simply enabling
a flag or pointing SQLite at the container filesystem will not complete this.
Cloud Run documents that its container filesystem is in memory and loses written
data when the instance stops. See the [official runtime contract](https://docs.cloud.google.com/run/docs/container-contract#file_system).

Choose and verify a durable backend for the existing contracts, then retire any
temporary test storage. Keep a single authoritative task/status record and
preserve source revision, prompt/rubric version, evidence and run ownership.
No provider, new billing service, recurring job or external write is enabled by
the readiness correction.
