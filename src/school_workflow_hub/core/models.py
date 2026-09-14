"""Typed records shared by workflows and adapters.

These contracts deliberately identify people and assignments by stable IDs, never
by their display names. They are lightweight because this foundation does not
replicate a live student-information system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ClassRecord:
    stable_id: str
    display_name: str
    calendar_id: str


@dataclass(frozen=True)
class EnrollmentRecord:
    class_stable_id: str
    learner_stable_id: str
    external_enrollment_id: Optional[str] = None


@dataclass(frozen=True)
class AssignmentRecord:
    stable_id: str
    class_stable_id: str
    title: str
    intended_due_date: Optional[str]
    deck_file_id: Optional[str] = None
    form_file_id: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmissionRevision:
    stable_id: str
    assignment_stable_id: str
    learner_stable_id: str
    revision_hash: str
    observed_at: str


@dataclass(frozen=True)
class FeedbackRunRequest:
    submission_revision_id: str
    prompt_version: str
    rubric_version: str
    model_identifier: str = "fake/disabled"


@dataclass(frozen=True)
class FeedbackRunResult:
    status: str
    feedback_reference: Optional[str]
    model_identifier: str
    needs_teacher_review: bool = True


@dataclass(frozen=True)
class GradeProposal:
    stable_id: str
    submission_revision_id: str
    proposed_value: Optional[str]
    rubric_version: str
    status: str = "proposed"


@dataclass(frozen=True)
class CumulativeSummary:
    learner_stable_id: str
    window_start: str
    window_end: str
    evidence_count: int
    status: str


@dataclass(frozen=True)
class FieldNote:
    stable_id: str
    observed_at: str
    class_stable_id: str
    observation: str
    interpretation: Optional[str] = None


@dataclass(frozen=True)
class CurriculumVersion:
    stable_id: str
    course: str
    version: str
    source_reference: str


@dataclass(frozen=True)
class AdapterResult:
    """An adapter outcome that never impersonates a live integration."""

    status: str
    data: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class SourceRevision:
    id: str
    source_type: str
    stable_object_id: str
    content_hash: str
    created: bool


@dataclass(frozen=True)
class RunResult:
    id: str
    created: bool
    status: str


@dataclass(frozen=True)
class ProposedAction:
    id: str
    created: bool
    state: str


@dataclass(frozen=True)
class ScheduleDecision:
    status: str
    reason: str
    local_time: str
