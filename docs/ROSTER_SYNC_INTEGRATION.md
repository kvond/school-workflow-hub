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
