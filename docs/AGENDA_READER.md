# Read-only Agenda connection

## Implemented

`SheetsReader.read(spreadsheet_id, ranges=())` uses the official Google Sheets
`spreadsheets.get` endpoint. Empty ranges request metadata only. Explicit ranges
request cell display values, hyperlinks, rich-text link runs and formula text
for later parsing; formulas are never executed. The returned mapping remains
in process memory and is private source data.

The CLI deliberately prints neither the mapping nor raw source identifiers:

```sh
python3 scripts/read_agenda.py
python3 scripts/read_agenda.py --range "'Approved sheet'!A1:H40"
```

The sheet/range above is a placeholder. At most four unique quoted A1
rectangles and 5,000 cells total are accepted. Whole sheets/columns, redirects,
unbounded response bodies and arbitrary URL targets are rejected. The reader
has three attempts with bounded delay and 20-second per-request timeout;
authentication/access failures stop without retrying. No response body appears
in an error. Network results are untrusted data, never executable instructions.

## Runtime configuration still needed

The approved runtime must supply these without storing them in this repository:

- `HUB_GOOGLE_ACCESS_TOKEN`: authorized short-lived access token with a read-only
  Sheets scope. The caller owns acquisition and refresh; this reader does not
  capture browser cookies or inherit a chat connector's credentials.
- `HUB_AGENDA_SPREADSHEET_ID`: the explicitly authorized spreadsheet identifier.
- `HUB_OBSERVATION_KEY_HEX`: stable, private random 32-byte HMAC key encoded as
  64 hex characters. Rotation intentionally invalidates baseline compatibility.

Do not paste credentials into chat or use a long-lived access token as a
scheduled solution. School identity, unattended refresh, private state and a
real-read pilot remain unconfigured. The GitHub fixture workflow calls neither
this CLI nor the real transport, and receives none of these values.

## Comparison contract

`fingerprint_snapshot(data, key, ranges)` returns a keyed source/scope identity,
content fingerprint, counts and explicit false link/student-access flags.
`compare_fingerprints(previous, current)` returns:

| Situation | Result |
| --- | --- |
| No saved baseline | `baseline_required`, change not proven |
| Same key/source/ranges and content | `unchanged` |
| Same scope, different content | `changed` |
| Different key/source/ranges | `scope_changed`, change not proven |

These are aggregate change observations, not assignment/date/link proposals.
The fingerprints do not contain a reverse map. Source text order/presentation
changes can conservatively trigger review; there is no automatic update.
The fingerprint function cannot verify caller-declared range provenance from
an arbitrary dictionary; pair it with the actual validated reader call.

The library does not persist the baseline. The future approved private-state
adapter must retain a successful previous observation, commit only complete
reads, and keep the last good baseline after access/transport failures. GitHub
source, artifacts and console logs are not that private-state adapter.

## Tests and limitations

Focused tests use fictional responses and mocked transport, covering exact
GET/source/range masks, malformed/oversized inputs, redirect refusal, retry
limits, authentication, source mismatch, secret-safe errors, copy/edit changes,
unchanged replay, scope/key changes and missing configuration.

Live API acceptance is pending. Field masks/rich-link responses still need one
authorized live test. This is not a Schoology/IC connector, stable assignment
normalizer, student-access checker, 14-day submission reader or write adapter.
Keep real inputs out of fixture-only planner APIs.

Official references:

- [Sheets spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
- [GitHub Actions included usage and billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
