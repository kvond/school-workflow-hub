# School Workflow Hub

The canonical private repository for the school's Python workflow code,
versioned contracts, prompts, synthetic tests and agent handoffs is
[kvond/school-workflow-hub](https://github.com/kvond/school-workflow-hub).
School records and credentials belong in the approved private runtime, not here.

## Status

Start with [START HERE](docs/START_HERE.md), the
[September 16 integration record](docs/PYTHON_INTEGRATION_20260916.md), and the
[capability inventory](config/capabilities.json).

The September 15 acceptance records verify hosted Agenda metadata, dated lessons,
prompts, school-account authentication, and Firestore persistence/shared claims
using fictional diagnostic data. These are historical acceptance records, not a
fresh health check. See [cloud readiness](docs/CLOUD_READINESS.md).

The Python library includes completion rules, reviewed feedback and class-summary
composition, learning-history validation, submission and Todoist intake contracts,
printable previews, and the reconciled four-page workspace and research drafts.
Live teaching-result providers, actual submission/IC readers, Agenda saves,
grade posting, filing, delivery and printing remain unconnected. Research stays
deferred. Save and Run remain disabled; publishing this code does not deploy it.

## Validation

Python 3.9 or later is required; the local checks use the standard library only.

```sh
python3 scripts/hub_status.py --json
python3 scripts/cloud_validation.py
python3 scripts/preview_workflow_inputs.py --out /tmp/school-workflow-hub/input-preview
```

On macOS use `/private/tmp/school-workflow-hub/input-preview` for the last
command. It writes fictional report HTML and a receipt, repeats the workflow,
and refuses to overwrite a different existing report.

`cloud_validation.py` runs the full local test suite and two identical synthetic
previews. It does not start a hosted job or contact school systems. The separate
GitHub Actions workflow is manual and requires its existing enable variable;
this code reconciliation does not change it or any cloud schedule.

For an installable Python library wheel, use an isolated environment with current
packaging tools (the older macOS system pip produced an invalid empty wheel):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

The repository checkout also contains the app assets, configuration and validation
scripts. Installing the library wheel alone is not a cloud deployment.

## Existing HTML Agenda integration

The existing frontend is in `web/`; its authenticated WSGI application is
`src/school_workflow_hub/web.py`. The deployed app can read Agenda and prompts.
Pages 2 and 3 use [hub-teaching-results/v1](docs/TEACHING_RESULTS_INTEGRATION.md),
whose private provider remains unconnected. The separate
[agenda-workspace/v1](docs/FOUR_PAGE_WORKFLOWS.md) library projection is not an HTTP
provider and does not replace that contract. Two-way Agenda saving is unfinished.

Read [agent boundaries](AGENTS.md) before continuing implementation or activation.
