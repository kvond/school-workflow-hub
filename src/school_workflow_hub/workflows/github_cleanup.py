"""Local-only, archive-first planning for stale GitHub project items and repositories.

This module deliberately receives a declared *synthetic manifest* instead of
enumerating GitHub.  It does not authenticate, make a network request, write a
file, enable a workflow, archive anything, or expose a delete operation.

The output is a deterministic review plan.  A later, separately approved
executor would still have to pass the Hub's central permission gate before it
could act on an individual ``ArchiveProposal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import json
from typing import Dict, Iterable, Optional, Protocol, Tuple


SCHEMA_VERSION = "github-cleanup-plan/v1"
PROJECT_ITEM_RESTORE_DOC = (
    "https://docs.github.com/en/issues/planning-and-tracking-with-projects/"
    "managing-items-in-your-project/archiving-items-from-your-project"
)
REPOSITORY_RESTORE_DOC = (
    "https://docs.github.com/en/repositories/archiving-a-github-repository/"
    "archiving-repositories"
)


class GitHubItemKind(str, Enum):
    """The only item kinds this read-only planner understands."""

    PROJECT_ITEM = "project_item"
    REPOSITORY = "repository"


class GitHubItemState(str, Enum):
    """A deliberately small status vocabulary supplied by a future reader."""

    OPEN = "open"
    DRAFT = "draft"
    CLOSED = "closed"
    MERGED = "merged"
    NOT_PLANNED = "not_planned"
    INACTIVE = "inactive"


TERMINAL_STATES = frozenset(
    (GitHubItemState.CLOSED, GitHubItemState.MERGED, GitHubItemState.NOT_PLANNED, GitHubItemState.INACTIVE)
)


class CleanupDisposition(str, Enum):
    """A result is either reviewable or explicitly kept/blocked; never deleted."""

    PROPOSE_ARCHIVE = "propose_archive"
    KEEP = "keep"
    BLOCKED = "blocked"
    ALREADY_ARCHIVED = "already_archived"


class CleanupPlanStatus(str, Enum):
    PLANNED = "planned_synthetic"
    FAILED_VALIDATION = "failed_validation"


def _require_stable_id(value: str, field_name: str) -> None:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("{0} must be a non-empty stable ID without whitespace".format(field_name))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GitHubCleanupItem:
    """One externally identified record from a future read-only inventory.

    ``stable_id`` is the identity key.  ``canonical_location`` is human-facing
    context only and cannot be used to broaden the plan's target set.
    """

    stable_id: str
    kind: GitHubItemKind
    canonical_location: str
    state: GitHubItemState
    last_activity_on: date
    is_archived: bool = False
    is_protected: bool = False
    is_active: bool = False
    is_restorable: bool = True
    estimated_storage_bytes: int = 0

    def __post_init__(self) -> None:
        _require_stable_id(self.stable_id, "stable_id")
        if not self.canonical_location or self.canonical_location != self.canonical_location.strip():
            raise ValueError("canonical_location is required")
        if self.estimated_storage_bytes < 0:
            raise ValueError("estimated_storage_bytes cannot be negative")

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "kind": self.kind.value,
            "canonical_location": self.canonical_location,
            "state": self.state.value,
            "last_activity_on": self.last_activity_on.isoformat(),
            "is_archived": self.is_archived,
            "is_protected": self.is_protected,
            "is_active": self.is_active,
            "is_restorable": self.is_restorable,
            "estimated_storage_bytes": self.estimated_storage_bytes,
        }


@dataclass(frozen=True)
class CleanupPolicy:
    """The finite, explicit scope a reviewer has chosen for one planning pass."""

    policy_stable_id: str
    as_of: date
    retention_days: int
    allowlisted_item_ids: Tuple[str, ...]
    excluded_item_ids: Tuple[str, ...] = ()
    protected_item_ids: Tuple[str, ...] = ()
    active_item_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_stable_id(self.policy_stable_id, "policy_stable_id")
        if self.retention_days < 1:
            raise ValueError("retention_days must be at least one day")
        all_groups = (
            ("allowlisted_item_ids", self.allowlisted_item_ids),
            ("excluded_item_ids", self.excluded_item_ids),
            ("protected_item_ids", self.protected_item_ids),
            ("active_item_ids", self.active_item_ids),
        )
        for group_name, values in all_groups:
            if len(values) != len(set(values)):
                raise ValueError("{0} must not contain duplicate IDs".format(group_name))
            for value in values:
                _require_stable_id(value, group_name)
        if not self.allowlisted_item_ids:
            raise ValueError("an explicit allowlist is required; this planner never infers scope")

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "policy_stable_id": self.policy_stable_id,
            "as_of": self.as_of.isoformat(),
            "retention_days": self.retention_days,
            "allowlisted_item_ids": sorted(self.allowlisted_item_ids),
            "excluded_item_ids": sorted(self.excluded_item_ids),
            "protected_item_ids": sorted(self.protected_item_ids),
            "active_item_ids": sorted(self.active_item_ids),
        }


@dataclass(frozen=True)
class RestoreGuide:
    """Human recovery guidance carried with every archive proposal."""

    kind: GitHubItemKind
    documentation_url: str
    instruction: str

    @classmethod
    def for_kind(cls, kind: GitHubItemKind) -> "RestoreGuide":
        if kind is GitHubItemKind.PROJECT_ITEM:
            return cls(
                kind=kind,
                documentation_url=PROJECT_ITEM_RESTORE_DOC,
                instruction=(
                    "If a separately approved archive occurs, restore the project item from "
                    "the project's Archived items view after reviewer confirmation."
                ),
            )
        return cls(
            kind=kind,
            documentation_url=REPOSITORY_RESTORE_DOC,
            instruction=(
                "If a separately approved archive occurs, unarchive the repository in its "
                "GitHub settings after reviewer confirmation."
            ),
        )

    def canonical_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind.value,
            "documentation_url": self.documentation_url,
            "instruction": self.instruction,
        }


@dataclass(frozen=True)
class CleanupDecision:
    """An auditable result for one manifest record."""

    stable_id: str
    kind: GitHubItemKind
    canonical_location: str
    disposition: CleanupDisposition
    reasons: Tuple[str, ...]
    action_stable_id: Optional[str] = None
    restore_guide: Optional[RestoreGuide] = None

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "kind": self.kind.value,
            "canonical_location": self.canonical_location,
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "action_stable_id": self.action_stable_id,
            "restore_guide": self.restore_guide.canonical_dict() if self.restore_guide else None,
        }


@dataclass(frozen=True)
class SpaceImpact:
    """Conservative capacity reporting; archive proposals never claim storage deletion."""

    project_view_slots_reduced: int
    reviewable_storage_bytes: int
    guaranteed_storage_reclaimed_bytes: int = 0
    note: str = (
        "Archive-first proposals may reduce project-view clutter, but they do not claim "
        "repository storage reclamation. Deletion is intentionally out of scope."
    )

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "project_view_slots_reduced": self.project_view_slots_reduced,
            "reviewable_storage_bytes": self.reviewable_storage_bytes,
            "guaranteed_storage_reclaimed_bytes": self.guaranteed_storage_reclaimed_bytes,
            "note": self.note,
        }


@dataclass(frozen=True)
class CleanupPlan:
    """A deterministic, plan-only artifact that contains no executor."""

    status: CleanupPlanStatus
    plan_stable_id: str
    policy_stable_id: Optional[str]
    manifest_digest: Optional[str]
    plan_digest: Optional[str]
    decisions: Tuple[CleanupDecision, ...]
    space_impact: SpaceImpact
    audit_events: Tuple[str, ...]
    external_calls: int = 0
    external_writes: int = 0
    failure_code: Optional[str] = None

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status.value,
            "plan_stable_id": self.plan_stable_id,
            "policy_stable_id": self.policy_stable_id,
            "manifest_digest": self.manifest_digest,
            "plan_digest": self.plan_digest,
            "decisions": [decision.canonical_dict() for decision in self.decisions],
            "space_impact": self.space_impact.canonical_dict(),
            "audit_events": list(self.audit_events),
            "external_calls": self.external_calls,
            "external_writes": self.external_writes,
            "failure_code": self.failure_code,
        }


class GitHubCleanupPlanner(Protocol):
    """Stable local planning interface; it deliberately exposes no execution method."""

    def plan(self, items: Iterable[GitHubCleanupItem], policy: CleanupPolicy) -> CleanupPlan:
        ...


def _validate_manifest(items: Tuple[GitHubCleanupItem, ...], policy: CleanupPolicy) -> None:
    seen = set()
    for item in items:
        if item.stable_id in seen:
            raise ValueError("manifest has duplicate stable IDs")
        seen.add(item.stable_id)
        if item.last_activity_on > policy.as_of:
            raise ValueError("manifest last_activity_on cannot be after policy as_of")


def _failed_plan(policy: Optional[CleanupPolicy]) -> CleanupPlan:
    """Return a safe failure artifact without echoing untrusted manifest details."""

    return CleanupPlan(
        status=CleanupPlanStatus.FAILED_VALIDATION,
        plan_stable_id="github-cleanup-plan:validation-failed",
        policy_stable_id=policy.policy_stable_id if policy else None,
        manifest_digest=None,
        plan_digest=None,
        decisions=(),
        space_impact=SpaceImpact(project_view_slots_reduced=0, reviewable_storage_bytes=0),
        audit_events=("validation_failed:no_archive_proposals_created",),
        failure_code="invalid_manifest_or_policy",
    )


class LocalGitHubCleanupPlanner:
    """Pure planner for declared data, safe to use in a synthetic test or dry run."""

    def plan(self, items: Iterable[GitHubCleanupItem], policy: CleanupPolicy) -> CleanupPlan:
        materialized_items = tuple(items)
        try:
            _validate_manifest(materialized_items, policy)
        except (TypeError, ValueError, AttributeError):
            return _failed_plan(policy)

        sorted_items = tuple(sorted(materialized_items, key=lambda item: item.stable_id))
        manifest_records = [item.canonical_dict() for item in sorted_items]
        manifest_digest = _digest(manifest_records)
        policy_record = policy.canonical_dict()
        plan_digest = _digest(
            {
                "schema_version": SCHEMA_VERSION,
                "policy": policy_record,
                "manifest_digest": manifest_digest,
            }
        )

        decisions = tuple(
            self._decide(item=item, policy=policy, plan_digest=plan_digest) for item in sorted_items
        )
        proposals = tuple(
            decision
            for decision in decisions
            if decision.disposition is CleanupDisposition.PROPOSE_ARCHIVE
        )
        proposed_ids = {decision.stable_id for decision in proposals}
        space_impact = SpaceImpact(
            project_view_slots_reduced=sum(
                1 for decision in proposals if decision.kind is GitHubItemKind.PROJECT_ITEM
            ),
            reviewable_storage_bytes=sum(
                item.estimated_storage_bytes
                for item in sorted_items
                if item.stable_id in proposed_ids
            ),
        )
        audit_events = tuple(
            "{0}:{1}:{2}".format(
                decision.stable_id, decision.disposition.value, ",".join(decision.reasons)
            )
            for decision in decisions
        )
        return CleanupPlan(
            status=CleanupPlanStatus.PLANNED,
            plan_stable_id="github-cleanup-plan:{0}".format(plan_digest[:16]),
            policy_stable_id=policy.policy_stable_id,
            manifest_digest=manifest_digest,
            plan_digest=plan_digest,
            decisions=decisions,
            space_impact=space_impact,
            audit_events=audit_events,
        )

    @staticmethod
    def _decide(
        item: GitHubCleanupItem, policy: CleanupPolicy, plan_digest: str
    ) -> CleanupDecision:
        if item.is_archived:
            return CleanupDecision(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_location=item.canonical_location,
                disposition=CleanupDisposition.ALREADY_ARCHIVED,
                reasons=("already_archived:no_duplicate_proposal",),
                restore_guide=RestoreGuide.for_kind(item.kind),
            )

        reasons = []
        if item.stable_id not in set(policy.allowlisted_item_ids):
            reasons.append("not_allowlisted")
        if item.stable_id in set(policy.excluded_item_ids):
            reasons.append("explicitly_excluded")
        if item.is_protected or item.stable_id in set(policy.protected_item_ids):
            reasons.append("protected")
        if item.is_active or item.stable_id in set(policy.active_item_ids):
            reasons.append("active")
        if not item.is_restorable:
            reasons.append("not_restorable")
        if reasons:
            return CleanupDecision(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_location=item.canonical_location,
                disposition=CleanupDisposition.BLOCKED,
                reasons=tuple(reasons),
            )

        age_days = (policy.as_of - item.last_activity_on).days
        if age_days < policy.retention_days:
            return CleanupDecision(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_location=item.canonical_location,
                disposition=CleanupDisposition.KEEP,
                reasons=("recent:{0}_days<{1}_day_retention".format(age_days, policy.retention_days),),
            )
        if item.state not in TERMINAL_STATES:
            return CleanupDecision(
                stable_id=item.stable_id,
                kind=item.kind,
                canonical_location=item.canonical_location,
                disposition=CleanupDisposition.KEEP,
                reasons=("non_terminal_state:{0}".format(item.state.value),),
            )

        action_seed = {
            "schema_version": SCHEMA_VERSION,
            "plan_digest": plan_digest,
            "item_stable_id": item.stable_id,
            "operation": "archive_only",
        }
        return CleanupDecision(
            stable_id=item.stable_id,
            kind=item.kind,
            canonical_location=item.canonical_location,
            disposition=CleanupDisposition.PROPOSE_ARCHIVE,
            reasons=("allowlisted", "terminal_state", "retention_elapsed:{0}_days".format(age_days)),
            action_stable_id="github-archive-proposal:{0}".format(_digest(action_seed)[:16]),
            restore_guide=RestoreGuide.for_kind(item.kind),
        )


def plan_synthetic_github_cleanup(
    items: Iterable[GitHubCleanupItem], policy: CleanupPolicy, planner: Optional[GitHubCleanupPlanner] = None
) -> CleanupPlan:
    """Plan a synthetic manifest without any GitHub/network/executor dependency."""

    active_planner: GitHubCleanupPlanner = planner or LocalGitHubCleanupPlanner()
    return active_planner.plan(items, policy)


def synthetic_cleanup_fixture() -> Tuple[Tuple[GitHubCleanupItem, ...], CleanupPolicy]:
    """Return a non-production fixture covering safe and unsafe candidate paths."""

    policy = CleanupPolicy(
        policy_stable_id="github-cleanup-policy:synthetic-v1",
        as_of=date(2026, 9, 13),
        retention_days=90,
        allowlisted_item_ids=(
            "github-project-item:eligible",
            "github-repository:eligible",
            "github-project-item:protected",
            "github-project-item:active",
            "github-project-item:recent",
            "github-project-item:excluded",
            "github-project-item:already-archived",
            "github-project-item:open",
            "github-project-item:not-restorable",
        ),
        excluded_item_ids=("github-project-item:excluded",),
        protected_item_ids=("github-project-item:protected",),
        active_item_ids=("github-project-item:active",),
    )
    items = (
        GitHubCleanupItem(
            stable_id="github-project-item:eligible",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-eligible",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 5, 1),
            estimated_storage_bytes=12,
        ),
        GitHubCleanupItem(
            stable_id="github-repository:eligible",
            kind=GitHubItemKind.REPOSITORY,
            canonical_location="example-org/synthetic-retired-repository",
            state=GitHubItemState.INACTIVE,
            last_activity_on=date(2026, 4, 1),
            estimated_storage_bytes=2048,
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:protected",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-protected",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:active",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-active",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:recent",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-recent",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 9, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:excluded",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-excluded",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:not-allowlisted",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-not-allowlisted",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:already-archived",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-already-archived",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
            is_archived=True,
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:open",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-open",
            state=GitHubItemState.OPEN,
            last_activity_on=date(2026, 1, 1),
        ),
        GitHubCleanupItem(
            stable_id="github-project-item:not-restorable",
            kind=GitHubItemKind.PROJECT_ITEM,
            canonical_location="example-org/synthetic-project#item-not-restorable",
            state=GitHubItemState.CLOSED,
            last_activity_on=date(2026, 1, 1),
            is_restorable=False,
        ),
    )
    return items, policy
