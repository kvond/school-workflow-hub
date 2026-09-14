"""Second-wave dependency and idempotency checks across the shared spine."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.pipeline import PreviewStep, RetryablePreviewError, run_preview_steps
from school_workflow_hub.core.workflow import WorkflowPreview, persist_preview, validate_preview
from school_workflow_hub.workflows.curriculum_pipeline import plan_curriculum_pipeline, synthetic_curriculum_pipeline_input
from school_workflow_hub.workflows.feedback_pipeline import EVIDENCE_SOURCE, load_feedback_fixture, plan_feedback_pipeline


class CurriculumCrossReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = initialize_database(Path(temporary.name) / "cross-review.sqlite3")
        self.addCleanup(self.db.close)

    def counts(self):
        return {table: self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("source_revisions", "proposed_actions", "review_items", "runs", "artifacts")}

    def test_curriculum_persists_to_existing_ledger_and_rerun_is_duplicate_free(self):
        preview = plan_curriculum_pipeline(synthetic_curriculum_pipeline_input())
        first = persist_preview(self.db, preview)
        counts = self.counts()
        second = persist_preview(self.db, preview)
        self.assertEqual(first, second)
        self.assertEqual(counts, self.counts())
        states = self.db.execute("SELECT state, review_status FROM proposed_actions").fetchall()
        self.assertTrue(states)
        self.assertTrue(all(row["state"] == "proposed" and row["review_status"] == "pending" for row in states))

    def test_persist_feedback_preview_retains_original_source_payload_and_rerun(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/feedback_pipeline_cases.json").read_text())
        assignments, observations, request = load_feedback_fixture(fixture)
        first = plan_feedback_pipeline(self.db, assignments, observations, request)
        before = [tuple(row) for row in self.db.execute(
            "SELECT id, payload_json, active FROM source_revisions WHERE source_type = ? ORDER BY id", (EVIDENCE_SOURCE,))]
        persist_preview(self.db, first.preview)
        after = [tuple(row) for row in self.db.execute(
            "SELECT id, payload_json, active FROM source_revisions WHERE source_type = ? ORDER BY id", (EVIDENCE_SOURCE,))]
        self.assertEqual(before, after, "shared preview persistence must not replace the source payload with its evidence pointer")
        second = plan_feedback_pipeline(self.db, assignments, observations, request)
        self.assertEqual(first.records, second.records)
        self.assertFalse(second.preview.changes)
        self.assertNotIn("stored_metadata_unreadable", {issue.code for issue in second.preview.issues})

    def test_feedback_as_of_never_uses_future_stored_submission(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/feedback_pipeline_cases.json").read_text())
        assignments, observations, request = load_feedback_fixture(fixture)
        plan_feedback_pipeline(self.db, assignments, observations, request)
        earlier = replace(request, as_of=request.as_of - timedelta(days=30))
        result = plan_feedback_pipeline(self.db, assignments, (), earlier)
        self.assertEqual(result.records, (), "a historical as-of request must not receive a later stored submission")

    def test_conflicting_source_revisions_in_one_preview_fail_before_persistence(self):
        first, second = plan_curriculum_pipeline(synthetic_curriculum_pipeline_input()).changes[:2]
        altered = replace(second, evidence=(replace(second.evidence[0], revision="synthetic-conflicting-revision"),) + second.evidence[1:])
        with self.assertRaises(ValueError):
            validate_preview(WorkflowPreview("synthetic-conflicting-evidence", (first, altered)))

    def test_completed_child_receipt_requires_completed_child_readback(self):
        preview = plan_curriculum_pipeline(synthetic_curriculum_pipeline_input())
        steps = (PreviewStep("curriculum", lambda: preview),)
        first = run_preview_steps(self.db, workflow="synthetic-receipt-test", inputs={"revision": "synthetic-v1"}, steps=steps)
        child = first["steps"]["curriculum"]["preview_run_ids"][0]
        self.db.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (child,))
        second = run_preview_steps(self.db, workflow="synthetic-receipt-test", inputs={"revision": "synthetic-v1"}, steps=steps)
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["steps"]["curriculum"]["code"], "receipt_readback_failed")

    def test_completed_receipt_rechecks_source_dependent_action_state(self):
        preview = plan_curriculum_pipeline(synthetic_curriculum_pipeline_input())
        steps = (PreviewStep("curriculum", lambda: preview),)
        first = run_preview_steps(self.db, workflow="synthetic-action-receipt", inputs={"revision": "synthetic-v1"}, steps=steps)
        self.assertEqual(first["status"], "completed")
        self.db.execute("UPDATE proposed_actions SET state = 'superseded' WHERE id = (SELECT id FROM proposed_actions LIMIT 1)")
        second = run_preview_steps(self.db, workflow="synthetic-action-receipt", inputs={"revision": "synthetic-v1"}, steps=steps)
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["steps"]["curriculum"]["code"], "receipt_readback_failed")

    def test_dependency_failure_resumes_without_reapplying_completed_steps(self):
        calls = {"agenda": 0, "lesson": 0}

        def agenda():
            calls["agenda"] += 1
            return plan_curriculum_pipeline(synthetic_curriculum_pipeline_input())

        def lesson():
            calls["lesson"] += 1
            if calls["lesson"] == 1:
                raise RetryablePreviewError("synthetic transient failure")
            return WorkflowPreview("synthetic-lesson-complete")

        steps = (PreviewStep("agenda", agenda), PreviewStep("lesson", lesson, ("agenda",)))
        first = run_preview_steps(self.db, workflow="synthetic-resume", inputs={"revision": "synthetic-v1"}, steps=steps)
        self.assertEqual(first["status"], "blocked")
        second = run_preview_steps(self.db, workflow="synthetic-resume", inputs={"revision": "synthetic-v1"}, steps=steps)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(calls, {"agenda": 1, "lesson": 2})
        counts = self.counts()
        self.assertEqual(second, run_preview_steps(self.db, workflow="synthetic-resume", inputs={"revision": "synthetic-v1"}, steps=steps))
        self.assertEqual(counts, self.counts())


if __name__ == "__main__":
    unittest.main()
