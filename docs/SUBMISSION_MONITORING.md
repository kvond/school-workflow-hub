# Hosted submission monitoring

Page 2's **Submission monitoring** panel watches one exact assignment throughout
the post-due window and records what actually changed, durably, in the existing
private cloud state. Discovery is form-first: the shared Google Form supplies
assignment-bound upload file IDs, then every candidate is verified beneath the
approved `00 ALL TURN IN` root. The TurnIn Map remains a locator, not a
completeness boundary. The monitor is deterministic hosted Python behind
`GET/POST /api/submission-monitor`; it never assigns a grade or a zero.

## What one scan does

1. Resolves the dated lesson from the live Agenda and requires its exact
   assignment label plus the shared prefilled `_turnInURL`.
2. Calls the Google Forms responses API with the runtime reader's
   `forms.responses.readonly` token. A response belongs to the lesson only
   when one of its text answers exactly equals the Agenda assignment label.
   File-upload answers supply Drive file IDs directly; zero responses is a
   clean empty state.
3. Verifies every Form-referenced file beneath the configured
   `HUB_SUBMISSIONS_ROOT_FOLDER` using the existing `drive.readonly` reader.
   A referenced file that cannot be verified under that root makes coverage
   incomplete; it never becomes evidence that a student is missing.
4. Reads the reviewed grading workbook only for already-verified
   student↔file mappings. It is not a prerequisite for discovering new work.
5. Advances and persists the existing monitor state, idempotency claims,
   revision evidence, deadlines and event history. The durable stream identity
   is class + exact assignment label, not a reused course folder.

Runtime configuration additionally requires `HUB_TURNIN_FORM_ID`, the editable
/API Form ID for the shared turn-in Form. The runtime service account must be
able to read that Form's responses. No Form, Drive, or student-file writes are
performed by this read path.

## The rules the implementation encodes

- **Assignment identity is the exact assignment label within the class, never a
  reused course folder and never the due date.** A deadline change emits
  `due_date_changed` and keeps the same stream and history.
- **The deadline is the third actual class meeting, never the navigation
  date.** The teacher declares an actual start by writing it in the day's Plan
  or Notes ("Started Energy today" — past tense; "Plan to start" is only an
  intention) either through the dashboard's Save to Agenda flow or directly in
  Sheets, where a later scan of that day picks it up; the selected dashboard
  date is meeting 1 even for a backdated note. Meetings 2 and 3 come from the
  Agenda's own Weeks calendar (`workflows/topic_deadline.py`): fixed weekday
  patterns per class, ⚠ closure lines, and the per-week A/B Friday
  designation. The final revised submission is due at the end of the third
  meeting's local day whether or not the topic is finished; only an explicit
  teacher deadline exception ("deadline/due <date>" in the day's text)
  overrides it. Repeated saves never duplicate a start, a contradictory start
  is flagged for review instead of resolved, an unfilled calendar makes the
  deadline provisional instead of guessed, and every derived deadline is
  labeled calculated — never verified in Infinite Campus. Date-only deadlines
  still include the whole local day and the grace window still includes the
  full fourteenth day via the existing `due_completion.boundaries` rules;
  verified individual extensions move a matched student's window, and no
  extension source is integrated yet, so none are applied silently.
- **Assessment currency is revision-bound, not date-bound.** A file edited at
  3 p.m. after a 9 a.m. assessment returns to awaiting reassessment the same
  day: a full workbook check timestamp is compared as an instant, a date-only
  marker can never prove currency for a file modified on or after that day,
  and unknown evidence proves nothing. Earlier assessments and grades are
  never altered by this.
- **Derived state is versioned.** `DERIVATION_VERSION` travels with each
  stored state; an unchanged rerun only replays a stored result when the
  derivation logic that produced it is current, otherwise the state is
  recomputed from the retained evidence with history, events and receipts
  preserved.
- **First scan = baseline.** Existing files are `first_observed`, never "just
  submitted" and never late.
- **Metadata is evidence, not judgment.** `modifiedTime`/`version` changes mean
  inspect or reassess. Timing labels are conservative:
  `content_predates_due_turnin_unverified` (a folder move does not update
  modifiedTime, so turn-in moments stay unverified) or
  `modified_after_due_needs_review`. Nothing here proves who submitted,
  proves improvement, or supports a zero.
- **Disappearance is an access event.** A file missing from a complete scan
  becomes `inaccessible` and keeps its history; it is never treated as a
  withdrawal, and a failed or partial scan never replaces the last complete
  baseline (it only records a failed attempt).
- **Unmatched files stay visible for review**; the roster basis is the
  workbook's verified mappings with its checked time recorded. The live IC
  roster reader is not integrated, and the panel says so instead of calling a
  cached view current.
- **Reruns do not duplicate.** An unchanged folder + mappings + due date
  returns the stored result with no new run, records or events; concurrent
  workers serialize on the task lease and baseline CAS. A dashboard refresh
  only reads; scans start from the explicit button (or an approved scheduler
  calling the same endpoint).

## Dashboard

`web/submission-monitor.mjs` renders: whether automatic monitoring is active
(honest `manual_scans_only` until a schedule is approved), last attempted and
last successful scan, scope/coverage/roster basis, per-file stages kept
distinct — `Detected · identity unmatched`, `Awaiting assessment`, `Assessed`
(the workbook's recorded IC posting note is shown as a recorded claim, never as
gradebook verification) — the changes the last scan proved, and a needs-review
list where every failure carries one specific recovery action.

## Verification state

648 backend tests (including the monitoring state machine: new, late-evidence,
modified, replacement, ambiguous identity, extension, deadline change, partial
page, permission failure, interrupted/concurrent worker) and 36 frontend tests
pass with identical synthetic replay.

Historical folder-reader acceptance, September 18, 2026: with the teacher-approved Viewer grant for
the runtime reader on the turn-in root and the project's Drive API enabled
(both were required; an unconfigured API and a missing grant produce the same
Drive 403, which is why the earlier inventory check never succeeded live), the
hosted scan completed for all three classes' current lessons. One class's tree
held five real student files: all were labeled first-observed at baseline, one
carried a verified workbook match and assessed stage, and four stayed visible
as unmatched/awaiting review. The other two trees scanned complete with a Form
and no loose files, recorded as exactly that. A rerun with unchanged inputs
returned the stored result (`unchanged_rerun`) with no new documents, a failed
pre-grant scan left a durable failed attempt without touching any baseline, and
a baseline written by one service revision read back intact from the next
revision. The dashboard panel rendered the same stored state after reload.

Live change detection (a student actually modifying or adding a document
between scans) has not been demonstrated against real work — no student file
was edited to manufacture an event — and remains covered by the synthetic
cases only. Scheduled execution is not enabled; enabling it is a separate
explicit approval (cadence + scope), and the scan endpoint's same-origin gate
means a scheduler integration will also need its own authorized entry path.


## Form-first rollout note — September 22, 2026

The form-first refactor is intentionally separate from the September 18
folder-reader acceptance. It must pass synthetic tests and one supervised live
lesson after deployment before being described as live-verified. The rollout
must enable the Google Forms API, configure the editable/API Form ID, and give
the existing runtime reader access to that Form. Existing Drive-root read
authority remains unchanged.
