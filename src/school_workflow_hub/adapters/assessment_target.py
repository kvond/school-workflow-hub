"""Exact registry and section targets shared by assessment reads and writes."""
import re
from urllib.parse import urlparse


def file_id(row):
    explicit = str(row.get("source_file_id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{20,128}", explicit):
        return explicit
    url = urlparse(str(row.get("source_url") or ""))
    if url.scheme == "https" and url.hostname in {"drive.google.com", "docs.google.com"}:
        match = re.search(r"/d/([A-Za-z0-9_-]{20,128})(?:/|$)", url.path)
        if match:
            return match[1]
    return ""


def section_scope(records, class_key, assignment_key, section_id, section_name=None):
    assignments = [row for row in records.get("assignments", [])
                   if row.get("assignment_key") == assignment_key and row.get("class_key") == class_key]
    if len(assignments) != 1:
        raise ValueError("assessment_assignment_unknown")
    if not isinstance(section_id, str) or not section_id or section_id in {"all", "-"}:
        raise ValueError("assessment_section_required")
    sections = [row for row in records.get("sections", [])
                if row.get("class_key") == class_key and row.get("section_id") == section_id]
    if len(sections) != 1:
        raise ValueError("assessment_section_unverified")
    section = sections[0]
    if section_name is not None and section_name != section["section_name"]:
        raise ValueError("assessment_section_conflict")
    return section


def compatible_identity(rows):
    """Require one connected saved identity; filenames and display names are not keys."""
    keys = ("student_id", "learner_key")
    values = {key: {str(row.get(key)) for row in rows if row.get(key)} for key in keys}
    if any(len(found) > 1 for found in values.values()):
        raise ValueError("assessment_identity_conflict")
    identifiers = [{(key, str(row[key])) for key in keys if row.get(key)} for row in rows]
    if any(not found for found in identifiers):
        raise ValueError("assessment_identity_unverified")
    # A native-ID-only row and a learner-key-only row need an existing row
    # connecting those keys. Their shared file/scope is not proof of identity.
    connected = set(identifiers[0]) if identifiers else set()
    pending = identifiers[1:]
    while pending:
        linked = [found for found in pending if found & connected]
        if not linked:
            raise ValueError("assessment_identity_conflict")
        for found in linked:
            connected.update(found)
            pending.remove(found)
    return {key: next(iter(found), "") for key, found in values.items()}


def check_identity(identity, saved):
    if any(identity.get(key) and saved.get(key) and identity[key] != saved[key]
           for key in ("student_id", "learner_key")):
        raise ValueError("assessment_identity_conflict")


def check_record_targets(request, rows):
    """A reviewed file binding cannot silently move to another assignment/section."""
    target = tuple(request.get(key) for key in ("class_key", "assignment_key", "section_id"))
    expected_file = request.get("file_id") or request.get("source", {}).get("file_id")
    verified = [row for row in rows if file_id(row) == expected_file
                and row.get("status") in {"assessed", "roster_matched"} and not row.get("hold_reason")]
    for row in verified:
        if tuple(row.get(key) for key in ("class_key", "assignment_key", "section_id")) != target:
            raise ValueError("assessment_target_conflict")
        if request.get("section_name") and row.get("section_name") and row["section_name"] != request["section_name"]:
            raise ValueError("assessment_target_conflict")
    if not verified:
        return None
    identity = compatible_identity(verified)
    check_identity(request.get("identity", {}), identity)
    return {**max(verified, key=lambda row: row.get("checked_at", "")), **identity}


def read_target(assertion, request, config):
    from .grading_records import read_records
    if not isinstance(request.get("identity"), dict):
        raise ValueError("invalid_assessment_identity")
    records = read_records(assertion, config)
    section_scope(records, request.get("class_key"), request.get("assignment_key"),
                  request.get("section_id"), request.get("section_name"))
    return check_record_targets(request, records.get("submissions", []))


def check_cached(packet, request, identity):
    fields = ("class_key", "assignment_key", "section_id", "section_name", "stage")
    if (any(packet.get(key) != request.get(key) for key in fields)
            or any(packet.get("identity", {}).get(key) != identity.get(key)
                   for key in ("student_id", "learner_key", "display_name"))):
        raise ValueError("assessment_cached_target_conflict")
