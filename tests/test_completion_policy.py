from dataclasses import replace
import unittest

from school_workflow_hub.workflows.completion_policy import (
    AssessedResponse, ReadinessAssessment, assess_completion, check_readiness, feedback_batches,
)
from school_workflow_hub.workflows.teacher_workspace import plan_completion_delivery


def ready(**changes):
    ids = {"NOTES": ("q1", "q2", "conflict"), "BANK": ("term (2n)",), "DRAFT": ("q1",)}
    value = ReadinessAssessment("[[MARKER-INVENTORY: fictional fixture]]", True, ids, ids,
                               True, True, True, "receipt:synthetic:inventory")
    return replace(value, **changes)


def responses(**changes):
    return tuple(AssessedResponse(c, q, changes.get(q, "meaningful_attempt"), "receipt:synthetic:response")
                 for c, ids in ready().required_ids.items() for q in ids)


class CompletionPolicyTests(unittest.TestCase):
    def test_final_counts_notes_only_conflict_and_bank_once(self):
        result = assess_completion(ready(), responses(q2="blank"), stage="final")
        self.assertEqual(result["scores"]["content_notes"]["value"], 7.5)
        self.assertEqual(result["scores"]["content_notes"]["required"], 4)
        self.assertEqual(result["scores"]["revision"]["value"], 10)
        self.assertEqual(result["counts"]["DRAFT"]["required"], 1)

    def test_initial_bank_and_draft_not_due(self):
        result = assess_completion(ready(), responses(**{"term (2n)": "blank"}), stage="initial")
        self.assertEqual(result["scores"]["content_notes"]["value"], 10)
        self.assertEqual(result["scores"]["content_notes"]["required"], 3)
        self.assertEqual(result["scores"]["revision"]["status"], "not_yet_due")
        self.assertIsNone(result["scores"]["revision"]["value"])

    def test_zero_denominators_are_na(self):
        ids = {c: () for c in ("NOTES", "BANK", "DRAFT")}
        result = assess_completion(ready(required_ids=ids, observed_ids=ids), (), stage="final")
        self.assertEqual(result["scores"]["revision"]["status"], "N/A")
        self.assertIsNone(result["scores"]["content_notes"]["value"])

    def test_unready_inventory_never_scores(self):
        for gate in (ready(inventory_line=""), ready(inventory_verified=False),
                     ready(lesson_sequence_verified=False), ready(conflict_after_bank_verified=False)):
            result = assess_completion(gate, (), stage="final")
            self.assertIsNone(result["scores"])
            self.assertEqual(result["readiness"]["status"], "NOT READY")

    def test_missing_extra_and_duplicate_markers_hold(self):
        for ids in ({"NOTES": ("q1",), "BANK": (), "DRAFT": ()},
                    {"NOTES": ("q1", "q1", "q2", "conflict"), "BANK": ("term (2n)",), "DRAFT": ("q1",)}):
            self.assertEqual(check_readiness(ready(observed_ids=ids))["status"], "NOT READY")

    def test_short_lesson_needs_verified_sequence_not_day3_literal(self):
        self.assertEqual(check_readiness(ready(conflict_required=False, conflict_after_bank_verified=False))["status"], "READY")

    def test_unreadable_and_missing_assessments_hold_not_zero(self):
        for assessed in (responses(q2="review"), responses()[:-1], responses() + responses()[:1]):
            self.assertIsNone(assess_completion(ready(), assessed, stage="final")["scores"])

    def test_printed_copy_is_not_a_meaningful_attempt(self):
        result = assess_completion(ready(), responses(q2="printed_copy"), stage="final")
        self.assertEqual(result["scores"]["content_notes"]["value"], 7.5)

    def test_optional_excluded_and_cannot_reclassify_required(self):
        extra = AssessedResponse("NOTES", "challenge", "meaningful_attempt", "receipt:synthetic:optional", True)
        result = assess_completion(ready(), responses() + (extra,), stage="final")
        self.assertEqual(result["scores"]["content_notes"]["required"], 4)
        self.assertEqual(result["optional_items_reported"], 1)
        self.assertIsNone(assess_completion(ready(), responses() + (replace(extra, question_id="q1"),), stage="final")["scores"])

    def test_latest_completion_does_not_claim_timing_or_growth(self):
        result = assess_completion(ready(), responses(), stage="final")
        self.assertEqual(result["growth_claim"], "not_inferred")
        self.assertEqual(result["external_writes"], 0)

    def test_batches_twenty_and_full_class_is_not_mutated(self):
        items = tuple(range(45))
        batches = feedback_batches(items)
        self.assertEqual([len(b) for b in batches], [20, 20, 5])
        self.assertEqual(tuple(v for b in batches for v in b), items)

    def test_ic_proposal_requires_same_grade_roster_readback(self):
        args = dict(stage="final", class_key="class:synthetic:bio", assignment_key="assignment:synthetic:one",
                    learner_key="learner:synthetic:one", submission_revision="revision:one",
                    roster_current_reference="receipt:synthetic:roster", ic_assignment_reference="ic:synthetic:one")
        result = plan_completion_delivery(ready(), responses(), **args)
        self.assertEqual(result["roster_step"], "prepared_not_written")
        self.assertEqual(result["ic_step"], "blocked")
        receipt = {"proposal_key": result["proposal_key"], "receipt_reference": "receipt:synthetic:saved",
                   "verification": "live_end_to_end"}
        next_step = plan_completion_delivery(ready(), responses(), **args, roster_grade_readback=receipt)
        self.assertEqual(next_step["ic_step"], "prepared_not_posted")
        self.assertFalse(next_step["execution_supported"])
        changed = plan_completion_delivery(ready(), responses(q2="blank"), **args, roster_grade_readback=receipt)
        self.assertEqual(changed["ic_step"], "blocked")
        for field in ("roster_current_reference", "ic_assignment_reference"):
            changed = plan_completion_delivery(ready(), responses(),
                                               **dict(args, **{field: "reference:changed"}),
                                               roster_grade_readback=receipt)
            self.assertNotEqual(changed["proposal_key"], result["proposal_key"])
            self.assertEqual(changed["ic_step"], "blocked")
