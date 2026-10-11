"""Authentic printed BANK labels may include their own single matching code."""
from copy import deepcopy
import unittest

from hub_v2 import completion

SIZE = (1000, 750)


def original():
    return {"size": SIZE, "slides": [
        {"number": 1, "units": [{"key": "inventory", "box": (0, 0, 900, 60),
         "text": "[[MARKER-INVENTORY:C09]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=2 "
                 "NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=Producer,Consumer"}]},
        {"number": 2, "units": [
            {"key": "directions", "text": "Explain each term using your own observations and words rather than copying these printed instructions.",
             "box": (20, 20, 900, 40)},
            {"key": "label-Producer", "text": "Producer\n[[BANK:C09:Producer]]", "box": (20, 100, 180, 40)},
            {"key": "answer-Producer", "text": "", "box": (220, 100, 400, 40)},
            {"key": "label-Consumer", "text": "Consumer\n[[BANK:C09:Consumer]]", "box": (20, 240, 180, 40)},
            {"key": "answer-Consumer", "text": "", "box": (220, 240, 400, 40)}]}]}


def unit(deck, key):
    return next(u for s in deck["slides"] for u in s["units"] if u["key"] == key)


class CodedBankLabelTests(unittest.TestCase):
    def test_each_definition_binds_to_its_authentic_coded_whole_term(self):
        template = original(); student = deepcopy(template)
        unit(student, "answer-Producer")["text"] = "Plants capture sunlight to make food."
        unit(student, "answer-Consumer")["text"] = "Animals eat other organisms for energy."
        result = completion.assess(student, template, found_codes_only=True)
        self.assertIsNone(result["note"])
        self.assertEqual(result["counts"]["BANK"], {"done": 2, "required": 2})
        self.assertEqual(result["notes_score"], 10)
        self.assertEqual({i["id"]: i["answer"] for i in result["items"] if i["category"] == "BANK"},
                         {"Producer": "Plants capture sunlight to make food.",
                          "Consumer": "Animals eat other organisms for energy."})

    def test_unchanged_labels_and_copied_teacher_instructions_receive_no_credit(self):
        template = original()
        unchanged = completion.assess(deepcopy(template), template, found_codes_only=True)
        self.assertEqual(unchanged["counts"]["BANK"]["done"], 0)
        student = deepcopy(template)
        unit(student, "answer-Producer")["text"] = unit(template, "directions")["text"]
        result = completion.assess(student, template, found_codes_only=True)
        self.assertIsNone(result["note"])
        self.assertEqual(result["counts"]["BANK"]["done"], 0)
        self.assertEqual(next(i["status"] for i in result["items"] if i["id"] == "Producer"), "copied")

    def test_wrong_lesson_category_id_or_multiple_codes_do_not_establish_a_label(self):
        for code in ("[[BANK:C08:Producer]]", "[[NOTES:C09:Producer]]", "[[BANK:C09:Consumer]]",
                     "[[BANK:C09:Producer]]\n[[BANK:C09:Producer]]"):
            with self.subTest(code=code):
                template = original()
                unit(template, "label-Producer")["text"] = "Producer\n" + code
                student = deepcopy(original())
                unit(student, "answer-Producer")["text"] = "Plants make their own food."
                result = completion.assess(student, template, found_codes_only=True)
                self.assertIn("unique printed term label", result["note"])
                self.assertIsNone(result["notes_score"])
                self.assertEqual(result["counts"]["BANK"]["done"], 0)

    def test_duplicate_plain_or_coded_labels_still_hold(self):
        for text in ("Producer", "Producer\n[[BANK:C09:Producer]]",
                     "Producer\n[[BANK:C08:Producer]]"):
            with self.subTest(text=text):
                template = original()
                template["slides"][1]["units"].append({"key": "duplicate", "text": text,
                                                     "box": (20, 170, 180, 40)})
                student = deepcopy(template)
                unit(student, "answer-Producer")["text"] = "Plants make their own food."
                result = completion.assess(student, template, found_codes_only=True)
                self.assertIn("unique printed term label", result["note"])
                self.assertIsNone(result["notes_score"])
                self.assertEqual(result["counts"]["BANK"]["done"], 0)

    def test_equal_distance_to_two_authentic_coded_labels_still_holds(self):
        template = original()
        unit(template, "label-Consumer")["box"] = (420, 100, 180, 40)
        unit(template, "answer-Producer")["box"] = (210, 100, 200, 40)
        student = deepcopy(template)
        unit(student, "answer-Producer")["text"] = "An answer between two distinct term labels."
        result = completion.assess(student, template, found_codes_only=True)
        self.assertIn("unique printed term label", result["note"])
        self.assertIsNone(result["notes_score"])
        self.assertEqual(result["counts"]["BANK"]["done"], 0)


if __name__ == "__main__":
    unittest.main()
