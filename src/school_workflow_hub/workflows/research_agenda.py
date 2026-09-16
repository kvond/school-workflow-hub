"""Research-page contract, not a codebook, student assessor or collection job.

Definitions are grounded in the supplied book; proposed observation indicators
await teacher review. This module produces no student coding or growth scores.
Private evidence must be resolved by the separately authorized host.
"""
from school_workflow_hub.workflows.research_codebook import codebook_draft


def build_research_agenda():
    """Return a fresh, data-free plan for the existing Research Data page."""
    dimensions = (
        ("conceptual_understanding", "Conceptual understanding",
         "Are students getting better at conceptualizing science concepts?", "primary"),
        ("engaged_agency", "Engaged agency",
         "What evidence of engaged agency appears in students' written work?", "secondary"),
        ("epistemic_agency", "Epistemic agency",
         "What evidence of epistemic agency appears in students' written work?", "secondary"),
        ("authorial_agency", "Authorial agency",
         "What evidence of authorial agency appears in students' written work?", "secondary"),
    )
    draft = codebook_draft()
    return {
        "schema_version": "research-agenda/v1",
        "status": draft["status"],
        "last_checked_at": draft["source"]["reviewed_at"],
        "evidence": [draft["source"]],
        "next_action": "Authorial separation approved. Review the remaining source-grounded criteria, including proposed conceptual indicators; student coding remains disabled.",
        "source_of_priority": "teacher_instruction",
        "source_documents_verified": True,
        "source_verification_scope": "attached_book_relevant_passages_only_not_live_school_or_older_research_documents",
        "codebook": draft,
        "dimensions": [
            {"key": key, "label": label, "question": question, "priority": priority,
             "definition": draft["definitions"][key]["definition"],
             "definition_source_reference": draft["source"]["source_reference"],
             "codebook_version": draft["version"],
             "observable_criteria": [{"id": ident, "proposal": indicator, "source_locator": locator,
                                      "status": "proposed_not_teacher_approved"}
                                     for ident, indicator, locator in draft["definitions"][key]["indicators"]],
             "examples": [], "counterexamples": list(draft["definitions"][key]["counterexamples"]),
             "status": "source_grounded_draft_awaiting_teacher_review"}
            for key, label, question, priority in dimensions
        ],
        "finding_contract": {
            "conditional_fields": {"authorial_agency": draft["authorial_recording"]},
            "required_fields": [
                "finding_id", "learner_pseudonym", "course_reference", "concept_reference",
                "dimension", "codebook_version", "criterion_reference", "evidence_references",
                "interpretation", "uncertainty", "alternative_explanations",
                "teacher_review_state", "review_reference", "supersedes_finding_id",
            ],
            "evidence_fields": [
                "evidence_id", "exact_student_excerpt", "source_document_reference",
                "source_revision", "slide_or_response_locator", "assignment_reference",
                "prompt_reference", "response_stage", "writing_date", "writing_date_evidence",
                "observed_at", "instructional_timing", "instructional_timing_evidence",
                "known_support_or_ai_assistance", "source_match_review_reference",
            ],
            "nullable_when_unknown": [
                "writing_date", "writing_date_evidence", "instructional_timing_evidence",
                "known_support_or_ai_assistance", "review_reference", "supersedes_finding_id",
            ],
            "unknown_timing_label": "unknown",
            "review_states": ["provisional", "accepted", "rejected", "needs_more_evidence"],
            "history_policy": "append_new_version_preserve_prior_evidence_and_reviews",
            "excerpt_policy": "Exact source-matched words only; no invented or silently rewritten quotations.",
        },
        "comparison_contract": {
            "modes": [
                {"key": "within_lesson_revision", "label": "First answer to revised answer",
                 "requires": ["same_learner", "same_prompt_and_concept", "distinct_response_evidence",
                              "verified_response_order", "instruction_and_support_context"]},
                {"key": "across_lessons", "label": "Conceptual development across lessons",
                 "requires": ["same_learner", "distinct_assignments", "verified_writing_order",
                              "shared_concept_and_criterion", "reviewed_task_comparability",
                              "instruction_and_support_context"]},
            ],
            "required_fields": ["earlier_evidence_reference", "later_evidence_reference",
                                "comparison_mode", "comparability_review_reference",
                                "codebook_version", "criterion_reference", "interpretation",
                                "uncertainty", "teacher_review_state"],
            "missing_earlier_evidence": "insufficient_evidence_of_growth",
            "unknown_writing_order": "change_direction_unverified",
            "noncomparable_tasks": "not_comparable",
            "observed_at_is_not_writing_date": True,
            "single_excerpt_can_support": "provisional_current_work_observation_only",
            "aggregate_requirements": ["eligible_comparable_case_count", "missing_or_excluded_case_count",
                                       "codebook_version", "review_state", "comparison_mode"],
        },
        "safeguards": {
            "agency_is_separate_from_conceptual_understanding": True,
            "completion_is_not_conceptual_growth": True,
            "longer_answers_are_not_automatically_better": True,
            "agency_evidence_is_not_a_stable_learner_trait": True,
            "later_filled_boxes_do_not_prove_preinstruction_thinking": True,
            "no_automatic_causal_or_ai_authorship_claims": True,
            "completion_grades_and_grade_history_unchanged": True,
            "requires_separate_research_authorization": True,
            "private_excerpts_never_in_public_html_or_repository": True,
        },
        "coding_supported": False,
        "collection_supported": False,
        "external_writes": 0,
    }
