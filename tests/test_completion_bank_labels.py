"""Printed BANK term labels must not broaden response ids or source readiness."""
from copy import deepcopy
import unittest

from hub_v2 import completion
from test_hub_v2 import pptx


TERM = "sensor / control center / effector"
TERMS = ("Anatomy", "Physiology", "Homeostasis", "Negative feedback", "Positive feedback", TERM)


def lesson(*, answer="", last_term=TERM, inventory_slide=1, notes_count=1):
    terms = (*TERMS[:-1], last_term)
    header = (f"[[MARKER-INVENTORY:A02]] NOTES={notes_count} DRAFT=1 OPTIONAL=0 BANK_TERMS=6 "
              "NOTES_IDS=Q-EXPLAIN DRAFT_IDS=Q-EXPLAIN OPTIONAL_IDS=- BANK_IDS=" + ",".join(terms))
    slides = [[("inventory", header, (0, 0, 900, 80))], [
        ("question", "Explain the control process.", (20, 20, 800, 40)),
        ("first", "Your first answer", (20, 80, 400, 30)),
        ("first answer", "", (20, 120, 400, 120)),
        ("revised", "Your revised answer", (20, 280, 400, 30)),
        ("revised answer", "", (20, 320, 400, 120)),
        ("notes code", "[[NOTES:A02:Q-EXPLAIN]]", (900, 500, 80, 10)),
        ("draft code", "[[DRAFT:A02:Q-EXPLAIN]]", (900, 520, 80, 10))]]
    for i, term in enumerate(terms):
        slides.append([
            ("term", term, (20, 20, 800, 40)),
            ("answer", answer if i == 5 else "", (20, 100, 800, 180)),
            ("bank code", f"[[BANK:A02:{term}]]", (900, 500, 80, 10))])
    if inventory_slide == 2:
        slides.insert(0, [("title", "Introduction to anatomy", (20, 20, 900, 80))])
    return completion.read_deck(pptx(slides))


class BankLabelTests(unittest.TestCase):
    def test_sixth_slash_term_matches_the_teacher_inventory_and_keeps_exact_id(self):
        deck = lesson()
        banks = [m for m in completion.markers(deck) if m["category"] == "BANK"]
        self.assertEqual([m["id"] for m in banks], list(TERMS))
        self.assertEqual({m["code"] for m in banks}, {"A02"})
        self.assertEqual(completion.MARKER.groups, 3)
        code, required, basis, note = completion.inventory(deck)
        self.assertEqual((code, basis, note), ("A02", "header", None))
        self.assertEqual(required["BANK"], list(TERMS))
        self.assertEqual(completion.coding_issues(deck), {"missing": [], "extra": []})
        ready = completion.readiness(deck)
        self.assertEqual(ready["counts"]["BANK"], 6)
        self.assertEqual(ready["status"], "READY")

    def test_unchanged_template_is_still_zero_and_does_not_credit_printed_term(self):
        template = lesson()
        result = completion.assess(deepcopy(template), template)
        self.assertEqual(result["counts"]["BANK"], {"done": 0, "required": 6})
        self.assertEqual((result["notes_score"], result["revision_score"]), (0.0, 0.0))
        sixth = next(i for i in result["items"] if i["category"] == "BANK" and i["id"] == TERM)
        self.assertEqual((sixth["status"], sixth["answer"]), ("blank", ""))
        self.assertEqual(result["uncoded"], [])

    def test_new_answer_is_associated_only_with_the_sixth_bank_card(self):
        answer = "The sensor detects a change and the effector responds to the control center."
        result = completion.assess(lesson(answer=answer), lesson())
        banks = [i for i in result["items"] if i["category"] == "BANK"]
        self.assertEqual([(i["id"], i["status"]) for i in banks],
                         [(t, "done" if t == TERM else "blank") for t in TERMS])
        self.assertEqual(banks[-1]["answer"], answer)
        self.assertEqual(result["counts"]["BANK"], {"done": 1, "required": 6})
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
        self.assertEqual((result["notes_score"], result["revision_score"]), (1.4, 0.0))

    def test_bank_fix_does_not_clear_later_inventory_or_real_notes_count_holds(self):
        for deck, issue in [(lesson(inventory_slide=2), "marker_inventory_not_on_first_slide"),
                            (lesson(notes_count=2), "marker_inventory_count_mismatch")]:
            with self.subTest(issue=issue):
                ready = completion.readiness(deck)
                self.assertEqual(ready["counts"]["BANK"], 6)
                self.assertEqual(ready["status"], "NOT_READY")
                self.assertIn(issue, ready["issues"])

    def test_other_categories_keep_their_existing_id_grammar(self):
        for category in ("NOTES", "DRAFT", "OPTIONAL"):
            with self.subTest(category=category):
                self.assertIsNone(completion.MARKER.fullmatch(f"[[{category}:A02:{TERM}]]"))
                match = completion.MARKER.fullmatch(f"[[{category}:A02:Q-EXPLAIN_2]]")
                self.assertEqual(match.groups(), (category, "A02", "Q-EXPLAIN_2"))
        self.assertIsNone(completion.MARKER.fullmatch(f"[[BANK:A/02:{TERM}]]"))

    def test_unsupported_labels_and_paths_remain_unparsed_and_inventory_held(self):
        invalid = ("../card", "/home/card", "sensor/control", "sensor // control", "sensor / ",
                   "sensor / / control", "https://example.invalid/card", "sensor; control", "sensor\n/ control",
                   "a" * 81, "a" * 76 + " / long")
        for label in invalid:
            with self.subTest(label=label):
                self.assertIsNone(completion.MARKER.fullmatch(f"[[BANK:A02:{label}]]"))
                ready = completion.readiness(lesson(last_term=label))
                self.assertEqual(ready["status"], "NOT_READY")
                self.assertEqual(ready["counts"]["BANK"], 5)
                self.assertIn("marker_inventory_count_mismatch", ready["issues"])


if __name__ == "__main__":
    unittest.main()
