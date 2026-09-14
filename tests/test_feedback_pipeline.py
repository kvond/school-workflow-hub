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

    def test_later_first_answers_and_revisions_count_once_as_separate_snapshots(self):
        original, revised = self.observations[:2]
        original = replace(original, completion=replace(original.completion, first_answers_completed=0))
        revised = replace(revised, completion=replace(revised.completion,
                          first_answers_completed=2, first_answers_completed_later=2))
        self.run_pipeline((original,))
        result = self.run_pipeline((revised,))
        grade = json.loads(result.grade_proposals[0].proposed_value)
        self.assertEqual(grade["completion"]["first_answers"], [2, 2])
        self.assertEqual(grade["completion"]["later_first_answers_credited"], 2)
        self.assertEqual(grade["revision"]["draft"], [2, 2])
        self.assertEqual(grade["revision"]["growth_claim"], "not_inferred")
        counts = self.counts()
        replay = self.run_pipeline((original, revised))
        self.assertEqual(replay.records, result.records)
        self.assertEqual(replay.preview.changes, ())
        self.assertEqual(counts, self.counts())
        self.assertEqual(len(replay.history), 2)

    def test_legacy_first_answer_counts_stay_unknown(self):
        result = self.run_pipeline((self.observations[1],))
        grade = json.loads(result.grade_proposals[0].proposed_value)
        self.assertIsNone(grade["completion"]["first_answers"])

    def test_known_first_answers_cannot_silently_become_unknown_or_lower(self):
        original = replace(self.observations[1], completion=replace(
            self.observations[1].completion, first_answers_completed=2))
        for index, first_count in enumerate((None, 1)):
            with self.subTest(first_count=first_count):
                changed = replace(original, completion=replace(original.completion, first_answers_completed=first_count),
                    submission=replace(original.submission, revision_hash=f"synthetic-revision:first-count-{index}",
                                       observed_at=f"2026-09-1{index + 2}T12:00:00-04:00"))
                held = self.run_pipeline((original, changed))
                self.assertEqual(held.grade_proposals, ())
                self.assertIn("completion_evidence_regressed", {issue.code for issue in held.preview.issues})

    def test_invalid_first_answer_evidence_is_rejected(self):
        original = self.observations[1]
        for changes in ({"first_answers_completed": True},
                        {"first_answers_completed": 0},
                        {"first_answers_completed": 3},
                        {"first_answers_completed_later": 1},
                        {"first_answers_completed": 1, "first_answers_completed_later": 2}):
            with self.subTest(changes=changes):
                result = self.run_pipeline((replace(original, completion=replace(original.completion, **changes)),))
                self.assertEqual(result.records, ())
                self.assertIn("invalid_submission_metadata", {issue.code for issue in result.preview.issues})

    def test_partial_and_unreadable_snapshots_never_create_lower_proposals(self):
        original = self.observations[1]
        first = self.run_pipeline((original,))
        for index, status in enumerate(("partial", "unreadable")):
            reduced = replace(original, evidence_status=status, completion=CompletionEvidence(),
                              submission=replace(original.submission,
                                  revision_hash=f"synthetic-revision:partial-{index}",
                                  observed_at=f"2026-09-1{index + 2}T12:00:00-04:00"))
            held = self.run_pipeline((reduced,))
            self.assertEqual(held.records, ())
            self.assertEqual(held.preview.changes, ())
            self.assertEqual(held.student_work_plan.grade_proposals, ())
            self.assertIn("incomplete_completion_evidence", {issue.code for issue in held.preview.issues})
            self.assertIn(original, held.history)
            self.assertIn(reduced, held.history)
            counts = self.counts()
            self.run_pipeline((reduced,))
            self.assertEqual(counts, self.counts())
            replay = self.run_pipeline(())
            self.assertEqual(replay.records, ())
            self.assertEqual(replay.preview.changes, ())
        # Prior proposal/evidence survive; there is no replacement zero grade.
        self.assertTrue(self.db.execute("SELECT 1 FROM proposed_actions WHERE proposed_state_json LIKE ?",
                                      ("%" + first.grade_proposals[0].stable_id + "%",)).fetchone())

    def test_reduced_complete_counts_require_review_and_preserve_history(self):
        original = self.observations[1]
        self.run_pipeline((original,))
        reduced = replace(original, completion=CompletionEvidence(),
                          submission=replace(original.submission, revision_hash="synthetic-revision:reduced",
                                             observed_at="2026-09-12T12:00:00-04:00"))
        held = self.run_pipeline((reduced,))
        self.assertEqual(held.grade_proposals, ())
        self.assertIn("completion_evidence_regressed", {issue.code for issue in held.preview.issues})
        self.assertEqual(len(held.history), 2)
        self.assertEqual(self.run_pipeline(()).grade_proposals, ())
        recovered = replace(original, submission=replace(original.submission,
                            revision_hash="synthetic-revision:recovered", observed_at="2026-09-13T12:00:00-04:00"))
        self.assertEqual(len(self.run_pipeline((recovered,)).grade_proposals), 1)

    def test_batch_reduction_and_missing_after_submission_are_held(self):
        original = self.observations[1]
        missing = replace(original, work_status="missing", conceptual_signal="none", completion=CompletionEvidence(),
                          submission=replace(original.submission, revision_hash="synthetic-revision:lost-access",
                                             observed_at="2026-09-12T12:00:00-04:00"))
        held = self.run_pipeline((original, missing))
        self.assertEqual(held.grade_proposals, ())
        self.assertIn("submission_availability_regressed", {issue.code for issue in held.preview.issues})
        self.assertEqual(len(held.history), 2)

    def test_changed_required_counts_need_review_not_a_silent_new_denominator(self):
        original = self.observations[1]
        changed = replace(original, completion=replace(original.completion, notes_required=3),
                          submission=replace(original.submission, revision_hash="synthetic-revision:structure",
                                             observed_at="2026-09-12T12:00:00-04:00"))
        held = self.run_pipeline((original, changed))
        self.assertEqual(held.grade_proposals, ())
        self.assertIn("completion_structure_changed", {issue.code for issue in held.preview.issues})

    def test_unreadable_stored_history_withholds_new_grade(self):
        original = self.observations[1]
        self.run_pipeline((original,))
        self.db.execute("UPDATE source_revisions SET payload_json = '{}' WHERE source_type = ?", (EVIDENCE_SOURCE,))
        changed = replace(original, submission=replace(original.submission, revision_hash="synthetic-revision:readable",
                                                       observed_at="2026-09-12T12:00:00-04:00"))
        held = self.run_pipeline((changed,))
        self.assertEqual(held.grade_proposals, ())
        self.assertIn("stored_metadata_unreadable", {issue.code for issue in held.preview.issues})

    def test_hold_blocks_nested_backlog_after_prompt_contract_changes(self):
        original = self.observations[1]
        prior = self.run_pipeline((original,))
        self.assertEqual(len(prior.student_work_plan.grade_proposals), 1)
        count = self.db.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0]
        partial = replace(original, evidence_status="partial", completion=CompletionEvidence(),
            submission=replace(original.submission, revision_hash="synthetic-revision:partial-prompt",
                               observed_at="2026-09-12T12:00:00-04:00"))
        changed_request = replace(self.request, prompt_version="synthetic-feedback-v2")
        held = self.run_pipeline((partial,), request=changed_request)
        self.assertEqual(held.grade_proposals, ())
        self.assertEqual(held.preview.changes, ())
        self.assertEqual(held.student_work_plan.status, "blocked")
        for field in ("discoveries", "grade_proposals", "feedback_requests", "proposed_action_ids", "cumulative_summaries"):
            self.assertEqual(getattr(held.student_work_plan, field), ())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0], count)
        self.assertIn(original, held.history)
        replay = self.run_pipeline((), request=changed_request)
        self.assertEqual(replay.student_work_plan.status, "blocked")
        self.assertEqual(replay.student_work_plan.grade_proposals, ())
        recovered = replace(original, submission=replace(original.submission,
            revision_hash="synthetic-revision:recovered-prompt", observed_at="2026-09-13T12:00:00-04:00"))
        ready = self.run_pipeline((recovered,), request=changed_request)
        self.assertEqual(len(ready.grade_proposals), 1)
        self.assertEqual(len(ready.student_work_plan.grade_proposals), 1)
        self.assertEqual(ready.student_work_plan.discoveries[0].submission, recovered.submission)

    def test_nested_hold_does_not_hide_unaffected_outer_feedback_records(self):
        original, unaffected = self.observations[1:3]
        partial = replace(original, evidence_status="partial")
        result = self.run_pipeline((partial, unaffected))
        self.assertEqual(result.student_work_plan.status, "blocked")
        self.assertEqual(result.student_work_plan.grade_proposals, ())
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].submission, unaffected.submission)
        self.assertEqual(len(result.preview.changes), 1)

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
