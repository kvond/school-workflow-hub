"""Trusted-host evidence for a rejected roster bootstrap; never retries it.

This immutable diagnostic preserves both pending intents and every prior run.
Its observed dependency bookends are NOT an atomic dependency transaction and
never grant sync, grade, activation or retry eligibility.
"""
from copy import deepcopy
from datetime import timedelta
import re
from time import monotonic

from . import roster_bootstrap as rb
from .roster_sync import ROSTER_SYNC_SCOPE
from ..core.cloud_state import Claim, CloudState, Conflict, StoreUnavailable, bounded_json, identifier, instant, key
from ..core.runs import content_hash

SOURCE_SCOPE = {"workflow": "school-source-preparation", "class_key": "all",
                "assignment_key": key("sourcescope", "school-morning-preparation-and-repair")}
RESERVATION_SECONDS = 500
FINAL_CHECK_SECONDS = 200
READBACK_SECONDS = 280


def fail(reason):
    raise Conflict("roster_bootstrap_rejection_" + reason)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)


def _evidence_reference(value):
    """Opaque state-style reference or a bounded private JSON basename.

    Evidence references are preserved metadata, not database document IDs or
    paths to open. Keep the global identifier contract strict and never accept
    directories, traversal, control characters or arbitrary file suffixes here.
    """
    try:
        return identifier(value)
    except ValueError:
        if (not isinstance(value, str) or len(value) > 160
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.private\.json", value)):
            fail("evidence_reference_invalid")
        return value


def capability(value, workbook, identity):
    if (not isinstance(value, dict) or set(value) != {"workbook", "identity", "can_edit"}
            or value["workbook"] != workbook or value["identity"] != identity
            or type(value["can_edit"]) is not bool):
        fail("capability_unverified")
    return deepcopy(value)


class RejectedBootstrap:
    """One create-if-absent diagnostic under an actual shared source guard.

    All callbacks are trusted host readers, never request-body evidence. The
    caller keeps its one-shot attempt fence and passes a real acquired source
    Claim plus that same SourceOperations.before_write. No lease is acquired,
    renewed, cleared, released or stolen by this object.
    """
    def __init__(self, port, mirror, *, read_config, read_failure, source_claim, before_write):
        if (not isinstance(source_claim, Claim) or source_claim.completed
                or source_claim.task_id != key("task", SOURCE_SCOPE)
                or not all(callable(v) for v in (getattr(port, "commit_guarded", None),
                    getattr(mirror, "read", None), getattr(mirror, "write_capability", None),
                    read_config, read_failure, before_write))):
            fail("trusted_capabilities_required")
        self.port, self.mirror = port, mirror
        self.read_config, self.read_failure = read_config, read_failure
        self.source_claim, self.before_write = source_claim, before_write
        self.intent_id = key("roster-bootstrap-intent", ROSTER_SYNC_SCOPE)
        self.marker_id = key("roster-intent", ROSTER_SYNC_SCOPE)
        self.task_id = key("task", ROSTER_SYNC_SCOPE)

    def _proof(self):
        failure = deepcopy(self.read_failure())
        fields = {"schema", "observer", "trace_reference", "trace_sha256", "attempt_reference", "attempt_sha256",
                  "native_code_sha256", "proposal_sha256", "native_operation", "status", "provider_reason",
                  "original_source_claim", "historical_capability", "historical_capability_reference",
                  "historical_capability_sha256"}
        if (not isinstance(failure, dict) or set(failure) != fields
                or failure["schema"] != "roster-bootstrap-rejection-evidence/v1"
                or failure["native_operation"] != "spreadsheets.batchUpdate"
                or type(failure["status"]) is not int or failure["status"] != 403
                or failure["provider_reason"] is not None):
            fail("direct_403_evidence_required")
        for field in ("trace_sha256", "attempt_sha256", "native_code_sha256", "proposal_sha256", "historical_capability_sha256"):
            if not digest(failure[field]): fail("evidence_hash_invalid")
        identifier(failure["observer"])
        for field in ("trace_reference", "attempt_reference", "historical_capability_reference"):
            _evidence_reference(failure[field])
        try:
            original_source = Claim(**failure["original_source_claim"])
        except (TypeError, ValueError):
            fail("source_claim_unverified")
        if original_source.completed or original_source.task_id != self.source_claim.task_id:
            fail("source_claim_unverified")
        first = self.port.read([self.intent_id, self.marker_id, self.task_id])
        intent_doc = first.documents[self.intent_id]
        if intent_doc is None: fail("pending_intent_required")
        intent = intent_doc.value
        if intent.get("state") != "pending" or intent.get("sha256") != content_hash(intent.get("plan")):
            fail("pending_intent_required")
        domain = Claim(**intent["claim"])
        if (domain.completed or domain.task_id != self.task_id or domain.owner != failure["observer"]
                or domain.owner != original_source.owner or intent["sha256"] != failure["proposal_sha256"]
                or domain.input_hash != intent["sha256"]
                or domain.run_id != key("run", {"task": self.task_id, "request": intent["request_id"]})
                or original_source.run_id != key("run", {"task": original_source.task_id, "request": intent["request_id"]})):
            fail("attempt_binding_changed")
        plan = intent["plan"]
        rb.targets_valid(plan["targets"], plan["titles"])
        if any(str(t["sheet_id"]) in plan["original"]["sheets"] for t in plan["targets"]):
            fail("new_target_identity_invalid")
        rb._project_destination(plan)
        config = self.read_config()
        identity = config.get("HUB_READER_SERVICE_ACCOUNT")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com", identity):
            fail("writer_identity_unverified")
        old_cap = capability(failure["historical_capability"], plan["original"]["workbook"], identity)
        if old_cap["can_edit"] is not False: fail("historical_denial_required")
        if rb.basis(config) != plan["config_basis"] or self.mirror.read() != plan["original"]:
            fail("original_or_config_changed")
        current_cap = capability(self.mirror.write_capability(), plan["original"]["workbook"], identity)
        if self.read_failure() != failure: fail("failure_evidence_changed")
        ids = list(dict.fromkeys([self.intent_id, self.marker_id, self.task_id, domain.run_id,
                                 original_source.run_id, self.source_claim.task_id, self.source_claim.run_id]))
        snapshot = self.port.read(ids)
        if any(snapshot.documents[k] != first.documents[k] for k in first.documents):
            fail("dependency_changed")
        marker = {"state": "pending", "kind": "bootstrap", "request_id": intent["request_id"],
                  "bootstrap_intent": self.intent_id, "claim": intent["claim"]}
        if snapshot.documents[self.marker_id] is None or snapshot.documents[self.marker_id].value != marker:
            fail("marker_changed")
        task = snapshot.documents[self.task_id]
        if task is None or task.value.get("baseline") is not None:
            fail("domain_changed")
        # Expiry is preserved history. Any takeover, completion or owner/fence
        # change is a conflict; do not relabel another worker's work as rejected.
        if (task.value.get("active_run") != domain.run_id or task.value.get("owner") != domain.owner
                or task.value.get("fence") != domain.fence):
            fail("domain_changed")
        for claim in (domain, original_source, self.source_claim):
            run = snapshot.documents[claim.run_id]
            if (run is None or run.value.get("task_id") != claim.task_id or run.value.get("owner") != claim.owner
                    or run.value.get("fence") != claim.fence or run.value.get("input_hash") != claim.input_hash
                    or run.value.get("receipt") is not None):
                fail("run_changed")
        source = snapshot.documents[self.source_claim.task_id]
        if source is None: fail("source_owner_unavailable")
        CloudState._owns(self.source_claim, source.value, snapshot.read_time)
        dependencies = {k: {"version": v.version, "sha256": content_hash(v.value)}
                        for k, v in snapshot.documents.items() if k != self.source_claim.task_id}
        # The native mutation plan is independently reconstructed using the same
        # pure builder as the concrete writer; no shortened/guessed request.
        payload = {"schema": "roster-bootstrap-rejection/v1", "status": "rejected_not_applied",
                   "retry_allowed": False, "activation_allowed": False, "grade_eligibility_changed": False,
                   "dependency_basis": "observed_guarded_bookends_not_atomic_transaction",
                   "intent_reference": self.intent_id, "intent_version": intent_doc.version,
                   "intent_sha256": content_hash(intent), "proposal_sha256": intent["sha256"],
                   "original_workbook_sha256": content_hash(plan["original"]),
                   "native_batch_sha256": content_hash(rb.native_requests(plan)),
                   "native_evidence": failure, "current_capability": current_cap,
                   "dependencies": dependencies, "guard_claim": self.source_claim.__dict__}
        bounded_json(payload)
        rid = key("rosterbootstraprejection", {"intent": self.intent_id, "version": intent_doc.version,
                                              "sha256": content_hash(intent)})
        return {"record_id": rid, "payload": payload}, snapshot

    def prepare(self):
        proof, _ = self._proof()
        return {**proof, "sha256": content_hash(proof)}

    def record_rejected_bootstrap(self, expected):
        """One sidecar CAS then exact readback; never a native write or retry."""
        current = self.prepare()
        if current != expected: fail("prepared_proof_changed")
        rid, payload = current["record_id"], current["payload"]
        prior = self.port.read([rid]).documents[rid]
        if prior is not None:
            if prior.value != payload: fail("immutable_record_conflict")
            return {"status": "verified_rejection_record", "record_id": rid, "version": prior.version,
                    "retry_allowed": False, "activation_allowed": False, "native_writes": 0}
        final = {}
        def guard():
            self.before_write(required_seconds=RESERVATION_SECONDS)
            started = monotonic()
            proof, snapshot = self._proof()
            elapsed = max(0, monotonic() - started)
            if proof != {"record_id": rid, "payload": payload}: fail("final_proof_changed")
            source = snapshot.documents[self.source_claim.task_id]
            remaining = (instant(source.value["expires_at"]) - instant(snapshot.read_time)).total_seconds() - elapsed
            if elapsed > FINAL_CHECK_SECONDS or remaining < READBACK_SECONDS:
                fail("final_source_budget_unavailable")
            final["snapshot"] = snapshot
            final["dispatch_started"] = monotonic()
        error = None
        try:
            self.port.commit_guarded({rid: (payload, None)}, before_write=guard)
        except Exception as exc:
            error = exc
        # No second commit even after a response was lost. A failed final guard
        # is not a dispatched write and cannot be treated as persistence proof.
        if not final:
            if error: raise error
            fail("final_guard_unavailable")
        proof_started = monotonic()
        post, snapshot = self._proof()
        if monotonic() - proof_started > FINAL_CHECK_SECONDS:
            raise StoreUnavailable("roster_bootstrap_rejection_post_proof_timed_out") from error
        stored_started = monotonic()
        stored = self.port.read([rid]).documents[rid]
        if (monotonic() - stored_started > 30
                or monotonic() - final["dispatch_started"] > READBACK_SECONDS):
            raise StoreUnavailable("roster_bootstrap_rejection_post_readback_timed_out") from error
        if (post != {"record_id": rid, "payload": payload} or stored is None or stored.value != payload
                or any(snapshot.documents[k] != v for k, v in final["snapshot"].documents.items())):
            raise StoreUnavailable("roster_bootstrap_rejection_readback_unconfirmed") from error
        return {"status": "verified_rejection_record", "record_id": rid, "version": stored.version,
                "retry_allowed": False, "activation_allowed": False, "native_writes": 0}
