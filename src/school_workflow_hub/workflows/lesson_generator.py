"""Subject-configurable, two-lessons-ahead synthetic planning scaffold.

It composes existing curriculum phase contracts and teacher-declared source
metadata. It does not generate or modify source slides, use an AI provider,
or turn prompt replacement suggestions into live deck edits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping, Optional, Tuple

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import (
    EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview,
    assert_safe_preview, validate_preview,
)
from school_workflow_hub.workflows.curriculum import (
    BiologyActivitySupport, Course, CurriculumLessonInput, ForensicsActivityPlan,
    LessonPhase, OriginalMaterialRights, PEDAGOGICAL_ARCHITECTURE, plan_synthetic_lesson,
)


FORENSICS_REVISION_PROMPT = (
    "I am a high school Forensics student. Do not give or rewrite my answer. "
    "Quote one evidence-supported idea. Ask: What is the strongest reason your conclusion "
    "could be wrong, incomplete, or too certain? Wait for my reply. Ask at most one "
    "useful follow-up about alternatives or missing evidence, then stop for my own "
    "revision. Do not assume guilt. QUESTION: [CURRENT FORENSICS QUESTION] "
    "FIRST ANSWER: [YOUR FIRST ANSWER]. I write in Your revised answer."
)

ANATOMY_REVISION_PROMPT = (
    "I am an Anatomy student. Do not write my answer for me. Quote one sound part "
    "of my explanation. Ask one focused, non-leading question connecting structure, "
    "function, mechanism, and evidence or a model limit. Wait for my reply. Ask at "
    "most one useful follow-up, then stop for my own revision. "
    "QUESTION: [CURRENT ANATOMY QUESTION] MY FIRST ANSWER: [YOUR FIRST ANSWER]."
)

DEFAULT_PEDAGOGICAL_PROMPT = (
    "Plan around {focal_question}. Use whole, explore/play, variation/critical aspects, "
    "relationships, coordinate whole. Vary one critical aspect at a time. Preserve "
    "source attribution and connect the final explanation to the initial whole."
)


@dataclass(frozen=True)
class SubjectLessonConfig:
    course: Course
    prompt_version: str
    pedagogical_prompt: str = DEFAULT_PEDAGOGICAL_PROMPT
    revision_prompt: str = ""
    activity_minutes: int = 30
    enabled: bool = True


@dataclass(frozen=True)
class LessonCandidate:
    lesson_id: str
    assignment: AssignmentRecord
    course: Course
    sequence: int
    focal_question: str
    source: EvidenceRef
    rights: OriginalMaterialRights
    field_notes: Tuple[EvidenceRef, ...] = ()
    biology_activity: Optional[BiologyActivitySupport] = None
    forensics_activity: Optional[ForensicsActivityPlan] = None


@dataclass(frozen=True)
class LessonGenerationInput:
    candidates: Tuple[LessonCandidate, ...]
    subject_configs: Tuple[SubjectLessonConfig, ...]
    current_sequence: Mapping[str, int]
    synthetic_marker: str = "fixture-only"


def default_subject_configs() -> Tuple[SubjectLessonConfig, ...]:
    """Replaceable prompt content; deployment configuration owns activation."""
    return (
        SubjectLessonConfig(Course.BIOLOGY, "synthetic-pedagogy-biology-v1",
                            revision_prompt="Ask me to connect evidence, mechanisms, and energy or matter; do not rewrite my answer."),
        SubjectLessonConfig(Course.FORENSICS, "synthetic-pedagogy-forensics-v1",
                            revision_prompt=FORENSICS_REVISION_PROMPT),
        SubjectLessonConfig(Course.ANATOMY, "synthetic-pedagogy-anatomy-v1",
                            revision_prompt=ANATOMY_REVISION_PROMPT),
    )


def subject_configs_from_rules(rules: Mapping[str, Mapping]) -> Tuple[SubjectLessonConfig, ...]:
    """Adapt the lead-owned HubConfig.course_rules without another config file."""
    defaults = {config.course.value: config for config in default_subject_configs()}
    configs = []
    for course in Course:
        rule = rules.get(course.value)
        if rule is None:
            continue
        if rule.get("lessons_ahead", 2) != 2:
            raise ValueError("this scaffold supports exactly two lessons ahead")
        default = defaults[course.value]
        focus = rule.get("revision_focus", "")
        prompt = rule.get("pedagogical_prompt", default.pedagogical_prompt)
        if focus:
            prompt += " Revision focus: " + focus
        configs.append(SubjectLessonConfig(
            course, rule.get("prompt_version", default.prompt_version), prompt,
            rule.get("revision_prompt", default.revision_prompt),
            rule.get("activity_minutes", default.activity_minutes), rule.get("enabled", True),
        ))
    return tuple(configs)


def _synthetic(value: str) -> bool:
    return isinstance(value, str) and "synthetic" in value and not any(c.isspace() for c in value)


def _source_ok(source: EvidenceRef) -> bool:
    return (_synthetic(source.stable_id) and _synthetic(source.revision)
            and bool(source.source_type) and source.reference.startswith("synthetic://"))


def _finish(workflow: str, changes=(), issues=(), artifacts=()) -> WorkflowPreview:
    result = WorkflowPreview(workflow, tuple(changes), tuple(issues), tuple(artifacts))
    validate_preview(result)
    return result


def plan_two_lessons_ahead(request: LessonGenerationInput) -> WorkflowPreview:
    """Plan at most two next lessons per configured subject, by declared order.

    An ambiguous or incomplete next lesson stays in the selected window and
    enters review; it is not silently skipped to a later lesson. Anatomy gets
    only an adaptation outline, keeping the original curriculum module's
    no-regeneration boundary intact.
    """
    if request.synthetic_marker != "fixture-only":
        raise ValueError("lesson generator accepts only fixture-only input")
    assert_safe_preview(asdict(request))
    changes, issues, artifacts = [], [], []
    for course in Course:
        configs = [item for item in request.subject_configs if item.course == course]
        if not configs:
            if any(item.course == course for item in request.candidates):
                issues.append(WorkflowIssue("missing_subject_configuration", "error"))
            continue
        if len(configs) != 1:
            issues.append(WorkflowIssue("duplicate_subject_configuration", "error"))
            continue
        config = configs[0]
        if not config.enabled:
            continue
        candidates = [item for item in request.candidates if item.course == course]
        if not candidates:
            continue
        after = request.current_sequence.get(course.value)
        if type(after) is not int or after < 0:
            issues.append(WorkflowIssue("missing_current_lesson_sequence", "error"))
            continue
        if (not _synthetic(config.prompt_version) or not config.pedagogical_prompt.strip()
                or type(config.activity_minutes) is not int or not 1 <= config.activity_minutes <= 120):
            issues.append(WorkflowIssue("invalid_subject_configuration", "error"))
            continue
        if any(type(item.sequence) is not int or item.sequence < 1 for item in candidates):
            issues.append(WorkflowIssue("invalid_lesson_sequence", "error"))
            continue
        sequences = sorted({item.sequence for item in candidates if item.sequence > after})[:2]
        if len(sequences) < 2:
            issues.append(WorkflowIssue("two_lesson_window_incomplete", "warning"))
        for sequence in sequences:
            choices = [item for item in candidates if item.sequence == sequence]
            if len(choices) != 1:
                issues.append(WorkflowIssue("ambiguous_lesson_sequence", "error"))
                continue
            candidate = choices[0]
            key = candidate.assignment.stable_id
            if (not _synthetic(candidate.lesson_id) or not _synthetic(key)
                    or not _synthetic(candidate.assignment.class_stable_id)
                    or not candidate.focal_question.strip() or not candidate.assignment.title.strip()):
                issues.append(WorkflowIssue("invalid_lesson_identity", "error", key))
                continue
            if sum(item.lesson_id == candidate.lesson_id or item.assignment.stable_id == key
                   for item in request.candidates) != 1:
                issues.append(WorkflowIssue("duplicate_lesson_identity", "error", key))
                continue
            if (not _source_ok(candidate.source) or any(not _source_ok(item) for item in candidate.field_notes)
                    or not candidate.rights.original_material_reference.startswith("synthetic://")
                    or not candidate.rights.rights_respect.strip()):
                issues.append(WorkflowIssue("missing_source_or_rights_evidence", "error", key))
                continue
            source_keys = [(item.source_type, item.stable_id) for item in (candidate.source,) + candidate.field_notes]
            if len(source_keys) != len(set(source_keys)):
                issues.append(WorkflowIssue("duplicate_source_evidence", "error", key))
                continue
            try:
                # Plain replacement deliberately avoids evaluating braces or
                # instructions embedded in teacher-supplied source strings.
                rendered_prompt = config.pedagogical_prompt.replace("{focal_question}", candidate.focal_question)
                phases = [phase.value for phase in PEDAGOGICAL_ARCHITECTURE]
                activity = None
                if course == Course.ANATOMY and (candidate.biology_activity or candidate.forensics_activity):
                    raise ValueError("Anatomy outline must not use another course activity")
                if course != Course.ANATOMY:
                    forensic_activity = candidate.forensics_activity
                    if course == Course.FORENSICS and forensic_activity is not None:
                        forensic_activity = replace(forensic_activity, estimated_minutes=config.activity_minutes)
                    result = plan_synthetic_lesson(CurriculumLessonInput(
                        course, "synthetic-curriculum-" + course.value, candidate.lesson_id,
                        candidate.source.revision, candidate.assignment.title, candidate.focal_question,
                        candidate.rights, candidate.biology_activity, forensic_activity,
                    ))
                    if result.plan is None:
                        raise ValueError("no curriculum plan")
                    phases = [phase.phase.value for phase in result.plan.phases]
                    activity_record = result.plan.biology_activity or result.plan.forensics_activity
                    activity = asdict(activity_record) if activity_record else None
            except ValueError:
                issues.append(WorkflowIssue("invalid_subject_activity", "error", key))
                continue
            evidence = (candidate.source,) + candidate.field_notes + (
                EvidenceRef("pedagogical_prompt", "synthetic-prompt-" + course.value,
                            config.prompt_version, "synthetic://prompts/" + course.value),)
            plan = {
                "lesson_id": candidate.lesson_id, "assignment_key": key, "course": course.value,
                "sequence": sequence, "title": candidate.assignment.title,
                "focal_question": candidate.focal_question, "phases": phases,
                "pedagogical_prompt": rendered_prompt, "prompt_version": config.prompt_version,
                "revision_prompt": config.revision_prompt, "activity_minutes": config.activity_minutes,
                "prompt_status": "staged_draft_not_manual_certified",
                "activity": activity, "source": asdict(candidate.source),
                "rights": asdict(candidate.rights), "field_notes": [asdict(item) for item in candidate.field_notes],
                "mode": "anatomy_adaptation_outline" if course == Course.ANATOMY else "synthetic_lesson_outline",
                "deck_modified": False, "source_regenerated": False,
            }
            stable_id = deterministic_id("lesson-outline", {"plan": plan, "evidence": [asdict(item) for item in evidence]})
            changes.append(PreviewChange(stable_id, key, "local_lesson_plan", candidate.lesson_id,
                                         "lesson_outline", None, plan, evidence, 1.0, True))
            artifacts.append(plan)
    return _finish("two_lessons_ahead", changes, issues, artifacts)


@dataclass(frozen=True)
class PromptOccurrence:
    assignment_key: str
    deck_id: str
    slide_id: str
    field: str
    text: str
    source_revision: str
    evidence_reference: str
    current_version: Optional[str] = None
    marker_ids: Tuple[str, ...] = ()


def inventory_forensics_prompts(occurrences: Tuple[PromptOccurrence, ...],
                                replacement: str = FORENSICS_REVISION_PROMPT,
                                prompt_version: str = "synthetic-forensics-revision-v2") -> WorkflowPreview:
    """Inventory synthetic prompt occurrences; draft conservative replacements.

    Exact replacement text is a no-op. Known Biology contamination and old
    generic revision scripts receive pending suggestions. Unknown wording
    remains an exception, and embedded feedback/grading codes are out of scope.
    """
    assert_safe_preview([asdict(item) for item in occurrences])
    assert_safe_preview(replacement)
    if not replacement.strip() or not _synthetic(prompt_version):
        raise ValueError("replacement prompt and synthetic version are required")
    changes, issues, artifacts = [], [], []
    identities = [(item.deck_id, item.slide_id, item.field) for item in occurrences]
    for occurrence in sorted(occurrences, key=lambda item: (item.deck_id, item.slide_id, item.field)):
        identity = (occurrence.deck_id, occurrence.slide_id, occurrence.field)
        key = occurrence.assignment_key
        if any(not _synthetic(value) for value in (key, occurrence.deck_id, occurrence.slide_id, occurrence.source_revision)):
            raise ValueError("synthetic prompt occurrence identifiers are required")
        if not occurrence.evidence_reference.startswith("synthetic://"):
            raise ValueError("synthetic prompt evidence reference required")
        if any(not _synthetic(marker) for marker in occurrence.marker_ids):
            raise ValueError("synthetic marker identifiers required")
        if identities.count(identity) != 1:
            issues.append(WorkflowIssue("duplicate_prompt_occurrence", "error", key))
            continue
        normalized = " ".join(occurrence.text.split()).casefold()
        current = " ".join(replacement.split()).casefold()
        if occurrence.field not in ("student_revision_prompt", "speaker_notes_revision_prompt"):
            status = "protected_or_unknown_field"
            issues.append(WorkflowIssue("prompt_field_requires_review", "warning", key))
        elif normalized == current:
            status = "current"
        elif (not normalized or "photosynthesis" in normalized or "sun's energy" in normalized
              or ("first answer" in normalized and "two questions" in normalized
                  and "do not rewrite" in normalized)):
            status = "missing_prompt_replacement_proposed" if not normalized else "replacement_proposed"
            evidence = (
                EvidenceRef("prompt_occurrence", occurrence.deck_id + ":" + occurrence.slide_id + ":" + occurrence.field,
                            occurrence.source_revision, occurrence.evidence_reference),
                EvidenceRef("pedagogical_prompt", "synthetic-forensics-revision", prompt_version,
                            "synthetic://prompts/forensics-revision"),
            )
            payload = {"occurrence": asdict(occurrence), "replacement": replacement, "prompt_version": prompt_version}
            changes.append(PreviewChange(deterministic_id("prompt-replacement", payload), key, "google_slides",
                                         occurrence.deck_id + ":" + occurrence.slide_id, occurrence.field,
                                         occurrence.text, replacement, evidence, 0.8 if not normalized else 0.95, True))
        else:
            status = "unknown_requires_review"
            issues.append(WorkflowIssue("unrecognized_revision_prompt", "warning", key))
        artifacts.append({"assignment_key": key, "deck_id": occurrence.deck_id,
                          "slide_id": occurrence.slide_id, "field": occurrence.field,
                          "source_revision": occurrence.source_revision, "status": status,
                          "observed_version": occurrence.current_version, "desired_version": prompt_version,
                          "prompt_status": "staged_draft_not_manual_certified",
                          "preserved_marker_ids": list(occurrence.marker_ids)})
    return _finish("forensics_prompt_inventory", changes, issues, artifacts)


def classroom_job_board_preview(roles: Tuple[str, ...], synthetic_aliases: Tuple[str, ...],
                                revision: str) -> WorkflowPreview:
    """Optional deterministic synthetic role rotation, with no real student data."""
    if not _synthetic(revision) or any(not alias.startswith("synthetic-learner-") for alias in synthetic_aliases):
        raise ValueError("synthetic aliases and revision required")
    assert_safe_preview([roles, synthetic_aliases])
    if len(set(roles)) != len(roles) or len(set(synthetic_aliases)) != len(synthetic_aliases):
        return _finish("classroom_job_board", issues=(WorkflowIssue("duplicate_job_or_alias", "error"),))
    if not roles or len(synthetic_aliases) < len(roles) or any(not role.strip() for role in roles):
        return _finish("classroom_job_board", issues=(WorkflowIssue("incomplete_job_board", "warning"),))
    board = {"revision": revision,
             "assignments": [{"role": role, "synthetic_alias": alias} for role, alias in zip(roles, synthetic_aliases)]}
    return _finish("classroom_job_board", artifacts=(board,))
