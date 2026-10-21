# Shared School Hub run reports

Katherine wants Codex and Claude to use this format when reporting a School Hub
run she requests, or a run under an already-authorized schedule. This is her
standing authorization to post that run's operational summary in the relevant
existing `#teaching-hub` thread. It does not authorize another run, schedule,
recipient, paid call or school write. Keep engineering coordination in
`#parallel-agents` under [the existing rules](PARALLEL_AGENTS.md).

## The visible record

Use one compact summary per actual attempt. When posting a starting summary,
mark it Running and update that same message when the attempt ends. Keep the
longer discussion in replies. If the connector cannot edit the earlier summary,
post one final update in its thread linked to the starting message. A rerun has
its own attempt ID and links the prior attempt; preserve earlier outcomes.

```text
[Date/time ET] | [work bucket] | [status]
Did: [actual result in one sentence; measured counts when useful]
Results: [report/index/folder links]
Unfinished: [exact limitation and effect, or none]
Next: [one action · owner, or none]
Details: [existing thread / private receipt link]
```

Use `America/New_York` for the displayed date and time. Name the work in plain
language, such as Feedback filing, Grading or Lesson updates. Keep the purpose
and actual scope clear. Report measured counts with their units; unknown or
unread scope is unverified, not zero. State when no output was created.

Statuses:

- **Running:** execution started; no completion claim yet.
- **Completed:** the requested scope and its required verification finished.
- **Partial:** some work completed; identify the remaining scope.
- **Blocked:** a known prerequisite prevented processing.
- **Failed:** execution ended unexpectedly; identify any effects still to check.
- **Uncertain:** a write may have happened; reconcile before retrying it.
- **Cancelled:** execution stopped intentionally; say what completed first.

A queued request, saved plan, active schedule, open tab or stored receipt alone
does not establish that a destination operation completed. Preserve existing
requirements for saved/read-back grades, verified files and delivery receipts.
An otherwise successful run with unavailable Slack remains a successful run
whose Slack report was not delivered; keep those outcomes distinct.

## What Details means

Details is an ordinary link to the existing thread or private receipt, not a new
dashboard or logging service. The AI or program doing the task records the
attempt ID, trigger/start/finish, exact input/source revisions, actual model and
provider/version, prompt/rubric versions when applicable, and what verification
was performed. Mark unavailable values unknown. Record **No runtime model call**
when the task's program made none; distinguish that from an AI writing the report.

Student identities, grades, student writing, private source IDs, model payloads
and report contents stay in their existing approved private records. Slack
contains the operational summary and appropriate links. Only safe technical
details belong in a Slack reply. Reuse existing receipts and outputs rather than
creating a second grade ledger or checklist. Credentials never belong in either
the summary or its evidence.

## Standalone operations spreadsheet

The private operations spreadsheet is the wide-screen view of the system and its
run history. Keep its component overview, run rows and issues separate from the
existing Agenda, WorkIndex, grade and feedback records. Link those records rather
than copying their student data into another ledger.

For each actual run, add one attempt row with its ID, Eastern date/time, purpose,
trigger, program/model, execution environment, actual result, what completed,
remaining scope, output/evidence links, next action and owner. Update that row
when the attempt ends. Link it from the compact Slack summary when the real
spreadsheet is available. Track unresolved technical issues in its Issues tab
with evidence and a next action; do not infer that an old issue remains current.

The component overview answers: what it does, what is working and on what
evidence, where it runs, what program or model runs it, what triggers it, what
it depends on, and known issues. Keep current observations, teacher reports,
synthetic checks and proposed changes clearly distinguished. Update a component's
status only when a run supplies relevant evidence.

Use the actual existing spreadsheet link when configured; do not invent a URL
or create a second register on every run. If the spreadsheet is unavailable,
preserve the row locally and report that it was not entered into the live sheet.
A local XLSX is a prepared artifact, not a published Google Sheet.

## If Slack is unavailable

Continue independent authorized work and return the same compact summary in
the current chat, prefaced **not posted to Slack**. Preserve the summary and
appropriate links for later delivery. Explain the specific access failure;
never claim posting succeeded without a successful tool result. If posting had
an uncertain outcome, check the existing thread before retrying to avoid a
duplicate message. Do not broaden access, install another sender or change a
schedule merely to deliver the report.

This document is a reporting preference. It changes no Hub Helper behavior,
source/grade policy, model budget, delivery marks or existing run ownership.
