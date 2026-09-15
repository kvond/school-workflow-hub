# Shared teaching prompts in Agenda

The existing Google Sheets Agenda owns the editable prompt text on the
`Workflow Prompts` tab. Its small layout is deliberate:

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

`GET /api/agenda-prompts` reads only `Workflow Prompts!A1:B10`, checks the named
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
3. Update the prompt cells on Workflow Prompts in the same pass.
4. Record date, change, affected Agenda pages, skill version and prompt version.
5. Reconcile the release reference and verify dashboard Copy instructions reads
   the intended text before accepting the new version.

No automatic skill rewrite, grade posting, feedback generation or printing is
triggered by editing prompt cells. Run remains separately gated.
