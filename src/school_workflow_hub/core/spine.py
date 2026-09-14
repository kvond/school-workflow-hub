"""Dry-run-only shared orchestration primitives.

The Hub's existing proposal queue remains the source for assignment-level
plans.  This module adds a small general ledger around that queue: immutable
work-item plans, dependencies, account-binding metadata, one-time approvals,
steps, artifacts, and audit events.  It deliberately has no live adapter or
write executor.  An approval never bypasses :class:`PermissionPolicy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3
from typing import Any, Dict, Mapping, Optional

from .database import transaction, utc_now
from .permissions import PermissionDenied, PermissionPolicy, ensure_dry_run
from .runs import canonical_json, content_hash, create_or_get_run, deterministic_id, finish_run
from .safe_logging import sanitize


class AuthorizationDenied(PermissionError):
    """A bound one-time approval could not authorize the requested operation."""


class UnsafePayloadError(ValueError):
    """A payload attempted to store a secret or arbitrary sensitive content."""


@dataclass(frozen=True)
class WorkItemResult:
    id: str
    created: bool
    plan_hash: str


@dataclass(frozen=True)
class AuthorizationRequest:
    """Every field is exact-match bound to a single approved plan."""

    actor_id: str
    scope: str
    target_system: str
    target_stable_id: str
    plan_hash: str


@dataclass(frozen=True)
class ApprovalGrant:
    id: str
    created: bool
    status: str


@dataclass(frozen=True)
class DryRunExecutionResult:
    run_id: str
    work_item_id: str
    artifact_id: str
    external_writes: int = 0
    live_model_calls: int = 0


_KNOWN_SCOPES = frozenset(
    {
        "local.read",
        "local.create",
        "external.read",
        "external.update",
        "destructive",
    }
)
_STEP_STATUSES = frozenset({"started", "completed", "blocked", "denied"})
_ACCOUNT_STATUSES = frozenset({"unverified", "verified", "revoked"})
_PERSONAL_DATA_KEY_PARTS = (
    "student",
    "learner",
    "submission",
    "roster",
    "grade",
    "email",
    "parent",
    "family",
    "attendance",
    "health",
    "medical",
)
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PERSON_IDENTIFIER_PATTERN = re.compile(r"\b(?:student|learner|submission)[:_-]", re.IGNORECASE)


def _contains_personal_or_student_data(value: Any, key: str = "") -> bool:
    """Fail closed rather than retaining identifiers in the shared generic ledger."""

    key_lower = key.lower()
    if any(part in key_lower for part in _PERSONAL_DATA_KEY_PARTS):
        return True
    if isinstance(value, Mapping):
        return any(_contains_personal_or_student_data(item, str(item_key)) for item_key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_personal_or_student_data(item) for item in value)
    if isinstance(value, str):
        return bool(_EMAIL_PATTERN.search(value) or _PERSON_IDENTIFIER_PATTERN.search(value))
    return False


def _specific_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if value.lower() in {"all", "any", "*"} or "*" in value or "?" in value:
        raise ValueError(f"{field} must be exact; wildcard or broad values are not allowed")
    if sanitize(value) != value:
        raise UnsafePayloadError(f"{field} contains sensitive-looking or unsupported content")
    if _contains_personal_or_student_data(value):
        raise UnsafePayloadError(f"{field} contains personal or student-identifying data")
    return value


def _specific_scope(value: str) -> str:
    value = _specific_text(value, "scope")
    if value not in _KNOWN_SCOPES:
        raise ValueError("scope is not a recognized exact permission")
    return value


def _safe_mapping(value: Optional[Mapping[str, Any]], field: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    raw = dict(value)
    cleaned = sanitize(raw)
    if cleaned != raw:
        raise UnsafePayloadError(f"{field} contains sensitive-looking or unsupported content")
    if _contains_personal_or_student_data(raw):
        raise UnsafePayloadError(f"{field} contains personal or student-identifying data")
    return cleaned


def _parse_expiry(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expires_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("expires_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("expires_at must include a timezone offset")
    return parsed


def _require_run(connection: sqlite3.Connection, run_id: str) -> None:
    row = connection.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown run: {run_id}")


def _append_audit_event(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor_id: Optional[str] = None,
    run_id: Optional[str] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> str:
    entity_type = _specific_text(entity_type, "entity_type")
    entity_id = _specific_text(entity_id, "entity_id")
    event_type = _specific_text(event_type, "event_type")
    if actor_id is not None:
        actor_id = _specific_text(actor_id, "actor_id")
    if run_id is not None:
        _require_run(connection, run_id)
    safe_detail = _safe_mapping(detail, "audit detail")
    created_at = utc_now()
    event_id = deterministic_id(
        "audit",
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "run_id": run_id,
            "detail": safe_detail,
            "created_at": created_at,
        },
    )
    connection.execute(
        "INSERT INTO audit_events "
        "(id, run_id, entity_type, entity_id, event_type, actor_id, detail_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, run_id, entity_type, entity_id, event_type, actor_id, canonical_json(safe_detail), created_at),
    )
    return event_id


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor_id: Optional[str] = None,
    run_id: Optional[str] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> str:
    """Append a sanitized event.  Events are append-only; callers never mutate them."""

    with transaction(connection):
        return _append_audit_event(
            connection,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor_id=actor_id,
            run_id=run_id,
            detail=detail,
        )


def create_or_get_work_item(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    kind: str,
    target_system: str,
    target_stable_id: str,
    required_permission: str,
    payload: Mapping[str, Any],
) -> WorkItemResult:
    """Store one immutable, idempotent synthetic plan and link it to its run."""

    kind = _specific_text(kind, "kind")
    target_system = _specific_text(target_system, "target_system")
    target_stable_id = _specific_text(target_stable_id, "target_stable_id")
    required_permission = _specific_scope(required_permission)
    safe_payload = _safe_mapping(payload, "work item payload")
    plan = {
        "schema_version": 1,
        "kind": kind,
        "target_system": target_system,
        "target_stable_id": target_stable_id,
        "required_permission": required_permission,
        "payload": safe_payload,
    }
    plan_hash = content_hash(plan)
    work_item_id = deterministic_id("work", plan)
    created = False
    with transaction(connection):
        _require_run(connection, run_id)
        existing = connection.execute(
            "SELECT id FROM work_items WHERE plan_hash = ?", (plan_hash,)
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO work_items "
                "(id, created_run_id, kind, target_system, target_stable_id, required_permission, plan_hash, plan_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    work_item_id,
                    run_id,
                    kind,
                    target_system,
                    target_stable_id,
                    required_permission,
                    plan_hash,
                    canonical_json(plan),
                    utc_now(),
                ),
            )
            _append_audit_event(
                connection,
                entity_type="work_item",
                entity_id=work_item_id,
                event_type="planned",
                run_id=run_id,
                detail={"plan_hash": plan_hash, "required_permission": required_permission},
            )
            created = True
        else:
            work_item_id = existing["id"]
        connection.execute(
            "INSERT OR IGNORE INTO run_work_items(run_id, work_item_id, role) VALUES (?, ?, 'planned')",
            (run_id, work_item_id),
        )
    return WorkItemResult(work_item_id, created, plan_hash)


def get_work_item(connection: sqlite3.Connection, work_item_id: str) -> Dict[str, Any]:
    row = connection.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown work item: {work_item_id}")
    return dict(row)


def add_work_item_dependency(
    connection: sqlite3.Connection,
    *,
    work_item_id: str,
    depends_on_work_item_id: str,
    dependency_kind: str = "requires",
) -> None:
    """Add a dependency only when it cannot form a cycle."""

    dependency_kind = _specific_text(dependency_kind, "dependency_kind")
    if work_item_id == depends_on_work_item_id:
        raise ValueError("a work item cannot depend on itself")
    with transaction(connection):
        get_work_item(connection, work_item_id)
        get_work_item(connection, depends_on_work_item_id)
        cycle = connection.execute(
            "WITH RECURSIVE required(id) AS ("
            " SELECT depends_on_work_item_id FROM work_item_dependencies WHERE work_item_id = ?"
            " UNION"
            " SELECT dependency.depends_on_work_item_id FROM work_item_dependencies AS dependency"
            " JOIN required ON dependency.work_item_id = required.id"
            ") SELECT 1 FROM required WHERE id = ? LIMIT 1",
            (depends_on_work_item_id, work_item_id),
        ).fetchone()
        if cycle is not None:
            raise ValueError("work item dependency would form a cycle")
        existing = connection.execute(
            "SELECT dependency_kind FROM work_item_dependencies WHERE work_item_id = ? AND depends_on_work_item_id = ?",
            (work_item_id, depends_on_work_item_id),
        ).fetchone()
        if existing:
            if existing["dependency_kind"] != dependency_kind:
                raise ValueError("dependency kind cannot change on an existing plan")
            return
        connection.execute(
            "INSERT OR IGNORE INTO work_item_dependencies "
            "(work_item_id, depends_on_work_item_id, dependency_kind) VALUES (?, ?, ?)",
            (work_item_id, depends_on_work_item_id, dependency_kind),
        )
        _append_audit_event(
            connection,
            entity_type="work_item",
            entity_id=work_item_id,
            event_type="dependency_added",
            detail={"depends_on_work_item_id": depends_on_work_item_id, "dependency_kind": dependency_kind},
        )


def record_run_step(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    name: str,
    status: str,
    work_item_id: Optional[str] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> str:
    """Record a sequential local step without implying an external operation."""

    name = _specific_text(name, "step name")
    if status not in _STEP_STATUSES:
        raise ValueError("unsupported run step status")
    safe_detail = _safe_mapping(detail, "step detail")
    with transaction(connection):
        _require_run(connection, run_id)
        if work_item_id is not None:
            get_work_item(connection, work_item_id)
        row = connection.execute(
            "SELECT COALESCE(MAX(step_index), 0) AS latest FROM run_steps WHERE run_id = ?", (run_id,)
        ).fetchone()
        step_index = int(row["latest"]) + 1
        step_id = deterministic_id(
            "step", {"run_id": run_id, "step_index": step_index, "name": name, "status": status}
        )
        connection.execute(
            "INSERT INTO run_steps "
            "(id, run_id, work_item_id, step_index, name, status, detail_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, run_id, work_item_id, step_index, name, status, canonical_json(safe_detail), utc_now()),
        )
        _append_audit_event(
            connection,
            entity_type="run_step",
            entity_id=step_id,
            event_type=f"step_{status}",
            run_id=run_id,
            detail={"name": name, "work_item_id": work_item_id},
        )
    return step_id


def record_artifact(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    kind: str,
    reference: str,
    metadata: Optional[Mapping[str, Any]] = None,
    work_item_id: Optional[str] = None,
) -> str:
    """Persist a synthetic/local artifact fingerprint, never the external content itself."""

    kind = _specific_text(kind, "artifact kind")
    reference = _specific_text(reference, "artifact reference")
    if not reference.startswith(("synthetic://", "local://")):
        raise ValueError("artifact reference must be synthetic:// or local:// in this foundation")
    safe_metadata = _safe_mapping(metadata, "artifact metadata")
    fingerprint = content_hash({"kind": kind, "reference": reference, "metadata": safe_metadata})
    artifact_id = deterministic_id(
        "artifact", {"run_id": run_id, "kind": kind, "reference": reference, "content_hash": fingerprint}
    )
    with transaction(connection):
        _require_run(connection, run_id)
        if work_item_id is not None:
            get_work_item(connection, work_item_id)
        existing = connection.execute(
            "SELECT id FROM artifacts WHERE run_id = ? AND kind = ? AND reference = ? AND content_hash = ?",
            (run_id, kind, reference, fingerprint),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        connection.execute(
            "INSERT INTO artifacts(id, run_id, work_item_id, kind, reference, content_hash, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, run_id, work_item_id, kind, reference, fingerprint, canonical_json(safe_metadata), utc_now()),
        )
        _append_audit_event(
            connection,
            entity_type="artifact",
            entity_id=artifact_id,
            event_type="recorded",
            run_id=run_id,
            detail={"kind": kind, "reference": reference, "work_item_id": work_item_id},
        )
    return artifact_id


def register_account_binding(
    connection: sqlite3.Connection,
    *,
    provider: str,
    account_alias: str,
    binding_reference: str,
    verification_status: str = "unverified",
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Register only non-secret account-routing metadata.

    ``binding_reference`` is a local alias such as ``school-browser-profile``;
    it is not an email address, cookie, token, or credential.
    """

    provider = _specific_text(provider, "provider")
    account_alias = _specific_text(account_alias, "account_alias")
    binding_reference = _specific_text(binding_reference, "binding_reference")
    if "@" in account_alias or "@" in binding_reference:
        raise ValueError("account metadata must use non-secret local aliases, not email addresses")
    if verification_status not in _ACCOUNT_STATUSES:
        raise ValueError("unsupported account binding verification status")
    safe_metadata = _safe_mapping(metadata, "account metadata")
    binding_id = deterministic_id(
        "account", {"provider": provider, "account_alias": account_alias, "binding_reference": binding_reference}
    )
    with transaction(connection):
        existing = connection.execute(
            "SELECT id, binding_reference FROM account_bindings WHERE provider = ? AND account_alias = ?",
            (provider, account_alias),
        ).fetchone()
        if existing is not None:
            if existing["binding_reference"] != binding_reference:
                raise ValueError("account alias is already bound to a different reference")
            return existing["id"]
        connection.execute(
            "INSERT INTO account_bindings "
            "(id, provider, account_alias, binding_reference, verification_status, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                binding_id,
                provider,
                account_alias,
                binding_reference,
                verification_status,
                canonical_json(safe_metadata),
                utc_now(),
            ),
        )
        _append_audit_event(
            connection,
            entity_type="account_binding",
            entity_id=binding_id,
            event_type="registered",
            detail={"provider": provider, "account_alias": account_alias, "verification_status": verification_status},
        )
    return binding_id


