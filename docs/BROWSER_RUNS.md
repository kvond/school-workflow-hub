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

## Scoped acceptance — September 16, 2026

The hosted school session successfully reserved and renewed the canonical roster
run. A second tab in the same signed-in profile saw the busy state and could not
adopt the claim. An attributed partial-check report saved with readback; after
reload, the same run ID, result hash and stored timestamp were returned. The
grading status also loaded. No grade or roster cell was written. This verifies
coordination and report persistence, not a complete grading or reconciliation
workflow. The full offline suite passed 557 tests, including 18 endpoint tests;
frontend syntax and uncertain-save recovery checks also passed.
