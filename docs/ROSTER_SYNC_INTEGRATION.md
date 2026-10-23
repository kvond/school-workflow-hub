# Roster sync integration

The portable roster-sync status endpoint is `GET /api/roster-sync`. It is
protected by the same signed IAP identity check as the Agenda endpoints and
returns no student, credential, Infinite Campus, or workbook identifiers.

It reads one deterministic shared task identity:

```text
workflow=weekly-roster-sync; class_key=all-classes; assignment_key=2026-2027
```

That task record is the single status every client reads, so the dashboard and
any other reader see the same state and none of them can invent a separate run
history. The existing Firestore fenced lease will allow one worker to claim the
task at a time; a retry with the same request identity retrieves the same result
instead of starting a second run.

## Who may execute

Reading the status is the only thing an assistant session does here. The
executor is the deterministic Python worker running under the school service
identity. Three rules follow from that, and they hold for every workflow in this
repository, not only roster sync:

- No scheduled or recurring run may depend on an assistant session being open,
  signed in, or reachable. A run that cannot complete unattended is not ready to
  be scheduled. This restates the standing boundary in `AGENTS.md`.
- No assistant account is a credential path to a school system. Credentials
  belong to the service identity in the approved private runtime.
- An assistant may read the shared status and may prepare work for review, but
  it never claims the task, writes a destination, or records a receipt.

## Required roster behavior

Infinite Campus is the canonical, changing class roster. A successful complete
IC read must update the Hub's active roster view: newly enrolled students are
added, and students no longer on the IC roster are removed from active roster
updates. Do not present ordinary additions or departures as student-work
exceptions. Keep historical submissions, grade receipts, and feedback records
for departed students; removing a student from the active roster must not erase
that history. An incomplete or inaccessible IC scan must not be treated as an
empty roster or as mass departures: leave the last complete roster intact and
report one sync failure with the exact class/source that could not be read.

The roster workbook is a downstream mirror for existing readers, not the
authority for current membership. After any workbook update, read it back and
verify the current active names against the IC snapshot.

### Study Hall is roster-only

The roster update scope includes Study Hall as well as the four instructional
sections. Configure `HUB_ROSTER_ONLY_SECTIONS` separately from the instructional
`HUB_ROSTER_TABS` and `HUB_ROSTER_SECTION_IDS` mappings. Each entry supplies its
teacher-facing section name and the exact private `class_key` (`study_hall`),
`section_id`, stable `tab_id`, and `school_year`. Bind those values to a complete
current IC roster and the existing native workbook tab; do not guess from a
Teaching Center attendance count or from a tab title. IDs and names belong only
in private runtime configuration and receipts.

`adapters.roster_only.plan_mirror` accepts that complete, matching section/year
observation only within one hour of its actual capture. Observed counts must
match all names; ambiguous names, incomplete scans and stale input hold the
update. The mirror's five owned columns are Full name, Roster status, Start
date, End date, and IC checked at. Active, incoming, and dropped rows retain
their explicit IC statuses; older names absent from a complete scan remain
inactive history. An incoming learner is never active before IC says so.
`verify_mirror` requires the exact native post-write readback. An authorized
worker must bind the original workbook baseline before applying this plan and
preserve all other tabs, teacher columns, formulas and attendance records.

`active_rows` reads only active mirror rows. Study Hall remains excluded from
the submission and grade-matching readers, which still read only the four
instructional sections. A Study Hall name is display evidence, not a school
email, student ID, grade identity, or membership in another course. The
authenticated status exposes only the scope count; configuring this mirror
does not enable or certify an unattended IC reader.

## Current state

`/api/roster-sync` is status-only. It returns `run_enabled: false` and does
not create a task, read Infinite Campus, or write the roster workbook. Thus the
live IC-to-Hub roster update is not accepted or activated yet. The trusted-host
worker below now implements the guarded synchronization/mirror half; the actual
IC transport and initial native-ID crosswalk remain unverified. Keep
roster churn out of student-work exception lists; show the sync connection gap
once as a system status until these adapters pass acceptance:

