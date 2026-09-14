"""Proposed-action queue and safe execution-state lifecycle."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .database import transaction, utc_now
from .models import ProposedAction
from .permissions import PermissionPolicy, require_external_execution
from .runs import canonical_json, deterministic_id
from .safe_logging import sanitize


LOCAL_ATTEMPT_STATUSES = frozenset(("blocked", "interrupted"))


def _parse_expiry(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expires_at must be an ISO-8601 timestamp when provided")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("expires_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("expires_at must include a timezone offset")
    return parsed


def _dependency_map(
    primary_revision_id: str, source_dependencies: Optional[Mapping[str, str]]
) -> Dict[str, str]:
    dependencies = {"primary": primary_revision_id}
    for role, revision_id in (source_dependencies or {}).items():
        if not isinstance(role, str) or not role.strip():
            raise ValueError("source dependency role must be a non-empty string")
        if role == "primary":
            raise ValueError("primary source dependency is supplied separately")
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise ValueError("source dependency revision ID must be a non-empty string")
        dependencies[role] = revision_id
    if len(set(dependencies.values())) != len(dependencies):
        raise ValueError("an action cannot list one source revision under multiple roles")
    return dependencies


def _action_freshness(
    connection: sqlite3.Connection, action_id: str
) -> tuple[sqlite3.Row, Optional[tuple[str, str]]]:
    row = connection.execute(
        "SELECT id, target_system, target_stable_id, state, review_status, expires_at "
        "FROM proposed_actions WHERE id = ?",
        (action_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown action: {action_id}")
    if row["state"] != "proposed":
        raise ValueError(f"action is not executable in state {row['state']}")
    expiry = _parse_expiry(row["expires_at"])
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        return row, ("expired", "action_expired")
    inactive_dependency = connection.execute(
        "SELECT 1 FROM action_source_dependencies AS dependency "
        "JOIN source_revisions AS source ON source.id = dependency.source_revision_id "
        "WHERE dependency.action_id = ? AND source.active = 0 LIMIT 1",
        (action_id,),
    ).fetchone()
    if inactive_dependency is not None:
        return row, ("superseded", "source_dependency_not_current")
    return row, None


def _mark_freshness_failure(
    connection: sqlite3.Connection, action_id: str, state: str, reason: str
) -> None:
    connection.execute(
        "UPDATE proposed_actions SET state = ?, review_status = 'needs_review', verification_json = ? "
        "WHERE id = ?",
        (state, canonical_json({"reason": reason}), action_id),
    )


def propose_action(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    action_type: str,
    target_system: str,
    target_stable_id: str,
    source_stable_id: str,
    source_revision_id: str,
    expected_state: Dict[str, Any],
    proposed_state: Dict[str, Any],
    evidence: Dict[str, Any],
    required_permission: str,
    expires_at: Optional[str] = None,
    source_dependencies: Optional[Mapping[str, str]] = None,
) -> ProposedAction:
    _parse_expiry(expires_at)
    dependencies = _dependency_map(source_revision_id, source_dependencies)
    identity = {
        "action_type": action_type,
        "target_system": target_system,
        "target_stable_id": target_stable_id,
        "source_stable_id": source_stable_id,
        "source_revision_id": source_revision_id,
        "source_dependencies": tuple(sorted(dependencies.items())),
        "expected_state": expected_state,
        "proposed_state": proposed_state,
        "evidence": evidence,
        "required_permission": required_permission,
        "expires_at": expires_at,
    }
    base_idempotency_key = deterministic_id("idem", identity)
    with transaction(connection):
        # Validate inside the same writer transaction as the insert: another
        # writer cannot make a source stale between validation and planning.
        source_row = connection.execute(
            "SELECT stable_object_id, active FROM source_revisions WHERE id = ?", (source_revision_id,)
        ).fetchone()
        if source_row is None:
            raise KeyError(f"unknown source revision: {source_revision_id}")
        if source_row["stable_object_id"] != source_stable_id:
            raise ValueError("source_stable_id does not match source_revision_id")
        if not source_row["active"]:
            raise ValueError("cannot propose an action from an inactive source revision")
        for role, dependency_revision_id in dependencies.items():
            dependency = connection.execute(
                "SELECT active FROM source_revisions WHERE id = ?", (dependency_revision_id,)
            ).fetchone()
            if dependency is None:
                raise KeyError(f"unknown source dependency: {dependency_revision_id}")
            if not dependency["active"]:
                raise ValueError(f"cannot propose an action from inactive dependency {role}")
        # Replaying the identical active plan returns its same action.  If a
        # source returns to an earlier content hash after an intervening change,
        # its old action is deliberately still `superseded`; create one fresh
        # review proposal rather than reviving that historical plan.
        generation = 0
        while True:
            plan_identity = identity if generation == 0 else {
                **identity,
                "replan_generation": generation,
            }
            idempotency_key = (
                base_idempotency_key
                if generation == 0
                else deterministic_id("idem", plan_identity)
            )
            action_id = deterministic_id("action", plan_identity)
            existing = connection.execute(
                "SELECT id, state FROM proposed_actions WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is None:
                break
            if existing["state"] != "superseded":
                return ProposedAction(existing["id"], False, existing["state"])
            generation += 1
        connection.execute(
            "INSERT INTO proposed_actions "
            "(id, run_id, action_type, target_system, target_stable_id, source_stable_id, source_revision_id, "
            "expected_state_json, proposed_state_json, evidence_json, required_permission, idempotency_key, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                run_id,
                action_type,
                target_system,
                target_stable_id,
                source_stable_id,
                source_revision_id,
                canonical_json(expected_state),
                canonical_json(proposed_state),
                canonical_json(evidence),
                required_permission,
                idempotency_key,
                expires_at,
                utc_now(),
            ),
        )
        connection.executemany(
            "INSERT INTO action_source_dependencies(action_id, source_revision_id, dependency_role) "
            "VALUES (?, ?, ?)",
            [(action_id, dependency_revision_id, role) for role, dependency_revision_id in dependencies.items()],
        )
        # A different expected state, evidence bundle, permission, expiry, or
        # source dependency is a new plan, not an amendment to an old approval.
        connection.execute(
            "UPDATE proposed_actions SET state = 'superseded', review_status = 'needs_review', verification_json = ? "
            "WHERE action_type = ? AND target_system = ? AND target_stable_id = ? "
            "AND source_stable_id = ? AND id <> ? AND state = 'proposed'",
            (
                canonical_json({"reason": "replacement_plan_created", "replacement_action_id": action_id}),
                action_type,
                target_system,
                target_stable_id,
                source_stable_id,
                action_id,
            ),
        )
    return ProposedAction(action_id, True, "proposed")


def create_review_item(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    kind: str,
    severity: str,
    payload: Dict[str, Any],
    action_id: Optional[str] = None,
) -> str:
    identity = {"run": run_id, "kind": kind, "action": action_id, "payload": payload}
    item_id = deterministic_id("review", identity)
    idempotency_key = deterministic_id("review_idem", identity)
    with transaction(connection):
        connection.execute(
            "INSERT OR IGNORE INTO review_items "
            "(id, run_id, action_id, kind, severity, payload_json, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, run_id, action_id, kind, severity, canonical_json(payload), idempotency_key, utc_now()),
        )
    return item_id


def validate_action(connection: sqlite3.Connection, action_id: str) -> None:
    """Record that a current, unexpired local proposal passed structural checks."""
    failure: Optional[tuple[str, str]] = None
    with transaction(connection):
        _, failure = _action_freshness(connection, action_id)
        if failure is None:
            connection.execute(
                "UPDATE proposed_actions SET review_status = 'validated' "
                "WHERE id = ? AND state = 'proposed'",
                (action_id,),
            )
        else:
            _mark_freshness_failure(connection, action_id, *failure)
    if failure is not None:
        raise ValueError("action is no longer current and must be replanned")


def attempt_external_execution(
    connection: sqlite3.Connection, action_id: str, policy: PermissionPolicy
) -> None:
    """Hard-denied precursor for future real adapters; no executor is invoked."""
    failure: Optional[tuple[str, str]] = None
    with transaction(connection):
        action, failure = _action_freshness(connection, action_id)
        if failure is not None:
            _mark_freshness_failure(connection, action_id, *failure)
        elif action["review_status"] != "validated":
            raise ValueError("action must be structurally validated before execution")
    if failure is not None:
        raise ValueError("action is no longer current and must be replanned")
    require_external_execution(policy, f"{action['target_system']}:{action['target_stable_id']}")


def record_execution_attempt(
    connection: sqlite3.Connection,
    action_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
) -> str:
    """Record a non-success local recovery observation; it cannot execute anything."""
    if status not in LOCAL_ATTEMPT_STATUSES:
        raise ValueError("the synthetic foundation cannot record a success-like execution status")
    with transaction(connection):
        action = connection.execute("SELECT state FROM proposed_actions WHERE id = ?", (action_id,)).fetchone()
        if action is None:
            raise KeyError(f"unknown action: {action_id}")
        if status == "interrupted" and action["state"] != "proposed":
            raise ValueError("only a proposed local plan can enter needs_reconciliation")
        row = connection.execute(
            "SELECT COALESCE(MAX(attempt_number), 0) AS latest FROM execution_attempts WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        attempt_number = int(row["latest"]) + 1
        attempt_id = deterministic_id("attempt", {"action": action_id, "number": attempt_number})
        needs_reconciliation = int(status == "interrupted")
        connection.execute(
            "INSERT INTO execution_attempts "
            "(id, action_id, attempt_number, status, started_at, finished_at, result_json, error_code, reconcile_required) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                action_id,
                attempt_number,
                status,
                utc_now(),
                utc_now(),
                canonical_json(sanitize(result or {})),
                error_code,
                needs_reconciliation,
            ),
        )
        if needs_reconciliation:
            connection.execute(
                "UPDATE proposed_actions SET state = 'needs_reconciliation' WHERE id = ?", (action_id,)
            )
    return attempt_id


def reconcile_interrupted_action(
    connection: sqlite3.Connection, action_id: str, observed_state: Dict[str, Any]
) -> None:
    """Unknown remote outcome becomes review work; it is never blindly retried."""
    with transaction(connection):
        connection.execute(
            "UPDATE execution_attempts SET reconcile_required = 0, result_json = ? "
            "WHERE action_id = ? AND reconcile_required = 1",
            (canonical_json(sanitize(observed_state)), action_id),
        )
        connection.execute(
            "UPDATE proposed_actions SET state = 'needs_review', review_status = 'needs_review', verification_json = ? "
            "WHERE id = ?",
            (canonical_json({"reconciled": True, "observed_state": sanitize(observed_state)}), action_id),
        )


def get_action(connection: sqlite3.Connection, action_id: str) -> Dict[str, Any]:
    row = connection.execute("SELECT * FROM proposed_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown action: {action_id}")
    return dict(row)
