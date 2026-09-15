# Due assignment completion workflow — v1

Implemented locally on September 15, 2026 in the existing Python backend.
Entry point: `workflows.due_completion.check_due_assignments`.
This composes source adapters and the existing `completion_policy` assessor
contract. It does not yet have authenticated live source adapters or a hosted
execution endpoint. It performs no grade writes, emails, printing or GB updates.

## Processing sequence

1. Read the complete current Agenda assignment inventory and IC curriculum.
   Use stable assignment IDs and verified due dates; never use a visibility End
   Date as an inferred due date. Report Agenda/IC differences as Needs you.
   Include all unfinished past-due assignments, even beyond the 14-day window.
2. Resolve each assignment's teacher Slides, assignment document, turn-in folder,
   initial/final assessment stage, marker inventory, and exact IC score targets.
   A folder may serve several lessons: its name alone cannot identify a submission.
   Do not guess that an IC `(F)` suffix means a particular score category.
3. Read the current IC **assigned** student roster. Preserve grade history and
   apply approved individual due-date extensions. Attendance/GB sheets are not
   needed for this process and must not be edited.
4. Follow every folder page and reconcile all cumulative turn-in history. Match
   student and assignment by verified stable identities. Keep early submissions;
   a last-14-days modified-file search is not a complete missing-work check.
   Access errors, partial pagination, unknown owners and ambiguous lesson matches
   hold the assignment for review. Multiple student documents require resolution.
5. Read each current document revision. Apply the supplied Prompt 1 to actual
   writing, preserving marker inventory and source evidence. A document existing,
   containing text or containing printed prompts does not prove completion.
   Meaningful attempts earn completion credit regardless of correctness.
6. Reuse `completion_policy.assess_completion` for Content/notes and Revision
   scores out of 10. Optional work is excluded. An initial checkpoint does not
   score final revision work. Unreadable work is Needs you, never automatic zero.
7. Keep missing work unscored through the full fourteenth calendar day after a
   date-only due date in the school timezone. After that cutoff, a fresh complete
   scan can propose zero and a parent-contact review item. First submissions after
   the cutoff require review. No automatic email is sent by this workflow.
8. Present proposed scores with check time, source revision and evidence. Bind
   IC targets explicitly. A separate authorized executor must recheck current
   submission/grade revisions immediately before posting, then read IC back.
   Existing grades and prior completion history remain intact.

## Runtime adapter contract

`scan_folder(scope, as_of) -> FolderScan` supplies an authenticated, assignment-
scoped, complete cumulative folder snapshot and current IC assigned roster.
`FolderSubmission.submitted_at` is the actual turn-in event; it is not a file
modification time. Revision observation order must identify the current content;
conflicts must be reported in `unresolved_items`. Pagination, document download,
student resolution, current assignment membership and history persistence are
responsibilities of the provider, not inferred by this pure workflow.

`assess_work(scope, fact) -> WorkAssessment` must read the exact document revision
referenced by the fact and return inspected response judgments. The assessment
must match document ID, revision and the verified lesson readiness record.
Existing local completion-policy code is reused, not replaced by string heuristics.

`receipt_id` fingerprints the checked scope, source snapshot, policy and result.
It is an identity, **not** a concurrency lock or proof of posting. Host execution
must atomically claim the existing shared task record for class/assignment before
reading or assessing. A second caller must see Working and stop. Store private
results and last successful baseline only after a complete successful scan;
failed scans never replace that baseline. Shared storage/claims remain unconnected.

## Portable manual use (school Claude or ChatGPT)

Use the versioned instructions with the selected lesson context and original
Prompt 1. Both clients must use the same authenticated sources and persistent
status record (Queued / Working / Needs you / Verified). If the shared record
cannot be read and claimed, do not start a concurrent live run. Credentials and
student content must not be copied into repository files or public pages.

Dashboard Copy instructions includes the sequence above in concise form followed
by the unchanged teacher Prompt 1. Run remains disabled pending authenticated
HTTPS source reads, shared claims, private storage and live readback acceptance.

## Acceptance evidence and remaining work

The prior browser test established that one hidden, excluded IC test score could
be saved, reread, cleared and removed. It did not establish a server-side IC
connector. The Drive connector currently returns Unknown tool for search; live
folder enumeration and student-work extraction were not verified in this build.
The current implementation is a provider-composition boundary tested using
fictional inputs. No student work was graded or real grades changed by it.

For Prompt 2, Prompt 3 and the existing dashboard integration, see
`TEACHING_RESULTS_INTEGRATION.md`.
