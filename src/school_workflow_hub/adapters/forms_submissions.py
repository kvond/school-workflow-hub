"""Assignment submission discovery, verified against the approved Drive root.

The lesson's Google Form supplies current FileUpload references. The exact
approved lesson folder supplements that index because earlier routing moved
some valid uploads after submission. Off-root Form references stay explicit
coverage gaps and never suppress verified work or become missing-work claims.

This module is read-only. It creates/moves no files and submits no Forms.
"""
from datetime import datetime
import re

from .hosted_agenda import ConnectionError, service_token
from .submission_inventory import (
    DRIVE_SCOPE, FOLDER, SHORTCUT, DriveReader, locate_files_under_root,
)

FORMS_SCOPE = "https://www.googleapis.com/auth/forms.responses.readonly"
FORM_ID = re.compile(r"[A-Za-z0-9_-]{20,256}")
FILE_ID = re.compile(r"[A-Za-z0-9_-]{20,128}")
MAX_FORM_PAGES = 20
MAX_RESPONSES = 5000
MAX_FILES = 500


class FormsReader:
    def __init__(self, token):
        import requests
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + token

    def responses(self, form_id, cursor=None):
        if not FORM_ID.fullmatch(form_id or ""):
            raise ConnectionError("submission_form_not_configured")
        params = {
            "pageSize": 500,
            "fields": "nextPageToken,responses(responseId,createTime,lastSubmittedTime,answers)",
        }
        if cursor:
            params["pageToken"] = cursor
        try:
            response = self.session.get(
                "https://forms.googleapis.com/v1/forms/" + form_id + "/responses",
                params=params, timeout=(5, 20), allow_redirects=False)
            if response.status_code in (401, 403):
                raise ConnectionError("submission_form_access_required")
            if response.status_code == 404:
                raise ConnectionError("submission_form_not_found")
            if response.status_code == 429:
                raise ConnectionError("submission_form_rate_limited")
            if response.status_code != 200:
                raise ConnectionError("submission_form_unavailable")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConnectionError("submission_form_response_invalid")
            return payload
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError("submission_form_unavailable") from None


def _timestamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 60:
        raise ConnectionError("submission_form_response_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ConnectionError("submission_form_response_invalid") from None
    return value


def _answers(response):
    answers = response.get("answers")
    if answers is None:
        return {}
    if not isinstance(answers, dict) or len(answers) > 200:
        raise ConnectionError("submission_form_response_invalid")
    return answers


def _text_values(answer):
    block = answer.get("textAnswers")
    if block is None:
        return []
    values = block.get("answers") if isinstance(block, dict) else None
    if not isinstance(values, list) or len(values) > 100:
        raise ConnectionError("submission_form_response_invalid")
    result = []
    for item in values:
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, str) or len(value) > 2000:
            raise ConnectionError("submission_form_response_invalid")
        result.append(value)
    return result


def _file_values(answer, submitted_at):
    block = answer.get("fileUploadAnswers")
    if block is None:
        return []
    values = block.get("answers") if isinstance(block, dict) else None
    if not isinstance(values, list) or len(values) > 50:
        raise ConnectionError("submission_form_response_invalid")
    result = []
    for item in values:
        if not isinstance(item, dict):
            raise ConnectionError("submission_form_response_invalid")
        file_id = item.get("fileId")
        name = item.get("fileName")
        mime = item.get("mimeType")
        if (not isinstance(file_id, str) or not FILE_ID.fullmatch(file_id)
                or not isinstance(name, str) or not 1 <= len(name) <= 400
                or not isinstance(mime, str) or not 1 <= len(mime) <= 200):
            raise ConnectionError("submission_form_response_invalid")
        result.append({"id": file_id, "form_name": name, "form_mime_type": mime,
                       "submitted_at": submitted_at})
    return result


def response_file_refs(reader, form_id, assignment_label=None):
    """Return FileUpload references from all complete responses.

    For an assignment-specific Form, assignment_label is None and every response
    belongs to that assignment. During migration, the older shared router passes
    an exact assignment label and only matching responses are accepted.
    """
    if assignment_label is not None and (
            not isinstance(assignment_label, str) or not 1 <= len(assignment_label) <= 200):
        raise ConnectionError("submission_assignment_required")
    cursor, cursors, pages, checked, matched = None, set(), 0, 0, 0
    refs = {}
    while True:
        pages += 1
        if pages > MAX_FORM_PAGES:
            raise ConnectionError("submission_form_scan_incomplete")
        payload = reader.responses(form_id, cursor)
        responses = payload.get("responses", [])
        if not isinstance(responses, list):
            raise ConnectionError("submission_form_response_invalid")
        checked += len(responses)
        if checked > MAX_RESPONSES:
            raise ConnectionError("submission_form_scan_incomplete")
        for response in responses:
            if not isinstance(response, dict):
                raise ConnectionError("submission_form_response_invalid")
            submitted_at = _timestamp(
                response.get("lastSubmittedTime") or response.get("createTime") or "")
            answers = _answers(response)
            text = []
            for answer in answers.values():
                if not isinstance(answer, dict):
                    raise ConnectionError("submission_form_response_invalid")
                text.extend(_text_values(answer))
            if assignment_label is not None and assignment_label not in text:
                continue
            matched += 1
            for answer in answers.values():
                for ref in _file_values(answer, submitted_at):
                    prior = refs.get(ref["id"])
                    if prior and (prior["form_name"], prior["form_mime_type"]) != (
                            ref["form_name"], ref["form_mime_type"]):
                        raise ConnectionError("submission_form_response_invalid")
                    if not prior or ref["submitted_at"] > prior["submitted_at"]:
                        refs[ref["id"]] = ref
                    if len(refs) > MAX_FILES:
                        raise ConnectionError("submission_form_scan_incomplete")
        cursor = payload.get("nextPageToken")
        if not cursor:
            break
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
            raise ConnectionError("submission_form_scan_incomplete")
        cursors.add(cursor)
    return refs, {"pages": pages, "responses_checked": checked,
                  "matching_responses": matched}



