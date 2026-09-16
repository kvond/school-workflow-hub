from dataclasses import replace
import unittest

from school_workflow_hub.workflows.completion_policy import (
    AssessedResponse, ManualReconciliation, ManualResponseMapping, ReadinessAssessment,
    assess_completion, check_readiness, feedback_batches,
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


def manual_case(*, stage="initial", ids=None, judgments=None):
    ids = ids if ids is not None else {"NOTES": ("one", "two", "three"), "BANK": (), "DRAFT": ("later",)}
    assessed, mappings = [], []
    for category in ("NOTES", "BANK", "DRAFT") if stage == "final" else ("NOTES",):
        for qid in ids[category]:
            ref = "field:synthetic:" + category + ":" + qid
            assessed.append(AssessedResponse(category, "duplicated_legacy_label",
                                            (judgments or {}).get(qid, "meaningful_attempt"), ref))
            mappings.append(ManualResponseMapping(category, qid, ref, "review:synthetic:map:" + qid))
    record = ManualReconciliation(
        readiness_evidence_reference=ready().evidence_reference,
        submission_revision_reference="revision:synthetic:distributed",
        requirements_reference="receipt:synthetic:distributed_prompts",
        stage_reference="receipt:synthetic:initial_or_final",
        reviewer_reference="reviewer:synthetic:one", evidence_reference="review:synthetic:one",
        stage=stage, required_ids=ids, response_mappings=tuple(mappings), requirements_verified=True,
    )
    return record, tuple(assessed)


class CompletionPolicyTests(unittest.TestCase):
    def test_manual_actual_distributed_prompts_resolve_markers_and_zero_bank(self):
        record, assessed = manual_case(stage="final", judgments={"two": "blank"})
        broken = ready(inventory_verified=False, lesson_sequence_verified=False,
                       conflict_after_bank_verified=False)
        strict = assess_completion(broken, assessed, stage="final")
        self.assertIsNone(strict["scores"])
        result = assess_completion(broken, assessed, stage="final", manual_reconciliation=record)
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "20/3")
        self.assertEqual(result["scores"]["content_notes"]["required"], 3)
        self.assertEqual(result["counts"]["BANK"]["required"], 0)
        self.assertEqual(result["readiness"]["original_status"], "NOT READY")
        self.assertIn("inventory_unverified", result["readiness"]["original_issues"])
        self.assertEqual(result["manual_reconciliation"]["submission_revision_reference"],
                         "revision:synthetic:distributed")
        self.assertEqual(result["external_writes"], 0)

    def test_manual_initial_does_not_wait_for_future_ambiguous_response(self):
        record, assessed = manual_case()
        future = AssessedResponse("DRAFT", "later", "review", "field:synthetic:future")
        result = assess_completion(ready(), assessed + (future,), stage="initial",
                                   manual_reconciliation=record)
        self.assertEqual(result["scores"]["content_notes"]["value"], 10)
        self.assertEqual(result["scores"]["revision"]["status"], "not_yet_due")
        self.assertIsNone(result["counts"]["DRAFT"]["completed"])
        self.assertEqual(result["deferred_categories"], ["BANK", "DRAFT"])
        self.assertIn(future.evidence_reference, result["unmapped_response_references"])

    def test_manual_mapping_counts_unique_prompt_once_even_with_duplicate_legacy_labels(self):
        record, assessed = manual_case()
        result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=record)
        self.assertEqual(result["counts"]["NOTES"], {"completed": 3, "required": 3, "due": True})
        duplicate = replace(record, response_mappings=record.response_mappings + record.response_mappings[:1])
        held = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=duplicate)
        self.assertIsNone(held["scores"])
        self.assertIn("duplicate_manual_item_mapping", held["issues"])

    def test_manual_one_response_field_cannot_cover_two_prompts(self):
        record, assessed = manual_case()
        mappings = list(record.response_mappings)
        mappings[1] = replace(mappings[1], response_evidence_reference=mappings[0].response_evidence_reference)
        result = assess_completion(ready(), assessed, stage="initial",
                                   manual_reconciliation=replace(record, response_mappings=tuple(mappings)))
        self.assertIsNone(result["scores"])
        self.assertIn("manual_response_field_reused", result["issues"])

    def test_manual_mapping_needs_unique_inspected_evidence_and_all_due_items(self):
        record, assessed = manual_case()
        for input_responses in (assessed[:-1], assessed + assessed[:1]):
            result = assess_completion(ready(), input_responses, stage="initial", manual_reconciliation=record)
            self.assertIsNone(result["scores"])
            self.assertIn("manual_response_evidence_not_unique", result["issues"])
        result = assess_completion(ready(), assessed, stage="initial",
                                   manual_reconciliation=replace(record, response_mappings=record.response_mappings[:-1]))
        self.assertIn("required_response_not_assessed", result["issues"])

    def test_manual_structure_review_never_credits_unclear_or_printed_writing(self):
        record, assessed = manual_case(judgments={"two": "review"})
        result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=record)
        self.assertIsNone(result["scores"])
        self.assertIn("unreadable_or_ambiguous_response", result["issues"])
        record, assessed = manual_case(judgments={"two": "printed_copy", "three": "blank"})
        result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=record)
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "10/3")

    def test_manual_provenance_and_checkpoint_cannot_be_reused_for_other_source(self):
        record, assessed = manual_case()
        for altered in (replace(record, readiness_evidence_reference="receipt:another_source"),
                        replace(record, submission_revision_reference="UNVERIFIED"),
                        replace(record, requirements_reference=""),
                        replace(record, reviewer_reference=""),
                        replace(record, requirements_verified=False),
                        replace(record, stage="final")):
            result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=altered)
            self.assertIsNone(result["scores"])
            self.assertEqual(result["status"], "needs_review")

    def test_manual_final_bank_still_requires_applicable_sequence_evidence(self):
        ids = {"NOTES": ("one",), "BANK": ("term",), "DRAFT": ()}
        record, assessed = manual_case(stage="final", ids=ids)
        result = assess_completion(ready(), assessed, stage="final", manual_reconciliation=record)
        self.assertIn("manual_bank_sequence_unverified", result["issues"])
        record = replace(record, bank_sequence_verified=True,
                         bank_sequence_evidence_reference="review:synthetic:bank_order")
        result = assess_completion(ready(), assessed, stage="final", manual_reconciliation=record)
        self.assertEqual(result["scores"]["content_notes"]["value"], 10)
        self.assertEqual(result["scores"]["revision"]["status"], "N/A")

    def test_manual_exact_fraction_preserved_without_posting_rounding(self):
        ids = {"NOTES": tuple("q" + str(n) for n in range(13)), "BANK": (), "DRAFT": ()}
        record, assessed = manual_case(ids=ids, judgments={"q" + str(n): "blank" for n in range(6, 13)})
        result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=record)
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "60/13")
        self.assertEqual(result["scores"]["content_notes"]["completed"], 6)

    def test_manual_invalid_requirements_do_not_silently_deduplicate(self):
        record, assessed = manual_case()
        record = replace(record, required_ids={"NOTES": ("one", "one"), "BANK": (), "DRAFT": ()})
        result = assess_completion(ready(), assessed, stage="initial", manual_reconciliation=record)
        self.assertIsNone(result["scores"])
        self.assertIn("distributed_requirements_invalid", result["issues"])

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
