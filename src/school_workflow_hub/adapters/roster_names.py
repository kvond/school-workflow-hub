"""Read-only student-name matching from the one approved school spreadsheet: Rosters.

This is roster evidence only. It does not read Infinite Campus, invent student
numbers, or write any school source. The spreadsheet is discovered by the exact
Drive title "Rosters"; similarly named workbooks are never used.
"""
from datetime import datetime, timezone
import hashlib
import re

from .google_sheets import SheetsReader, SheetsReadError
from .hosted_agenda import ConnectionError, service_token
from .submission_inventory import DRIVE_SCOPE, DriveReader

ROSTER_TITLE = "Rosters"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
ROSTER_RANGES = ("'A2 Forensics'!A2:B80", "'A3 Forensics'!A2:B80")


def _norm(value):
    text = str(value or "").casefold()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _first_variants(value):
    text = str(value or "").strip()
    base = re.sub(r"\s*\([^)]*\)\s*", " ", text).strip()
    values = {_norm(base)}
    for nickname in re.findall(r"\(([^)]*)\)", text):
        values.add(_norm(nickname))
    return {v for v in values if v}


def _rows(sheet):
    result = []
    data = sheet.get("data", []) if isinstance(sheet, dict) else []
    for block in data:
        for row in block.get("rowData", []) or []:
            cells = row.get("values", []) if isinstance(row, dict) else []
            values = [str(c.get("formattedValue", "")) if isinstance(c, dict) else ""
                      for c in cells[:2]]
            values += [""] * (2 - len(values))
            result.append(values[:2])
    return result


def _parse_section(sheet, section_name):
    rows = _rows(sheet)
    students = []
    started = False
    for last, first in rows:
        if not started:
            if _norm(last) == "last name" and _norm(first) == "first name":
                started = True
            continue
        if not last and not first:
            break
        if _norm(last).endswith("forensics") and not first:
            break
        if not last or not first:
            continue
        last_norm = _norm(last)
        for first_norm in _first_variants(first):
            students.append((first_norm + " " + last_norm, {
                "display_name": re.sub(r"\s*\([^)]*\)\s*", " ", first).strip()
                                + " " + last.strip(),
                "section_name": section_name,
            }))
    return students


def _find_exact_rosters(drive):
    page = drive.get("files", {
        "q": "name = 'Rosters' and mimeType = '" + SPREADSHEET_MIME
             + "' and trashed = false",
        "fields": "files(id,name,mimeType,trashed),incompleteSearch,nextPageToken",
        "pageSize": 10,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })
    if (page.get("incompleteSearch") is True or page.get("nextPageToken")
            or not isinstance(page.get("files"), list)):
        raise ConnectionError("roster_source_incomplete")
    matches = [f for f in page["files"]
               if f.get("name") == ROSTER_TITLE and f.get("mimeType") == SPREADSHEET_MIME
               and f.get("trashed") is False]
    if not matches:
        raise ConnectionError("roster_source_not_found")
    if len(matches) != 1:
        raise ConnectionError("roster_source_ambiguous")
    return matches[0]["id"]


def read_forensics_roster(config):
    """Return normalized unique A2/A3 name matches from the exact Rosters sheet."""
    service_email = str(config.get("HUB_READER_SERVICE_ACCOUNT", ""))
    drive = DriveReader(service_token(service_email, DRIVE_SCOPE))
    source = _find_exact_rosters(drive)
    reader = SheetsReader(service_token(service_email))
    try:
        data = reader.read(source, ROSTER_RANGES)
    except SheetsReadError:
        raise ConnectionError("roster_source_unavailable") from None
    if data.get("properties", {}).get("title") != ROSTER_TITLE:
        raise ConnectionError("roster_source_changed")
    sheets = {s.get("properties", {}).get("title"): s for s in data.get("sheets", [])}
    if set(sheets) != {"A2 Forensics", "A3 Forensics"}:
        raise ConnectionError("roster_source_changed")
    by_name = {}
    conflicts = set()
    for section in ("A2 Forensics", "A3 Forensics"):
        for name, row in _parse_section(sheets[section], section):
            if name in by_name and by_name[name]["section_name"] != section:
                conflicts.add(name)
            else:
                by_name[name] = row
    for name in conflicts:
        by_name.pop(name, None)
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "source_title": ROSTER_TITLE, "by_name": by_name}


def match_hint(roster, hint):
    key = _norm(hint)
    row = (roster or {}).get("by_name", {}).get(key)
    if not row:
        return None
    opaque = hashlib.sha256(
        ("Rosters|" + row["section_name"] + "|" + key).encode("utf-8")).hexdigest()[:16]
    return {"student_id": "roster:" + opaque,
            "student_name": row["display_name"],
            "section_id": "rosters:" + row["section_name"].split()[0].lower(),
            "section_name": row["section_name"],
            "identity_basis": "Rosters exact normalized name match",
            "assessed_marker": "unknown",
            "marker_precision": "unknown",
            "ic_posting_status": "unverified"}
