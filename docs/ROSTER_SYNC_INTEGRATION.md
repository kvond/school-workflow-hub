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
