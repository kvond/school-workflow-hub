from __future__ import annotations

import unittest

from school_workflow_hub.workflows.curriculum import (
    BiologyActivitySupport,
    Course,
    CurriculumLessonInput,
    FakeCurriculumPlanner,
    ForensicsActivityPlan,
    LessonPhase,
    OriginalMaterialRights,
    PEDAGOGICAL_ARCHITECTURE,
    plan_synthetic_lesson,
    synthetic_biology_example,
)


class CurriculumWorkstreamTestCase(unittest.TestCase):
    def test_fake_biology_plan_preserves_stable_ids_and_architecture(self) -> None:
        first = synthetic_biology_example()
        second = synthetic_biology_example()

        self.assertEqual(first.status, "planned_synthetic")
        self.assertIsNotNone(first.plan)
        assert first.plan is not None
        assert second.plan is not None
        self.assertEqual(first.plan.stable_id, second.plan.stable_id)
        self.assertEqual(first.plan.lesson_stable_id, "biology:lesson:energy-intro")
        self.assertEqual(
            tuple(phase.phase for phase in first.plan.phases), PEDAGOGICAL_ARCHITECTURE
        )
        self.assertEqual(first.plan.biology_activity.pause_after_phase, LessonPhase.EXPLORE_PLAY)
        self.assertTrue(first.plan.biology_activity.setup_notes)

    def test_rights_field_is_preserved_without_copying_material(self) -> None:
        result = synthetic_biology_example()
        assert result.plan is not None

        rights = result.plan.original_materials_rights
        self.assertEqual(rights.original_material_reference, "synthetic://curriculum/biology/energy-intro")
        self.assertIn("no source material copied", rights.rights_respect)
        self.assertIn("do not copy", rights.reuse_boundary)

    def test_forensics_fake_output_is_a_roughly_thirty_minute_local_plan(self) -> None:
        request = CurriculumLessonInput(
            course=Course.FORENSICS,
            curriculum_version_stable_id="curriculum:forensics:synthetic-v1",
            lesson_stable_id="forensics:lesson:evidence-logic",
            source_revision_id="source:synthetic:forensics:evidence-logic:v1",
            display_title="Synthetic evidence reasoning",
            focal_question="Which claim is best supported by the evidence?",
            original_materials_rights=OriginalMaterialRights(
                original_material_reference="synthetic://curriculum/forensics/evidence-logic",
                rights_respect="synthetic fixture; no source material copied",
            ),
            forensics_activity=ForensicsActivityPlan(
                relevant_reading="Read a short synthetic case summary.",
                evidence_task="Sort the stated observations from interpretations.",
                logic_task="Test whether the conclusion is too certain for the evidence.",
                hands_on_task="Arrange synthetic evidence cards into a defensible claim chain.",
            ),
        )

        result = plan_synthetic_lesson(request, FakeCurriculumPlanner())
        self.assertEqual(result.status, "planned_synthetic")
        assert result.plan is not None
        self.assertEqual(result.plan.forensics_activity.estimated_minutes, 30)
        self.assertIn("conclusion", result.plan.forensics_activity.logic_task)

    def test_no_external_execution_or_model_calls_are_available(self) -> None:
        result = synthetic_biology_example()

        self.assertFalse(result.external_execution_attempted)
        self.assertEqual(result.live_model_calls, 0)
        self.assertEqual(result.plan.course, Course.BIOLOGY)

    def test_anatomy_is_explicitly_not_regenerated(self) -> None:
        request = CurriculumLessonInput(
            course=Course.ANATOMY,
            curriculum_version_stable_id="curriculum:anatomy:synthetic-v1",
            lesson_stable_id="anatomy:lesson:kept-source",
            source_revision_id="source:synthetic:anatomy:kept-source:v1",
            display_title="Existing source stays untouched",
            focal_question="How does the existing material support the lesson?",
            original_materials_rights=OriginalMaterialRights(
                original_material_reference="synthetic://curriculum/anatomy/kept-source",
                rights_respect="synthetic fixture; no source material copied",
            ),
        )

        result = plan_synthetic_lesson(request)
        self.assertEqual(result.status, "not_supported")
        self.assertIsNone(result.plan)
        self.assertFalse(result.external_execution_attempted)

    def test_forensics_activity_requires_all_four_parts(self) -> None:
        with self.assertRaises(ValueError):
            ForensicsActivityPlan(
                relevant_reading="Read this.",
                evidence_task="Use evidence.",
                logic_task="Use logic.",
                hands_on_task="",
            )