def historical_assignment_file_ids(records, class_key, assignment_label):
    """Recorded file IDs that can be conservatively tied to this dated lesson.

    Current submissions are still selected by the exact shared-Form label. This
    helper only recovers already-recorded historical evidence. Forensics has a
    stable workbook convention (FNN...) that corresponds to the Agenda Wk NN;
    other courses are included only when the recorded assignment title itself
    clearly appears in the Agenda label.
    """
    if not isinstance(records, dict) or not isinstance(assignment_label, str):
        return []
    keys = set()
    normalized_label = re.sub(r"[^a-z0-9]+", " ", assignment_label.lower()).strip()
    week = re.search(r"\bWk\s*0?(\d{1,2})\b", assignment_label)
    for row in records.get("assignments", []) or []:
        if row.get("class_key") != class_key:
            continue
        assignment_key = str(row.get("assignment_key") or "")
        title = re.sub(r"[^a-z0-9]+", " ", str(row.get("assignment_title") or "").lower()).strip()
        title_match = bool(title and len(title) >= 8 and title in normalized_label)
        forensics_week_match = bool(
            class_key == "forensics" and week
            and assignment_key.startswith("forensics:F" + f"{int(week.group(1)):02d}"))
        if title_match or forensics_week_match:
            keys.add(assignment_key)
    ids = []
    for row in records.get("submissions", []) or []:
        if row.get("class_key") != class_key or row.get("assignment_key") not in keys:
            continue
        file_id = str(row.get("source_file_id") or "")
        if FILE_ID.fullmatch(file_id) and file_id not in ids:
            ids.append(file_id)
        if len(ids) >= MAX_FILES:
            break
    return ids


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _assignment_parts(label):
    match = re.search(r"\bWk\s*0?(\d{1,2})\s*[—–-]\s*(.+)$", str(label or ""))
    if not match:
        return None, None
    return int(match.group(1)), _norm(match.group(2))


def _folder_files_for_assignment(drive, course_root_id, assignment_label):
    """Files from the exact approved week/lesson folder beneath a course root.

    The course root comes from the Agenda TurnIn Map. We accept a child folder
    only when both its Wk number and normalized topic exactly match the Agenda
    assignment label; no student filename is used to establish membership.
    """
    week, topic = _assignment_parts(assignment_label)
    if not week or not topic or not FILE_ID.fullmatch(str(course_root_id or "")):
        return {}, 0, None
    candidates, cursor, cursors, pages = [], None, set(), 0
    while True:
        pages += 1
        if pages > 8:
            raise ConnectionError("submission_scan_incomplete")
        page = drive.children(course_root_id, cursor)
        if page.get("incompleteSearch") is True or not isinstance(page.get("files"), list):
            raise ConnectionError("submission_scan_incomplete")
        for item in page["files"]:
            if item.get("trashed") is not False or item.get("mimeType") != FOLDER:
                continue
            name = str(item.get("name") or "")
            match = re.fullmatch(
                r"📥?\s*Wk\s*0?(\d{1,2})\s*[—–-]\s*(.+?)\s*[—–-]\s*\d{4}-\d{2}-\d{2}",
                name)
            if match and int(match.group(1)) == week and _norm(match.group(2)) == topic:
                candidates.append(item["id"])
        cursor = page.get("nextPageToken")
        if not cursor:
            break
        if cursor in cursors:
            raise ConnectionError("submission_scan_incomplete")
        cursors.add(cursor)
    if len(candidates) > 1:
        raise ConnectionError("submission_lesson_mapping_required")
    if not candidates:
        return {}, pages, None

    folder_id = candidates[0]
    files, queue, seen, scan_pages = {}, [folder_id], set(), 0
    while queue:
        parent = queue.pop(0)
        if parent in seen or len(seen) >= 20:
            raise ConnectionError("submission_scan_incomplete")
        seen.add(parent)
        cursor, cursors = None, set()
        while True:
            scan_pages += 1
            if scan_pages > 30:
                raise ConnectionError("submission_scan_incomplete")
            page = drive.children(parent, cursor)
            if page.get("incompleteSearch") is True or not isinstance(page.get("files"), list):
                raise ConnectionError("submission_scan_incomplete")
            for item in page["files"]:
                if item.get("trashed") is not False:
                    raise ConnectionError("submission_scan_incomplete")
                if item.get("mimeType") == FOLDER:
                    queue.append(item["id"])
                elif item.get("mimeType") == SHORTCUT:
                    raise ConnectionError("submission_shortcut_needs_review")
                elif item.get("mimeType") != "application/vnd.google-apps.form":
                    files[item["id"]] = item
                    if len(files) > MAX_FILES:
                        raise ConnectionError("submission_scan_incomplete")
            cursor = page.get("nextPageToken")
            if not cursor:
                break
            if cursor in cursors:
                raise ConnectionError("submission_scan_incomplete")
            cursors.add(cursor)
    return files, pages + scan_pages, folder_id

