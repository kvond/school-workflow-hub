"""Strict native IC Roster Report input; no login, browser action or writer.

The trusted host supplies original CSV bytes and separately observed native
ID/status groups. A file on disk is not a fresh source read or unattended reader.
Only the explicitly configured report-membership scheme consumes this input.
"""
import csv
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import re
from zoneinfo import ZoneInfo

from . import roster_worker as rw
from ..core.cloud_state import instant
from ..core.runs import content_hash

HEADER = ("State Code", "Course", "Room", "Term(s)", "Period", "Last Name", "First Name",
          "Middle Name", "Suffix", "Alias", "Gender", "Grade", "Start Date", "End Date", "Student Number")
CONTEXT_FIELDS = {"origin", "account", "school_id", "calendar_id", "structure_id", "school_year",
                  "term", "term_start", "term_end"}
SCOPE_FIELDS = ("state_code", "course", "room", "terms", "period")
OPTIONS = {"local_student_number": True, "incoming": True, "dropped": True, "section_placement": True}
PROOF_FIELDS = {"schema", "capture_id", "captured_at", "observed_at", "csv_sha256", "context",
                "effective_date", "options", "status_sha256", "bindings_sha256"}


def _fail(reason):
    rw._fail("report_" + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _date(value, fmt="%Y-%m-%d", *, empty=False):
    if value == "" and empty:
        return ""
    if not isinstance(value, str):
        _fail("date_invalid")
    try:
        date = datetime.strptime(value, fmt).date()
        if date.strftime(fmt) != value:
            raise ValueError()
        return date.isoformat()
    except ValueError:
        _fail("date_invalid")


def _context(value):
    if (not isinstance(value, dict) or set(value) != CONTEXT_FIELDS
            or any(not rw._text(v) for v in value.values())
            or not re.fullmatch(r"https://[A-Za-z0-9.-]+", value["origin"])
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value["account"])
            or any(not rw._id(value[k]) for k in ("school_id", "calendar_id", "structure_id"))
            or not re.fullmatch(r"20\d{2}-\d{2}", value["school_year"])):
        _fail("context_invalid")
    if _date(value["term_start"]) > _date(value["term_end"]):
        _fail("context_invalid")


def validate_proof(proof, now):
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS
            or proof["schema"] != rw.REPORT_SCHEME or not rw._id(proof["capture_id"])
            or proof["options"] != OPTIONS
            or any(not _hash(proof[f]) for f in ("csv_sha256", "status_sha256", "bindings_sha256"))):
        _fail("proof_invalid")
    _context(proof["context"])
    try:
        captured, observed = instant(proof["captured_at"]), instant(proof["observed_at"])
    except (ValueError, TypeError):
        _fail("time_invalid")
    if not captured <= observed <= now or now - captured > timedelta(minutes=15):
        _fail("capture_stale")
    effective = _date(proof["effective_date"])
    if (effective != now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            or not proof["context"]["term_start"] <= effective <= proof["context"]["term_end"]):
        _fail("effective_date_invalid")


def membership_key(context, section_id, student_number):
    """A report relationship key, explicitly NOT the hidden native rosterID.

    Status/date observations live in immutable receipts. This key makes no claim
    to distinguish hidden native enrollment episodes or to link old mirror rows.
    """
    return rw.REPORT_MEMBER_PREFIX + content_hash({"scheme": rw.REPORT_SCHEME,
        "origin": context["origin"], "school_id": context["school_id"],
        "calendar_id": context["calendar_id"], "structure_id": context["structure_id"],
        "school_year": context["school_year"], "section_id": section_id, "student_number": student_number})


