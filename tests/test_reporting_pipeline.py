from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.workflow import persist_preview, validate_preview
from school_workflow_hub.workflows.feedback_pipeline import plan_feedback_pipeline
from school_workflow_hub.workflows.reporting_pipeline import plan_reporting_pipeline, render_report_packet
from tests.test_feedback_pipeline import fixture


class ReportingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = initialize_database(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.assignments, self.observations, self.request = fixture()

    def test_reports_share_latest_revision_and_distinct_status_counts(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        result = plan_reporting_pipeline(source)
        self.assertEqual(len(result.folder_plans), 5)
        report = next(item for item in result.morning_summary.learner_reports if item.submitted_count == 3)
        self.assertEqual(report.notes_completed, 6)
        self.assertEqual(report.drafts_completed, 6)
        self.assertEqual(report.paired_revision_evidence, 5)
        self.assertEqual(report.late_count, 1)
        self.assertEqual(sum(item.missing_count for item in result.friday_report.learner_reports), 1)
        self.assertEqual(sum(item.absent_count for item in result.friday_report.learner_reports), 1)
        self.assertEqual(result.friday_report.delivery_state, "not_delivered")
        self.assertEqual(result.external_writes, 0)
        validate_preview(result.preview)

    def test_14_day_report_includes_item_outside_friday_window(self):
        item = self.observations[0]
        item = replace(item, submission=replace(item.submission, observed_at=(self.request.as_of - timedelta(days=10)).isoformat()))
        source = plan_feedback_pipeline(self.db, self.assignments, (item,), self.request)
        result = plan_reporting_pipeline(source)
        self.assertEqual(result.friday_report.learner_reports, ())
        self.assertEqual(len(result.biweekly_report.learner_reports), 1)

    def test_report_persistence_is_idempotent(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        preview = plan_reporting_pipeline(source).preview
        first = persist_preview(self.db, preview)
        second = persist_preview(self.db, preview)
        self.assertEqual(first, second)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], len(preview.artifacts))

    def test_reports_show_evidence_bound_prompts_and_revision_comparison(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        result = plan_reporting_pipeline(source, friday_class_ids=(self.request.class_stable_id,))
        conceptual = next(item for item in result.morning_summary.learner_reports if item.submitted_count == 3)
        self.assertEqual(conceptual.conceptual_signals, ("evidence_boundary",))
        self.assertIn("strongest reason", conceptual.conversation_prompts[0])
        self.assertTrue(conceptual.notable_connections)
        missing = next(item for item in result.morning_summary.learner_reports if item.missing_count)
        self.assertEqual(missing.conversation_prompts, ())
        self.assertEqual(missing.evidence_status, "conceptual_evidence_unavailable")
        changes = result.biweekly_report.evidence_changes
        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0].notes_change, changes[0].bank_change, changes[0].draft_change), (1, 2, 2))
        self.assertIn("growth_not_inferred", changes[0].interpretation)
        self.assertEqual(result.friday_meeting_date, "2026-09-18")
        self.assertEqual(result.friday_meeting_status, "configured")
        self.assertEqual(result.friday_meeting_class_ids, (self.request.class_stable_id,))

    def test_unconfigured_friday_calendar_is_visible(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        result = plan_reporting_pipeline(source)
        self.assertEqual(result.friday_meeting_status, "unverified")
        self.assertIn("friday_class_meetings_unverified", result.exception_codes)

    def test_markdown_packet_is_deterministic_and_prints_no_private_prose(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        preview = plan_reporting_pipeline(source).preview
        packet = render_report_packet(preview)
        self.assertEqual(packet, render_report_packet(preview))
        self.assertEqual(packet, render_report_packet(replace(preview, artifacts=tuple(reversed(preview.artifacts)))))
        for title in ("Morning summary", "Friday report", "Biweekly report", "Exceptions"):
            self.assertIn(title, packet)
        self.assertIn("Delivery is disabled", packet)
        self.assertIn("Draft interpretation", packet)
        self.assertIn("learning growth is not inferred", packet)
        self.assertIn(source.records[0].source_revision_id, packet)
        self.assertIn("synthetic-revision:reasoning-v1", packet)
        self.assertIn("synthetic-revision:reasoning-v2", packet)
        self.assertNotIn("learner:synthetic:p001`", packet)
        artifacts = list(preview.artifacts)
        first = dict(artifacts[0])
        learners = list(first["learner_reports"])
        learners[0] = dict(learners[0], conversation_prompts=("arbitrary personal prose withheld",), notable_connections=("arbitrary personal prose withheld",))
        first["learner_reports"] = learners
        artifacts[0] = first
        modified = render_report_packet(replace(preview, artifacts=tuple(artifacts)))
        self.assertNotIn("arbitrary personal prose withheld", modified)
        self.assertEqual(packet, modified)

    def test_markdown_packet_rejects_unsafe_identity_without_echo(self):
        source = plan_feedback_pipeline(self.db, self.assignments, self.observations, self.request)
        preview = plan_reporting_pipeline(source).preview
        artifacts = list(preview.artifacts)
        first = dict(artifacts[0])
        learners = list(first["learner_reports"])
        learners[0] = dict(learners[0], learner_pseudonym="arbitrary learner identity withheld")
        first["learner_reports"] = learners
        artifacts[0] = first
        with self.assertRaisesRegex(ValueError, "^invalid_synthetic_report_packet$"):
            render_report_packet(replace(preview, artifacts=tuple(artifacts)))
        with self.assertRaisesRegex(ValueError, "^invalid_synthetic_report_packet$"):
            render_report_packet(replace(preview, workflow="another_workflow"))


if __name__ == "__main__":
    unittest.main()
