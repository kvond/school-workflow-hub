"""Assignment-scoped Forms responses and Drive metadata, GET requests only.

Read every response page before publishing any files. Identity answers are
unverified provenance, never a replacement for reviewed workbook mappings.
"""
import re

from .hosted_agenda import ConnectionError, service_token
from .submission_sources import ID, parse_form
from ..workflows.due_completion import timestamp

RESPONSE_SCOPE = "https://www.googleapis.com/auth/forms.responses.readonly"
MAX_PAGES = 50
MAX_RESPONSES = 25000
MAX_FILES = 500


class FormsReader:
    def __init__(self, token, body_token):
        import requests
        self.session = requests.Session()
        self.session.trust_env = False
        self.token, self.body_token = token, body_token

    def _get(self, form_id, *, responses=False, cursor=None):
        if not isinstance(form_id, str) or not re.fullmatch(ID, form_id):
            raise ConnectionError("submission_form_mapping_required")
        params = {"pageSize": 500} if responses else {}
        if cursor:
            params["pageToken"] = cursor
        try:
            response = self.session.get(
                "https://forms.googleapis.com/v1/forms/" + form_id + ("/responses" if responses else ""),
                headers={"Authorization": "Bearer " + (self.token if responses else self.body_token)},
                params=params, timeout=(5, 20), allow_redirects=False)
            if response.status_code == 403:
                try:
                    error = response.json().get("error", {})
                    reasons = {e.get("reason") for e in error.get("errors", [])}
                    reasons.update(e.get("reason") for e in error.get("details", []))
                except Exception:
                    reasons = set()
                if reasons & {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}:
                    raise ConnectionError("submission_forms_scope_missing")
                if reasons & {"SERVICE_DISABLED", "accessNotConfigured"}:
                    raise ConnectionError("submission_forms_api_disabled")
                raise ConnectionError("submission_form_access_required")
            if response.status_code == 404:
                raise ConnectionError("submission_form_access_required")
            if response.status_code == 429:
                raise ConnectionError("submission_forms_rate_limited")
            if response.status_code != 200:
                raise ConnectionError("submission_forms_unavailable")
            result = response.json()
            if not isinstance(result, dict):
                raise ConnectionError("submission_form_scan_incomplete")
            return result
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError("submission_forms_unavailable") from None

    def form(self, form_id):
        return self._get(form_id)

    def responses(self, form_id, cursor):
        return self._get(form_id, responses=True, cursor=cursor)


def _text_answer(answers, question):
    answer = answers.get(question, {})
    if not isinstance(answer, dict):
        raise ConnectionError("submission_form_scan_incomplete")
    values = answer.get("textAnswers", {}).get("answers", [])
    if not isinstance(values, list) or any(not isinstance(v, dict) or not isinstance(v.get("value"), str) for v in values):
        raise ConnectionError("submission_form_scan_incomplete")
    return [v["value"] for v in values]


def collect(reader, drive, source):
    """Return (form metadata, Drive files, coverage), including empty results."""
    try:
        return _collect(reader, drive, source)
    except ConnectionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ConnectionError("submission_form_scan_incomplete") from None


