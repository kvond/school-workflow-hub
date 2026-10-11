"""A genuine response hold names the received slide that produced the hold."""
from copy import deepcopy
import unittest

from hub_v2 import completion
from test_eyewitness_choice_binding import fixture, raw, circle


class ResponseReviewSlideContextTests(unittest.TestCase):
    def test_choice_review_names_received_slide_and_leaves_arithmetic_unchanged(self):
        teacher = completion.read_deck(raw(fixture()))
        student = completion.read_deck(raw(fixture() + circle() + circle("NO")))
        student["slides"][0]["number"] = 19
        result = completion.assess(student, teacher, found_codes_only=True)
        self.assertTrue(result["note"].startswith("Slide 19: This YES/NO activity"))
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
        self.assertIsNone(result["notes_score"])

    def test_only_actual_review_locations_are_listed_not_loose_uncoded_writing(self):
        teacher = completion.read_deck(raw(fixture()))
        student = completion.read_deck(raw(fixture() + circle() + circle("NO")))
        teacher["slides"] += [deepcopy(teacher["slides"][0])]
        teacher["slides"][1]["number"] = 8
        student["slides"] += [deepcopy(student["slides"][0])]
        student["slides"][1]["number"] = 8
        student["slides"] += [{"number": 14, "units": [{"key": "uncoded", "text": "Unrelated uncoded writing.", "box": (0,0,20000,20000)}]}]
        result = completion.assess(student, teacher, found_codes_only=True)
        self.assertTrue(result["note"].startswith("Slides 1, 8: "))
        self.assertNotIn("14", result["note"])

    def test_blank_or_accepted_responses_do_not_gain_a_review_note(self):
        for content in (fixture(), fixture() + circle()):
            with self.subTest(content=content[-30:]):
                result = completion.assess(completion.read_deck(raw(content)), completion.read_deck(raw(fixture())))
                self.assertIsNone(result["note"])


if __name__ == "__main__":
    unittest.main()
