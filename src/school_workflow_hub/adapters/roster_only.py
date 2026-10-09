"""Roster-only class mirrors, deliberately separate from grading identities.

The host supplies a complete, fresh IC observation. This module does not fetch
IC, grant a student a grading identity, or write a workbook. Its bounded plan
can be applied by an authorized roster worker and verified against readback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .hosted_agenda import ConnectionError
from .roster_matching import CLASS_TABS


HEADERS = ("Full name", "Roster status", "Start date", "End date", "IC checked at")
STATUSES = frozenset({"active", "incoming", "dropped", "inactive"})


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or "," not in value or any(c in value for c in "\r\n\x00")):
        raise ConnectionError("roster_only_name_invalid")
    return " ".join(value.split()).casefold()


def _at(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ConnectionError("roster_only_time_invalid") from None


def configured_targets(config):
    """Resolve explicit private mappings; academic readers never consume them."""
    raw = config.get("HUB_ROSTER_ONLY_SECTIONS", {})
    try:
        targets = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        raise ConnectionError("roster_only_configuration_invalid") from None
    if not isinstance(targets, dict) or len(targets) > 8:
        raise ConnectionError("roster_only_configuration_invalid")
    academic = {name for names in CLASS_TABS.values() for name in names}
    try:
        academic_ids = config.get("HUB_ROSTER_SECTION_IDS", {})
        academic_tabs = config.get("HUB_ROSTER_TABS", {})
        academic_ids = json.loads(academic_ids) if isinstance(academic_ids, str) else academic_ids
        academic_tabs = json.loads(academic_tabs) if isinstance(academic_tabs, str) else academic_tabs
        if not isinstance(academic_ids, dict) or not isinstance(academic_tabs, dict):
            raise ValueError()
    except (TypeError, ValueError):
        raise ConnectionError("roster_only_configuration_invalid") from None
    sections = {str(value) for value in academic_ids.values()}
    tabs = {value for value in academic_tabs.values() if type(value) is int}
    result = {}
    for label, target in targets.items():
        if (not isinstance(label, str) or not label.strip() or len(label) > 80
                or label in academic or not isinstance(target, dict)
                or set(target) != {"class_key", "section_id", "tab_id", "school_year"}
                or target.get("class_key") != "study_hall"
                or not isinstance(target.get("section_id"), str)
                or not re.fullmatch(r"[1-9][0-9]{0,11}", target["section_id"])
                or type(target.get("tab_id")) is not int or target["tab_id"] < 0
                or not isinstance(target.get("school_year"), str)
                or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}", target["school_year"])):
            raise ConnectionError("roster_only_configuration_invalid")
        if target["section_id"] in sections or target["tab_id"] in tabs:
            raise ConnectionError("roster_only_mapping_conflict")
        sections.add(target["section_id"])
        tabs.add(target["tab_id"])
        result[label] = dict(target, section_name=label, roster_only=True,
                             grading_eligible=False)
    return result


def plan_mirror(config, section_name, snapshot, previous_rows, *, now=None):
    """Plan a complete active view plus retained incoming/departed history.

    Name matching here establishes only roster display rows. IC IDs, assessment
    authority and membership in another class are never inferred from names.
    Existing teacher columns belong to the caller and must stay outside A:E.
    """
    target = configured_targets(config).get(section_name)
    if target is None:
        raise ConnectionError("roster_only_target_unconfigured")
    if (not isinstance(snapshot, dict) or snapshot.get("complete") is not True
            or snapshot.get("section_id") != target["section_id"]
            or snapshot.get("school_year") != target["school_year"]
            or snapshot.get("source") != "infinite_campus"
            or not isinstance(snapshot.get("rows"), list)):
        raise ConnectionError("roster_only_complete_snapshot_required")
    checked = _at(snapshot.get("checked_at"))
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or checked > clock or clock - checked > timedelta(hours=1):
        raise ConnectionError("roster_only_snapshot_stale")
    current, observed_dates = {}, {}
    counts = {s: 0 for s in ("active", "incoming", "dropped")}
    for row in snapshot["rows"]:
        if not isinstance(row, dict) or row.get("status") not in counts:
            raise ConnectionError("roster_only_snapshot_invalid")
        name_key = _name(row.get("name"))
        if name_key in current:
            raise ConnectionError("roster_only_ambiguous_name")
        current[name_key] = [row["name"].strip(), row["status"],
                             str(row.get("start_date") or ""),
                             str(row.get("end_date") or ""), snapshot["checked_at"]]
        observed_dates[name_key] = ("start_date" in row, "end_date" in row)
        counts[row["status"]] += 1
    if counts != snapshot.get("counts") or len(current) > 199:
        raise ConnectionError("roster_only_snapshot_count_mismatch")
    if not isinstance(previous_rows, list) or len(previous_rows) > 199:
        raise ConnectionError("roster_only_baseline_invalid")
    prior = {}
    for row in previous_rows:
        if not isinstance(row, list):
            raise ConnectionError("roster_only_baseline_invalid")
        if not row or not any(row):
            continue
        if len(row) > 5 or any(not isinstance(cell, str) for cell in row):
            raise ConnectionError("roster_only_baseline_invalid")
        name_key = _name(row[0])
        if name_key in prior:
            raise ConnectionError("roster_only_ambiguous_name")
        if len(row) > 1 and row[1] not in STATUSES:
            raise ConnectionError("roster_only_baseline_invalid")
        prior[name_key] = (row + [""] * 5)[:5]
    retained = []
    for name_key, row in prior.items():
        if name_key in current:
            # A names/status-only capture cannot erase earlier verified dates.
            for column, present in zip((2, 3), observed_dates[name_key]):
                if not present:
                    current[name_key][column] = row[column]
        else:
            # Absence from a complete scan means inactive, not a guessed IC drop.
            retained.append([row[0], "inactive", row[2], row[3], snapshot["checked_at"]])
    order = {"active": 0, "incoming": 1, "dropped": 2, "inactive": 3}
    rows = sorted(list(current.values()) + retained,
                  key=lambda row: (order[row[1]], _name(row[0])))
    if len(rows) > 199:
        raise ConnectionError("roster_only_row_limit")
    return {"schema": "hub-roster-only-mirror/v1", "target": target,
            "checked_at": snapshot["checked_at"], "counts": dict(counts, inactive=len(retained)),
            "headers": list(HEADERS), "rows": rows,
            "source_sha256": _hash(snapshot), "baseline_sha256": _hash(previous_rows),
            "mirror_sha256": _hash(rows), "grading_eligible": False}


def verify_mirror(plan, rows):
    """An exact post-write read is required before any success receipt."""
    if rows != [plan["headers"], *plan["rows"]]:
        raise ConnectionError("roster_only_readback_mismatch")
    return {"status": "verified", "counts": dict(plan["counts"]),
            "source_sha256": plan["source_sha256"], "mirror_sha256": plan["mirror_sha256"],
            "checked_at": plan["checked_at"], "grading_eligible": False}


def active_rows(rows):
    """Read this mirror without treating retained or incoming rows as active."""
    if not isinstance(rows, list) or not rows or rows[0] != list(HEADERS):
        raise ConnectionError("roster_only_baseline_invalid")
    result, seen = [], set()
    for row in rows[1:]:
        if len(row) != 5 or row[1] not in STATUSES:
            raise ConnectionError("roster_only_baseline_invalid")
        name_key = _name(row[0])
        if name_key in seen:
            raise ConnectionError("roster_only_ambiguous_name")
        seen.add(name_key)
        if row[1] == "active":
            result.append(row[0])
    return result
