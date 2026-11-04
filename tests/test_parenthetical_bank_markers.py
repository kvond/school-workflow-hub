"""Synthetic BANK punctuation: exact identities and no neighboring credit."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from hub_v2 import completion, embedded_code_repair, native_teacher_source
from school_workflow_hub.core import markers
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as ar, deck_release
from test_assessment_records import packet, response
from test_deck_release import good_slides
from test_embedded_code_repair import pptx, shape


TERM = "signal (alpha, beta, gamma)"
TERMS = ("source", "current", "resistance", "charge", "voltage", TERM)
FILE = "FictionalBankPresentation123456"
ANSWER = "These three features represent distinct stages of the observed signal."


def native(*, term=TERM, answer="", neighbor="", coded_label=False):
    terms = (*TERMS[:-1], term)
    header = ("[[MARKER-INVENTORY:C17]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=6 "
              "NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=" + ",".join(terms))
    pages = [{"objectId": "inventory-page", "pageElements": [shape("inventory", header, 0, width=650)]}]
    for index in range(0, 6, 2):
        elements = []
        for offset, label in enumerate(terms[index:index + 2]):
            position = index + offset
            x = 20 + 350 * offset
            tag = f"[[BANK:C17:{label}]]"
            elements += [shape(f"label{position}", label + ("\n" + tag if coded_label else ""), 40,
                               x=x, width=280, height=35),
                         shape(f"answer{position}", answer if position == 5 else neighbor if position == 4 else "",
                               100, x=x, width=280, height=90)]
            if not coded_label:
                elements.append(shape(f"code{position}", tag, 430 + 20 * offset, x=750, width=30))
        pages.append({"objectId": f"bank-page{index}", "pageElements": elements})
    # Uncoded practice text never becomes a BANK response.
    pages.append({"objectId": "practice", "pageElements": [shape("practice-answer", "An unscored practice prediction.", 80)]})
    return {"presentationId": FILE,
            "pageSize": {"width": {"magnitude": 720, "unit": "PT"},
                         "height": {"magnitude": 405, "unit": "PT"}}, "slides": pages}


def parse(value, reader):
    if reader == "native":
        return native_teacher_source.read(value, presentation_id=FILE, revision="7", source_role="student")
    return completion.read_deck(pptx(value))


def text(deck):
    return "\n".join(u["text"] for s in deck["slides"] for u in s["units"])


def reviewed(source, answer=""):
    item = packet()
    item["manifest"] = ar.derive_manifest(source)
    item["source"]["sha256"] = hashlib.sha256(source.encode()).hexdigest()
    item["responses"] = [response(term, "BANK", "meaningful_attempt" if term == TERM and answer else "blank",
                                  answer if term == TERM else "", conceptual="Evidence unavailable",
                                  explanation="Evidence unavailable") for term in TERMS]
    return item


class ParentheticalBankTests(unittest.TestCase):
    def test_exact_groups_bounds_and_category_restrictions(self):
        for term in (TERM, "a" * 73 + " (b, c)", "sensor / control center / effector", "Simple term"):
            tag = f"[[BANK:C17:{term}]]"
            self.assertEqual(completion.MARKER.fullmatch(tag).groups(), ("BANK", "C17", term))
            self.assertEqual(ar._MARKER.fullmatch(tag).groups(), ("BANK", term))
            self.assertTrue(ar._question_id(term, category="BANK"))
        self.assertEqual((completion.MARKER.groups, ar._MARKER.groups), (3, 2))
        for category in ("NOTES", "DRAFT", "OPTIONAL"):
            self.assertIsNone(completion.MARKER.fullmatch(f"[[{category}:C17:{TERM}]]"))
            self.assertFalse(ar._question_id(TERM, category=category))
        self.assertIsNone(completion.MARKER.fullmatch(f"[[BANK:C/17:{TERM}]]"))
        self.assertIsNone(completion.MARKER.fullmatch(f"[[BANK:{'C' * 41}:{TERM}]]"))

    def test_all_inventory_and_manifest_paths_keep_six_literal_ids(self):
        for reader in ("native", "export"):
            with self.subTest(reader=reader):
                deck = parse(native(), reader)
                ready = completion.readiness(deck)
                self.assertEqual((ready["status"], ready["counts"]["BANK"]), ("READY", 6))
                self.assertEqual(completion.inventory(deck)[1]["BANK"], list(TERMS))
                self.assertEqual(completion.coding_issues(deck), {"missing": [], "extra": []})
                declared = embedded_code_repair._declared_inventory(ready, completion.markers(deck), deck)
                self.assertEqual(declared["BANK"], list(TERMS))
                source = text(deck)
                self.assertEqual(ar.derive_manifest(source)["bank_ids"], list(TERMS))
                self.assertEqual(ar.census_manifest(source)["bank_ids"], list(TERMS))
                versioned = ("[[MARKER-INVENTORY:C17]]\nVERSION=synthetic-r7\n"
                             "NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=6\nNOTES_IDS=\nDRAFT_IDS=\n"
                             "OPTIONAL_IDS=\nBANK_IDS=\n" + "\n".join(TERMS) + "\n\n" +
                             "\n".join(f"[[BANK:C17:{term}]]" for term in TERMS))
                self.assertEqual(ar.derive_manifest(versioned)["bank_ids"], list(TERMS))
                omitted = source.replace(" BANK_IDS=" + ",".join(TERMS), "")
                self.assertEqual(ar.derive_manifest(omitted)["bank_ids"], list(TERMS))

    def test_native_export_one_to_one_binding_and_blank_neighbor(self):
        for teacher_reader in ("native", "export"):
            for student_reader in ("native", "export"):
                for coded_label in (False, True):
                    for answer, neighbor in ((ANSWER, ""), ("", "A distinct voltage definition."),
                                             (ANSWER, "A distinct voltage definition."), ("", "")):
                        with self.subTest(teacher=teacher_reader, student=student_reader,
                                          coded_label=coded_label, answer=bool(answer), neighbor=bool(neighbor)):
                            template = parse(native(coded_label=coded_label), teacher_reader)
                            student = parse(native(answer=answer, neighbor=neighbor, coded_label=coded_label), student_reader)
                            original = deepcopy((student, template))
                            result = completion.assess(student, template, found_codes_only=True)
                            self.assertIsNone(result["note"])
                            banks = {i["id"]: i for i in result["items"] if i["category"] == "BANK"}
                            self.assertEqual(set(banks), set(TERMS))
                            self.assertEqual(banks[TERM]["answer"], answer)
                            self.assertEqual(banks["voltage"]["answer"], neighbor)
                            self.assertEqual(result["counts"]["BANK"], {"done": bool(answer) + bool(neighbor), "required": 6})
                            self.assertEqual((student, template), original)

    def test_malformed_source_or_copy_cannot_transfer_credit_to_its_neighbor(self):
        for bad in ("signal (alpha,, beta)", "signal (alpha, beta", "signal alpha, beta)",
                    "signal ((alpha, beta))", "signal (alpha, )", "signal (alpha, beta )",
                    "signal (alpha,beta)", "signal (alpha; beta)", "../term", "signal\n(alpha, beta)",
                    "signal [alpha, beta]", "a" * 74 + " (b, c)"):
            with self.subTest(bad=bad):
                self.assertIsNone(completion.MARKER.fullmatch(f"[[BANK:C17:{bad}]]"))
                self.assertFalse(ar._question_id(bad, category="BANK"))
                for reader in ("native", "export"):
                    malformed = parse(native(term=bad, answer=ANSWER), reader)
                    good = parse(native(), reader)
                    check = completion.student_readiness(malformed, require_complete_codes=False)
                    self.assertEqual(check["status"], "NOT_READY")
                    self.assertIn("malformed_feedback_marker", check["issues"])
                    for student, template in ((malformed, good), (malformed, malformed), (good, malformed)):
                        with patch.object(completion, "score", side_effect=AssertionError("Malformed source must hold")):
                            result = completion.assess(student, template, found_codes_only=True)
                        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))
                        self.assertIn("malformed", result["note"])
                        self.assertFalse(any(i["answer"] == ANSWER for i in result["items"]))
                    with self.assertRaisesRegex(ValueError, "assessment_manifest_unparsed"):
                        ar.derive_manifest(text(malformed))
        self.assertTrue(markers.malformed_bank_marker("[[BANK:C17:unknown@term]]"))

    def test_packet_evidence_identity_duplicates_and_changed_source_remain_checked(self):
        source = text(parse(native(answer=ANSWER), "export"))
        item = reviewed(source, ANSWER)
        original = deepcopy(item)
        self.assertIs(ar.validate_packet(item, source), item)
        with patch.object(ar, "assess_completion", wraps=ar.assess_completion) as assess:
            ar.completion(item)
        target = next(r for r in assess.call_args.args[1] if r.question_id == TERM)
        self.assertEqual(target.evidence_reference, "sha256:" + item["source"]["sha256"][:32] +
                         ":bank_" + content_hash(TERM)[:24])
        self.assertEqual(item, original)
        with self.assertRaisesRegex(ValueError, "assessment_evidence_not_in_source"):
            ar.validate_packet(item, source.replace(ANSWER, "Different synthetic writing."))
        for key in ("manifest", "responses"):
            changed = deepcopy(item)
            if key == "manifest":
                changed[key]["bank_ids"].append(TERM)
            else:
                changed[key].append(deepcopy(changed[key][-1]))
            with self.assertRaises(ValueError):
                ar.validate_packet(changed, source)
        deck = parse(native(), "native")
        deck["slides"][-2]["units"].append(deepcopy(deck["slides"][-2]["units"][-1]))
        self.assertIn("duplicate_feedback_marker", completion.readiness(deck)["issues"])

    def test_release_list_grammar_and_existing_revision_guards(self):
        slides = tuple(s.replace("glucose", TERM) for s in good_slides())
        self.assertEqual(deck_release.validate_release(slides, revision="7", prompt0="p0", prompt2="p2")["status"], "READY")
        bad = tuple(s.replace(TERM, "signal (alpha,, beta)") for s in slides)
        self.assertIn("malformed_feedback_marker", deck_release.validate_release(bad, revision="7")["issues"])
        for field in ("NOTES_IDS", "DRAFT_IDS", "OPTIONAL_IDS"):
            self.assertNotEqual(deck_release._compact_ids(f" {field}={TERM}")[field], [TERM])
        self.assertEqual(markers.split_ids(TERM + ",voltage", category="BANK"), [TERM, "voltage"])


if __name__ == "__main__":
    unittest.main()