1. a complete-current Infinite Campus roster reader with stable private
   identity mapping; and
2. a narrowly scoped roster-workbook writer with a post-write readback.

The executor must create and claim the task only after both checks succeed,
record a source fingerprint/baseline and destination readback receipt, and
release the claim to `Needs you` on any partial scan. It must preserve grades,
attendance, and non-roster columns. No scheduled job should be enabled until a
supervised run produces that evidence.

## Trusted-host integration (October 22 implementation)

`adapters.roster_worker` now supplies the deterministic integration and a
concrete `SheetsRosterMirror` using the existing Google backend. This is **code,
not live reader acceptance or activation**. The status endpoint, schedules,
Chrome helper and `run_enabled: false` remain unchanged. No HTTP run route was
added. The earlier fixture-only reconciliation planner remains fixture-only.

The trusted host supplies three independently verified capabilities:

1. An IC reader implementing `read_complete(targets)`, under its existing school
   identity. There is **no configured IC transport in this repository**:
   `InfiniteCampusBrowserAdapter` is a disabled stub and a browser-run report
   does not prove a complete roster. An accepted reader must produce native
   student and enrollment IDs, explicit section/year, original observation time,
   source revision, expected row count, complete unfiltered coverage and no
   remaining pagination. All explicit active, incoming and withdrawn memberships
   are included. Names-only UI observations cannot satisfy this contract. No new
   login, credential copying, guessed IC endpoint or extension change is provided.
2. Exact existing workbook/tab/owned-column bindings and the initial native-ID
   crosswalk. `SheetsRosterMirror` verifies instructional target IDs against
   `HUB_ROSTER_TABS` and `HUB_ROSTER_SECTION_IDS`; its status column must match
   `HUB_ROSTER_STATUS_COLUMNS`. Study Hall uses its separately configured
   `HUB_ROSTER_ONLY_SECTIONS` section/year/tab binding and never enters academic
   readers. These are private runtime values, not repository examples to copy.
3. The shared source-owner guard's required `before_write(required_seconds=...)` callable. The host
   uses the same `SourceOperations` scope as all other preparation writers.
   The callback freshly verifies the safe time window, helper inputs and actual
   ownership. A no-op callback is suitable only for synthetic tests, not live
   acceptance. This worker does not acquire or invent external-session evidence.

Use `RosterWorker(port, reader, mirror, targets, before_write=guard.before_write)`
from the trusted service host, then `run(owner=..., request_id=...)`. No optional
flag or browser request turns a synthetic reader into an accepted integration.
Keep real acceptance separate: the coordinator must record the actual complete
IC capture, exact stable-ID crosswalk, ownership evidence, native mirror
readback, and existing Hub-reader result before enabling or claiming execution.

The capture's top-level fields are `source` (`infinite_campus`), `reader_id`,
`complete`, `errors` (empty), and `sections`. Each section contains `section_id`,
`school_year`, `complete`, `filtered` (false), `next_page` (null), `revision`,
`checked_at`, `expected_count`, and `rows`. Each row contains `student_id`,
`membership_id`, `first`, `last`, `status`, `start_date`, and `end_date`.
Dates are ISO calendar dates, an explicitly observed empty string, or null when
unavailable. Unavailable dates preserve the same enrollment's prior verified
value; a new enrollment never borrows its predecessor's dates. Both source
bookends must be at most 15 minutes old and agree on scope, revision and rows.
Different sections may contain the same student ID with distinct native
memberships; conflicting identities or reused enrollment IDs fail closed.

