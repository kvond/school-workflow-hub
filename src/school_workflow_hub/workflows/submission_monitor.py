"""Deterministic submission-monitoring state advance. Observation, not judgment.

Consumes one COMPLETE folder scan plus the verified workbook file mappings and
produces the next cumulative stream state and the events that scan proved.
Partial or failed scans must never reach this module: the caller stops instead,
so an earlier complete baseline is never replaced by a worse read.

The stream identity is the class plus the turn-in folder, never the due date,
so a deadline change cannot duplicate an assignment or orphan its history.
Drive metadata (modifiedTime, version) is revision EVIDENCE for inspection; it
is not a captured copy of student writing, not proof of who turned work in, and
never grounds for a grade or a zero. A file disappearing from a complete scan
is an access/review event, not proof the student withdrew the submission.
"""
import re

from school_workflow_hub.core.runs import content_hash, deterministic_id
from school_workflow_hub.workflows.due_completion import boundaries, timestamp

VERSION = "hub-submission-monitor/v1"
# Bump when derivation logic changes so stored state is recomputed from the
# retained evidence instead of an unchanged rerun replaying obsolete output.
DERIVATION_VERSION = 4
CLASS_KEYS = {"biology", "forensics", "anatomy"}
TIMEZONE_NAME = "America/New_York"
MAX_FILES = 500
MAX_EVENTS = 800
MAX_FILE_HISTORY = 20
# Event kinds are a closed vocabulary; the dashboard renders nothing else.
EVENT_KINDS = {"first_observed", "appeared", "modified", "inaccessible", "reappeared",
               "matched", "due_date_changed", "possible_replacement"}
# What the metadata actually supports about timing, stated conservatively.
TIMINGS = {"not_due_yet", "content_predates_due_turnin_unverified",
           "modified_after_due_needs_review", "due_date_unverified", "baseline_start"}


def stream_scope(class_key, folder_id):
    """Legacy folder-bound scope retained for stored fixtures and old records."""
    if class_key not in CLASS_KEYS:
        raise ValueError("invalid_monitor_class")
    if not isinstance(folder_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", folder_id):
        raise ValueError("invalid_monitor_folder")
    return {"workflow": "submission-monitor", "class_key": class_key,
            "assignment_key": "folder-" + folder_id}


def assignment_scope(class_key, assignment_label):
    """Stable stream identity for the real form-first workflow."""
    if class_key not in CLASS_KEYS:
        raise ValueError("invalid_monitor_class")
    if (not isinstance(assignment_label, str) or not assignment_label.strip()
            or len(assignment_label) > 200 or any(ord(ch) < 32 for ch in assignment_label)):
        raise ValueError("invalid_monitor_assignment")
    return {"workflow": "submission-monitor", "class_key": class_key,
            "assignment_key": "assignment-" + content_hash({
                "class_key": class_key, "assignment_label": assignment_label})[:32]}

def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


def _scan_source(scan):
    if _text(scan.get("source_id"), 128) and _text(scan.get("source_name"), 300):
        return {"source_id": scan["source_id"], "source_name": scan["source_name"],
                "source_kind": scan.get("source_kind") or "submission_source"}
    if _text(scan.get("folder_id"), 128) and _text(scan.get("folder_name"), 300):
        return {"source_id": scan["folder_id"], "source_name": scan["folder_name"],
                "source_kind": "drive_folder", "folder_id": scan["folder_id"]}
    raise ValueError("invalid_monitor_scan")


def _validate_scan(scan):
    if (not isinstance(scan, dict)
            or not {"class_key", "assignment_label", "date",
                    "observed_at", "coverage", "files"} <= set(scan)
            or scan["class_key"] not in CLASS_KEYS
            or not _text(scan["assignment_label"], 200)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", scan.get("date") or "")
            or not isinstance(scan["coverage"], dict) or scan["coverage"].get("complete") is not True
            or not isinstance(scan["files"], list) or len(scan["files"]) > MAX_FILES):
        raise ValueError("invalid_monitor_scan")
    source = _scan_source(scan)
    if (not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source["source_id"])
            or not _text(source.get("source_kind"), 80)):
        raise ValueError("invalid_monitor_scan")
    timestamp(scan["observed_at"])
    seen = set()
    for item in scan["files"]:
        if (not isinstance(item, dict) or not _text(item.get("id"), 128)
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", item["id"])
                or not _text(item.get("name"), 400) or item["id"] in seen):
            raise ValueError("invalid_monitor_scan")
        timestamp(item.get("modified_at") or "")
        seen.add(item["id"])

