"""Read the teacher's private grading workbook; never grade, post, or persist.

Unlike dated lesson results, this read model spans assignments and sections.
The Sheet is the record; source documents remain links. No disk/fixture fallback.
"""
from datetime import datetime, timezone
import math
import re

from .google_sheets import SheetsReader, SheetsReadError
from .roster_matching import read_roster_snapshot
from .hosted_agenda import (ConnectionError, _identity_configuration, authorize,
                            service_token)
from ..core.cloud_state import content_hash

SCHEMA = "hub-grading-records/v1"
CLASSES = {"biology", "forensics", "anatomy"}
EXCLUDED = {"identity_unverified", "superseded"}
# The historical NOTES checkpoint is an explicit saved value. Do not infer a
# checkpoint from free text, answer labels, scores, or a plausible final alias.
ASSESSMENT_STAGES = {"initial": "initial", "final": "final",
                     "initial_NOTES_checkpoint": "initial"}
# Only fixed reader codes may cross the authenticated HTTP boundary. Never
# forward an arbitrary exception message, provider response, or cell value.
SHEETS_READ_ERRORS = frozenset({
    "authentication_required", "access_denied", "source_not_found",
    "source_unavailable", "source_rate_limited", "source_transient_failure",
    "response_limit", "invalid_response",
    "invalid_credential", "unsupported_read_endpoint", "invalid_spreadsheet_id",
    "invalid_ranges", "invalid_range", "range_limit",
})
HEADERS = {
    "Assignments": ("assignment_key", "class_key", "assignment_title"),
    "Submissions": ("record_id", "assignment_key", "section_id", "section_name",
        "student_id", "display_name", "source_url", "source_revision", "status",
        "content_score", "revision_score", "hold_reason", "feedback", "checked_at",
        "ic_posting_status"),
    "Answers": ("record_id", "question_id", "stage", "response", "conceptual_level",
        "explanation_level"),
    "Class summaries": ("class_key", "section_id", "section_name", "summary",
        "coverage", "checked_at"),
}


def configured(config):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{20,128}",
                            config.get("HUB_GRADING_SPREADSHEET_ID", "")))


def _column(number):
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def _sheet_map(response):
    if not isinstance(response, dict) or not isinstance(response.get("sheets"), list):
        raise ConnectionError("grading_workbook_layout_required")
    result, ids = {}, set()
    for sheet in response["sheets"]:
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        if (not isinstance(properties, dict) or not isinstance(properties.get("title"), str)
                or type(properties.get("sheetId")) is not int or properties["sheetId"] < 0
                or properties["title"] in result or properties["sheetId"] in ids):
            raise ConnectionError("grading_workbook_layout_required")
        result[properties["title"]] = sheet
        ids.add(properties["sheetId"])
    return result


