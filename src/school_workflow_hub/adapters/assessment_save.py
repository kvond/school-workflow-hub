"""Assessment saves into the private grading workbook, bound to a fresh capture.

The server never trusts the packet's memory of the submission: it re-exports
the document itself, and a content hash mismatch means the student edited the
file after the review, so the save is refused instead of recording an
assessment against older evidence. Appends are idempotent through the
deterministic record id and the cloud claim; earlier rows for the same student
and assignment are marked superseded, never deleted or rewritten. Readback
uses the same strict reader the dashboard uses, so a save that the dashboard
could not display correctly fails here first.
"""
from datetime import datetime, timezone
import hashlib
import io
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET

from .firestore_state import configured_store
from .google_sheets import SheetsReader, SheetsReadError
from .grading_records import HEADERS, _column, _sheet_map, configured, read_records
from .hosted_agenda import (ConnectionError, _identity_configuration, authorize,
                            service_token)
from ..core.cloud_state import CloudState, Conflict
from ..core.runs import content_hash
from ..workflows import assessment_records

CAPTURE_VERSION = "hub-assessment-capture/v1"
SAVE_VERSION = "hub-assessment-save/v1"
DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
NATIVE_EXPORTABLE = {"application/vnd.google-apps.presentation",
                     "application/vnd.google-apps.document"}
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EXPORTABLE = NATIVE_EXPORTABLE | {PPTX}
NUMERIC = ("content_score", "revision_score")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _drive(token, path, params, *, binary=False):
    import requests as http
    try:
        with http.Session() as session:
            session.trust_env = False
            response = session.get("https://www.googleapis.com/drive/v3/" + path,
                                   params=params,
                                   headers={"Authorization": "Bearer " + token},
                                   timeout=(5, 30), allow_redirects=False)
            if response.status_code in (403, 404):
                raise ConnectionError("assessment_source_access_required")
            if response.status_code != 200:
                raise ConnectionError("assessment_source_unavailable")
            return response.content if binary else response.json()
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError("assessment_source_unavailable") from None


def _batch(source, token, requests):
    import requests as http
    try:
        with http.Session() as session:
            session.trust_env = False
            response = session.post(
                "https://sheets.googleapis.com/v4/spreadsheets/" + source + ":batchUpdate",
                headers={"Authorization": "Bearer " + token}, json={"requests": requests},
                timeout=(5, 30), allow_redirects=False)
            if response.status_code != 200:
                code = {400: "assessment_write_request_invalid",
                        401: "assessment_writer_identity_failed",
                        403: "assessment_writer_permission_denied",
                        429: "assessment_write_rate_limited"}.get(
                            response.status_code, "assessment_write_unavailable")
                raise ConnectionError(code)
            return response.json()
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError("assessment_write_uncertain") from None


