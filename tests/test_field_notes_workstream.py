from __future__ import annotations

from dataclasses import replace
from datetime import date
import unittest

from school_workflow_hub.workflows.field_notes import (
    FakeClock,
    FieldNotesMaintenanceInput,
    GitHubReportSnapshot,
    LifecycleReminderKind,
    plan_field_notes_maintenance,
    run_synthetic_field_notes_maintenance_example,
    synthetic_field_notes_maintenance_example,
)


class FieldNotesMaintenanceWorkstreamContractTests(unittest.TestCase):
    def test_fake_example_keeps_observation_interpretation_reflection_and_task_ids_separate(self) -> None:
        result = run_synthetic_field_notes_maintenance_example()

        self.assertEqual(len(result.journal_append_plans), 1)
        journal_plan = result.journal_append_plans[0]
        self.assertEqual(journal_plan.field_note_stable_id, "field-note:synthetic:001")
        self.assertEqual(journal_plan.class_stable_id, "class:synthetic:biology-a4")
        self.assertEqual(journal_plan.observed_on, date(2026, 11, 15))
        self.assertIn("learners paused", journal_plan.observation)
        self.assertIn("comparison step", journal_plan.interpretation)
        self.assertIn("partner comparison", journal_plan.reflection)
        self.assertEqual(journal_plan.preserved_todoist_task_ids, ("todoist:synthetic:task-001",))
        self.assertEqual(journal_plan.operation, "append_only_local_journal_plan")
        self.assertFalse(journal_plan.drive_write_performed)
        self.assertFalse(journal_plan.todoist_write_performed)

    def test_fake_clock_plans_mid_november_review_only_on_its_target_date(self) -> None:
        result = run_synthetic_field_notes_maintenance_example()
        kinds = tuple(reminder.kind for reminder in result.lifecycle_reminder_plans)

        self.assertEqual(kinds, (LifecycleReminderKind.MID_NOVEMBER_REVIEW,))
        self.assertFalse(result.scheduled_automation_created)

    def test_mid_august_and_friday_meeting_are_bounded_to_their_fake_clock_rules(self) -> None:
        fixture = synthetic_field_notes_maintenance_example()
        friday = date(2026, 8, 14)  # Friday; it is intentionally not the August 15 trigger.
        friday_input = replace(
            fixture,
            fake_clock=FakeClock(friday),
            friday_meeting_confirmed=True,
        )
        friday_result = plan_field_notes_maintenance(friday_input)
        self.assertEqual(
            tuple(item.kind for item in friday_result.lifecycle_reminder_plans),
            (LifecycleReminderKind.FRIDAY_MEETING,),
        )

        august_result = plan_field_notes_maintenance(
            replace(fixture, fake_clock=FakeClock(date(2026, 8, 15)), friday_meeting_confirmed=False)
        )
        self.assertEqual(
            tuple(item.kind for item in august_result.lifecycle_reminder_plans),
            (LifecycleReminderKind.MID_AUGUST_RESTART,),
        )

        non_friday = plan_field_notes_maintenance(
            replace(fixture, fake_clock=FakeClock(date(2026, 8, 13)), friday_meeting_confirmed=True)
        )
        self.assertEqual(non_friday.lifecycle_reminder_plans, ())
        self.assertEqual(non_friday.maintenance_issues[0].code, "friday_meeting_not_on_friday")

    def test_stale_github_item_is_an_archive_proposal_never_a_deletion(self) -> None:
        result = run_synthetic_field_notes_maintenance_example()

        self.assertEqual(len(result.proposed_github_archives), 1)
        archive = result.proposed_github_archives[0]
        self.assertEqual(archive.report_stable_id, "github-report:synthetic:old")
        self.assertEqual(archive.action, "propose_archive_only")
        self.assertEqual(archive.lifecycle_state, "plan_only")
        self.assertFalse(archive.deletion_performed)
        self.assertFalse(archive.github_write_performed)

    def test_fresh_and_future_github_snapshots_are_not_archived(self) -> None:
        fixture = synthetic_field_notes_maintenance_example()
        fresh = GitHubReportSnapshot(
            report_stable_id="github-report:synthetic:fresh",
            repository_stable_id="github-repo:synthetic:hub",
            observed_on=date(2026, 11, 14),
            source_revision_id="github-report-source:synthetic:fresh:v1",
            display_title="Synthetic fresh report",
        )
        future = replace(fresh, report_stable_id="github-report:synthetic:future", observed_on=date(2026, 12, 1))
        result = plan_field_notes_maintenance(
            replace(fixture, github_report_snapshots=(fresh, future), stale_after_days=30)
        )

        self.assertEqual(result.proposed_github_archives, ())

    def test_future_job_metadata_and_external_boundaries_are_visible(self) -> None:
        result = run_synthetic_field_notes_maintenance_example()

        self.assertEqual(len(result.future_job_metadata), 1)
        job = result.future_job_metadata[0]
        self.assertEqual(job.job_stable_id, "job:synthetic:monthly-maintenance")
        self.assertTrue(job.host_requirement)
        self.assertTrue(job.account_scope)
        self.assertEqual(job.permission_requirements, ("external.read",))
        self.assertTrue(job.persistence_requirement)
        self.assertTrue(job.cost_boundary)
        self.assertTrue(job.offline_behavior)
        self.assertEqual(job.execution_state, "not_scheduled")
        self.assertEqual(result.external_calls_made, 0)
        self.assertEqual(result.external_writes_performed, 0)
        self.assertEqual(result.live_model_calls, 0)

    def test_invalid_stale_threshold_is_not_silently_normalized(self) -> None:
        fixture = synthetic_field_notes_maintenance_example()
        with self.assertRaises(ValueError):
            replace(fixture, stale_after_days=0)


if __name__ == "__main__":
    unittest.main()
