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

## Teaching Hub v2 (`/v2/`)

`src/hub_v2/` is a small, read-only replacement for Pages 01 and 02, served at
`/v2/` by the same Cloud Run service, sign-in and settings. **Lesson plans**
reads each class's weekly Agenda tab by header name and week label.
**Student work** reads the Form(s) linked in the lesson's "Turn it in" cell and
everything in the lesson's turn-in folders in `00 ALL TURN IN / <class>`: any
folder named for the lesson's week, and each Form's own `(File responses)`
folder wherever it sits. Files are sorted to an assignment by where they sit or,
when one folder holds several assignments, by the deck they were copied from;
anything unsortable is listed as "Not sorted". Each file is matched to the
roster by school email or by the username/name in its file name. Each student's
current file is shown (Form uploads first; trashed files and files named for
another week are never picked) with the rest as history, alongside who hasn't
turned in and a grade sheet CSV. A problem
with one tab, cell or Form is shown on that card; it never blanks the page.

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
previews. It does not start a hosted job or contact school systems. Without
`requirements-hosting.txt` installed, the four signed-identity tests skip and are
not exercised; the gate expects exactly those skips and fails on any other. The
separate GitHub Actions workflow is manual and requires its existing enable
variable; this code reconciliation does not change it or any cloud schedule.

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