Instructional mirrors require explicit eight-column ownership with existing headers:
Last Name, First Name, Roster status, IC student ID, IC enrollment ID, Start date,
End date, and IC checked at. Instructional names stay in A:B, in either header
order. Status may be in C:Y. Study Hall preserves its existing A:E Full name/status/date/
checked-at layout and adds separately verified first/last/native-ID columns;
its existing roster-only reader can still consume the unchanged A:E schema.
The configured column bound must cover the full
native tab width, up to 26 columns; unsupported wider tabs hold rather than
pretending unobserved teacher columns are empty. The bounded native read checks
all rows of a tab (at most 2,000) and refuses named/ID/status data outside the
existing readers' 200-row support. A larger mirror needs a separately reviewed
reader expansion, not truncation. A new student occupies a completely empty
physical row; existing students never move. Rows with teacher fields or formulas
are not reused. Owned formulas, missing native IDs and undeclared columns hold
before a write. No headers, tabs or ID crosswalks are created automatically.

Departures change their owned status to `inactive`; original names, IDs, dates
and teacher fields remain in the same row. Explicit IC withdrawal stays
`withdrawn` for instructional rows and uses the existing `dropped` label for
Study Hall; incoming stays `incoming`. Rejoining retains the row and records
both old and new native enrollment identities in immutable private before/after
receipts. The two existing instructional readers include only `active` rows
when their exact status column is configured and its header/statuses verify.
Unconfigured legacy rosters retain their existing parsing behavior. A blank or
unknown configured status is an availability failure, not active membership.

The existing roster `CloudState` task fences the run. The worker checks source
bookends, the mirror baseline and shared ownership before persisting one
private write intent. The concrete mirror rechecks the baseline and writes only
individual declared `userEnteredValue` cells in a single native batch. It never
sorts, deletes rows, replaces tabs, changes formats or touches unowned formulas.
The concrete writer obtains tokens and its final baseline before reserving a
170-second native write/readback budget plus 30 seconds for a final roster-owner
read through the shared guard. The roster lease is renewed before the potentially
slow shared activity check and then independently rechecked after it. The final
read must prove the same owner/fence and at least 170 seconds remaining after
conservatively subtracting its measured response elapsed time. If that check
takes more than its reserved 30 seconds, no native write starts; it cannot spend
the shared cutoff/native-write reservation. The reservation covers one bounded
native POST plus the two bounded readback GETs and their permitted read retries;
readback reuses its in-memory token rather than refreshing credentials inside
that window. The guard refuses the operation if that whole budget cannot finish
before the helper cutoff. Google Sheets has no native compare-and-swap for cells: the fresh baseline plus
shared lease is not a provider-enforced transaction with Firestore. Independent
human edits are detected by the complete post-write grid readback; conflicting
or uncertain results stop further writes.

A pending intent blocks later runs, even after process death or lease expiry.
`reconcile()` is read-only at the school destination: it never resends the batch,
clears uncertainty or takes a new claim. It accepts only the entire expected
mirror readback and the original still-valid claim (or already persisted receipt).
If ownership expired, or a cell differs, the coordinator must inspect the exact
scope through the existing recovery procedure. Partial/stale/unavailable source
reads do not advance the complete roster or invent missing work. Receipt storage
is verified independently of the native write response.

Tests use synthetic IC captures and mock the Google transport. They establish
these contracts, including lost-response recovery, without claiming a live
reader, changed workbook, updated roster, or grade entry.

### Next live acceptance step — Codex owns this engineering work

When a coordinated, signed-in school IC session is available for native access,
the Codex coordinator inspects the supported roster/export capability for the
four exact instructional sections and verified Study Hall. Establish observed
native student/enrollment IDs, complete status/count coverage and the initial
stable-ID-to-existing-row crosswalk; then implement and independently verify
that capture transport through the existing trusted-host boundary. Preserve
all named-only history while its crosswalk remains unverified. This is unfinished
engineering, not a request for Katherine to invent IDs, supply an adapter or
repeat known identity decisions. No screen control, browser export, real roster
capture, workbook setup or live acceptance occurred in this background repair.

### Native Roster Report input

