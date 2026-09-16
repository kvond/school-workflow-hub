# Teacher-directed Agenda editing

The teacher authorized implementation and deployment of live Agenda saves on
September 16. This supersedes the historical code-only restriction for this
specific workflow. It does not activate grade posting, emails or printing.

GET `/api/agenda-save?date=YYYY-MM-DD&class_key=forensics` verifies signed IAP,
resolves the weekly row/day, reads exact values, verifies Sheets edit access
using an empty batch, and checks private state access. It supplies the revision
used by POST `/api/agenda-save`. The browser sends date, class_key, revision,
values (title, plan, notes), and a stable request_id. No target coordinates or
credentials are accepted from the browser.

The writer obtains a workbook-scoped Firestore claim, rereads the mapped source,
rejects stale input, writes changed literal values only, and rereads the same
destination. Saved is returned only after matching readback and a durable receipt.
Repeated completed requests verify the destination without another write.
Uncertain writes retain their claim and require reconciliation instead of blind
retries. Both same-origin HTTPS and signed IAP are required for POST. Source
spreadsheet and service account come from existing private runtime configuration.

The weekly sheet is the write target. Existing Dashboard/list formulas remain
intact. Literal Dashboard overrides and days without a weekly column are held
for mapping review. Extra resource links are not yet writable. Sheets direct
edits do not share the Firestore lock: the fresh comparison catches prior edits,
but Sheets offers no atomic revision precondition for a cell update. Avoid
simultaneous direct-Sheets editing of the same cell during a save.

Local synthetic tests are not live acceptance. Verify Editor permission and one
supervised save/readback in the hosted service before reporting this operational.
