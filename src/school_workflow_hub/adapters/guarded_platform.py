"""Fail-closed platform contract and a strictly local lifecycle simulator.

The real adapter is deliberately inert even with an approval. The separately
named fake accepts only synthetic targets and has no callback, API, browser or
transport injection point. Its apply/rollback operate on in-memory dictionaries
and require the shared spine's exact, run-bound local approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Dict, Mapping, Optional, Protocol, Tuple

from school_workflow_hub.adapters.contracts import LocalSyntheticAdapter
from school_workflow_hub.core.models import AdapterResult
from school_workflow_hub.core.permissions import PermissionPolicy, require_external_execution
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.core.spine import (
    AuthorizationDenied, AuthorizationRequest, authorize_and_consume,
    create_or_get_work_item, record_run_step,
)
from school_workflow_hub.workflows.schoology_gradebook import canonicalize_url


_FIELDS = frozenset({"title", "due_date", "deck_reference", "destination_id", "publication", "week_key", "marking_period"})


@dataclass(frozen=True)
class PlatformSnapshot:
    target_id: str
    revision: str
    fields: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class SyntheticPlatformPlan:
    run_id: str
    target_id: str
    before: PlatformSnapshot
    after: Tuple[Tuple[str, str], ...]
    work_item_id: str
    plan_hash: str
    operation: str = "apply"


class GuardedPlatformContract(Protocol):
    """Planning and applying are distinct; no method implies authorization."""
    def inspect(self, target_id: str) -> AdapterResult: ...
    def snapshot(self, target_id: str) -> PlatformSnapshot | AdapterResult: ...
    def plan(self, run_id: str, snapshot: PlatformSnapshot, proposed: Mapping[str, str]) -> SyntheticPlatformPlan | AdapterResult: ...
    def apply(self, plan: SyntheticPlatformPlan, *, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> AdapterResult: ...
    def readback(self, plan: SyntheticPlatformPlan) -> AdapterResult: ...
    def plan_rollback(self, run_id: str, plan: SyntheticPlatformPlan) -> SyntheticPlatformPlan | AdapterResult: ...
    def rollback(self, plan: SyntheticPlatformPlan, *, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> AdapterResult: ...


class GuardedPlatformAdapter:
    """Unconfigured real Schoology/IC shell; external mutations always deny."""
    def __init__(self, system: str):
        if system not in {"schoology", "infinite_campus"}:
            raise ValueError("unsupported platform")
        self.system = system

    def inspect(self, target_id: str) -> AdapterResult:
        return AdapterResult("not_configured", reason="live platform reader is unavailable")

    def snapshot(self, target_id: str) -> AdapterResult:
        return AdapterResult("not_configured", reason="a verified live inspection is required")

    def plan(self, *args, **kwargs) -> AdapterResult:
        return AdapterResult("not_configured", reason="no verified live snapshot; use the offline reconciliation preview")

    def apply(self, plan, *, grant_id=None, request=None, policy: PermissionPolicy) -> AdapterResult:
        require_external_execution(policy, self.system)
        raise AssertionError("external policy must never permit execution")

    def readback(self, plan) -> AdapterResult:
        return AdapterResult("not_configured", reason="no live execution or readback is available")

    def plan_rollback(self, *args, **kwargs) -> AdapterResult:
        return AdapterResult("not_configured", reason="no verified live snapshot is available")

    def rollback(self, plan, *, grant_id=None, request=None, policy: PermissionPolicy) -> AdapterResult:
        require_external_execution(policy, self.system)
        raise AssertionError("external policy must never permit rollback")


class SyntheticInterruption(RuntimeError):
    """A declared fake failure; read back before continuing this local plan."""


def _validate_fields(fields: Mapping[str, str]) -> Tuple[Tuple[str, str], ...]:
    if set(fields) - _FIELDS:
        raise ValueError("unsupported synthetic platform field")
    for field, value in fields.items():
        if not isinstance(value, str) or "@" in value or any(ord(c) < 32 for c in value):
            raise ValueError("only non-identifying synthetic field values are supported")
        if field == "deck_reference":
            canonical = canonicalize_url(value)
            if not canonical.startswith("https://example.invalid/"):
                raise ValueError("the fake accepts only example.invalid deck destinations")
        elif field == "destination_id" and not value.startswith("synthetic:"):
            raise ValueError("the fake accepts only synthetic destination IDs")
    return tuple(sorted(fields.items()))


class LocalSyntheticPlatformAdapter(LocalSyntheticAdapter):
    """In-memory fake with no reachable live execution path.

    A successful local apply retains its receipt, allowing a same-run retry to
    reconcile a failure after the dictionary changed. A fresh adapter loses that
    fake state deliberately; it must not pretend to have resumed a live system.
    """
    def __init__(self, connection: sqlite3.Connection, targets: Mapping[str, Mapping[str, str]], *, login_available: bool = True):
        if any(not target.startswith("synthetic:") for target in targets):
            raise ValueError("only synthetic targets are accepted")
        self._connection = connection
        self._targets = {target: dict(_validate_fields(fields)) for target, fields in targets.items()}
        self._login_available = login_available
        self._snapshots: Dict[str, PlatformSnapshot] = {}
        self._plans: Dict[str, SyntheticPlatformPlan] = {}
        self._receipts: Dict[str, tuple] = {}
        self._applied: set[str] = set()
        self._verified: set[str] = set()
        self._failure: Optional[str] = None
        self.local_apply_count = 0
        self.external_writes = 0
        self.network_calls = 0

    def inject_failure_once(self, stage: str) -> None:
        if stage not in {"before_apply", "after_apply"}:
            raise ValueError("unsupported synthetic failure stage")
        self._failure = stage

    def inspect(self, target_id: str) -> AdapterResult:
        if not self._login_available:
            return AdapterResult("login_unavailable", reason="declared synthetic login failure")
        if target_id not in self._targets:
            return AdapterResult("not_found", reason="synthetic target is absent")
        return AdapterResult("ok", data={"target_id": target_id, "fields": dict(self._targets[target_id])})

    def snapshot(self, target_id: str) -> PlatformSnapshot:
        inspected = self.inspect(target_id)
        if inspected.status != "ok":
            raise ValueError(inspected.status)
        fields = tuple(sorted(inspected.data["fields"].items()))
        snapshot = PlatformSnapshot(target_id, content_hash({"target": target_id, "fields": fields}), fields)
        self._snapshots[target_id] = snapshot
        return snapshot

    def plan(self, run_id: str, snapshot: PlatformSnapshot, proposed: Mapping[str, str], *, operation: str = "apply") -> SyntheticPlatformPlan:
        if operation not in {"apply", "rollback"}:
            raise ValueError("unsupported operation")
        if self._snapshots.get(snapshot.target_id) != snapshot or self.snapshot(snapshot.target_id) != snapshot:
            raise ValueError("a fresh inspected snapshot is required")
        after = _validate_fields(proposed)
        item = create_or_get_work_item(
            self._connection, run_id=run_id, kind=f"synthetic_platform_{operation}",
            target_system="local_synthetic_platform", target_stable_id=snapshot.target_id,
            required_permission="local.create",
            payload={"operation": operation, "before_revision": snapshot.revision, "before": dict(snapshot.fields), "after": dict(after)},
        )
        plan = SyntheticPlatformPlan(run_id, snapshot.target_id, snapshot, after, item.id, item.plan_hash, operation)
        self._plans[item.plan_hash] = plan
        return plan

    def _registered(self, plan: SyntheticPlatformPlan) -> None:
        if self._plans.get(plan.plan_hash) != plan:
            raise AuthorizationDenied("plan was not created by this local simulator")

    def _authorize(self, plan: SyntheticPlatformPlan, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> None:
        self._registered(plan)
        if (request.scope, request.target_system, request.target_stable_id, request.plan_hash) != (
            "local.create", "local_synthetic_platform", plan.target_id, plan.plan_hash
        ):
            raise AuthorizationDenied("request does not match this exact local plan")
        policy.require("local.create", plan.target_id)
        receipt = (plan.run_id, grant_id, request)
        if plan.plan_hash in self._receipts:
            if self._receipts[plan.plan_hash] != receipt:
                raise AuthorizationDenied("retry does not match the consumed local approval")
            return
        authorize_and_consume(self._connection, run_id=plan.run_id, grant_id=grant_id, request=request, policy=policy)
        self._receipts[plan.plan_hash] = receipt

    def _apply(self, plan: SyntheticPlatformPlan, *, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> AdapterResult:
        self._registered(plan)
        current = self.snapshot(plan.target_id)
        expected = plan.after if plan.plan_hash in self._applied else plan.before.fields
        if current.fields != expected:
            raise ValueError("snapshot changed; replan before retry or rollback")
        self._authorize(plan, grant_id, request, policy)
        if plan.plan_hash in self._applied:
            verified = self.readback(plan)
            return AdapterResult("no_op" if verified.status == "verified" else "needs_reconciliation", data=verified.data)
        if self._failure == "before_apply":
            self._failure = None
            record_run_step(self._connection, run_id=plan.run_id, work_item_id=plan.work_item_id, name="synthetic_before_apply", status="blocked")
            raise SyntheticInterruption("synthetic failure before local apply")
        self._targets[plan.target_id] = dict(plan.after)
        self._applied.add(plan.plan_hash)
        self.local_apply_count += int(plan.before.fields != plan.after)
        record_run_step(self._connection, run_id=plan.run_id, work_item_id=plan.work_item_id, name=f"synthetic_{plan.operation}", status="completed")
        if self._failure == "after_apply":
            self._failure = None
            raise SyntheticInterruption("synthetic failure after local apply; readback required")
        return AdapterResult("simulated", data={"external_writes": 0, "readback_required": True})

    def apply(self, plan: SyntheticPlatformPlan, *, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> AdapterResult:
        if plan.operation != "apply":
            raise ValueError("rollback plans must use rollback")
        return self._apply(plan, grant_id=grant_id, request=request, policy=policy)

    def readback(self, plan: SyntheticPlatformPlan) -> AdapterResult:
        self._registered(plan)
        if plan.plan_hash not in self._applied:
            return AdapterResult("not_applied", reason="no local application receipt exists")
        actual = self.snapshot(plan.target_id)
        if actual.fields != plan.after:
            return AdapterResult("needs_reconciliation", reason="local state differs from plan")
        if plan.plan_hash not in self._verified:
            record_run_step(self._connection, run_id=plan.run_id, work_item_id=plan.work_item_id, name="synthetic_readback", status="completed")
            self._verified.add(plan.plan_hash)
        return AdapterResult("verified", data={"revision": actual.revision, "external_writes": 0})

    def plan_rollback(self, run_id: str, plan: SyntheticPlatformPlan) -> SyntheticPlatformPlan:
        if self.readback(plan).status != "verified":
            raise ValueError("rollback requires verified unchanged applied state")
        return self.plan(run_id, self.snapshot(plan.target_id), dict(plan.before.fields), operation="rollback")

    def rollback(self, plan: SyntheticPlatformPlan, *, grant_id: str, request: AuthorizationRequest, policy: PermissionPolicy) -> AdapterResult:
        if plan.operation != "rollback":
            raise ValueError("an explicit rollback plan is required")
        return self._apply(plan, grant_id=grant_id, request=request, policy=policy)
