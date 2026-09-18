"""Synthetic monitoring cases: fictional files, students and dates only."""
import unittest

from school_workflow_hub.workflows.submission_monitor import (
    VERSION, advance_state, stream_scope,
)

FOLDER = "f" * 24
OTHER = "g" * 24


def scan(files, observed="2026-09-05T16:00:00+00:00", **overrides):
    base = {"class_key": "biology", "folder_id": FOLDER,
            "folder_name": "Biology — Wk 01 — Fictional",
            "assignment_label": "B01", "date": "2026-09-04",
            "observed_at": observed,
            "coverage": {"complete": True, "folders_checked": 1, "forms_found": 0},
            "files": files}
    base.update(overrides)
    return base


def entry(file_id, modified="2026-09-04T15:00:00+00:00", name="Fictional doc", version="3"):
    return {"id": file_id, "name": name, "modified_at": modified, "version": version}


def mappings(by_file=None, checked="2026-09-05T15:00:00+00:00"):
    return {"source": "grading_workbook", "checked_at": checked, "by_file": by_file or {}}


def mapping(student="9001", marker="2026-09-05"):
    return {"student_id": student, "section_id": "101", "identity_basis": "workbook",
            "assessed_marker": marker, "ic_posting_status": "content=unverified"}


def advance(previous, current, **kwargs):
    options = {"mappings": mappings(), "due_date": "2026-09-04",
               "as_of": "2026-09-08T12:00:00+00:00"}
    options.update(kwargs)
    return advance_state(previous, current, **options)


FILE_A, FILE_B, FILE_C = "a" * 24, "b" * 24, "c" * 24


class StreamIdentity(unittest.TestCase):
    def test_scope_is_folder_bound_and_due_date_free(self):
        scope = stream_scope("biology", FOLDER)
        self.assertEqual(scope["assignment_key"], "folder-" + FOLDER)
        with self.assertRaises(ValueError):
            stream_scope("art", FOLDER)
        with self.assertRaises(ValueError):
            stream_scope("biology", "short")

    def test_stream_mismatch_and_stale_scans_are_rejected(self):
        first = advance(None, scan([entry(FILE_A)]))["state"]
        with self.assertRaises(ValueError):
            advance(first, scan([entry(FILE_A)], folder_id=OTHER,
                                observed="2026-09-05T17:00:00+00:00"))
        with self.assertRaises(ValueError):
            advance(first, scan([entry(FILE_A)]))  # not newer

    def test_partial_scan_never_reaches_the_state(self):
        broken = scan([entry(FILE_A)])
        broken["coverage"] = {"complete": False}
        with self.assertRaises(ValueError):
            advance(None, broken)


