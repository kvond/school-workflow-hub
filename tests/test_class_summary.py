"""Synthetic compiled reports; no school records or writes."""
import copy
import unittest
from school_workflow_hub.workflows.class_summary import build_class_summary


def record(key="s1", stage="final"):
    return {"schema_version": "student-feedback-compiled/v1", "prompt_version": "prompt:1",
            "class_key": "class:bio", "assignment_key": "assignment:1", "learner_key": key,
            "display_name": "Example A", "batch_key": "batch:1", "submission_revision": "revision:1",
            "stage": stage, "timing_evidence": "unknown",
            "completion": {c: {"completed": 1 if c == "NOTES" else 0,
                "required": 1, "due": stage == "final" or c == "NOTES"} for c in ("NOTES", "BANK", "DRAFT")},
            "understanding": [{"question_id": "Q1", "level": "Developing"}],
            "misconceptions": [], "review_reasons": []}


def misconception():
    return {"idea_key": "idea:1", "description": "Matter disappears", "quote": "It disappears.",
            "source_text": "It disappears.", "question_id": "Q1", "timing": "unknown", "evidence_reference": "evidence:1"}


def summary(rows, **kwargs):
    return build_class_summary(rows, class_key="class:bio", assignment_key="assignment:1", **kwargs)


class ClassSummaryTests(unittest.TestCase):
    def test_unknown_coverage_is_partial(self):
        result = summary([record()])
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["coverage"]["expected"])
        self.assertEqual(result["external_writes"], 0)

    def test_all_batches_support_more_than_twenty(self):
        rows = [record(f"s{i}") for i in range(31)]
        for row in rows[20:]:
            row["batch_key"] = "batch:2"
        result = summary(rows, expected_learners=[r["learner_key"] for r in rows],
                         expected_batch_keys=["batch:1", "batch:2"])
        self.assertEqual(result["coverage"]["state"], "complete")
        self.assertEqual(result["participation"]["decks_in"], 31)
        self.assertEqual(result["proficiency_by_question"]["Q1"]["Developing"], 31)

    def test_missing_batch_and_student_reported(self):
        result = summary([record()], expected_learners=["s1", "s2"], expected_batch_keys=["batch:1", "batch:2"])
        self.assertEqual(result["coverage"]["missing_learners"], ["s2"])
        self.assertEqual(result["coverage"]["missing_batches"], ["batch:2"])
        self.assertEqual(result["status"], "partial")

    def test_duplicate_student_rejected_across_batches(self):
        a, b = record(), record()
        b["batch_key"] = "batch:2"
        with self.assertRaisesRegex(ValueError, "duplicate_student"):
            summary([a, b])

    def test_initial_bank_blank_is_not_due_and_does_not_prevent_complete(self):
        result = summary([record(stage="initial")])
        group = result["participation"]["by_stage"]["initial"]
        self.assertEqual(group["complete"], 1)
        self.assertEqual(group["categories"]["BANK"]["status"], "not_yet_due")
        self.assertEqual(group["categories"]["DRAFT"]["complete_students"], 0)

    def test_unknown_completion_not_zero_or_complete(self):
        row = record()
        row["completion"] = None
        row["understanding"] = []
        result = summary([row])
        group = result["participation"]["by_stage"]["final"]
        self.assertEqual(group["completion_unavailable"], 1)
        self.assertEqual(group["complete"], 0)
        self.assertEqual(len(result["teacher_checks"]), 2)

    def test_recurrence_requires_distinct_students_not_multiple_quotes(self):
        a = record()
        a["misconceptions"] = [misconception(), misconception()]
        self.assertEqual(summary([a])["recurring_misconceptions"], [])
        b = record("s2")
        b["misconceptions"] = [misconception()]
        result = summary([a, b])
        issue = result["recurring_misconceptions"][0]
        self.assertEqual(issue["student_count"], 2)
        self.assertEqual(len(issue["evidence"]), 2)
        self.assertEqual(issue["evidence"][0]["timing"], "unknown")
        self.assertEqual(result["instructional_contrasts"], [])

    def test_fabricated_quote_rejected(self):
        row = record()
        row["misconceptions"] = [misconception()]
        row["misconceptions"][0]["quote"] = "Fabricated."
        with self.assertRaisesRegex(ValueError, "misconception_evidence"):
            summary([row])

    def test_contrast_requires_recurring_evidence(self):
        rows = [record(), record("s2")]
        for row in rows:
            row["misconceptions"] = [misconception()]
        contrast = {"idea_key": "idea:1", "case_a": "Water in an open container", "case_b": "Water in a sealed container",
                    "rationale": "Track mass before and after evaporation.", "evidence_references": ["evidence:1"]}
        self.assertEqual(summary(rows, contrasts=[contrast])["instructional_contrasts"], [contrast])
        contrast["evidence_references"] = ["unrelated:1"]
        with self.assertRaisesRegex(ValueError, "evidence_mismatch"):
            summary(rows, contrasts=[contrast])

    def test_unavailable_question_not_assigned_level(self):
        a, b = record(), record("s2")
        b["understanding"] = []
        self.assertEqual(summary([a, b])["proficiency_by_question"]["Q1"]["unavailable"], 1)

    def test_invalid_counts_and_scope_rejected(self):
        for change in (lambda r: r.update(assignment_key="other"),
                       lambda r: r["completion"]["NOTES"].update(completed=True),
                       lambda r: r["completion"]["BANK"].update(required=-1)):
            row = record()
            change(row)
            with self.assertRaises(ValueError):
                summary([row])

    def test_mixed_timing_counts_later_completion_and_revision_separately(self):
        row = record()
        row["comparisons"] = [
            {"question_id": "Q1", "basis": "genuine_earlier_answer", "change": "revised", "evidence_references": ["e:1", "e:2"]},
            {"question_id": "Q2", "basis": "known_later_completion", "change": "completed_later", "evidence_references": ["e:3", "e:4"]}]
        counts = summary([row])["participation"]["comparison_timing"]
        self.assertEqual(counts["documented_revision"], 1)
        self.assertEqual(counts["known_later_completion"], 1)
        row["comparisons"][0]["basis"] = "unknown_timing"
        with self.assertRaisesRegex(ValueError, "unsupported_compiled_change"):
            summary([row])

    def test_zero_denominator_is_na(self):
        row = record()
        row["completion"]["DRAFT"].update(required=0)
        result = summary([row])
        self.assertEqual(result["participation"]["by_stage"]["final"]["categories"]["DRAFT"]["status"], "N/A")

    def test_inputs_preserved(self):
        row = record()
        before = copy.deepcopy(row)
        summary([row])
        self.assertEqual(row, before)


if __name__ == "__main__":
    unittest.main()
