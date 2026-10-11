# Explicit combined completion assessments

`HUB_COMBINED_ASSESSMENTS` is a private JSON list. Its default is empty, so
existing assignments keep their single-deck behavior. Every declaration has
exactly `school_year`, `agenda_id`, `class_key`, `week`, `form_id`,
`lesson_folder_id`, and `components`. Each component has exactly `cycle`,
`teacher_id`, and a short teacher-facing `label`. This implementation accepts
Forensics only. It does not replace Biology Photosynthesis's strict policy.

The declaration must match the current Agenda's exact spreadsheet, year,
class, week, canonical Form, lesson folder and teacher links. Source cycles
come from the complete submitted bytes, not a filename, Form title or row
position. A neighboring Form retains its own assignment; it is never imported
as another component. The normal IC map still needs one explicit Form entry
with the independently verified section/Notes/Revision targets. No map is
created by this reader.

The reader retains original current/earlier histories and derives one latest
authenticated school-email upload per registered cycle. On-time selection
preserves the existing deadline. A verified final-entry recheck may select
later authentic revisions. Wrong Form, folder-only/shared intake, uncertain
history, conflicting identity, equal-time competing files, unknown/mixed
cycles and unavailable or changing sources remain review cases.

Meaningful actual NOTES/BANK and DRAFT counts are summed separately, then the
existing ten-point calculation rounds each pooled result once. Missing parts
are disclosed; no teacher denominator or missing-work zero is invented. Each
item is identified by cycle and question/category, so matching question IDs in
two different lesson parts count independently. A zero denominator is N/A.
Per-file writing feedback uses only that source's own teacher and revisions.
Only the latest eligible on-time original of each declared cycle can receive
that report, with its exact original Form receipt and date. A newer upload of
the other component does not hide it; superseded files remain ineligible.

Every combined result includes `component_coverage`, component counts and
`aggregate_provenance`: the exact scope digest, selected original IDs, Form
receipt IDs/dates, source versions/raw hashes/metadata hashes and exact teacher
bases. A fresh full-byte read with identical metadata bookends must match each
bound basis before scoring or recording completion. Export-byte variation
also holds; this implementation does not waive fonts or strip source markers.
The existing helper revision binds the whole source-derived Form and scores.
Keep the actual result with the existing private run evidence; this change
does not add a new datastore writer or claim an IC save from a source result.

Source originals, grade history, feedback history, Printed/Handed fields,
IC flags, higher-only comparisons and uncertain-save recovery are unchanged.
Runtime configuration, current source intake and exact destination readback
must be verified separately before claiming live combined-grade acceptance.
