"""Fixture-only, fail-closed enrollment status planning; no grade or roster writes.

The source represents a complete current class roster. The grading snapshot
includes retained withdrawn learners. Absence proposes withdrawal, never deletion.
Completeness is supplied evidence, not a claim that a live roster was fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Tuple

from school_workflow_hub.core.models import EnrollmentRecord
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import (
    EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview, validate_preview,
)
from school_workflow_hub.workflows.feedback_pipeline import require_fixture_id


@dataclass(frozen=True)
class RosterEntry:
    enrollment: EnrollmentRecord
    status: str = "active"


@dataclass(frozen=True)
class RosterSnapshot:
    class_stable_id: str
    revision: str
    observed_at: datetime
    complete: bool
    expected_count: int
    entries: Tuple[RosterEntry, ...]


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _snapshot_issue(snapshot: RosterSnapshot, now: datetime) -> str | None:
    if not isinstance(snapshot, RosterSnapshot):
        return "invalid_roster_snapshot"
    try:
        require_fixture_id(snapshot.class_stable_id, "class")
        require_fixture_id(snapshot.revision, "roster-revision")
    except ValueError:
        return "non_fixture_roster_identity"
    if snapshot.complete is not True:
        return "incomplete_roster_snapshot"
    if (type(snapshot.expected_count) is not int or snapshot.expected_count < 0
            or not isinstance(snapshot.entries, tuple)
            or snapshot.expected_count != len(snapshot.entries)):
        return "roster_count_mismatch"
    if len(snapshot.entries) > 500:
        return "roster_snapshot_limit_exceeded"
    if not _aware(snapshot.observed_at):
        return "invalid_roster_timestamp"
    age = now - snapshot.observed_at
    if age < timedelta(0) or age > timedelta(hours=24):
        return "roster_snapshot_not_current"
    learners, external_ids = set(), set()
    for entry in snapshot.entries:
        if not isinstance(entry, RosterEntry) or not isinstance(entry.enrollment, EnrollmentRecord):
            return "invalid_roster_entry"
        enrollment = entry.enrollment
        try:
            require_fixture_id(enrollment.learner_stable_id, "learner")
            require_fixture_id(enrollment.class_stable_id, "class")
            if enrollment.external_enrollment_id is not None:
                require_fixture_id(enrollment.external_enrollment_id, "enrollment")
        except ValueError:
            return "non_fixture_roster_identity"
        if enrollment.class_stable_id != snapshot.class_stable_id:
            return "mixed_class_roster"
        if not isinstance(entry.status, str) or entry.status not in {"active", "withdrawn"}:
            return "ambiguous_enrollment_status"
        if enrollment.learner_stable_id in learners:
            return "duplicate_learner_identity"
        learners.add(enrollment.learner_stable_id)
        if enrollment.external_enrollment_id is not None:
            if enrollment.external_enrollment_id in external_ids:
                return "ambiguous_enrollment_identity"
            external_ids.add(enrollment.external_enrollment_id)
    return None


def plan_roster_reconciliation(
    source: RosterSnapshot, grading: RosterSnapshot, *, observed_at: datetime,
) -> WorkflowPreview:
    """Compare one class and propose only active/withdrawn membership changes.

    Invalid/partial/stale/ambiguous input blocks the entire class. No grade
    argument or database handle is accepted; grade history cannot be overwritten.
    The shared assignment_key field scopes roster proposals to the class key.
    A generated target is a local fixture membership key, not a live-system ID.
    """
    def blocked(code: str) -> WorkflowPreview:
        result = WorkflowPreview("roster_reconciliation", issues=(WorkflowIssue(code, "blocked"),))
        validate_preview(result)
        return result

    if not _aware(observed_at):
        return blocked("invalid_roster_observation_time")
    for snapshot in (source, grading):
        issue = _snapshot_issue(snapshot, observed_at)
        if issue:
            return blocked(issue)
    if source.class_stable_id != grading.class_stable_id:
        return blocked("roster_class_mismatch")
    # The source is the current active roster, not a historical enrollment export.
    if any(entry.status != "active" for entry in source.entries):
        return blocked("source_roster_not_active_only")
    external_learners = {}
    for entry in source.entries + grading.entries:
        key = entry.enrollment.external_enrollment_id
        learner = entry.enrollment.learner_stable_id
        if key is not None and key in external_learners and external_learners[key] != learner:
            return blocked("conflicting_enrollment_identity")
        if key is not None:
            external_learners[key] = learner
    desired = {entry.enrollment.learner_stable_id: entry for entry in source.entries}
    existing = {entry.enrollment.learner_stable_id: entry for entry in grading.entries}
    class_id = source.class_stable_id
    evidence = tuple(
        EvidenceRef(kind, class_id, snapshot.revision, "synthetic://" + kind + "/" + class_id)
        for kind, snapshot in (("current_class_roster", source), ("grading_roster", grading))
    )
    changes, memberships = [], []
    for learner in sorted(set(desired) | set(existing)):
        old = existing[learner].status if learner in existing else None
        proposed = "active" if learner in desired else "withdrawn"
        if old == proposed:
            continue
        target = deterministic_id("fixture-membership", {"class": class_id, "learner": learner})
        operation = "add" if old is None else ("reactivate" if proposed == "active" else "withdraw")
        memberships.append({"target_key": target, "learner_key": learner, "operation": operation})
        change_id = deterministic_id("roster-change", {
            "target": target, "old": old, "proposed": proposed,
            "source_revision": source.revision, "grading_revision": grading.revision,
        })
        changes.append(PreviewChange(
            stable_id=change_id, assignment_key=class_id, target_system="grading_roster",
            target_id=target, field="enrollment_status", old_value=old,
            proposed_value=proposed, evidence=evidence, confidence=1.0,
            reversible=True,
        ))
    result = WorkflowPreview("roster_reconciliation", changes=tuple(changes), artifacts=({
        "scope": "fixture_only", "class_key": class_id,
        "grade_history_policy": "retain_all_existing_grade_history",
        "membership_policy": "status_only_never_delete",
        "source_count": len(source.entries), "grading_count": len(grading.entries),
        "membership_proposals": memberships,
        "student_access_verified": False,
    },))
    validate_preview(result)
    return result
