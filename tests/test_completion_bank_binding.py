"""Whole BANK term labels must win over broad headings in response fallback."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import json

from hub_v2 import app, completion, google, morning


TERMS = ("Autotroph", "Heterotroph", "Producer", "Consumer", "Decomposer",
         "Trophic level", "Energy pyramid", "Biomass", "Symbiosis", "Mutualism",
         "Parasitism", "Commensalism", "Niche", "Coevolution")
SIZE = (1000, 750)


def concept_bank():
    units = [{"key": "heading", "text": "Symbiosis & Relationships", "box": (520, 100, 450, 30)},
             {"key": "directions", "text": "Define each term in your own words.", "box": (20, 20, 900, 30)}]
    for n, term in enumerate(TERMS):
        x, y = (20, 150 + n * 65) if n < 8 else (520, 150 + (n - 8) * 65)
        units.extend([
            {"key": "label-" + term, "text": term, "box": (x, y, 100, 40)},
            {"key": "answer-" + term, "text": f"[[BANK:C09:{term}]]", "box": (x + 120, y, 300, 40)}])
    return {"number": 1, "units": units}


def bind(student, template):
    written, _ = completion._written(student, template, SIZE)
    return completion._assign(completion.markers({"slides": [student]}), written, template, SIZE)


class BankBindingTests(unittest.TestCase):
    def test_every_response_matches_only_its_whole_term_despite_the_broad_heading(self):
        template = concept_bank()
        for term in TERMS:
            with self.subTest(term=term):
                student = deepcopy(template)
                answer = next(u for u in student["units"] if u["key"] == "answer-" + term)
                answer["text"] += "\nAn independent definition for this term."
                assigned, uncoded, _ = bind(student, template)
                self.assertEqual({key for key, values in assigned.items() if values}, {("BANK", term)})
                self.assertEqual(uncoded, [])

    def test_fourteen_distinct_responses_do_not_merge_under_the_section_heading(self):
        template = concept_bank()
        student = deepcopy(template)
        for term in TERMS:
            next(u for u in student["units"] if u["key"] == "answer-" + term)["text"] += "\nDefinition about " + term
        assigned, uncoded, _ = bind(student, template)
        for term in TERMS:
            self.assertEqual([u["key"] for u, _ in assigned[("BANK", term)]], ["answer-" + term])
        self.assertEqual(uncoded, [])

    def test_unchanged_template_and_additive_codes_do_not_create_written_evidence(self):
        template = concept_bank()
        assigned, uncoded, _ = bind(deepcopy(template), template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual(uncoded, [])
        original = deepcopy(template)
        for term in TERMS[:3]:
            next(u for u in original["units"] if u["key"] == "answer-" + term)["text"] = ""
        prepared = deepcopy(original)
        for term in TERMS[:3]:
            response = next(u for u in prepared["units"] if u["key"] == "answer-" + term)
            x, y, w, h = response["box"]
            prepared["units"].append({"key": "new-code-" + term, "text": f"[[BANK:C09:{term}]]",
                                      "box": (x + w * .4, y + h * .45, w * .2, h * .1)})
        self.assertEqual(completion._written(prepared, original, SIZE), ([], {}))
        for term in TERMS:
            student = deepcopy(prepared)
            next(u for u in student["units"] if u["key"] == "answer-" + term)["text"] += "\nAn independent definition."
            assigned, uncoded, _ = bind(student, template)
            self.assertEqual({key for key, values in assigned.items() if values}, {("BANK", term)})
            self.assertEqual(uncoded, [])

    def test_full_term_normalization_still_matches_camelcase_and_spaced_labels(self):
        template = {"number": 1, "units": [
            {"key": "broad", "text": "Inductive reasoning & evidence", "box": (20, 20, 700, 30)},
            {"key": "term", "text": "Inductive reasoning", "box": (20, 100, 150, 30)},
            {"key": "answer", "text": "", "box": (200, 100, 300, 40)},
            {"key": "code", "text": "[[BANK:F09:InductiveReasoning]]", "box": (900, 600, 50, 10)}]}
        student = deepcopy(template)
        student["units"][2]["text"] = "Patterns found by comparing observations."
        assigned, uncoded, _ = bind(student, template)
        self.assertEqual([u["key"] for u, _ in assigned[("BANK", "InductiveReasoning")]], ["answer"])
        self.assertEqual(uncoded, [])

    def test_missing_or_repeated_whole_labels_do_not_guess_from_a_broad_heading(self):
        for labels in ([], [(20, 100, 100, 30), (520, 100, 100, 30)]):
            with self.subTest(labels=labels):
                template = {"number": 1, "units": [
                    {"key": "broad", "text": "Symbiosis & Relationships", "box": (20, 20, 900, 30)},
                    {"key": "answer", "text": "", "box": (200, 100, 300, 40)},
                    {"key": "code", "text": "[[BANK:C09:Symbiosis]]", "box": (900, 600, 50, 10)}]}
                template["units"] += [{"key": str(i), "text": "Symbiosis", "box": box} for i, box in enumerate(labels)]
                student = deepcopy(template)
                student["units"][1]["text"] = "An independent definition."
                assigned, uncoded, _ = bind(student, template)
                self.assertEqual(assigned[("BANK", "Symbiosis")], [])
                self.assertEqual([u["key"] for u, _ in uncoded], ["answer"])

    def test_equidistant_term_labels_leave_the_response_uncoded(self):
        template = {"number": 1, "units": [
            {"key": "a", "text": "Producer", "box": (20, 100, 100, 30)},
            {"key": "b", "text": "Consumer", "box": (520, 100, 100, 30)},
            {"key": "answer", "text": "", "box": (220, 100, 200, 30)},
            {"key": "codes", "text": "[[BANK:C09:Producer]]\n[[BANK:C09:Consumer]]", "box": (900, 600, 50, 10)}]}
        student = deepcopy(template)
        student["units"][2]["text"] = "An unbound definition."
        assigned, uncoded, _ = bind(student, template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual([u["key"] for u, _ in uncoded], ["answer"])

    def test_written_but_unbound_bank_response_holds_before_score_arithmetic(self):
        template = {"number": 1, "units": [
            {"key": "broad", "text": "Symbiosis & Relationships", "box": (20, 20, 900, 30)},
            {"key": "answer", "text": "", "box": (200, 100, 300, 40)},
            {"key": "code", "text": "[[BANK:C09:Symbiosis]]", "box": (900, 600, 50, 10)}]}
        student = deepcopy(template)
        student["units"][1]["text"] = "Two organisms have a close relationship."
        with patch.object(completion, "score", side_effect=AssertionError("Unbound BANK must not be scored")):
            result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [template], "size": SIZE})
        self.assertIn("unique printed term label", result["note"])
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))
        self.assertEqual([u["slide"] for u in result["uncoded"]], [1])

    def test_another_valid_bank_label_cannot_claim_an_ambiguous_terms_response(self):
        template = {"number": 1, "units": [
            {"key": "a", "text": "Symbiosis", "box": (20, 100, 100, 30)},
            {"key": "duplicate", "text": "Symbiosis", "box": (20, 200, 100, 30)},
            {"key": "b", "text": "Mutualism", "box": (520, 100, 100, 30)},
            {"key": "answer", "text": "", "box": (220, 100, 200, 30)},
            {"key": "codes", "text": "[[BANK:C09:Symbiosis]]\n[[BANK:C09:Mutualism]]", "box": (900, 600, 50, 10)}]}
        student = deepcopy(template)
        student["units"][3]["text"] = "A close relationship between organisms."
        assigned, uncoded, basis = bind(student, template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual([u["key"] for u, _ in uncoded], ["answer"])
        self.assertEqual(basis, "BANK term labels need review")

    def test_inline_writing_in_a_repeated_term_cannot_bypass_unique_label_validation(self):
        template = {"number": 1, "units": [
            {"key": "a", "text": "Producer", "box": (20, 100, 300, 40)},
            {"key": "duplicate", "text": "Producer", "box": (20, 200, 300, 40)},
            {"key": "b", "text": "Consumer", "box": (520, 100, 300, 40)},
            {"key": "codes", "text": "[[BANK:C09:Producer]]\n[[BANK:C09:Consumer]]", "box": (900, 600, 50, 30)}]}
        student = deepcopy(template)
        student["units"][0]["text"] += " makes its own food from captured sunlight"
        assigned, uncoded, basis = bind(student, template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual([u["key"] for u, _ in uncoded], ["a"])
        self.assertEqual(basis, "BANK term labels need review")
        with patch.object(completion, "score", side_effect=AssertionError("Repeated inline term must hold")):
            result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [template], "size": SIZE})
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))

    def test_mixed_bank_ambiguity_cannot_fall_through_to_notes_or_draft(self):
        template = {"number": 1, "units": [
            {"key": "a", "text": "Producer", "box": (20, 100, 100, 30)},
            {"key": "duplicate", "text": "Producer", "box": (20, 200, 100, 30)},
            {"key": "answer", "text": "", "box": (220, 100, 200, 30)},
            {"key": "codes", "text": "[[BANK:C09:Producer]]\n[[NOTES:C09:Q]]\n[[DRAFT:C09:Q]]",
             "box": (900, 600, 50, 30)}]}
        student = deepcopy(template)
        student["units"][2]["text"] = "An organism that makes its own food."
        assigned, uncoded, basis = bind(student, template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual([u["key"] for u, _ in uncoded], ["answer"])
        self.assertEqual(basis, "BANK term labels need review")
        with patch.object(completion, "score", side_effect=AssertionError("Mixed BANK ambiguity must hold")):
            result = completion.assess({"slides": [student], "size": SIZE}, {"slides": [template], "size": SIZE})
        self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))

    def test_valid_mixed_bank_and_notes_keep_their_separate_responses(self):
        template = {"number": 1, "units": [
            {"key": "bank-label", "text": "Producer", "box": (20, 100, 100, 30)},
            {"key": "bank-answer", "text": "", "box": (125, 100, 100, 30)},
            {"key": "notes-answer", "text": "", "box": (20, 400, 300, 80)},
            {"key": "codes", "text": "[[BANK:C09:Producer]]\n[[NOTES:C09:Q]]", "box": (900, 600, 50, 30)}]}
        student = deepcopy(template)
        student["units"][1]["text"] = "Makes food using captured sunlight."
        student["units"][2]["text"] = "Energy enters an ecosystem through producers."
        assigned, uncoded, _ = bind(student, template)
        self.assertEqual([u["key"] for u, _ in assigned[("BANK", "Producer")]], ["bank-answer"])
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "Q")]], ["notes-answer"])
        self.assertEqual(uncoded, [])

    def test_mixed_near_bank_tie_holds_and_far_notes_response_keeps_its_category(self):
        template = {"number": 1, "units": [
            {"key": "a", "text": "Producer", "box": (20, 100, 100, 30)},
            {"key": "b", "text": "Consumer", "box": (220, 100, 100, 30)},
            {"key": "near", "text": "", "box": (130, 100, 80, 30)},
            {"key": "far", "text": "", "box": (130, 400, 80, 30)},
            {"key": "codes", "text": "[[BANK:C09:Producer]]\n[[BANK:C09:Consumer]]\n[[NOTES:C09:Q]]",
             "box": (900, 600, 50, 30)}]}
        near = deepcopy(template)
        near["units"][2]["text"] = "An ambiguous term definition."
        assigned, uncoded, basis = bind(near, template)
        self.assertTrue(all(not values for values in assigned.values()))
        self.assertEqual([u["key"] for u, _ in uncoded], ["near"])
        self.assertEqual(basis, "BANK term labels need review")
        far = deepcopy(template)
        far["units"][3]["text"] = "An independent explanation for the notes question."
        assigned, uncoded, basis = bind(far, template)
        self.assertEqual([u["key"] for u, _ in assigned[("NOTES", "Q")]], ["far"])
        self.assertTrue(all(not values for key, values in assigned.items() if key[0] == "BANK"))
        self.assertEqual(uncoded, [])
        self.assertNotEqual(basis, "BANK term labels need review")

    def test_nonbank_first_and_revised_answer_binding_is_unchanged(self):
        template = {"number": 1, "units": [
            {"key": "first-label", "text": "Your first answer", "box": (20, 50, 300, 30)},
            {"key": "first", "text": "", "box": (20, 100, 300, 100)},
            {"key": "revised-label", "text": "Your revised answer", "box": (20, 250, 300, 30)},
            {"key": "revised", "text": "", "box": (20, 300, 300, 100)},
            {"key": "codes", "text": "[[NOTES:C09:Q]]\n[[DRAFT:C09:Q]]", "box": (900, 600, 50, 10)}]}
        student = deepcopy(template)
        student["units"][3]["text"] = "I changed my explanation after discussing the evidence."
        assigned, uncoded, _ = bind(student, template)
        self.assertEqual(assigned[("NOTES", "Q")], [])
        self.assertEqual([u["key"] for u, _ in assigned[("DRAFT", "Q")]], ["revised"])
        self.assertEqual(uncoded, [])

    def test_real_unbound_bank_result_stays_check_and_null_through_the_morning_pipeline(self):
        header = {"number": 1, "units": [{"key": "inventory",
            "text": "[[MARKER-INVENTORY:C09]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=1 "
                    "NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=Symbiosis",
            "box": (0, 0, 900, 60)}]}
        bank = {"number": 2, "units": [
            {"key": "broad", "text": "Symbiosis & Relationships", "box": (20, 20, 900, 30)},
            {"key": "answer", "text": "", "box": (200, 100, 300, 40)},
            {"key": "code", "text": "[[BANK:C09:Symbiosis]]", "box": (900, 600, 50, 10)}]}
        template = {"slides": [header, bank], "size": SIZE}
        student = deepcopy(template)
        student["slides"][1]["units"][1]["text"] = "Two organisms live in a close relationship."
        self.assertEqual(completion.readiness(student)["status"], "READY")
        fid = "FICTIONAL_BANK_FORM_123"
        sid = "FICTIONAL_BANK_STUDENT_123"
        tid = "FICTIONAL_BANK_TEMPLATE_123"
        ready = {"status": "READY", "source_resolution": {"decks": [{"id": tid}]}}
        row = {"last": "Rivera", "first": "Ana", "section": "A4 Fictional", "matched": True,
               "files": [{"id": sid, "mime": completion.SLIDES, "name": "Copy of Fictional bank"}]}
        form = {"form_id": fid, "label": "Vocabulary — Turn in", "grade_readiness": ready,
                "students": [row], "missing": [], "late": [], "excused": [], "elsewhere": [],
                "window": {"from": "2020-01-01T00:00:00Z", "at": "2050-01-01T00:00:00Z"}}
        found = {"lessons": [{"topic": "Vocabulary", "forms": [form]}], "problems": []}
        agenda = Mock(classes={"biology": {"name": "Fictional Biology"}})
        agenda.lessons.return_value = [{"topic": "Vocabulary", "forms": [{"form_id": fid}], "materials": []}]
        config = {"HUB_IC_ASSIGNMENT_MAP": json.dumps([{"class_key": "biology", "form_id": fid, "targets": []}])}
        with patch.object(completion, "_template", return_value=({"id": tid, "name": "Fictional template",
                "version": "1", "modifiedTime": "fictional-template"}, template)), \
                patch.object(completion, "_read", return_value=student), \
                patch.object(google, "drive_file", return_value={"id": sid, "name": "Fictional bank",
                    "version": "1", "modifiedTime": "fictional-student"}), \
                patch.object(completion, "assignment_readiness", return_value=ready), \
                patch.object(completion, "lesson_sources", return_value=ready["source_resolution"]), \
                patch.object(morning.ledger, "read", return_value={"configured": True}), \
                patch.dict(completion._results, {}, clear=True), \
                patch.object(completion, "score", side_effect=AssertionError("Held source cannot become numeric zero")):
            scored = app._score_rows("fictional-token", agenda, "biology", "Wk 09", fid, form, found)
            self.assertEqual(len(scored), 1)
            self.assertIn("unique printed term label", scored[0][1]["note"])
            self.assertEqual((scored[0][1]["notes_score"], scored[0][1]["revision_score"]), (None, None))
            values = morning.rows(form, scored, datetime.now(timezone.utc))
            self.assertEqual([(r["kind"], r["notes"], r["revision"]) for r in values], [("check", None, None)])
            result = morning.make_list("fictional-token", config, agenda, "biology", "Wk 09", fid,
                                       Mock(return_value=found), app._score_rows)
        self.assertEqual(result, {"code": "source_scores_held", "source_holds": {"checks": 1}})
        self.assertNotIn("list", result)


if __name__ == "__main__":
    unittest.main()
