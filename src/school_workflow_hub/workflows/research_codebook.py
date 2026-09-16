"""Source-grounded research interpretation draft; no student coding or IO.

Only paraphrased definitions, source locators and proposed indicators are kept
here. The manuscript, its student cases and operational records are not copied.
"""

CODEBOOK_VERSION = "answerable-research/2026-09-15-draft2"
SOURCE_SHA256 = "42d7040cb7e309b5c2a6564df3a3a4b392f9602a44723e01d0036f99c95455e1"
REVIEWED_AT = "2026-09-15T02:49:36Z"


def source_review():
    return {
        "source_reference": "book:full-revised-decision-draft:42d7040cb7e3",
        "title": "Answerable Teaching — Full Revised Decision Draft",
        "sha256": SOURCE_SHA256,
        "reviewed_at": REVIEWED_AT,
        "verification": "local_document_review",
        "scope": "Relevant definition, variation, growth-report and case-index passages; not a full editorial audit.",
        "locator_method": "One-based document.xml body descendant paragraph order, including table cells and empty paragraphs; rendered PDF page numbers are this local render only.",
        "teacher_approved": False,
        "older_research_document_reviewed": False,
    }


def codebook_draft():
    """Return fresh review-only proposals, explicitly distinct from quotations."""
    return {
        "version": CODEBOOK_VERSION,
        "previous_version": "answerable-research/2026-09-14-draft1",
        "status": "source_grounded_draft_awaiting_teacher_review",
        "source": source_review(),
        "criteria_origin": "assistant_operational_proposals_grounded_in_book_not_a_published_rubric",
        "numeric_scale": None,
        "coding_enabled": False,
        "definitions": {
            "conceptual_understanding": {
                "definition": "The learner can discern and reason with a concept's critical aspects and relationships, rather than only recognize its words or representations, and can use that understanding in explanations, contrasts and changed cases.",
                "definition_kind": "synthesis_of_book_passages_not_a_single_explicit_definition",
                "source_locators": ["P573-P585: The problem biology poses; Variation Theory; What-if questions",
                                    "P593: thinking versus question wording", "P605: Critical aspects",
                                    "P640-P647: Feedback; Growth made visible; Lingering"],
                "indicators": [
                    ("CU1", "Discerns the relevant critical aspect and distinguishes it from a surface feature.", "P575-P582; P605"),
                    ("CU2", "Explains a relationship or system dynamic in the learner's own reasoning, with disciplinary evidence considered separately.", "P574-P575; P585-P586"),
                    ("CU3", "Uses a distinction to interpret a contrast or new case and notices where a prior generalization fails.", "P580-P585; P593"),
                    ("CU4", "Predicts consequences of a changed condition and gives reasons, including limitations or alternative consequences when visible.", "P585-P586"),
                    ("CU5", "Revises an identifiable conception across comparable work, explaining what changed and why when that evidence is available.", "P640-P647"),
                ],
                "counterexamples": ["A copied definition or diagram label without visible reasoning.",
                                    "Longer wording, more filled boxes or higher completion credit alone.",
                                    "A corrected claim with no evidence of the learner's reasoning: correctness may be observed, but conceptual growth remains uncertain."],
            },
            "engaged_agency": {
                "definition": "The learner brings their own effort to understand and their own purposes or way of thinking to the assigned disciplinary task while remaining within the lesson frame; the thinking may be partial or scientifically mistaken.",
                "definition_kind": "paraphrase_of_explicit_book_definition",
                "source_locators": ["Chapter 2 P176; P215-P217: Engaged agency"],
                "indicators": [
                    ("ENG1", "Offers a self-originated explanation, method, question or representation inside the assigned task; record evidence for origination instead of inferring it from style.", "P176; P215-P217"),
                    ("ENG2", "Describes a difficulty, tests an idea or requests a meaningful change in representation while pursuing the lesson's object.", "P217"),
                ],
                "counterexamples": ["Compliance, completion, polished language or correct recall alone.",
                                    "Writing I think without an identifiable reasoning move.",
                                    "Silence or missing writing does not establish lack of engagement."],
            },
            "epistemic_agency": {
                "definition": "The learner makes a bid to reshape the problem, the question, what counts as a solution or the kind of knowledge being sought, rather than only reason within the teacher's existing frame.",
                "definition_kind": "paraphrase_of_explicit_book_definition",
                "source_locators": ["Chapter 1 P90: bid versus recognition", "Chapter 2 P177; P265-P266: Epistemic agency"],
                "indicators": [
                    ("EPI1", "Reframes what is being asked or challenges what counts as a relevant solution or account; document the original and proposed frames.", "P177; P265-P266"),
                    ("EPI2", "Proposes an alternative question or counterclaim that changes the knowledge task, not merely its answer.", "P177; P266"),
                ],
                "counterexamples": ["A partial or incorrect answer by itself.",
                                    "A strong evidence-based answer or an alternative conclusion within the unchanged task.",
                                    "Performing an assigned critique step without a further identifiable reframing move."],
            },
            "authorial_agency": {
                "definition": "The learner authors purposes and a direction of study of their own, deciding what is worth pursuing and how to pursue it rather than merely choosing among teacher-created options.",
                "definition_kind": "shared_core_of_book_definitions_with_teacher_approved_separate_forms",
                "source_locators": ["Chapter 2 P179; P274-P278: Authorial agency",
                                    "Chapter 8 P850-P855: Authorial agency and intellectual authorship"],
                "indicators": [
                    ("AUT1", "Articulates a learner-originated purpose and direction of study beyond selecting an assigned option; record a candidate bid, not a settled trait.", "P274-P275"),
                    ("AUT2", "Documents decisions about inquiry path, methods or standards, with linked follow-through before claiming a sustained self-authored course of study.", "P276-P278; P850-P851"),
                ],
                "counterexamples": ["Using one's own words or revising an assigned answer alone.",
                                    "Choosing a topic from a menu or completing an extra assigned activity.",
                                    "An isolated interesting question without evidence of an authored purpose and path."],
            },
        },
        "approved_decisions": [
            {"id": "authorial_scope", "status": "teacher_approved",
             "approval_source": "explicit_teacher_confirmation_in_conversation",
             "question": "Should the research index record emerging self-authored projects within class separately from sustained independent learning paths?",
             "evidence": ["Chapter 2 P274-P275 permits learner-created curricular purposes inside school.",
                          "Chapter 8 P850-P851 emphasizes an entire self-directed path beyond the provided frame."],
             "rule": "Record emerging student-designed inquiries separately from sustained independent learning paths; neither is a score or fixed learner label."},
        ],
        "authorial_recording": {
            "approval_state": "teacher_approved_separation_only",
            "forms": [
                {"key": "emerging_student_designed_inquiry", "label": "Emerging student-designed inquiry",
                 "evidence_focus": "Learner-originated purpose or question with a proposed direction of study; sustained follow-through is not yet established."},
                {"key": "sustained_independent_learning_path", "label": "Sustained independent learning path",
                 "evidence_focus": "Linked evidence over time of a learner-authored purpose, decisions about methods or direction, and follow-through."},
            ],
            "insufficient_evidence": "unclassified_description_pending_review",
            "record_fields": ["authorial_form", "evidence_references", "teacher_review_state", "earlier_inquiry_reference"],
            "earlier_inquiry_reference_optional": True,
            "automatic_promotion": False,
            "fixed_duration_threshold": None,
            "numeric_rank": None,
            "preserve_earlier_records": True,
            "approval_does_not_enable_student_coding": True,
        },
        "open_decisions": [
            {"id": "conceptual_indicators", "status": "teacher_review_needed",
             "question": "Accept or revise CU1-CU5 as an open observation guide, with lesson-specific critical aspects supplied separately.",
             "evidence": ["P602 mentions four stages without enumerating them in the following passage.",
                          "P605 describes critical aspects; P645 describes longitudinal quoted evidence."],
             "interim_rule": "No invented four-stage scale, fixed cutoff or composite growth score."},
        ],
        "interpretation_rules": {
            "unit": "contextual_move_or_episode_not_a_type_of_student",
            "open_case_index": True,
            "multiple_codes_allowed_with_separate_evidence": True,
            "unclassified_description_allowed": True,
            "context_fields": ["assigned_frame", "student_proposed_frame", "origination_evidence",
                               "object_of_action", "answerability", "capability_evidence",
                               "decision_making", "opportunity_to_express_move",
                               "prompted_or_spontaneous", "teacher_response", "follow_through"],
            "bid_is_not_teacher_recognition": True,
            "intellectual_authorship_is_not_authorial_agency": True,
            "engagement_phases_not_ranked_or_assigned_from_silence": True,
            "disciplinary_fit_separate_from_agency": True,
            "democratic_agency": "Retain in unclassified context if encountered; not added as a fourth tracking target without teacher direction.",
            "source_locators": ["Chapter 2 P161-P170; P231; P279", "Chapter 8 P855",
                                "Chapter 11 P1032", "Chapter 12 P1071; P1097-P1100"],
        },
        "source_cautions": [
            "The manuscript is a decision draft with unresolved placeholders and duplicate chapter numbering; use headings and paragraph locators, not guessed chapter numbers.",
            "The book's style-based copying discussion is not an automated AI-authorship detector; no penalty or provenance claim follows from writing style.",
            "Book descriptions of grading do not replace the teacher's separately approved completion-credit rules.",
            "Conceptual-science correctness still needs lesson-specific evidence and teacher review; acknowledging a student's agency is not endorsement of every claim.",
        ],
    }