class BaselineAndEvents(unittest.TestCase):
    def test_first_scan_labels_first_observed_without_late_claims(self):
        result = advance(None, scan([entry(FILE_A), entry(FILE_B)]))
        kinds = {e["kind"] for e in result["events"]}
        self.assertEqual(kinds, {"first_observed"})
        record = result["state"]["files"][FILE_A]
        self.assertEqual(record["first_observed_kind"], "first_observed")
        self.assertEqual(record["timing"], "baseline_start")
        self.assertEqual(result["state"]["schema_version"], VERSION)

    def test_new_file_with_pre_due_content_stays_timing_unverified(self):
        first = advance(None, scan([entry(FILE_A)]))["state"]
        result = advance(first, scan(
            [entry(FILE_A), entry(FILE_B, modified="2026-09-04T14:00:00+00:00")],
            observed="2026-09-06T16:00:00+00:00"))
        appeared = [e for e in result["events"] if e["kind"] == "appeared"]
        self.assertEqual(len(appeared), 1)
        self.assertEqual(result["state"]["files"][FILE_B]["timing"],
                         "content_predates_due_turnin_unverified")

    def test_new_file_modified_after_due_needs_review(self):
        first = advance(None, scan([entry(FILE_A)]))["state"]
        result = advance(first, scan(
            [entry(FILE_A), entry(FILE_B, modified="2026-09-06T02:00:00+00:00")],
            observed="2026-09-06T16:00:00+00:00"))
        self.assertEqual(result["state"]["files"][FILE_B]["timing"],
                         "modified_after_due_needs_review")
        reasons = {r["reason"] for r in result["state"]["needs_review"]}
        self.assertIn("after_due_evidence_needs_review", reasons)

    def test_modification_is_inspection_evidence_not_resubmission_proof(self):
        first = advance(None, scan([entry(FILE_A)]))["state"]
        result = advance(first, scan(
            [entry(FILE_A, modified="2026-09-06T10:00:00+00:00", version="4")],
            observed="2026-09-06T16:00:00+00:00"))
        modified = [e for e in result["events"] if e["kind"] == "modified"]
        self.assertEqual(len(modified), 1)
        self.assertTrue(modified[0]["modified_after_due"])
        self.assertEqual(len(result["state"]["files"][FILE_A]["revisions"]), 2)

    def test_disappearance_is_access_review_not_withdrawal(self):
        first = advance(None, scan([entry(FILE_A), entry(FILE_B)]))["state"]
        result = advance(first, scan([entry(FILE_A)],
                                     observed="2026-09-06T16:00:00+00:00"))
        record = result["state"]["files"][FILE_B]
        self.assertEqual(record["status"], "inaccessible")
        self.assertIn(FILE_B, [e["file_id"] for e in result["events"]
                               if e["kind"] == "inaccessible"])
        again = advance(result["state"], scan(
            [entry(FILE_A), entry(FILE_B)], observed="2026-09-07T16:00:00+00:00"))
        self.assertEqual(again["state"]["files"][FILE_B]["status"], "present")
        self.assertIn("reappeared", {e["kind"] for e in again["events"]})

    def test_replacement_pair_is_flagged_for_review_not_resolved(self):
        matched = mappings({FILE_A: mapping(), FILE_B: mapping()})
        first = advance(None, scan([entry(FILE_A)]), mappings=matched)["state"]
        result = advance(first, scan([entry(FILE_B)],
                                     observed="2026-09-06T16:00:00+00:00"),
                         mappings=matched)
        kinds = {e["kind"] for e in result["events"]}
        self.assertIn("possible_replacement", kinds)
        self.assertIn("inaccessible", kinds)

    def test_deadline_change_keeps_identity_and_history(self):
        first = advance(None, scan([entry(FILE_A)]))["state"]
        result = advance(first, scan([entry(FILE_A)],
                                     observed="2026-09-06T16:00:00+00:00"),
                         due_date="2026-09-08")
        self.assertIn("due_date_changed", {e["kind"] for e in result["events"]})
        self.assertEqual(set(result["state"]["files"]), {FILE_A})
        self.assertEqual(result["state"]["files"][FILE_A]["first_observed_at"],
                         first["files"][FILE_A]["first_observed_at"])


class IdentityAndAssessment(unittest.TestCase):
    def test_unmatched_files_stay_visible_for_review(self):
        result = advance(None, scan([entry(FILE_A)]))
        reasons = {r["reason"] for r in result["state"]["needs_review"]}
        self.assertIn("unmatched_identity_needs_review", reasons)
        self.assertIn(FILE_A, result["state"]["awaiting_assessment"])

    def test_matched_files_with_newer_revision_await_assessment(self):
        matched = mappings({FILE_A: mapping(marker="2026-09-03")})
        result = advance(None, scan([entry(FILE_A)]), mappings=matched)
        self.assertIn(FILE_A, result["state"]["awaiting_assessment"])
        current = mappings({FILE_A: mapping(marker="2026-09-05")})
        rerun = advance(result["state"], scan(
            [entry(FILE_A)], observed="2026-09-06T16:00:00+00:00"), mappings=current)
        self.assertNotIn(FILE_A, rerun["state"]["awaiting_assessment"])

    def test_extension_moves_the_grace_window_for_matched_students_only(self):
        matched = mappings({FILE_A: mapping(student="9001")})
        result = advance(None, scan([entry(FILE_A), entry(FILE_B)]), mappings=matched,
                         extensions={"9001": "2026-09-10"})
        with_extension = result["state"]["files"][FILE_A]["grace_ends_at"]
        without = result["state"]["files"][FILE_B]["grace_ends_at"]
        self.assertGreater(with_extension, without)

    def test_events_are_deterministic_for_identical_evidence(self):
        one = advance(None, scan([entry(FILE_A)]))
        two = advance(None, scan([entry(FILE_A)]))
        self.assertEqual([e["event_id"] for e in one["events"]],
                         [e["event_id"] for e in two["events"]])
        self.assertEqual(one["fingerprint"], two["fingerprint"])


if __name__ == "__main__":
    unittest.main()
