"""Cross-workflow regression checks for teaching, not research or live writes."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.models import EnrollmentRecord
from school_workflow_hub.core.workflow import persist_preview
from school_workflow_hub.workflows.feedback_pipeline import plan_feedback_pipeline
from school_workflow_hub.workflows.reporting_pipeline import plan_reporting_pipeline, render_report_packet
from school_workflow_hub.workflows.roster_reconciliation import RosterEntry, RosterSnapshot, plan_roster_reconciliation
from school_workflow_hub.workflows.schoology_gradebook import DesiredPlatformAssignment, ObservedPlatformAssignment, reconcile_platform_assignments
from tests.test_feedback_pipeline import fixture


class TeachingContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = initialize_database(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.assignments, self.observations, self.request = fixture()

    def feedback(self, observations, assignments=None):
        return plan_feedback_pipeline(self.db, assignments or self.assignments,
                                      observations, self.request)

    def test_roster_withdrawal_preview_preserves_existing_feedback_and_history(self):
        before = self.feedback(self.observations)
        history = [tuple(row) for row in self.db.execute(
            "SELECT * FROM source_revisions WHERE source_type = 'synthetic_feedback_evidence' ORDER BY id")]
        cls = self.request.class_stable_id
        def snap(learners, revision):
            return RosterSnapshot(cls, "roster-revision:synthetic:" + revision,
                self.request.as_of, True, len(learners), tuple(
                    RosterEntry(EnrollmentRecord(cls, "learner:synthetic:" + learner))
                    for learner in learners))
        roster = plan_roster_reconciliation(snap(("p001", "p003", "p004"), "current"),
                  snap(("p001", "p002", "p003"), "grading"), observed_at=self.request.as_of)
        first = persist_preview(self.db, roster)
        self.assertEqual(first, persist_preview(self.db, roster))
        self.assertEqual(history, [tuple(row) for row in self.db.execute(
            "SELECT * FROM source_revisions WHERE source_type = 'synthetic_feedback_evidence' ORDER BY id")])
        self.assertEqual(before.records, self.feedback(()).records)
        self.assertEqual({c.field for c in roster.changes}, {"enrollment_status"})
        self.assertEqual(roster.external_writes, 0)

    def test_agenda_due_change_reaches_both_platforms_and_feedback_without_erasing_history(self):
        old = self.feedback((self.observations[1],))
        revised = replace(self.assignments[0], intended_due_date="2026-09-18")
        mapped = replace(revised, external_ids={"schoology": "synthetic:page", "infinite_campus": "synthetic:ic"})
        desired = DesiredPlatformAssignment(mapped, "week-03", "mp-01",
            "https://example.invalid/slides/reasoning/copy", "synthetic:folder",
            "draft", "synthetic:ic", "synthetic:agenda-new")
        observations = tuple(ObservedPlatformAssignment(mapped.stable_id, mapped.class_stable_id,
            system, target, "synthetic:observed-old", {"due_date": "2026-09-10"})
            for system, target in mapped.external_ids.items())
        preview = reconcile_platform_assignments((desired,), observations,
            fields_by_system={"schoology": ("due_date",), "infinite_campus": ("due_date",)},
            require_observed_fields=True)
        self.assertEqual({(c.target_system, c.proposed_value) for c in preview.changes},
                         {("schoology", "2026-09-18"), ("infinite_campus", "2026-09-18")})
        current = self.feedback((), (revised,) + self.assignments[1:])
        self.assertTrue(old.records[0].late)
        self.assertFalse(current.records[0].late)
        self.assertEqual(old.records[0].source_revision_id, current.records[0].source_revision_id)
        self.assertNotEqual(old.grade_proposals[0].stable_id, current.grade_proposals[0].stable_id)
        reports = plan_reporting_pipeline(current)
        self.assertEqual(reports.morning_summary.learner_reports[0].late_count, 0)
        self.assertEqual(preview.external_writes, 0)

    def test_held_evidence_is_visible_as_unfinished_not_a_reported_credit_drop(self):
        original = self.observations[1]
        self.feedback((original,))
        partial = replace(original, evidence_status="partial",
            completion=replace(original.completion, notes_completed=1, notes_completed_later=0),
            submission=replace(original.submission, revision_hash="synthetic-revision:partial-report",
                               observed_at="2026-09-12T12:00:00-04:00"))
        held = self.feedback((partial,))
        reports = plan_reporting_pipeline(held)
        self.assertEqual(held.grade_proposals, ())
        self.assertEqual(reports.biweekly_report.evidence_changes, ())
        rendered = render_report_packet(reports.preview)
        self.assertIn("Grading is unfinished", rendered)
        self.assertIn("incomplete_completion_evidence", rendered)
        self.assertNotIn("unrecognized_issue_code", rendered)
        self.assertNotIn("NOTES -1", rendered)
        self.assertEqual(len(held.history), 2)

    def test_independent_first_answer_credit_works_for_all_three_courses(self):
        for course in ("biology", "anatomy", "forensics"):
            with self.subTest(course=course):
                cls = "class:synthetic:" + course
                assignments = tuple(replace(a, stable_id=a.stable_id + "-" + course,
                    class_stable_id=cls) for a in self.assignments)
                obs = self.observations[1]
                obs = replace(obs, class_stable_id=cls,
                    completion=replace(obs.completion, first_answers_completed=2, first_answers_completed_later=2),
                    submission=replace(obs.submission, stable_id=obs.submission.stable_id + "-" + course,
                        assignment_stable_id=assignments[0].stable_id))
                result = plan_feedback_pipeline(self.db, assignments, (obs,), replace(self.request, class_stable_id=cls))
                self.assertEqual(len(result.grade_proposals), 1)
                self.assertIn('"later_first_answers_credited":2', result.grade_proposals[0].proposed_value)
                self.assertEqual(result.external_writes, 0)