Infinite Campus documents a supported teacher export at **Instruction > Reports
> Roster**, also reached from **Students > Roster > Print Options**. Select the
exact term and sections, today's effective date, **Local Number**, **Show Incoming
Students**, **Show Dropped Students**, and **Show Section Placement**; the arrow
next to Generate offers CSV. Do not include unrelated demographic, guardian,
health or program fields. The read-only Student Roster independently shows the
active, incoming and dropped groups and their counts, enrollment start/end dates,
and student contact details when authorized.

These are supported source surfaces, not evidence that an export was actually
captured or that an unattended connector exists. Preserve the original CSV and
its byte hash privately, together with the observed school identity, school,
calendar/year, term/date range, effective date, exact selected sections/options
and independent status counts. A second complete capture must agree before an
import can use it. File modification time, a row count computed from the same
CSV, or a report filename cannot independently prove current complete coverage.

The report's Local Student Number is not IC's person ID, and it is not an
enrollment ID. IC explicitly documents that OneRoster enrollment `sourcedId`
is not visible in the Campus UI. The separate `adapters.roster_report` input
names its derived student/section/year relationship key explicitly rather than
placing a hash under an **IC enrollment ID** header. Native-ID targets retain
their existing schema; report targets explicitly set
`identity_scheme: ic_roster_report_v1` and require the distinct owned headers
**IC local student number** and **IC membership key**. Switching schemes never
relabels existing IDs. A report relationship key does not claim to identify a
hidden native enrollment episode; every observed status/start/end value remains
in the immutable source and mirror receipts, including reentry observations.
The official OneRoster documentation page does not establish an authorized API
connection, permission to acquire credentials, or a reason to query private IC
endpoints.

An initial mapping to existing mirror rows needs independently verified identity
evidence. Do not infer a school email from a name or assign a native ID to an
existing row merely because names match. A preserved names-only legacy row is
not proof of current membership. Any separate initialization design must retain
its teacher/formula cells and history and verify the exact native destination.

`parse_csv(bytes)` accepts the observed official 15-column header, optionally
one strictly empty trailing column, numeric Local Student Numbers as text
(retaining leading zeros), full first/middle/last/suffix and recorded aliases,
and original start/end dates. It refuses changed headers, extra populated
columns, malformed dates, duplicate identities or conflicting identities across
sections. The observed native Gender cell may be blank, M or F; its original
bytes remain in the private capture hash but the value is discarded from every
canonical row and never used for identity, status or membership. Other values
remain an unsupported-format hold. A successful parse proves file format only. The CSV does **not** have
an explicit roster-status column: blank End Date cannot prove active membership,
and dates never substitute for native Active/Incoming/Dropped groups.

`prepare_capture(raw, observation, expected_context=..., bindings=...,
targets=..., now=...)` joins those exact CSV rows to independent native status
groups by Local Student Number. The trusted caller supplies current account,
origin, school/calendar/structure/year, term and actual term dates, effective
date, exact report options, original generation/read times, byte hash and unique
capture identity. Private `bindings` map each already verified native section
ID to the exact CSV State Code/Course/Room/Term(s)/Period tuple. Each separately
observed native section must give complete unfiltered/unpaginated status groups
and independent active/incoming/withdrawn counts; their exact IDs and counts
must agree with the CSV. An empty section requires independent native zero
coverage. The native Roster screen or a verified same-context grouped PDF may
supply that evidence; a count computed from the CSV cannot.

`NativeRosterReportReader(capture, expected_context=..., bindings=...)` adapts
an accepted host capture callback to the existing worker. Every callback must
obtain original native source evidence; no browser transport, file watcher,
login or persistent-file fallback is configured by this module. A supervised
capture capability does not establish unattended execution. Captures must be
at most 15 minutes old on the current Eastern effective date, within the exact
term. The worker requires a genuinely separate second generation after the
first native observation, with a distinct capture identity, and matching full
CSV/context/status/binding proof. Re-reading one downloaded artifact cannot
satisfy bookends. Both captures are retained in the immutable private receipt.
Unknown dates are not carried forward by this path: each CSV date is actually
observed, including explicit blank values. All existing source-owner/cutoff,
native-write budget, intent/uncertainty and complete readback gates still apply.

