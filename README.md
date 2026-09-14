# School Workflow Hub

Private cloud-preparation repository: reviewed application code, synthetic
fixtures and tests only. No school documents, actual assignment links, student
records, credentials, local reports or original repository history are included.
Operational task IDs in the acceptance script were replaced with fictional
identifiers in this export; the original local project is unchanged.

## Status

Code migration only. Cloud execution is not yet verified. No live school
connector, recurring schedule, paid model call or school-system write is enabled.
Live lesson work cannot yet continue here while a laptop is off.

## Validation

```sh
python3 scripts/cloud_validation.py
```

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