def _validate_mappings(mappings):
    if (not isinstance(mappings, dict)
            or mappings.get("source") != "grading_workbook"
            or not isinstance(mappings.get("by_file"), dict) or len(mappings["by_file"]) > 5000):
        raise ValueError("invalid_monitor_mappings")
    timestamp(mappings.get("checked_at") or "")
    for value in mappings["by_file"].values():
        if (not isinstance(value, dict)
                or not _text(value.get("student_id"), 64) or not _text(value.get("section_id"), 64)
                or not _text(value.get("identity_basis"), 64)
                or not _text(value.get("assessed_marker"), 40)
                or value.get("marker_precision") not in ("timestamp", "date", "unknown")
                or not _text(value.get("ic_posting_status"), 200)):
            raise ValueError("invalid_monitor_mappings")


def _cutoff(due_date, extension):
    effective = extension or due_date
    if not effective:
        return None, None
    due_end, grace_end = boundaries(effective, TIMEZONE_NAME)
    return due_end, grace_end


def _timing(is_baseline, due_end, modified, observed):
    if is_baseline:
        return "baseline_start" if due_end is not None else "due_date_unverified"
    if due_end is None:
        return "due_date_unverified"
    if observed <= due_end:
        return "not_due_yet"
    if modified <= due_end:
        # The document last changed before the deadline; a folder move does not
        # update modifiedTime, so the actual turn-in moment stays unverified.
        return "content_predates_due_turnin_unverified"
    return "modified_after_due_needs_review"


