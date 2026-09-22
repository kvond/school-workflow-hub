# Hosted submission intake

The authenticated inventory and monitor support **Form-first intake**, including
one shared Form used by several courses and assignments. Reads never create,
move, convert, edit or delete Drive files or folders, submit a Form, or post grades.

## Source resolution

The dated Agenda reader reads course rows through column U. `_turnInURL` (U)
contains the submission Form, while `_folderURL` (R) is lesson material. The
separate TurnIn Map supplies the exact assignment label and legacy folder ID.
A present Form takes precedence. Missing/invalid Form configuration is an
explicit hold; it never falls back to scanning a potentially course-wide folder.
Rows with no Form retain the existing bounded folder path.

The observed workbook layout uses prefilled links to a **shared Form**. A Form
ID alone therefore cannot identify an assignment. Every Form read selects the
exact answer to a configured assignment question. The prefilled `entry.N`
number is not assumed to equal a Forms API `questionId`. There is no fuzzy
matching, filename-based assignment inference or class-wide grading fallback.
The Agenda export was inspected, but live Form responses, Drive permissions and
upload-folder creation timing have not been verified by this change.

## Private runtime configuration

Set `HUB_SUBMISSION_FORMS` to a JSON object. This is a configuration handoff,
not authorization to alter service-account grants, enable APIs or deploy.
The repository owner must configure and confirm those separately. Keep real
IDs and student data out of GitHub. This example is entirely fictional:

```json
{
  "1FAIpQLfictional_published_123456789": {
    "form_id": "fictional_form_1234567890",
    "assignment_question_id": "question_from_forms_get",
    "assignment_entry_id": "12345",
    "identity_questions": {
      "name": "name_question_id",
      "period": "period_question_id"
    },
    "lessons": {
      "biology": {
        "biology:fictional": "Biology · Wk 01 — Fictional topic"
      }
    }
  }
}
```

- The outer key is the published ID from `/forms/d/e/.../viewform`, or the
  canonical API ID for an editor link/raw API ID.
- Obtain `form_id` from the owner’s editor link `/forms/d/{id}/edit`, then use
  `forms.get` to verify `responderUri` and the exact assignment `questionId`.
  The code checks these against the binding before reading responses.
- Obtain `assignment_entry_id` from the Agenda’s prefilled URL; compare the
  named question and value with the actual Form. Published IDs and entry
  numbers are never silently converted to different API identifiers.
- `identity_questions` is optional. Collected email and configured name/period
  answers are unverified provenance; workbook identity matching stays authoritative.
- `lessons` binds the dashboard’s existing workbook assignment keys to exact
  Form answers for the **Process work** action, without needing an already
  assessed submission or a workbook write. Register the assignment in the
  existing Assignments table first. Alternatively, its optional `turnin_form`
  and `turnin_assignment` columns can supply that binding. Configure one source,
  not both; ambiguous bindings are rejected. The dated path uses the Agenda
  directly and does not need the `lessons` section.
- Multiple weekly answers referring to one teaching unit are **not** silently
  combined. Each binding currently selects one exact answer. Review such units
  before claiming complete historical coverage.

The attached `HUB_READER_SERVICE_ACCOUNT` uses the existing short-lived token
mechanism. Responses use `forms.responses.readonly`; schema and file reads use
`drive.readonly` (also accepted by `forms.get`). The Forms API must be enabled,
and the identity must have access to the Form’s responses and each uploaded
file. Scope approval alone does not grant file access. No IAM, sharing, Cloud
Build, scheduling or deployment configuration is changed by this patch.

## Read guarantees and limits

`forms.responses.list` reads all pages (up to 50 pages / 25,000 responses),
filters the exact assignment, and extracts every upload answer. Drive metadata
is fetched by file ID, up to 500 distinct files. Duplicate references preserve
response/question provenance; different file IDs are retained. File versions
and modified times come from Drive, not from response submission timestamps.

Zero responses returns an empty result. Matching responses without uploads are
counted separately. Responses with no usable assignment answer are counted as
unassigned and never assigned to a class by inference. Inaccessible or trashed
referenced files, malformed pages, repeated cursors, exceeded bounds, changed
Form schema and permission failures stop the complete read. A failed monitor
read cannot replace the previous successful baseline. Rate limits surface a
retry-later state; this reader does not spin on Google or increase quotas.

Coverage means the selected assignment’s visible Form references, **not** a
complete current roster, an atomic historical snapshot, or proof of missing
work. Google’s response-list API does not supply a snapshot token. Questions
are reread to detect schema drift; later student responses are picked up on
later scans. Existing grading, review, claim and readback controls remain.
The legacy folder intake is still available only for explicitly folder-based
sources. Recovered work and other historical intake need separate reconciliation.

## PowerPoint capture

Native Google Slides and Docs retain their existing text export. A Drive file
whose actual MIME type is PPTX is downloaded by ID (`alt=media`) and read locally
without conversion. Presentation order, grouped/table text, paragraph breaks
and embedded text markers are preserved. Speaker notes are excluded; images
are not OCRed. File metadata is reread after capture to reject concurrent edits.

Limits: 32 MB download, 128 MB expanded ZIP, 10,000 entries, 1,000 slides,
8 MB per XML part and 2 MB extracted text. Invalid/duplicate/encrypted archives,
external slide targets and DTDs fail explicitly rather than producing blank work.
This supports PPTX already stored in Drive; it does **not** implement dashboard
ZIP upload or prove that a locally downloaded copy is the live Drive revision.
A Google Slides download may be exported as PPTX without changing its source.

## Validation and deployment acceptance

Synthetic tests cover source parsing, exact shared-Form filtering, zero/one/many
responses, pagination, multiple upload questions, missing answers, deduplication,
failed reads, metadata drift, read-only scopes, inventory/monitor/topic wiring,
and native/PPTX capture. Run `python3 scripts/cloud_validation.py` with hosting
dependencies and `node --test web/tests/*.test.mjs tests/*.test.mjs`.

Before merging/deploying, the owner must supply the private binding and confirm
required access. After deployment, check one known assignment against its actual
Form responses and file IDs, one empty assignment, unchanged rerun and a failed
read retaining its baseline. Do not manufacture student submissions for testing.
Implementation and synthetic tests do not establish live acceptance.

See [submission monitoring](SUBMISSION_MONITORING.md) for persistence and history.
API references checked September 22, 2026:
[response listing](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses/list),
[file upload answers](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses),
[Form schema](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms),
[Form read scopes](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/get),
[quotas](https://developers.google.com/workspace/forms/api/limits), and
[export formats](https://developers.google.com/workspace/drive/api/guides/ref-export-formats).