def _collect(reader, drive, source):
    form_id = source["id"]
    form = reader.form(form_id)
    if form.get("formId") != form_id:
        raise ConnectionError("submission_form_mapping_conflict")
    if source.get("published_id"):
        ref = parse_form(form.get("responderUri", ""))
        if ref["kind"] != "published" or ref["id"] != source["published_id"]:
            raise ConnectionError("submission_form_mapping_conflict")
    questions = {}
    for item in form.get("items", []):
        question = item.get("questionItem", {}).get("question")
        if question:
            questions[question["questionId"]] = question
    assignment_question = questions.get(source["assignment_question_id"], {})
    if not any(k in assignment_question for k in ("textQuestion", "choiceQuestion")):
        raise ConnectionError("submission_form_mapping_conflict")
    if any(q not in questions for q in source.get("identity_questions", {}).values()):
        raise ConnectionError("submission_form_mapping_conflict")
    upload_questions = {q for q, v in questions.items() if "fileUploadQuestion" in v}
    if not upload_questions:
        raise ConnectionError("submission_form_upload_question_required")
    cursor, cursors, seen_responses, references = None, set(), set(), {}
    matched, missing_uploads, unassigned = 0, 0, 0
    for page_number in range(1, MAX_PAGES + 1):
        page = reader.responses(form_id, cursor)
        responses = page.get("responses", [])
        if not isinstance(responses, list):
            raise ConnectionError("submission_form_scan_incomplete")
        for response in responses:
            response_id = response.get("responseId")
            if (response.get('formId', form_id) != form_id
                    or not isinstance(response_id, str) or not 0 < len(response_id) <= 256
                    or response_id in seen_responses or len(seen_responses) >= MAX_RESPONSES):
                raise ConnectionError("submission_form_scan_incomplete")
            seen_responses.add(response_id)
            answers = response.get("answers", {})
            if not isinstance(answers, dict):
                raise ConnectionError("submission_form_scan_incomplete")
            assignment = _text_answer(answers, source["assignment_question_id"])
            if len(assignment) != 1 or not assignment[0]:
                unassigned += 1
                continue
            if assignment != [source["assignment"]]:
                continue
            matched += 1
            submitted_at = response.get("lastSubmittedTime") or response.get("createTime")
            timestamp(submitted_at)
            identity = {name: _text_answer(answers, question)
                        for name, question in source.get("identity_questions", {}).items()}
            email = response.get("respondentEmail")
            if email is not None and (not isinstance(email, str) or len(email) > 320):
                raise ConnectionError("submission_form_scan_incomplete")
            provenance = {"response_id": response_id, "submitted_at": submitted_at,
                          "respondent_email": email, "identity_answers": identity,
                          "identity_verified": False}
            found = False
            for qid in sorted(upload_questions):
                answer = answers.get(qid, {})
                uploads = answer.get("fileUploadAnswers", {}).get("answers", [])
                if not isinstance(uploads, list):
                    raise ConnectionError("submission_form_scan_incomplete")
                for upload in uploads:
                    fid = upload.get("fileId")
                    if not isinstance(fid, str) or not re.fullmatch(ID, fid):
                        raise ConnectionError("submission_form_scan_incomplete")
                    found = True
                    evidence = dict(provenance, question_id=qid)
                    refs = references.setdefault(fid, [])
                    if evidence not in refs:
                        refs.append(evidence)
                    if len(references) > MAX_FILES or len(refs) > 100:
                        raise ConnectionError("submission_form_scan_incomplete")
            if not found:
                missing_uploads += 1
        cursor = page.get("nextPageToken")
        if not cursor:
            break
        if not isinstance(cursor, str) or len(cursor) > 8000 or cursor in cursors:
            raise ConnectionError("submission_form_scan_incomplete")
        cursors.add(cursor)
    else:
        raise ConnectionError("submission_form_scan_incomplete")
    files = []
    for fid, refs in sorted(references.items()):
        try:
            meta = drive.metadata(fid)
        except ConnectionError as error:
            if str(error) == "submission_folder_access_required":
                raise ConnectionError("submission_file_access_required") from None
            raise
        if (meta.get("id") != fid or meta.get("trashed") is not False
                or meta.get("mimeType") in {"application/vnd.google-apps.folder", "application/vnd.google-apps.shortcut", "application/vnd.google-apps.form"}
                or not isinstance(meta.get('mimeType'), str)
                or not isinstance(meta.get("name"), str) or not 0 < len(meta["name"]) <= 400
                or not meta.get("version")):
            raise ConnectionError("submission_file_access_required")
        timestamp(meta.get("modifiedTime"))
        files.append(dict(meta, form_responses=sorted(refs, key=lambda r: (r['response_id'], r['question_id']))))
    # Response enumeration has no snapshot token. Verify the question mapping
    # did not change during this read; do not claim complete roster coverage.
    if reader.form(form_id) != form:
        raise ConnectionError("submission_form_changed")
    return form, files, {"complete": True, "source": "form_responses",
                        "scope": "selected_assignment_only", "roster_complete": False,
                        "pages_read": page_number, "responses_matched": matched,
                        "responses_without_upload": missing_uploads,
                        "unassigned_responses": unassigned,
                        "folders_checked": 0, "forms_found": 1}


def read(source, config):
    from .submission_inventory import DRIVE_SCOPE, DriveReader
    account = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    drive_token = service_token(account, DRIVE_SCOPE)
    reader = FormsReader(service_token(account, RESPONSE_SCOPE), drive_token)
    drive = DriveReader(drive_token)
    try:
        return collect(reader, drive, source)
    finally:
        reader.session.close()
        drive.session.close()
