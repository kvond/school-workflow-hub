# Shared entry point for the School Workflow Hub

## Assessment workflow status as of September 18, 2026

Dashboard-operated grading is live (PRs #14–#22, service revision from commit
on `main`). From Page 2 the teacher can: **Load lesson → Generate feedback for
this submission / Generate pending feedback for this lesson → review the draft
(judgments, levels, evidence quotes, report) → Save reviewed assessment to the
workbook → reopen via View work & feedback → Print feedback**.

- `/api/assessment-save`: capture-bound saves (fresh export + content hash;
  evidence excerpts must be literal substrings; Python-only arithmetic;
  supersede + strict readback). Four A4 Biology Cycle 02 submissions are
  assessed and saved; one (a pre-redraft deck without the manifest header) is
  held for manual reconciliation, never treated as missing.
- `/api/assessment-run`: Anthropic drafting on the Cloud Run identity —
  workspace **School Workflow Hub**, model `claude-sonnet-5`, key in Secret
  Manager `hub-anthropic-api-key` bound as `HUB_ANTHROPIC_API_KEY` (never in
  the repo or logs). Live Agenda prompts are read at draft time (stale-prompt
  refusal). Billing guards, all live-verified: already-assessed unchanged
  content and stored drafts never re-bill; drafts persist server-side per
  content hash and survive refreshes; a claim serializes duplicates. Budget:
  $5/month workspace hard limit; teacher-approved $5 total pilot budget;
  **$0 spent so far** (no eligible pending submission has needed a call yet).
- Everything server-side runs under the runtime service account: no local
  AI-to-Drive connector is in the assessment path; a school laptop only needs
  the IAP dashboard sign-in. Panel messages distinguish dashboard sign-in
  failures from server-side Drive access failures.
- IC handoff: saved rows flow into the existing Page 2 posting review
  unchanged (verified listing all new records); no grade has been posted.

Updated September 17, 2026. This private repository is the canonical code and
handoff location for authorized agents. Keep deterministic workflow logic here
and school data in the approved private runtime. Continue the existing Agenda
and backend; do not create another dashboard, task database or routine agent-only
workflow.

## Read these first

1. [AGENTS.md](../AGENTS.md): ownership and implementation boundaries.
2. [Python integration](PYTHON_INTEGRATION_20260916.md): module map, validation and remaining connections.
3. [Cloud readiness](CLOUD_READINESS.md): latest recorded hosted acceptance and the next one-lesson milestone.
4. [Capabilities](../config/capabilities.json): code evidence and scoped remaining work.
5. [Current prompts](AGENDA_PROMPTS.md) and [feedback v2](FEEDBACK_V2.md): authoritative instructions and the accepted report layout.
6. [Submission monitoring](SUBMISSION_MONITORING.md): the hosted turn-in folder watcher and its dashboard panel.
7. [Browser run coordination](BROWSER_RUNS.md): the separately authorized browser-assisted roster/grading path when a district IC API is unavailable. It reuses private cloud state and does not turn browser reports into server-verified school results.

Current Page 2 scope: the teacher authorized supervised posting of the existing
reviewed workbook grades from a signed-in school Chrome or Edge browser on their
Mac or school Windows computer. This narrow exception permits a connected local
browser worker, including an assistant, to claim the shared run, perform the
selected verified writes and record an attributed receipt. Each computer uses
its own school session; credentials and cookies are never copied to the server
or between computers. It does not enable a schedule or regrade submitted work.
The [browser companion](../browser-extension/README.md) implements the Page 2
review and bounded executor for the current assignment's Score panel.

Page 2 grading status as of the evening of September 17, 2026:

- Mac read/verify/skip/receipt path: **live verified** end to end with an
  executor-completed supervised run (matching grades skipped, no school write,
  attributed receipt saved and read back through the dashboard after reload).
- Companion **0.2.3** is the accepted Mac build; `main` matches it exactly.
- An actual new blank-grade posting with IC readback is **not yet live-tested**.
  The first genuinely blank reviewed grade that needs posting is that
  acceptance; never change a correct grade to test writing.
- The Windows computer's companion is **not yet installed or tested**.
- Known minor UI defect: the Page 2 review modal's "Review & post in school
  browser" button stays disabled after a failed attempt; close and reopen the
  review to retry. Cosmetic only.
- The private workbook connection is repaired and reads live records through
  the authenticated dashboard. Do not reopen that investigation without fresh
  evidence of an actual failed read.
- Live-session behavior the companion depends on is recorded in
  [BROWSER_RUNS.md](BROWSER_RUNS.md) and the companion README; a stored browser
  report alone still does not prove a write.

```sh
python3 scripts/hub_status.py --json
python3 scripts/cloud_validation.py
```

These commands use the standard library. Status inspection reads the repository;
validation runs the full synthetic suite and verifies identical replay. Neither
command performs a live health check, loads student records or activates a job.

## What is established

The September 15 acceptance records in `CLOUD_READINESS.md` and
`PRIVATE_CLOUD_STATE.md` verify hosted signed-IAP authentication, Agenda metadata,
dated class lessons and prompts. Firestore write/readback, competing ownership
rejection, retry receipts and persistence across service revisions were verified
with a fictional record. These are recorded observations, not a new health check.

