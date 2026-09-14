from __future__ import annotations

from dataclasses import replace
import unittest

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.workflow import EvidenceRef, validate_preview
from school_workflow_hub.workflows.curriculum import (
    BiologyActivitySupport, Course, ForensicsActivityPlan, LessonPhase,
    OriginalMaterialRights, PEDAGOGICAL_ARCHITECTURE,
)
from school_workflow_hub.workflows.lesson_generator import (
    ANATOMY_REVISION_PROMPT, FORENSICS_REVISION_PROMPT, LessonCandidate,
    LessonGenerationInput, PromptOccurrence, classroom_job_board_preview,
    default_subject_configs, inventory_forensics_prompts, plan_two_lessons_ahead,
    subject_configs_from_rules,
)


def synthetic_lesson_candidate(course=Course.FORENSICS, sequence=1):
    key = "synthetic-{}-lesson-{}".format(course.value, sequence)
    return LessonCandidate(
        key, AssignmentRecord("synthetic-assignment-{}-{}".format(course.value, sequence),
                              "synthetic-class-" + course.value, "Synthetic lesson {}".format(sequence), None),
        course, sequence, "Which explanation accounts for the evidence?",
        EvidenceRef("curriculum", key + "-source", "synthetic-source-v1", "synthetic://curriculum/" + key),
        OriginalMaterialRights("synthetic://curriculum/" + key, "Synthetic teacher-authored fixture; no source copied."),
        (EvidenceRef("field_note", key + "-note", "synthetic-note-v1", "synthetic://field-note/" + key),),
        BiologyActivitySupport(LessonPhase.EXPLORE_PLAY, "Pause to record a prediction.", ("Stage synthetic cards.",))
        if course == Course.BIOLOGY else None,
        ForensicsActivityPlan("Read a synthetic case.", "Sort observations and inferences.",
                              "Test the strongest competing explanation.", "Arrange synthetic evidence cards.")
        if course == Course.FORENSICS else None,
    )


def synthetic_generation_input():
    return LessonGenerationInput(
        tuple(synthetic_lesson_candidate(course, sequence) for course in Course for sequence in range(1, 5)),
        default_subject_configs(), {course.value: 1 for course in Course})


def synthetic_prompt(text="QUESTION: What does photosynthesis do with the Sun's energy?"):
    return PromptOccurrence("synthetic-assignment-forensics", "synthetic-deck-forensics", "synthetic-slide-1",
                            "student_revision_prompt", text, "synthetic-deck-v1", "synthetic://prompt/slide-1")


