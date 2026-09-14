"""Synthetic, plan-only curriculum/lesson/activity workstream scaffold.

This module deliberately makes a *plan* from declared synthetic input.  It
does not read source decks, regenerate lesson materials, contact an AI model,
or execute any external action.  In particular, Anatomy is returned as an
explicitly unsupported course so a future approved implementation can use the
appropriate source materials rather than silently regenerating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple


class Course(str, Enum):
    """Courses represented by this first, deliberately narrow scaffold."""

    BIOLOGY = "biology"
    FORENSICS = "forensics"
    ANATOMY = "anatomy"


class LessonPhase(str, Enum):
    """The required whole-to-whole learning architecture."""

    WHOLE = "whole"
    EXPLORE_PLAY = "explore/play"
    VARIATION_CRITICAL_ASPECTS = "variation/critical aspects"
    RELATIONSHIPS = "relationships"
    COORDINATE_WHOLE = "coordinate whole"


PEDAGOGICAL_ARCHITECTURE: Tuple[LessonPhase, ...] = (
    LessonPhase.WHOLE,
    LessonPhase.EXPLORE_PLAY,
    LessonPhase.VARIATION_CRITICAL_ASPECTS,
    LessonPhase.RELATIONSHIPS,
    LessonPhase.COORDINATE_WHOLE,
)


def _require_stable_id(value: str, field_name: str) -> None:
    """Reject empty/display-name-like identity values at the workflow edge."""

    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError(f"{field_name} must be a non-empty stable ID without whitespace")


@dataclass(frozen=True)
class OriginalMaterialRights:
    """A visible boundary for teacher-owned, licensed, or otherwise permitted work.

    The field is intentionally carried into every plan so later adapters cannot
    lose the provenance/right-respect requirement while moving between systems.
    """

    original_material_reference: str
    rights_respect: str
    reuse_boundary: str = "plan metadata only; do not copy, regenerate, or publish source materials"

    def __post_init__(self) -> None:
        if not self.original_material_reference.strip():
            raise ValueError("original_material_reference is required")
        if not self.rights_respect.strip():
            raise ValueError("rights_respect is required")


@dataclass(frozen=True)
class BiologyActivitySupport:
    """Teacher-facing pause and setup guidance for a Biology activity."""

    pause_after_phase: LessonPhase
    pause_note: str
    setup_notes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.pause_note.strip():
            raise ValueError("biology pause_note is required")
        if not self.setup_notes or any(not note.strip() for note in self.setup_notes):
            raise ValueError("biology setup_notes must contain at least one non-empty note")


@dataclass(frozen=True)
class ForensicsActivityPlan:
    """A bounded, relevant reading/evidence/logic/hands-on activity outline."""

    relevant_reading: str
    evidence_task: str
    logic_task: str
    hands_on_task: str
    estimated_minutes: int = 30

    def __post_init__(self) -> None:
        if not 25 <= self.estimated_minutes <= 35:
            raise ValueError("forensics estimated_minutes must stay near the 30-minute target")
        fields = (
            self.relevant_reading,
            self.evidence_task,
            self.logic_task,
            self.hands_on_task,
        )
        if any(not item.strip() for item in fields):
            raise ValueError("forensics activity requires reading, evidence, logic, and hands-on tasks")


@dataclass(frozen=True)
class CurriculumLessonInput:
    """Typed, source-referenced input for one local planning pass."""

    course: Course
    curriculum_version_stable_id: str
    lesson_stable_id: str
    source_revision_id: str
    display_title: str
    focal_question: str
    original_materials_rights: OriginalMaterialRights
    biology_activity: Optional[BiologyActivitySupport] = None
    forensics_activity: Optional[ForensicsActivityPlan] = None

    def __post_init__(self) -> None:
        _require_stable_id(self.curriculum_version_stable_id, "curriculum_version_stable_id")
        _require_stable_id(self.lesson_stable_id, "lesson_stable_id")
        _require_stable_id(self.source_revision_id, "source_revision_id")
        if not self.display_title.strip() or not self.focal_question.strip():
            raise ValueError("display_title and focal_question are required")
        if self.course is Course.BIOLOGY:
            if self.biology_activity is None or self.forensics_activity is not None:
                raise ValueError("biology input requires Biology activity support only")
        elif self.course is Course.FORENSICS:
            if self.forensics_activity is None or self.biology_activity is not None:
                raise ValueError("forensics input requires the bounded Forensics activity only")
        elif self.course is Course.ANATOMY:
            if self.biology_activity is not None or self.forensics_activity is not None:
                raise ValueError("anatomy is not generated by this scaffold")


@dataclass(frozen=True)
class PhasePlan:
    phase: LessonPhase
    teacher_intent: str


@dataclass(frozen=True)
class CurriculumLessonPlan:
    """A local plan that preserves stable source identity and rights metadata."""

    stable_id: str
    course: Course
    lesson_stable_id: str
    source_revision_id: str
    display_title: str
    phases: Tuple[PhasePlan, ...]
    original_materials_rights: OriginalMaterialRights
    biology_activity: Optional[BiologyActivitySupport] = None
    forensics_activity: Optional[ForensicsActivityPlan] = None


@dataclass(frozen=True)
class CurriculumWorkflowResult:
    """Result of a local planner; the execution boundary is observable in data."""

    status: str
    plan: Optional[CurriculumLessonPlan]
    reason: Optional[str] = None
    external_execution_attempted: bool = False
    live_model_calls: int = 0


class CurriculumPlanner(Protocol):
    """Future planners must retain this read/plan-only output contract."""

    def plan(self, request: CurriculumLessonInput) -> CurriculumWorkflowResult:
        ...


class FakeCurriculumPlanner:
    """Deterministic offline planner used only for tests and the synthetic demo."""

    def plan(self, request: CurriculumLessonInput) -> CurriculumWorkflowResult:
        if request.course is Course.ANATOMY:
            return CurriculumWorkflowResult(
                status="not_supported",
                plan=None,
                reason="Anatomy regeneration is deliberately out of scope for this scaffold",
            )

        plan_id = "curriculum-plan:{lesson}:{revision}".format(
            lesson=request.lesson_stable_id,
            revision=request.source_revision_id,
        )
        phases = (
            PhasePlan(LessonPhase.WHOLE, "Frame the whole question: {0}".format(request.focal_question)),
            PhasePlan(LessonPhase.EXPLORE_PLAY, "Let learners explore a bounded example before naming rules."),
            PhasePlan(
                LessonPhase.VARIATION_CRITICAL_ASPECTS,
                "Vary one critical aspect at a time and compare what changes.",
            ),
            PhasePlan(LessonPhase.RELATIONSHIPS, "Connect evidence, concepts, and the whole question."),
            PhasePlan(LessonPhase.COORDINATE_WHOLE, "Coordinate the parts into a revised explanation."),
        )
        plan = CurriculumLessonPlan(
            stable_id=plan_id,
            course=request.course,
            lesson_stable_id=request.lesson_stable_id,
            source_revision_id=request.source_revision_id,
            display_title=request.display_title,
            phases=phases,
            original_materials_rights=request.original_materials_rights,
            biology_activity=request.biology_activity,
            forensics_activity=request.forensics_activity,
        )
        return CurriculumWorkflowResult(status="planned_synthetic", plan=plan)


def plan_synthetic_lesson(
    request: CurriculumLessonInput, planner: Optional[CurriculumPlanner] = None
) -> CurriculumWorkflowResult:
    """Run a supplied local planner, defaulting to the deterministic fake.

    This wrapper intentionally has no executor parameter.  A future live path
    must be a separately approved workstream and pass the Hub permission gates.
    """

    active_planner: CurriculumPlanner = planner or FakeCurriculumPlanner()
    return active_planner.plan(request)


def synthetic_biology_example() -> CurriculumWorkflowResult:
    """Tiny non-student, non-source-deck fixture used by tests and local demos."""

    request = CurriculumLessonInput(
        course=Course.BIOLOGY,
        curriculum_version_stable_id="curriculum:biology:synthetic-v1",
        lesson_stable_id="biology:lesson:energy-intro",
        source_revision_id="source:synthetic:biology:energy-intro:v1",
        display_title="Synthetic energy introduction",
        focal_question="How can a system change while energy is conserved?",
        original_materials_rights=OriginalMaterialRights(
            original_material_reference="synthetic://curriculum/biology/energy-intro",
            rights_respect="synthetic fixture; no source material copied",
        ),
        biology_activity=BiologyActivitySupport(
            pause_after_phase=LessonPhase.EXPLORE_PLAY,
            pause_note="Pause after the first observation and surface a prediction.",
            setup_notes=("Stage the synthetic cards before class.",),
        ),
    )
    return plan_synthetic_lesson(request)
