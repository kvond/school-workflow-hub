"""Common preview contracts and persistence for the synthetic Hub workflows.

The existing source/action/review tables remain the sole planning ledger. This
module adds a common output envelope, not another executor or data store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import re
import sqlite3
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from .actions import create_review_item, propose_action
from .models import SourceRevision
from .runs import content_hash, create_or_get_run, deterministic_id, finish_run
from .safe_logging import SENSITIVE_VALUE_PATTERN
from .sources import record_source_revision
from .spine import record_artifact


@dataclass(frozen=True)
class EvidenceRef:
    source_type: str
    stable_id: str
    revision: str
    reference: str


@dataclass(frozen=True)
class PreviewChange:
    stable_id: str
    assignment_key: str
    target_system: str
    target_id: str
    field: str
    old_value: Any
    proposed_value: Any
    evidence: Tuple[EvidenceRef, ...]
    confidence: float
    reversible: bool
    approval_status: str = "pending"
    status: str = "proposed"


@dataclass(frozen=True)
class WorkflowIssue:
    code: str
    severity: str
    assignment_key: Optional[str] = None
    retryable: bool = False


@dataclass(frozen=True)
class WorkflowPreview:
    workflow: str
    changes: Tuple[PreviewChange, ...] = ()
    issues: Tuple[WorkflowIssue, ...] = ()
    artifacts: Tuple[Any, ...] = ()
    external_writes: int = 0
    live_model_calls: int = 0


_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_PRIVATE_KEYS = frozenset({
    "name", "student_name", "learner_name", "email", "email_address",
    "student_id", "real_student_id", "raw_text", "submission_text",
    "student_content", "response_body", "credentials", "password", "token",
})


def assert_safe_preview(value: Any, key: str = "") -> None:
    """Reject private/credential data before hashing, storing, or displaying it.