def _tables(reader, source):
    sheets = _sheet_map(reader.read(source))
    ranges = []
    for title, required in HEADERS.items():
        properties = sheets.get(title, {}).get("properties", {})
        grid = properties.get("gridProperties", {})
        if not isinstance(grid, dict):
            raise ConnectionError("grading_workbook_layout_required")
        rows, columns = grid.get("rowCount"), grid.get("columnCount")
        if (type(rows) is not int or not 1 <= rows <= 10000 or
                type(columns) is not int or not len(required) <= columns <= 40):
            raise ConnectionError("grading_workbook_layout_required")
        ranges.append("'" + title + "'!A1:" + _column(columns) + str(rows))
    data = _sheet_map(reader.read_grading_tables(source, tuple(ranges)))
    if set(data) != set(HEADERS):
        raise ConnectionError("grading_workbook_changed")
    result, text_bytes = {}, 0
    for title, required in HEADERS.items():
        properties = sheets[title]["properties"]
        grid = properties["gridProperties"]
        rows, columns = grid["rowCount"], grid["columnCount"]
        if data[title]["properties"]["sheetId"] != properties["sheetId"]:
            raise ConnectionError("grading_workbook_changed")
        blocks = data[title].get("data", [])
        if not isinstance(blocks, list):
            raise ConnectionError("grading_workbook_layout_required")
        values = {}
        for block in blocks:
            if not isinstance(block, dict):
                raise ConnectionError("grading_workbook_layout_required")
            start, left = block.get("startRow", 0), block.get("startColumn", 0)
            row_data = block.get("rowData", [])
            if (type(start) is not int or not 0 <= start < rows or type(left) is not int
                    or left != 0 or not isinstance(row_data, list)):
                raise ConnectionError("grading_workbook_layout_required")
            for offset, row in enumerate(row_data):
                position = start + offset
                cells = row.get("values", []) if isinstance(row, dict) else None
                if (position in values or not position < rows or not isinstance(cells, list)
                        or len(cells) > columns or any(not isinstance(cell, dict) for cell in cells)):
                    raise ConnectionError("grading_workbook_layout_required")
                values[position] = cells
        headers = [str(cell.get("formattedValue", "")) for cell in values.get(0, [])]
        headers += [""] * (columns - len(headers))
        used = [h for h in headers if h]
        if len(set(used)) != len(used) or not set(required) <= set(used):
            raise ConnectionError("grading_workbook_headers_required")
        score_columns = {i for i, header in enumerate(headers) if title == "Submissions"
                         and header in {"content_score", "revision_score"}}
        result[title] = []
        for position, cells in sorted(values.items()):
            text = [str(cell.get("formattedValue", "")) for cell in cells]
            if position:
                for index in score_columns:
                    effective = cells[index].get("effectiveValue", {}) if index < len(cells) else {}
                    if not isinstance(effective, dict):
                        raise ConnectionError("grading_workbook_layout_required")
                    number = effective.get("numberValue")
                    if type(number) in (int, float):
                        text[index] = str(number)
            text += [""] * (columns - len(text))
            text_bytes += sum(len(value.encode("utf-8")) for value in text)
            if text_bytes > 4_000_000:
                raise ConnectionError("grading_workbook_limit")
            if position and any(text):
                result[title].append({h: text[i] for i, h in enumerate(headers) if h})
    fresh = _sheet_map(reader.read(source))
    if any(fresh.get(title, {}).get("properties") != sheets[title]["properties"] for title in HEADERS):
        raise ConnectionError("grading_workbook_changed")
    return result


def _unique(rows, key):
    result = {}
    for row in rows:
        value = key(row)
        if not value or value in result:
            raise ConnectionError("grading_record_conflict")
        result[value] = row
    return result


def _score(value):
    if value == "":
        return None
    try:
        parsed = float(value)
        if not math.isfinite(parsed) or not 0 <= parsed <= 10:
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise ConnectionError("grading_score_invalid") from None


def _checkpoint(row):
    source = row.get("assessment_stage", "")
    row["assessment_stage_source"] = source
    stage = ASSESSMENT_STAGES.get(source, "") if isinstance(source, str) else ""
    row["assessment_stage"] = stage
    # Intake has verified identity but has not assessed a checkpoint yet. Keep
    # those unscored rows eligible for their first assessment and writer readback.
    pending = (source == "" and row["status"] == "roster_matched"
               and row["content_score"] == "" and row["revision_score"] == "")
    row["assessment_stage_review_reason"] = "" if stage or pending else "assessment_stage_unverified"
    if row["assessment_stage_review_reason"]:
        # Keep the source's existing hold and history, but never make an unknown
        # checkpoint an actionable/countable assessment in the read projection.
        row["hold_reason"] = "; ".join(filter(None, (
            row["hold_reason"], "assessment_stage_unverified")))


