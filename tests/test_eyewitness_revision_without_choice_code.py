"""The actual old revision field remains scorable without a newer choice code."""
from copy import deepcopy
import unittest

from hub_v2 import completion
from test_eyewitness_choice_binding import fixture, raw


class RevisionWithoutChoiceCodeTests(unittest.TestCase):
    def setUp(self):
        self.teacher = completion.read_deck(raw(fixture()))
        self.student = deepcopy(self.teacher)
        self.student["slides"][0]["units"] = [u for u in self.student["slides"][0]["units"] if "binding" not in u]

    def unit(self, name, deck=None):
        return next(u for u in (deck or self.student)["slides"][0]["units"] if u["key"] == name)

    def score(self):
        return completion.assess(self.student, self.teacher, found_codes_only=True)

    def assert_held(self):
        result = self.score()
        self.assertIsNone(result["revision_score"])
        self.assertIn("revision response", result["note"])

    def test_original_blank_revision_is_zero_and_missing_choice_remains_na(self):
        result = self.score()
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, 0.0))
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 0})
        self.assertIsNone(result["note"])

    def test_meaningful_wrong_answer_in_original_box_counts_only_revision(self):
        self.unit("revision-response")["text"] = "A very confident witness is always correct."
        result = self.score()
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, 10.0))
        self.assertEqual(result["counts"]["DRAFT"], {"done": 1, "required": 1})
        self.assertIsNone(result["note"])

    def test_source_bound_new_box_inside_original_blank_counts(self):
        self.student["slides"][0]["units"].append({"key": "new-answer", "text": "Lighting matters.",
                                                 "box": (570000, 220000, 350000, 70000)})
        self.assertEqual(self.score()["revision_score"], 10.0)

    def test_printed_revision_label_edit_cannot_become_student_answer(self):
        self.unit("revision-label")["text"] += " Several independently typed words only in the label."
        self.assert_held()

    def test_initial_choice_overlay_and_inherited_buttons_never_count_as_revision(self):
        self.unit("yes")["text"] = "YES independently added commentary."
        result = self.score()
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, 0.0))
        self.assertIsNone(result["note"])

    def test_duplicate_or_crossing_response_regions_hold(self):
        for box in ((570000, 220000, 350000, 70000), (530000, 200000, 450000, 90000)):
            with self.subTest(box=box):
                self.unit("revision-response")["text"] = "My original answer."
                extra = {"key": "competing-answer", "text": "My other answer.", "box": box}
                self.student["slides"][0]["units"].append(extra)
                self.assert_held()
                self.student["slides"][0]["units"].remove(extra)

    def test_changed_draft_question_or_marker_box_cannot_borrow_teacher_field(self):
        for changed in ("id", "box"):
            with self.subTest(changed=changed):
                old = deepcopy(self.unit("revision-label"))
                if changed == "id":
                    self.unit("revision-label")["text"] = old["text"].replace("Q-MASTERY-4", "Q-OTHER")
                else:
                    self.unit("revision-label")["box"] = (540000, 100000, 400000, 70000)
                result = self.score()
                self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                if changed == "box":
                    self.assert_held()
                self.unit("revision-label").update(old)

    def test_unknown_teacher_descriptor_geometry_or_original_style_holds(self):
        for changed in ("field", "style"):
            with self.subTest(changed=changed):
                old = deepcopy(self.teacher)
                if changed == "field":
                    marker = next(u for u in self.teacher["slides"][0]["units"] if "binding" in u)
                    marker["binding"]["revision"]["box"][0] += 20000
                else:
                    self.teacher["slides"][0]["choice_shapes"][1]["sha256"] = "0" * 64
                self.assert_held()
                self.teacher = old

    def test_copied_prompt_in_authentic_blank_is_not_completion(self):
        self.unit("revision-response")["text"] = self.unit("question")["text"]
        result = self.score()
        self.assertEqual(result["revision_score"], 0.0)
        self.assertEqual(result["items"][0]["status"], "copied")


if __name__ == "__main__":
    unittest.main()