def _request_from_work_item(connection: sqlite3.Connection, request: AuthorizationRequest) -> sqlite3.Row:
    _specific_text(request.actor_id, "actor_id")
    _specific_scope(request.scope)
    _specific_text(request.target_system, "target_system")
    _specific_text(request.target_stable_id, "target_stable_id")
    if not isinstance(request.plan_hash, str) or len(request.plan_hash) != 64:
        raise ValueError("plan_hash must be a SHA-256 plan hash")
    row = connection.execute(
        "SELECT id, required_permission, target_system, target_stable_id, plan_hash FROM work_items WHERE plan_hash = ?",
        (request.plan_hash,),
    ).fetchone()
    if row is None:
        raise KeyError("approval request references an unknown immutable plan")
    if (
        row["required_permission"] != request.scope
        or row["target_system"] != request.target_system
        or row["target_stable_id"] != request.target_stable_id
    ):
        raise AuthorizationDenied("approval request does not exactly match the work item plan")
    return row


def issue_one_time_approval(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    request: AuthorizationRequest,
    grant_nonce: str,
    expires_at: str,
) -> ApprovalGrant:
    """Issue an exact, expiring, revocable approval record for one plan.

    The nonce is a non-secret operator reference used for idempotent issuance;
    a new approval must use a new reference.  This function still cannot turn
    on external writes.
    """

    grant_nonce = _specific_text(grant_nonce, "grant_nonce")
    expiry = _parse_expiry(expires_at)
    if expiry <= datetime.now(timezone.utc):
        raise ValueError("approval expiry must be in the future")
    with transaction(connection):
        _require_run(connection, run_id)
        item = _request_from_work_item(connection, request)
        existing = connection.execute(
            "SELECT id, status, approved_run_id FROM approval_grants "
            "WHERE actor_id = ? AND work_item_id = ? AND grant_nonce = ?",
            (request.actor_id, item["id"], grant_nonce),
        ).fetchone()
        if existing is not None:
            if existing["approved_run_id"] != run_id:
                raise ValueError("grant_nonce is already bound to a different run")
            return ApprovalGrant(existing["id"], False, existing["status"])
        grant_id = deterministic_id(
            "approval",
            {
                "actor_id": request.actor_id,
                "approved_run_id": run_id,
                "work_item_id": item["id"],
                "scope": request.scope,
                "target_system": request.target_system,
                "target_stable_id": request.target_stable_id,
                "plan_hash": request.plan_hash,
                "grant_nonce": grant_nonce,
                "expires_at": expires_at,
            },
        )
        connection.execute(
            "INSERT INTO approval_grants "
            "(id, work_item_id, approved_run_id, actor_id, scope, target_system, target_stable_id, plan_hash, grant_nonce, status, expires_at, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                grant_id,
                item["id"],
                run_id,
                request.actor_id,
                request.scope,
                request.target_system,
                request.target_stable_id,
                request.plan_hash,
                grant_nonce,
                expires_at,
                utc_now(),
            ),
        )
        _append_audit_event(
            connection,
            entity_type="approval_grant",
            entity_id=grant_id,
            event_type="issued",
            actor_id=request.actor_id,
            run_id=run_id,
            detail={"work_item_id": item["id"], "scope": request.scope, "expires_at": expires_at},
        )
    return ApprovalGrant(grant_id, True, "active")


