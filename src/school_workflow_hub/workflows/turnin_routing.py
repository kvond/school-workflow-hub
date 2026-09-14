"""Synthetic, reversible dry-run proof for assignment turn-in routing.

The workflow consumes declared, non-student records for an assignment master,
its folder/Form/slide-copy manifest, and observed downstream route fields. It
returns comparison issues or immutable *dry-run* diffs. It contains no browser,
Schoology, gradebook, Drive, Google Forms, Slides, model, database, or executor
dependency.

A dry-run plan is not an approval and cannot change a Schoology or gradebook
assignment. The rollback snapshot is included solely so a future, separately
approved executor could validate reversibility against the fresh observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple


SCHEMA_VERSION = "turnin-routing-proof/v1"


class DownstreamSystem(str, Enum):
    SCHOOLOGY = "schoology"
    GRADEBOOK = "gradebook"


def _require_stable_id(value: str, field_name: str) -> None:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("{0} must be a non-empty stable ID without whitespace".format(field_name))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AssignmentTurnInMaster:
    """Teacher-declared routing requirements for one assignment identity."""

    class_stable_id: str
    assignment_stable_id: str
    turn_in_folder_stable_id: str
    form_file_stable_id: str
    slide_copy_source_file_stable_id: str
    schoology_assignment_target_stable_id: str
    gradebook_assignment_target_stable_id: str
    master_revision_stable_id: str

    def __post_init__(self) -> None:
        for field_name, value in self._stable_fields():
            _require_stable_id(value, field_name)

    def _stable_fields(self) -> Tuple[Tuple[str, str], ...]:
        return (
            ("class_stable_id", self.class_stable_id),
            ("assignment_stable_id", self.assignment_stable_id),
            ("turn_in_folder_stable_id", self.turn_in_folder_stable_id),
            ("form_file_stable_id", self.form_file_stable_id),
            ("slide_copy_source_file_stable_id", self.slide_copy_source_file_stable_id),
            ("schoology_assignment_target_stable_id", self.schoology_assignment_target_stable_id),
            ("gradebook_assignment_target_stable_id", self.gradebook_assignment_target_stable_id),
            ("master_revision_stable_id", self.master_revision_stable_id),
        )

    def canonical_dict(self) -> Dict[str, str]:
        return dict(self._stable_fields())


@dataclass(frozen=True)
class TurnInFolderManifest:
    """Read-only evidence that the expected folder, Form, and slide source agree."""

    class_stable_id: str
    assignment_stable_id: str
    turn_in_folder_stable_id: str
    form_file_stable_id: str
    slide_copy_source_file_stable_id: str
    manifest_revision_stable_id: str

    def __post_init__(self) -> None:
        for field_name, value in self._stable_fields():
            _require_stable_id(value, field_name)

    def _stable_fields(self) -> Tuple[Tuple[str, str], ...]:
        return (
            ("class_stable_id", self.class_stable_id),
            ("assignment_stable_id", self.assignment_stable_id),
            ("turn_in_folder_stable_id", self.turn_in_folder_stable_id),
            ("form_file_stable_id", self.form_file_stable_id),
            ("slide_copy_source_file_stable_id", self.slide_copy_source_file_stable_id),
            ("manifest_revision_stable_id", self.manifest_revision_stable_id),
        )

    def canonical_dict(self) -> Dict[str, str]:
        return dict(self._stable_fields())


@dataclass(frozen=True)
class ObservedDownstreamTurnInRoute:
    """Read-only representation of one target's current routing fields."""

    system: DownstreamSystem
    class_stable_id: str
    assignment_stable_id: str
    downstream_assignment_target_stable_id: str
    turn_in_folder_stable_id: str
    form_file_stable_id: str
    slide_copy_source_file_stable_id: str
    observation_revision_stable_id: str

    def __post_init__(self) -> None:
        for field_name, value in self._stable_fields():
            _require_stable_id(value, field_name)

    def _stable_fields(self) -> Tuple[Tuple[str, str], ...]:
        return (
            ("class_stable_id", self.class_stable_id),
            ("assignment_stable_id", self.assignment_stable_id),
            ("downstream_assignment_target_stable_id", self.downstream_assignment_target_stable_id),
            ("turn_in_folder_stable_id", self.turn_in_folder_stable_id),
            ("form_file_stable_id", self.form_file_stable_id),
            ("slide_copy_source_file_stable_id", self.slide_copy_source_file_stable_id),
            ("observation_revision_stable_id", self.observation_revision_stable_id),
        )

    def canonical_dict(self) -> Dict[str, str]:
        result: Dict[str, str] = {"system": self.system.value}
        result.update(dict(self._stable_fields()))
        return result


