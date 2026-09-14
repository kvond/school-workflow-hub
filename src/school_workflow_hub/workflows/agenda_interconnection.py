"""Synthetic agenda-to-downstream interconnection proof.

This module deliberately does not read an Agenda, browser, Drive, Schoology,
or Infinite Campus.  It accepts fully synthetic fixtures, records local source
revisions, and produces only proposed actions.  The shared action lifecycle
keeps every external write hard-denied in this foundation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from school_workflow_hub.core.actions import get_action, propose_action
from school_workflow_hub.core.runs import create_or_get_run, finish_run
from school_workflow_hub.core.sources import record_source_revision


@dataclass(frozen=True)
class AgendaItemRevision:
    """A single explicit Agenda item and its intended due date."""

    class_stable_id: str
    agenda_item_stable_id: str
    assignment_stable_id: str
    title: str
    intended_due_date: str
    revision_label: str


@dataclass(frozen=True)
class AssignmentRouteRevision:
    """Stable downstream IDs and required materials for one assignment."""

    class_stable_id: str
    assignment_stable_id: str
    assignment_folder_item_id: str
    schoology_assignment_id: str
    infinite_campus_assignment_id: str
    deck_file_id: str
    form_file_id: Optional[str]
    revision_label: str


@dataclass(frozen=True)
class AssignmentFolderObservation:
    """Read-only synthetic observation of a folder-facing assignment item."""

    class_stable_id: str
    assignment_stable_id: str
    assignment_folder_item_id: str
    observed_due_date: str
    observed_message: str
    deck_file_id: str
    form_file_id: Optional[str]
    revision_label: str


@dataclass(frozen=True)
class SchoologyObservation:
    """Read-only synthetic observation of a Schoology assignment."""

    class_stable_id: str
    assignment_stable_id: str
    schoology_assignment_id: str
    observed_due_date: str
    observed_message: str
    revision_label: str


@dataclass(frozen=True)
class InfiniteCampusObservation:
    """Read-only synthetic observation of an Infinite Campus assignment."""

    class_stable_id: str
    assignment_stable_id: str
    infinite_campus_assignment_id: str
    observed_due_date: str
    revision_label: str


@dataclass(frozen=True)
class AgendaInterconnectionInput:
    """All synthetic records needed for one deterministic planning pass."""

    fixture_id: str
    synthetic_marker: str
    agenda_item: AgendaItemRevision
    assignment_route: AssignmentRouteRevision
    assignment_folder: AssignmentFolderObservation
    schoology: SchoologyObservation
    infinite_campus: InfiniteCampusObservation


@dataclass(frozen=True)
class InterconnectionIssue:
    code: str
    severity: str
    detail: str


@dataclass(frozen=True)
class NormalizedAgendaChange:
    """Revision-bound record used to build all downstream proposals."""

    class_stable_id: str
    agenda_item_stable_id: str
    assignment_stable_id: str
    title: str
    intended_due_date: str
    assignment_folder_item_id: str
    schoology_assignment_id: str
    infinite_campus_assignment_id: str
    deck_file_id: str
    form_file_id: Optional[str]
    agenda_revision_id: str
    routing_revision_id: str


@dataclass(frozen=True)
class ProposedDownstreamChange:
    """Local-only action evidence for one downstream target."""

    action_id: str
    action_type: str
    target_system: str
    target_stable_id: str
    state: str
    review_status: str
    required_permission: str
    expected_state: Dict[str, Any]
    proposed_state: Dict[str, Any]
    source_revision_id: str
    source_dependencies: Tuple[Tuple[str, str], ...]
    diff: Tuple[str, ...]
    external_write_performed: bool = False


@dataclass(frozen=True)
class AgendaInterconnectionProofResult:
    """The complete synthetic evidence bundle; never an execution result."""

    fixture_id: str
    run_id: str
    run_status: str
    outcome: str
    normalized_change: NormalizedAgendaChange
    source_revisions: Tuple[Tuple[str, str], ...]
    proposals: Tuple[ProposedDownstreamChange, ...]
    issues: Tuple[InterconnectionIssue, ...]
    external_writes: int = 0
    live_model_calls: int = 0


def load_synthetic_agenda_interconnection_fixture(path: Path) -> AgendaInterconnectionInput:
    """Load a repository-controlled JSON fixture; it is never an external source."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    return AgendaInterconnectionInput(
        fixture_id=raw["fixture_id"],
        synthetic_marker=raw["synthetic_marker"],
        agenda_item=AgendaItemRevision(**raw["agenda_item"]),
        assignment_route=AssignmentRouteRevision(**raw["assignment_route"]),
        assignment_folder=AssignmentFolderObservation(**raw["assignment_folder"]),
        schoology=SchoologyObservation(**raw["schoology"]),
        infinite_campus=InfiniteCampusObservation(**raw["infinite_campus"]),
    )


