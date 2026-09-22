"""Read-only Google Forms submission intake keyed by the Agenda Form link.

The Agenda's responder URL is the durable assignment handle.  The public
responder id is not the Forms API id, so Cloud Run supplies the API id through
configuration; no school-specific ids are committed here.  Responses are
filtered by an exact prefilled assignment label, then uploaded Drive files are
read by id.  This module never creates, moves, renames, or deletes Drive items.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from .hosted_agenda import ConnectionError
from .submission_inventory import DRIVE_SCOPE, DriveReader

FORMS_SCOPE = "https://www.googleapis.com/auth/forms.responses.readonly"
MAX_RESPONSES = 10000
MAX_FILES = 500
_ID = re.compile(r"[A-Za-z0-9_-]{20,128}")


def parse_turnin_url(raw, fallback_label=None):
    """Normalize an Agenda turn-in Form without treating the public id as API id."""
    try:
        parts = urlsplit(raw)
    except (TypeError, ValueError):
        raise ConnectionError("submission_form_mapping_required") from None
    if (parts.scheme != "https" or parts.hostname != "docs.google.com"
            or parts.username or parts.password):
        raise ConnectionError("submission_form_mapping_required")
    public = re.fullmatch(r"/forms/d/e/([A-Za-z0-9_-]{20,128})/viewform/?", parts.path)
    editor = re.fullmatch(r"/forms/d/([A-Za-z0-9_-]{20,128})/(?:edit|viewform)/?", parts.path)
    if not public and not editor:
        raise ConnectionError("submission_form_mapping_required")
    query = parse_qs(parts.query, keep_blank_values=True)
    labels = [value.strip() for key, values in query.items() if key.startswith("entry.")
              for value in values if isinstance(value, str) and value.strip()]
    label = labels[0] if len(labels) == 1 else str(fallback_label or "").strip()
    if not label or len(label) > 200:
        raise ConnectionError("submission_form_mapping_required")
    return {"public_id": public.group(1) if public else None,
            "form_id": editor.group(1) if editor else None,
            "assignment_label": label}


def form_id(parsed, config):
    """Resolve the Forms API id without ever inferring it from a /d/e/ public id."""
    direct = parsed.get("form_id")
    if direct and _ID.fullmatch(direct):
        return direct
    public = parsed.get("public_id")
    raw_map = str(config.get("HUB_FORM_ID_MAP") or "").strip()
    if raw_map:
        try:
            mapping = json.loads(raw_map)
        except (TypeError, ValueError):
            raise ConnectionError("submission_form_mapping_required") from None
        if (not isinstance(mapping, dict) or len(mapping) > 20
                or any(not isinstance(k, str) or not _ID.fullmatch(k)
                       or not isinstance(v, str) or not _ID.fullmatch(v)
                       for k, v in mapping.items())):
            raise ConnectionError("submission_form_mapping_required")
        resolved = mapping.get(public)
        if resolved:
            return resolved
    # Current school setup uses one shared responder Form.  This single-form
    # fallback keeps Cloud Run configuration simple while the explicit map is
    # available if the workflow later grows to multiple Forms.
    single = str(config.get("HUB_SUBMISSION_FORM_ID") or "").strip()
    if single and _ID.fullmatch(single):
        return single
    raise ConnectionError("submission_form_mapping_required")


def source_id(parsed):
    """Opaque stable stream key for one shared-Form assignment."""
    anchor = parsed.get("public_id") or parsed.get("form_id") or ""
    label = parsed.get("assignment_label") or ""
    if not _ID.fullmatch(anchor) or not label:
        raise ConnectionError("submission_form_mapping_required")
    digest = hashlib.sha256((anchor + "\0" + label).encode("utf-8")).hexdigest()[:32]
    return "form_" + digest


class FormsReader:
    def __init__(self, token):
        import requests
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + token

    def responses(self, form, cursor=None):
        params = {"pageSize": 5000}
        if cursor:
            params["pageToken"] = cursor
        try:
            response = self.session.get(
                "https://forms.googleapis.com/v1/forms/" + form + "/responses",
                params=params, timeout=(5, 20), allow_redirects=False)
            if response.status_code in (401, 403, 404):
                raise ConnectionError("submission_form_access_required")
            if response.status_code != 200:
                raise ConnectionError("submission_source_unavailable")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError("submission_source_unavailable") from None


def _text_answers(answer):
    block = answer.get("textAnswers") if isinstance(answer, dict) else None
    values = block.get("answers") if isinstance(block, dict) else None
    if not isinstance(values, list):
        return []
    return [str(item.get("value") or "") for item in values if isinstance(item, dict)]


def _file_answers(answer):
    block = answer.get("fileUploadAnswers") if isinstance(answer, dict) else None
    values = block.get("answers") if isinstance(block, dict) else None
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def collect(forms_reader, drive_reader, api_form_id, assignment_label):
    """Return a complete normalized file list for exactly one assignment label."""
    if not _ID.fullmatch(api_form_id) or not assignment_label or len(assignment_label) > 200:
        raise ConnectionError("submission_form_mapping_required")
    cursor, cursors, count = None, set(), 0
    file_ids = []
    while True:
        page = forms_reader.responses(api_form_id, cursor)
        responses = page.get("responses", [])
        if responses is None:
            responses = []
        if not isinstance(responses, list):
            raise ConnectionError("submission_scan_incomplete")
        count += len(responses)
        if count > MAX_RESPONSES:
            raise ConnectionError("submission_scan_incomplete")
        for response in responses:
            if not isinstance(response, dict):
                raise ConnectionError("submission_scan_incomplete")
            answers = response.get("answers") or {}
            if not isinstance(answers, dict):
                raise ConnectionError("submission_scan_incomplete")
            text = [value for answer in answers.values() for value in _text_answers(answer)]
            if assignment_label not in text:
                continue
            for answer in answers.values():
                for uploaded in _file_answers(answer):
                    file_id = str(uploaded.get("fileId") or "")
                    if not _ID.fullmatch(file_id):
                        raise ConnectionError("submission_scan_incomplete")
                    if file_id not in file_ids:
                        file_ids.append(file_id)
                    if len(file_ids) > MAX_FILES:
                        raise ConnectionError("submission_scan_incomplete")
        cursor = page.get("nextPageToken")
        if not cursor:
            break
        if not isinstance(cursor, str) or cursor in cursors or len(cursors) >= 20:
            raise ConnectionError("submission_scan_incomplete")
        cursors.add(cursor)
    files = []
    for file_id in sorted(file_ids):
        meta = drive_reader.metadata(file_id)
        if (not isinstance(meta, dict) or meta.get("id") != file_id
                or meta.get("trashed") is not False
                or not isinstance(meta.get("name"), str)
                or not meta.get("modifiedTime")):
            raise ConnectionError("submission_scan_incomplete")
        files.append(meta)
    return files, count


def read_assignment(config, parsed, forms_token, drive_token):
    """Read one assignment with separate narrow Forms and Drive tokens."""
    api_form = form_id(parsed, config)
    files, responses_checked = collect(
        FormsReader(forms_token), DriveReader(drive_token),
        api_form, parsed["assignment_label"])
    return {"files": files, "responses_checked": responses_checked,
            "source_id": source_id(parsed),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "external_writes": 0}
