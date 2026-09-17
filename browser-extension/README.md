# Signed-in school browser companion

This Chrome/Edge extension is the supervised Page 2 grade-posting candidate for
macOS and Windows. Code and synthetic tests do not establish a successful live
installation or grade write. Test each authorized browser/profile separately.

The teacher opens the current assignment's Score panel in Infinite Campus and
starts the review from Page 2 in the same school browser profile. The companion
fresh-reads the existing grading workbook through the authenticated dashboard,
compares the explicit source references to that current assignment, and presents
its own review in IC. Only the trusted confirmation button in that review can
start the executor. The Page 2 message itself cannot save a grade.

The supported adapter uses the observed visible IC DOM, exact student/section/
assignment identities, and the native Save control. It does not call IC APIs,
read hidden application state, change authentication, or store credentials.
An unsupported or ambiguous gradebook stops for review. It does not infer a
missing student ID or a revision assignment, scale scores, overwrite a
conflicting nonblank grade, or substitute zero for an unscored component.

## Ownership and recovery

The original signed-in Hub tab performs a narrowly allowlisted set of operations:

- `source` reads `/api/grading-records` with fresh, same-origin credentials.
- `status`, `claim`, `renew`, `finish`, and `release` use the existing
  `/api/browser-runs` endpoint and its grading scope shared across computers.
- The claim request ID binds the reviewed plan hash. The server's ownership
  cookie remains HttpOnly; the extension never reads it. A random worker ID is
  private to the originating Hub content-script instance.
- The executor rechecks the source and ownership before each grade input and
  before Save. The adapter checks the exact live destination. Matching grades
  are preserved. Save must be followed by assignment reload and exact readback.
- Reports are explicitly attributed browser observations. Cloud persistence
verifies the receipt, not the school write independently.

While a review is open, its IC content script sends one private extension
heartbeat every 20 seconds to avoid the MV3 worker's normal idle suspension.
This does not fetch a source, renew a lease, contact the network, or perform a
school action. It stops on a final result or the original five-minute expiry.
A pending receipt retry keeps it only until that same expiry. Unexpected worker
restart still loses the binding and fails closed; this is not a persistent job.

Keep both tabs open. The review binding expires after five minutes. Closing,
navigating, or restarting either tab/worker invalidates it. A cloud lease may
still exist, so inspect its status and actual IC destinations before resuming.
An uncertain report save retains the identical report in private browser memory
for receipt retry only, including after the review expires. No further grading
operation is permitted while that report is pending. Worker/browser restart
loses this retry memory; shared lease recovery prevents an automatic repeat.

If a Save outcome cannot be verified, the public result has
`possible_changes:true` and `external_writes:null`. An unsaved dirty input may
instead report known zero writes with the same possible-changes flag. Neither
case permits retrying a school action without inspecting IC. A receipt retry
never repeats a grade fill or native Save.

## Permission and data boundary

The source manifest has no host permissions and empty configuration. The
packager adds only the two explicitly supplied exact HTTPS origins. It requests
no cookies, debugger, native messaging, persistent storage, or broad tab access.

The Hub bridge checks its exact origin and top-level window. Runtime messages
bind the extension identity, original Hub tab, IC tab, top frame, request UUID,
private session UUID, call UUID, and lifetime. Only the IC extension context can
ask the original Hub tab for source data or lease operations. Page messages are
limited to connection status and preparing a review. They cannot supply a URL,
fetch arbitrary resources, reserve a run, or request a grade write.

Student records and claim handles stay inside the private extension channel.
Public Page 2 replies contain only fixed status codes, bounded counts, expiry,
and effect certainty. There is no second school datastore, local daemon, model
call, recurring job, or background grading schedule.

## Page 2 protocol

Send with `window.postMessage(message, location.origin)` from the authenticated
Hub page:

```js
{
  channel: 'hub-grade-posting',
  version: 1,
  request_id: crypto.randomUUID(),
  type: 'status' // or 'prepare', with review from buildPostingReview()
}
```

`prepare` includes the fresh immutable `hub-grade-posting-review/v1` object. The
protocol preserves raw IC references, denominators, exact score strings, and
exclusions. Source and live destination evidence remain separate.

Replies use channel `hub-grade-posting-result`, version 1, and the same request
ID. The caller checks window source, exact origin, schema, and correlation.
The capability is `review_in_ic`; `posting_enabled:false` means the page message
cannot authorize a write. `review_opened` includes `expires_at`; a later result
with the same ID completes the review. A timeout is unconfirmed, not success.

Final codes include `already_matches`, `posted_verified`, `needs_review`,
`source_changed`, and `report_unconfirmed`. Counts are exactly
`{checked,changed,needs_review}`, each a bounded nonnegative integer. Known
verified writes use the changed count; uncertain effects use the explicit
possible-changes contract above. No reply contains student data or credentials.

The root-owned adapter API is:

```js
HubIcAdapter.prepare(review, {hubCall, onResult})
// Returns {code:'review_opened'} before the asynchronous final result.
// onResult receives only a safe code/count/effect summary.
```

`hubCall(action,payload)` privately returns the raw authenticated source response
or the existing browser-run response. Its payloads are `{}` for source/status,
`{request_id: sha256}` for claim, `{claim}` for renew, `{claim,report}` for finish,
and `{claim,reason}` for release. No arbitrary method, URL, or workflow is accepted.

## Package and test

These origins are fictional examples. The Python standard-library packager
writes a new unpacked directory and adjacent ZIP, refusing to overwrite either.

```sh
python3 browser-extension/package_extension.py \
  --hub-origin https://hub.example.test \
  --ic-origin https://school.example.test \
  --output /absolute/path/outside/repository/hub-browser-companion
node --test browser-extension/tests/*.test.cjs
python3 -m unittest discover -s browser-extension/tests -p 'test_*.py'
```

The ZIP is reproducible for identical source and origins, and contains only the
extension runtime, manifest, and configuration. No dependency download, remote
code, build service, key, or certificate is required. Do not commit generated
school deployment packages or private configuration.

When the concrete reviewed package is ready, unpack it and use **Load unpacked**
in `chrome://extensions` or `edge://extensions`, with Developer mode enabled if
permitted. Install in the school profile on each computer. Managed school policy
may require an administrator to permit installation; do not bypass restrictions.
Mac acceptance does not establish Windows acceptance. Successful existing-score
checks establish a live read/skip path, not an unobserved grade-write path.

Official references: [Chrome content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
and [Edge local extension testing](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading).
