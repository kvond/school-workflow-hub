"""No-model import of a complete reviewed first assessment.

The absence proof contains existing native context, not a fabricated saved
assessment. Identity-only intake rows remain unchanged throughout Save.
"""
from copy import deepcopy
import re

from . import assessment_save as saving, assessment_review as review, assessment_target
from .firestore_state import configured_store
from ..core.cloud_state import Conflict, bounded_json, instant
from ..core.runs import content_hash
from ..workflows import assessment_records as records


MODE = "first_current_review"
BASIS_FIELDS = {"mode", "source", "packet_sha256", "target_sha256", "absence_sha256"}


def _source(capture):
    binding = review.source_binding(capture)
    if (set(binding) != {"file_id", "sha256", "version", "modified_at", "raw_sha256", "metadata_sha256"}
            or any(not isinstance(binding[k], str) or not re.fullmatch(r"[a-f0-9]{64}", binding[k])
                   for k in ("sha256", "raw_sha256", "metadata_sha256"))):
        raise ValueError("invalid_assessment_initial_review")
    instant(binding["modified_at"])
    return binding


def basis_fields(basis):
    if (not isinstance(basis, dict) or set(basis) != BASIS_FIELDS or basis.get("mode") != MODE
            or any(not isinstance(basis.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", basis[k])
                   for k in ("packet_sha256", "target_sha256", "absence_sha256"))
            or not isinstance(basis.get("source"), dict)):
        raise ValueError("invalid_assessment_initial_review")


def _packet(value):
    if (not isinstance(value, dict) or set(value) != records.PACKET_KEYS
            or not isinstance(value.get("source"), dict) or not isinstance(value.get("identity"), dict)):
        raise ValueError("invalid_assessment_initial_review")
    bounded_json(value)
    return deepcopy(value)


def identity_only(row, answers=()):
    """Only an explicitly unscored, unheld intake row is not an assessment."""
    empty = ("assessment_stage", "assessment_version", "hold_reason", "superseded_by",
             "content_score", "revision_score", "content_score_exact", "revision_score_exact",
             "feedback", "notes_completed", "notes_required", "bank_completed", "bank_required",
             "drafts_completed", "drafts_required", "checked_date", "source_snapshot_date",
             "prior_score_exact", "prior_hold_reason", "prior_readiness", "reconciliation_basis",
             "content_points_possible")
    return (row.get("status") == "roster_matched" and not answers
            and row.get("ic_posting_status") in (None, "", "not_posted")
            and all(row.get(k) in (None, "") for k in empty))


def registry_context(assertion, packet, config):
    """The section proof is a native roster projection, outside the four tables."""
    current = saving.read_records(assertion, config)
    section = assessment_target.section_scope(current, packet.get("class_key"),
        packet.get("assignment_key"), packet.get("section_id"), packet.get("section_name"))
    assessment_target.check_record_targets(packet, current.get("submissions", []))
    assignments = [r for r in current["assignments"]
                   if r.get("assignment_key") == packet.get("assignment_key")
                   and r.get("class_key") == packet.get("class_key")]
    return {"assignment": deepcopy(assignments[0]), "section": deepcopy(section)}


def _proof(packet, workbook, context, *, replay=False):
    if assessment_target.file_id({"source_url": packet["source"]["url"]}) != packet["source"]["file_id"]:
        raise ValueError("invalid_assessment_initial_review")
    assignment = [r for _, r in workbook["Assignments"]["rows"]
                  if r.get("assignment_key") == packet["assignment_key"]]
    if len(assignment) != 1 or assignment[0].get("class_key") != packet["class_key"]:
        raise ValueError("assessment_assignment_unknown")
    identity = packet["identity"]
    target = {k: deepcopy(packet[k]) for k in ("class_key", "assignment_key", "section_id",
        "section_name", "identity", "stage", "stage_reference", "ic_assignment_id")}
    target.update(file_id=packet["source"]["file_id"], source_url=packet["source"]["url"],
                  assignment=assignment[0], registry=context)
    wanted_id = records.record_id(packet)
    relevant = []
    for position, row in workbook["Submissions"]["rows"]:
        same_file = assessment_target.file_id(row) == packet["source"]["file_id"]
        same_identity = any(identity.get(k) and identity[k] == row.get(k)
                            for k in ("student_id", "learner_key"))
        same_scope = (row.get("assignment_key") == packet["assignment_key"]
                      and row.get("section_id") == packet["section_id"])
        if not same_file and not (same_scope and same_identity):
            continue
        if (not same_scope or row.get("section_name") != packet["section_name"]):
            raise Conflict("assessment_initial_review_changed")
        relevant.append((position, row))
    task_identities = {("ic:" + row["student_id"]) if k == "student_id" else row[k]
        for row in [identity, *(r for _, r in relevant)]
        for k in ("student_id", "learner_key") if row.get(k)}
    if len(task_identities) != 1:
        # Older writers coordinate each key separately. A new first-import mode
        # cannot safely migrate or atomically fence multiple existing identities.
        raise Conflict("assessment_initial_identity_reconciliation_required")
    if relevant:
        # Every key must be explicitly connected; names and a shared file are
        # never an identity bridge. The complete approved identity is retained.
        existing = assessment_target.compatible_identity([r for _, r in relevant])
        assessment_target.check_identity(identity, existing)
        if (any(identity.get(k) and not existing.get(k) for k in ("student_id", "learner_key"))
                or records.record_identity(packet) != (
                    "ic:" + existing["student_id"] if existing["student_id"] else existing["learner_key"])):
            raise Conflict("assessment_initial_review_changed")
    linked_ids = {r.get("record_id") for _, r in relevant} | {wanted_id}
    linked = [(p, a) for p, a in workbook["Answers"]["rows"]
              if a.get("record_id") in linked_ids
              or assessment_target.file_id(a) == packet["source"]["file_id"]]
    preserved, allowed = [], []
    for position, row in relevant:
        answers = [a for _, a in linked if a.get("record_id") == row.get("record_id")]
        if replay and row.get("record_id") == wanted_id:
            if row.get("status") != "assessed" or row.get("hold_reason") or row.get("superseded_by"):
                raise Conflict("assessment_initial_review_changed")
            allowed.append(row)
        elif identity_only(row, answers):
            preserved.append((position, row))
        else:
            raise Conflict("assessment_initial_predecessor_exists")
    if replay and len(allowed) != 1:
        raise Conflict("assessment_initial_review_changed")
    if any(a.get("record_id") != wanted_id or not replay for _, a in linked):
        raise Conflict("assessment_initial_predecessor_exists")
    # Replays remove only the exact newly saved assessment; all earlier native
    # intake context must still reproduce the original absence proof.
    absence = {"submissions": preserved, "answers": [], "assignment": assignment[0]}
    return content_hash(target), content_hash(absence)


def validate(packet, basis, workbook, capture, context, *, replay=False):
    basis_fields(basis)
    if content_hash(packet) != basis["packet_sha256"]:
        raise ValueError("assessment_initial_packet_changed")
    current_source = _source(capture)
    if (basis["source"] != current_source or any(packet["source"].get(k) != current_source[k]
            for k in ("file_id", "sha256", "version", "modified_at"))):
        raise Conflict("submission_changed_reassess")
    records.validate_packet(packet, capture["text"])
    review.assert_requirements(packet, capture)
    result = records.completion(packet)
    target, absence = _proof(packet, workbook, context, replay=replay)
    if target != basis["target_sha256"] or absence != basis["absence_sha256"]:
        raise Conflict("assessment_initial_review_changed")
    return result


def _schema(packet, result, capture, workbook):
    rows = records.build_rows(packet, result, capture, now=saving._now())
    compatibility = saving.schema_compatibility(workbook, rows)
    if compatibility["outcome"] != "ready":
        raise saving.SchemaHeld(compatibility)
    records.aligned_cells(workbook["Submissions"]["headers"], rows["submission"], numeric=saving.NUMERIC)
    for answer in rows["answers"]:
        records.aligned_cells(workbook["Answers"]["headers"], answer)


def prepare(assertion, payload, config):
    """Verify a supplied approved packet without a claim, cache or model call."""
    if not isinstance(payload, dict) or set(payload) != {"packet"} or not isinstance(payload["packet"], dict):
        raise ValueError("invalid_assessment_initial_review")
    packet = _packet(payload["packet"])
    context = registry_context(assertion, packet, config)  # Authenticate first.
    assessment_target.read_target(assertion, packet, config)
    capture = saving.capture(assertion, packet.get("source", {}).get("file_id", ""), config)
    records.validate_packet(packet, capture["text"])
    review.assert_requirements(packet, capture)
    if any(packet["source"].get(k) != _source(capture)[k]
           for k in ("file_id", "sha256", "version", "modified_at")):
        raise Conflict("submission_changed_reassess")
    workbook = review.read_workbook(config)
    target, absence = _proof(packet, workbook, context)
    basis = {"mode": MODE, "source": _source(capture),
             "packet_sha256": content_hash(packet), "target_sha256": target, "absence_sha256": absence}
    result = validate(packet, basis, workbook, capture, context)
    _schema(packet, result, capture, workbook)
    port = configured_store(config)
    review.no_pending_save(port, packet)
    assessment_target.read_target(assertion, packet, config)
    final_context = registry_context(assertion, packet, config)
    final_workbook = review.read_workbook(config)
    final_capture = saving.capture(assertion, packet["source"]["file_id"], config)
    validate(packet, basis, final_workbook, final_capture, final_context)
    _schema(packet, result, final_capture, final_workbook)
    if final_workbook != workbook:
        raise Conflict("assessment_initial_review_changed")
    review.no_pending_save(port, packet)
    return {"schema_version": review.SCHEMA, "status": "first_packet_ready", "packet": packet,
            "scores": result["scores"], "counts": result["counts"], "record_id": records.record_id(packet),
            "review_basis": basis, "external_writes": 0, "live_model_calls": 0}


def readback(assertion, payload, config):
    """Resolve an uncertain first Save by reading exact evidence, never Save."""
    if (not isinstance(payload, dict) or set(payload) != {"packet", "review_basis"}
            or not isinstance(payload.get("packet"), dict)):
        raise ValueError("invalid_assessment_initial_review")
    packet, basis = _packet(payload["packet"]), deepcopy(payload["review_basis"])
    basis_fields(basis)
    bounded_json(payload)
    context = registry_context(assertion, packet, config)
    assessment_target.read_target(assertion, packet, config)
    capture = saving.capture(assertion, packet.get("source", {}).get("file_id", ""), config)
    workbook = review.read_workbook(config)
    result = validate(packet, basis, workbook, capture, context, replay=True)
    _schema(packet, result, capture, workbook)
    review.assert_replay_packet(configured_store(config), packet)
    rows = records.build_rows(packet, result, capture, now=saving._now())
    receipt = saving._verify_readback(saving.read_records(assertion, config), rows, [])
    final_context = registry_context(assertion, packet, config)
    final_workbook = review.read_workbook(config)
    final_capture = saving.capture(assertion, packet["source"]["file_id"], config)
    validate(packet, basis, final_workbook, final_capture, final_context, replay=True)
    if final_workbook != workbook:
        raise Conflict("assessment_initial_review_changed")
    return {**receipt, "schema_version": saving.SAVE_VERSION, "status": "already_saved",
            "unchanged_rerun": True, "source": review.source_binding(final_capture),
            "readback_verified": True, "external_writes": 0, "live_model_calls": 0}