def run_synthetic_agenda_interconnection(
    connection: sqlite3.Connection, workflow_input: AgendaInterconnectionInput
) -> AgendaInterconnectionProofResult:
    """Record a local dry-run and generate source-bound proposed changes.

    This is intentionally safe to re-run: run, source, and action identities
    are deterministic, while no browser, model, adapter, or executor is used.
    """

    _require_synthetic_fixture(workflow_input)
    run = create_or_get_run(
        connection,
        "agenda_interconnection_proof",
        {"input_version": 1, "fixture": _input_as_dict(workflow_input)},
    )
    result = plan_agenda_interconnection(connection, run.id, workflow_input)
    finish_run(
        connection,
        run.id,
        details={
            "fixture_id": workflow_input.fixture_id,
            "outcome": result.outcome,
            "proposal_count": len(result.proposals),
            "issue_codes": [issue.code for issue in result.issues],
        },
    )
    return AgendaInterconnectionProofResult(
        fixture_id=result.fixture_id,
        run_id=result.run_id,
        run_status="completed",
        outcome=result.outcome,
        normalized_change=result.normalized_change,
        source_revisions=result.source_revisions,
        proposals=result.proposals,
        issues=result.issues,
    )


def plan_agenda_interconnection(
    connection: sqlite3.Connection, run_id: str, workflow_input: AgendaInterconnectionInput
) -> AgendaInterconnectionProofResult:
    """Normalize one agenda item, then create zero or more local proposals."""

    _require_synthetic_fixture(workflow_input)
    input_issues = _validate_input(workflow_input)
    source_revisions = _record_synthetic_source_revisions(connection, run_id, workflow_input)
    normalized = NormalizedAgendaChange(
        class_stable_id=workflow_input.agenda_item.class_stable_id,
        agenda_item_stable_id=workflow_input.agenda_item.agenda_item_stable_id,
        assignment_stable_id=workflow_input.agenda_item.assignment_stable_id,
        title=workflow_input.agenda_item.title,
        intended_due_date=workflow_input.agenda_item.intended_due_date,
        assignment_folder_item_id=workflow_input.assignment_route.assignment_folder_item_id,
        schoology_assignment_id=workflow_input.assignment_route.schoology_assignment_id,
        infinite_campus_assignment_id=workflow_input.assignment_route.infinite_campus_assignment_id,
        deck_file_id=workflow_input.assignment_route.deck_file_id,
        form_file_id=workflow_input.assignment_route.form_file_id,
        agenda_revision_id=source_revisions["agenda_item"],
        routing_revision_id=source_revisions["assignment_route"],
    )
    issues = input_issues + _routing_conflicts(workflow_input)
    source_summary = tuple(sorted(source_revisions.items()))
    if issues:
        return AgendaInterconnectionProofResult(
            fixture_id=workflow_input.fixture_id,
            run_id=run_id,
            run_status="running",
            outcome="denied",
            normalized_change=normalized,
            source_revisions=source_summary,
            proposals=(),
            issues=tuple(sorted(issues, key=lambda issue: (issue.code, issue.detail))),
        )

    expected_and_proposed = _target_states(workflow_input, normalized)
    proposals: List[ProposedDownstreamChange] = []
    for target_system, action_type, target_stable_id, expected, proposed, observation_role in expected_and_proposed:
        if expected == proposed:
            continue
        dependencies = {
            "assignment_route": source_revisions["assignment_route"],
            observation_role: source_revisions[observation_role],
        }
        action = propose_action(
            connection,
            run_id=run_id,
            action_type=action_type,
            target_system=target_system,
            target_stable_id=target_stable_id,
            source_stable_id=normalized.agenda_item_stable_id,
            source_revision_id=normalized.agenda_revision_id,
            source_dependencies=dependencies,
            expected_state=expected,
            proposed_state=proposed,
            evidence={
                "fixture_id": workflow_input.fixture_id,
                "agenda_item_stable_id": normalized.agenda_item_stable_id,
                "assignment_stable_id": normalized.assignment_stable_id,
                "source_revisions": {
                    "agenda_item": normalized.agenda_revision_id,
                    **dependencies,
                },
            },
            required_permission="external.update",
        )
        action_row = get_action(connection, action.id)
        source_dependencies = tuple(
            sorted((("primary", normalized.agenda_revision_id), *dependencies.items()))
        )
        proposals.append(
            ProposedDownstreamChange(
                action_id=action.id,
                action_type=action_type,
                target_system=target_system,
                target_stable_id=target_stable_id,
                state=action_row["state"],
                review_status=action_row["review_status"],
                required_permission=action_row["required_permission"],
                expected_state=json.loads(action_row["expected_state_json"]),
                proposed_state=json.loads(action_row["proposed_state_json"]),
                source_revision_id=action_row["source_revision_id"],
                source_dependencies=source_dependencies,
                diff=_diff_lines(target_system, target_stable_id, expected, proposed),
            )
        )

    outcome = "proposed" if proposals else "no_change"
    return AgendaInterconnectionProofResult(
        fixture_id=workflow_input.fixture_id,
        run_id=run_id,
        run_status="running",
        outcome=outcome,
        normalized_change=normalized,
        source_revisions=source_summary,
        proposals=tuple(proposals),
        issues=(),
    )


