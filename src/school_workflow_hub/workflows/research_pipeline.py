"""Pseudonymous research and dated field-note plans, with no journal writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Tuple

from school_workflow_hub.core.workflow import WorkflowIssue, WorkflowPreview
from school_workflow_hub.workflows.feedback_pipeline import (
    FeedbackPipelineResult, SIGNALS, require_fixture_id,
)
from school_workflow_hub.workflows.field_notes import (
    FakeClock, FieldNoteIntake, FieldNotesMaintenanceInput, LocalJournalAppendPlan,
    plan_field_notes_maintenance,
)


@dataclass(frozen=True)
class CategoryVersion:
    stable_id: str
    version: int
    signal: str
    prior_version: int | None = None

    def __post_init__(self):
        require_fixture_id(self.stable_id, "category")
        if type(self.version) is not int or self.version < 1 or self.signal not in SIGNALS - {"none"}:
            raise ValueError("invalid_category_version")
        if self.prior_version is not None and (type(self.prior_version) is not int or not 1 <= self.prior_version < self.version):
            raise ValueError("invalid_category_history")


@dataclass(frozen=True)
class ResearchCategoryResult:
    learner_pseudonym: str
    category_id: str
    category_version: int
    assignment_keys: Tuple[str, ...]
    source_revision_ids: Tuple[str, ...]
    threshold: int
    status: str
    inference: str = "provisional_inductive_category_not_diagnosis"


@dataclass(frozen=True)
class RetentionPlan:
    source_revision_id: str
    eligible_after: str
    eligible_now: bool
    action: str = "review_retention_only"
    deletion_performed: bool = False


@dataclass(frozen=True)
class SyntheticFieldNote:
    stable_id: str
    class_stable_id: str
    observed_on: date
    observation_code: str
    interpretation_code: str
    reflection_code: str


@dataclass(frozen=True)
class ResearchPipelineResult:
    categories: Tuple[ResearchCategoryResult, ...]
    category_history: Tuple[CategoryVersion, ...]
    retention_plans: Tuple[RetentionPlan, ...]
    field_note_plans: Tuple[LocalJournalAppendPlan, ...]
    preview: WorkflowPreview
    external_writes: int = 0
    live_model_calls: int = 0


_NOTE_TEXT = {
    "pause_before_comparison": "Synthetic observation: learners paused before comparing the two examples.",
    "transition_may_need_support": "Tentative interpretation: the transition may need additional support.",
    "try_partner_comparison": "Teacher reflection: try a brief partner comparison next lesson.",
}


def plan_research_pipeline(result: FeedbackPipelineResult, categories: Tuple[CategoryVersion, ...], field_notes: Tuple[SyntheticFieldNote, ...] = (), *, research_assignment_threshold: int = 3) -> ResearchPipelineResult:
    """Require evidence from three distinct assignments, never three revisions.

    Category versions are explicit, inductive and provisional. Revisions of a
    single assignment cannot manufacture repeated evidence. Retention is only
    a candidate review date; no row, source, artifact or grade is deleted.
    """
    if type(research_assignment_threshold) is not int or not 3 <= research_assignment_threshold <= 1000:
        raise ValueError("invalid_research_assignment_threshold")
    history = {}
    for category in categories:
        if not isinstance(category, CategoryVersion):
            raise ValueError("invalid_category_version")
        key = (category.stable_id, category.version)
        if key in history and history[key] != category:
            raise ValueError("conflicting_category_version")
        history[key] = category
    for category in history.values():
        if category.prior_version is not None and (category.stable_id, category.prior_version) not in history:
            raise ValueError("missing_category_history")
    current = {}
    for category in history.values():
        if category.stable_id not in current or current[category.stable_id].version < category.version:
            current[category.stable_id] = category
    groups = {}
    for record in result.records:
        if record.work_status != "submitted":
            continue
        for category in current.values():
            if record.conceptual_signal == category.signal:
                groups.setdefault((record.learner_pseudonym, category.stable_id), []).append(record)
    findings = []
    for (learner, category_id), items in sorted(groups.items()):
        assignments = tuple(sorted({item.assignment.stable_id for item in items}))
        findings.append(ResearchCategoryResult(learner, category_id, current[category_id].version, assignments, tuple(sorted({item.source_revision_id for item in items})), research_assignment_threshold, "eligible_for_teacher_review" if len(assignments) >= research_assignment_threshold else "insufficient_distinct_assignments"))
    retention = tuple(RetentionPlan(record.source_revision_id, (datetime.fromisoformat(record.submission.observed_at.replace("Z", "+00:00")) + timedelta(days=result.retention_days)).isoformat(), datetime.fromisoformat(record.submission.observed_at.replace("Z", "+00:00")) + timedelta(days=result.retention_days) <= result.as_of) for record in result.records)
    notes, issues = [], []
    seen = {}
    for item in field_notes:
        try:
            if not isinstance(item, SyntheticFieldNote):
                raise ValueError("invalid_field_note")
            require_fixture_id(item.stable_id, "field-note")
            require_fixture_id(item.class_stable_id, "class")
            if not isinstance(item.observed_on, date) or isinstance(item.observed_on, datetime) or item.observed_on > result.as_of.date():
                raise ValueError("invalid_field_note")
            if (item.observation_code != "pause_before_comparison" or item.interpretation_code != "transition_may_need_support" or item.reflection_code != "try_partner_comparison"):
                raise ValueError("invalid_field_note")
        except (ValueError, TypeError):
            issues.append(WorkflowIssue("invalid_synthetic_field_note", "error"))
            continue
        classes = {record.assignment.class_stable_id for record in result.records}
        if item.class_stable_id not in classes:
            issues.append(WorkflowIssue("wrong_course_field_note", "error"))
            continue
        if item.stable_id in seen:
            if seen[item.stable_id] != item:
                raise ValueError("field_note_requires_new_append_id")
            continue
        seen[item.stable_id] = item
        notes.append(FieldNoteIntake(item.stable_id, item.observed_on, item.class_stable_id, _NOTE_TEXT[item.observation_code], _NOTE_TEXT[item.interpretation_code], _NOTE_TEXT[item.reflection_code]))
    append_plans = plan_field_notes_maintenance(FieldNotesMaintenanceInput(tuple(notes), FakeClock(result.as_of.date()), False, (), ())).journal_append_plans
    artifacts = tuple(asdict(item) for item in findings) + tuple(asdict(item) for item in retention) + tuple({**asdict(item), "observed_on": item.observed_on.isoformat()} for item in append_plans)
    return ResearchPipelineResult(tuple(findings), tuple(history[key] for key in sorted(history)), retention, append_plans, WorkflowPreview("research_pipeline", issues=tuple(issues), artifacts=artifacts))
