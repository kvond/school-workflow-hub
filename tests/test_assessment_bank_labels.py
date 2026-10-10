"""BANK term punctuation stays category-bound through packet validation."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import completion
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as ar
from test_assessment_records import packet, response
from test_completion_bank_labels import lesson, TERM, TERMS


ANSWER = "The sensor detects a change and the effector responds to the control center."


def capture_text(*, answer="", last_term=TERM, versioned=False):
    deck = lesson(answer=answer, last_term=last_term)
    if versioned:
        terms = (*TERMS[:-1], last_term)
        deck["slides"][0]["units"][0]["text"] = (
            "[[MARKER-INVENTORY:A02]]\nVERSION=A02-synthetic-r1\n"
            "NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=6\n"
            "NOTES_IDS=\nQ-EXPLAIN\nDRAFT_IDS=\nQ-EXPLAIN\nOPTIONAL_IDS=\n"
            "BANK_IDS=\n" + "\n".join(terms) + "\nCHECKPOINTS=UNVERIFIED")
    return "\n".join(unit["text"] for slide in deck["slides"] for unit in slide["units"])


def bank_packet(text, *, answer=""):
    item = packet()
    item["class_key"] = "anatomy"
    item["manifest"] = ar.derive_manifest(text)
    item["responses"] = [response("Q-EXPLAIN", category, "blank", "",
                                  conceptual="Evidence unavailable", explanation="Evidence unavailable")
                         for category in ("NOTES", "DRAFT")]
    for term in TERMS:
        completed = term == TERM and bool(answer)
        item["responses"].append(response(term, "BANK", "meaningful_attempt" if completed else "blank",
                                          answer if completed else "", conceptual="Developing" if completed else "Evidence unavailable",
                                          explanation="Names an idea" if completed else "Evidence unavailable"))
    return item


class AssessmentBankLabelTests(unittest.TestCase):
    def test_compact_versioned_and_census_paths_keep_all_six_terms(self):
        for versioned in (False, True):
            with self.subTest(versioned=versioned):
                text = capture_text(versioned=versioned)
                manifest = ar.derive_manifest(text)
                self.assertEqual(manifest["bank_ids"], list(TERMS))
                self.assertEqual(manifest["notes_ids"], ["Q-EXPLAIN"])
                self.assertEqual(manifest["draft_ids"], ["Q-EXPLAIN"])
                self.assertEqual(ar.census_manifest(text)["bank_ids"], list(TERMS))
                item = bank_packet(text)
                self.assertIs(ar.validate_packet(item, text), item)
                result = ar.completion(item)
                self.assertEqual(result["counts"]["BANK"], {"completed": 0, "required": 6, "due": True})
                self.assertEqual(result["scores"]["content_notes"]["exact_value"], "0")
                self.assertEqual(result["scores"]["revision"]["exact_value"], "0")

    def test_completed_sixth_bank_response_keeps_original_id_and_safe_evidence_key(self):
        text = capture_text(answer=ANSWER)
        item = bank_packet(text, answer=ANSWER)
        before = deepcopy(item)
        self.assertIs(ar.validate_packet(item, text), item)
        with patch.object(ar, "assess_completion", wraps=ar.assess_completion) as assess:
            result = ar.completion(item)
        assessed = assess.call_args.args[1]
        sixth = next(r for r in assessed if r.category == "BANK" and r.question_id == TERM)
        evidence = "sha256:" + item["source"]["sha256"][:32]
        self.assertEqual(sixth.evidence_reference, evidence + ":bank_" + content_hash(TERM)[:24])
        plain = next(r for r in assessed if r.category == "BANK" and r.question_id == "Negative feedback")
        self.assertEqual(plain.evidence_reference, evidence + ":Negative_feedback")
        self.assertEqual(result["scores"]["content_notes"]["exact_value"], "10/7")
        self.assertEqual(item, before)  # no assessment/identity/history rewriting

    def test_slash_labels_are_rejected_in_other_manifest_and_response_categories(self):
        text = capture_text(versioned=True)
        for key in ("notes_ids", "draft_ids", "optional_ids"):
            with self.subTest(key=key):
                item = bank_packet(text)
                item["manifest"][key] = [TERM]
                with self.assertRaisesRegex(ValueError, "invalid_assessment_manifest"):
                    ar.validate_packet(item, text)
        for category in ("NOTES", "DRAFT"):
            with self.subTest(category=category):
                item = bank_packet(text)
                item["responses"][-1]["category"] = category
                with self.assertRaisesRegex(ValueError, "invalid_assessment_response"):
                    ar.validate_packet(item, text)

    def test_unsupported_bank_paths_cannot_become_manifest_ids(self):
        for label in ("../card", "/home/card", "sensor/control", "sensor // control", "sensor / ",
                      "sensor / / control", "a" * 81, "a" * 76 + " / long"):
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "assessment_manifest_unparsed"):
                    ar.derive_manifest(capture_text(last_term=label))
                text = capture_text(versioned=True)
                item = bank_packet(text)
                item["manifest"]["bank_ids"][-1] = label
                with self.assertRaisesRegex(ValueError, "invalid_assessment_manifest"):
                    ar.validate_packet(item, text)

    def test_parser_groups_and_default_question_validation_remain_strict(self):
        self.assertEqual(ar._MARKER.groups, 2)
        tag = f"[[BANK:A02:{TERM}]]"
        self.assertEqual(ar._MARKER.fullmatch(tag).groups(), ("BANK", TERM))
        self.assertEqual(completion.MARKER.fullmatch(tag).groups(), ("BANK", "A02", TERM))
        self.assertFalse(ar._question_id(TERM))
        self.assertTrue(ar._question_id(TERM, category="BANK"))
        for category in ("NOTES", "DRAFT", "OPTIONAL"):
            self.assertFalse(ar._question_id(TERM, category=category))
            self.assertIsNone(ar._MARKER.fullmatch(f"[[{category}:A02:{TERM}]]"))
        self.assertIsNone(ar._MARKER.fullmatch(f"[[BANK:A/02:{TERM}]]"))


if __name__ == "__main__":
    unittest.main()
