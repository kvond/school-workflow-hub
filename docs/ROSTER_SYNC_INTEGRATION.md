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
required IC-to-Hub roster update is not implemented or live-verified yet. Keep
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
