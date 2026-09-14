"""Runnable synthetic demonstration of the shared Hub foundation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from school_workflow_hub.core.actions import create_review_item, propose_action, validate_action
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.reports import render_teacher_summary
from school_workflow_hub.core.runs import create_or_get_run, finish_run
from school_workflow_hub.core.sources import record_source_revision


def run_synthetic_demo(
    database_path: str | Path,
    reports_directory: str | Path,
    agenda_due_date: str = "2026-09-16",
) -> Dict[str, Any]:
    """Plan, never execute, one due-date update and one feedback review.

    Repeated input obtains the same logical run/action records. A changed agenda
    due date creates a new agenda revision and supersedes the earlier proposal.
    """
    connection = initialize_database(database_path)
    try:
        inputs = {"agenda_due_date": agenda_due_date, "fixture": "synthetic-demo-v1"}
        run = create_or_get_run(connection, "synthetic_demo", inputs)

        agenda = record_source_revision(
            connection,
            "agenda",
            "biology-a4:week-01",
            {
                "assignment_stable_id": "biology-a4:energy-intro",
                "intended_due_date": agenda_due_date,
                "week_label": "Week 01",
            },
            "synthetic://agenda/biology-a4/week-01",
            run.id,
        )
        assignment_master = record_source_revision(
            connection,
            "assignment_master",
            "biology-a4:energy-intro",
            {
                "deck_file_id": "synthetic-deck-energy-intro",
                "form_file_id": "synthetic-form-energy-intro",
                "external_assignment_id": "synthetic-ic-energy-intro",
                "observed_ic_due_date": "2026-09-15",
            },
            "synthetic://assignment-master/biology-a4/energy-intro",
            run.id,
        )
        submission = record_source_revision(
            connection,
            "submission",
            "submission:synthetic-001",
            {
                "assignment_stable_id": "biology-a4:energy-intro",
                "learner_stable_id": "synthetic-learner-001",
                "revision_number": 2,
                "content_hash": "synthetic-changed-submission-v2",
                "prompt_version": "synthetic-feedback-v1",
            },
            "synthetic://submissions/synthetic-001/revision-2",
            run.id,
        )
        due_date_action = propose_action(
            connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="synthetic-ic-energy-intro",
            source_stable_id="biology-a4:week-01",
            source_revision_id=agenda.id,
            expected_state={"observed_due_date": "2026-09-15"},
            proposed_state={"intended_due_date": agenda_due_date},
            evidence={
                "agenda_revision_id": agenda.id,
                "assignment_master_revision_id": assignment_master.id,
                "reason": "intended pacing differs from observed IC date",
            },
            required_permission="external.update",
            source_dependencies={"assignment_master": assignment_master.id},
        )
        validate_action(connection, due_date_action.id)
        review_item = create_review_item(
            connection,
            run_id=run.id,
            kind="feedback_revision_ready",
            severity="normal",
            payload={
                "submission_revision_id": submission.id,
                "prompt_version": "synthetic-feedback-v1",
                "model_identifier": "fake/disabled",
                "reason": "changed submission needs teacher-approved feedback workflow",
            },
        )
        finish_run(
            connection,
            run.id,
            details={
                "agenda_revision_id": agenda.id,
                "due_date_action_id": due_date_action.id,
                "review_item_id": review_item,
                "external_writes": 0,
                "live_model_calls": 0,
            },
        )
        report_path = render_teacher_summary(
            connection,
            run.id,
            Path(reports_directory) / f"teacher-summary-{run.id}.md",
        )
        return {
            "run_id": run.id,
            "run_created": run.created,
            "agenda_revision_id": agenda.id,
            "agenda_revision_created": agenda.created,
            "due_date_action_id": due_date_action.id,
            "due_date_action_created": due_date_action.created,
            "review_item_id": review_item,
            "report_path": str(report_path),
            "external_writes": 0,
            "live_model_calls": 0,
        }
    finally:
        connection.close()
