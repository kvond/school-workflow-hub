from dataclasses import replace
import unittest

from school_workflow_hub.core.models import AssignmentRecord, EnrollmentRecord, SubmissionRevision
from school_workflow_hub.workflows.completion_policy import AssessedResponse, ReadinessAssessment
from school_workflow_hub.workflows.due_completion import (
    DueAssignment, FolderScan, FolderSubmission, WorkAssessment, check_due_assignments,
    process_assignment, boundaries,
)

NOW = "2026-09-15T16:00:00-04:00"
IDS = {"NOTES": ("q1", "q2"), "BANK": ("b1",), "DRAFT": ("d1",)}
READY = ReadinessAssessment("[[MARKER-INVENTORY: synthetic]]", True, IDS, IDS,
                           True, False, False, "evidence:synthetic:lesson")
SCOPE = DueAssignment(AssignmentRecord("assignment:fictional", "class:fictional", "Fictional lesson",
                                      "2026-09-01"), "folder:fictional", "mapping:v1",
                      "2026-09-01", "final", READY,
                      {"content_notes": "ic:content", "revision": "ic:revision"})
ROSTER = (EnrollmentRecord("class:fictional", "learner:one"),
          EnrollmentRecord("class:fictional", "learner:two"))
FACT = FolderSubmission(SubmissionRevision("doc:one", "assignment:fictional", "learner:one", "revision:1",
                                           "2026-09-02T10:00:00-04:00"),
                        "2026-08-31T10:00:00-04:00", "evidence:synthetic:work")
SCAN = FolderScan("folder:fictional", "assignment:fictional", "snapshot:1", NOW,
                  (FACT,), True, ROSTER, True)


def assessor(scope, fact):
    responses = tuple(AssessedResponse(c, q, "blank" if q == "q2" else "meaningful_attempt",
                                      "evidence:synthetic:response") for c, ids in IDS.items() for q in ids)
    return WorkAssessment(fact.revision.stable_id, fact.revision.revision_hash, scope.readiness, responses)