Only fixture hosts are accepted in this synthetic build. A real reader requires
an approved private storage boundary, not a flag on this validator.
"""
    if key.lower() in _PRIVATE_KEYS:
        raise ValueError("private data is not accepted by synthetic previews")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            assert_safe_preview(child, str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_safe_preview(child)
    elif isinstance(value, str):
        if _EMAIL.search(value) or SENSITIVE_VALUE_PATTERN.search(value):
            raise ValueError("sensitive or unsupported text in synthetic preview")
        for match in _URL.findall(value):
            parsed = urlsplit(match)
            host = (parsed.hostname or "").lower()
            if parsed.username or parsed.password or not (
                host == "example.invalid" or host.endswith(".example.invalid")
            ):
                raise ValueError("only fixture URLs are accepted in synthetic previews")
        if "answerableteaching.com" in value.lower():
            raise ValueError("prohibited destination in preview")
    elif value is not None and not isinstance(value, (int, float, bool)):
        raise ValueError("unsupported preview value")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite preview value")


def validate_preview(preview: WorkflowPreview) -> None:
    if not isinstance(preview, WorkflowPreview) or not preview.workflow:
        raise ValueError("a named workflow preview is required")
    if preview.external_writes != 0 or preview.live_model_calls != 0:
        raise ValueError("only zero-write, zero-model previews are supported")
    assert_safe_preview(asdict(preview))
    identities = set()
    target_fields = set()
    all_evidence = {}
    for change in preview.changes:
        if not all(isinstance(x, str) and x.strip() for x in (
            change.stable_id, change.assignment_key, change.target_system,
            change.target_id, change.field,
        )):
            raise ValueError("change identity fields are required")
        if change.target_id.upper() in {"UNVERIFIED", "UNCONFIGURED"}:
            raise ValueError("unknown target must enter the exception queue")
        if change.stable_id in identities:
            raise ValueError("duplicate change identity")
        target_field = (change.target_system, change.target_id, change.field)
        if target_field in target_fields:
            raise ValueError("conflicting or duplicate target field")
        identities.add(change.stable_id)
        target_fields.add(target_field)
        if not change.evidence:
            raise ValueError("every proposed change needs source evidence")
        if (isinstance(change.confidence, bool) or not isinstance(change.confidence, (int, float))
                or not math.isfinite(change.confidence) or not 0 <= change.confidence <= 1):
            raise ValueError("confidence must be between zero and one")
        if not isinstance(change.reversible, bool):
            raise ValueError("reversibility must be explicit")
        if change.approval_status != "pending" or change.status != "proposed":
            raise ValueError("a generated preview cannot authorize itself")
        if change.old_value == change.proposed_value:
            raise ValueError("already-correct values must not generate changes")
        seen_sources = set()
        for evidence in change.evidence:
            if not all(isinstance(x, str) and x.strip() for x in asdict(evidence).values()):
                raise ValueError("evidence identity and revision are required")
            if not evidence.reference.startswith(("synthetic://", "local://")):
                raise ValueError("evidence must use a synthetic or local reference")
            source_key = (evidence.source_type, evidence.stable_id)
            if source_key in seen_sources:
                raise ValueError("duplicate or conflicting source evidence")
            seen_sources.add(source_key)
            identity = (evidence.revision, evidence.reference)
            if source_key in all_evidence and all_evidence[source_key] != identity:
                raise ValueError("conflicting evidence across changes")
            all_evidence[source_key] = identity
    for issue in preview.issues:
        if not issue.code or issue.severity not in {"info", "warning", "error", "blocked"}:
            raise ValueError("invalid workflow issue")


def combine_previews(workflow: str, previews: Tuple[WorkflowPreview, ...]) -> WorkflowPreview:
    """Coalesce identical target changes; conflicting plans become review work."""
    grouped = {}
    issues = set()
    artifacts = {}
    for preview in previews:
        validate_preview(preview)
        issues.update(preview.issues)
        for artifact in preview.artifacts:
            artifacts[content_hash(artifact)] = artifact
        for change in preview.changes:
            grouped.setdefault((change.target_system, change.target_id, change.field), []).append(change)
    changes = []
    for key, candidates in sorted(grouped.items()):
        first = candidates[0]
        evidence = {}
        conflict = False
        for item in candidates:
            if (item.assignment_key, item.old_value, item.proposed_value) != (first.assignment_key, first.old_value, first.proposed_value):
                conflict = True
            for ref in item.evidence:
                source_key = (ref.source_type, ref.stable_id)
                if source_key in evidence and evidence[source_key] != ref:
                    conflict = True
                evidence[source_key] = ref
        if conflict:
            issues.add(WorkflowIssue("conflicting_workflow_proposals", "error", first.assignment_key))
            continue
        merged = replace(first, evidence=tuple(evidence[k] for k in sorted(evidence)),
                         confidence=min(c.confidence for c in candidates),
                         reversible=all(c.reversible for c in candidates))
        identity = asdict(merged)
        identity.pop("stable_id")
        changes.append(replace(merged, stable_id=deterministic_id("combined-change", identity)))
    result = WorkflowPreview(workflow, tuple(changes),
                             tuple(sorted(issues, key=lambda i: (i.assignment_key or "", i.code, i.severity))),
                             tuple(artifacts[k] for k in sorted(artifacts)))
    validate_preview(result)
    return result


def persist_preview(connection: sqlite3.Connection, preview: WorkflowPreview) -> dict:
    """Persist a validated preview idempotently using the established tables."""
    validate_preview(preview)
    payload = asdict(preview)
    # Preflight every source before persisting anything. Replaying an old
    # observation is not evidence that the actual source reverted; a genuine
    # revert must arrive with a newly observed revision identifier.
    for change in preview.changes:
        for evidence in change.evidence:
            existing = connection.execute("SELECT * FROM source_revisions WHERE id = ?", (evidence.revision,)).fetchone()
            if existing:
                if (existing["source_type"] != evidence.source_type or
                        existing["stable_object_id"] != evidence.stable_id or
                        existing["source_reference"] != evidence.reference or not existing["active"]):
                    raise ValueError("evidence revision identity is inconsistent or stale")
            else:
                prior = connection.execute(
                    "SELECT active FROM source_revisions WHERE source_type = ? "
                    "AND stable_object_id = ? AND content_hash = ?",
                    ("preview_evidence:" + evidence.source_type, evidence.stable_id,
                     content_hash({"revision": evidence.revision, "reference": evidence.reference})),
                ).fetchone()
                if prior and not prior["active"]:
                    raise ValueError("historical preview evidence requires a fresh observation")
    run = create_or_get_run(connection, preview.workflow, {"preview": payload})
    if not run.created and run.status == "completed" and connection.execute(
        "SELECT 1 FROM proposed_actions WHERE run_id = ? AND state <> 'proposed' LIMIT 1", (run.id,)
    ).fetchone():
        raise ValueError("completed preview proposals changed; reconciliation required")
    action_ids = []
    issue_ids = []
    # Validate the whole input above before making any durable record.
    for change in preview.changes:
        revisions = []
        for evidence in change.evidence:
            existing = connection.execute("SELECT * FROM source_revisions WHERE id = ?", (evidence.revision,)).fetchone()
            if existing:
                if (existing["source_type"] != evidence.source_type or
                        existing["stable_object_id"] != evidence.stable_id or
                        existing["source_reference"] != evidence.reference or not existing["active"]):
                    raise ValueError("evidence revision identity is inconsistent or stale")
                revisions.append(SourceRevision(existing["id"], existing["source_type"],
                                                existing["stable_object_id"], existing["content_hash"], False))
            else:
                # Pointer-only observations must never replace a richer source
                # record maintained by student_work or another existing module.
                revisions.append(record_source_revision(
                    connection, "preview_evidence:" + evidence.source_type, evidence.stable_id,
                    {"revision": evidence.revision, "reference": evidence.reference},
                    evidence.reference, run.id,
                ))
        first = revisions[0]
        action = propose_action(
            connection, run_id=run.id,
            action_type="preview_" + change.field,
            target_system=change.target_system, target_stable_id=change.target_id,
            source_stable_id=first.stable_object_id, source_revision_id=first.id,
            source_dependencies={"evidence_" + str(i): ref.id for i, ref in enumerate(revisions[1:], 1)},
            expected_state={change.field: change.old_value},
            proposed_state={change.field: change.proposed_value},
            evidence={"assignment_key": change.assignment_key,
                      "change_id": change.stable_id,
                      "confidence": change.confidence,
                      "reversible": change.reversible,
                      "approval_status": change.approval_status},
            required_permission="external.update",
        )
        action_ids.append(action.id)
    for issue in preview.issues:
        issue_ids.append(create_review_item(
            connection, run_id=run.id, kind=issue.code,
            severity=issue.severity, payload=asdict(issue),
        ))
    # Artifacts retain only the digest in the generic spine. Preview content is
    # returned to the caller and never mistaken for approved student feedback.
    artifact_ids = []
    for artifact in preview.artifacts:
        digest = content_hash(artifact)
        artifact_ids.append(record_artifact(
            connection, run_id=run.id, kind="workflow_preview",
            reference="synthetic://previews/" + digest,
            metadata={"fingerprint": digest},
        ))
    if run.created or run.status != "completed":
        finish_run(connection, run.id, details={
            "change_count": len(action_ids), "issue_count": len(issue_ids),
            "artifact_count": len(artifact_ids),
        })
    return {"run_id": run.id, "action_ids": tuple(action_ids),
            "issue_ids": tuple(issue_ids), "artifact_ids": tuple(artifact_ids),
            "external_writes": 0, "live_model_calls": 0}
