# Python integration — September 16, 2026

## Where the program lives

The canonical code is the private repository
[kvond/school-workflow-hub](https://github.com/kvond/school-workflow-hub).
The package is `src/school_workflow_hub`; the existing Agenda frontend is `web/`.
`school_workflow_hub.web:application` is the WSGI entry point. This release
reconciles previously local modules into that repository; it does not redeploy
Cloud Run or activate any workflow.

## Reconciled components

| Component | Python location | Relationship to the existing system |
| --- | --- | --- |
| Four-page workspace projection | `workflows/teacher_workspace.py` | Host-injected planning/read model; preserves `agenda-operations/v1` inside `agenda-workspace/v1`. Not the deployed teaching-results provider. |
| Dashboard navigation | `adapters/dashboard_navigation.py` | Bounded row-2 discovery. Reuses the read-only Sheets client; does not replace the hosted dated-lesson reader. |
| Completion | `workflows/completion_policy.py`, `workflows/due_completion.py` | Existing deterministic rules remain authoritative; workspace tests and plans use the same rules. |
| Feedback, summary and history | `workflows/student_feedback.py`, `class_summary.py`, `learning_progress.py`, `missing_work_feedback.py`, `teaching_results.py` | Existing reviewed-evidence composition and feedback-v2.1 remain authoritative. |
| Research drafts | `workflows/research_agenda.py`, `research_codebook.py` | Retains separate dimensions and approved authorial distinction. No collection, student coding or model calls enabled. |
| Private state | `core/cloud_state.py`, `adapters/firestore_state.py` | Reuses existing hosted task/result store and ownership controls; no new store. |

The original September 14 prompt text is retained for provenance and explicitly
catalogued as historical. Runtime instructions come from Agenda; the reviewed
release reference is `web/teacher-prompts.json` (September 16 feedback-v2.1).
See `AGENDA_PROMPTS.md` for coordinated prompt maintenance.

README, START_HERE, capabilities and related handoffs now distinguish historical
hosted acceptance from incomplete teaching integration. They no longer direct
the next agent to build hosting, authentication or private storage from scratch.

## Validation

The pre-reconciliation working tree passed 513 synthetic Python tests. The
integrated `python3 scripts/cloud_validation.py` run passed **522 tests** with
zero failures/errors/skips and identical synthetic replay. Nine added regression
tests cover ambiguous submissions, deadline extensions, invalid private mappings,
stale/partial evidence, destination-bound receipts and Dashboard scope handling.
Tests use fictional data and mocked transport; no student records, school-source
reads, school writes or live model calls are part of this release.

The workspace now reuses `due_completion.boundaries` for deadline arithmetic;
verified individual extensions override assignment cutoffs. Multiple competing
submission documents and contradictory turn-in observations require review.
Partial history cannot claim missing work, and current feed timestamps cannot
relabel old row evidence. Verified missing submissions require missing-work
feedback under v2.1. Delivery proposal identity includes the roster/IC mappings,
so changing a destination invalidates an old roster receipt. Missing or
out-of-scope Dashboard grid evidence remains blocked rather than empty success.

The same 522-test validation and capability inventory also passed from a clean
export of the staged repository, without local untracked dependencies. Node's
existing workspace contract accepted both checked-in fixtures and newly generated
Python synthetic output; the preview's missing-work feedback state was reconciled.

The unchanged package metadata built `school_workflow_hub-0.1.0-py3-none-any.whl`
with 72 Python files in a modern isolated Python 3.9.6 build environment. Installed
into a second clean environment, Python `-I` imported all reconciled modules from
site-packages and composed the workspace with collection/coding/writes disabled;
CLI `--help` passed. The older Apple system pip produced an invalid empty UNKNOWN
wheel, so README documents the verified isolated build procedure. No global
packages changed. The full checkout remains necessary for app assets, configuration
and repository scripts; the library wheel is not a standalone hosted deployment.

## Remaining connections

1. **Actual inputs:** complete IC due-assignment/current-roster readers, verified
   active assignment folders, full cumulative submission history, exact revisions,
   stable identities and individual extensions. A 14-day change window does not
   prove missing work.
2. **Reviewed evidence:** real marker inventory and writing assessments under the
   current prompts. Deterministic completion is separate from conceptual judgment.
3. **Private results:** bind teaching results and history to existing Firestore
   ownership/receipts and inject the existing `hub-teaching-results/v1` provider.
   Verify matching results on Page 2 and Page 3. Verify backup/restore before
   relying on stored real student history.
4. **Agenda saves:** authoritative weekly/list plan and note writes, conflict
   rejection, exact readback and reload persistence.
5. **Destinations:** IC grade posting, feedback filing, email and teacher-directed
   printing each need their own scoped executor, duplicate prevention and readback.
6. **Activation:** one supervised end-to-end lesson and recovery checks before
   unattended execution. Existing schedules and cloud settings are unchanged.
7. **Journal/research:** approved journal append/readback and later authorized
   research evidence. Research remains deferred until teaching workflows pass.

September 15 hosted acceptance is documented in `CLOUD_READINESS.md` and
`PRIVATE_CLOUD_STATE.md`: Agenda/authentication and fictional persistence/claim
checks passed. This code reconciliation does not constitute a fresh production
health check or live school-workflow acceptance.