def project(tables):
    assignments = _unique(tables["Assignments"], lambda r: r["assignment_key"])
    if any(r["class_key"] not in CLASSES for r in assignments.values()):
        raise ConnectionError("grading_class_invalid")
    records = _unique([dict(row) for row in tables["Submissions"]], lambda r: r["record_id"])
    for row in records.values():
        assignment = assignments.get(row["assignment_key"])
        if assignment is None:
            raise ConnectionError("grading_assignment_unresolved")
        row.update(class_key=assignment["class_key"], assignment_title=assignment["assignment_title"], answers=[])
        _checkpoint(row)
        roster_key = row.get("learner_key", "") if row["status"] in {"assessed", "roster_matched"} else ""
        row["count_key"] = ("ic:" + row["student_id"] if row["student_id"] else
                            "roster:" + roster_key if roster_key else "")
        row["included_in_counts"] = bool(row["count_key"] and row["section_id"]
            and row["status"] not in EXCLUDED and not row["assessment_stage_review_reason"])
        row["content_score"] = _score(row["content_score"])
        row["revision_score"] = _score(row["revision_score"])
        # Holds and conflicting identity records never become numeric zeroes.
        if row["hold_reason"] or row["status"] in EXCLUDED:
            row["content_score"] = row["revision_score"] = None
    native_matches = {}
    for row in records.values():
        if row["included_in_counts"] and row["student_id"] and row.get("learner_key"):
            scope = (row["class_key"], row["section_id"], row["learner_key"])
            if scope in native_matches and native_matches[scope] != row["student_id"]:
                raise ConnectionError("grading_identity_conflict")
            native_matches[scope] = row["student_id"]
    for row in records.values():
        native = native_matches.get((row["class_key"], row["section_id"], row.get("learner_key")))
        if row["included_in_counts"] and native:
            # Count an earlier roster-matched artifact with its explicitly linked
            # identity; never fill or alter the posting field student_id.
            row["count_key"] = "ic:" + native
    for answer in tables["Answers"]:
        if answer["record_id"] not in records:
            raise ConnectionError("grading_answer_unresolved")
        records[answer["record_id"]]["answers"].append(answer)
    summaries = _unique(tables["Class summaries"], lambda r: (r["class_key"], r["section_id"]))
    for (course, section), row in summaries.items():
        if course not in CLASSES or not section or not row["summary"] or len(row["summary"]) > 1600:
            raise ConnectionError("grading_summary_invalid")
        eligible = [r for r in records.values() if r["class_key"] == course and r["section_id"] == section and r["included_in_counts"]]
        row["submitted_students"] = len({r["count_key"] for r in eligible})
        row["submission_records"] = len(eligible)
    return {"schema_version": SCHEMA, "provenance": "source_read",
        "checked_at": datetime.now(timezone.utc).isoformat(), "source_revision": content_hash(tables),
        "assignments": list(assignments.values()), "submissions": list(records.values()),
        "summaries": list(summaries.values()), "external_writes": 0}


def read_records(assertion, config):
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    if not configured(config):
        raise ConnectionError("grading_records_not_connected")
    try:
        reader = SheetsReader(service_token(config.get("HUB_READER_SERVICE_ACCOUNT", "")))
        result = project(_tables(reader, config["HUB_GRADING_SPREADSHEET_ID"]))
        result.update(sections=[], sections_status="needs_review")
        try:
            roster = read_roster_snapshot(config)
            result["sections"] = roster["sections"]
            result["sections_status"] = "source_read" if roster["section_mapping_complete"] else "needs_review"
            if not roster["section_mapping_complete"]:
                result["sections_review_reason"] = "roster_section_mapping_required"
        except ConnectionError as error:
            code = str(error)
            result["sections_review_reason"] = code if re.fullmatch(r"[a-z_]{1,64}", code) else "roster_source_unavailable"
        return result
    except ConnectionError:
        raise
    except SheetsReadError as error:
        code = str(error)
        diagnostic = "grading_records_" + code if code in SHEETS_READ_ERRORS else "grading_records_unavailable"
        raise ConnectionError(diagnostic) from None
    except Exception:
        raise ConnectionError("grading_records_unavailable") from None