def proof_as_dict(result: AgendaInterconnectionProofResult) -> Dict[str, Any]:
    """Return a JSON-ready evidence bundle used by the golden tests."""

    return {
        "external_writes": result.external_writes,
        "fixture_id": result.fixture_id,
        "issues": [asdict(issue) for issue in result.issues],
        "live_model_calls": result.live_model_calls,
        "normalized_change": asdict(result.normalized_change),
        "outcome": result.outcome,
        "proposals": [
            {
                "action_id": proposal.action_id,
                "action_type": proposal.action_type,
                "diff": list(proposal.diff),
                "expected_state": proposal.expected_state,
                "external_write_performed": proposal.external_write_performed,
                "proposed_state": proposal.proposed_state,
                "required_permission": proposal.required_permission,
                "review_status": proposal.review_status,
                "source_dependencies": [list(dependency) for dependency in proposal.source_dependencies],
                "source_revision_id": proposal.source_revision_id,
                "state": proposal.state,
                "target_stable_id": proposal.target_stable_id,
                "target_system": proposal.target_system,
            }
            for proposal in result.proposals
        ],
        "run_id": result.run_id,
        "run_status": result.run_status,
        "source_revisions": [list(revision) for revision in result.source_revisions],
    }


def render_agenda_interconnection_proof(result: AgendaInterconnectionProofResult) -> str:
    """Render stable, inspectable golden output; no external presentation occurs."""

    return json.dumps(proof_as_dict(result), indent=2, sort_keys=True) + "\n"


def _input_as_dict(workflow_input: AgendaInterconnectionInput) -> Dict[str, Any]:
    return asdict(workflow_input)


def _require_synthetic_fixture(workflow_input: AgendaInterconnectionInput) -> None:
    """Fail before any local source record when this is not declared synthetic."""

    if workflow_input.synthetic_marker != "fixture-only":
        raise ValueError("agenda interconnection proof accepts only fixture-only synthetic input")
    if "synthetic" not in workflow_input.fixture_id:
        raise ValueError("synthetic fixture ID must include the synthetic marker")
    for record in (
        workflow_input.agenda_item,
        workflow_input.assignment_route,
        workflow_input.assignment_folder,
        workflow_input.schoology,
        workflow_input.infinite_campus,
    ):
        for field_name, value in asdict(record).items():
            if field_name.endswith("_id") or field_name == "revision_label":
                if value is not None and "synthetic" not in value:
                    raise ValueError("synthetic fixture identifier is required: {}".format(field_name))


