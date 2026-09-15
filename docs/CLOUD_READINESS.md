# Cloud integration: remaining work and acceptance

Updated September 15, 2026. This is the implementation and verification checklist
for the existing Python backend and Agenda interface. It supersedes older
handoffs that say hosting or the dated Agenda reader does not exist. It is not
the operational student/task database. Research remains owned by the other chat.

The storage implementation and acceptance procedure is documented in
[Private cloud state](PRIVATE_CLOUD_STATE.md). Its Python store, Firestore adapter,
fenced claims and fictional-data verification route are deployed. Authenticated
HTTPS readback on September 15 verified a stored fictional record across two
service revisions, a competing-claim rejection and idempotent result retry. The
latest acceptance receipt is summarized in that document. Check the authenticated
storage endpoint again when resuming work; a historical receipt is not a current
health check.

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
- Firestore is connected through the attached runtime identity. The live readiness
  endpoint reported `persistent_storage_verified=true`, private storage
  `restart_verified` and shared claims `storage_probe_verified`. Anonymous hub
  requests redirect to Google sign-in; anonymous Firestore reads return 403.
- All 460 Python tests pass. Forced simultaneous claims, a superseded worker's
  commit, expired ownership, repeat results and failed-scan baseline preservation
  are covered locally; those local cases are distinct from the live probe above.
- No hosted Agenda writer, IC reader/writer, submission assessor, teaching-results
  provider or delivery adapter is connected by this release. No student records
  were processed by the storage acceptance.
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
| 1 | Private storage and shared ownership are deployed; bind each teaching provider to this store next. | Live fictional write/readback, repeat result, competing-claim rejection and cross-revision persistence passed. Forced races, expiry recovery and baseline preservation passed locally. Actual student/result/history integration still requires scoped acceptance. |
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

## Durable state is connected; teaching providers remain

Firestore is the single hosted state store. SQLite migrations and helpers remain
offline fixtures; they are not a second operational database. The hosted adapter
uses runtime IAM, bounded reads and atomic commits with version preconditions.
Successful result publication includes its task, run receipt and optional baseline
in one commit. It does not imply that any school destination was updated.

Teaching readers, reviewed result publication, learning-history queries and the
Page 2/Page 3 provider still need to use this store with stable identity and source
revision mapping. Decide and verify backup/restore before relying on it for real
student history. Agenda saves, school posting/delivery and unattended runs remain
disabled. Research work and the existing Friday scan were not changed.
