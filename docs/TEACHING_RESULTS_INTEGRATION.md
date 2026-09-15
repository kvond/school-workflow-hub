# Completion, feedback and class summary on Agenda

## Authoritative instructions

The three §12 prompts supplied September 15, 2026 match the existing canonical
`web/teacher-prompts.json` prompt bodies (ignoring surrounding whitespace).
The original text and fingerprint are unchanged. Page 2 and Page 3 retain Copy
instructions for manual use in school Claude or ChatGPT. Completion also carries
the separate due-assignment workflow; it does not rewrite Prompt 1.

## Implemented composition

- `workflows.due_completion`: due Agenda/IC assignment scope → complete cumulative
  folder/roster snapshot → revision-bound Prompt 1 completion proposals. Missing
  work stays unscored through the two-week cutoff; ambiguous work never becomes zero.
- `workflows.student_feedback`: Prompt 2 reviewed evidence → seven exact report
  sections, exact quotations and slide attribution, first name + initial from the
  supplied filename, blank reflection with three ruled lines. Batches are at most
  twenty. Each batch provides structured compiled records and portable lines.
- `workflows.class_summary`: all compiled batches or supplied compiled records →
  participation by checkpoint/final and NOTES/BANK/DRAFT, understanding by question,
  recurring misconceptions supported by more than one student's words, supplied
  evidence-backed instructional contrasts, and teacher checks. Missing coverage
  remains partial. No decks are required when compiled evidence is supplied.
- `workflows.teaching_results`: joins those results only when class, assignment,
  learner, submission revision, assessment stage and completion counts agree.
  No duplicate GB grade record is introduced. Existing IC grade history is untouched.

The code validates and formats evidence-based assessments. It does not inspect
student science through string-length heuristics or invent positive feedback.
An authorized document reader and assessor must supply actual revision-bound
writing and reviewed judgments. No model or paid service is called by these functions.

## Existing dashboard integration

`web/teaching-results.mjs` renders completion scores and expandable student feedback
on Page 2, and all five Prompt 3 sections on Page 3. Empty state never classifies
students as missing. Fictional inputs are explicitly synthetic; source reads are
prepared-for-review, never posted or delivered. Date/class changes and failed
refreshes clear prior results and discard late HTTP responses. Research is untouched.

GET `/api/teaching-results` accepts `date`, `class_key`, `agenda_checked_at`.
The existing signed IAP identity check runs before any private provider read.
The provider must independently match the requested Agenda snapshot; it must not
copy request identity onto unrelated records. Responses use `Cache-Control: no-store`.
Malformed scope, stale scope, synthetic provider output and enabled capabilities
are rejected by the live HTTP boundary. No POST/save/run action is added.

The WSGI app accepts a host-injected `teaching_provider` with:

```python
read_results(day=..., class_key=..., agenda_checked_at=...) -> dict
```

The response schema is `hub-teaching-results/v1`, with date, UI class key, stable
source class identity, assignment, source revision, Agenda read time, result check
time, provenance, completion, feedback, summary and disabled capabilities.
Only authenticated private runtime storage may supply this payload; no local
JSON/fixture fallback, public student file or credential configuration is included.

## Still required before live processing

1. Authenticated folder/document reading, stable student/lesson resolution and
   complete cumulative submission history.
2. Current IC assignment due dates and assigned-roster readers, including extensions.
3. Actual writing assessments under these prompts and a supervised one-lesson check.
4. The shared persistent private task/status record and atomic execution claim.
   Clients must claim the same class/assignment task; a second caller seeing Working
   stops. Failed scans cannot overwrite the last successful baseline.
5. Provider hookup and live acceptance. Grade posting, email, document filing and
   printer execution each require their own authorization and destination readback.

Current Cloud Run entry point has no teaching provider and responds unavailable
rather than serving sample student work. A deployed UI is not evidence of live
workflow execution. Run, saving, printing, emailing and grade posting remain disabled.
