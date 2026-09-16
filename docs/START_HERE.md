# Shared entry point for the School Workflow Hub

Updated September 16, 2026. This private repository is the canonical code and
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
6. [Browser run coordination](BROWSER_RUNS.md): the separately authorized browser-assisted roster/grading path when a district IC API is unavailable. It reuses private cloud state and does not turn browser reports into server-verified school results.

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
  boundary; its actual private provider is still missing.
- `hub-roster-sync/v1` is the authenticated shared status boundary at
  `/api/roster-sync`; it reuses the cloud task/lease model and remains
  status-only until its IC reader and roster-workbook writer are accepted.
- Firestore is the hosted task/result store. SQLite helpers remain offline
  synthetic fixtures; do not introduce operational dual writes.
- Cloud deployment, credentials, billing, Actions enable variables and schedules
  remain with the cloud owner. This integration publishes code only.

Save, Run, grade posting, email and printing remain disabled until their actual
integration and acceptance. Preserve stable file/slide IDs and grade history.
Keep student records, source IDs, credentials and browser profiles out of GitHub.

For every continuation, record the code revision, checks, exact remaining scope
and evidence level. A code pass is not a school read, hosted deployment or live
end-to-end result. Update existing task records rather than creating duplicates.
