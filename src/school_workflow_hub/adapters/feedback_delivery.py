"""Private per-feedback-version delivery state.

Assessment records remain authoritative for evidence and feedback text. This
adapter stores only the teacher-facing delivery lifecycle for one immutable
assessment record: ready -> printed -> handed_out. A later assessment has a new
record_id and therefore a separate lifecycle; history is never overwritten.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .firestore_state import configured_store
from ..core.cloud_state import Conflict, key

SCHEMA = "hub-feedback-delivery/v1"
ACTIONS = {"ready", "printed", "handed_out"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def document_id(record_id):
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("feedback_delivery_record")
    return key("feedback-delivery", {"record_id": record_id})


def _canonical(row):
    if not isinstance(row, dict):
        raise ValueError("feedback_delivery_record")
    required = ("record_id", "class_key", "assignment_key", "section_id")
    if any(not isinstance(row.get(name), str) or not row.get(name) for name in required):
        raise ValueError("feedback_delivery_record")
    if not isinstance(row.get("feedback"), str) or not row.get("feedback").strip():
        raise ValueError("feedback_delivery_record")
    return {
        "schema_version": SCHEMA,
        "record_id": row["record_id"],
        "class_key": row["class_key"],
        "assignment_key": row["assignment_key"],
        "assignment_title": str(row.get("assignment_title") or "")[:300],
        "section_id": row["section_id"],
        "section_name": str(row.get("section_name") or row["section_id"])[:200],
        "display_name": str(row.get("display_name") or "")[:200],
        "learner_key": str(row.get("learner_key") or row.get("student_id") or row.get("count_key") or "")[:200],
        "source_sha256": str(row.get("source_sha256") or "")[:128],
        "source_file_id": str(row.get("source_file_id") or "")[:200],
        "checked_at": str(row.get("checked_at") or "")[:80],
    }


def _initial(row, at=None):
    value = _canonical(row)
    value.update({
        "status": "ready",
        "ready_at": at or str(row.get("checked_at") or "") or _now(),
        "printed_at": None,
        "handed_out_at": None,
    })
    return value


def ensure_ready(row, config):
    """Create a ready record if absent; never regress printed/handed state."""
    port = configured_store(config)
    ident = document_id(row.get("record_id"))
    for _ in range(4):
        snap = port.read([ident])
        old = snap.documents[ident]
        if old:
            if old.value.get("record_id") != row["record_id"]:
                raise Conflict("feedback_delivery_identity_conflict")
            return dict(old.value)
        value = _initial(row, snap.read_time)
        try:
            port.commit({ident: (value, None)})
            return value
        except Conflict:
            continue
    raise Conflict("feedback_delivery_contention")


def mark(row, action, config):
    """Advance one canonical saved feedback record; actions are idempotent."""
    if action not in ACTIONS:
        raise ValueError("feedback_delivery_action")
    port = configured_store(config)
    ident = document_id(row.get("record_id"))
    for _ in range(4):
        snap = port.read([ident])
        old = snap.documents[ident]
        value = dict(old.value) if old else _initial(row, snap.read_time)
        canonical = _canonical(row)
        for name, expected in canonical.items():
            if name in value and name not in {"assignment_title", "section_name", "display_name",
                                               "learner_key", "source_sha256", "source_file_id",
                                               "checked_at"} and value[name] != expected:
                raise Conflict("feedback_delivery_identity_conflict")
            value[name] = expected
        if action == "printed" and not value.get("printed_at"):
            value["printed_at"] = snap.read_time
        elif action == "handed_out" and not value.get("handed_out_at"):
            value["handed_out_at"] = snap.read_time
        if value.get("handed_out_at"):
            value["status"] = "handed_out"
        elif value.get("printed_at"):
            value["status"] = "printed"
        else:
            value["status"] = "ready"
        expected = old.version if old else None
        if old and value == old.value:
            return value
        try:
            port.commit({ident: (value, expected)})
            return value
        except Conflict:
            continue
    raise Conflict("feedback_delivery_contention")


def states(rows, config):
    """Return delivery state for every canonical saved row with feedback.

    Legacy feedback with no state record projects as ready without writing on a
    GET, so old saved reports immediately appear in the queue.
    """
    canonical = []
    for row in rows or ():
        try:
            canonical.append((row, _canonical(row)))
        except ValueError:
            continue
    ids = [document_id(item["record_id"]) for _, item in canonical]
    port = configured_store(config)
    snap = port.read(ids) if ids else None
    out = []
    for row, item in canonical:
        doc = snap.documents[document_id(item["record_id"])] if snap else None
        state = dict(doc.value) if doc else _initial(row, row.get("checked_at") or "")
        out.append(state)
    return out


def queue(rows, config):
    """Current non-superseded feedback grouped for the teacher cockpit."""
    current = [
        row for row in rows or ()
        if row.get("status") not in {"superseded", "identity_unverified"}
        and not row.get("hold_reason") and row.get("section_id")
        and isinstance(row.get("feedback"), str) and row.get("feedback").strip()
    ]
    delivery = states(current, config)
    return {
        "schema_version": "hub-feedback-queue/v1",
        "ready": [item for item in delivery if item["status"] == "ready"],
        "printed": [item for item in delivery if item["status"] == "printed"],
        "handed_out": [item for item in delivery if item["status"] == "handed_out"],
        "external_writes": 0,
    }
