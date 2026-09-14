from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from school_workflow_hub.adapters.contracts import LocalSyntheticAdapter
from school_workflow_hub.adapters.fakes import FakeStudentWorkAdapter
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.models import AdapterResult, SubmissionRevision
from school_workflow_hub.core.sources import record_source_revision
from school_workflow_hub.workflows.student_work import (
    STUDENT_WORK_SOURCE_TYPE,
    StudentWorkWorkflowError,
    StudentWorkWorkflowRequest,
    make_synthetic_student_work_example,
    plan_student_work,
)


class _UnavailableStudentWorkAdapter(LocalSyntheticAdapter):
    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        return AdapterResult("not_configured", reason="no real student-work adapter is configured")


class _MetadataStudentWorkAdapter(LocalSyntheticAdapter):
    def __init__(self, revisions):
        self.revisions = revisions
        self.calls = []

    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        self.calls.append((class_stable_id, since))
        return AdapterResult("ok", data={"revisions": self.revisions})


class _ExternalStudentWorkAdapter:
    def __init__(self) -> None:
        self.called = False

    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        self.called = True
        return AdapterResult("ok", data={"revisions": []})


def _revision(
    stable_id: str,
    learner_id: str,
    observed_at: str,
    revision_hash: str = "hash-v1",
) -> SubmissionRevision:
    return SubmissionRevision(
        stable_id=stable_id,
        assignment_stable_id="biology-a4:energy-intro",
        learner_stable_id=learner_id,
        revision_hash=revision_hash,
        observed_at=observed_at,
    )


class StudentWorkWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.connection = initialize_database(Path(self.tempdir.name) / "state.sqlite3")
        self.addCleanup(self.connection.close)
        self.as_of = datetime.fromisoformat("2026-09-13T12:00:00-04:00")

    def request(self, **overrides):
        values = {
            "class_stable_id": "biology-a4",
            "as_of": self.as_of,
            "feedback_prompt_version": "feedback-v3",
            "rubric_version": "rubric-v2",
            "model_identifier": "fake/disabled",
        }
        values.update(overrides)
        return StudentWorkWorkflowRequest(**values)

    def test_fake_example_produces_a_local_plan_only(self) -> None:
        reader, request = make_synthetic_student_work_example()
        result = plan_student_work(self.connection, reader, request)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.discoveries), 1)
        self.assertEqual(result.external_writes, 0)
        self.assertEqual(result.live_model_calls, 0)
        self.assertEqual(result.feedback_requests[0].model_identifier, "fake/disabled")
        proposal = result.grade_proposals[0]
        self.assertIsNone(proposal.proposed_value)
        self.assertEqual(proposal.status, "proposed")

    def test_rolling_window_keeps_a_previously_recorded_backlog_item(self) -> None:
        retained = _revision(
            "submission:retained", "learner:retained", "2026-08-01T12:00:00-04:00"
        )
        record_source_revision(
            self.connection,
            STUDENT_WORK_SOURCE_TYPE,
            retained.stable_id,
            {
                "class_stable_id": "biology-a4",
                "submission_revision": {
                    "stable_id": retained.stable_id,
                    "assignment_stable_id": retained.assignment_stable_id,
                    "learner_stable_id": retained.learner_stable_id,
                    "revision_hash": retained.revision_hash,
                    "observed_at": retained.observed_at,
                },
                "work_status": "submitted",
                "conceptual_difficulty": None,
                "original_response_reference": "synthetic://responses/retained",
            },
            "synthetic://submissions/retained",
        )
        in_window = _revision(
            "submission:new", "learner:new", "2026-09-10T12:00:00-04:00"
        )
        out_of_window = _revision(
            "submission:too-old", "learner:old", "2026-08-15T12:00:00-04:00"
        )
        result = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((in_window, out_of_window)),
            self.request(),
        )
        self.assertEqual(len(result.discoveries), 2)
        by_submission = {item.submission.stable_id: item for item in result.discoveries}
        self.assertTrue(by_submission["submission:retained"].retained_from_backlog)
        self.assertFalse(by_submission["submission:new"].retained_from_backlog)
        stored_ids = {
            row["stable_object_id"]
            for row in self.connection.execute(
                "SELECT stable_object_id FROM source_revisions WHERE source_type = ?",
                (STUDENT_WORK_SOURCE_TYPE,),
            )
        }
        self.assertNotIn("submission:too-old", stored_ids)

    def test_retained_backlog_is_scoped_to_the_requested_class(self) -> None:
        anatomy = _revision(
            "submission:anatomy", "learner:anatomy", "2026-08-01T12:00:00-04:00"
        )
        record_source_revision(
            self.connection,
            STUDENT_WORK_SOURCE_TYPE,
            anatomy.stable_id,
            {
                "class_stable_id": "anatomy-c1",
                "submission_revision": {
                    "stable_id": anatomy.stable_id,
                    "assignment_stable_id": anatomy.assignment_stable_id,
                    "learner_stable_id": anatomy.learner_stable_id,
                    "revision_hash": anatomy.revision_hash,
                    "observed_at": anatomy.observed_at,
                },
                "work_status": "submitted",
                "conceptual_difficulty": None,
                "original_response_reference": None,
            },
            "synthetic://submissions/anatomy",
        )
        result = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter(()),
            self.request(),
        )
        self.assertEqual(result.discoveries, ())
        actions = self.connection.execute(
            "SELECT COUNT(*) AS total FROM proposed_actions WHERE action_type = 'grade_proposal_review'"
        ).fetchone()
        self.assertEqual(actions["total"], 0)

    def test_missing_absent_and_conceptual_signals_get_distinct_review_kinds(self) -> None:
        reader = _MetadataStudentWorkAdapter(
            [
                {
                    "stable_id": "submission:missing",
                    "assignment_stable_id": "biology-a4:energy-intro",
                    "learner_stable_id": "learner:missing",
                    "revision_hash": "missing-v1",
                    "observed_at": "2026-09-12T12:00:00-04:00",
                    "work_status": "missing",
                },
                {
                    "stable_id": "submission:absent",
                    "assignment_stable_id": "biology-a4:energy-intro",
                    "learner_stable_id": "learner:absent",
                    "revision_hash": "absent-v1",
                    "observed_at": "2026-09-12T12:01:00-04:00",
                    "work_status": "absent",
                },
                {
                    "stable_id": "submission:concept",
                    "assignment_stable_id": "biology-a4:energy-intro",
                    "learner_stable_id": "learner:concept",
                    "revision_hash": "concept-v1",
                    "observed_at": "2026-09-12T12:02:00-04:00",
                    "work_status": "submitted",
                    "conceptual_difficulty": "Evidence does not yet distinguish correlation from cause.",
                },
            ]
        )
        result = plan_student_work(self.connection, reader, self.request())
        self.assertEqual(len(result.discoveries), 3)
        kinds = {
            row["kind"]
            for row in self.connection.execute("SELECT kind FROM review_items").fetchall()
        }
        self.assertEqual(
            kinds,
            {"missing_work", "absent_work", "conceptual_difficulty_review"},
        )
        statuses = {item.work_status for item in result.discoveries}
        self.assertEqual(statuses, {"missing", "absent", "submitted"})
        self.assertEqual(
            {summary.status for summary in result.cumulative_summaries},
            {
                "needs_attendance_or_completion_review",
                "needs_conceptual_review",
            },
        )

    def test_plan_records_feedback_contract_and_is_idempotent(self) -> None:
        revision = _revision(
            "submission:metadata", "learner:metadata", "2026-09-12T12:00:00-04:00"
        )
        request = self.request(
            feedback_prompt_version="feedback-v7",
            rubric_version="rubric-v4",
            model_identifier="fake/disabled",
        )
        first = plan_student_work(self.connection, FakeStudentWorkAdapter((revision,)), request)
        second = plan_student_work(self.connection, FakeStudentWorkAdapter((revision,)), request)
        self.assertTrue(first.run_created)
        self.assertFalse(second.run_created)
        self.assertEqual(len(first.proposed_action_ids), 1)
        self.assertEqual(len(second.proposed_action_ids), 0)
        action_count = self.connection.execute(
            "SELECT COUNT(*) AS total FROM proposed_actions WHERE action_type = 'grade_proposal_review'"
        ).fetchone()["total"]
        self.assertEqual(action_count, 1)
        row = self.connection.execute(
            "SELECT proposed_state_json FROM proposed_actions WHERE id = ?",
            (first.proposed_action_ids[0],),
        ).fetchone()
        contract = json.loads(row["proposed_state_json"])["feedback_contract"]
        self.assertEqual(
            contract,
            {
                "feedback_prompt_version": "feedback-v7",
                "rubric_version": "rubric-v4",
                "model_identifier": "fake/disabled",
            },
        )

    def test_new_feedback_contract_supersedes_prior_local_plan(self) -> None:
        revision = _revision(
            "submission:contract", "learner:contract", "2026-09-12T12:00:00-04:00"
        )
        first = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((revision,)),
            self.request(feedback_prompt_version="feedback-v1"),
        )
        second = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((revision,)),
            self.request(feedback_prompt_version="feedback-v2"),
        )
        self.assertEqual(len(second.proposed_action_ids), 1)
        old_action = self.connection.execute(
            "SELECT state FROM proposed_actions WHERE id = ?", (first.proposed_action_ids[0],)
        ).fetchone()
        self.assertEqual(old_action["state"], "superseded")
        new_action = self.connection.execute(
            "SELECT state FROM proposed_actions WHERE id = ?", (second.proposed_action_ids[0],)
        ).fetchone()
        self.assertEqual(new_action["state"], "proposed")

    def test_reverted_feedback_contract_creates_fresh_active_review_plan(self) -> None:
        revision = _revision(
            "submission:contract-revert", "learner:contract-revert", "2026-09-12T12:00:00-04:00"
        )
        first = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((revision,)),
            self.request(feedback_prompt_version="feedback-v1"),
        )
        second = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((revision,)),
            self.request(feedback_prompt_version="feedback-v2"),
        )
        reverted = plan_student_work(
            self.connection,
            FakeStudentWorkAdapter((revision,)),
            self.request(feedback_prompt_version="feedback-v1"),
        )
        self.assertEqual(len(first.proposed_action_ids), 1)
        self.assertEqual(len(second.proposed_action_ids), 1)
        self.assertEqual(len(reverted.proposed_action_ids), 1)
        self.assertNotEqual(first.proposed_action_ids[0], reverted.proposed_action_ids[0])
        states = {
            row["id"]: row["state"]
            for row in self.connection.execute(
                "SELECT id, state FROM proposed_actions WHERE action_type = 'grade_proposal_review'"
            ).fetchall()
        }
        self.assertEqual(states[first.proposed_action_ids[0]], "superseded")
        self.assertEqual(states[second.proposed_action_ids[0]], "superseded")
        self.assertEqual(states[reverted.proposed_action_ids[0]], "proposed")

    def test_multiple_revisions_in_one_adapter_batch_plan_only_newest_active_revision(self) -> None:
        older = _revision(
            "submission:two-revisions",
            "learner:two-revisions",
            "2026-09-12T09:00:00-04:00",
            revision_hash="hash-v1",
        )
        newer = _revision(
            "submission:two-revisions",
            "learner:two-revisions",
            "2026-09-12T10:00:00-04:00",
            revision_hash="hash-v2",
        )
        result = plan_student_work(
            self.connection, FakeStudentWorkAdapter((older, newer)), self.request()
        )
        self.assertEqual(len(result.proposed_action_ids), 1)
        action = self.connection.execute(
            "SELECT source_revision_id FROM proposed_actions WHERE id = ?",
            (result.proposed_action_ids[0],),
        ).fetchone()
        source = self.connection.execute(
            "SELECT active, payload_json FROM source_revisions WHERE id = ?", (action["source_revision_id"],)
        ).fetchone()
        self.assertEqual(source["active"], 1)
        self.assertEqual(
            json.loads(source["payload_json"])["submission_revision"]["revision_hash"], "hash-v2"
        )

    def test_unavailable_reader_returns_a_blocked_local_review_item(self) -> None:
        result = plan_student_work(self.connection, _UnavailableStudentWorkAdapter(), self.request())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(result.discoveries), 0)
        self.assertEqual(len(result.review_item_ids), 1)
        self.assertEqual(result.external_writes, 0)
        run = self.connection.execute("SELECT status FROM runs WHERE id = ?", (result.run_id,)).fetchone()
        self.assertEqual(run["status"], "blocked")

    def test_non_synthetic_reader_is_not_called(self) -> None:
        reader = _ExternalStudentWorkAdapter()
        result = plan_student_work(self.connection, reader, self.request())
        self.assertFalse(reader.called)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.external_writes, 0)

    def test_ambiguous_missing_work_and_naive_time_fail_safely(self) -> None:
        with self.assertRaises(StudentWorkWorkflowError):
            plan_student_work(
                self.connection,
                _MetadataStudentWorkAdapter(
                    [
                        {
                            "stable_id": "submission:ambiguous",
                            "assignment_stable_id": "biology-a4:energy-intro",
                            "learner_stable_id": "learner:ambiguous",
                            "revision_hash": "ambiguous-v1",
                            "observed_at": "2026-09-12T12:00:00-04:00",
                            "work_status": "missing",
                            "conceptual_difficulty": "unsupported inference",
                        }
                    ]
                ),
                self.request(),
            )
        with self.assertRaises(StudentWorkWorkflowError):
            plan_student_work(
                self.connection,
                FakeStudentWorkAdapter(
                    (
                        _revision("submission:naive", "learner:naive", "2026-09-12T12:00:00"),
                    )
                ),
                self.request(),
            )


if __name__ == "__main__":
    unittest.main()
