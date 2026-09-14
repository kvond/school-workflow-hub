"""Deterministic synthetic Agenda/calendar to downstream preview.

Calendar day labels are supplied evidence, never inferred by alternating dates.
An ordinal refers to a teaching meeting of the requested A/B day within this
explicit calendar window. Holidays consume no meeting. All output is review
data; this module has no adapter, writer, schedule or model client.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, Optional, Tuple

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import (
    EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview, assert_safe_preview, validate_preview,
)
from school_workflow_hub.workflows.agenda_assignment import (
    AgendaAssignmentInput, AgendaLessonRevision, AssignmentMasterRevision,
    ObservedInfiniteCampusRevision, plan_agenda_assignment_updates,
)
from school_workflow_hub.workflows.agenda_interconnection import (
    AssignmentFolderObservation, InfiniteCampusObservation, SchoologyObservation,
)


@dataclass(frozen=True)
class CalendarDay:
    date: str
    day_type: str  # A, B, or NO_SCHOOL; repeated A/B days are valid evidence.


@dataclass(frozen=True)
class CalendarRevision:
    stable_id: str
    revision: str
    days: Tuple[CalendarDay, ...]


@dataclass(frozen=True)
class AgendaSlot:
    lesson_id: str
    assignment: AssignmentRecord
    agenda_revision: str
    day_type: str
    meeting_index: int
    sessions: int = 1
    requires_form: bool = True


@dataclass(frozen=True)
class AssignmentObservation:
    assignment: AssignmentRecord
    revision: str


@dataclass(frozen=True)
class CurriculumPipelineInput:
    calendar: CalendarRevision
    agenda: Tuple[AgendaSlot, ...]
    assignments: Tuple[AssignmentObservation, ...]
    folders: Tuple[AssignmentFolderObservation, ...]
    schoology: Tuple[SchoologyObservation, ...]
    infinite_campus: Tuple[InfiniteCampusObservation, ...]
    previous_calendar: Optional[CalendarRevision] = None
    synthetic_marker: str = "fixture-only"


def _key(record: AssignmentRecord) -> str:
    # Same stable assignment model used by the rest of the Hub; titles never join.
    return record.stable_id


def _evidence(source: str, stable_id: str, revision: str) -> EvidenceRef:
    return EvidenceRef(source, stable_id, revision, "synthetic://{}/{}".format(source, stable_id))


def _change(key: str, system: str, target: str, field: str, old: Any, new: Any,
            evidence: Tuple[EvidenceRef, ...]) -> PreviewChange:
    payload = {"assignment_key": key, "system": system, "target": target,
               "field": field, "old": old, "new": new,
               "evidence": [asdict(item) for item in evidence]}
    return PreviewChange(deterministic_id("curriculum-change", payload), key, system,
                         target, field, old, new, evidence, 1.0, True)


def _valid_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _synthetic_id(value: Any) -> bool:
    return isinstance(value, str) and "synthetic" in value and bool(value.strip()) and not any(
        character.isspace() for character in value)


def _validate_synthetic_input(request: CurriculumPipelineInput) -> None:
    if request.synthetic_marker != "fixture-only":
        raise ValueError("curriculum pipeline accepts only fixture-only input")
    # Missing identifiers remain actionable mapping issues. Nonempty real IDs
    # cannot enter this synthetic-only implementation or its output artifacts.
    def check(value: Any, field: str = "") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "external_ids":
                    for identifier in child.values():
                        if identifier and not _synthetic_id(identifier):
                            raise ValueError("synthetic downstream IDs are required")
                else:
                    check(child, name)
        elif isinstance(value, (tuple, list)):
            for child in value:
                check(child, field)
        elif value and (field.endswith("_id") or field in ("stable_id", "revision", "agenda_revision", "revision_label")):
            if not _synthetic_id(value):
                raise ValueError("synthetic identifiers/revisions are required: " + field)
    assert_safe_preview(asdict(request))
    check(asdict(request))


def normalize_calendar(calendar: CalendarRevision) -> Tuple[Tuple[CalendarDay, ...], Tuple[WorkflowIssue, ...]]:
    """Return chronological explicit days, or errors without inventing a date."""
    issues = []
    seen = set()
    for day in calendar.days:
        if not _valid_date(day.date) or day.day_type not in ("A", "B", "NO_SCHOOL"):
            issues.append(WorkflowIssue("invalid_calendar_day", "error"))
        if day.date in seen:
            issues.append(WorkflowIssue("duplicate_calendar_date", "error"))
        seen.add(day.date)
    if not _synthetic_id(calendar.stable_id) or not _synthetic_id(calendar.revision):
        issues.append(WorkflowIssue("missing_calendar_provenance", "error"))
    return tuple(sorted(calendar.days, key=lambda day: day.date)), tuple(issues)


def plan_curriculum_pipeline(request: CurriculumPipelineInput) -> WorkflowPreview:
    """Produce field-level proposals and shared exception-queue issues.

    ``AssignmentRecord.intended_due_date`` is an optional explicit override.
    Otherwise the last of the declared sessions supplies the due date. An
    invalid or exhausted calendar blocks that lesson, rather than extending
    the calendar or shifting to an unobserved school day.
    """
    _validate_synthetic_input(request)
    days, calendar_issues = normalize_calendar(request.calendar)
    issues = list(calendar_issues)
    changes = []
    artifacts = []
    if calendar_issues:
        return WorkflowPreview("curriculum_pipeline", issues=tuple(issues))
    if request.previous_calendar:
        _, old_issues = normalize_calendar(request.previous_calendar)
        if old_issues:
            return WorkflowPreview("curriculum_pipeline", issues=tuple(old_issues))
        if sorted(request.previous_calendar.days, key=lambda day: day.date) != list(days):
            issues.append(WorkflowIssue("calendar_sequence_changed", "info"))

    agenda_by_key: Dict[str, list] = {}
    for slot in request.agenda:
        agenda_by_key.setdefault(_key(slot.assignment), []).append(slot)
    lesson_ids = [slot.lesson_id for slot in request.agenda]
    route_owners: Dict[Tuple[str, str], set] = {}
    for slot in request.agenda:
        for system, target in slot.assignment.external_ids.items():
            if target:
                route_owners.setdefault((system, target), set()).add(_key(slot.assignment))

    for key, slots in sorted(agenda_by_key.items()):
        if len(slots) != 1:
            issues.append(WorkflowIssue("duplicate_agenda_assignment", "error", key))
            continue
        slot = slots[0]
        intended = slot.assignment
        if lesson_ids.count(slot.lesson_id) != 1:
            issues.append(WorkflowIssue("ambiguous_lesson_identity", "error", key))
            continue
        if (not _synthetic_id(key) or not _synthetic_id(slot.lesson_id)
                or not _synthetic_id(intended.class_stable_id) or not _synthetic_id(slot.agenda_revision)
                or not intended.title.strip()):
            issues.append(WorkflowIssue("invalid_agenda_identity", "error", key))
            continue
        if not intended.deck_file_id or (slot.requires_form and not intended.form_file_id):
            issues.append(WorkflowIssue("missing_required_material_mapping", "error", key))
            continue
        if (slot.day_type not in ("A", "B") or type(slot.meeting_index) is not int
                or type(slot.sessions) is not int or slot.meeting_index < 1 or slot.sessions < 1):
            issues.append(WorkflowIssue("invalid_agenda_calendar_slot", "error", key))
            continue
        meetings = [day for day in days if day.day_type == slot.day_type]
        end_index = slot.meeting_index + slot.sessions - 2
        if end_index >= len(meetings):
            issues.append(WorkflowIssue("missing_calendar_meeting", "error", key))
            continue
        due = intended.intended_due_date or meetings[end_index].date
        if not _valid_date(due):
            issues.append(WorkflowIssue("invalid_agenda_due_date", "error", key))
            continue
        if due < meetings[slot.meeting_index - 1].date:
            issues.append(WorkflowIssue("due_date_precedes_lesson", "error", key))
            continue
        artifacts.append({"assignment_key": key, "lesson_id": slot.lesson_id,
                          "day_type": slot.day_type, "meeting_index": slot.meeting_index,
                          "teaching_dates": [day.date for day in meetings[slot.meeting_index - 1:end_index + 1]],
                          "intended_due_date": due, "calendar_revision": request.calendar.revision,
                          "agenda_revision": slot.agenda_revision})
        observed_masters = [item for item in request.assignments if _key(item.assignment) == key]
        if len(observed_masters) != 1:
            code = "duplicate_assignment_mapping" if observed_masters else "missing_assignment_mapping"
            issues.append(WorkflowIssue(code, "error", key))
            continue
        master = observed_masters[0]
        if master.assignment.class_stable_id != intended.class_stable_id:
            issues.append(WorkflowIssue("wrong_course_assignment_mapping", "error", key))
            continue
        if not master.revision:
            issues.append(WorkflowIssue("missing_assignment_revision", "error", key))
            continue
        if master.assignment.external_ids != intended.external_ids:
            issues.append(WorkflowIssue("unsafe_target_remapping", "error", key))
            continue
        if any(len(route_owners[(system, target)]) > 1
               for system, target in intended.external_ids.items() if target):
            issues.append(WorkflowIssue("duplicate_downstream_target", "error", key))
            continue
        base_evidence = (_evidence("agenda", slot.lesson_id, slot.agenda_revision),
                         _evidence("calendar", request.calendar.stable_id, request.calendar.revision),
                         _evidence("assignment_master", key, master.revision))
        for field, old, new in (
            ("title", master.assignment.title, intended.title),
            ("intended_due_date", master.assignment.intended_due_date, due),
            ("deck_file_id", master.assignment.deck_file_id, intended.deck_file_id),
            ("form_file_id", master.assignment.form_file_id, intended.form_file_id),
        ):
            if old != new:
                changes.append(_change(key, "assignment_master", key, field, old, new, base_evidence))

        # Reuse the proven Agenda/IC validator before converting its date plan
        # into the shared envelope. Link/title proposals still use the same
        # AssignmentRecord identity and this pass's source evidence.
        for system, observations, id_field in (
            ("assignment_folder", request.folders, "assignment_folder_item_id"),
            ("schoology", request.schoology, "schoology_assignment_id"),
            ("infinite_campus", request.infinite_campus, "infinite_campus_assignment_id"),
        ):
            target = intended.external_ids.get(system)
            matches = [item for item in observations if item.assignment_stable_id == key]
            if not target or len(matches) != 1:
                code = "ambiguous_{}_mapping".format(system) if len(matches) > 1 else "missing_{}_mapping".format(system)
                issues.append(WorkflowIssue(code, "error", key))
                continue
            observed = matches[0]
            if observed.class_stable_id != intended.class_stable_id or getattr(observed, id_field) != target:
                issues.append(WorkflowIssue("{}_target_conflict".format(system), "error", key))
                continue
            if not observed.revision_label or not _valid_date(observed.observed_due_date):
                issues.append(WorkflowIssue("invalid_{}_observation".format(system), "error", key))
                continue
            evidence = base_evidence + (_evidence(system, target, observed.revision_label),)
            if system == "infinite_campus":
                existing = plan_agenda_assignment_updates(AgendaAssignmentInput(
                    (AgendaLessonRevision(intended.class_stable_id, slot.lesson_id, key, intended.deck_file_id,
                                          intended.form_file_id, due, slot.agenda_revision),),
                    (AssignmentMasterRevision(intended.class_stable_id, key, intended.deck_file_id,
                                              intended.form_file_id, target, master.revision),),
                    (ObservedInfiniteCampusRevision(intended.class_stable_id, key, target,
                                                    observed.observed_due_date, observed.revision_label),),
                ))
                issues.extend(WorkflowIssue(item.code, item.severity, key) for item in existing.issues)
                for proposed in existing.plans:
                    changes.append(_change(key, system, target, "due_date", proposed.observed_ic_due_date,
                                           proposed.proposed_ic_due_date, evidence))
                continue
            if system == "assignment_folder":
                message = "Complete {title}. Materials: deck {deck}; form {form}. Due {due}.".format(
                    title=intended.title, deck=intended.deck_file_id, form=intended.form_file_id or "none", due=due)
                fields = (("deck_file_id", observed.deck_file_id, intended.deck_file_id),
                          ("form_file_id", observed.form_file_id, intended.form_file_id))
            else:
                message = "Complete {title} using deck {deck}; submit form {form}. Due {due}.".format(
                    title=intended.title, deck=intended.deck_file_id, form=intended.form_file_id or "none", due=due)
                fields = ()
            fields += (("due_date", observed.observed_due_date, due), ("message", observed.observed_message, message))
            for field, old, new in fields:
                if old != new:
                    changes.append(_change(key, system, target, field, old, new, evidence))
    preview = WorkflowPreview("curriculum_pipeline", changes=tuple(sorted(changes, key=lambda item: item.stable_id)),
                              issues=tuple(sorted(issues, key=lambda item: (item.assignment_key or "", item.code))),
                              artifacts=tuple(artifacts))
    validate_preview(preview)
    return preview


def compare_next_year_baseline(baseline: Tuple[AssignmentRecord, ...],
                               current: Tuple[AssignmentRecord, ...],
                               baseline_revision: str, current_revision: str) -> WorkflowPreview:
    """Compare synthetic teaching baselines by stable ID, retaining both versions."""
    changes = []
    issues = []
    assert_safe_preview([asdict(item) for item in baseline + current])
    for revision in (baseline_revision, current_revision):
        if not _synthetic_id(revision):
            raise ValueError("synthetic revision required")
    all_keys = sorted({item.stable_id for item in baseline + current})
    for key in all_keys:
        if not _synthetic_id(key):
            raise ValueError("synthetic assignment ID required")
        old = [item for item in baseline if item.stable_id == key]
        new = [item for item in current if item.stable_id == key]
        if len(old) != 1 or len(new) != 1:
            issues.append(WorkflowIssue("baseline_missing_or_duplicate_assignment", "warning", key))
            continue
        if old[0].class_stable_id != new[0].class_stable_id:
            issues.append(WorkflowIssue("baseline_wrong_course_assignment", "error", key))
            continue
        evidence = (_evidence("baseline", key, baseline_revision), _evidence("current", key, current_revision))
        for field in ("title", "intended_due_date", "deck_file_id", "form_file_id"):
            before, after = getattr(old[0], field), getattr(new[0], field)
            if before != after:
                changes.append(_change(key, "next_year_baseline", key, field, before, after, evidence))
    result = WorkflowPreview("next_year_baseline", changes=tuple(changes), issues=tuple(issues))
    validate_preview(result)
    return result


def synthetic_curriculum_pipeline_input() -> CurriculumPipelineInput:
    """Small public fixture constructor for the common integration harness."""
    assignment = AssignmentRecord(
        "assignment:synthetic:forensics-evidence", "class:synthetic:forensics-a1",
        "Synthetic evidence reasoning", None, "synthetic-deck-evidence", "synthetic-form-evidence",
        {"assignment_folder": "synthetic-folder-evidence", "schoology": "synthetic-schoology-evidence",
         "infinite_campus": "synthetic-ic-evidence"},
    )
    return CurriculumPipelineInput(
        CalendarRevision("synthetic-calendar-a1", "synthetic-calendar-v1",
                         (CalendarDay("2026-09-14", "A"), CalendarDay("2026-09-15", "B"),
                          CalendarDay("2026-09-16", "A"))),
        (AgendaSlot("synthetic-lesson-evidence", assignment, "synthetic-agenda-v1", "A", 1, 2),),
        (AssignmentObservation(assignment, "synthetic-assignment-master-v1"),),
        (AssignmentFolderObservation(assignment.class_stable_id, assignment.stable_id, "synthetic-folder-evidence",
                                     "2026-09-14", "Synthetic previous message", assignment.deck_file_id,
                                     assignment.form_file_id, "synthetic-folder-v1"),),
        (SchoologyObservation(assignment.class_stable_id, assignment.stable_id, "synthetic-schoology-evidence",
                              "2026-09-14", "Synthetic previous message", "synthetic-schoology-v1"),),
        (InfiniteCampusObservation(assignment.class_stable_id, assignment.stable_id, "synthetic-ic-evidence",
                                   "2026-09-14", "synthetic-ic-v1"),),
    )