@dataclass(frozen=True)
class TurnInRoutingInput:
    """All source-revision bounded records required for a local comparison."""

    assignment_master: Tuple[AssignmentTurnInMaster, ...]
    folder_manifests: Tuple[TurnInFolderManifest, ...]
    observed_downstream_routes: Tuple[ObservedDownstreamTurnInRoute, ...]


@dataclass(frozen=True)
class RoutingIssue:
    code: str
    severity: str
    class_stable_id: Optional[str]
    assignment_stable_id: Optional[str]
    system: Optional[DownstreamSystem]
    detail: str

    def canonical_dict(self) -> Dict[str, Optional[str]]:
        return {
            "code": self.code,
            "severity": self.severity,
            "class_stable_id": self.class_stable_id,
            "assignment_stable_id": self.assignment_stable_id,
            "system": self.system.value if self.system else None,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RouteFieldDiff:
    """A single reversible route field change: observed value → master value."""

    field_name: str
    observed_stable_id: str
    expected_stable_id: str

    def canonical_dict(self) -> Dict[str, str]:
        return {
            "field_name": self.field_name,
            "observed_stable_id": self.observed_stable_id,
            "expected_stable_id": self.expected_stable_id,
        }


@dataclass(frozen=True)
class ReversibleRouteDryRunPlan:
    """Immutable diff; no function in this module can execute it."""

    plan_stable_id: str
    system: DownstreamSystem
    class_stable_id: str
    assignment_stable_id: str
    downstream_assignment_target_stable_id: str
    master_revision_stable_id: str
    manifest_revision_stable_id: str
    observation_revision_stable_id: str
    diffs: Tuple[RouteFieldDiff, ...]
    rollback_diffs: Tuple[RouteFieldDiff, ...]
    lifecycle_state: str = "dry_run_only"
    required_permission: str = "external.update"
    reversible: bool = True
    external_write_performed: bool = False

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "plan_stable_id": self.plan_stable_id,
            "system": self.system.value,
            "class_stable_id": self.class_stable_id,
            "assignment_stable_id": self.assignment_stable_id,
            "downstream_assignment_target_stable_id": self.downstream_assignment_target_stable_id,
            "master_revision_stable_id": self.master_revision_stable_id,
            "manifest_revision_stable_id": self.manifest_revision_stable_id,
            "observation_revision_stable_id": self.observation_revision_stable_id,
            "diffs": [diff.canonical_dict() for diff in self.diffs],
            "rollback_diffs": [diff.canonical_dict() for diff in self.rollback_diffs],
            "lifecycle_state": self.lifecycle_state,
            "required_permission": self.required_permission,
            "reversible": self.reversible,
            "external_write_performed": self.external_write_performed,
        }


@dataclass(frozen=True)
class TurnInRoutingProofResult:
    """Complete audit from a synthetic comparison, explicitly recording zero side effects."""

    plans: Tuple[ReversibleRouteDryRunPlan, ...]
    issues: Tuple[RoutingIssue, ...]
    input_digest: str
    audit_events: Tuple[str, ...]
    browser_calls: int = 0
    model_calls: int = 0
    external_calls: int = 0
    external_writes: int = 0

    def canonical_dict(self) -> Dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "plans": [plan.canonical_dict() for plan in self.plans],
            "issues": [issue.canonical_dict() for issue in self.issues],
            "input_digest": self.input_digest,
            "audit_events": list(self.audit_events),
            "browser_calls": self.browser_calls,
            "model_calls": self.model_calls,
            "external_calls": self.external_calls,
            "external_writes": self.external_writes,
        }


class TurnInRoutingPlanner(Protocol):
    """The future integration boundary intentionally contains planning only."""

    def plan(self, workflow_input: TurnInRoutingInput) -> TurnInRoutingProofResult:
        ...


