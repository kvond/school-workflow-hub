# Browser-assisted roster and grading runs

The signed-in dashboard exposes shared ownership and private run reports at
`/api/browser-runs`. Infinite Campus has no configured server API. This interface
reserves work and stores reports; it does not execute grading, grant permission,
or independently verify school results. Actual destination changes require a
separately implemented and accepted local browser executor.

Manual requests and scheduled wake-ups use the same procedure and canonical
task: `weekly-roster-sync/all-classes/2026-2027` for roster checks and the browser
grading scope defined in `adapters/browser_runs.py` for completion grading.
The existing Firestore store supplies server timestamps, compare-and-set
fencing, immutable results, and durable readback. No second operational store
or district credential is introduced.

## Teacher-initiated Page 2 posting

The September 17 teacher authorization permits supervised posting of existing
reviewed workbook grades through the signed-in school Chrome or Edge browser on
the teacher's Mac or school Windows computer. A connected browser worker may be
an assistant operating that browser. It may claim the existing shared run,
perform the selected authorized writes and record an attributed private receipt.
This replaces the earlier blanket prohibition on assistant browser writes for
this specific manual workflow. It does not authorize other school writes or
scheduled execution.

Each computer must use its own authenticated school session. Verify the browser
profile and signed-in school account before starting. Do not copy school
passwords, cookies or browser profiles to another computer, a chat, GitHub or the
cloud service. Authentication protections and restricted workbook sharing remain
in place. If the local browser executor is unavailable, the action must explain
that limitation without claiming a queued or completed grade post.

Page 2 reviews the selected workbook records and their observed source revision;
it does not assess the work again. Before writing, fresh-read the same source,
verify exact IC student, section, term and assignment mappings for each content
or revision component, and confirm the destination's points scale. A learner key
or a matching display name is not an IC student ID. Blank, held, superseded or
unverified grades stay unscored; duplicate current records need review. A
recorded IC posting status is not live gradebook evidence. Skip matching current
grades and return conflicting existing grades or teacher changes for explicit
review. Follow the ownership, recovery and destination readback requirements
below. Approval of one selection does not cover changed records or a new scope.

The [browser companion](../browser-extension/README.md) implements this bounded
executor and the Page 2 connection. It uses the current assignment's rendered
Score panel, exact source references, full visible grade snapshots, and native
Save followed by reload/readback. Synthetic validation does not establish live
acceptance. An existing-score check verifies the read/skip path; live posting
remains unverified until an authorized write and matching IC readback occur.

Live-session findings the companion now accounts for (0.2.2): Infinite Campus
renders the Student Summary panel header before its body; closed summary panels
linger in the document with layout boxes and sit inside `ic-multiscore`, so
panel reads must be a single consistent pass bound to the routed student, and
score-panel checks must exclude summary-dialog content. The companion is a
Chrome unpacked extension: content-script changes only take effect after the
extension itself is reloaded, and the packaged folder must stay at the path
Chrome recorded at install (repackage and re-load unpacked after moving it).
`gradebook_required` on a fresh review normally means the matching Score panel
is not open in the single signed-in Infinite Campus tab; `choose_one_ic_tab`
means more than one Infinite Campus tab exists in that browser. A stopped or
expired earlier run leaves `recovery_required` on the shared grading record;
inspect the actual score panel, then reserve the run and save an accurate
private report through `/api/browser-runs` (the Automation run records panel)
to clear it — release keeps recovery in place by design.

## Teacher-initiated Hub v2 list entry (September 28)

The teacher authorized the browser companion (0.3.0) to enter the Teaching
Hub v2's IC list for one assignment at a time, in the signed-in school Chrome.
It replaces the Claude extension typing scores by hand, which gave inconsistent
results. The Claude extension still does the roster check and the clicking, and
does these only after the teacher's OK in chat.

- **Source.** On the Hub's IC panel, "Send to the Chrome helper" hands the
  companion the list shown there (`hub-ic-list/v1`). The list names every
  roster student with a score, a 0, Excused, "check by hand" or nothing yet.
  The companion keeps it in browser session storage, which is memory only, for
  three hours. The page gets back only codes and counts.
- **Destination.** In the open 10-point score panel, "Hub helper" matches IC rows
  to the list by name, one to one. It refuses when most rows don't match or when
  they span sections, and it shows every row with what it will do. The teacher
  checks the lesson and the column: (F) is Notes and (S) is Revision by default.
- **Write.** The companion re-reads IC first and stops if anything changed. It
  fills blanks and raises lower scores, and never lowers one (keep the higher
  score). It leaves flagged rows, excused students, unsure rows and unmatched
  names alone, then uses native Save, reloads, and compares every score. A
  score that didn't read back is reported by name. An unconfirmed Save says to
  check IC before trying again.
- **Receipt and repeats.** The companion sends the Hub counts only, never names.
  Once every section and column is done and read back, the Hub adds an "Entered
  in IC" row with those counts to the "Scores entered" sheet. There is no cloud
  lease. Each run re-reads IC and enters only what is still missing, so a repeat
  after a partial or uncertain run cannot lower or duplicate a score.

