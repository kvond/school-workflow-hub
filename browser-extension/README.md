# Signed-in school browser companion

This Chrome/Edge extension is the supervised Page 2 grade-posting candidate for
macOS and Windows. Code and synthetic tests do not establish a successful live
installation or grade write. Test each authorized browser/profile separately.

## Current acceptance state — September 17, 2026

**The installed Mac integration is incomplete.** Installation in the authorized
school Chrome profile and the Hub's `companion_available` status were observed.
The selected IC scores were manually read as matching their recorded
grades. That was a manual comparison, not an executor-completed read/skip run.
No completed grading receipt or live grade write/readback has been verified.
The next live acceptance pass was interrupted after browser fixes. Do not infer
that the installed package contains every subsequent repository change.

Windows has not been installed or tested. The code and package are portable;
the authenticated school session and live acceptance are specific to each
computer and browser profile. Preserve this distinction in any AI handoff.

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

## Browser observations that future changes must preserve

The implementation is in [ic-adapter.js](ic-adapter.js), especially `surface`,
`identify`, `snapshot`, `verifyBindings`, and `prepare`. These are observations
of the rendered browser UI, not permission to query private IC APIs:

- The current Score panel is `ic-multiscore` inside the same-origin
  `iframe#main-workspace`. IC also gives its bulk-fill field the class
  `multiscore__simple-score--input`. Select only per-student inputs with
  `id^="as_alignmentMultiScore_"`, and require the complete numeric
  `as_alignmentMultiScore_<score-group>-<row-index>` grammar. The bulk-fill box
  is never a grade destination; a row index is never a student ID.
- Whitespace in rendered `aria-label` values varies. Store and compare the same
  whitespace-normalized complete label at snapshot and fill time. The observed
  shape is `<assignment-code> MP: Marking Period Score input for <display-name>`.
  Retain the assignment code and marker checks. A normalized label locates a
  control; it does not establish student identity.
- Kendo renders the student link and score input in separate locked tables.
  Their matching `data-kendo-grid-item-index` locates the visible link to open.
  Read the resulting **Student Summary** using its nearest `[role="dialog"]`.
  Do not walk upward to a broad ancestor containing unrelated Close buttons.
- The summary's **Number** supplies the official `student_id`. Its visible
  same-origin link at `/campus/sis/navigation/parent/student.ToolSet`, containing
  `selectedPersonID`, supplies the separate `person_id`. Select by the exact
  href path and query field, not an assumed text label. Require one such link,
  a matching visible student heading, and one Close button inside that dialog.
  Cache identity only for that live document; reload clears the cache.
- Opening Student Summary changes an IC route even though the assignment stays
  open. [background.js](background.js) stores the original full URL and permits
  only the observed suffix
  `//tertiaryRouteOutlet:studentInfoSidePanel/multiscore/<section>/<person>/1/<resource>`
  inside the existing assignment outlet. **The order is section, person, 1,
  resource.** Section and final resource must equal the original assignment;
  only the numeric person may vary. Closing the summary restores the same
  normalized URL. Other outlets, assignments, sections, query/hash changes,
  and all Hub URL changes invalidate the binding.
- Before Save, compare the full visible student snapshot with the approved
  snapshot plus only the intended blank-cell fills. Existing values, flags and
  all unrelated rows must still agree. A Save notification alone is not proof;
  reload the assignment and compare exact saved identities and scores.

These fixes need the interrupted live pass below. Synthetic checks in
[tests/ic-adapter.test.cjs](tests/ic-adapter.test.cjs) and
[tests/runtime.test.cjs](tests/runtime.test.cjs) are useful regressions but do
not reproduce every native browser interaction.

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
navigating outside the explicitly allowed Student Summary route, or restarting
either tab/worker invalidates it. A cloud lease may
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

The adapter API is:

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
checks establish a live read/skip path only when the executor and its receipt
actually complete; they do not establish an unobserved grade-write path.

Use each laptop's own signed-in school profile. Transfer the reviewed package
only; never transfer cookies, passwords, browser profiles, session IDs, pending
claims, or report-retry memory. Source `config.js` stays empty and the source
manifest stays without host permissions in Git. Supply the exact approved
origins explicitly when packaging outside the repository. Private configuration
and generated school packages are not repository data.

## Resume the interrupted live pass

1. Read [../docs/BROWSER_RUNS.md](../docs/BROWSER_RUNS.md), inspect the current
   shared grading status and any uncertain IC effects, and obtain the latest
   repository commit. Do not clear a lease or repeat a school action to make a
   test pass. Repackage current source with `package_extension.py`; a matching
   extension version label alone does not prove that the installed files match.
2. Update/reload the reviewed extension in the authorized school profile. After
   confirming there are no unsaved or uncertain edits to preserve, reload
   **both the Hub top-level tab and IC top-level tab**, then reopen the exact
   assignment's Score panel. Loading/reloading an extension does not refresh the
   content scripts in an already-open document.
3. On Page 2, choose the intended class/section and refresh submissions. Open
   the IC review and verify the identity checks and Student Summary open/close
   cycle. Keep both tabs open through the result. Do not rapidly repeat a
   failing source request or replace it with sample data.
4. For the two manually observed matching scores, complete **Verify matching
   grades** without changing either score to manufacture a write test. Require
   the executor's assignment reload/readback, matching grades, exact stored
   report hash, and final `already_matches` result. Until all occur, the live
   read/skip path remains incomplete.
5. A live posting claim additionally requires a separately selected authorized
   blank target, native Save, exact reloaded grade readback and matching receipt.
   Do not treat the matching-score pass as this write acceptance. Record each
   browser's observed result honestly; repeat installation and acceptance in
   the school Windows profile before calling that computer ready.

Continuation code map: [../web/grade-posting.mjs](../web/grade-posting.mjs) builds
the source review; [../web/browser-companion.mjs](../web/browser-companion.mjs)
handles Page 2 transport; [hub-content.js](hub-content.js) performs authenticated
Hub reads/lease operations; [background.js](background.js) binds tabs, routes and
lifetimes; [ic-content.js](ic-content.js) supplies private adapter ports;
[plan.js](plan.js) compares exact source/destination values;
[executor.js](executor.js) owns claim/renew/skip/fill/save/readback/report order;
[ic-adapter.js](ic-adapter.js) owns the visible UI selectors and actions.

If the review fails before its overlay appears, fixed background stage warnings
distinguish Hub binding transport/reply from IC prepare transport/reply. Adapter
warnings likewise use allowlisted codes. Preserve those safe diagnostics;
never log provider responses, raw records, credentials, or student identities.

Official references: [Chrome content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
and [Edge local extension testing](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading).
