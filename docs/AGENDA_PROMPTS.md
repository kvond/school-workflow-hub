# Shared teaching prompts in Agenda

The existing Google Sheets Agenda owns the editable prompt text on the
prompts tab (currently `Feedback Prompts`). Its small layout is deliberate:

| Cell | Content |
| --- | --- |
| B2 | Update-together rule |
| B3 | Prompt version |
| B4 | Alignment verification note |
| B6 | Completion — Prompt 1 |
| B7 | Feedback — Prompt 2 |
| B8 | Class summary — Prompt 3 |
| B9 | Workflow integration reference |
| B10 | Change-record fields |

The dashboard shows direct Agenda-cell links beside Copy instructions for each
prompt. Copy instructions fetches the **current Agenda text**, using the existing
signed-IAP, read-only Sheets connection, rather than silently copying an outdated
packaged version. If Agenda cannot be read, it reports the failure.

`GET /api/agenda-prompts` locates the unique prompts tab from metadata, reads only `A1:B10`, checks the named
rows, and returns exact text, SHA-256 fingerprints, direct links, version and
change rule. Spreadsheet and sheet IDs come from authenticated runtime reads;
no school identifiers or credentials are embedded in the published assets.

The bundled `web/teacher-prompts.json` remains a reviewed release reference and
holds the separate due-assignment manual preamble. When Agenda prompt text differs,
the dialog flags alignment review. That comparison is **not** proof that matching
skills or teaching pages were reviewed.

## When instructions change

1. Update the affected Agenda lesson/planning pages.
2. Update the matching skill instructions, including the matching Skill 4 part
   and §12 prompt where both are maintained.
3. Update the prompt cells on Feedback Prompts in the same pass.
4. Record date, change, affected Agenda pages, skill version and prompt version.
5. Reconcile the release reference and verify dashboard Copy instructions reads
   the intended text before accepting the new version.

No automatic skill rewrite, grade posting, feedback generation or printing is
triggered by editing prompt cells. Run remains separately gated.

The current report layout and year-history contract are in `FEEDBACK_V2.md`. Runtime submission routing uses the active root recorded in Agenda Feedback Prompts B5 and the verified assignment TurnIn Map; exclude trashed duplicates.

## September 16 prompt alignment

The feedback-v2.1 Agenda change updates B3 and B6:B8 together. It preserves
Prompt 1 completion arithmetic, adds per-submission manual reconciliation of
marker defects, excludes not-yet-due work, and limits Page 3 to one short summary
per verified class section. Private evidence/history remains separate. The
active bundle and `FEEDBACK_V2.md` carry this revision; historical assessments
retain their original prompt-version provenance.

The bundle fingerprint continues the existing algorithm:
`sha256(json.dumps(prompts, sort_keys=True, ensure_ascii=False).encode())`, using
Python's default JSON separators, UTF-8 and no appended newline. It hashes only
the three-prompt object. Each API prompt fingerprint instead hashes that exact
prompt's UTF-8 text. Agenda B3 carries the aggregate fingerprint.

Connector readback verified the four Agenda values and the other populated
cells across the existing prompt-tab grid. This does not prove deployed Copy
instructions, native visual rendering, or every separately maintained §12,
Skill 4 and worked-example copy is aligned. Those remain distinct readback
checks under the B2 update-together rule; do not label all destinations aligned
from the bundle fingerprint alone.