class LocalTurnInRoutingPlanner:
    """Pure comparison engine for fake manifests and fake target observations."""

    def plan(self, workflow_input: TurnInRoutingInput) -> TurnInRoutingProofResult:
        masters = _group_by_assignment(workflow_input.assignment_master)
        manifests = _group_by_assignment(workflow_input.folder_manifests)
        downstream = _group_routes_by_assignment_and_system(workflow_input.observed_downstream_routes)
        input_digest = _input_digest(workflow_input)
        issues: List[RoutingIssue] = []
        plans: List[ReversibleRouteDryRunPlan] = []

        _report_duplicates("assignment_master", masters, issues)
        _report_duplicates("folder_manifest", manifests, issues)
        _report_route_duplicates(downstream, issues)

        for key in sorted(masters):
            master_records = masters[key]
            if len(master_records) != 1:
                continue
            master = master_records[0]
            manifest_records = manifests.get(key, [])
            if not manifest_records:
                issues.append(
                    _issue(
                        "missing_folder_form_slide_manifest",
                        master,
                        None,
                        "No read-only folder/Form/slide manifest is available for this assignment.",
                    )
                )
                continue
            if len(manifest_records) != 1:
                continue
            manifest = manifest_records[0]
            if not _manifest_matches_master(master, manifest):
                issues.append(
                    _issue(
                        "folder_form_slide_manifest_conflict",
                        master,
                        None,
                        "Folder, Form, or slide-copy source stable IDs do not exactly match the assignment master.",
                    )
                )
                continue

            for system in (DownstreamSystem.SCHOOLOGY, DownstreamSystem.GRADEBOOK):
                route_records = downstream.get((master.class_stable_id, master.assignment_stable_id, system), [])
                if not route_records:
                    issues.append(
                        _issue(
                            "missing_downstream_target",
                            master,
                            system,
                            "No read-only downstream target observation is available; target selection is not inferred.",
                        )
                    )
                    continue
                if len(route_records) != 1:
                    continue
                observed = route_records[0]
                expected_target = _expected_target(master, system)
                if observed.downstream_assignment_target_stable_id != expected_target:
                    issues.append(
                        _issue(
                            "downstream_target_conflict",
                            master,
                            system,
                            "Observed downstream target stable ID does not exactly match the assignment master.",
                        )
                    )
                    continue
                diffs = _route_diffs(master, observed)
                if not diffs:
                    continue
                plans.append(_dry_run_plan(master, manifest, observed, diffs, input_digest))

        sorted_plans = tuple(sorted(plans, key=lambda plan: plan.plan_stable_id))
        sorted_issues = tuple(sorted(issues, key=_issue_sort_key))
        audit_events = tuple(
            [
                "input:{0}".format(input_digest),
                "plans:{0}".format(len(sorted_plans)),
                "issues:{0}".format(len(sorted_issues)),
            ]
            + ["plan:{0}".format(plan.plan_stable_id) for plan in sorted_plans]
            + [
                "issue:{0}:{1}:{2}".format(
                    issue.code,
                    issue.assignment_stable_id or "none",
                    issue.system.value if issue.system else "none",
                )
                for issue in sorted_issues
            ]
        )
        return TurnInRoutingProofResult(
            plans=sorted_plans,
            issues=sorted_issues,
            input_digest=input_digest,
            audit_events=audit_events,
        )


def plan_synthetic_turnin_routing(
    workflow_input: TurnInRoutingInput, planner: Optional[TurnInRoutingPlanner] = None
) -> TurnInRoutingProofResult:
    """Run a local fake comparison; no external behavior is reachable here."""

    active_planner: TurnInRoutingPlanner = planner or LocalTurnInRoutingPlanner()
    return active_planner.plan(workflow_input)


def _group_by_assignment(records: Sequence[object]) -> Dict[Tuple[str, str], List[object]]:
    grouped: Dict[Tuple[str, str], List[object]] = {}
    for record in records:
        key = (getattr(record, "class_stable_id"), getattr(record, "assignment_stable_id"))
        grouped.setdefault(key, []).append(record)
    return grouped


def _group_routes_by_assignment_and_system(
    routes: Sequence[ObservedDownstreamTurnInRoute],
) -> Dict[Tuple[str, str, DownstreamSystem], List[ObservedDownstreamTurnInRoute]]:
    grouped: Dict[Tuple[str, str, DownstreamSystem], List[ObservedDownstreamTurnInRoute]] = {}
    for route in routes:
        key = (route.class_stable_id, route.assignment_stable_id, route.system)
        grouped.setdefault(key, []).append(route)
    return grouped


def _report_duplicates(
    source_name: str, grouped: Dict[Tuple[str, str], List[object]], issues: List[RoutingIssue]
) -> None:
    for (class_stable_id, assignment_stable_id), records in grouped.items():
        if len(records) > 1:
            issues.append(
                RoutingIssue(
                    code="ambiguous_{0}_mapping".format(source_name),
                    severity="error",
                    class_stable_id=class_stable_id,
                    assignment_stable_id=assignment_stable_id,
                    system=None,
                    detail="More than one record has this stable class/assignment identity; no target is chosen.",
                )
            )


def _report_route_duplicates(
    grouped: Dict[Tuple[str, str, DownstreamSystem], List[ObservedDownstreamTurnInRoute]],
    issues: List[RoutingIssue],
) -> None:
    for (class_stable_id, assignment_stable_id, system), records in grouped.items():
        if len(records) > 1:
            issues.append(
                RoutingIssue(
                    code="ambiguous_downstream_target",
                    severity="error",
                    class_stable_id=class_stable_id,
                    assignment_stable_id=assignment_stable_id,
                    system=system,
                    detail="More than one observed downstream target matches this assignment; no target is chosen.",
                )
            )


