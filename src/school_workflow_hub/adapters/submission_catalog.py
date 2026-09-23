"""Authenticated lesson metadata for selectors; never read student work.

The registry read is restricted to Assignments. Agenda contributes stable
discovery identities and source links, without fetching those linked resources.
"""
from datetime import datetime, timezone
import re

from .google_sheets import SheetsReader, SheetsReadError, MAX_CELLS
from .grading_records import CLASSES, _column, _sheet_map
from .hosted_agenda import ConnectionError, _identity_configuration, authorize, service_token
from .shared_form_intake import agenda_catalog

SCHEMA = "hub-submission-catalog/v1"
MAX_ROWS, MAX_COLUMNS, MAX_TEXT_BYTES = 2000, 40, 1_000_000
REGISTRY_FIELDS = frozenset({"assignment_key", "class_key", "assignment_title",
                             "assignment_code", "source_deck_id", "source_url"})
ENTRY_FIELDS = frozenset({
    "assignment_key", "class_key", "assignment_title", "week", "source_deck_id",
    "source_url", "source_reference", "source_references", "discovery_only",
    "registry_mapping_verified", "registry_assignment_key", "review_reasons",
    "form_id", "form_url", "form_source", "form_mapping_verified",
    "form_review_reason", "form_review_action", "form_source_reference", "form_source_references",
    "form_binding_ambiguous", "destination_folder_url", "due_date",
})
ERRORS = frozenset({
    "sign_in_required", "connection_not_configured", "runtime_identity_unavailable",
    "grading_records_not_connected", "submission_catalog_unavailable",
    "submission_catalog_registry_limit", "submission_catalog_registry_invalid",
    "submission_catalog_registry_changed", "agenda_layout_changed",
    "intake_catalog_needs_review", "intake_configuration_invalid",
    "intake_registry_mapping_required", "intake_registry_conflict",
    "intake_registry_ambiguous", "intake_source_deck_unavailable",
})


def safe_error(error):
    code = str(error)
    return code if code in ERRORS else "submission_catalog_unavailable"


def _registry(reader, source):
    """Read the complete bounded Assignments grid, no other table's cells."""
    before = _sheet_map(reader.read(source)).get("Assignments", {}).get("properties", {})
    grid = before.get("gridProperties", {})
    rows, columns = grid.get("rowCount"), grid.get("columnCount")
    if (type(rows) is not int or not 1 <= rows <= MAX_ROWS or type(columns) is not int
            or not 3 <= columns <= MAX_COLUMNS):
        raise ConnectionError("submission_catalog_registry_limit")
    values, text_bytes = {}, 0
    step = MAX_CELLS // columns
    for top in range(0, rows, step):
        bottom = min(rows, top + step)
        rectangle = "'Assignments'!A%d:%s%d" % (top + 1, _column(columns), bottom)
        data = _sheet_map(reader.read(source, (rectangle,)))
        if set(data) != {"Assignments"} or data["Assignments"]["properties"].get("sheetId") != before.get("sheetId"):
            raise ConnectionError("submission_catalog_registry_changed")
        blocks = data["Assignments"].get("data", [])
        if not isinstance(blocks, list):
            raise ConnectionError("submission_catalog_registry_invalid")
        for block in blocks:
            if not isinstance(block, dict):
                raise ConnectionError("submission_catalog_registry_invalid")
            start, left, block_rows = block.get("startRow", 0), block.get("startColumn", 0), block.get("rowData", [])
            if (type(start) is not int or not top <= start < bottom or type(left) is not int
                    or left != 0 or not isinstance(block_rows, list)):
                raise ConnectionError("submission_catalog_registry_invalid")
            for offset, row in enumerate(block_rows):
                position = start + offset
                cells = row.get("values", []) if isinstance(row, dict) else None
                if (position in values or position >= bottom or not isinstance(cells, list)
                        or len(cells) > columns or any(not isinstance(cell, dict) for cell in cells)):
                    raise ConnectionError("submission_catalog_registry_invalid")
                text = [cell.get("formattedValue", "") for cell in cells]
                if any(not isinstance(value, str) for value in text):
                    raise ConnectionError("submission_catalog_registry_invalid")
                text_bytes += sum(len(value.encode("utf-8")) for value in text)
                if text_bytes > MAX_TEXT_BYTES:
                    raise ConnectionError("submission_catalog_registry_limit")
                values[position] = text + [""] * (columns - len(text))
    after = _sheet_map(reader.read(source)).get("Assignments", {}).get("properties", {})
    if after != before:
        raise ConnectionError("submission_catalog_registry_changed")
    headers = values.get(0, [])
    used = [header for header in headers if header]
    required = {"assignment_key", "class_key", "assignment_title"}
    if len(used) != len(set(used)) or not required <= set(used):
        raise ConnectionError("submission_catalog_registry_invalid")
    result, keys = [], set()
    for position, text in sorted(values.items()):
        if not position or not any(text):
            continue
        row = {header: text[index] for index, header in enumerate(headers) if header in REGISTRY_FIELDS}
        key = row["assignment_key"]
        if (not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", key) or key in keys
                or row["class_key"] not in CLASSES or not row["assignment_title"]):
            raise ConnectionError("submission_catalog_registry_invalid")
        keys.add(key)
        result.append(row)
    return result


def read_catalog(assertion, class_key, config):
    if not isinstance(class_key, str) or class_key not in CLASSES:
        raise ValueError("invalid_submission_scope")
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)  # No metadata call before IAP validation.
    source = config.get("HUB_GRADING_SPREADSHEET_ID", "")
    if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source):
        raise ConnectionError("grading_records_not_connected")
    try:
        reader = SheetsReader(service_token(config.get("HUB_READER_SERVICE_ACCOUNT", "")))
        assignments = _registry(reader, source)
        catalog = agenda_catalog(assertion, config, {"assignments": assignments},
                                 class_key=class_key, discovery=True)
        entries, issues, seen = [], [], set()
        for entry in catalog:
            key = entry.get("assignment_key")
            if (entry.get("class_key") != class_key or not isinstance(key, str)
                    or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", key) or key in seen):
                raise ConnectionError("submission_catalog_unavailable")
            seen.add(key)
            entries.append({name: value for name, value in entry.items() if name in ENTRY_FIELDS})
            for issue in entry.get("catalog_issues", []):
                safe = {"source_reference": str(issue.get("source_reference", ""))[:160],
                        "error": safe_error(issue.get("error", "intake_catalog_needs_review"))}
                if safe not in issues:
                    issues.append(safe)
        return {"schema_version": SCHEMA, "provenance": "source_read", "external_writes": 0,
                "live_model_calls": 0, "class_key": class_key,
                "checked_at": datetime.now(timezone.utc).isoformat(), "assignments": entries,
                "coverage": {"complete": not issues, "issues": issues}}
    except SheetsReadError:
        raise ConnectionError("submission_catalog_unavailable") from None
    except ConnectionError as error:
        raise ConnectionError(safe_error(error)) from None