def collect_assignment_submissions(config, assignment_label, *, form_id=None,
                                   course_root_id=None, known_file_ids=()):
    """Read form references and return only files verified below the approved root.

    known_file_ids are assignment-bound file IDs already present in the grading
    workbook. They are historical evidence only. Current intake comes from the
    lesson Form plus the exact approved lesson folder; recorded files elsewhere
    remain explicit legacy coverage.
    """
    configured_form_id = str(config.get("HUB_TURNIN_FORM_ID", ""))
    form_id = str(form_id or "")
    dedicated = bool(FORM_ID.fullmatch(form_id))
    if not dedicated:
        form_id = configured_form_id
    root_id = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", ""))
    service_email = str(config.get("HUB_READER_SERVICE_ACCOUNT", ""))
    if not FORM_ID.fullmatch(form_id):
        raise ConnectionError("submission_form_not_configured")
    if not FILE_ID.fullmatch(root_id):
        raise ConnectionError("submission_root_not_configured")

    form_token = service_token(service_email, FORMS_SCOPE)
    refs, evidence = response_file_refs(
        FormsReader(form_token), form_id, None if dedicated else assignment_label)
    evidence = {**evidence,
                "form_source": "assignment_specific" if dedicated else "shared_router"}

    known = []
    for file_id in known_file_ids:
        file_id = str(file_id or "")
        if FILE_ID.fullmatch(file_id) and file_id not in known:
            known.append(file_id)
        if len(known) > MAX_FILES:
            raise ConnectionError("submission_scan_incomplete")

    # Current Form response IDs are verified against the approved root. A
    # Form reference outside the root is a coverage gap, not a reason to hide
    # every other valid submission. The exact week/lesson folder is also read
    # because historical Google Form routing moved some valid submissions away
    # from the Form's own File-responses folder.
    candidates = list(refs)
    legacy_known = [file_id for file_id in known if file_id not in refs]
    course_root_id = str(course_root_id or "")
    if not candidates and not FILE_ID.fullmatch(course_root_id):
        return [], {**evidence, "root_folders_checked": 0,
                    "root_verified_files": 0, "known_recorded_files": len(known),
                    "legacy_outside_root_files": len(legacy_known),
                    "form_refs_outside_root": 0, "lesson_folder_files": 0,
                    "lesson_folder_id": None}

    drive_token = service_token(service_email, DRIVE_SCOPE)
    drive = DriveReader(drive_token)
    root = drive.metadata(root_id)
    if root.get("trashed") is not False or root.get("mimeType") != FOLDER:
        raise ConnectionError("submission_folder_inactive")

    found, folders_checked = ({}, 0)
    if candidates:
        found, folders_checked = locate_files_under_root(drive, candidates, root_id)
    missing_form = sorted(set(refs) - set(found))

    folder_files, lesson_checked, lesson_folder_id = ({}, 0, None)
    if FILE_ID.fullmatch(course_root_id):
        course_found, course_checked = locate_files_under_root(
            drive, [course_root_id], root_id)
        folders_checked += course_checked
        if course_root_id in course_found:
            folder_files, lesson_checked, lesson_folder_id = _folder_files_for_assignment(
                drive, course_root_id, assignment_label)
            folders_checked += lesson_checked

    merged = dict(folder_files)
    merged.update(found)
    files = []
    for file_id in sorted(merged):
        item = merged[file_id]
        if item.get("trashed") is not False or item.get("mimeType") in {FOLDER, SHORTCUT}:
            raise ConnectionError("submission_scan_incomplete")
        name = item.get("name")
        modified = item.get("modifiedTime")
        if (not isinstance(name, str) or not name or len(name) > 400
                or not isinstance(modified, str) or not modified):
            raise ConnectionError("submission_scan_incomplete")
        ref = refs.get(file_id)
        files.append({"id": file_id, "name": name, "mimeType": item.get("mimeType"),
                      "modifiedTime": modified, "version": item.get("version"),
                      "submitted_at": ref.get("submitted_at") if ref else None,
                      "assignment_evidence": "form_response" if ref else "lesson_folder"})
    return files, {**evidence, "root_folders_checked": folders_checked,
                   "root_verified_files": len(files), "known_recorded_files": len(known),
                   "legacy_outside_root_files": len(legacy_known),
                   "form_refs_outside_root": len(missing_form),
                   "lesson_folder_files": len(folder_files),
                   "lesson_folder_id": lesson_folder_id}