def _manifest_matches_master(master: AssignmentTurnInMaster, manifest: TurnInFolderManifest) -> bool:
    return (
        master.class_stable_id == manifest.class_stable_id
        and master.assignment_stable_id == manifest.assignment_stable_id
        and master.turn_in_folder_stable_id == manifest.turn_in_folder_stable_id
        and master.form_file_stable_id == manifest.form_file_stable_id
        and master.slide_copy_source_file_stable_id == manifest.slide_copy_source_file_stable_id
    )


def _expected_target(master: AssignmentTurnInMaster, system: DownstreamSystem) -> str:
    if system is DownstreamSystem.SCHOOLOGY:
        return master.schoology_assignment_target_stable_id
    return master.gradebook_assignment_target_stable_id


def _route_diffs(
    master: AssignmentTurnInMaster, observed: ObservedDownstreamTurnInRoute
) -> Tuple[RouteFieldDiff, ...]:
    fields = (
        ("turn_in_folder_stable_id", observed.turn_in_folder_stable_id, master.turn_in_folder_stable_id),
        ("form_file_stable_id", observed.form_file_stable_id, master.form_file_stable_id),
        (
            "slide_copy_source_file_stable_id",
            observed.slide_copy_source_file_stable_id,
            master.slide_copy_source_file_stable_id,
        ),
    )
    return tuple(
        RouteFieldDiff(field_name=field_name, observed_stable_id=observed_id, expected_stable_id=expected_id)
        for field_name, observed_id, expected_id in fields
        if observed_id != expected_id
    )


def _dry_run_plan(
    master: AssignmentTurnInMaster,
    manifest: TurnInFolderManifest,
    observed: ObservedDownstreamTurnInRoute,
    diffs: Tuple[RouteFieldDiff, ...],
    input_digest: str,
) -> ReversibleRouteDryRunPlan:
    seed = {
        "schema_version": SCHEMA_VERSION,
        "input_digest": input_digest,
        "system": observed.system.value,
        "assignment_stable_id": master.assignment_stable_id,
        "downstream_assignment_target_stable_id": observed.downstream_assignment_target_stable_id,
        "master_revision_stable_id": master.master_revision_stable_id,
        "manifest_revision_stable_id": manifest.manifest_revision_stable_id,
        "observation_revision_stable_id": observed.observation_revision_stable_id,
        "diffs": [diff.canonical_dict() for diff in diffs],
    }
    rollback_diffs = tuple(
        RouteFieldDiff(
            field_name=diff.field_name,
            observed_stable_id=diff.expected_stable_id,
            expected_stable_id=diff.observed_stable_id,
        )
        for diff in diffs
    )
    return ReversibleRouteDryRunPlan(
        plan_stable_id="turnin-route-dry-run:{0}".format(_digest(seed)[:16]),
        system=observed.system,
        class_stable_id=master.class_stable_id,
        assignment_stable_id=master.assignment_stable_id,
        downstream_assignment_target_stable_id=observed.downstream_assignment_target_stable_id,
        master_revision_stable_id=master.master_revision_stable_id,
        manifest_revision_stable_id=manifest.manifest_revision_stable_id,
        observation_revision_stable_id=observed.observation_revision_stable_id,
        diffs=diffs,
        rollback_diffs=rollback_diffs,
    )


def _issue(
    code: str,
    master: AssignmentTurnInMaster,
    system: Optional[DownstreamSystem],
    detail: str,
) -> RoutingIssue:
    return RoutingIssue(
        code=code,
        severity="error",
        class_stable_id=master.class_stable_id,
        assignment_stable_id=master.assignment_stable_id,
        system=system,
        detail=detail,
    )


def _issue_sort_key(issue: RoutingIssue) -> Tuple[str, str, str, str]:
    return (
        issue.assignment_stable_id or "",
        issue.system.value if issue.system else "",
        issue.code,
        issue.class_stable_id or "",
    )


def _input_digest(workflow_input: TurnInRoutingInput) -> str:
    master_records = sorted(
        (master.canonical_dict() for master in workflow_input.assignment_master),
        key=lambda record: (record["class_stable_id"], record["assignment_stable_id"]),
    )
    manifest_records = sorted(
        (manifest.canonical_dict() for manifest in workflow_input.folder_manifests),
        key=lambda record: (record["class_stable_id"], record["assignment_stable_id"]),
    )
    route_records = sorted(
        (route.canonical_dict() for route in workflow_input.observed_downstream_routes),
        key=lambda record: (
            record["class_stable_id"],
            record["assignment_stable_id"],
            record["system"],
            record["downstream_assignment_target_stable_id"],
        ),
    )
    return _digest(
        {
            "schema_version": SCHEMA_VERSION,
            "assignment_master": master_records,
            "folder_manifests": manifest_records,
            "observed_downstream_routes": route_records,
        }
    )
