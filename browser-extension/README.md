# Signed-in school browser companion ("Chrome helper")

## Weekday batch and missing-work zeros (0.5.13)

The opted-in weekday run processes all exact mapped assignments in one batch,
without an assignment-by-assignment approval popup. It fills blank scores and
raises lower scores only, then saves, reloads, and verifies each assignment.
The Hub proposes 0 for verified missing work after the two-week window that
starts with the first roster student's submission (never before that lesson's
Agenda week). A later in-window submission can replace the 0 by raising it.
An IC `Missing` marker does not block writing this Hub-verified numeric zero;
the helper leaves the marker unchanged. Other IC flags still require review.
Receipt directions use IC as the roster authority: Hub-only names are stale
candidates absent from the current IC roster; IC-only names are current IC
students absent from the Hub list. These are roster differences, not missing
assignments. The helper leaves unmatched students unscored until reconciled.

This Chrome/Edge extension enters grades in Infinite Campus from the teacher's
own signed-in school browser. Since 0.3.0 its main job is the Teaching Hub
v2's IC list (below). The older Page 2 path is documented after it. The v2
list entry has one supervised live entry (September 29, the teacher's Mac).
Test each authorized browser and profile separately.

## Hub v2: enter the IC list (0.3.0, authorized September 28)

1. On the Hub, open an assignment's **Scores are ready to enter in IC** and click
   **Send to the Chrome helper**. The page sends the list shown there
   (`hub-ic-list/v1`, [entry.js](entry.js) `validateList`). It covers every
   roster student of the class with a score, a 0, Excused, "check by hand" (with
   the reason) or "nothing yet". The background keeps it in
   `chrome.storage.session` for three hours. That storage is memory only,
   cleared when the browser closes, and unreadable by web pages and content
   scripts. Sending the list again for the same assignment replaces it.
2. In Infinite Campus, open that assignment's score panel. A **Hub helper**
   button appears at the bottom right while a list is waiting. It uses the same
   panel readers as Page 2 (`HubIcAdapter.panel`). The panel must be scored out
   of 10, with one score group and one label code.
3. The review ([ic-entry.js](ic-entry.js)) picks the list whose lesson shares
   the most words with the IC title, and the column from the title: `(F)` is
   Notes and `(S)` is Revision. Both can be changed. It matches IC names
   ("Last, First") to the list one to one, allowing nicknames and accents. It
   refuses when under 60% of the IC rows match or when they span sections. It
   shows every student with IC now, the Hub value, and what it will do.
4. The teacher's rule is to **keep the higher score**, so the helper fills
   blanks and raises lower scores, and keeps IC when it is the same or higher.
   It leaves rows with an IC flag, excused students (to be marked Exempt by
   hand), "check by hand" rows and names that don't match. Hub students missing
   from the IC section are listed.
5. **Enter N scores and Save** stays disabled until the checkbox naming the IC
   assignment, the column and the Hub lesson is ticked, and it needs a trusted
   click. `execute` re-reads IC and stops if anything changed since the
   review. It types each change and compares the typed panel with the plan,
   then uses native Save, reloads the frame, and compares every row. A score
   that didn't read back is named. A Save that can't be confirmed is reported
   as uncertain, and nothing is retried automatically.
6. The result goes back to the Hub tab as counts only. When every section and
   column of the list has read back, the Hub adds one "Entered in IC" row with
   those counts to the "Scores entered" sheet.

There is no cloud lease for this path. Each run starts from a fresh read of IC
and enters only what is still missing, so repeating after a partial or
uncertain run cannot lower or duplicate a score. On Monday the Claude extension
clicks through these steps after the teacher's OK. It never types scores
itself.

**Acceptance state (September 29).** Unit and messaging tests pass
([tests/entry.test.cjs](tests/entry.test.cjs),
[tests/entry-runtime.test.cjs](tests/entry-runtime.test.cjs)). On September 28,
a dry run against a mock of the observed score panel filled two blanks, raised
one score, kept higher scores, left a flagged row, then saved, reloaded and
read back. A second review found nothing to enter, and a change made in IC
after the review stopped the run before any typing.

On September 29 the teacher ran 0.3.0 in her school Chrome profile on her Mac.
The Hub list reached the helper, "Hub helper" appeared on the real IC score
panel, and one score was entered, saved and read back after reload. So IC
accepts the helper's typing and native Save. That was a single entry.
Raises, several sections, and "Entered in IC" across a whole assignment have
not yet been seen live, so check them the first time they happen. Her school
Windows computer isn't installed; district policy may block unpacked
extensions, and IT has been asked.

**Install or update.** Package with the exact origins (see "Package and test"),
then open chrome://extensions, turn on Developer mode, click Load unpacked and
choose the package folder. After an update, click the extension's reload
arrow. Content-script changes only apply to IC and Hub tabs opened or
reloaded after that. Version 0.4.0 requests `storage` and the new `alarms` permission. Update the
Hub server and reload the extension and both tabs before using mornings.
Session storage holds student lists; trusted-context-only local storage holds
the opt-in, attempt date, uncertainty block and counts-only pending summary.
No student records or credentials are stored there.

## Opt-in weekday mornings (0.4.0)

The September 30 authorization adds one deterministic unattended path. The
popup's **Enter automatically on weekday mornings** is off by default. Enable
it only in the home Mac's school profile after the runtime bindings below have
been verified. This is a single-profile schedule; do not enable it on a second
computer. Manual v2 entry keeps its existing confirmation.

`chrome.alarms` checks once a minute against **America/New_York**, including DST.
It runs once at or after 7 a.m. on weekdays, catches up after sleep before noon,
and records a skipped attempt at noon or later. Missed weekdays are reported
on the next wake (the latest ten are retained), with no makeup score entry. Chrome must be running; alarms
do not wake the Mac. The worker recreates the alarm after a restart. A durable
attempt stamp is written before any IC operation. Worker interruption consumes
that day's attempt; it does not resume score entry.

The worker selects exactly one open Hub tab and one open IC tab. It asks the
Hub for all dated Agenda assignments, skips those with final entry already
recorded or no scores, and obtains fresh `hub-ic-list/v1` lists. Unreadable
sources, unscored decks, unsorted files and ambiguous roster rows stay held.
The run reports roster sync as unavailable before starting scores: the existing
roster endpoint is status-only, so no students are added, renamed or removed.

The worker navigates the existing IC tab to each explicitly mapped score panel
and makes it active. It never uses title matching. A bounded readiness check
stops if the panel cannot be rendered or a login page appears; it never signs
in. It verifies the resource, section, term, year, school, calendar, structure,
score group, task marker, label code and 10-point scale against the mapping.
Fresh source fingerprints and mappings must still agree before each panel.
The same keep-higher planner and save/reload executor perform entry. Changing
any IC value after the review stops before typing. A changed flag, an unsaved
reload or incomplete readback never counts as success.

A write-capable request is durably marked potentially uncertain before sending
it to IC. A lost reply, interrupted worker or uncertain save blocks **future
mornings**, not just that day. After checking and resolving the affected IC
panel, the teacher can tick the recovery acknowledgement in the popup and
add a verification note and choose **Mark verified — allow future mornings**
(0.5.0). The authenticated teacher identity, server timestamp and note are
appended to the private ledger and read back before the lock is released.
If that fails, the block remains. This does not rerun the current day. Turning
the setting off stops further fills/saves; an in-flight native Save cannot be
undone. Only the extension popup can change the setting or clear the block;
web pages cannot start an unattended run. Bounded private heartbeat messages
keep the worker available while waiting for reads/save; they grant no authority.

**Receipts.** The worker sends an “Entered in IC” note through the existing
endpoint only after every required section/column has a confirmed result.
Counts and reason counts are appended as “Morning helper” rows in the existing
private **Scores entered** sheet and read back. The Hub's Routine page shows
the latest ten summaries, including holds and failures. No student names or answers
are in that counts-only summary. Since 0.5.0, readback failures additionally
record the affected names in separate private ledger rows, never in persistent
extension storage. The Hub shows those details with the run, and displays a
locked-assignment or signed-out IC notice at the top. Earlier runs cannot
retroactively supply missing names. A signed-out/unreachable Hub leaves a counts-only receipt
pending locally for later delivery. Receipt delivery may retry; score entry
never retries automatically. The browser can only report its observations;
the server does not independently verify an IC write.

**Private binding prerequisite.** The legacy source supports explicit
`IC_RESOURCE`/score-group/term/task/code references, but v2 currently has no
verified Form-to-route mapping. The runtime must supply `HUB_IC_ASSIGNMENT_MAP`
as a JSON array, curated from those verified references and actual IC panel
URLs. Do not guess or commit live identifiers. Each entry is:

```json
{
  "class_key": "biology",
  "form_id": "FICTIONAL_FORM_1234",
  "targets": [{
    "section": "A4 Fictional", "column": "notes",
    "section_id": "42", "term_id": "5", "year": "2027", "school_id": "7",
    "calendar_id": "8", "structure_id": "9", "assignment_id": "123",
    "score_group": "3", "task_id": "1", "label_code": "F",
    "url": "https://school.example.test/campus/sis/control-center/classroom/curriculum/resource/123/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/42/objectSectionID/123/calendarID/8/structureID/9)?classroomSectionID=42&classroomTermID=5&year=2027&schoolID=7&calendarID=8&structureID=9"
  }]
}
```

Each ready Form requires exactly one target per section and score component
(`notes` / `revision`) with something to enter. Duplicate, absent or mismatched
bindings hold that assignment. The IC origin must equal the package's exact
origin. The existing IAP and same-origin protections apply to the new
`/v2/api/morning/catalog`, `/list` and `/reports` routes. No new model calls,
credentials, live connectors or external datastore are introduced.

**Build acceptance (October 1).** Unit/messaging tests and a synthetic mock
panel cover blanks, raises, higher-score preservation, flags, excused/manual
holds, sign-out, changed grades, noon/DST, duplicate alarms, worker restart,
lost responses, opt-out and receipt gating. These are not live grades. The
first enabled live morning and the teacher's subsequent IC check remain the
acceptance. The September 30 supervised run has not been independently checked
against the live ledger by this build, so the September 29 acceptance statement
above remains unchanged. The 6:30 tidy and 8:00 feedback tasks are out of scope.

## Current acceptance state — September 17, 2026 (evening)

**The Mac read/verify/skip path is live-verified with companion 0.2.3.** An
executor-completed supervised review ran end to end in the authorized school
Chrome profile on the teacher's Mac: fresh authenticated workbook read, exact
assignment/section/term/points-scale match, per-student identity verification,
shared-run ownership claim, matching-grade comparison, destination reload, and
an attributed private receipt that the dashboard read back after a full page
reload. Both selected grades already matched, so no grade was changed; that is
the verified read/verify/skip acceptance, not a write acceptance.

**A live blank-grade write with IC readback has not yet been exercised.** The
first genuinely blank reviewed grade that needs posting serves as that
acceptance; never change a correct grade to test writing.

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
  `iframe#main-workspace`. Select per-student inputs by the explicit
  `as_alignmentMultiScore_<score-group>-<row-index>` ID grammar and accessible
  score label; do not depend on a CSS class that IC may change. The bulk-fill
  box has no per-row ID and is never a grade destination; a row index is never
  a student ID. A live 0.5.1 diagnostic reported `score_input_count` on a
  mapped assignment, which prompted this class-independent selector and its
  regression test.
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
no cookies, debugger, native messaging, or broad tab access. The morning path
uses bounded persistent extension state as described above.

The Hub bridge checks its exact origin and top-level window. Runtime messages
bind the extension identity, original Hub tab, IC tab, top frame, request UUID,
private session UUID, call UUID, and lifetime. Only the IC extension context can
ask the original Hub tab for source data or lease operations. Page messages are
limited to connection status and preparing a review. They cannot supply a URL,
fetch arbitrary resources, reserve a run, or request a grade write.

Student records and claim handles stay inside the private extension channel.
Public Page 2 replies contain only fixed status codes, bounded counts, expiry,
and effect certainty. There is no second student datastore, local daemon or model call. The separately
opted-in v2 morning schedule does not activate the older Page 2 path.

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

### Retry before any assignment starts (0.4.1)

The popup offers **Retry interrupted morning** only for today's stopped attempt before any assignment was processed, while morning entry is enabled and it is 7 a.m.–noon Eastern on a weekday. The previous report must be delivered, with no outstanding report, in-flight action, pending grade review or uncertainty block. The explicit popup action starts a new attributed report and repeats all source, mapping and destination checks. Automatic attempts remain once per day. Partially processed or uncertain runs cannot use this retry.

### Run now and safe preflight recovery (0.5.9)

After a clean **missed noon** report proves no write-capable request started, after a teacher verifies the exact interrupted item and the private recovery receipt clears its hold, or after a fully **finished** pass has been delivered, the popup can offer **Run all mapped assignments now** for one fresh full-catalog check that weekday, including outside the morning window. The finished-pass recheck is separate from both the automatic weekday attempt and the missed-run/recovery action, so an already-used run-now does not hide this final verification pass. It rereads current Hub lists and exact IC destinations, fills blanks or raises lower values only, saves, reloads, and verifies each panel. Uncertain, partial, or failed save states remain blocked until the existing verification path is completed. The button is unavailable unless weekday entry is enabled, receipts are delivered, no action is in flight, and no finished-pass recheck has already been used that day. Scheduled runs remain limited to one automatic attempt per weekday.

A single additional same-day full-catalog recheck may follow a preflight failure when the receipt is delivered, the failing item has zero counts, and every earlier item is a complete `read_back` or `nothing_to_enter` result. This rechecks fresh Hub and IC state, so previously verified writes are kept instead of repeated; any uncertain, partial, or unverified save remains blocked. If the failed run has no assignment items, durable state must prove no write-capable request was sent. IC panel discovery waits up to 60 seconds for the current assignment fields to render.

### Same-day finished-run catch-up and 2026–27 exclusions (0.5.12)

The automatic catalog skips the Week 1 item when its assignment label is a raw Google Forms URL, and skips the established Forensics assignment **Inductive and Deductive Reasoning**. Both appear in the counts-only receipt as `nothing to enter` with an explicit intentional-skip label; the first is labeled `Week 1 (intentionally skipped)` so its Form URL is not copied into the receipt. Rules are bounded to school year 2026–27 and do not store a Form ID or student data. Other catalog assignments continue through the normal fresh-source, exact-mapping, keep-higher, save and readback checks.

### IC navigation readiness (0.4.2)

Wait for the exact destination URL after navigation, then for a readable IC workspace frame. A loading, inaccessible or cross-origin frame remains unavailable and cannot authorize entry; only a visible login form is labeled signed out. An explicit retry can also cover zero-count preflight failures when durable state proves no write-capable request was ever sent. Legacy state qualifies only if the old inflight property was never created. Uncertain and previously executed attempts remain excluded.
