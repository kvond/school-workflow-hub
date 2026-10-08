"""Assignment routing on fictional lesson titles and uploads only."""
import unittest

from hub_v2 import work


class AssignmentMatchingTests(unittest.TestCase):
    def form(self, label, *decks):
        return {"form_id": label, "label": label, "name": "",
                "signature": {work.deck_stem(deck) for deck in decks}}

    def upload(self, *names):
        return {"files": [{"name": name} for name in names]}

    def test_teacher_deck_pairing_outweighs_misleading_lesson_label_words(self):
        observation = "Observation_Deductive_Reasoning_VT_CSI_1_markered"
        inductive = "Inductive_Deductive_Forensics_Google_Slides"
        forms = [self.form("Inductive and Deductive Reasoning — Turn In",
                           "Week 04 — Eyewitness Accounts (VT)"),
                 self.form("Observation Skills — Turn In")]
        work.pair_decks(forms, [work.deck_stem(observation), work.deck_stem(inductive)],
                        [observation, inductive])
        upload = self.upload("Copy of " + observation + " - Fictional Student.pptx")

        self.assertGreater(work.assignment_match_score(upload, forms[1]),
                           work.assignment_match_score(upload, forms[0]))
        self.assertEqual(work.assignment_for_submission(upload, forms), 1)
        self.assertEqual(work.assignment_for_submission(
            self.upload("Copy of " + inductive + " - Fictional Student.pptx"), forms), 0)

    def test_learned_deck_signature_outweighs_label_fallback(self):
        deck = "Observation_Deductive_Reasoning_VT_CSI_1_markered"
        forms = [self.form("Inductive and Deductive Reasoning — Turn In"),
                 self.form("Observation Skills — Turn In", deck)]
        self.assertEqual(work.assignment_for_submission(self.upload("Copy of " + deck), forms), 1)

    def test_unique_label_fallback_still_matches_student_lesson_title(self):
        forms = [self.form("Membrane Structure — Turn In"),
                 self.form("Membrane Transport — Turn In")]
        self.assertEqual(work.assignment_for_submission(
            self.upload("Copy of Membrane Transport - Fictional Student.pptx"), forms), 1)

    def test_equal_matches_stay_in_review(self):
        for forms in ([self.form("First lesson", "Membrane Transport"),
                       self.form("Second lesson", "Membrane Transport")],
                      [self.form("Membrane Transport Quiz — Turn In"),
                       self.form("Membrane Transport Review — Turn In")]):
            with self.subTest(labels=[form["label"] for form in forms]):
                self.assertIsNone(work.assignment_for_submission(
                    self.upload("Membrane Transport"), forms))

    def test_multi_file_upload_needs_one_unique_assignment_for_all_files(self):
        forms = [self.form("Membrane Structure — Turn In", "Membrane Structure"),
                 self.form("Membrane Transport — Turn In", "Membrane Transport")]
        self.assertIsNone(work.assignment_for_submission(
            self.upload("Copy of Membrane Structure", "Copy of Membrane Transport"), forms))
        self.assertIsNone(work.assignment_for_submission(
            self.upload("Copy of Membrane Structure", "My attachment"), forms))
        self.assertEqual(work.assignment_for_submission(
            self.upload("Copy of Membrane Structure", "Membrane Structure"), forms), 0)

    def contaminated_forms(self):
        types = "▶ LIVE — Week 05 — Types of Evidence (VT)"
        investigative = "▶ LIVE — Week 05 — Investigative Processes (VT)"
        # A swapped upload taught each Form the other's deck as well as its own.
        return [self.form("Types of Evidence — Turn In", types, investigative),
                self.form("Investigative Processes (Steps of CSI) — Turn In", types, investigative)], \
            types, investigative

    def test_teacher_decks_are_authoritative_when_form_uploads_are_swapped(self):
        forms, types, investigative = self.contaminated_forms()
        work.pair_decks(forms, [work.deck_stem(types), work.deck_stem(investigative)],
                        [types, investigative])
        self.assertEqual(forms[0]["teacher_signature"], {work.deck_stem(types)})
        self.assertEqual(forms[1]["teacher_signature"], {work.deck_stem(investigative)})
        self.assertEqual(work.assignment_for_submission(self.upload(
            "Copy of " + investigative + " - Fictional Student.pptx"), forms), 1)
        self.assertEqual(work.assignment_for_submission(self.upload(
            "Copy of " + types + " - Fictional Student.pptx"), forms), 0)

    def test_one_teacher_deck_can_pair_despite_populated_learned_signatures(self):
        forms, types, investigative = self.contaminated_forms()
        work.pair_decks(forms, [work.deck_stem(types)], [types])
        self.assertEqual(forms[0]["teacher_signature"], {work.deck_stem(types)})
        self.assertNotIn("teacher_signature", forms[1])
        self.assertEqual(work.assignment_for_submission(self.upload(
            "Copy of " + types + " - Fictional Student.pptx"), forms), 0)
        # With no linked teacher deck for this Form, its complete unique title
        # resolves equal learned matches. The descriptive suffix is optional.
        self.assertEqual(work.assignment_for_submission(self.upload(
            "Copy of " + investigative + " - Fictional Student.pptx"), forms), 1)
        self.assertIsNone(work.assignment_for_submission(self.upload(
            "Copy of " + types, "Copy of " + investigative), forms))

    def test_partial_label_overlap_cannot_resolve_contaminated_signatures(self):
        deck = "Observation_Deductive_Reasoning_VT_CSI_1_markered"
        forms = [self.form("Inductive and Deductive Reasoning — Turn In", deck),
                 self.form("Observation Skills — Turn In", deck)]
        work.pair_decks(forms, [work.deck_stem(deck)], [deck])
        self.assertTrue(all(not form.get("teacher_signature") for form in forms))
        self.assertIsNone(work.assignment_for_submission(self.upload("Copy of " + deck), forms))

    def test_conflicting_teacher_decks_stay_in_review_despite_one_full_title(self):
        forms, types, investigative = self.contaminated_forms()
        for form in forms:
            form["teacher_signature"] = {work.deck_stem(investigative)}
        self.assertIsNone(work.assignment_for_submission(self.upload(
            "Copy of " + investigative + " - Fictional Student.pptx"), forms))


if __name__ == "__main__":
    unittest.main()
