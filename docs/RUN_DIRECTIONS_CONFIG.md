# Filling the control panel's run directions

The directions on control panel page 00 are stored in `web/run-directions.json`
with the teacher's wording intact and every school identifier replaced by a
`{{placeholder}}`. `AGENTS.md` forbids actual school URLs, IDs and accounts in
this repository, so the values live on the running service instead of in the
tree. They reach the panel through one environment variable.

## The variable

`HUB_RUN_DIRECTIONS` holds a single JSON object mapping declared placeholder
names to short strings. Any name the template does not declare is refused, and
so is a value longer than 500 characters or of any type but a string. Setting
the variable on the Cloud Run service is what makes the filled directions appear
on every computer the teacher signs into, rather than on whichever laptop holds
a local file.

The declared names are listed in `placeholders` in `web/run-directions.json` and
mirrored in `DECLARED` in `src/school_workflow_hub/adapters/run_directions.py`; a
test fails if the two ever disagree. The shape, with the values removed:

```json
{
  "timezone": "",
  "school_year": "",
  "school_account": "",
  "infinite_campus_url": "",
  "dashboard_url": "",
  "agenda_url": "",
  "roster_workbook_url": "",
  "assessment_history_url": "",
  "roster_sections": "",
  "roster_protected_cells": "",
  "checkpoint_override": ""
}
```

`checkpoint_override` is the exception and is meant to be absent most of the
time. It names one cycle's assignment, its section of the directions drops out
entirely when it is unset, and leaving a stale one in place would direct a run at
an assignment whose checkpoint has already passed.

## Where to set it

Cloud Run console, service `school-workflow-hub`, region `us-central1`: **Edit &
deploy new revision** → **Variables & Secrets** → **Add variable**. Setting it
starts a new revision, which is the only step needed; no rebuild is required.

A value here is private input, not a preview asset. It is served only to a
request carrying a verified IAP identity on the allowed list, exactly as the
Agenda reader is, and `/api/run-directions` answers a 403 or a 503 otherwise. No
value is written to a log or repeated in an error.

## Reading it back

`GET /api/run-directions` returns `schema_version`, the `values` object, the
`missing` list of declared names still unset (excluding the optional checkpoint)
and `complete`. The panel asks the service first and falls back to a gitignored
`web/run-directions.local.json` for local development; that file is deliberately
absent from the served asset allowlist, so it never reaches the deployed service.
