"""Explicit fictional-data storage acceptance; cannot accept student payloads."""
import re
import uuid

from school_workflow_hub.core.cloud_state import Busy, CloudState, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

PROBE_VERSION = "hub-storage-probe/v1"


def scope(probe_id):
    if not isinstance(probe_id, str) or not re.fullmatch(r"[a-z0-9-]{8,64}", probe_id):
        raise ValueError("invalid_storage_probe_id")
    return {"workflow": "storage-verification", "class_key": "synthetic", "assignment_key": probe_id}


def run_probe(factory, probe_id, revision):
    """One fixed nonstudent record and real CAS contention, using runtime IAM."""
    if not isinstance(revision, str) or not re.fullmatch(r"[a-z0-9-]{1,100}", revision):
        raise ValueError("cloud_revision_required")
    task_scope = scope(probe_id)
    store = CloudState(factory())
    task_id = store.create_task(task_scope)
    claim = store.claim(task_id, owner="probe-" + uuid.uuid4().hex, request_id="primary",
                        input_hash=content_hash(PROBE_VERSION), ttl_seconds=120)
    if claim.completed:
        return read_probe(factory, probe_id, revision)
    try:
        CloudState(factory()).claim(task_id, owner="competitor-" + uuid.uuid4().hex,
            request_id="competitor", input_hash=content_hash(PROBE_VERSION))
    except Busy:
        pass
    else:
        raise StoreUnavailable("storage_concurrency_check_failed")
    sample = {"schema_version": PROBE_VERSION, "fictional": True,
              "message": "Fictional storage acceptance only; no student record.",
              "created_revision": revision, "duplicate_claim_rejected": True}
    receipt = store.finish(claim, records={"sample": sample})
    retry = CloudState(factory()).finish(claim, records={"sample": sample})
    if retry != receipt:
        raise StoreUnavailable("storage_idempotency_check_failed")
    return read_probe(factory, probe_id, revision)


def read_probe(factory, probe_id, revision):
    task_id = key("task", scope(probe_id))
    port = factory()
    task_doc = port.read([task_id]).documents[task_id]
    if task_doc is None or not task_doc.value.get("last_result"):
        raise StoreUnavailable("storage_probe_not_completed")
    receipt = task_doc.value["last_result"]
    run_id, record_id = receipt["run_id"], receipt["record_ids"]["sample"]
    snapshot = port.read([run_id, record_id])
    run, record = snapshot.documents[run_id], snapshot.documents[record_id]
    if run is None or record is None or run.value.get("receipt") != receipt:
        raise StoreUnavailable("storage_probe_readback_failed")
    sample = record.value.get("payload", {})
    if (sample.get("schema_version") != PROBE_VERSION or sample.get("fictional") is not True
            or sample.get("duplicate_claim_rejected") is not True
            or record.value.get("scope") != scope(probe_id)
            or receipt["result_hash"] != content_hash({"records": {"sample": sample},
                                                      "baseline": None, "expected_checkpoint": None})):
        raise StoreUnavailable("storage_probe_readback_failed")
    restarted = bool(revision and sample.get("created_revision") and revision != sample["created_revision"])
    return {"schema_version": PROBE_VERSION, "checked_at": snapshot.read_time,
            "status": "restart_verified" if restarted else "write_readback_verified",
            "write_readback_verified": True, "duplicate_claim_rejected": True,
            "restart_verified": restarted, "persistent_storage_verified": restarted,
            "student_records_processed": 0, "school_writes": 0,
            "save_enabled": False, "run_enabled": False}
