"""Read the teacher's current roster workbook and match newly discovered files.

The grading workbook remains authoritative when a file already has a verified
identity. This adapter only supplies a roster-backed identity for newly
discovered files whose submitter name uniquely matches one active roster row.
No roster or student record is written here.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .google_sheets import SheetsReader, SheetsReadError
from .hosted_agenda import ConnectionError, service_token

DEFAULT_ROSTER_SPREADSHEET_ID = "1jmaxOjkmLeC-gMWHDHHySZ3lP36wtp5lFdYEl1CRguI"
ROSTER_RANGES = (
    "'A2 Forensics'!A1:B40",
    "'A3 Forensics'!A1:B40",
    "'A4 Biology'!A1:B40",
    "'B3 A&P'!A1:B40",
)
CLASS_TABS = {
    "forensics": ("A2 Forensics", "A3 Forensics"),
    "biology": ("A4 Biology",),
    "anatomy": ("B3 A&P",),
}


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _first_base(value):
    return _norm(re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")))


def _rows(data):
    result = {}
    for sheet in data.get("sheets", []) if isinstance(data, dict) else []:
        props = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
        title = props.get("title")
        rows = []
        for block in sheet.get("data", []) or []:
            start = int(block.get("startRow", 0))
            for offset, row in enumerate(block.get("rowData", []) or []):
                values = row.get("values", []) if isinstance(row, dict) else []
                cells = [str(c.get("formattedValue", "")) if isinstance(c, dict) else ""
                         for c in values]
                rows.append((start + offset + 1, cells))
        result[title] = sorted(rows)
    return result


def read_roster(config):
    roster_id = str(config.get("HUB_ROSTER_SPREADSHEET_ID")
                    or DEFAULT_ROSTER_SPREADSHEET_ID)
    try:
        reader = SheetsReader(service_token(
            config.get("HUB_READER_SERVICE_ACCOUNT", "")))
        data = reader.read(roster_id, ROSTER_RANGES)
    except ConnectionError:
        raise
    except SheetsReadError:
        raise ConnectionError("roster_source_unavailable") from None
    except Exception:
        raise ConnectionError("roster_source_unavailable") from None

    tables = _rows(data)
    entries = []
    for class_key, tabs in CLASS_TABS.items():
        for tab in tabs:
            rows = tables.get(tab, [])
            started = False
            for _, cells in rows:
                last = cells[0].strip() if cells else ""
                first = cells[1].strip() if len(cells) > 1 else ""
                if not started:
                    if _norm(last) == "last name" and _norm(first) == "first name":
                        started = True
                    continue
                if not last and not first:
                    break
                # Stop if an embedded second roster begins inside a legacy tab.
                if _norm(last).endswith("forensics") or _norm(last) in {
                        "a4 biology", "b3 a p"}:
                    break
                if not last or not first or _norm(last) == "last name":
                    continue
                first_key = _first_base(first)
                last_key = _norm(last)
                if not first_key or not last_key:
                    continue
                digest = hashlib.sha256(
                    ("roster|" + class_key + "|" + tab + "|" + first_key + "|" +
                     last_key).encode("utf-8")).hexdigest()
                entries.append({
                    "class_key": class_key, "section_name": tab,
                    "first": first, "last": last,
                    "name_key": first_key + " " + last_key,
                    "learner_key": "roster:" + digest,
                    "display_id": "roster:" + digest[:24],
                })
    return entries


def _submitted_name(file_name):
    value = str(file_name or "")
    # Google Forms upload copies end in " - Display Name". The lesson title may
    # also contain hyphens, so take the final delimiter only.
    if " - " not in value:
        return ""
    return value.rsplit(" - ", 1)[-1].strip()


def match_files(files, class_key, roster_entries, section_ids=None):
    """Return unique roster matches keyed by Drive file ID.

    The filename is only accepted as identity evidence when its final Google
    submitter-name suffix uniquely matches one active roster row in this class.
    Ambiguous or absent matches are left unmatched for human review.
    """
    section_ids = section_ids or {}
    entries = [r for r in roster_entries if r.get("class_key") == class_key]
    by_name = {}
    for row in entries:
        by_name.setdefault(row["name_key"], []).append(row)
    result = {}
    for item in files or []:
        file_id = str(item.get("id") or "")
        submitted = _norm(_submitted_name(item.get("name")))
        matches = by_name.get(submitted, [])
        if len(matches) != 1:
            continue
        row = matches[0]
        result[file_id] = {
            "student_id": row["display_id"],
            "section_id": str(section_ids.get(row["section_name"]) or row["section_name"]),
            "identity_basis": "current_roster_unique_name",
            "assessed_marker": "unknown",
            "marker_precision": "unknown",
            "ic_posting_status": "unverified",
        }
    return result
