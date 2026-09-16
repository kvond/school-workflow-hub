import unittest

from school_workflow_hub.workflows.research_agenda import build_research_agenda
from school_workflow_hub.workflows.teacher_workspace import build_teacher_workspace


class ResearchAgendaTests(unittest.TestCase):
    def test_conceptual_understanding_is_first_and_only_primary(self):
        dimensions = build_research_agenda()["dimensions"]
        self.assertEqual(dimensions[0]["key"], "conceptual_understanding")
        self.assertEqual([d["key"] for d in dimensions if d["priority"] == "primary"],
                         ["conceptual_understanding"])
        self.assertIn("getting better", dimensions[0]["question"])

    def test_agency_categories_are_separate_and_use_teacher_names(self):
        self.assertEqual([d["label"] for d in build_research_agenda()["dimensions"][1:]],
                         ["Engaged agency", "Epistemic agency", "Authorial agency"])

    def test_book_review_is_distinct_from_teacher_approval_and_live_access(self):
        agenda = build_research_agenda()
        self.assertTrue(agenda["source_documents_verified"])
        self.assertEqual(agenda["evidence"][0]["verification"], "local_document_review")
        self.assertFalse(agenda["evidence"][0]["teacher_approved"])
        self.assertFalse(agenda["evidence"][0]["older_research_document_reviewed"])
        for dimension in agenda["dimensions"]:
            for field in ("definition", "definition_source_reference", "codebook_version"):
                self.assertTrue(dimension[field])
            self.assertTrue(dimension["observable_criteria"])
            self.assertEqual(dimension["examples"], [])
            self.assertEqual(dimension["status"], "source_grounded_draft_awaiting_teacher_review")
            self.assertTrue(all(c["status"] == "proposed_not_teacher_approved"
                                for c in dimension["observable_criteria"]))

    def test_finding_requires_quote_provenance_and_teacher_review(self):
        contract = build_research_agenda()["finding_contract"]
        for field in ("exact_student_excerpt", "source_revision", "slide_or_response_locator",
                      "writing_date_evidence", "source_match_review_reference"):
            self.assertIn(field, contract["evidence_fields"])
        for field in ("teacher_review_state", "uncertainty", "alternative_explanations",
                      "codebook_version", "supersedes_finding_id"):
            self.assertIn(field, contract["required_fields"])

    def test_growth_requires_comparable_dated_evidence_not_submission_completion(self):
        contract = build_research_agenda()["comparison_contract"]
        self.assertEqual([m["key"] for m in contract["modes"]],
                         ["within_lesson_revision", "across_lessons"])
        self.assertIn("distinct_assignments", contract["modes"][1]["requires"])
        self.assertIn("reviewed_task_comparability", contract["modes"][1]["requires"])
        self.assertEqual(contract["missing_earlier_evidence"], "insufficient_evidence_of_growth")
        self.assertEqual(contract["unknown_writing_order"], "change_direction_unverified")
        self.assertTrue(contract["observed_at_is_not_writing_date"])

    def test_no_execution_or_grade_effect(self):
        agenda = build_research_agenda()
        self.assertFalse(agenda["coding_supported"])
        self.assertFalse(agenda["collection_supported"])
        self.assertEqual(agenda["external_writes"], 0)
        self.assertTrue(agenda["safeguards"]["completion_grades_and_grade_history_unchanged"])

    def test_plan_in_existing_page_does_not_expose_restricted_records(self):
        result = build_teacher_workspace(lessons=(), submission_feeds=(),
                                        generated_at="2026-09-14T20:00:00Z",
                                        research_receipts=({"private": "must not leak"},))
        page = result["pages"]["research_data"]
        self.assertEqual(page["status"], "restricted")
        self.assertEqual(page["records"], [])
        self.assertEqual(page["agenda"]["schema_version"], "research-agenda/v1")
        self.assertNotIn("must not leak", str(result))
        self.assertFalse(result["actions"]["collect_research"]["supported"])

    def test_fresh_payload_prevents_one_view_mutating_another(self):
        first = build_research_agenda()
        before = build_research_agenda()
        first["dimensions"][0]["observable_criteria"].append("invented")
        self.assertEqual(build_research_agenda(), before)

    def test_conceptual_indicators_are_proposals_not_an_invented_book_scale(self):
        book = build_research_agenda()["codebook"]
        self.assertIsNone(book["numeric_scale"])
        self.assertEqual(book["definitions"]["conceptual_understanding"]["definition_kind"],
                         "synthesis_of_book_passages_not_a_single_explicit_definition")
        self.assertTrue(all(len(item) == 3 for item in book["definitions"]["conceptual_understanding"]["indicators"]))
        self.assertIn("conceptual_indicators", [d["id"] for d in book["open_decisions"]])

    def test_authorial_boundary_records_explicit_teacher_approval_only(self):
        book = build_research_agenda()["codebook"]
        decision = next(d for d in book["approved_decisions"] if d["id"] == "authorial_scope")
        self.assertEqual(decision["status"], "teacher_approved")
        self.assertNotIn("authorial_scope", [d["id"] for d in book["open_decisions"]])
        self.assertFalse(book["source"]["teacher_approved"])
        self.assertFalse(book["coding_enabled"])
        self.assertEqual(len(decision["evidence"]), 2)
        self.assertTrue(book["interpretation_rules"]["intellectual_authorship_is_not_authorial_agency"])

    def test_authorial_forms_have_separate_evidence_without_automatic_promotion(self):
        agenda = build_research_agenda()
        recording = agenda["finding_contract"]["conditional_fields"]["authorial_agency"]
        self.assertEqual(recording, agenda["codebook"]["authorial_recording"])
        self.assertEqual([form["key"] for form in recording["forms"]],
                         ["emerging_student_designed_inquiry", "sustained_independent_learning_path"])
        self.assertTrue(all(form["evidence_focus"] for form in recording["forms"]))
        self.assertFalse(recording["automatic_promotion"])
        self.assertIsNone(recording["fixed_duration_threshold"])
        self.assertIsNone(recording["numeric_rank"])
        self.assertTrue(recording["preserve_earlier_records"])
        self.assertTrue(recording["earlier_inquiry_reference_optional"])

    def test_decision_revision_does_not_approve_remaining_conceptual_criteria(self):
        book = build_research_agenda()["codebook"]
        self.assertEqual(book["previous_version"], "answerable-research/2026-09-14-draft1")
        self.assertNotEqual(book["version"], book["previous_version"])
        decision = next(d for d in book["open_decisions"] if d["id"] == "conceptual_indicators")
        self.assertEqual(decision["status"], "teacher_review_needed")

    def test_agency_is_open_contextual_index_not_ranking(self):
        rules = build_research_agenda()["codebook"]["interpretation_rules"]
        self.assertEqual(rules["unit"], "contextual_move_or_episode_not_a_type_of_student")
        self.assertTrue(rules["open_case_index"])
        self.assertTrue(rules["multiple_codes_allowed_with_separate_evidence"])
        self.assertTrue(rules["unclassified_description_allowed"])
        self.assertTrue(rules["bid_is_not_teacher_recognition"])
        for key in ("assigned_frame", "student_proposed_frame", "prompted_or_spontaneous",
                    "opportunity_to_express_move", "teacher_response"):
            self.assertIn(key, rules["context_fields"])

    def test_every_definition_has_source_locators_and_no_actual_student_cases(self):
        book = build_research_agenda()["codebook"]
        self.assertEqual(len(book["source"]["sha256"]), 64)
        for item in book["definitions"].values():
            self.assertTrue(item["source_locators"])
            self.assertTrue(item["counterexamples"])
        self.assertFalse(book["coding_enabled"])
