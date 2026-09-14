from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.workflow import persist_preview, validate_preview
from school_workflow_hub.workflows.feedback_pipeline import plan_feedback_pipeline
from school_workflow_hub.workflows.research_pipeline import CategoryVersion, SyntheticFieldNote, plan_research_pipeline
from tests.test_feedback_pipeline import fixture


class ResearchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = initialize_database(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.assignments, self.observations, self.request = fixture()
        self.categories = (CategoryVersion("category:synthetic:evidence", 1, "evidence_boundary"),)

    def source(self, observations=None, request=None):
        return plan_feedback_pipeline(self.db, self.assignments, self.observations if observations is None else observations, self.request if request is None else request)

    def test_threshold_requires_three_distinct_assignments(self):
        result = plan_research_pipeline(self.source(), self.categories)
        self.assertEqual(len(result.categories), 1)
        self.assertEqual(len(result.categories[0].assignment_keys), 3)
        self.assertEqual(result.categories[0].status, "eligible_for_teacher_review")
        self.assertEqual(result.categories[0].threshold, 3)
        validate_preview(result.preview)

    def test_many_revisions_of_one_assignment_cannot_meet_threshold(self):
        source = self.source(self.observations[:2])
        source = replace(source, records=source.records * 3)
        result = plan_research_pipeline(source, self.categories)
        self.assertEqual(result.categories[0].status, "insufficient_distinct_assignments")
        self.assertEqual(len(result.categories[0].assignment_keys), 1)

    def test_threshold_config_can_strengthen_but_not_weaken_minimum(self):
        source = self.source()
        result = plan_research_pipeline(source, self.categories, research_assignment_threshold=4)
        self.assertEqual(result.categories[0].status, "insufficient_distinct_assignments")
        self.assertEqual(result.categories[0].threshold, 4)
        with self.assertRaises(ValueError):
            plan_research_pipeline(source, self.categories, research_assignment_threshold=2)

    def test_category_history_is_versioned_and_missing_parent_is_rejected(self):
        next_version = CategoryVersion("category:synthetic:evidence", 2, "evidence_boundary", 1)
        source = self.source()
        result = plan_research_pipeline(source, self.categories + (next_version,))
        self.assertEqual(len(result.category_history), 2)
        self.assertEqual(result.categories[0].category_version, 2)
        with self.assertRaises(ValueError):
            plan_research_pipeline(source, (next_version,))

    def test_retention_is_configurable_preview_without_deletion(self):
        self.source()
        later = self.source((), replace(self.request, as_of=self.request.as_of + timedelta(days=31), retention_days=14))
        count = self.db.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0]
        result = plan_research_pipeline(later, self.categories)
        self.assertTrue(all(item.eligible_now for item in result.retention_plans))
        self.assertTrue(all(not item.deletion_performed for item in result.retention_plans))
        self.assertEqual(count, self.db.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0])

    def test_dated_field_note_is_append_only_deduplicated_plan(self):
        note = SyntheticFieldNote("field-note:synthetic:001", self.request.class_stable_id, self.request.as_of.date(), "pause_before_comparison", "transition_may_need_support", "try_partner_comparison")
        result = plan_research_pipeline(self.source(), self.categories, (note, note))
        self.assertEqual(len(result.field_note_plans), 1)
        self.assertEqual(result.field_note_plans[0].operation, "append_only_local_journal_plan")
        self.assertEqual(result.field_note_plans[0].observed_on, note.observed_on)
        self.assertFalse(result.field_note_plans[0].drive_write_performed)
        validate_preview(result.preview)
        self.assertEqual(persist_preview(self.db, result.preview), persist_preview(self.db, result.preview))

    def test_raw_note_values_are_withheld_and_wrong_course_is_reconciled(self):
        note = SyntheticFieldNote("field-note:synthetic:001", self.request.class_stable_id, self.request.as_of.date(), "private text withheld", "transition_may_need_support", "try_partner_comparison")
        result = plan_research_pipeline(self.source(), self.categories, (note,))
        self.assertEqual(result.field_note_plans, ())
        self.assertNotIn("private text withheld", repr(result))
        self.assertEqual(result.preview.issues[0].code, "invalid_synthetic_field_note")


if __name__ == "__main__":
    unittest.main()