Native format observation and synthetic tests do not establish an accepted
current roster, an initialized mirror, a configured capture transport or a
changed workbook. `run_enabled` and the helper remain unchanged. Codex owns the
remaining live capture, exact private binding/crosswalk, reviewed initialization
and source/destination acceptance; Katherine need not supply engineering.

Official references: [Roster Report](https://kb.infinitecampus.com/help/printing-a-sections-roster),
[Viewing Your Roster](https://kb.infinitecampus.com/help/viewing-your-roster), and
[OneRoster API Documentation](https://kb.infinitecampus.com/help/oneroster-api-documentation-oauth-2).

### Post-run maintenance activity evidence

`hub_v2.source_activity.PostRunSourceActivity` supplies the **post-run maintenance**
`SourceOperations.check_activity` callback. It does not replace the normal 6:30
preparation reader: it requires an observed current-day settled helper attempt
at or after 7:00 Eastern. It grants no maintenance window by itself. Continue
using the separately verified maintenance authorization, shared source scope,
full native write/readback budgets and final owner/fence/time checks. Pass that
same detached `maintenance_authorization` envelope into the activity adapter;
its `authorization_revision` must equal the I-12 cell hash. The I-12 authority
reference/start/expiration must match the grant exactly, so a source reservation
cannot outlive the participant deferrals or the authority record.

The coordinator supplies these private readers; none may echo fixture data or
refresh a timestamp without a new native observation:

- `read_record()`: `read_i12_cell(token, spreadsheet_id=..., tab=..., row=...,
  id_column=1, note_column=6)` reads the exact existing I-12 row and its cell
  note through the normal Google Sheets reader. The note is JSON containing
  `source_activity`; sibling properties remain untouched. The hash of the
  exact raw cell value, note and row ID is the `expected_revision`. There is
  no note writer here. Preserve any teacher text before an authorized note
  update; a changed note/value requires fresh review and a new pinned revision.
- `port`: the existing authorized state port, with `known_claims` mapping exactly
  `roster`, `grading`, `archive_biology`, `archive_forensics`, `archive_anatomy`
  to their established private task IDs. Both reads are bounded batches, using
  server time. Do not discover or create replacement tasks. Missing/partial
  claims hold. Expired claims remain historical and unmodified. A live claim
  blocks unless `owned_claims()` supplies this coordinator's exact actual
  `Claim` object from its own acquisition, its owner matches that role in the
  pinned I-12 `claim_owners`, and its run record verifies task, owner, fence and
  input hash. Never reconstruct this callback from foreign live records. A
  matching owner string alone cannot establish ownership.
- `read_helper()`: read the coordinator's fresh private native-popup observation
  (maximum age 60 seconds), with the exact school account and configured
  source/destination origins. It may read an evidence file from the immediately
  preceding native observation; reading an old file is not a new observation.
  The unchanged popup's visible, enabled **Run all mapped assignments now**
  control carries its existing no-busy/no-block/inflight/pending/outbox gate.
  The separately reviewed terminal source-gap observation below can establish
  source idleness when that replay control is absent; it never enables replay.
  Root is the sole browser operator; no extension storage, hidden transport,
  package changes or toggles are needed or authorized by this adapter.
- `read_receipts()`: `read_morning_receipts(token, config)` reads the existing
  full bounded native ledger and projects every exact run, rejecting truncated,
  malformed and conflicting history. The ten-run dashboard projection is not
  complete receipt evidence. There must be exactly one current-day terminal
  receipt matching the popup's displayed date/outcome, and no current-day
  running or uncertain competing receipt. Supported settled outcomes are
  `finished`, `source_scores_held`, an empty `missed_noon`, and the strictly
  validated `coverage_incomplete` terminal batch below. Other outcomes remain
  held pending a separately reviewed exact disposition.

The private I-12 `source_activity` object has this contract (all actual account,
references, IDs, authorization contents and evidence remain outside Git):

```text
schema: source-activity/v1
record_id: I-12
source_scope: the one existing shared preparation scope
owner / sole_live_executor: exact coordinator owner ID
claim_owners: exact five known role keys, each with its permitted domain owner
authorization_ref: current teacher decision reference
authorized_from / authorized_until: explicit offset timestamps
participants: exact expected participant-ID map, each containing:
  mode: code_read_only | deferred | consulting_only
  evidence_ref: existing explicit disposition evidence
  valid_until: at least the authorization expiration
```

The pinned expected participant map includes exactly three code/read-only agents,
feedback and secondary workers' explicit deferrals, and any actually known
consulting-only participant. Offline processes do not invent extra participants.
A withdrawn, missing, changed or too-short disposition holds.

The private native helper observation contract is:

```text
schema: native-helper-observation/v1
observed_at: actual native observation timestamp
observer: the coordinator owner ID
evidence_ref: the existing private native observation reference
account / source_origin / destination_origin: exact freshly verified values
busy: false; blocked: false; ready: true
run_now_visible: true; run_now_enabled: true
last_attempt_day / last_attempt_code: literal displayed attempt date/outcome
```

For a finalized `coverage_incomplete` batch with replay controls absent, retain
`run_now_visible: false` and `run_now_enabled: false`; never invent availability.
The same fresh observation must additionally contain `recovery_visible: false`,
the actual native `morning_text`, and the **full** actual native `next_text`.
The adapter compares both complete texts against the current-day outcome and
all held items in the exact ledger receipt. Top-level **Ready** alone proves
only manual-list state. Active-run text, a recovery pane, partial/truncated text,
an old day or any mismatch holds. Do not generate the observation from the ledger.

This batch must be nonempty with the first non-completed item explicitly
`coverage_incomplete`, matching the unchanged helper's aggregation. Source,
mapping, no-score and excluded items require zero counters and reasons.
`nothing_to_enter` permits retained and left counts but no filled/raised score;
only a `read_back` item may contain filled/raised counts. Comparison reason
totals must match left counts. In-progress, uncertain, contradictory or partial
outcomes still hold. A completed scheduled run and the sole coordinator's
explicit maintenance ownership do not authorize another run or permit a hidden
automatic retry. Helper code, settings and recovery state remain unchanged.

Record, claim and full receipt bookends must agree within 60 seconds; availability
failures hold with no retry or mutation. `last_evidence` identifies the exact Hub
receipt using `correlation_basis: unique_day_and_outcome`. The native popup does
not display a run ID: do not claim one was observed there. This is source-writer
idleness evidence, **not grade verification**. Multiple matching runs remain
ambiguous. All native grade counts still require their own exact saved-cell
readback. `source_idle_correlation` records the same honest correlation, and
`reentry_available` records only the observed control availability; false is not
changed to true to permit source maintenance. A failed check stops further source writes and preserves helper
recovery, historical claims, receipts and student records.

Tests use synthetic records and the real maintenance/shared-lease guard. They
verify duplicate-writer exclusion, delayed ownership loss, zero native writes
on holds, complete receipt reads and native Sheet note parsing. They do not
establish current live idleness, source repairs or roster completion.

### One-time initialization without a names-only crosswalk

The trusted-host `adapters.roster_bootstrap.BootstrapWorker` can initialize all
four instructional sections and the one verified Study Hall in **new canonical
current tabs inside the same existing Rosters workbook**. It does not create a
workbook, match an old row to a native ID, invent an email, migrate student
history, rename/delete a tab, mark a legacy learner inactive, or activate any
configuration. Original tabs, cells, formulas, notes, formatting and history
remain in place. The immutable receipt labels their scope `legacy_unlinked`;
that label is not written into any old student's cells or treated as a departure.

The coordinator first supplies the exact five new stable tab IDs/titles, verified
existing section/year mappings, current private configuration reader, accepted
`NativeRosterReportReader`, existing cloud state port with `commit_guarded`,
and actual shared guard. New tab IDs/titles must be absent from a complete fresh
workbook read; there is no collision fallback or guessed destination replacement.
Both native captures must independently cover all five sections, be no older
than fifteen minutes, carry distinct original capture IDs/times, and agree in
all canonical content, context, membership/status and provenance. Study Hall
incoming/withdrawn membership stays separate from instructional eligibility.

`worker.prepare()` is read-only. It returns `plan` and `sha256`, binding both
native captures, the current configuration-map basis, exact original workbook
snapshot, new tab IDs/titles/owned cells and proposed existing-map updates.
The concrete `SheetsBootstrap` reader preserves original user-entered values,
formulas, notes, text styling, validation and sheet metadata; unavailable,
oversized, partial, changed or malformed scope holds. There is no partial-class
initialization or activation when one section is unavailable.
Preparation also checks the projected workbook with all five new grids against
the same readback tab/cell/serialization bounds. Before the pending intent is
committed, the full eventual immutable record wrapper and verified intent/result
envelopes must fit the existing private-state limit. These checks never truncate
original preservation evidence or enlarge storage limits after a native write.

`worker.apply(proposal, expected_sha256=..., owner=..., request_id=...)` freshly
validates the proposal, source freshness, original workbook and configuration.
It acquires the existing roster task's fence, then atomically writes the full
private pending intent and a **pending bootstrap marker in the existing
roster-intent key**. The ordinary roster worker therefore refuses an uncertain
bootstrap even after its lease expires. No separate job or schedule is created.
One native Sheets batch adds all five tabs and their initial user-entered cells:

- Instructional: A First Name, B Last Name, C Roster status, D IC local student
  number, E IC membership key, F Start date, G End date, H IC checked at.
- Study Hall: existing A:E display contract, F IC local student number,
  G IC membership key, H First Name, I Last Name.

Each tab has 200 rows and exactly eight or nine columns. Canonical identity is
the explicitly named local-number/report-membership scheme. The existing reader
uses an explicit native Start date for membership eligibility when there is no
teacher Added/Joined column; existing Added/Joined data retains precedence.
Names-only legacy history is not a canonical current member list or an email
identity source.

The native operation reserves the established 170-second one-POST/two-GET
readback budget plus the bounded final roster-ownership check. Native tokens
are acquired before that reservation. Each private state mutation uses the
existing guarded CAS adapter; failed ownership/activity/time checks dispatch no
native write. Exact readback must show every new canonical cell and all original
tabs/cells unchanged before the immutable receipt and verified uncertainty marker
are published. A lost write response or differing readback leaves the pending
marker intact. `worker.reconcile()` only reads that exact destination and verifies
its existing intent/claim; it cannot create tabs, resend the batch, acquire a
replacement owner or clear an expired/foreign uncertainty hold.

A verified result says `verified_not_activated`, gives counts separately from
its immutable receipt, and returns `targets` plus `proposed_config_updates`.
Only then may the coordinator update the existing `HUB_ROSTER_TABS`,
`HUB_ROSTER_STATUS_COLUMNS`, `HUB_ROSTER_COLUMN_LAYOUTS` and
`HUB_ROSTER_ONLY_SECTIONS` in **one verified runtime revision**, preserving
section IDs, helper mappings, unrelated configuration and the old map until
complete success. The module does not perform that cutover. Fresh deployed
readback must prove all instructional active counts/status/date filtering and
Study Hall isolation before calling roster initialization active.

The private receipt's `roster_bootstrap` payload contains `plan.source_before`,
`plan.source_after`, original snapshots, exact canonical rows and a destination
hash. Receiving this receipt is not feedback/grade eligibility or email proof.
An importer requiring a native email-to-local-number crosswalk must independently
verify that exact correspondence and the actually active runtime mapping; it
cannot infer it from the new tab, old name, or a pending initialization receipt.
