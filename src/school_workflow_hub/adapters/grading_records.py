"""Read the teacher's private grading workbook; never grade, post, or persist.

Unlike dated lesson results, this read model spans assignments and sections.
The Sheet is the record; source documents remain links. No disk/fixture fallback.
"""
from datetime import datetime, timezone
import math
import re

from .google_sheets import SheetsReader, SheetsReadError
from .hosted_agenda import (ConnectionError, _identity_configuration, authorize,
                            service_token)
from ..core.cloud_state import content_hash

SCHEMA = "hub-grading-records/v1"
CLASSES = {"biology", "forensics", "anatomy"}
EXCLUDED = {"identity_unverified", "superseded"}
# Only fixed reader codes may cross the authenticated HTTP boundary. Never
# forward an arbitrary exception message, provider response, or cell value.
SHEETS_READ_ERRORS = frozenset({
    "authentication_required", "access_denied", "source_not_found",
    "source_unavailable", "response_limit", "invalid_response",
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


def _tables(reader, source):
    metadata = reader.read(source)
    sheets = {s["properties"]["title"]: s["properties"] for s in metadata["sheets"]}
    result, requests, text_bytes = {}, 0, 0
    for title, required in HEADERS.items():
        properties = sheets.get(title, {})
        grid = properties.get("gridProperties", {})
        rows, columns = grid.get("rowCount"), grid.get("columnCount")
        if (type(rows) is not int or not 1 <= rows <= 10000 or
                type(columns) is not int or not len(required) <= columns <= 40):
            raise ConnectionError("grading_workbook_layout_required")
        values, score_columns = [], set()
        size = 5000 // columns
        for first in range(0, rows, size):
            requests += 1
            if requests > 128:
                raise ConnectionError("grading_workbook_limit")
            last = min(first + size, rows)
            cell_range = "'" + title.replace("'", "''") + "'!A" + str(first + 1) + ":" + _column(columns) + str(last)
            response = reader.read(source, (cell_range,))
            matches = [s for s in response["sheets"] if s["properties"]["sheetId"] == properties["sheetId"]]
            if len(matches) != 1 or matches[0]["properties"]["title"] != title:
                raise ConnectionError("grading_workbook_changed")
            chunk = [[""] * columns for _ in range(last - first)]
            seen = set()
            for block in matches[0].get("data", []):
                start, left = block.get("startRow", 0), block.get("startColumn", 0)
                if type(start) is not int or type(left) is not int or left != 0:
                    raise ConnectionError("grading_workbook_layout_required")
                for offset, row in enumerate(block.get("rowData", [])):
                    position = start + offset
                    cells = row.get("values", [])
                    if position in seen or not first <= position < last or len(cells) > columns:
                        raise ConnectionError("grading_workbook_layout_required")
                    seen.add(position)
                    text = [str(c.get("formattedValue", "")) for c in cells]
                    if title == "Submissions" and position == 0:
                        score_columns = {i for i, value in enumerate(text) if value in {"content_score", "revision_score"}}
                    if position:
                        for i in score_columns:
                            number = cells[i].get("effectiveValue", {}).get("numberValue") if i < len(cells) else None
                            if type(number) in (int, float):
                                text[i] = str(number)
                    chunk[position - first] = text + [""] * (columns - len(cells))
                    text_bytes += sum(len(value.encode("utf-8")) for value in chunk[position - first])
                    if text_bytes > 4_000_000:
                        raise ConnectionError("grading_workbook_limit")
            values.extend(chunk)
        headers = values[0]
        used = [h for h in headers if h]
        if len(set(used)) != len(used) or not set(required) <= set(used):
            raise ConnectionError("grading_workbook_headers_required")
        result[title] = [{h: row[i] for i, h in enumerate(headers) if h}
                         for row in values[1:] if any(row)]
    fresh = reader.read(source)
    fresh_properties = {s["properties"]["title"]: s["properties"] for s in fresh["sheets"]}
    if any(fresh_properties.get(title) != sheets[title] for title in HEADERS):
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


def project(tables):
    assignments = _unique(tables["Assignments"], lambda r: r["assignment_key"])
    if any(r["class_key"] not in CLASSES for r in assignments.values()):
        raise ConnectionError("grading_class_invalid")
    records = _unique(tables["Submissions"], lambda r: r["record_id"])
    for row in records.values():
        assignment = assignments.get(row["assignment_key"])
        if assignment is None:
            raise ConnectionError("grading_assignment_unresolved")
        row.update(class_key=assignment["class_key"], assignment_title=assignment["assignment_title"], answers=[])
        roster_key = row.get("learner_key", "") if row["status"] in {"assessed", "roster_matched"} else ""
        row["count_key"] = ("ic:" + row["student_id"] if row["student_id"] else
                            "roster:" + roster_key if roster_key else "")
        row["included_in_counts"] = bool(row["count_key"] and row["section_id"] and row["status"] not in EXCLUDED)
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
        return project(_tables(reader, config["HUB_GRADING_SPREADSHEET_ID"]))
    except ConnectionError:
        raise
    except SheetsReadError as error:
        code = str(error)
        diagnostic = "grading_records_" + code if code in SHEETS_READ_ERRORS else "grading_records_unavailable"
        raise ConnectionError(diagnostic) from None
    except Exception:
        raise ConnectionError("grading_records_unavailable") from None
