"""No-model review of an existing assessment; loading never writes or claims.

Legacy workbook rows are deliberately not turned back into judgments. A full
retained packet must reproduce their literal evidence, or the teacher supplies
a prepared packet for the existing review UI. Save binds either path to the
unchanged original record and exact current source.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

from . import assessment_save as saving, assessment_target, assessment_provider
from .firestore_state import configured_store
from .google_sheets import SheetsReader, SheetsReadError
from .grading_records import ASSESSMENT_STAGES
from .hosted_agenda import ConnectionError, service_token
from ..core.cloud_state import CloudState, Conflict, key
from ..core.runs import content_hash
from ..workflows import assessment_records as records

SCHEMA = "hub-assessment-review/v1"
BASIS_FIELDS = {"record_id", "record_sha256", "source"}
PROTECTED_SUBMISSION = ("assignment_key", "section_id", "section_name", "student_id",
    "student_id_kind", "learner_key", "display_name", "identity_basis", "source_url",
    "source_file_id", "source_revision", "source_sha256", "assessment_stage",
    "reconciliation_basis", "notes_required", "bank_required", "drafts_required",
    "ic_assignment_id")
EDITABLE_ANSWER = {"record_id", "meaningful_complete", "conceptual_level",
    "explanation_level", "conceptual_feedback", "assessment_date"}


def task_scope(packet):
    return {"workflow": "assessment-save", "class_key": packet["class_key"],
            "assignment_key": content_hash({"assignment": packet["assignment_key"],
                "identity": records.record_identity(packet)})[:40]}


def source_binding(capture):
    return {"file_id": capture["file"]["id"], "sha256": capture["sha256"],
            "version": capture["file"]["version"],
            "modified_at": capture["file"]["modified_at"],
            **{k: capture[k] for k in ("raw_sha256", "metadata_sha256") if k in capture}}


def read_workbook(config):
    if not saving.configured(config):
        raise ConnectionError("grading_records_not_connected")
    try:
        return saving._workbook(SheetsReader(service_token(
            config.get("HUB_READER_SERVICE_ACCOUNT", ""))), config["HUB_GRADING_SPREADSHEET_ID"])
    except SheetsReadError:
        raise ConnectionError("assessment_workbook_unavailable") from None


def baseline(workbook, record_id, *, active=True):
    if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", record_id):
        raise ValueError("invalid_assessment_review")
    found = [row for _, row in workbook["Submissions"]["rows"] if row.get("record_id") == record_id]
    if len(found) != 1:
        raise ValueError("assessment_review_record_unavailable")
    row = found[0]
    if active and (row.get("status") != "assessed" or row.get("hold_reason")
                   or row.get("superseded_by") or row.get("assessment_stage") not in ASSESSMENT_STAGES):
        raise ValueError("assessment_review_record_unavailable")
    answers = [a for _, a in workbook["Answers"]["rows"] if a.get("record_id") == record_id]
    pairs = [(a.get("stage"), a.get("question_id")) for a in answers]
    if not answers or len(set(pairs)) != len(pairs):
        raise ValueError("assessment_review_record_unavailable")
    return {"submission": deepcopy(row), "answers": deepcopy(answers)}


def projection(packet, capture, original):
    row = original["submission"]
    snapshot = {**capture, "captured_at": row.get("source_snapshot_date", "")}
    return records.build_rows(packet, records.completion(packet), snapshot,
        now=row["checked_at"], prior_score_exact=row.get("prior_score_exact", ""),
        prior_hold_reason=row.get("prior_hold_reason", ""))


def same_cell(field, left, right):
    if field in saving.NUMERIC:
        if left in (None, "") or right in (None, ""):
            return left in (None, "") and right in (None, "")
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    return str(left if left is not None else "") == str(right if right is not None else "")


def matches_packet(packet, capture, original):
    try:
        records.validate_packet(packet, capture["text"])
        assert_requirements(packet, capture)
        assert_source(packet, capture, original["submission"])
        generated = projection(packet, capture, original)
        if generated["record_id"] != original["submission"]["record_id"]:
            return False
        if any(not same_cell(k, v, ASSESSMENT_STAGES.get(original["submission"].get(k))
                            if k == "assessment_stage" else original["submission"].get(k))
               for k, v in generated["submission"].items()):
            return False
        saved = {(a["stage"], a["question_id"]): a for a in original["answers"]}
        return len(saved) == len(generated["answers"]) and all(
            (a["stage"], a["question_id"]) in saved and all(
                same_cell(k, v, saved[(a["stage"], a["question_id"])].get(k))
                for k, v in a.items()) for a in generated["answers"])
    except (ValueError, KeyError, TypeError, Conflict):
        return False


def assert_source(packet, capture, saved):
    source = packet.get("source", {})
    if (any(source.get(k) != source_binding(capture)[k] for k in
            ("file_id", "sha256", "version", "modified_at"))
            or saved.get("source_sha256") != capture["sha256"]
            or saved.get("source_revision") != capture["file"]["version"]):
        raise Conflict("submission_changed_reassess")


def assert_requirements(packet, capture):
    # Initial-checkpoint workbook flags do not distinguish required BANK/DRAFT
    # from optional work. Bind those roles to the exact unchanged source's
    # existing manifest/census parser, never infer them from saved counts.
    observed = records.derive_manifest(capture["text"])
    if any(set(packet["manifest"][field]) != set(observed[field]) for field in
           ("notes_ids", "bank_ids", "draft_ids", "optional_ids")):
        raise ValueError("assessment_review_protected_field_changed")


def validate_successor(packet, basis, workbook, capture):
    if not isinstance(basis, dict) or set(basis) != BASIS_FIELDS:
        raise ValueError("invalid_assessment_review")
    original = baseline(workbook, basis["record_id"])
    active = [row for _, row in saving._matching_rows(workbook["Submissions"], packet)
              if row.get("status") != "superseded"]
    if len(active) != 1 or active[0].get("record_id") != basis["record_id"]:
        raise Conflict("assessment_review_changed")
    if content_hash(original) != basis["record_sha256"]:
        raise Conflict("assessment_review_changed")
    if source_binding(capture) != basis["source"]:
        raise Conflict("submission_changed_reassess")
    assert_source(packet, capture, original["submission"])
    assert_requirements(packet, capture)
    generated = projection(packet, capture, original)
    if any(not same_cell(k, generated["submission"].get(k),
                        ASSESSMENT_STAGES.get(original["submission"].get(k))
                        if k == "assessment_stage" else original["submission"].get(k))
           for k in PROTECTED_SUBMISSION):
        raise ValueError("assessment_review_protected_field_changed")
    old = {(a["stage"], a["question_id"]): a for a in original["answers"]}
    new = {(a["stage"], a["question_id"]): a for a in generated["answers"]}
    if set(old) != set(new):
        raise ValueError("assessment_review_protected_field_changed")
    for pair, answer in new.items():
        if any(old[pair].get(field) and field not in answer for field in records.OPTIONAL_RESPONSE_KEYS):
            raise ValueError("assessment_review_protected_field_changed")
        for field, value in answer.items():
            if field in EDITABLE_ANSWER:
                continue
            # Choosing blank is an explicit existing review action. Its prior
            # quote survives in the superseded record, never overwritten.
            if field == "response" and value == "" and answer["meaningful_complete"] == "FALSE":
                continue
            if not same_cell(field, value, old[pair].get(field)):
                raise ValueError("assessment_review_protected_field_changed")
    return original


def no_pending_save(port, packet):
    task_id = key("task", task_scope(packet))
    doc = port.read([task_id]).documents[task_id]
    # Expiry is not permission to replay an unknown school write.
    if doc is not None and doc.value.get("active_run") is not None:
        raise Conflict("assessment_review_save_pending")
    return doc


class UnclaimedTaskPort:
    """CAS claim input that cannot adopt any active run, including an expired one.

    Check every claim-loop read. A competing commit after the read changes the
    task version, so native CAS refuses it; the retry then sees the active run
    and stops. Only claiming uses this view; renew/finish use the actual port.
    """
    def __init__(self, port, task_id):
        self.port, self.task_id = port, task_id

    def read(self, ids):
        snapshot = self.port.read(ids)
        doc = snapshot.documents.get(self.task_id)
        if doc is not None and doc.value.get("active_run") is not None:
            raise Conflict("assessment_review_save_pending")
        return snapshot

    def commit(self, changes):
        return self.port.commit(changes)


def load(assertion, record_id, config):
    # read_records authenticates before any workbook or source access.
    current = saving.read_records(assertion, config)
    row = next((r for r in current["submissions"] if r["record_id"] == record_id), None)
    if row is None:
        raise ValueError("assessment_review_record_unavailable")
    request = {k: row.get(k) for k in ("class_key", "assignment_key", "section_id", "section_name")}
    request.update(file_id=assessment_target.file_id(row), identity={})
    assessment_target.read_target(assertion, request, config)
    workbook = read_workbook(config)
    original = baseline(workbook, record_id)
    saved = original["submission"]
    if (assessment_target.file_id(saved) != request["file_id"]
            or any(saved.get(k) != request[k] for k in ("assignment_key", "section_id", "section_name"))):
        raise Conflict("assessment_review_changed")
    capture = saving.capture(assertion, request["file_id"], config)
    if saved.get("source_sha256") != capture["sha256"] or saved.get("source_revision") != capture["file"]["version"]:
        raise Conflict("submission_changed_reassess")
    identity = {k: saved.get(k, "") for k in records.IDENTITY_KEYS}
    packet_target = {**request, "identity": identity}
    store = CloudState(configured_store(config))
    task = no_pending_save(store.port, packet_target)
    packet = None
    retained_id = ((task.value.get("last_result") or {}).get("record_ids", {}).get("reviewed_packet")
                   if task else None)
    if retained_id:
        retained = store.port.read([retained_id]).documents[retained_id]
        if retained and retained.value.get("payload", {}).get("record_id") == record_id:
            candidate = retained.value["payload"].get("packet")
            if not matches_packet(candidate, capture, original):
                raise Conflict("assessment_review_changed")
            packet = candidate
    if packet is None:
        target = {**request, "stage": ASSESSMENT_STAGES[saved["assessment_stage"]]}
        draft = assessment_provider._stored_draft(store, request["file_id"], capture["sha256"], target)
        if draft and matches_packet(draft.get("packet"), capture, original):
            packet = draft["packet"]
    # Bookend the exact rows: a changing saved assessment never inherits proof.
    if baseline(read_workbook(config), record_id) != original:
        raise Conflict("assessment_review_changed")
    return {"schema_version": SCHEMA,
            "status": "saved_review_ready" if packet is not None else "saved_packet_required",
            "packet": deepcopy(packet), "base": original,
            "review_basis": {"record_id": record_id, "record_sha256": content_hash(original),
                             "source": source_binding(capture)},
            "external_writes": 0, "live_model_calls": 0}
