"""Synthetic-only agenda-to-assignment planning.

This workflow has no adapter, database, browser, or network dependency.  It
compares source *revisions* by stable IDs and returns local plans for a human
to review.  A returned plan is deliberately not an authorization or an
external-system write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class AgendaLessonRevision:
    """One intended lesson/assignment relationship from the Agenda.

    ``agenda_lesson_stable_id`` identifies the lesson record, while
    ``assignment_stable_id`` identifies the assignment across the Agenda,
    assignment master, and downstream systems.  Display names are intentionally
    not part of this contract.
    """

    class_stable_id: str
    agenda_lesson_stable_id: str
    assignment_stable_id: str
    deck_file_id: str
    form_file_id: Optional[str]
    intended_due_date: str
    agenda_revision_id: str


@dataclass(frozen=True)
class AssignmentMasterRevision:
    """Stable assignment routing captured from the assignment master."""

    class_stable_id: str
    assignment_stable_id: str
    deck_file_id: str
    form_file_id: Optional[str]
    infinite_campus_assignment_id: str
    assignment_master_revision_id: str


@dataclass(frozen=True)
class ObservedInfiniteCampusRevision:
    """A read-only observation of an existing Infinite Campus assignment."""

    class_stable_id: str
    assignment_stable_id: str
    infinite_campus_assignment_id: str
    observed_due_date: str
    observed_revision_id: str


@dataclass(frozen=True)
class AgendaAssignmentInput:
    """All records required for a single deterministic planning pass."""

    agenda_lessons: Tuple[AgendaLessonRevision, ...]
    assignment_master: Tuple[AssignmentMasterRevision, ...]
    observed_infinite_campus: Tuple[ObservedInfiniteCampusRevision, ...]


@dataclass(frozen=True)
class MappingIssue:
    """A condition that prevents or qualifies a downstream date proposal."""

    code: str
    severity: str
    class_stable_id: Optional[str]
    assignment_stable_id: Optional[str]
    detail: str


@dataclass(frozen=True)
class LocalProposedDateUpdate:
    """A local-only candidate for a later, separately authorized action.

    The three date fields make the authority boundary visible: Agenda supplies
    intent, Infinite Campus supplies observation, and this object supplies only
    a proposed update.  The workflow never calls an executor.
    """

    proposal_key: str
    class_stable_id: str
    agenda_lesson_stable_id: str
    assignment_stable_id: str
    deck_file_id: str
    form_file_id: Optional[str]
    infinite_campus_assignment_id: str
    agenda_revision_id: str
    assignment_master_revision_id: str
    observed_ic_revision_id: str
    intended_agenda_due_date: str
    observed_ic_due_date: str
    proposed_ic_due_date: str
    target_system: str = "infinite_campus"
    required_permission: str = "external.update"
    lifecycle_state: str = "plan_only"
    external_write_performed: bool = False


@dataclass(frozen=True)
class AgendaAssignmentPlanResult:
    """Immutable local result; plans require later validation and approval."""

    plans: Tuple[LocalProposedDateUpdate, ...]
    issues: Tuple[MappingIssue, ...]


def plan_agenda_assignment_updates(
    workflow_input: AgendaAssignmentInput,
) -> AgendaAssignmentPlanResult:
    """Compare revision-bound records and return safe local due-date plans.

    No marking-period date is calculated or inferred.  A date is used only when
    it is explicitly supplied in both the Agenda and observed IC record.  Any
    missing, conflicting, or ambiguous mapping blocks a plan for that
    assignment.
    """

    issues: List[MappingIssue] = []
    agenda_by_key = _group_by_assignment(workflow_input.agenda_lessons)
    master_by_key = _group_by_assignment(workflow_input.assignment_master)
    observed_by_key = _group_by_assignment(workflow_input.observed_infinite_campus)

    for lesson in workflow_input.agenda_lessons:
        _validate_agenda_lesson(lesson, issues)
    for master in workflow_input.assignment_master:
        _validate_assignment_master(master, issues)
    for observed in workflow_input.observed_infinite_campus:
        _validate_observed_ic(observed, issues)

    _report_duplicate_agenda_mapping(agenda_by_key, issues)
    _report_duplicate_mapping("assignment_master", master_by_key, issues)
    _report_duplicate_mapping("observed_infinite_campus", observed_by_key, issues)

    plans: List[LocalProposedDateUpdate] = []
    for key in sorted(agenda_by_key):
        lessons = agenda_by_key[key]
        if len(lessons) != 1:
            continue
        lesson = lessons[0]
        if not _agenda_record_is_valid(lesson):
            continue

        masters = master_by_key.get(key, [])
        if not masters:
            issues.append(
                _issue(
                    "missing_assignment_master_mapping",
                    lesson,
                    "No assignment-master record uses this class and assignment stable ID.",
                )
            )
            continue
        if len(masters) != 1:
            continue
        master = masters[0]
        if not _master_record_is_valid(master):
            continue
        if not _same_lesson_routing(lesson, master):
            issues.append(
                _issue(
                    "agenda_assignment_routing_conflict",
                    lesson,
                    "Agenda deck/Form stable IDs do not exactly match the assignment-master mapping.",
                )
            )
            continue

        observations = observed_by_key.get(key, [])
        if not observations:
            issues.append(
                _issue(
                    "missing_observed_ic_assignment",
                    lesson,
                    "No read-only Infinite Campus observation is available, so expected state cannot be verified.",
                )
            )
            continue
        if len(observations) != 1:
            continue
        observed = observations[0]
        if not _observed_record_is_valid(observed):
            continue
        if observed.infinite_campus_assignment_id != master.infinite_campus_assignment_id:
            issues.append(
                _issue(
                    "infinite_campus_target_conflict",
                    lesson,
                    "Observed Infinite Campus assignment ID does not match the assignment-master routing ID.",
                )
            )
            continue

        if lesson.intended_due_date == observed.observed_due_date:
            continue
        plans.append(
            LocalProposedDateUpdate(
                proposal_key=_proposal_key(lesson, master, observed),
                class_stable_id=lesson.class_stable_id,
                agenda_lesson_stable_id=lesson.agenda_lesson_stable_id,
                assignment_stable_id=lesson.assignment_stable_id,
                deck_file_id=lesson.deck_file_id,
                form_file_id=lesson.form_file_id,
                infinite_campus_assignment_id=master.infinite_campus_assignment_id,
                agenda_revision_id=lesson.agenda_revision_id,
                assignment_master_revision_id=master.assignment_master_revision_id,
                observed_ic_revision_id=observed.observed_revision_id,
                intended_agenda_due_date=lesson.intended_due_date,
                observed_ic_due_date=observed.observed_due_date,
                proposed_ic_due_date=lesson.intended_due_date,
            )
        )

    return AgendaAssignmentPlanResult(
        plans=tuple(sorted(plans, key=lambda item: item.proposal_key)),
        issues=tuple(sorted(issues, key=_issue_sort_key)),
    )


def synthetic_agenda_assignment_example() -> AgendaAssignmentInput:
    """Return a tiny fake source set suitable for demos and contract tests."""

    return AgendaAssignmentInput(
        agenda_lessons=(
            AgendaLessonRevision(
                class_stable_id="synthetic-biology-a4",
                agenda_lesson_stable_id="agenda:synthetic-biology-a4:week-01:lesson-01",
                assignment_stable_id="assignment:synthetic-biology-a4:energy-intro",
                deck_file_id="synthetic-deck-energy-intro",
                form_file_id="synthetic-form-energy-intro",
                intended_due_date="2026-09-16",
                agenda_revision_id="agenda-rev-synthetic-001",
            ),
        ),
        assignment_master=(
            AssignmentMasterRevision(
                class_stable_id="synthetic-biology-a4",
                assignment_stable_id="assignment:synthetic-biology-a4:energy-intro",
                deck_file_id="synthetic-deck-energy-intro",
                form_file_id="synthetic-form-energy-intro",
                infinite_campus_assignment_id="synthetic-ic-energy-intro",
                assignment_master_revision_id="assignment-master-rev-synthetic-001",
            ),
        ),
        observed_infinite_campus=(
            ObservedInfiniteCampusRevision(
                class_stable_id="synthetic-biology-a4",
                assignment_stable_id="assignment:synthetic-biology-a4:energy-intro",
                infinite_campus_assignment_id="synthetic-ic-energy-intro",
                observed_due_date="2026-09-15",
                observed_revision_id="observed-ic-rev-synthetic-001",
            ),
        ),
    )


def run_synthetic_agenda_assignment_example() -> AgendaAssignmentPlanResult:
    """Plan the tiny fake example without making any external call or write."""

    return plan_agenda_assignment_updates(synthetic_agenda_assignment_example())


def _group_by_assignment(records: Sequence[object]) -> Dict[Tuple[str, str], List[object]]:
    grouped: Dict[Tuple[str, str], List[object]] = {}
    for record in records:
        class_stable_id = getattr(record, "class_stable_id")
        assignment_stable_id = getattr(record, "assignment_stable_id")
        grouped.setdefault((class_stable_id, assignment_stable_id), []).append(record)
    return grouped


def _report_duplicate_agenda_mapping(
    grouped: Dict[Tuple[str, str], List[object]], issues: List[MappingIssue]
) -> None:
    for (class_stable_id, assignment_stable_id), records in grouped.items():
        if len(records) > 1:
            issues.append(
                MappingIssue(
                    code="ambiguous_agenda_assignment_mapping",
                    severity="error",
                    class_stable_id=class_stable_id,
                    assignment_stable_id=assignment_stable_id,
                    detail=(
                        "More than one Agenda lesson maps to this assignment stable ID; "
                        "the workflow will not choose a due date."
                    ),
                )
            )


def _report_duplicate_mapping(
    source_name: str, grouped: Dict[Tuple[str, str], List[object]], issues: List[MappingIssue]
) -> None:
    for (class_stable_id, assignment_stable_id), records in grouped.items():
        if len(records) > 1:
            issues.append(
                MappingIssue(
                    code="ambiguous_{}_mapping".format(source_name),
                    severity="error",
                    class_stable_id=class_stable_id,
                    assignment_stable_id=assignment_stable_id,
                    detail="More than one {} record uses this stable assignment mapping.".format(source_name),
                )
            )


def _validate_agenda_lesson(record: AgendaLessonRevision, issues: List[MappingIssue]) -> None:
    _validate_identifiers(
        record.class_stable_id,
        record.agenda_lesson_stable_id,
        record.assignment_stable_id,
        record.deck_file_id,
        record.agenda_revision_id,
        record=record,
        issues=issues,
        source_name="agenda",
    )
    _validate_optional_form_id(record.form_file_id, record, issues, "agenda")
    _validate_date(record.intended_due_date, record, issues, "invalid_agenda_due_date")


def _validate_assignment_master(record: AssignmentMasterRevision, issues: List[MappingIssue]) -> None:
    _validate_identifiers(
        record.class_stable_id,
        record.assignment_stable_id,
        record.deck_file_id,
        record.infinite_campus_assignment_id,
        record.assignment_master_revision_id,
        record=record,
        issues=issues,
        source_name="assignment_master",
    )
    _validate_optional_form_id(record.form_file_id, record, issues, "assignment_master")


def _validate_observed_ic(record: ObservedInfiniteCampusRevision, issues: List[MappingIssue]) -> None:
    _validate_identifiers(
        record.class_stable_id,
        record.assignment_stable_id,
        record.infinite_campus_assignment_id,
        record.observed_revision_id,
        record=record,
        issues=issues,
        source_name="observed_infinite_campus",
    )
    _validate_date(record.observed_due_date, record, issues, "invalid_observed_ic_due_date")


def _validate_identifiers(
    *identifiers: str,
    record: object,
    issues: List[MappingIssue],
    source_name: str,
) -> None:
    if any(not identifier or not identifier.strip() for identifier in identifiers):
        issues.append(
            MappingIssue(
                code="invalid_{}_stable_identifier".format(source_name),
                severity="error",
                class_stable_id=getattr(record, "class_stable_id", None),
                assignment_stable_id=getattr(record, "assignment_stable_id", None),
                detail="{} contains a missing stable identifier.".format(source_name),
            )
        )


def _validate_date(value: str, record: object, issues: List[MappingIssue], code: str) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        issues.append(
            MappingIssue(
                code=code,
                severity="error",
                class_stable_id=getattr(record, "class_stable_id", None),
                assignment_stable_id=getattr(record, "assignment_stable_id", None),
                detail="Dates must be explicit ISO calendar dates; this workflow never infers one.",
            )
        )


def _validate_optional_form_id(
    form_file_id: Optional[str], record: object, issues: List[MappingIssue], source_name: str
) -> None:
    if form_file_id is not None and not form_file_id.strip():
        issues.append(
            MappingIssue(
                code="invalid_{}_form_file_id".format(source_name),
                severity="error",
                class_stable_id=getattr(record, "class_stable_id", None),
                assignment_stable_id=getattr(record, "assignment_stable_id", None),
                detail="A Form ID must be a stable identifier or explicitly None when no Form applies.",
            )
        )


def _agenda_record_is_valid(record: AgendaLessonRevision) -> bool:
    return _identifiers_present(
        record.class_stable_id,
        record.agenda_lesson_stable_id,
        record.assignment_stable_id,
        record.deck_file_id,
        record.agenda_revision_id,
    ) and _form_is_valid(record.form_file_id) and _is_iso_date(record.intended_due_date)


def _master_record_is_valid(record: AssignmentMasterRevision) -> bool:
    return _identifiers_present(
        record.class_stable_id,
        record.assignment_stable_id,
        record.deck_file_id,
        record.infinite_campus_assignment_id,
        record.assignment_master_revision_id,
    ) and _form_is_valid(record.form_file_id)


def _observed_record_is_valid(record: ObservedInfiniteCampusRevision) -> bool:
    return _identifiers_present(
        record.class_stable_id,
        record.assignment_stable_id,
        record.infinite_campus_assignment_id,
        record.observed_revision_id,
    ) and _is_iso_date(record.observed_due_date)


def _identifiers_present(*identifiers: str) -> bool:
    return all(identifier and identifier.strip() for identifier in identifiers)


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _form_is_valid(value: Optional[str]) -> bool:
    return value is None or bool(value.strip())


def _same_lesson_routing(
    lesson: AgendaLessonRevision, master: AssignmentMasterRevision
) -> bool:
    return lesson.deck_file_id == master.deck_file_id and lesson.form_file_id == master.form_file_id


def _proposal_key(
    lesson: AgendaLessonRevision,
    master: AssignmentMasterRevision,
    observed: ObservedInfiniteCampusRevision,
) -> str:
    return "agenda-assignment-plan:{}:{}:{}:{}:{}".format(
        lesson.agenda_revision_id,
        master.assignment_master_revision_id,
        observed.observed_revision_id,
        master.infinite_campus_assignment_id,
        lesson.intended_due_date,
    )


def _issue(code: str, lesson: AgendaLessonRevision, detail: str) -> MappingIssue:
    return MappingIssue(
        code=code,
        severity="error",
        class_stable_id=lesson.class_stable_id,
        assignment_stable_id=lesson.assignment_stable_id,
        detail=detail,
    )


def _issue_sort_key(issue: MappingIssue) -> Tuple[str, str, str, str]:
    return (
        issue.class_stable_id or "",
        issue.assignment_stable_id or "",
        issue.code,
        issue.detail,
    )