The September 16 reconciliation publishes the previously local workspace,
Dashboard navigation and research draft modules alongside the newer teaching
results, feedback, learning history and cloud-state implementation. Pure Python
composition remains distinct from a live provider or destination executor.

The live Agenda prompt cells are the runtime instruction source;
`web/teacher-prompts.json` is the reviewed release reference. The September 14
text under `prompts/` is historical and cannot override feedback v2. Research
definitions preserve the approved authorial separation; other proposed criteria
still require teacher review. Collection and student coding remain deferred.

## Next session state — after the September 17 evening acceptance

The Mac Page 2 read/verify/skip/receipt path works and is recorded above; do
not rework it without fresh evidence. What remains for the broader system, in
priority order:

1. Submission monitoring across the two-week post-due window: **live accepted
   September 18** for all three classes' current lessons (see
   [SUBMISSION_MONITORING.md](SUBMISSION_MONITORING.md) for exactly what was
   verified and what remains synthetic-only). Remaining pieces: a scan
   schedule (separate explicit approval plus a scheduler-authorized entry
   path), and live change detection against a genuinely edited student file.
2. Grading the new and modified submissions that monitoring surfaces, using the
   current Agenda prompts for completion, feedback and class summaries, feeding
   the same reviewed-workbook path Page 2 already posts from.
3. Agenda-to-IC date alignment and two-way Agenda plan/note saving with
   readback (sequence items 2 and 5 below).
4. Roster updates through the existing `hub-roster-sync/v1` boundary once its
   IC reader and roster-workbook writer are accepted.
5. Windows acceptance: install the packaged companion in the school Windows
   browser's own signed-in profile and repeat the supervised review there. Mac
   acceptance does not transfer.
6. First live blank-grade posting with IC readback, when a genuinely blank
   reviewed grade needs it.

## Next implementation sequence

1. Recheck the existing hosted service and runtime access when beginning live
   integration. Reuse its identity and Firestore store rather than creating them
   again. Verify the backup/restore approach before relying on real student history.
2. Connect one actual due lesson's active submission folder and current IC
   assignment/roster readers. Resolve stable identities, individual extensions,
   full pagination, cumulative history and exact document revisions. A recent
   14-day crawl alone cannot prove a learner never submitted.
3. Supply actual reviewed writing assessments to the accepted completion and
   feedback functions. Preserve prior versions. Missing or ambiguous evidence is
   a review state, never permission to invent quotes, growth or student zeros.
4. Bind that result to the existing private state/claim service and inject the
   `hub-teaching-results/v1` provider into the existing HTTP app. Verify Page 2 and
   Page 3 show the same stored scope and revisions after reload.
5. Complete two-way Agenda plan/note saving with authoritative weekly/list
   mapping, concurrent-edit rejection and destination readback.
6. Implement and accept each authorized IC, feedback-filing, email or requested
   print destination separately. A stored proposal is not successful delivery.
7. Verify the full supervised lesson before enabling unattended execution.
   Coordinate existing schedules; do not create duplicate jobs. Research follows
   teaching-workflow acceptance.

The current priority is Agenda-to-IC alignment and real teaching inputs. The
older parent-page/Schoology-first sequence and claims that hosting does not
exist are superseded. Older 252/322/361/382-test counts are historical checkpoints;
use the integration record for this reconciliation's validation.

## Contract and ownership boundaries

- `agenda-operations/v1` remains the strict operation-status projection.
- `agenda-workspace/v1` is a host-injected library wrapper for planning and
  private views. It is not a replacement for the deployed Page 2/Page 3 provider.
- `hub-teaching-results/v1` is the existing authenticated teaching-results
  boundary for dated lesson composition; its actual private provider is still
  missing. Page 2 reads the separate existing private grading workbook through
  `hub-grading-records/v1`; see [the workbook contract](PRIVATE_GRADING_WORKBOOK.md).
- `hub-roster-sync/v1` is the authenticated shared status boundary at
  `/api/roster-sync`; it reuses the cloud task/lease model and remains
  status-only until its IC reader and roster-workbook writer are accepted.
- Firestore is the hosted task/result store. SQLite helpers remain offline
  synthetic fixtures; do not introduce operational dual writes.
- Cloud deployment, credentials, billing, Actions enable variables and schedules
  remain with the cloud owner. This integration publishes code only.

The Page 2 posting exception authorizes implementation and supervised execution
only after source, identity, assignment, current-grade and ownership checks.
The executor's read/verify/skip path is live-accepted on the Mac; its write
path and the Windows install are not. Other unconnected Run, email and
printing actions still require their own integration and acceptance. Preserve
stable file/slide IDs and grade history. Keep student records, source IDs,
credentials and browser profiles out of GitHub.

For every continuation, record the code revision, checks, exact remaining scope
and evidence level. A code pass is not a school read, hosted deployment or live
end-to-end result. Update existing task records rather than creating duplicates.
