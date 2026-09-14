"""Read model for the EXISTING HTML Agenda; no dashboard, server or executor.

Caller supplies genuine receipts from its authorized source/test runs. Shape
validation cannot authenticate receipts. Never manufacture live verification.
"""
import copy
from datetime import datetime
import re

from school_workflow_hub.core.runs import deterministic_id

KINDS = {"assignment_links", "date_alignment", "grading_review", "roster_changes"}
STATUSES = {"not_checked", "in_progress", "passed", "needs_review", "blocked", "failed", "not_applicable"}
VERIFICATIONS = {"unverified", "synthetic_test", "live_read", "live_end_to_end"}


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_status_timestamp") from None


def _key(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", value))


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def _validate_check(item, *, synchronization=False):
    required = {"status", "verification", "last_checked_at", "evidence", "next_action"}
    if (not isinstance(item, dict) or not required.issubset(item)
            or (synchronization and set(item) != required)
            or not _choice(item["status"], STATUSES)
            or not _choice(item["verification"], VERIFICATIONS)):
        raise ValueError("invalid_operation_check")
    evidence = item["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ValueError("invalid_operation_evidence")
    for entry in evidence:
        if (not isinstance(entry, dict) or set(entry) != {"kind", "reference", "observed_at", "summary"}
                or not _choice(entry["kind"], {"test_receipt", "source_snapshot", "readback", "error_receipt"})
                or not _key(entry["reference"]) or not isinstance(entry["summary"], str)
                or len(entry["summary"]) > 500):
            raise ValueError("invalid_operation_evidence")
        _timestamp(entry["observed_at"])
    if evidence and item["last_checked_at"] is None:
        raise ValueError("evidence_requires_check_time")
    if item["last_checked_at"] is not None:
        checked = _timestamp(item["last_checked_at"])
        if any(_timestamp(e["observed_at"]) > checked for e in evidence):
            raise ValueError("evidence_after_check")
    if item["status"] == "passed":
        if item["verification"] == "unverified" or item["last_checked_at"] is None or not evidence:
            raise ValueError("unverified_pass_forbidden")
        kinds = {e["kind"] for e in evidence}
        required_kind = {"synthetic_test": "test_receipt", "live_read": "source_snapshot", "live_end_to_end": "readback"}[item["verification"]]
        if required_kind not in kinds:
            raise ValueError("verification_evidence_mismatch")
    if item["status"] == "not_checked" and (item["last_checked_at"] is not None or evidence or item["verification"] != "unverified"):
        raise ValueError("unchecked_operation_has_evidence")
    action = item["next_action"]
    if action is not None and (not isinstance(action, dict) or set(action) != {"label", "kind", "todoist_task_id"}
            or not _choice(action["kind"], {"review", "recheck", "connect", "open_task"})
            or not isinstance(action["label"], str) or not action["label"].strip() or len(action["label"]) > 300
            or (action["todoist_task_id"] is not None and not _key(action["todoist_task_id"]))):
        raise ValueError("invalid_operation_next_action")
    if item["status"] in {"needs_review", "blocked", "failed"} and action is None:
        raise ValueError("operation_next_action_required")
    if synchronization and item["status"] == "passed":
        if item["verification"] != "live_end_to_end" or not any(e["kind"] == "readback" for e in evidence):
            raise ValueError("automatic_sync_requires_live_readback")


def build_operations_snapshot(records, *, generated_at, connection_state="not_connected", sync_check=None):
    """Return agenda-operations/v1. Transport and saving are not implemented here.

    Display records beside existing Agenda lessons, joined by stable source IDs.
    Unverified data and synthetic passes never enable automatic synchronization.
    """
    generated = _timestamp(generated_at)
    if not _choice(connection_state, {"not_connected", "preview_only", "connected_readonly", "connected_live"}):
        raise ValueError("invalid_operation_connection")
    if not isinstance(records, (tuple, list)) or len(records) > 5000:
        raise ValueError("invalid_operation_records")
    operations, seen = [], set()
    for original in records:
        item = copy.deepcopy(original)
        if (not isinstance(item, dict) or set(item) != {"kind", "scope", "status", "verification", "last_checked_at", "evidence", "next_action"}
                or not _choice(item["kind"], KINDS)):
            raise ValueError("invalid_operation_record")
        scope = item["scope"]
        if (not isinstance(scope, dict) or set(scope) != {"class_key", "assignment_key", "week_key"}
                or not _key(scope["class_key"])
                or any(scope[k] is not None and not _key(scope[k]) for k in ("assignment_key", "week_key"))):
            raise ValueError("invalid_operation_scope")
        if item["kind"] != "roster_changes" and scope["assignment_key"] is None:
            raise ValueError("assignment_scope_required")
        _validate_check(item)
        if item["last_checked_at"] is not None and _timestamp(item["last_checked_at"]) > generated:
            raise ValueError("check_after_snapshot")
        identity = deterministic_id("agenda-operation", {"kind": item["kind"], "scope": scope})
        if identity in seen:
            raise ValueError("duplicate_operation_scope")
        seen.add(identity)
        item["id"] = identity
        operations.append(item)
    sync = copy.deepcopy(sync_check) if sync_check is not None else {
        "status": "not_checked", "verification": "unverified", "last_checked_at": None,
        "evidence": [], "next_action": None}
    _validate_check(sync, synchronization=True)
    if sync["last_checked_at"] is not None and _timestamp(sync["last_checked_at"]) > generated:
        raise ValueError("check_after_snapshot")
    active = sync["status"] == "passed" and sync["verification"] == "live_end_to_end" and connection_state == "connected_live"
    return {"schema_version": "agenda-operations/v1", "generated_at": generated_at,
            "connection_state": connection_state, "edit_saving_supported": False,
            "work_queue": {"system": "todoist", "project": "AIHS", "create_tasks": False},
            "automatic_sync": dict(sync, active=active),
            "operations": sorted(operations, key=lambda x: x["id"])}
