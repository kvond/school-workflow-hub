# Shared entry point for the School Workflow Hub

## Product direction

The user is making presentation/source corrections while the existing partly
built workflows are turned into reusable Python. The destination is cloud
execution, with this private GitHub repository as the shared code, interfaces,
tests and handoff location. Any agent authorized to read this repository should
be able to continue from here. Private repository access must be granted through
the owner's normal account controls; this does not make school data public.

Routine jobs must call deterministic Python functions. AI agents help build,
review and handle exceptions; they should not repeatedly reconstruct the same
logic from chat history. Keep prompts replaceable and never invent grades or
interpret missing observations as verified absence.

## Read these first

1. `AGENTS.md` — project boundaries and ownership rules.
2. `config/capabilities.json` — completed code, evidence paths and remaining work.
3. `docs/AGENDA_READER.md` — first read-only API connection and exact limitations.
4. `docs/TOMORROW_TESTS.md` — existing offline test/review packet.

Get the same inventory without relying on a particular AI tool:

```sh
python3 scripts/hub_status.py --json
python3 scripts/run_all_tests.py
python3 scripts/cloud_validation.py
```

These commands need Python's standard library only. Status inspection reads the
repository; tests use fictional data and mocked transport. `cloud_validation`
locally exercises the same entry point as the GitHub-hosted workflow and checks
two identical previews. It does not dispatch a cloud run itself.

## This continuation

**Latest workflow work:** read `WORKFLOW_INPUTS_20260914.md`. The application
now includes explicit Agenda mapping/checkpoint composition, read-only Todoist
notes and routing, submission-page collection and printable report output.
The latest local suite is 322 tests. Cloud deployment/hosting/activation is
owned by the other chat; do not duplicate or alter that work.

- Reused the existing contracts, SQLite ledgers, fixture workflows, private
  assignment comparator and guarded platform interfaces.
- Brought the reviewed GitHub export up to the later 252-test teaching checkpoint:
  roster status/history safeguards, unknown/colliding target holds, later
  completion evidence and unfinished-grading reporting.
- Added `adapters/google_sheets.py`, `scripts/read_agenda.py`, capability JSON,
  status CLI and focused regression tests. No second framework or database.
- The Sheets reader uses a supplied authorized token and a single explicit
  source. It has GET only, bounded ranges/response size/retries, rejects
  redirects and prints only opaque fingerprints through its CLI.
- Added comparison semantics for unchanged, changed, missing-baseline and
  incompatible-scope observations. An initial read is not proof of a change.

## Next implementation sequence

1. Select a school-authorized runtime identity and credential refresh mechanism
   for the read-only source; provide no tokens in chat, source or task comments.
2. Establish the live stable lesson/link baseline: Agenda -> parent assignment
   lists/messages -> weekly assignment pages -> Schoology, one week at a time.
   Keep date changes separate from lesson identity; preserve copy/edit mode.
3. Map the actual Agenda layout and source ranges to normalized observations.
   The explicit `AgendaLayout` normalizer and `observe_agenda` composition are
   now implemented; select/validate their real configuration rather than
   rebuilding them.
   Do not infer stable assignment IDs from titles or row numbers. Live range
   reads alone do not satisfy this mapping step.
4. Select approved persistent private state with retention/access rules. Store
   baselines atomically only after a complete successful observation. Keep
   student data and raw school sources out of GitHub artifacts/logs/repository.
   `core.observation_baseline` now provides atomic baseline storage in the
   existing ledger, including stale-read and concurrent-checkpoint refusal.
5. Validate one real read/compare and error/recovery path before recurring use.
6. Add the real submission reader, current rubric and private feedback/report
   acceptance. Then implement separately authorized delivery/update adapters.
7. Research collection follows teaching-system verification. The existing
   synthetic category signals are not an open inductive codebook.

## Cloud verification

Deployment and activation belong to the separate cloud chat. This application
handoff requires no cloud-usage response in the workflow-building chat.

The manual GitHub workflow is present and disabled by default. Its first
activation requires included-usage/spending confirmation. It runs standard
Ubuntu, read-only repository permissions, one job at a time and a ten-minute
timeout; it does not need school credentials or upload generated records.

Record the actual commit, hosted run URL and successful job conclusion before
calling cloud execution verified. A skipped job is not a pass. A passing fixture
job does not validate the real source connector or a school operation.

Do not silently add recurring cron, new paid services, a public endpoint,
browser profiles, school credentials or a live grade writer to this stage.

## Handoff discipline

For each continuation record code changes, commands/results, exact commit,
observed cloud state and next blockers. Update the capability inventory only
when evidence supports it. A build can be complete while its live acceptance
task remains open. Preserve presentation/source corrections and existing grade
history. Avoid copying older local handoffs over this shared entry point.

## HTML owner handoff

Read `docs/HTML_AGENDA_INTEGRATION.md` for the implemented
`agenda-operations/v1` status contract. Eight focused contract tests cover
proof requirements, identity and synchronization display gates. Use the existing
Agenda page. HTTP transport, live receipts and edit saving remain unconnected.

The HTML owner's September 14 requirements are reconciled in
`docs/FRONTEND_REQUIREMENTS_ALIGNMENT.md`: authenticated HTTPS, portable
versioned instructions, task versus operation identity, record revisions,
weekly/list save conflicts and shared run claims. That document distinguishes
the implemented projection from requested services that remain absent.