def _record_synthetic_source_revisions(
    connection: sqlite3.Connection, run_id: str, workflow_input: AgendaInterconnectionInput
) -> Dict[str, str]:
    records: Iterable[Tuple[str, str, str, Any]] = (
        (
            "agenda_item",
            workflow_input.agenda_item.agenda_item_stable_id,
            "synthetic://agenda/{}".format(workflow_input.agenda_item.agenda_item_stable_id),
            workflow_input.agenda_item,
        ),
        (
            "assignment_route",
            workflow_input.assignment_route.assignment_stable_id,
            "synthetic://assignment-route/{}".format(workflow_input.assignment_route.assignment_stable_id),
            workflow_input.assignment_route,
        ),
        (
            "assignment_folder_observation",
            workflow_input.assignment_folder.assignment_folder_item_id,
            "synthetic://assignment-folder/{}".format(
                workflow_input.assignment_folder.assignment_folder_item_id
            ),
            workflow_input.assignment_folder,
        ),
        (
            "schoology_observation",
            workflow_input.schoology.schoology_assignment_id,
            "synthetic://schoology/{}".format(workflow_input.schoology.schoology_assignment_id),
            workflow_input.schoology,
        ),
        (
            "infinite_campus_observation",
            workflow_input.infinite_campus.infinite_campus_assignment_id,
            "synthetic://infinite-campus/{}".format(
                workflow_input.infinite_campus.infinite_campus_assignment_id
            ),
            workflow_input.infinite_campus,
        ),
    )
    revisions: Dict[str, str] = {}
    for source_type, stable_id, source_reference, record in records:
        revisions[source_type] = record_source_revision(
            connection,
            source_type,
            stable_id,
            asdict(record),
            source_reference,
            run_id,
        ).id
    return revisions


def _validate_input(workflow_input: AgendaInterconnectionInput) -> List[InterconnectionIssue]:
    issues: List[InterconnectionIssue] = []
    records = (
        ("agenda_item", workflow_input.agenda_item),
        ("assignment_route", workflow_input.assignment_route),
        ("assignment_folder", workflow_input.assignment_folder),
        ("schoology", workflow_input.schoology),
        ("infinite_campus", workflow_input.infinite_campus),
    )
    for record_name, record in records:
        for field_name, value in asdict(record).items():
            if field_name == "form_file_id" and value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    InterconnectionIssue(
                        code="invalid_{}_field".format(record_name),
                        severity="error",
                        detail="{} must provide a non-empty stable field: {}".format(
                            record_name, field_name
                        ),
                    )
                )
    for record_name, due_date in (
        ("agenda_item", workflow_input.agenda_item.intended_due_date),
        ("assignment_folder", workflow_input.assignment_folder.observed_due_date),
        ("schoology", workflow_input.schoology.observed_due_date),
        ("infinite_campus", workflow_input.infinite_campus.observed_due_date),
    ):
        if not _is_iso_date(due_date):
            issues.append(
                InterconnectionIssue(
                    code="invalid_{}_due_date".format(record_name),
                    severity="error",
                    detail="Due dates must be explicit ISO calendar dates; no date is inferred.",
                )
            )
    return issues