class DueCompletionTests(unittest.TestCase):
    def run_case(self, scope=SCOPE, scan=SCAN, now=NOW, assess=assessor):
        return process_assignment(scope, as_of=now, scan_folder=lambda *_: scan, assess_work=assess)

    def test_actual_assessments_score_and_early_turnin_is_not_lost(self):
        result = self.run_case()
        first, second = result["rows"]
        self.assertAlmostEqual(first["scores"]["content_notes"], 20/3)
        self.assertEqual(first["scores"]["revision"], 10)
        self.assertEqual(second["status"], "missing_unscored")
        self.assertIsNone(second["scores"])
        self.assertFalse(first["post_enabled"])

    def test_due_date_mismatch_prevents_source_call(self):
        def forbidden(*_):
            self.fail("must not scan mismatched assignment")
        result = process_assignment(replace(SCOPE, ic_due_date="2026-09-02"), as_of=NOW,
                                    scan_folder=forbidden, assess_work=forbidden)
        self.assertEqual(result["issues"], ["agenda_ic_due_mismatch"])

    def test_future_assignment_does_not_read_folder(self):
        scope = replace(SCOPE, assignment=replace(SCOPE.assignment, intended_due_date="2026-09-20"),
                        ic_due_date="2026-09-20")
        self.assertEqual(self.run_case(scope=scope)["status"], "not_due")

    def test_whole_fourteenth_day_unscored_then_zero(self):
        for now, state in (("2026-09-15T23:59:59-04:00", "missing_unscored"),
                           ("2026-09-16T00:00:00-04:00", "zero_proposed")):
            result = self.run_case(scan=replace(SCAN, observed_at=now), now=now)
            row = result["rows"][1]
            self.assertEqual(row["status"], state)
            self.assertEqual(row["scores"], None if state == "missing_unscored" else
                             {"content_notes": 0, "revision": 0})

    def test_individual_extension_prevents_zero(self):
        now = "2026-09-16T12:00:00-04:00"
        scan = replace(SCAN, observed_at=now, effective_due_dates={"learner:two": "2026-09-10"})
        self.assertEqual(self.run_case(scan=scan, now=now)["rows"][1]["status"], "missing_unscored")

    def test_partial_history_roster_unresolved_and_wrong_folder_hold(self):
        for scan in (replace(SCAN, complete_history=False), replace(SCAN, roster_complete=False),
                     replace(SCAN, unresolved_items=("unknown:owner",)), replace(SCAN, folder_id="wrong")):
            result = self.run_case(scan=scan)
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["status"], "needs_you")

    def test_stale_scan_cannot_zero(self):
        self.assertEqual(self.run_case(now="2026-09-16T12:00:00-04:00")["issues"],
                         ["stale_or_future_scan"])

    def test_unreadable_and_stale_assessment_do_not_score(self):
        def unavailable(*_):
            raise RuntimeError("private provider error")
        for assess in (unavailable, lambda s, f: replace(assessor(s, f), revision_hash="stale")):
            result = self.run_case(assess=assess)
            self.assertIsNone(result["rows"][0]["scores"])
            self.assertNotIn("private provider error", str(result))

    def test_unrecognized_student_and_cross_assignment_hold_entire_scan(self):
        for revision in (replace(FACT.revision, learner_stable_id="unknown"),
                         replace(FACT.revision, assignment_stable_id="wrong")):
            self.assertEqual(self.run_case(scan=replace(SCAN, submissions=(replace(FACT, revision=revision),)))
                             ["issues"], ["submission_identity_mismatch"])

    def test_multiple_student_documents_need_review(self):
        extra = replace(FACT, revision=replace(FACT.revision, stable_id="doc:two"))
        self.assertEqual(self.run_case(scan=replace(SCAN, submissions=(FACT, extra)))["rows"][0]
                         ["issues"], ["multiple_documents_need_review"])

    def test_latest_revision_is_assessed(self):
        extra = replace(FACT, revision=replace(FACT.revision, revision_hash="revision:2",
                                              observed_at="2026-09-10T10:00:00-04:00"))
        observed = []
        def capture(s, f):
            observed.append(f.revision.revision_hash)
            return assessor(s, f)
        self.run_case(scan=replace(SCAN, submissions=(extra, FACT)), assess=capture)
        self.assertEqual(observed, ["revision:2"])

    def test_work_first_submitted_after_cutoff_requires_review(self):
        now = "2026-09-16T12:00:00-04:00"
        late = replace(FACT, submitted_at=now, revision=replace(FACT.revision, observed_at=now))
        row = self.run_case(scan=replace(SCAN, observed_at=now, submissions=(late,)), now=now)["rows"][0]
        self.assertIsNone(row["scores"])
        self.assertEqual(row["issues"], ["submitted_after_cutoff_needs_review"])

    def test_missing_score_target_is_flagged_not_guessed(self):
        row = self.run_case(scope=replace(SCOPE, grade_targets={}))["rows"][0]
        self.assertIn("ic_score_target_unverified", row["issues"])
        self.assertFalse(row["post_enabled"])

    def test_policy_and_replay_deterministic(self):
        args = dict(as_of=NOW, scan_folder=lambda *_: SCAN, assess_work=assessor)
        self.assertEqual(check_due_assignments([SCOPE], **args), check_due_assignments([SCOPE], **args))
        with self.assertRaises(ValueError):
            check_due_assignments([SCOPE, SCOPE], **args)

    def test_daylight_saving_uses_local_calendar(self):
        due, cutoff = boundaries("2026-10-25", "America/New_York")
        self.assertEqual(due.utcoffset().total_seconds(), -14400)
        self.assertEqual(cutoff.utcoffset().total_seconds(), -18000)
        self.assertEqual(cutoff.date().isoformat(), "2026-11-08")

    def test_source_failure_has_no_rows_or_zero(self):
        def fail(*_):
            raise RuntimeError("private source response")
        result = process_assignment(SCOPE, as_of=NOW, scan_folder=fail, assess_work=assessor)
        self.assertEqual(result["issues"], ["submission_source_unavailable"])
        self.assertEqual(result["rows"], [])

    def test_no_required_items_never_propose_zero_or_parent_email(self):
        ids = {c: () for c in IDS}
        scope = replace(SCOPE, readiness=replace(READY, required_ids=ids, observed_ids=ids))
        now = "2026-09-16T12:00:00-04:00"
        result = self.run_case(scope=scope, scan=replace(SCAN, observed_at=now, submissions=()), now=now)
        self.assertTrue(all(r["scores"] is None and r["parent_email"] == "not_needed" for r in result["rows"]))
