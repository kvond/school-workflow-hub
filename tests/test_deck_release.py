from __future__ import annotations

import io
import unittest
import zipfile

from school_workflow_hub.workflows.deck_release import (
    critical_aspects,
    derive_teacher_contract,
    fingerprint,
    pptx_slide_texts,
    validate_release,
)


def good_slides():
    return (
        """TEACHER REFERENCE — do not project
Week 06 — Synthetic
CRITICAL ASPECTS
1 — Selective permeability: what the membrane lets through
WEEK QUESTION
Why can two small substances cross differently?
SCORED FOR COMPLETION
Build a Rule · Pattern break · 3-Tier · What if?
NOT SCORED
Concept Bank
[[MARKER-INVENTORY:C06]] NOTES=6 DRAFT=1 OPTIONAL=1 BANK_TERMS=1 NOTES_IDS=CA1-QUESTION,CA1-CONTRAST,CA1-RULE,CA1-PATTERNBREAK,CA1-MASTERY,WHATIF DRAFT_IDS=CA1-MASTERY OPTIONAL_IDS=OPT-RELATES BANK_IDS=glucose""",
        """Critical aspect: selective permeability
Why does water cross differently from glucose?
[[NOTES:C06:CA1-QUESTION]]""",
        """Critical aspect: selective permeability
Compare the same cell with two different substances.
[[NOTES:C06:CA1-CONTRAST]]""",
        """Critical aspect: selective permeability
Finish this sentence as a rule
[[NOTES:C06:CA1-RULE]]""",
        """Critical aspect: selective permeability
Pattern break
What changes when direction reverses?
[[NOTES:C06:CA1-PATTERNBREAK]]""",
        """Critical aspect: selective permeability
Getting Started
Mastery
[[NOTES:C06:CA1-MASTERY]]
[[DRAFT:C06:CA1-MASTERY]]""",
        """Critical aspect: selective permeability
What if?
Apply the rule to a new case.
[[NOTES:C06:WHATIF]]""",
        """Define these in your own words
[[BANK:C06:glucose]]
[[OPTIONAL:C06:OPT-RELATES]]""",
    )


class DeckReleaseTests(unittest.TestCase):
    def test_ready_deck_binds_prompt_fingerprints_and_revision(self):
        result = validate_release(
            good_slides(), revision="42", prompt0="prompt zero", prompt2="prompt two"
        )
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["issues"])
        self.assertEqual(result["receipt"]["revision"], "42")
        self.assertEqual(result["receipt"]["prompt0_sha256"], fingerprint("prompt zero"))
        self.assertEqual(result["counts"], {"NOTES": 6, "DRAFT": 1, "BANK": 1, "OPTIONAL": 1})
        phases = result["critical_aspects"][0]["phases"]
        self.assertTrue(all(phases.values()))

    def test_declared_aspect_missing_from_final_deck_is_not_ready(self):
        slides = list(good_slides())
        slides[0] = slides[0].replace(
            "1 — Selective permeability: what the membrane lets through",
            "1 — Selective permeability: what the membrane lets through\n"
            "2 — Chromosome separation: complete daughter-cell sets",
        )
        result = validate_release(
            tuple(slides), revision="43", prompt0="p0", prompt2="p2"
        )
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn(
            "critical_aspect_block_missing:chromosome separation", result["issues"]
        )

    def test_marker_inventory_count_mismatch_is_not_ready(self):
        slides = list(good_slides())
        slides[0] = slides[0].replace("NOTES=6", "NOTES=7")
        result = validate_release(tuple(slides), prompt0="p0", prompt2="p2")
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("marker_inventory_count_mismatch", result["issues"])

    def test_inventory_on_a_later_slide_cannot_release_the_deck(self):
        slides = list(good_slides())
        slides[0], inventory = slides[0].rsplit("\n", 1)
        slides[-1] += "\n" + inventory
        result = validate_release(tuple(slides), revision="42", prompt0="p0", prompt2="p2")
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("marker_inventory_not_on_first_slide", result["issues"])

    def test_counts_cannot_replace_exact_ids_for_a_populated_category(self):
        import re
        for field in ("NOTES_IDS", "DRAFT_IDS", "OPTIONAL_IDS", "BANK_IDS"):
            for replacement in ("", field + "="):
                with self.subTest(field=field, replacement=replacement):
                    slides = list(good_slides())
                    slides[0] = re.sub(r"\b" + field + r"=[^\s]*", replacement, slides[0])
                    result = validate_release(tuple(slides), revision="42", prompt0="p0", prompt2="p2")
                    self.assertEqual(result["status"], "NOT_READY")
                    self.assertIn("marker_inventory_id_mismatch", result["issues"])

    def test_zero_count_categories_do_not_require_invented_ids(self):
        slides = list(good_slides())
        slides[0] = slides[0].replace("OPTIONAL=1 BANK_TERMS=1", "OPTIONAL=0 BANK_TERMS=0")
        slides[0] = slides[0].replace(" OPTIONAL_IDS=OPT-RELATES BANK_IDS=glucose", "")
        slides = slides[:-1]
        result = validate_release(tuple(slides), revision="42", prompt0="p0", prompt2="p2")
        self.assertEqual(result["status"], "READY")

    def test_no_ready_receipt_without_a_final_source_revision(self):
        for revision in ("", "   ", None):
            with self.subTest(revision=revision):
                result = validate_release(good_slides(), revision=revision, prompt0="p0", prompt2="p2")
                self.assertEqual(result["status"], "NOT_READY")
                self.assertIn("deck_revision_unavailable", result["issues"])

    def test_four_declared_aspects_with_only_two_present_names_the_missing_pair(self):
        slides = list(good_slides())
        slides[0] = slides[0].replace(
            "1 — Selective permeability: what the membrane lets through",
            "1 — Selective permeability: what the membrane lets through\n"
            "2 — Chromosome separation\n3 — Cilia motion\n4 — Tissue structure",
        ).replace("NOTES=6 DRAFT=1", "NOTES=12 DRAFT=2").replace(
            " DRAFT_IDS=CA1-MASTERY",
            ",CA2-QUESTION,CA2-CONTRAST,CA2-RULE,CA2-PATTERNBREAK,CA2-MASTERY,CA2-WHATIF"
            " DRAFT_IDS=CA1-MASTERY,CA2-MASTERY",
        )
        slides.extend(slide.replace("selective permeability", "chromosome separation")
                      .replace("CA1-", "CA2-").replace(":WHATIF", ":CA2-WHATIF")
                      for slide in good_slides()[1:-1])
        result = validate_release(tuple(slides), revision="42", prompt0="p0", prompt2="p2")
        self.assertEqual(result["status"], "NOT_READY")
        self.assertEqual(result["issues"], ["critical_aspect_block_missing:cilia motion",
                                             "critical_aspect_block_missing:tissue structure"])

    def test_duplicate_marker_is_not_ready(self):
        slides = list(good_slides())
        slides.append("[[NOTES:C06:CA1-QUESTION]]")
        result = validate_release(tuple(slides), prompt0="p0", prompt2="p2")
        self.assertIn("duplicate_feedback_marker", result["issues"])

    def test_prompt_change_marks_prior_ready_receipt_stale(self):
        first = validate_release(
            good_slides(), revision="1", prompt0="old p0", prompt2="p2"
        )
        second = validate_release(
            good_slides(), revision="1", prompt0="new p0", prompt2="p2",
            prior_receipt=first["receipt"],
        )
        self.assertEqual(second["status"], "PROMPT_STALE")

    def test_visual_only_revision_fast_recertifies_without_model_work(self):
        first = validate_release(
            good_slides(), revision="1", prompt0="p0", prompt2="p2"
        )
        second = validate_release(
            good_slides(), revision="2", prompt0="p0", prompt2="p2",
            prior_receipt=first["receipt"],
        )
        self.assertEqual(second["status"], "READY")
        self.assertEqual(second["receipt"]["revision"], "2")

    def test_contract_text_change_marks_prior_receipt_stale(self):
        first = validate_release(
            good_slides(), revision="1", prompt0="p0", prompt2="p2"
        )
        slides = list(good_slides())
        slides[1] += "\nA harmless-looking text edit."
        second = validate_release(
            tuple(slides), revision="2", prompt0="p0", prompt2="p2",
            prior_receipt=first["receipt"],
        )
        self.assertEqual(second["status"], "STALE")

    def test_missing_live_prompt_contract_fails_closed(self):
        result = validate_release(good_slides())
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("agenda_prompt_contract_unavailable", result["issues"])

    def test_pptx_text_extraction_keeps_slide_order(self):
        drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
        def slide_xml(text):
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="%s"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>%s'
                '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
                % (drawing, text)
            )
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("ppt/slides/slide2.xml", slide_xml("Second"))
            archive.writestr("ppt/slides/slide1.xml", slide_xml("First"))
        self.assertEqual(pptx_slide_texts(raw.getvalue()), ("First", "Second"))