def revoke_one_time_approval(
    connection: sqlite3.Connection, *, grant_id: str, actor_id: str
) -> None:
    """Revoke an active approval before it is consumed."""

    actor_id = _specific_text(actor_id, "actor_id")
    with transaction(connection):
        row = connection.execute("SELECT status, actor_id FROM approval_grants WHERE id = ?", (grant_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown approval grant: {grant_id}")
        if row["actor_id"] != actor_id:
            raise AuthorizationDenied("only the bound approval identity can revoke this grant")
        if row["status"] != "active":
            raise AuthorizationDenied("only an active approval can be revoked")
        connection.execute(
            "UPDATE approval_grants SET status = 'revoked', revoked_at = ? WHERE id = ?",
            (utc_now(), grant_id),
        )
        _append_audit_event(
            connection,
            entity_type="approval_grant",
            entity_id=grant_id,
            event_type="revoked",
            actor_id=actor_id,
        )


def authorize_and_consume(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    grant_id: str,
    request: AuthorizationRequest,
    policy: PermissionPolicy,
    mode: str = "dry_run",
) -> None:
    """Atomically use a matching one-time grant after central policy enforcement."""

    failure: Optional[str] = None
    ensure_dry_run(mode)
    with transaction(connection):
        _require_run(connection, run_id)
        row = connection.execute("SELECT * FROM approval_grants WHERE id = ?", (grant_id,)).fetchone()
        if row is None:
            failure = "unknown_grant"
        else:
            try:
                _request_from_work_item(connection, request)
            except (KeyError, ValueError, AuthorizationDenied):
                failure = "invalid_request"
            if failure is None and row["status"] != "active":
                failure = f"grant_{row['status']}"
            if failure is None and row["approved_run_id"] != run_id:
                failure = "grant_run_mismatch"
            if failure is None and _parse_expiry(row["expires_at"]) <= datetime.now(timezone.utc):
                connection.execute(
                    "UPDATE approval_grants SET status = 'expired' WHERE id = ? AND status = 'active'", (grant_id,)
                )
                failure = "grant_expired"
            if failure is None and (
                row["actor_id"] != request.actor_id
                or row["scope"] != request.scope
                or row["target_system"] != request.target_system
                or row["target_stable_id"] != request.target_stable_id
                or row["plan_hash"] != request.plan_hash
            ):
                failure = "grant_binding_mismatch"
            if failure is None:
                try:
                    policy.require(request.scope, f"{request.target_system}:{request.target_stable_id}")
                except PermissionDenied:
                    failure = "policy_denied"
            if failure is None:
                updated = connection.execute(
                    "UPDATE approval_grants SET status = 'consumed', consumed_at = ? WHERE id = ? AND status = 'active'",
                    (utc_now(), grant_id),
                ).rowcount
                if updated != 1:
                    failure = "grant_not_active"
        if failure is None:
            _append_audit_event(
                connection,
                entity_type="approval_grant",
                entity_id=grant_id,
                event_type="consumed",
                actor_id=request.actor_id,
                run_id=run_id,
                detail={"scope": request.scope, "target_system": request.target_system},
            )
        else:
            _append_audit_event(
                connection,
                entity_type="approval_grant",
                entity_id=grant_id,
                event_type="authorization_denied",
                actor_id=request.actor_id,
                run_id=run_id,
                detail={"reason": failure},
            )
    if failure is not None:
        raise AuthorizationDenied("one-time approval did not authorize this exact dry-run request")


def _existing_dry_run_artifact(
    connection: sqlite3.Connection, *, run_id: str, work_item_id: str
) -> Optional[str]:
    row = connection.execute(
        "SELECT id FROM artifacts WHERE run_id = ? AND work_item_id = ? AND kind = 'dry_run_plan'",
        (run_id, work_item_id),
    ).fetchone()
    return None if row is None else row["id"]


def _consumed_grant_matches_request(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    grant_id: str,
    request: AuthorizationRequest,
    policy: PermissionPolicy,
) -> bool:
    """Allow a local artifact to be reconciled after an interrupted dry run only.

    This never resumes an external operation: it requires the same immutable
    plan and current local permission, then merely permits recording the
    missing local preview artifact tied to an already consumed grant.
    """

    _request_from_work_item(connection, request)
    _require_run(connection, run_id)
    policy.require(request.scope, f"{request.target_system}:{request.target_stable_id}")
    row = connection.execute("SELECT * FROM approval_grants WHERE id = ?", (grant_id,)).fetchone()
    if row is None or row["status"] != "consumed":
        return False
    return (
        row["actor_id"] == request.actor_id
        and row["approved_run_id"] == run_id
        and row["scope"] == request.scope
        and row["target_system"] == request.target_system
        and row["target_stable_id"] == request.target_stable_id
        and row["plan_hash"] == request.plan_hash
    )


def execute_dry_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    work_item_id: str,
    grant_id: str,
    request: AuthorizationRequest,
    policy: PermissionPolicy,
    mode: str = "dry_run",
) -> DryRunExecutionResult:
    """Produce a local preview artifact after one-time authorization.

    This is intentionally the only execution gateway shipped with the Hub.  It
    cannot write externally, invoke a model, or be switched to write mode.
    """

    try:
        ensure_dry_run(mode)
    except PermissionDenied:
        record_run_step(
            connection,
            run_id=run_id,
            work_item_id=work_item_id,
            name="reject_non_dry_run_mode",
            status="blocked",
            detail={"reason": "write_mode_hard_denied"},
        )
        raise
    item = get_work_item(connection, work_item_id)
    if item["plan_hash"] != request.plan_hash:
        raise AuthorizationDenied("work item does not match the authorization request plan")
    existing_artifact = _existing_dry_run_artifact(
        connection, run_id=run_id, work_item_id=work_item_id
    )
    if existing_artifact is not None:
        return DryRunExecutionResult(run_id=run_id, work_item_id=work_item_id, artifact_id=existing_artifact)
    try:
        resume_consumed_grant = _consumed_grant_matches_request(
            connection,
            run_id=run_id,
            grant_id=grant_id,
            request=request,
            policy=policy,
        )
    except PermissionDenied as error:
        raise AuthorizationDenied("current policy cannot reconcile the local dry-run artifact") from error
    if resume_consumed_grant:
        record_run_step(
            connection,
            run_id=run_id,
            work_item_id=work_item_id,
            name="reconcile_consumed_dry_run",
            status="started",
        )
        artifact_id = record_artifact(
            connection,
            run_id=run_id,
            work_item_id=work_item_id,
            kind="dry_run_plan",
            reference=f"synthetic://dry-run/{work_item_id}",
            metadata={"plan_hash": request.plan_hash, "required_permission": request.scope},
        )
        append_audit_event(
            connection,
            entity_type="approval_grant",
            entity_id=grant_id,
            event_type="dry_run_reconciled",
            actor_id=request.actor_id,
            run_id=run_id,
            detail={"artifact_id": artifact_id, "work_item_id": work_item_id},
        )
        record_run_step(
            connection,
            run_id=run_id,
            work_item_id=work_item_id,
            name="reconcile_consumed_dry_run",
            status="completed",
            detail={"artifact_id": artifact_id},
        )
        return DryRunExecutionResult(run_id=run_id, work_item_id=work_item_id, artifact_id=artifact_id)
    record_run_step(
        connection,
        run_id=run_id,
        work_item_id=work_item_id,
        name="authorize_dry_run",
        status="started",
    )
    try:
        authorize_and_consume(
            connection,
            run_id=run_id,
            grant_id=grant_id,
            request=request,
            policy=policy,
            mode=mode,
        )
    except AuthorizationDenied:
        record_run_step(
            connection,
            run_id=run_id,
            work_item_id=work_item_id,
            name="authorize_dry_run",
            status="denied",
        )
        raise
    artifact_id = record_artifact(
        connection,
        run_id=run_id,
        work_item_id=work_item_id,
        kind="dry_run_plan",
        reference=f"synthetic://dry-run/{work_item_id}",
        metadata={"plan_hash": request.plan_hash, "required_permission": request.scope},
    )
    record_run_step(
        connection,
        run_id=run_id,
        work_item_id=work_item_id,
        name="record_dry_run_artifact",
        status="completed",
        detail={"artifact_id": artifact_id},
    )
    return DryRunExecutionResult(run_id=run_id, work_item_id=work_item_id, artifact_id=artifact_id)


def run_mock_end_to_end(connection: sqlite3.Connection) -> DryRunExecutionResult:
    """Exercise persistence, authorization, audit, and dry-run behavior locally.

    It uses only synthetic identifiers and a `local.create` preview scope.  A
    repeat observes the completed idempotent run rather than consuming a second
    approval.
    """

    run = create_or_get_run(connection, "automation_spine_demo", {"fixture": "automation-spine-v1"})
    work_item = create_or_get_work_item(
        connection,
        run_id=run.id,
        kind="render_preview",
        target_system="local_report",
        target_stable_id="synthetic:automation-spine-preview",
        required_permission="local.create",
        payload={"fixture": "automation-spine-v1", "output": "preview-only"},
    )
    if not run.created:
        artifact = connection.execute(
            "SELECT id FROM artifacts WHERE run_id = ? AND work_item_id = ? AND kind = 'dry_run_plan'",
            (run.id, work_item.id),
        ).fetchone()
        if artifact is not None:
            return DryRunExecutionResult(run.id, work_item.id, artifact["id"])
    register_account_binding(
        connection,
        provider="synthetic",
        account_alias="synthetic-school-account",
        binding_reference="synthetic-school-profile",
        verification_status="unverified",
    )
    request = AuthorizationRequest(
        actor_id="operator:synthetic",
        scope="local.create",
        target_system="local_report",
        target_stable_id="synthetic:automation-spine-preview",
        plan_hash=work_item.plan_hash,
    )
    grant = issue_one_time_approval(
        connection,
        run_id=run.id,
        request=request,
        grant_nonce="synthetic-demo-grant-v1",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    result = execute_dry_run(
        connection,
        run_id=run.id,
        work_item_id=work_item.id,
        grant_id=grant.id,
        request=request,
        policy=PermissionPolicy.from_allowed(["local.read", "local.create"]),
    )
    finish_run(
        connection,
        run.id,
        details={
            "automation_spine": "synthetic_only",
            "artifact_id": result.artifact_id,
            "external_writes": 0,
            "live_model_calls": 0,
        },
    )
    return result