def _routing_conflicts(workflow_input: AgendaInterconnectionInput) -> List[InterconnectionIssue]:
    agenda = workflow_input.agenda_item
    route = workflow_input.assignment_route
    folder = workflow_input.assignment_folder
    schoology = workflow_input.schoology
    infinite_campus = workflow_input.infinite_campus
    issues: List[InterconnectionIssue] = []
    if (agenda.class_stable_id, agenda.assignment_stable_id) != (
        route.class_stable_id,
        route.assignment_stable_id,
    ):
        issues.append(
            InterconnectionIssue(
                "agenda_route_conflict",
                "error",
                "Agenda class/assignment stable IDs do not match the assignment route.",
            )
        )
    for system, observation, route_target, observation_target in (
        (
            "assignment_folder",
            folder,
            route.assignment_folder_item_id,
            folder.assignment_folder_item_id,
        ),
        ("schoology", schoology, route.schoology_assignment_id, schoology.schoology_assignment_id),
        (
            "infinite_campus",
            infinite_campus,
            route.infinite_campus_assignment_id,
            infinite_campus.infinite_campus_assignment_id,
        ),
    ):
        if (observation.class_stable_id, observation.assignment_stable_id) != (
            agenda.class_stable_id,
            agenda.assignment_stable_id,
        ):
            issues.append(
                InterconnectionIssue(
                    "{}_assignment_conflict".format(system),
                    "error",
                    "{} observation does not map to the Agenda class and assignment stable IDs.".format(
                        system
                    ),
                )
            )
        if route_target != observation_target:
            issues.append(
                InterconnectionIssue(
                    "{}_target_conflict".format(system),
                    "error",
                    "{} observed target ID does not match the assignment route target ID.".format(
                        system
                    ),
                )
            )
    return issues


def _target_states(
    workflow_input: AgendaInterconnectionInput, normalized: NormalizedAgendaChange
) -> Tuple[Tuple[str, str, str, Dict[str, Any], Dict[str, Any], str], ...]:
    folder = workflow_input.assignment_folder
    schoology = workflow_input.schoology
    infinite_campus = workflow_input.infinite_campus
    folder_expected = {
        "deck_file_id": folder.deck_file_id,
        "due_date": folder.observed_due_date,
        "form_file_id": folder.form_file_id,
        "message": folder.observed_message,
    }
    folder_proposed = {
        "deck_file_id": normalized.deck_file_id,
        "due_date": normalized.intended_due_date,
        "form_file_id": normalized.form_file_id,
        "message": _assignment_folder_message(normalized),
    }
    schoology_expected = {
        "due_date": schoology.observed_due_date,
        "message": schoology.observed_message,
    }
    schoology_proposed = {
        "due_date": normalized.intended_due_date,
        "message": _schoology_message(normalized),
    }
    infinite_campus_expected = {"due_date": infinite_campus.observed_due_date}
    infinite_campus_proposed = {"due_date": normalized.intended_due_date}
    return (
        (
            "assignment_folder",
            "update_assignment_folder_materials_and_message",
            normalized.assignment_folder_item_id,
            folder_expected,
            folder_proposed,
            "assignment_folder_observation",
        ),
        (
            "schoology",
            "update_schoology_message_and_due_date",
            normalized.schoology_assignment_id,
            schoology_expected,
            schoology_proposed,
            "schoology_observation",
        ),
        (
            "infinite_campus",
            "update_infinite_campus_due_date",
            normalized.infinite_campus_assignment_id,
            infinite_campus_expected,
            infinite_campus_proposed,
            "infinite_campus_observation",
        ),
    )


def _assignment_folder_message(normalized: NormalizedAgendaChange) -> str:
    return "Complete {title}. Materials: deck {deck}; form {form}. Due {due}.".format(
        title=normalized.title,
        deck=normalized.deck_file_id,
        form=normalized.form_file_id or "none",
        due=normalized.intended_due_date,
    )


def _schoology_message(normalized: NormalizedAgendaChange) -> str:
    return "Complete {title} using deck {deck}; submit form {form}. Due {due}.".format(
        title=normalized.title,
        deck=normalized.deck_file_id,
        form=normalized.form_file_id or "none",
        due=normalized.intended_due_date,
    )


def _diff_lines(
    target_system: str,
    target_stable_id: str,
    expected: Mapping[str, Any],
    proposed: Mapping[str, Any],
) -> Tuple[str, ...]:
    lines: List[str] = ["@@ {}:{} @@".format(target_system, target_stable_id)]
    for key in sorted(set(expected) | set(proposed)):
        if expected.get(key) == proposed.get(key):
            continue
        lines.append("- {}: {}".format(key, json.dumps(expected.get(key), sort_keys=True)))
        lines.append("+ {}: {}".format(key, json.dumps(proposed.get(key), sort_keys=True)))
    return tuple(lines)


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True