class CriticalAspectsTests(unittest.TestCase):
    """One reader for both course formats (shared with native feedback)."""

    def test_biology_lesson_numbering(self):
        self.assertEqual(critical_aspects(
            "TO PRINT\nCA1 worksheet\nCRITICAL ASPECTS\n1. Storing energy\n2. Releasing energy\n"
            "[[MARKER-INVENTORY:C09]] NOTES=4"),
            [{"number": 1, "name": "Storing energy"}, {"number": 2, "name": "Releasing energy"}])

    def test_anatomy_unit_numbering_and_singular_heading(self):
        reference = ("TEACHER REFERENCE — do not project\nCRITICAL ASPECT\n"
                     "3.  Structure fits function — why a tissue is built the way it is\n"
                     "WEEK QUESTION\nCould you predict the job?")
        self.assertEqual(critical_aspects(reference), [
            {"number": 3, "name": "Structure fits function — why a tissue is built the way it is"}])
        self.assertEqual(derive_teacher_contract((reference,))["critical_aspects"],
                         ["structure fits function"])

    def test_names_keep_their_exact_spacing(self):
        self.assertEqual(critical_aspects("CRITICAL ASPECTS\n1.  Two  spaces  kept\n")[0]["name"],
                         "Two  spaces  kept")

    def test_dash_numbering_and_unnumbered_lists(self):
        self.assertEqual(critical_aspects("CRITICAL ASPECTS\n1 — Permeability: what crosses\nWEEK QUESTION\nWhy?"),
                         [{"number": 1, "name": "Permeability: what crosses"}])
        self.assertEqual(critical_aspects("Critical aspects\nPermeability\nGradients\nNOT SCORED\nBank"),
                         [{"number": None, "name": "Permeability"}, {"number": None, "name": "Gradients"}])

    def test_numbered_list_ends_at_the_first_unnumbered_line(self):
        self.assertEqual([a["name"] for a in critical_aspects(
            "CRITICAL ASPECTS\n1. Storing energy\nWATCH FOR\nCommon errors")], ["Storing energy"])

    def test_no_heading_means_no_list(self):
        self.assertEqual(critical_aspects("Critical aspect: storing energy\n1. Not a list"), [])


if __name__ == "__main__":
    unittest.main()
