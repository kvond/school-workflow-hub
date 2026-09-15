# Authenticated dated Agenda read — v1

`GET /api/agenda-day?date=YYYY-MM-DD` uses the existing Sheets reader, attached runtime identity and signed IAP assertion verification. It reuses the five environment variables documented in AGENDA_CONNECTION_PILOT.md. No new OAuth scopes, keys, backend or storage service.

The response is `hub-agenda-day/v1`, status `source_read`, with the requested calendar date, check time, week/closure text and three class views: topic, plan, notes, teacher Slides `/edit` URL, separate student Slides/folder/material links, weekly to-do and prep, and sheet-coordinate provenance. Coordinates are not stable assignment IDs or write targets. It always returns `save_enabled:false`, `run_enabled:false`, `external_writes:0`. This is a source read, not destination verification or comparison.

## Source mapping

The existing Weeks table maps explicit Monday dates to course rows and week labels. The reader validates the visible tabs, header positions, date membership and course week labels. Reads are bounded using the existing reader limits: metadata; Weeks A2:G43 and Dashboard A6:F9; the three course sheets A3:R through the selected row (row cap 60, under 5000 cells per read). Unknown dates, ambiguous mapping and changed headers fail closed.

Day headings select the weekly plan/note pair. Friday closure/rotation text remains verbatim; no attendance classification is inferred. The current Dashboard's dated literal entries are retained as overrides; formula display differences are retained with the underlying weekly plan separately. Historical and future views use weekly fields because the Dashboard only exposes its current week. Blank notes remain blank. Teacher links must already be Google Slides `/edit` URLs: a student `/copy` URL is never converted into a claimed teacher link.

## UI and privacy

Use **Load this date from Agenda**. Subsequent date changes read that date. Requests are same-origin with redirects rejected; backend and frontend both enforce the read-only response shape. Source text is rendered as text, never HTML. Responses are `no-store`; raw data and credentials are not logged or written to disk. Synthetic examples and research are unchanged.

Clean in-tab drafts initialize from the source. Dirty drafts are never replaced by a source refresh; the latest incoming source is shown separately. Older responses cannot replace the selected date's newer response. A failed refresh labels the prior read as potentially stale. Reloading loses drafts. Saving, printing, email, grading and workflow execution remain disabled.

## Validation boundary

Synthetic Python tests cover day mapping, closures/extra meetings, link separation, layout/date failures, missing signed identity, request methods and private error redaction. Browser checks use explicitly synthetic intercepted responses for draft preservation, date races, unsafe links and failed refreshes. These do not prove authenticated deployed lesson rendering. A teacher must open the deployed page, load a date and compare its content and teacher deck against the live Agenda before that end-to-end result is marked verified.
