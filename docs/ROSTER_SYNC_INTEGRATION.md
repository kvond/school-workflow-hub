# Roster sync integration

The portable roster-sync status endpoint is `GET /api/roster-sync`. It is
protected by the same signed IAP identity check as the Agenda endpoints and
returns no student, credential, Infinite Campus, or workbook identifiers.

It reads one deterministic shared task identity:

```text
workflow=weekly-roster-sync; class_key=all-classes; assignment_key=2026-2027
```

That task will be the canonical coordination point for the dashboard, school
Claude, and ChatGPT. The existing Firestore fenced lease will allow one worker
to claim it at a time; a retry with the same request identity retrieves the
same result instead of starting a second run.

## Current state

`/api/roster-sync` is status-only. It returns `run_enabled: false` and does
not create a task, read Infinite Campus, or write the roster workbook. This is
intentional until the two adapters below have passed a supervised acceptance:

1. a complete-current Infinite Campus roster reader with stable private
   identity mapping; and
2. a narrowly scoped roster-workbook writer with a post-write readback.

The executor must create and claim the task only after both checks succeed,
record a source fingerprint/baseline and destination readback receipt, and
release the claim to `Needs you` on any partial scan. It must preserve grades,
attendance, and non-roster columns. No scheduled job should be enabled until a
supervised run produces that evidence.