def _pptx_text(raw):
    """Bounded text extraction from slide shapes and PowerPoint speaker notes."""
    if not raw or len(raw) > 20_000_000:
        raise ConnectionError("assessment_source_too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 1200 or sum(i.file_size for i in infos) > 20_000_000:
                raise ConnectionError("assessment_source_too_large")
            pattern = re.compile(
                r"^ppt/(slides/slide|notesSlides/notesSlide)(\d+)\.xml$")
            selected = []
            for info in infos:
                match = pattern.fullmatch(info.filename)
                if match:
                    selected.append((int(match.group(2)),
                                     0 if match.group(1).startswith("slides/") else 1,
                                     info))
            if not selected:
                raise ConnectionError("assessment_source_unsupported")
            lines = []
            for number, kind, info in sorted(selected):
                if info.file_size > 2_000_000:
                    raise ConnectionError("assessment_source_too_large")
                try:
                    root = ET.fromstring(archive.read(info))
                except ET.ParseError:
                    raise ConnectionError("assessment_source_unsupported") from None
                words = [node.text for node in root.iter(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}t")
                         if isinstance(node.text, str) and node.text]
                if words:
                    label = "Slide" if kind == 0 else "Notes"
                    lines.append(f"[{label} {number}]")
                    lines.extend(words)
            text = "\n".join(lines).replace("\r\n", "\n").lstrip("\ufeff")
            if not text or len(text.encode("utf-8")) > 2_000_000:
                raise ConnectionError("assessment_source_too_large")
            return text
    except ConnectionError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        raise ConnectionError("assessment_source_unsupported") from None


def capture(assertion, file_id, config):
    """Authenticated fresh export of one submission: metadata, text, hash."""
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", file_id):
        raise ConnectionError("invalid_assessment_source")
    token = service_token(config.get("HUB_READER_SERVICE_ACCOUNT", ""), DRIVE_READ_SCOPE)
    meta = _drive(token, "files/" + file_id,
                  {"fields": "id,name,mimeType,trashed,modifiedTime,version",
                   "supportsAllDrives": "true"})
    if not isinstance(meta, dict) or meta.get("id") != file_id or meta.get("trashed") is not False:
        raise ConnectionError("assessment_source_unavailable")
    if meta.get("mimeType") not in EXPORTABLE:
        raise ConnectionError("assessment_source_unsupported")
    if meta["mimeType"] == PPTX:
        raw = _drive(token, "files/" + file_id, {"alt": "media",
                     "supportsAllDrives": "true"}, binary=True)
        text = _pptx_text(raw)
    else:
        raw = _drive(token, "files/" + file_id + "/export",
                     {"mimeType": "text/plain"}, binary=True)
        if not raw or len(raw) > 2_000_000:
            raise ConnectionError("assessment_source_too_large")
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").lstrip("﻿")
    return {"schema_version": CAPTURE_VERSION,
            "file": {"id": meta["id"], "name": str(meta.get("name", ""))[:200],
                     "mime_type": meta["mimeType"],
                     "modified_at": str(meta.get("modifiedTime", "")),
                     "version": str(meta.get("version", ""))},
            "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "captured_at": _now(), "external_writes": 0}


def _workbook(reader, source):
    """The four grading tables with live headers and 0-based grid positions."""
    sheets = _sheet_map(reader.read(source))
    ranges, geometry = [], {}
    for title, required in HEADERS.items():
        properties = sheets.get(title, {}).get("properties", {})
        grid = properties.get("gridProperties", {})
        rows = grid.get("rowCount") if isinstance(grid, dict) else None
        columns = grid.get("columnCount") if isinstance(grid, dict) else None
        if (type(rows) is not int or not 1 <= rows <= 10000 or type(columns) is not int
                or not len(required) <= columns <= 40):
            raise ConnectionError("assessment_workbook_layout")
        geometry[title] = {"sheet_id": properties["sheetId"], "rows": rows,
                           "columns": columns}
        ranges.append("'" + title + "'!A1:" + _column(columns) + str(rows))
    data = _sheet_map(reader.read_grading_tables(source, tuple(ranges)))
    result = {}
    for title, required in HEADERS.items():
        info = geometry[title]
        if data[title]["properties"]["sheetId"] != info["sheet_id"]:
            raise ConnectionError("assessment_workbook_changed")
        values = {}
        for block in data[title].get("data", []) or []:
            if not isinstance(block, dict) or block.get("startColumn", 0) != 0:
                raise ConnectionError("assessment_workbook_layout")
            start = block.get("startRow", 0)
            for offset, row in enumerate(block.get("rowData", []) or []):
                cells = row.get("values", []) if isinstance(row, dict) else []
                position = start + offset
                if position in values or not isinstance(cells, list):
                    raise ConnectionError("assessment_workbook_layout")
                values[position] = [str(c.get("formattedValue", "")) if isinstance(c, dict)
                                    else "" for c in cells]
        headers = list(values.get(0, []))
        headers += [""] * (info["columns"] - len(headers))
        used = [h for h in headers if h]
        if len(set(used)) != len(used) or not set(required) <= set(used):
            raise ConnectionError("assessment_workbook_headers")
        rows = []
        for position in sorted(values):
            if not position:
                continue
            text = values[position] + [""] * (info["columns"] - len(values[position]))
            if any(text):
                rows.append((position, {h: text[i] for i, h in enumerate(headers) if h}))
        result[title] = {"sheet_id": info["sheet_id"], "headers": headers, "rows": rows}
    return result


def _set_cell(table, position, header, value):
    return {"updateCells": {
        "start": {"sheetId": table["sheet_id"], "rowIndex": position,
                  "columnIndex": table["headers"].index(header)},
        "rows": [{"values": [{"userEnteredValue": {"stringValue": value}}]}],
        "fields": "userEnteredValue"}}


def _append(table, row, *, numeric=()):
    return {"appendCells": {"sheetId": table["sheet_id"],
                            "rows": [assessment_records.aligned_cells(
                                table["headers"], row, numeric=numeric)],
                            "fields": "userEnteredValue"}}


def _matching_rows(table, packet):
    identity = packet["identity"]
    matches = []
    for position, row in table["rows"]:
        if row.get("assignment_key") != packet["assignment_key"]:
            continue
        if identity["student_id"]:
            same = row.get("student_id") == identity["student_id"]
        else:
            same = (not row.get("student_id")
                    and row.get("learner_key") == identity["learner_key"])
        if same:
            matches.append((position, row))
    return matches


def _verify_readback(records, rows, superseded_ids):
    rid = rows["record_id"]
    expected = rows["submission"]
    by_id = {row["record_id"]: row for row in records["submissions"]}
    saved = by_id.get(rid)
    if (saved is None or saved["status"] != "assessed"
            or saved.get("source_sha256") != expected["source_sha256"]
            or saved.get("content_score_exact") != expected["content_score_exact"]
            or len(saved["answers"]) != len(rows["answers"])):
        raise ConnectionError("assessment_readback_failed")
    for score in NUMERIC:
        want = expected[score]
        got = saved[score]
        if (want is None) != (got is None) or (want is not None
                                               and abs(got - want) > 1e-9):
            raise ConnectionError("assessment_readback_failed")
    for old in superseded_ids:
        row = by_id.get(old)
        if row is None or row["status"] != "superseded" or row.get("superseded_by") != rid:
            raise ConnectionError("assessment_readback_failed")
    return {"record_id": rid, "status": saved["status"],
            "content_score": saved["content_score"],
            "content_score_exact": saved.get("content_score_exact", ""),
            "revision_score": saved["revision_score"],
            "revision_score_exact": saved.get("revision_score_exact", ""),
            "answers": len(saved["answers"]),
            "included_in_counts": saved["included_in_counts"],
            "superseded": sorted(superseded_ids)}


def _request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", value):
        raise ValueError("invalid_assessment_save")
    return value


def save(assertion, payload, config):
    """Validate, re-capture, claim, append + supersede, and read back one packet."""
    if (not isinstance(payload, dict) or set(payload) != {"request_id", "packet"}
            or not isinstance(payload.get("packet"), dict)):
        if isinstance(payload, dict) and set(payload) == {"request_id", "summary"}:
            return _save_summary(assertion, payload, config)
        raise ValueError("invalid_assessment_save")
    request_id = _request_id(payload["request_id"])
    packet = payload["packet"]
    source = packet.get("source") if isinstance(packet.get("source"), dict) else {}
    fresh = capture(assertion, source.get("file_id", ""), config)
    if fresh["sha256"] != source.get("sha256"):
        # The document changed between review and save: back to reassessment,
        # never an assessment recorded against evidence that no longer exists.
        raise Conflict("submission_changed_reassess")
    assessment_records.validate_packet(packet, fresh["text"])
    result = assessment_records.completion(packet)
    if not configured(config):
        raise ConnectionError("grading_records_not_connected")
    workbook_id = config["HUB_GRADING_SPREADSHEET_ID"]
    service_account = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    try:
        reader = SheetsReader(service_token(service_account))
        workbook = _workbook(reader, workbook_id)
    except SheetsReadError:
        raise ConnectionError("assessment_workbook_unavailable") from None
    assignments = {row.get("assignment_key"): row.get("class_key")
                   for _, row in workbook["Assignments"]["rows"]}
    if assignments.get(packet["assignment_key"]) != packet["class_key"]:
        raise ValueError("assessment_assignment_unknown")
    submissions = workbook["Submissions"]
    matches = _matching_rows(submissions, packet)
    rid = assessment_records.record_id(packet)
    existing = next((row for _, row in matches if row.get("record_id") == rid), None)
    supersede = [(position, row) for position, row in matches
                 if row.get("record_id") != rid and row.get("status") != "superseded"]
    latest_prior = max(supersede, key=lambda m: m[1].get("checked_at", ""), default=None)
    rows = assessment_records.build_rows(
        packet, result, fresh, now=_now(),
        prior_score_exact=latest_prior[1].get("content_score_exact", "") if latest_prior else "",
        prior_hold_reason=latest_prior[1].get("hold_reason", "") if latest_prior else "")
    if existing is not None and not supersede:
        if existing.get("source_sha256") != fresh["sha256"]:
            raise Conflict("assessment_record_conflict")
        records = read_records(assertion, config)
        receipt = _verify_readback(records, rows, [])
        return {"status": "already_saved", "unchanged_rerun": True, **receipt,
                "source": {"file_id": fresh["file"]["id"],
                           "modified_at": fresh["file"]["modified_at"],
                           "version": fresh["file"]["version"],
                           "sha256": fresh["sha256"]},
                "readback_verified": True}
    store = CloudState(configured_store(config))
    task = store.create_task({"workflow": "assessment-save",
                              "class_key": packet["class_key"],
                              "assignment_key": content_hash({
                                  "assignment": packet["assignment_key"],
                                  "identity": assessment_records.record_identity(packet)})[:40]})
    claim = store.claim(task, owner="assess-" + uuid.uuid4().hex, request_id=request_id,
                        input_hash=content_hash(packet), ttl_seconds=300)
    requests = []
    if existing is None:
        requests.append(_append(submissions, rows["submission"], numeric=NUMERIC))
        answers_table = workbook["Answers"]
        for answer in rows["answers"]:
            requests.append(_append(answers_table, answer))
    superseded_ids = []
    for position, row in supersede:
        superseded_ids.append(row["record_id"])
        requests.append(_set_cell(submissions, position, "status", "superseded"))
        requests.append(_set_cell(submissions, position, "superseded_by", rid))
    if requests and not claim.completed:
        token = service_token(service_account, WRITE_SCOPE)
        _batch(workbook_id, token, requests)
    records = read_records(assertion, config)
    receipt = _verify_readback(records, rows, superseded_ids)
    if not claim.completed:
        store.finish(claim, records={"receipt": {**receipt,
                                                 "source_sha256": fresh["sha256"],
                                                 "saved_at": _now()}})
    return {"status": "saved", "unchanged_rerun": claim.completed and not requests,
            **receipt,
            "source": {"file_id": fresh["file"]["id"],
                       "modified_at": fresh["file"]["modified_at"],
                       "version": fresh["file"]["version"], "sha256": fresh["sha256"]},
            "workbook_revision": records["source_revision"],
            "readback_verified": True}


def _save_summary(assertion, payload, config):
    """Upsert one class-summary row; the verified sample is the caller's duty."""
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    request_id = _request_id(payload["request_id"])
    row = assessment_records.summary_row(payload["summary"], now=_now())
    if not configured(config):
        raise ConnectionError("grading_records_not_connected")
    workbook_id = config["HUB_GRADING_SPREADSHEET_ID"]
    service_account = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    try:
        reader = SheetsReader(service_token(service_account))
        workbook = _workbook(reader, workbook_id)
    except SheetsReadError:
        raise ConnectionError("assessment_workbook_unavailable") from None
    table = workbook["Class summaries"]
    match = next((position for position, existing in table["rows"]
                  if existing.get("class_key") == row["class_key"]
                  and existing.get("section_id") == row["section_id"]), None)
    store = CloudState(configured_store(config))
    task = store.create_task({"workflow": "assessment-summary",
                              "class_key": row["class_key"],
                              "assignment_key": "section-" + row["section_id"]})
    claim = store.claim(task, owner="summary-" + uuid.uuid4().hex, request_id=request_id,
                        input_hash=content_hash(row["summary"] + row["coverage"]),
                        ttl_seconds=300)
    if not claim.completed:
        token = service_token(service_account, WRITE_SCOPE)
        if match is None:
            _batch(workbook_id, token, [_append(table, row)])
        else:
            _batch(workbook_id, token,
                   [_set_cell(table, match, header, row[header])
                    for header in ("section_name", "summary", "coverage", "checked_at")])
    records = read_records(assertion, config)
    saved = next((s for s in records["summaries"]
                  if s["class_key"] == row["class_key"]
                  and s["section_id"] == row["section_id"]), None)
    if saved is None or saved["summary"] != row["summary"] or saved["coverage"] != row["coverage"]:
        raise ConnectionError("assessment_readback_failed")
    if not claim.completed:
        store.finish(claim, records={"receipt": {"class_key": row["class_key"],
                                                 "section_id": row["section_id"],
                                                 "saved_at": _now()}})
    return {"status": "saved", "kind": "class_summary", "class_key": row["class_key"],
            "section_id": row["section_id"],
            "submitted_students": saved["submitted_students"],
            "submission_records": saved["submission_records"],
            "readback_verified": True}
