# Hosted Agenda metadata check

The first live connection is a teacher-triggered metadata read through the
existing `SheetsReader`. It does not import lesson cells or use fixture planners.
The teacher reported successful school-account IAP sign-in and shared the Agenda
as Viewer with the existing attached Cloud Run service account.

## Runtime configuration

Use Cloud Run runtime settings, never source/build variables, for these values:

- `HUB_AGENDA_READ_ENABLED=true`: explicitly enables the metadata pilot.
- `HUB_AGENDA_SPREADSHEET_ID`: exact spreadsheet just shared by its owner.
- `HUB_READER_SERVICE_ACCOUNT`: email returned by describing the actual service.
- `HUB_IAP_AUDIENCE`: `/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE`.
- `HUB_ALLOWED_EMAILS`: comma-separated teacher emails also permitted in IAP.

Configuration remains absent by default. Deploying code alone does not enable a
source read. Do not grant domain-wide delegation or create/download service-account
keys. Keep the runtime identity a Viewer of the Agenda for this pilot.

## Request path and safeguards

The explicit **Check Agenda access** button calls `GET /api/agenda-connection`.
The endpoint first checks configuration, verifies the IAP JWT signature against
Google's public keys, expiration, issue time, issuer, exact service audience and
teacher allowlist. Raw email headers are not trusted. Only then does Google's
credential library obtain a short-lived Sheets-readonly token from the attached
runtime service account. Its actual email must match the configured identity.

`SheetsReader.read(source)` then requests metadata only. The response contains
the check timestamp, tab count and explicit false flags for loaded cells,
lesson mapping, saving and execution. No source ID/title, student data, token,
provider error body or user email is returned. Exceptions use fixed codes.
HEAD and write methods cannot trigger this read. No background polling or
scheduled task is added. A failed recheck clears the UI's verified label.

This is not the persistent task/status ledger. A result is a single observation,
not a saved baseline or a destination-write receipt. `/api/readiness` still
describes the unconnected lesson/workflow application; metadata success does not
change that. Actual dated lessons, distinct teacher notes, Slides links, source
mapping and durable synchronization remain separate steps.

## Acceptance

1. Verify the deployed build/revision includes this code.
2. Supply the reviewed runtime settings for the exact shared file.
3. Sign in as the teacher and click Check Agenda access. Record the displayed
   timestamp and tab count as live read evidence only after a successful result.
4. Verify an unauthorized/unsigned request cannot cause a source read. In local
   tests, forged signatures, wrong audience/issuer/user and expired/future tokens
   are rejected before obtaining a service-account token.
5. Leave preview data labeled and all saving/execution disabled.

Local validation: 338 Python tests passed with the hosting dependencies installed,
including real ES256 signature checks using synthetic keys and mocked key fetches.
These tests do not establish live Google credential refresh or school acceptance.

References:
- https://docs.cloud.google.com/iap/docs/signed-headers-howto
- https://developers.google.com/workspace/guides/create-credentials
- https://docs.cloud.google.com/run/docs/securing/service-identity