class LessonGeneratorTests(unittest.TestCase):
    def test_next_two_per_subject_reuses_architecture_and_retains_sources(self):
        result = plan_two_lessons_ahead(synthetic_generation_input())
        validate_preview(result)
        self.assertEqual(len(result.changes), 6)
        self.assertEqual({artifact["sequence"] for artifact in result.artifacts}, {2, 3})
        for artifact in result.artifacts:
            self.assertEqual(artifact["phases"], [phase.value for phase in PEDAGOGICAL_ARCHITECTURE])
            self.assertTrue(artifact["rights"]["rights_respect"])
            self.assertEqual(len(artifact["field_notes"]), 1)
            self.assertFalse(artifact["deck_modified"])
            self.assertFalse(artifact["source_regenerated"])
        self.assertEqual(result.external_writes, 0)
        self.assertEqual(result.live_model_calls, 0)

    def test_subject_prompts_and_activity_differences(self):
        result = plan_two_lessons_ahead(synthetic_generation_input())
        by_course = {item["course"]: item for item in result.artifacts}
        self.assertEqual(by_course["forensics"]["revision_prompt"], FORENSICS_REVISION_PROMPT)
        self.assertEqual(by_course["forensics"]["activity"]["estimated_minutes"], 30)
        self.assertEqual(by_course["anatomy"]["revision_prompt"], ANATOMY_REVISION_PROMPT)
        self.assertEqual(by_course["anatomy"]["mode"], "anatomy_adaptation_outline")
        self.assertIsNone(by_course["anatomy"]["activity"])
        self.assertTrue(by_course["biology"]["activity"]["setup_notes"])

    def test_replaceable_prompt_updates_provenance_and_id_without_live_model(self):
        request = synthetic_generation_input()
        first = plan_two_lessons_ahead(request)
        config = replace(request.subject_configs[0], pedagogical_prompt="Synthetic new pattern: {focal_question}",
                         prompt_version="synthetic-pedagogy-biology-v2")
        second = plan_two_lessons_ahead(replace(request, subject_configs=(config,) + request.subject_configs[1:]))
        before = [item.stable_id for item in first.changes if item.proposed_value["course"] == "biology"]
        after = [item.stable_id for item in second.changes if item.proposed_value["course"] == "biology"]
        self.assertNotEqual(before, after)
        self.assertTrue(all("{focal_question}" not in item["pedagogical_prompt"] for item in second.artifacts))
        self.assertEqual(second.live_model_calls, 0)

    def test_identical_and_reordered_input_has_identical_result(self):
        request = synthetic_generation_input()
        first = plan_two_lessons_ahead(request)
        self.assertEqual(first, plan_two_lessons_ahead(request))
        self.assertEqual(first, plan_two_lessons_ahead(replace(request, candidates=tuple(reversed(request.candidates)))))

    def test_ambiguous_next_lesson_is_not_skipped_to_later_lesson(self):
        request = synthetic_generation_input()
        duplicate = synthetic_lesson_candidate(Course.FORENSICS, 2)
        result = plan_two_lessons_ahead(replace(request, candidates=request.candidates + (duplicate,)))
        forensic = [item for item in result.artifacts if item["course"] == "forensics"]
        self.assertEqual([item["sequence"] for item in forensic], [3])
        self.assertIn("ambiguous_lesson_sequence", [issue.code for issue in result.issues])

    def test_missing_source_evidence_or_activity_enters_shared_exception_contract(self):
        item = synthetic_lesson_candidate()
        bad_source = replace(item, source=replace(item.source, revision=""))
        bad_activity = replace(item, forensics_activity=None)
        for candidate, code in ((bad_source, "missing_source_or_rights_evidence"),
                                (bad_activity, "invalid_subject_activity")):
            request = LessonGenerationInput((candidate,), default_subject_configs(), {course.value: 0 for course in Course})
            result = plan_two_lessons_ahead(request)
            self.assertFalse(result.changes)
            self.assertIn(code, [issue.code for issue in result.issues])

    def test_disabled_subject_and_missing_current_position_do_not_guess(self):
        request = synthetic_generation_input()
        disabled = tuple(replace(item, enabled=False) for item in request.subject_configs)
        self.assertEqual(plan_two_lessons_ahead(replace(request, subject_configs=disabled)).changes, ())
        result = plan_two_lessons_ahead(replace(request, current_sequence={}))
        self.assertFalse(result.changes)
        self.assertEqual({item.code for item in result.issues}, {"missing_current_lesson_sequence"})

    def test_prompt_inventory_drafts_forensics_replacement_with_old_new_evidence(self):
        result = inventory_forensics_prompts((synthetic_prompt(),))
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertIn("photosynthesis", change.old_value)
        self.assertIn("strongest reason your conclusion could be wrong, incomplete, or too certain", change.proposed_value)
        self.assertNotIn("photosynthesis", change.proposed_value)
        self.assertEqual(change.approval_status, "pending")
        self.assertTrue(change.reversible)
        self.assertEqual(len(change.evidence), 2)
        self.assertEqual(result, inventory_forensics_prompts((synthetic_prompt(),)))

    def test_prompt_current_noop_unknown_and_protected_fields_require_review(self):
        current = inventory_forensics_prompts((synthetic_prompt(FORENSICS_REVISION_PROMPT),))
        self.assertFalse(current.changes)
        self.assertEqual(current.artifacts[0]["status"], "current")
        unknown = inventory_forensics_prompts((synthetic_prompt("Synthetic custom classroom directions"),))
        self.assertFalse(unknown.changes)
        self.assertEqual(unknown.issues[0].code, "unrecognized_revision_prompt")
        protected = inventory_forensics_prompts((replace(synthetic_prompt(), field="teacher_grading_code"),))
        self.assertFalse(protected.changes)
        self.assertEqual(protected.issues[0].code, "prompt_field_requires_review")

    def test_duplicate_prompt_occurrence_is_not_edited(self):
        result = inventory_forensics_prompts((synthetic_prompt(), synthetic_prompt()))
        self.assertFalse(result.changes)
        self.assertEqual({issue.code for issue in result.issues}, {"duplicate_prompt_occurrence"})

    def test_missing_prompt_is_staged_and_marker_ids_are_preserved(self):
        occurrence = replace(synthetic_prompt(""), marker_ids=("synthetic-F03-CA1-DRAFT",))
        result = inventory_forensics_prompts((occurrence,))
        self.assertEqual(result.changes[0].old_value, "")
        self.assertEqual(result.artifacts[0]["status"], "missing_prompt_replacement_proposed")
        self.assertEqual(result.artifacts[0]["preserved_marker_ids"], list(occurrence.marker_ids))
        self.assertEqual(result.artifacts[0]["prompt_status"], "staged_draft_not_manual_certified")
        for prompt in (FORENSICS_REVISION_PROMPT, ANATOMY_REVISION_PROMPT):
            self.assertIn("Wait for my reply", prompt)
            self.assertIn("at most one", prompt)

    def test_course_rules_use_shared_configuration(self):
        configs = subject_configs_from_rules({"forensics": {
            "revision_focus": "Challenge certainty", "prompt_version": "synthetic-forensics-v3",
            "lessons_ahead": 2, "prefer_digital": True,
        }})
        self.assertEqual(len(configs), 1)
        self.assertIn("Challenge certainty", configs[0].pedagogical_prompt)
        self.assertEqual(configs[0].prompt_version, "synthetic-forensics-v3")

    def test_synthetic_data_and_nonprivate_prompt_only(self):
        with self.assertRaises(ValueError):
            inventory_forensics_prompts((replace(synthetic_prompt(), deck_id="real-object"),))
        with self.assertRaises(ValueError):
            inventory_forensics_prompts((synthetic_prompt("fixture.person@example.invalid"),))
        with self.assertRaises(ValueError):
            plan_two_lessons_ahead(replace(synthetic_generation_input(), synthetic_marker="live"))

    def test_job_board_is_synthetic_deterministic_and_does_not_write(self):
        first = classroom_job_board_preview(("Materials", "Timekeeper"),
                                             ("synthetic-learner-01", "synthetic-learner-02"), "synthetic-board-v1")
        self.assertEqual(first.artifacts[0]["assignments"][0]["role"], "Materials")
        self.assertFalse(first.changes)
        self.assertEqual(first.external_writes, 0)
        self.assertEqual(first, classroom_job_board_preview(("Materials", "Timekeeper"),
                         ("synthetic-learner-01", "synthetic-learner-02"), "synthetic-board-v1"))
        with self.assertRaises(ValueError):
            classroom_job_board_preview(("Materials",), ("Real Name",), "synthetic-board-v1")


if __name__ == "__main__":
    unittest.main()