def advance_state(previous, scan, *, mappings, due_date, due_basis="unverified",
                  extensions=None, as_of):
    """Advance one stream. previous is the last complete state or None.

    due_date is the derived topic deadline (date-only deadlines include the
    whole local day; the grace window includes the full fourteenth day), and
    due_basis says where it came from — the scanned navigation date is never a
    deadline by itself. extensions map student_id to an approved individual due
    date and apply only to files with a verified student match; they never hide
    an unmatched file.
    """
    _validate_scan(scan)
    _validate_mappings(mappings)
    extensions = extensions or {}
    if not isinstance(extensions, dict) or len(extensions) > 500:
        raise ValueError("invalid_monitor_extensions")
    if due_date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
        raise ValueError("invalid_monitor_due_date")
    now = timestamp(as_of)
    observed = timestamp(scan["observed_at"])
    if observed > now:
        raise ValueError("monitor_scan_from_future")
    if previous is not None:
        stream_prior = (previous or {}).get("stream", {}) if isinstance(previous, dict) else {}
        if (not isinstance(previous, dict) or previous.get("schema_version") != VERSION
                or stream_prior.get("class_key") != scan["class_key"]
                or stream_prior.get("assignment_label") != scan["assignment_label"]
                or (stream_prior.get("folder_id") and scan.get("folder_id")
                    and stream_prior["folder_id"] != scan["folder_id"])):
            raise ValueError("monitor_stream_mismatch")
        if timestamp(previous["observed_at"]) >= observed:
            raise ValueError("monitor_scan_not_newer")

    source = _scan_source(scan)
    stream = {"class_key": scan["class_key"], "assignment_label": scan["assignment_label"],
              "source_id": source["source_id"], "source_name": source["source_name"],
              "source_kind": source["source_kind"]}
    if source.get("folder_id"):
        stream["folder_id"] = source["folder_id"]
    events, files = [], {}
    prior_files = dict((previous or {}).get("files") or {})
    scanned = {item["id"]: item for item in scan["files"]}

    def emit(kind, file_id, evidence, **detail):
        if kind not in EVENT_KINDS:
            raise ValueError("invalid_monitor_event")
        events.append({"event_id": deterministic_id("submission-event", {
            "assignment": scan["assignment_label"], "kind": kind, "file": file_id, "evidence": evidence}),
            "kind": kind, "file_id": file_id, "observed_at": scan["observed_at"], **detail})

    for file_id, item in scanned.items():
        prior = prior_files.get(file_id)
        mapping = mappings["by_file"].get(file_id)
        match = None
        if mapping:
            match = {"student_id": mapping["student_id"], "section_id": mapping["section_id"],
                     "identity_basis": mapping["identity_basis"],
                     "assessed_marker": mapping["assessed_marker"],
                     "marker_precision": mapping["marker_precision"],
                     "ic_posting_status": mapping["ic_posting_status"]}
        due_end, grace_end = _cutoff(due_date, mapping and extensions.get(mapping["student_id"]))
        modified = timestamp(item["modified_at"])
        record = {"name": item["name"], "modified_at": item["modified_at"],
                  "version": item.get("version"), "status": "present", "match": match,
                  "grace_ends_at": grace_end.isoformat() if grace_end else None}
        if prior is None:
            kind = "first_observed" if previous is None else "appeared"
            record.update(first_observed_at=scan["observed_at"], first_observed_kind=kind,
                          timing=_timing(previous is None, due_end, modified, observed),
                          revisions=[{"modified_at": item["modified_at"],
                                      "version": item.get("version"),
                                      "observed_at": scan["observed_at"]}])
            emit(kind, file_id, {"modified_at": item["modified_at"], "version": item.get("version")},
                 timing=record["timing"])
        else:
            record.update(first_observed_at=prior["first_observed_at"],
                          first_observed_kind=prior["first_observed_kind"],
                          timing=prior["timing"], revisions=list(prior["revisions"]))
            if prior["status"] != "present":
                emit("reappeared", file_id, {"modified_at": item["modified_at"]})
                record["status"] = "present"
            if (item["modified_at"], item.get("version")) != (prior["modified_at"], prior.get("version")):
                record["revisions"] = (record["revisions"]
                                       + [{"modified_at": item["modified_at"],
                                           "version": item.get("version"),
                                           "observed_at": scan["observed_at"]}])[-MAX_FILE_HISTORY:]
                after_due = bool(due_end and modified > due_end)
                emit("modified", file_id,
                     {"from": {"modified_at": prior["modified_at"], "version": prior.get("version")},
                      "to": {"modified_at": item["modified_at"], "version": item.get("version")}},
                     modified_after_due=after_due)
            if prior.get("match") is None and match is not None:
                emit("matched", file_id, {"basis": match["identity_basis"]})
        files[file_id] = record

    for file_id, prior in prior_files.items():
        if file_id in scanned:
            continue
        kept = dict(prior)
        if prior["status"] == "present":
            kept["status"] = "inaccessible"
            kept["inaccessible_since"] = scan["observed_at"]
            emit("inaccessible", file_id, {"last_modified_at": prior["modified_at"]})
        files[file_id] = kept

    # A same-scan pair of one appeared file and one newly inaccessible file for
    # the same verified student is flagged for review, never resolved silently.
    lost_by_student = {f["match"]["student_id"]: fid for fid, f in files.items()
                       if f["status"] == "inaccessible" and f.get("match")
                       and f.get("inaccessible_since") == scan["observed_at"]}
    for event in [e for e in list(events) if e["kind"] == "appeared"]:
        match = files[event["file_id"]].get("match")
        lost = match and lost_by_student.get(match["student_id"])
        if lost and lost != event["file_id"]:
            emit("possible_replacement", event["file_id"], {"previous_file": lost},
                 previous_file_id=lost)

    if previous and (previous.get("due_date") or None) != (due_date or None):
        emit("due_date_changed", "-", {"from": previous.get("due_date"), "to": due_date},
             previous_due_date=previous.get("due_date"), due_date=due_date)
    if len(files) > MAX_FILES or len(events) > MAX_EVENTS:
        raise ValueError("monitor_state_limit")

    def assessed_covers_current(record, match):
        """Only evidence that binds the assessment to the current revision counts.

        A same-day edit after an assessment must return to awaiting: a full
        assessment timestamp is compared as an instant, a date-only marker can
        never prove currency for a file modified on or after that same day, and
        unknown evidence never proves anything.
        """
        precision = match["marker_precision"]
        if precision == "timestamp":
            try:
                return timestamp(record["modified_at"]) <= timestamp(match["assessed_marker"])
            except ValueError:
                return False
        if precision == "date":
            day = re.sub(r"[^0-9]", "", match["assessed_marker"])[:8]
            modified_day = re.sub(r"[^0-9]", "", record["modified_at"])[:8]
            return bool(day) and len(day) == 8 and modified_day < day
        return False

    awaiting, review = [], []
    for file_id, record in sorted(files.items()):
        if record["status"] != "present":
            review.append({"file_id": file_id, "reason": "inaccessible_needs_review"})
            continue
        match = record.get("match")
        if match is None:
            review.append({"file_id": file_id, "reason": "unmatched_identity_needs_review"})
            awaiting.append(file_id)
        elif not assessed_covers_current(record, match):
            awaiting.append(file_id)
        if record["timing"] == "modified_after_due_needs_review":
            review.append({"file_id": file_id, "reason": "after_due_evidence_needs_review"})

    state = {"schema_version": VERSION, "derivation_version": DERIVATION_VERSION,
             "stream": stream,
             "observed_at": scan["observed_at"], "date": scan["date"], "due_date": due_date,
             "due_basis": due_basis,
             "timezone": TIMEZONE_NAME,
             "coverage": {"complete": True,
                          "source": scan["coverage"].get("source", source["source_kind"]),
                          "folders_checked": scan["coverage"].get("folders_checked", 0),
                          "forms_found": scan["coverage"].get("forms_found", 0),
                          "responses_checked": scan["coverage"].get("responses_checked"),
                          "matching_responses": scan["coverage"].get("matching_responses"),
                          "root_verified_files": scan["coverage"].get("root_verified_files")},
             "roster": {"source": "grading_workbook", "checked_at": mappings["checked_at"],
                        "coverage": "verified_file_mappings_only",
                        "ic_roster_reader": "not_integrated"},
             "files": files, "awaiting_assessment": sorted(set(awaiting)),
             "needs_review": review, "scan_count": ((previous or {}).get("scan_count") or 0) + 1,
             "baseline_established_at": (previous or {}).get("baseline_established_at")
             or scan["observed_at"], "external_writes": 0}
    fingerprint = content_hash({"files": {k: {"m": v["modified_at"], "v": v.get("version"),
                                              "s": v["status"]} for k, v in files.items()}})
    counts = {"present": sum(1 for f in files.values() if f["status"] == "present"),
              "inaccessible": sum(1 for f in files.values() if f["status"] != "present"),
              "matched": sum(1 for f in files.values() if f.get("match") and f["status"] == "present"),
              "unmatched": sum(1 for f in files.values() if not f.get("match") and f["status"] == "present"),
              "awaiting_assessment": len(state["awaiting_assessment"]),
              "needs_review": len(review), "events_this_scan": len(events)}
    return {"state": state, "events": events, "fingerprint": fingerprint, "counts": counts}
