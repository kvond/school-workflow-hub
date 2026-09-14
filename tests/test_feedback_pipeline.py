from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.workflow import validate_preview
from school_workflow_hub.workflows.feedback_pipeline import (
    CompletionEvidence, EVIDENCE_SOURCE, load_feedback_fixture,
    plan_feedback_pipeline, pseudonymous_learner_id,
)


def fixture():
    return load_feedback_fixture(json.loads((Path(__file__).parent / "fixtures/feedback_pipeline_cases.json").read_text()))


class FeedbackPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = initialize_database(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.assignments, self.observations, self.request = fixture()

    def run_pipeline(self, observations=None, request=None, assignments=None):
        return plan_feedback_pipeline(self.db, self.assignments if assignments is None else assignments, self.observations if observations is None else observations, self.request if request is None else request)

    def counts(self):
        return {table: self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("source_revisions", "proposed_actions", "review_items", "runs")}

    def test_complete_repeat_run_has_no_duplicates_and_reuses_student_work(self):
        first = self.run_pipeline()
        counts = self.counts()
        second = self.run_pipeline()
        self.assertEqual(counts, self.counts())
        self.assertEqual(first.records, second.records)
        self.assertEqual(len(first.records), 5)
        self.assertEqual(second.preview.changes, ())
        self.assertFalse(second.student_work_plan.run_created)
        self.assertEqual(len(first.student_work_plan.grade_proposals), 5)
        self.assertEqual(first.external_writes, 0)
        self.assertEqual(first.live_model_calls, 0)
        validate_preview(first.preview)

    def test_later_completion_is_credited_separately_without_invented_growth(self):
        result = self.run_pipeline()
        record = next(item for item in result.records if item.submission.stable_id.endswith("reasoning-p001"))
        value = json.loads(record.grade_proposal.proposed_value)
        self.assertEqual(value["completion"]["notes"], [2, 2])
        self.assertEqual(value["completion"]["later_notes_credited"], 1)
        self.assertEqual(value["completion"]["later_bank_credited"], 2)
        self.assertEqual(value["revision"]["draft"], [2, 2])
        self.assertEqual(value["revision"]["paired_evidence_count"], 1)
        self.assertEqual(value["revision"]["growth_claim"], "not_inferred")
        self.assertEqual(value["optional"], "unscored")
        self.assertTrue(record.late)
        self.assertIn("strongest reason", record.feedback_draft)
        for item in result.records:
            if item.work_status in ("missing", "absent"):
                self.assertIsNone(item.grade_proposal.proposed_value)
                self.assertFalse(item.late)
                self.assertEqual(item.conceptual_signal, "none")

    def test_resubmission_preserves_history_and_stale_replay_cannot_reverse_it(self):
        original, revised = self.observations[:2]
        first = self.run_pipeline((original,))
        second = self.run_pipeline((revised,))
        self.assertNotEqual(first.grade_proposals[0].stable_id, second.grade_proposals[0].stable_id)
        rows = self.db.execute("SELECT active FROM source_revisions WHERE source_type = ?", (EVIDENCE_SOURCE,)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row[0] for row in rows), 1)
        third = self.run_pipeline((original,))
        self.assertEqual(second.records, third.records)
        self.assertEqual(third.preview.changes, ())

    def test_14_day_window_and_retained_backlog(self):
        original = self.observations[0]
        out = replace(original, submission=replace(original.submission, observed_at=(self.request.as_of - timedelta(days=15)).isoformat()))
        future = replace(original, submission=replace(original.submission, observed_at=(self.request.as_of + timedelta(days=1)).isoformat()))
        self.assertEqual(self.run_pipeline((out, future)).records, ())
        first = self.run_pipeline((original,))
        later = self.run_pipeline((), replace(self.request, as_of=self.request.as_of + timedelta(days=30)))
        self.assertEqual(first.records, later.records)

    def test_malformed_wrong_course_and_rubric_mismatch_are_code_only(self):
        original = self.observations[0]
        bad = (
            replace(original, source_reference="protected input withheld"),
            replace(original, class_stable_id="class:synthetic:anatomy-b3"),
            replace(original, rubric_version="synthetic-rubric-v2"),
            replace(original, completion=replace(original.completion, notes_completed=1001)),
            replace(original, contains_sensitive_content=True),
        )
        result = self.run_pipeline(bad)
        self.assertEqual(result.records, ())
        self.assertEqual({issue.code for issue in result.preview.issues}, {"invalid_submission_metadata", "wrong_course_submission", "rubric_mismatch"})
        dump = "\n".join(self.db.iterdump())
        self.assertNotIn("protected input withheld", dump)
        validate_preview(result.preview)

    def test_ambiguous_revision_and_identity_conflict_fail_closed(self):
        original = self.observations[0]
        ambiguous = replace(original, completion=replace(original.completion, notes_completed=2))
        result = self.run_pipeline((original, ambiguous))
        self.assertEqual(result.records, ())
        self.assertIn("ambiguous_submission_revision", {issue.code for issue in result.preview.issues})
        wrong_identity = replace(self.observations[1], submission=replace(self.observations[1].submission, learner_stable_id="learner:synthetic:p999"))
        result = self.run_pipeline((original, wrong_identity))
        self.assertEqual(result.records, ())

    def test_invalid_missing_completion_not_counted_as_conceptual(self):
        missing = replace(self.observations[-1], conceptual_signal="evidence_boundary")
        result = self.run_pipeline((missing,))
        self.assertEqual(result.records, ())
        self.assertEqual(result.preview.issues[0].code, "invalid_submission_metadata")

    def test_duplicate_logical_submission_is_quarantined(self):
        original = self.observations[0]
        duplicate = replace(original, submission=replace(original.submission, stable_id="submission:synthetic:duplicate"))
        result = self.run_pipeline((original, duplicate))
        self.assertEqual(result.records, ())
        self.assertEqual(result.preview.issues[0].code, "duplicate_assignment_submission")

    def test_fixture_structure_error_withholds_arbitrary_input(self):
        with self.assertRaisesRegex(ValueError, "^invalid_feedback_fixture_structure$"):
            load_feedback_fixture({"request": {"arbitrary caller key withheld": "fixture"}})

    def test_partial_failure_replay_resumes_without_duplicate_review_records(self):
        from school_workflow_hub.workflows import feedback_pipeline
        actual = feedback_pipeline.propose_action
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic_interruption")
            return actual(*args, **kwargs)
        with patch.object(feedback_pipeline, "propose_action", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        resumed = self.run_pipeline()
        self.assertEqual(len(resumed.records), 5)
        counts = self.counts()
        self.run_pipeline()
        self.assertEqual(counts, self.counts())

    def test_pseudonym_is_stable_and_not_original_identifier(self):
        value = "learner:synthetic:p001"
        self.assertEqual(pseudonymous_learner_id(value), pseudonymous_learner_id(value))
        self.assertNotEqual(pseudonymous_learner_id(value), value)
        with self.assertRaises(ValueError):
            pseudonymous_learner_id("arbitrary input")


if __name__ == "__main__":
    unittest.main()
