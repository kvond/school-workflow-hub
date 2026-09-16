# Hosted submission folder check

Page 2 now offers **Check submitted files**. The signed-IAP protected
`GET /api/submission-inventory?date=YYYY-MM-DD&class_key=forensics` resolves
its folder from the current Agenda TurnIn Map. No browser folder ID is accepted.
The attached runtime identity uses Drive metadata read-only scope. Share the
active lesson tree with that identity as Viewer; connector access in a chat
does not grant runtime access.

The reader requires a course/week-specific folder, paginates and traverses
nested upload folders with bounded depth, page and record limits. Incomplete
search, repeated cursors, mismatched parents and shortcuts fail the entire
check. Scope changes in the UI discard old responses. No student data is stored
in repository assets or local fixture files.

This is an inventory of files visible to the runtime identity, not proof of
complete submission history or missing work. It does not identify students,
read document answers, assess completion, generate feedback, infer zeros or
post grades. IC roster/due-date readers, evidence assessment and the private
teaching-results provider remain outstanding. No new schedule or background
execution is enabled. Roster Run remains disabled until its actual executor
exists and passes acceptance.

September 16 validation: 539 Python tests passed including nested pagination,
wrong-lesson/parent rejection, partial-scan rejection and signed identity checks.
Hosted read acceptance must be recorded separately after deployment.
