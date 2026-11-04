"""Synthetic numbered BANK terms preserve literal IDs across shared readers."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from hub_v2 import completion as c, embedded_code_repair
from school_workflow_hub.core import markers, runs
from school_workflow_hub.workflows import assessment_records as ar, deck_release
from test_assessment_records import packet, response
from test_deck_release import good_slides
from test_parenthetical_bank_markers import native as base_native, parse, text, TERMS as OLD_TERMS, ANSWER

TERMS = ("1. alpha term", "2. beta term", "3. gamma term", "4. delta term", "5. epsilon term",
         "6. paired term (all six pairs)")


def native(*, last=TERMS[-1], answered=None):
    value = base_native()
    terms = (*TERMS[:-1], last)
    for page in value["slides"]:
        for element in page["pageElements"]:
            run = element["shape"]["text"]["textElements"][0]["textRun"]
            body = run["content"].rstrip("\n")
            if element["objectId"] == "inventory":
                body = body.split("BANK_IDS=", 1)[0] + "BANK_IDS=" + ",".join(terms)
            for old, term in zip(OLD_TERMS, terms):
                if body == old: body = term
                if body == f"[[BANK:C17:{old}]]": body = f"[[BANK:C17:{term}]]"
            if answered is not None and element["objectId"] == f"answer{answered}": body = ANSWER
            run["content"] = body + "\n"
    return value


class NumberedBankTests(unittest.TestCase):
    def test_six_literal_numbered_terms_survive_every_reader_inventory_and_manifest(self):
        for reader in ("native", "export"):
            with self.subTest(reader=reader):
                deck = parse(native(), reader)
                ready = c.readiness(deck)
                self.assertEqual((ready["status"], ready["counts"]["BANK"]), ("READY", 6))
                self.assertEqual([m["id"] for m in c.markers(deck)], list(TERMS))
                self.assertEqual(c.inventory(deck)[1]["BANK"], list(TERMS))
                self.assertEqual(embedded_code_repair._declared_inventory(ready, c.markers(deck), deck)["BANK"], list(TERMS))
                source = text(deck)
                self.assertEqual(ar.derive_manifest(source)["bank_ids"], list(TERMS))
                self.assertEqual(ar.census_manifest(source)["bank_ids"], list(TERMS))
                versioned = ("[[MARKER-INVENTORY:C17]]\nVERSION=synthetic-numbered-r1\n"
                             "NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=6\nNOTES_IDS=\nDRAFT_IDS=\nOPTIONAL_IDS=\nBANK_IDS=\n"
                             + "\n".join(TERMS) + "\n\n" + "\n".join(f"[[BANK:C17:{t}]]" for t in TERMS))
                self.assertEqual(ar.derive_manifest(versioned)["bank_ids"], list(TERMS))

    def test_isolated_native_export_responses_keep_all_six_exact_identities(self):
        for teacher in ("native", "export"):
            for student in ("native", "export"):
                for index in range(6):
                    with self.subTest(teacher=teacher, student=student, index=index):
                        original = parse(native(), teacher)
                        submitted = parse(native(answered=index), student)
                        before = deepcopy((submitted, original))
                        result = c.assess(submitted, original, found_codes_only=True)
                        self.assertIsNone(result["note"])
                        self.assertEqual([(i["id"], i["answer"]) for i in result["items"] if i["answer"]], [(TERMS[index], ANSWER)])
                        self.assertEqual(result["counts"]["BANK"], {"done": 1, "required": 6})
                        self.assertEqual((submitted, original), before)
                blank = c.assess(parse(native(), student), parse(native(), teacher), found_codes_only=True)
                self.assertEqual(blank["counts"]["BANK"], {"done": 0, "required": 6})

    def test_bounded_bank_only_grammar_and_malformed_terms_fail_closed(self):
        for label in ("7. synthetic position", "8. paired term (all six pairs)", "paired term (all six pairs)",
                      "9. signal (alpha, beta, gamma)", "9. sensor / control center / effector", "9. " + "a" * 73 + " (b)"):
            self.assertTrue(ar._question_id(label, category="BANK"))
            self.assertEqual(c.MARKER.fullmatch(f"[[BANK:C17:{label}]]").groups(), ("BANK", "C17", label))
            self.assertEqual(ar._MARKER.fullmatch(f"[[BANK:C17:{label}]]").groups(), ("BANK", label))
            for category in ("NOTES", "DRAFT", "OPTIONAL"):
                self.assertFalse(ar._question_id(label, category=category))
                self.assertIsNone(c.MARKER.fullmatch(f"[[{category}:C17:{label}]]"))
        invalid = ("0. term", "01. term", "1000. term", "7.term", "7.  term", "7. ../term", "../term", "/tmp/term",
                   "https://example.invalid/term", "term.name", "7. term.", "7. term ()", "7. term ((pairs))",
                   "7. term (pairs", "7. term pairs)", "7. term (all; pairs)", "7. term (alpha,, beta)",
                   "7. term (alpha,beta)", "7. term (alpha, )", "7. term (all pairs) extra", "9. " + "a" * 74 + " (b)")
        for label in invalid:
            with self.subTest(label=label):
                self.assertIsNone(c.MARKER.fullmatch(f"[[BANK:C17:{label}]]"))
                self.assertFalse(ar._question_id(label, category="BANK"))
                for reader in ("native", "export"):
                    deck = parse(native(last=label, answered=5), reader)
                    ready = c.student_readiness(deck, require_complete_codes=False)
                    self.assertEqual(ready["status"], "NOT_READY")
                    self.assertIn("malformed_feedback_marker", ready["issues"])
                    result = c.assess(deck, parse(native(last=label), reader), found_codes_only=True)
                    self.assertIsNone(result["notes_score"])
                    self.assertFalse(any(i["answer"] == ANSWER for i in result["items"]))
                    with self.assertRaisesRegex(ValueError, "assessment_manifest_unparsed"): ar.derive_manifest(text(deck))

    def test_packet_evidence_refs_preserve_literal_id_and_source_validation(self):
        source = text(parse(native(answered=5), "export"))
        item = packet(); item["source"]["sha256"] = hashlib.sha256(source.encode()).hexdigest()
        item["manifest"] = ar.derive_manifest(source)
        item["responses"] = [response(term, "BANK", "meaningful_attempt" if i == 5 else "blank", ANSWER if i == 5 else "",
                                      conceptual="Evidence unavailable", explanation="Evidence unavailable") for i, term in enumerate(TERMS)]
        before = deepcopy(item)
        self.assertIs(ar.validate_packet(item, source), item)
        with patch.object(ar, "assess_completion", wraps=ar.assess_completion) as assess:
            ar.completion(item)
        for evidence in assess.call_args.args[1]:
            self.assertEqual(evidence.evidence_reference, "sha256:" + item["source"]["sha256"][:32] + ":bank_" + runs.content_hash(evidence.question_id)[:24])
        self.assertEqual(item, before)
        with self.assertRaisesRegex(ValueError, "assessment_evidence_not_in_source"):
            ar.validate_packet(item, source.replace(ANSWER, "Different synthetic answer."))
        item["manifest"]["bank_ids"].append(TERMS[0])
        with self.assertRaises(ValueError): ar.validate_packet(item, source)

    def test_release_inventory_and_duplicate_cycle_guards_still_apply(self):
        slides = tuple(s.replace("glucose", TERMS[-1]) for s in good_slides())
        self.assertEqual(deck_release.validate_release(slides, revision="7", prompt0="p0", prompt2="p2")["status"], "READY")
        deck = parse(native(), "native")
        duplicate = deepcopy(deck["slides"][-2]["units"][-1]); duplicate["key"] += "-duplicate"
        deck["slides"][-2]["units"].append(duplicate)
        self.assertIn("duplicate_feedback_marker", c.readiness(deck)["issues"])
        self.assertIsNone(c.MARKER.fullmatch(f"[[BANK:C/17:{TERMS[-1]}]]"))
        self.assertEqual(markers.split_ids(",".join(TERMS), category="BANK"), list(TERMS))


if __name__ == "__main__":
    unittest.main()
