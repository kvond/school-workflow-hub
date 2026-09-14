# School Workflow Hub

Private cloud-preparation repository: reviewed application code, synthetic
fixtures and tests only. No school documents, actual assignment links, student
records, credentials, local reports or original repository history are included.
Operational task IDs in the acceptance script were replaced with fictional
identifiers in this export; the original local project is unchanged.

## Status

**Python is the implementation, and cloud execution is the target.** Authorized
AI agents should begin with [START HERE](docs/START_HERE.md) and the
[machine-readable capability inventory](config/capabilities.json).

The later teaching safeguards are now included. A bounded read-only Google
Sheets transport and opaque change-comparison API are implemented, with mocked
transport tests. The runtime credential and persistent baseline are not
configured; this reader has not performed a live school read.

**Workflow continuation:** explicit Agenda row normalization and atomic opaque
checkpoints now use the existing state ledger. A read-only Todoist note adapter,
date/class routing, bounded 14-day submission page collector, and printable HTML
report preview are implemented and integrated. See
[workflow inputs](docs/WORKFLOW_INPUTS_20260914.md). The complete suite passes
**322 tests** locally; real source acceptance remains pending.

Cloud deployment/hosting and activation belong to the separate cloud chat.

Cloud execution is not yet verified. No recurring schedule, paid model call or
school-system write is enabled. Source code on GitHub does not by itself mean
that live lesson work runs while a laptop is off.

## Validation

```sh
python3 scripts/hub_status.py --json
python3 scripts/cloud_validation.py
python3 scripts/preview_workflow_inputs.py --out /tmp/school-workflow-hub/input-preview
```

On macOS use `/private/tmp/school-workflow-hub/input-preview` for the last
command. It writes fictional report HTML and a receipt, repeats the workflow,
and refuses to overwrite a different existing report.

The GitHub workflow is manual-only and requires the private repository's default
branch and `HUB_CLOUD_VALIDATION_ENABLED=true`. Keep that variable absent until
the owner approves Actions usage/spending controls and the first cloud test.
Do not enable paid overages or add school credentials. Its ten-minute timeout
limits one job, not total account spending.

The job runs existing tests and two identical synthetic previews. It emits an
aggregate summary, with no database/report artifact uploads. School access,
private persistent state and recurring school tasks need separate implementation
and verification. See [agent boundaries](AGENTS.md) and the
[offline review note](docs/TOMORROW_TESTS.md).

## Existing HTML Agenda integration

Use [the integration contract](docs/HTML_AGENDA_INTEGRATION.md) and
`examples/agenda_operations.preview.json` to connect the existing Agenda page
to the shared Python status model. The Agenda HTML app, transport and edit saving
are not implemented in this repository.