def parse_csv(raw):
    """Parse only the observed official CSV format; never infer status or scope."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 2_000_000:
        _fail("bytes_invalid")
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True))
    except (UnicodeError, csv.Error):
        _fail("csv_invalid")
    if not rows or tuple(rows[0]) != HEADER or len(rows) > 1593:
        _fail("header_or_size_invalid")
    result, seen, identities = [], set(), {}
    for row in rows[1:]:
        if len(row) == 16 and row[-1] == "":
            row = row[:-1]  # observed official export has one empty trailing field
        if len(row) != 15 or any(not rw._text(v, empty=True) for v in row):
            _fail("row_invalid")
        row = [v.strip() for v in row]
        if (not all(row[:5]) or not row[5] or not row[6]
                or not re.fullmatch(r"[0-9]{1,20}", row[14]) or row[10]):
            _fail("identity_or_scope_invalid")
        scope, number = dict(zip(SCOPE_FIELDS, row[:5])), row[14]
        identity = tuple(row[5:10])
        # A repeated ID in a section is ambiguous even if rows happen to match.
        key = (tuple(row[:5]), number)
        if key in seen or (number in identities and identities[number] != identity):
            _fail("identity_conflict")
        seen.add(key)
        identities[number] = identity
        first = " ".join(v for v in (row[6], row[7]) if v)
        if row[9] and row[9].casefold() not in {row[6].casefold(), first.casefold()}:
            first += " (" + row[9] + ")"
        last = " ".join(v for v in (row[5], row[8]) if v)
        result.append({"scope": scope, "student_id": number, "first": first, "last": last,
                       "start_date": _date(row[12], "%m/%d/%Y", empty=True),
                       "end_date": _date(row[13], "%m/%d/%Y", empty=True)})
    return result


def prepare_capture(raw, observation, *, expected_context, bindings, targets, now):
    """Join CSV identity/dates to complete independent native status groups.

    The caller is an accepted trusted-host capture capability, not a browser
    request. Observation time is the original native generation/read time, never
    download mtime. The native group evidence must come from the same exact
    section/year/effective date and include every local Student Number.
    """
    rw.validate_targets(targets)
    if not all(t.get("identity_scheme") == rw.REPORT_SCHEME for t in targets):
        _fail("identity_scheme_required")
    if (not isinstance(observation, dict)
            or set(observation) != {"capture_id", "captured_at", "observed_at", "csv_sha256", "context",
                                    "effective_date", "options", "sections"}
            or observation["context"] != expected_context
            or observation["csv_sha256"] != hashlib.sha256(raw).hexdigest()):
        _fail("observation_mismatch")
    _context(expected_context)
    wanted = {t["section_id"]: t for t in targets}
    if (not isinstance(bindings, dict) or set(bindings) != set(wanted)
            or any(not isinstance(b, dict) or set(b) != set(SCOPE_FIELDS)
                   or any(not rw._text(v) for v in b.values()) for b in bindings.values())
            or len({tuple(b[k] for k in SCOPE_FIELDS) for b in bindings.values()}) != len(wanted)
            or any(t["school_year"] != expected_context["school_year"] for t in targets)):
        _fail("binding_invalid")
    sections = observation["sections"]
    if not isinstance(sections, list) or len(sections) != len(wanted):
        _fail("coverage_incomplete")
    parsed = parse_csv(raw)
    by_scope = {tuple(b[k] for k in SCOPE_FIELDS): sid for sid, b in bindings.items()}
    csv_sections = {sid: [] for sid in wanted}
    for row in parsed:
        sid = by_scope.get(tuple(row["scope"][k] for k in SCOPE_FIELDS))
        if sid is None:
            _fail("unbound_section")
        csv_sections[sid].append(row)
    result, seen = [], set()
    canonical_status = []
    for native in sections:
        if (not isinstance(native, dict)
                or set(native) != {"section_id", "scope", "complete", "filtered", "next_page", "counts", "students"}
                or native["section_id"] not in wanted or native["section_id"] in seen
                or native["scope"] != bindings[native["section_id"]]
                or native["complete"] is not True or native["filtered"] is not False or native["next_page"] is not None
                or not isinstance(native["counts"], dict) or set(native["counts"]) != rw.SOURCE_STATUSES
                or any(type(v) is not int or not 0 <= v <= 199 for v in native["counts"].values())
                or not isinstance(native["students"], list)):
            _fail("native_status_coverage_incomplete")
        sid = native["section_id"]
        seen.add(sid)
        statuses, counts = {}, dict.fromkeys(rw.SOURCE_STATUSES, 0)
        for student in native["students"]:
            if (not isinstance(student, dict) or set(student) != {"student_number", "status"}
                    or not isinstance(student["student_number"], str)
                    or not re.fullmatch(r"[0-9]{1,20}", student["student_number"])
                    or student["student_number"] in statuses or student["status"] not in rw.SOURCE_STATUSES):
                _fail("native_status_identity_invalid")
            statuses[student["student_number"]] = student["status"]
            counts[student["status"]] += 1
        if (counts != native["counts"] or len(statuses) > 199
                or set(statuses) != {r["student_id"] for r in csv_sections[sid]}):
            _fail("native_status_count_mismatch")
        if expected_context["term"] not in [t.strip() for t in bindings[sid]["terms"].split(";")]:
            _fail("term_mismatch")
        canonical_status.append({"section_id": sid, "scope": native["scope"], "counts": counts,
                                 "students": sorted(native["students"], key=lambda s: s["student_number"])})
        rows = [{**{k: v for k, v in row.items() if k != "scope"},
                 "membership_id": membership_key(expected_context, sid, row["student_id"]),
                 "status": statuses[row["student_id"]]} for row in csv_sections[sid]]
        result.append({"section_id": sid, "school_year": wanted[sid]["school_year"],
            "complete": True, "filtered": False, "next_page": None,
            "revision": content_hash({"scope": native["scope"], "rows": sorted(rows, key=lambda r: r["student_id"])}),
            "checked_at": observation["captured_at"], "expected_count": len(rows), "rows": rows})
    proof = {k: deepcopy(observation[k]) for k in PROOF_FIELDS - {"schema", "status_sha256", "bindings_sha256"}}
    proof.update(schema=rw.REPORT_SCHEME, bindings_sha256=content_hash(bindings),
                 status_sha256=content_hash(sorted(canonical_status, key=lambda s: s["section_id"])))
    validate_proof(proof, now)
    return rw.validate_capture({"source": "infinite_campus", "reader_id": "ic-native-roster-report",
        "complete": True, "errors": [], "sections": result, "report_proof": proof}, targets, now)


class NativeRosterReportReader:
    """An injected supported native capture, never a filename/persisted fallback.

    capture() must obtain a NEW original CSV and original native status evidence
    each time. No host transport is configured here. Reusing an observation is
    rejected by the worker's independent-bookend check, not certified as fresh.
    """
    def __init__(self, capture, *, expected_context, bindings, clock=None):
        if not callable(capture):
            _fail("native_capture_required")
        self.capture = capture
        self.expected_context, self.bindings = deepcopy(expected_context), deepcopy(bindings)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read_complete(self, targets):
        raw, observation = self.capture()
        return prepare_capture(raw, observation, expected_context=self.expected_context,
                               bindings=self.bindings, targets=targets, now=self.clock())