Synthetic tests and a dry run against a mock score panel have passed. Live
acceptance is still required: the first supervised run in the real Infinite
Campus, with the teacher checking the reloaded scores.

## Worker procedure

1. Verify the school browser account, current school/year/sections, live Agenda
   instructions, selected workbook and assignment mappings. Use the supported
   browser and connected source tools. Actual private targets belong in the
   authorized runtime/task configuration, never this repository.
2. Open Connection details → Automation run records. Select the workflow and
   refresh status. Respect another active worker. Reserve a browser run; a
   recovered completed request is a stored result, not permission to repeat it.
3. Work from complete current evidence. Renew ownership before expiry and
   immediately before every permitted school write. A lease lasts at most
   fifteen minutes; the dashboard does not renew it indefinitely in the
   background. If renewal fails, stop writes. A cloud lease fences record
   publication, but cannot physically fence an Infinite Campus UI write.
4. Preserve stable learner/assignment identities, prior grades and teacher
   overrides. Fresh-read the exact destination, skip matching results, and
   save/reload/read back any authorized write. An uncertain write or an expired
   predecessor requires destination recovery before another attempt.
5. Fill the Private run report JSON and save it. The strict report contract is:

   ```json
   {
     "workflow": "roster",
     "outcome": "needs_review",
     "summary": "Partial check; one section needs identity review.",
     "counts": {"checked": 4, "changed": 0, "needs_review": 1},
     "evidence": [{
       "kind": "review_note",
       "reference": "Private source reference and scope of the unresolved check",
       "checked_at": "2026-09-16T14:00:00Z"
     }]
   }
   ```

   Workflow is `roster` or `grading`; outcome is `checked`, `needs_review`, or
   `posted`. Evidence kinds are `source_read`, `workbook_readback`,
   `grade_readback`, and `review_note`. Use actual observation timestamps and
   durable private references. The example is fictional. Storing this report
   does not replace detailed student assessment history or prove a complete
   scan. Never use inaccessible or ambiguous evidence to assign a zero.
6. An uncertain report-save response retains the exact report for retry. Do not
   repeat school changes to repair record publication. The server verifies
   the saved report readback and labels its provenance `browser_report` with
   `server_verified=false`. Browser reports cannot advance a verified baseline.
   Use Stop for review when a run cannot produce a report.

The tab keeps only an in-flight report retry in session storage, so reloading
does not lose the exact retry payload. A confirmed save clears that temporary
copy. It is not the shared results store. A definitive conflict requires
explicit recovery and inspection of any prior school changes; it never
automatically resubmits the school action.

## Authentication and status

GET requires verified IAP identity. Mutations also require the configured exact
HTTPS origin, JSON content type, bounded strict payload, and the initiating
browser's high-entropy Secure/HttpOnly/SameSite=Strict host cookie and a random
tab worker ID. Both are required to resume a run; separate tabs sharing one
school browser cannot adopt each other's claim. Use one worker per tab; do not
duplicate an active worker tab. The cookie secret is never returned by the API.
Owner, scope and lease fields are checked against
private stored state; client JSON cannot confer ownership. Other browsers see
busy status without claim handles or report content.

The existing generic manual dashboard actions copy a request for the connected
Codex task. They do not silently queue or launch a cloud IC job. The Page 2 manual
posting exception does not enable or change a schedule. Existing scheduled
wake-ups are coordination only until their executor and independent authorization
are accepted; do not treat a teacher-supervised browser path as unattended
readiness. Source, destination and history checks remain requirements for each
actual execution, without regrading the reviewed Page 2 records.

## Scoped acceptance — September 17, 2026

With companion 0.2.3 in the signed-in school browser on the teacher's Mac, a
teacher-initiated supervised Page 2 review completed end to end for the
selected existing grades of one live assignment: authenticated fresh workbook
read, exact section/assignment/term/points-scale match, per-student identity
verification against the rendered Student Summary, ownership claim on the
shared grading run, matching-grade comparison, and destination reload. Both
selected grades already matched the workbook, so no grade was changed; the
review reported "2 grades already match", saved an attributed private receipt,
and the dashboard read the same stored report back after a full reload. This
verifies the read/verify/skip path and receipt persistence. An actual new
grade write with IC readback remains deliberately unexercised, and the school
Windows computer's companion remains unverified.

## Scoped acceptance — September 16, 2026

The hosted school session successfully reserved and renewed the canonical roster
run. A second tab in the same signed-in profile saw the busy state and could not
adopt the claim. An attributed partial-check report saved with readback; after
reload, the same run ID, result hash and stored timestamp were returned. The
grading status also loaded. No grade or roster cell was written. This verifies
coordination and report persistence, not a complete grading or reconciliation
workflow. The full offline suite passed 557 tests, including 18 endpoint tests;
frontend syntax and uncertain-save recovery checks also passed.
