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

ROSTER_RANGES = (
    "'A2 Forensics'!A1:B200",
    "'A3 Forensics'!A1:B200",
    "'A4 Biology'!A1:B200",
    "'B3 A&P'!A1:B200",
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
    roster_id = str(config.get("HUB_ROSTER_SPREADSHEET_ID") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", roster_id):
        raise ConnectionError("roster_source_not_configured")
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

    return parse_roster(data)


def parse_roster(data):
    """Explicitly accept Last/First headers, headerless pairs and Last, First.

    A title row is recognized by its exact configured tab name. Blank rows do
    not terminate a roster. Malformed populated rows fail closed, not disappear.
    """
    tables = _rows(data)
    entries = []
    for class_key, tabs in CLASS_TABS.items():
        for tab in tabs:
            for position, cells in tables.get(tab, []):
                cells = [c.strip() for c in cells]
                if not any(cells):
                    continue
                last, first = (cells + ["", ""])[:2]
                if _norm(last) == _norm(tab) and not first:
                    continue
                if _norm(last) == "last name" and _norm(first) == "first name":
                    continue
                layout = "last_first_columns"
                if last and not first and "," in last:
                    last, first = [part.strip() for part in last.split(",", 1)]
                    layout = "last_comma_full_first"
                if (not last or not first or any(cells[2:])
                        or not _first_base(first) or not _norm(last)):
                    raise ConnectionError("roster_layout_needs_review")
                first_key, last_key = _first_base(first), _norm(last)
                digest = hashlib.sha256(
                    ("roster|" + class_key + "|" + tab + "|" + first_key + "|" +
                     last_key).encode("utf-8")).hexdigest()
                entries.append({"class_key": class_key, "section_name": tab,
                    "first": first, "last": last,
                    "name_key": first_key + " " + last_key,
                    "learner_key": "roster:" + digest,
                    "display_id": "roster:" + digest[:24],
                    "roster_row": position, "layout": layout})
    return entries


def _aliases(row):
    legal = _first_base(row["first"])
    firsts = {legal, legal.split()[0]}
    firsts.update(_norm(n) for n in re.findall(r"\(([^)]+)\)", row["first"]))
    return {_norm(f + " " + row["last"]) for f in firsts if f}


def match_respondent(email, entries, domains, prefix="s."):
    """Conservative unique roster match from collected school email only.

    Domain and the school's username prefix are private configuration. Numbers
    are never stripped and initials are never guessed. Explicit nickname and
    middle-name alternatives are considered across ALL classes before routing.
    """
    email = str(email or "").strip().lower()
    if email.count("@") != 1:
        return None, "respondent_email_missing"
    local, domain = email.split("@")
    if domain not in {str(d).strip().lower() for d in domains if str(d).strip()}:
        return None, "respondent_email_domain_unverified"
    if prefix and local.startswith(prefix):
        local = local[len(prefix):]
    # Compare components; do not infer a person from an initial or filename.
    compact = lambda value: re.sub(r"[^a-z0-9]", "", _norm(value))
    matches = [r for r in entries if any(compact(n) == compact(local)
                                        for n in _aliases(r))]
    if len(matches) != 1:
        return None, "roster_identity_ambiguous" if matches else "roster_identity_unmatched"
    return matches[0], None


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
