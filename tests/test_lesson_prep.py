"""Prep is a pure projection of fictional Agenda evidence, never lab release."""
from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from hub_v2 import agenda, lesson_prep
from tests.test_hub_v2 import cell, grid

PRINT = "https://docs.google.com/document/d/FICTIONAL_PRINT/edit"
DEMO = "https://docs.google.com/document/d/FICTIONAL_DEMO/edit"
LAB = "https://docs.google.com/presentation/d/FICTIONAL_LAB/edit"


def source():
    weeks = grid({1: ["Week", "Monday", "Dates & closures"],
                  2: ["Wk 08", "10/19/2026", "Current calendar"],
                  3: ["Wk 09", "10/26/2026", "Friday = B day"],
                  4: ["Wk 10", "11/02/2026", "Mon/Tue schools closed"],
                  5: ["Wk 11", "11/09/2026", ""]})
    classes = grid({1: ["Wk", "Topic", "Lab / prep", "Activity", "Turn it in", "Mon plan", "Mon note"],
                    2: ["Wk 08", "Current topic", "PULL: goggles", "", "", "Plan", "Day note"],
                    3: ["Wk 09", "Next topic", "BUY: yeast; PRINT: student CER", "", "", "Next plan", ""],
                    4: ["Wk 10", "[CLOSED — holiday]", "BUY: do not reschedule automatically", "", "", "", ""],
                    5: ["Wk 11", "Later topic", "PRINT: later paper", "", "", "", ""]})
    return agenda.Agenda("FICTIONAL_AGENDA", "Fictional Agenda",
                         {"Weeks": 1, "Biology": 2, "Forensics": 3, "A&P": 4},
                         {"Weeks": weeks, "Biology": deepcopy(classes),
                          "Forensics": deepcopy(classes), "A&P": deepcopy(classes)})


class PrepTests(TestCase):
    def test_all_subjects_keep_source_notes_and_explicit_categories(self):
        for cls in source().day(date(2026, 10, 19))["classes"]:
            with self.subTest(cls=cls["key"]):
                lesson = cls["lessons"][0]
                self.assertEqual(lesson["prep"], "PULL: goggles")
                self.assertEqual(lesson["prep_notes"]["items"], [{"label": "PULL", "text": "goggles"}])
                self.assertEqual(lesson["prep_notes"]["tracks"], [])
                self.assertEqual(lesson["today"]["note"], "Day note")

    def test_empty_notes_do_not_invent_supply_lists_or_sections(self):
        self.assertIsNone(lesson_prep.project({"prep": "", "materials": [{"label": "Teacher deck", "url": LAB}]}))
        result = lesson_prep.project({"prep": "Goggles and gloves. Do not use hot water."})
        self.assertEqual(result["items"], [{"label": "Notes", "text": "Goggles and gloves. Do not use hot water."}])

    def test_biology_tracks_only_when_explicit_and_no_demo_form_warning(self):
        lesson = {"prep": "BUY: yeast\nDemo: teacher setup\nPULL: trays\nIndependent Lab: PRINT: data sheet",
                  "materials": [{"label": "Demo teacher prep", "url": DEMO},
                                {"label": "Independent Lab student deck", "url": LAB}]}
        result = lesson_prep.project(lesson, biology=True)
        self.assertEqual([t["label"] for t in result["tracks"]], ["Demo", "Independent Lab"])
        self.assertEqual(result["tracks"][1]["items"], [{"label": "PRINT", "text": "data sheet"}])
        self.assertIn("No Turn-in Form", result["issues"][0])
        demo = lesson_prep.project({"prep": "Demo: PULL: trays"}, biology=True)
        self.assertEqual(demo["issues"], [])

    def test_other_subjects_preserve_demo_wording_without_creating_track(self):
        for key in ("forensics", "anatomy"):
            result = lesson_prep.project({"prep": "Demo: use existing print packet"}, biology=key == "biology")
            self.assertEqual(result["tracks"], [])
            self.assertEqual(result["items"][0]["text"], "Demo: use existing print packet")
        result = lesson_prep.project({"prep": "Demonstration planning stays unchanged"}, biology=True)
        self.assertEqual(result["tracks"], [])

    def test_rich_formula_links_are_preserved_and_deduplicated(self):
        s = source()
        entry = s.classes["biology"]
        entry["cells"][3, 3] = cell("PRINT: Student CER", userEnteredValue={
            "formulaValue": '=HYPERLINK("%s","PRINT: Student CER")' % PRINT})
        entry["cells"][3, 4] = cell("PRINT: Student CER", hyperlink=PRINT)
        prep = s.lessons("biology", "Wk 09")[0]["prep_notes"]
        self.assertEqual(prep["links"], [{"label": "PRINT: Student CER", "url": PRINT}])
        self.assertEqual(prep["link_status"], "not_checked")

    def test_present_form_never_becomes_exact_independent_mapping_proof(self):
        result = lesson_prep.project({"prep": "Independent Lab: student work", "forms": [
            {"label": "Other lesson", "form_id": "FICTIONAL_FORM", "problem": None}]}, biology=True)
        self.assertIn("mapping and release are not verified", result["issues"][-1])
        result = lesson_prep.project({"prep": "Independent: student work", "forms": [
            {"problem": "Published Form cannot be read"}]}, biology=True)
        self.assertIn("Published Form cannot be read", result["issues"])

    def test_unsupported_links_stay_nonclickable_without_network_probe(self):
        for url in ("javascript:alert(1)", "https://user:password@example.test/x", "https://example.test:bad/x", "https://exa mple.test/"):
            result = lesson_prep.project({"prep_links": [{"label": "PRINT", "url": url}]})
            self.assertEqual(result["links"], [])
            self.assertIn("unsupported address", result["issues"][0])
        # Even a syntactically sound URL is never reported accessible.
        result = lesson_prep.project({"prep_links": [{"label": "PRINT", "url": "https://example.test/missing"}]})
        self.assertEqual(result["link_status"], "not_checked")

    def test_future_uses_exact_next_calendar_week_and_row_provenance(self):
        s = source()
        for cls in s.day(date(2026, 10, 23))["classes"]:
            future = cls["next_prep"]
            self.assertEqual((future["monday"], future["week"], future["closures"]),
                             ("2026-10-26", "Wk 09", "Friday = B day"))
            self.assertEqual([l["row"] for l in future["lessons"]], [3])
            self.assertTrue(future["lessons"][0]["edit_url"].endswith("range=A3"))
            self.assertEqual(future["lessons"][0]["prep_notes"]["items"][0]["text"], "yeast")

    def test_postponed_lesson_follows_source_row_not_old_label_or_title(self):
        s = source()
        s.classes["biology"]["cells"][3, 3] = cell("")
        s.classes["biology"]["cells"][5, 3] = cell("BUY: yeast")
        before = deepcopy(s.classes["biology"]["cells"])
        self.assertEqual(s.day(date(2026, 10, 23))["classes"][0]["next_prep"]["lessons"], [])
        future = s.day(date(2026, 11, 6))["classes"][0]["next_prep"]
        self.assertEqual((future["monday"], future["lessons"][0]["row"]), ("2026-11-09", 5))
        self.assertEqual(s.classes["biology"]["cells"], before)

    def test_holiday_is_not_skipped_and_closed_source_is_explicit(self):
        future = source().day(date(2026, 10, 30))["classes"][0]["next_prep"]
        self.assertEqual(future["monday"], "2026-11-02")
        self.assertEqual(future["closures"], "Mon/Tue schools closed")
        self.assertEqual(future["lessons"][0]["kind"], "closed")

    def test_missing_or_ambiguous_future_week_is_unavailable_not_empty_or_skipped(self):
        s = source()
        s.weeks = [w for w in s.weeks if w["label"] != "Wk 09"]
        result = s.day(date(2026, 10, 19))["classes"][0]["next_prep"]
        self.assertIn("unavailable or ambiguous", result["problem"])
        s.weeks += [dict(s.weeks[0], monday="2026-10-26"), dict(s.weeks[0], monday="2026-10-26")]
        self.assertIsNotNone(s.day(date(2026, 10, 19))["classes"][0]["next_prep"]["problem"])

    def test_missing_future_class_row_is_visible_and_separate_lessons_stay_separate(self):
        s = source()
        entry = s.classes["biology"]
        entry["layout"]["rows"] = [(r, label) for r, label in entry["layout"]["rows"] if r != 3]
        self.assertIn("No lesson row", s.day(date(2026, 10, 19))["classes"][0]["next_prep"]["problem"])
        entry["layout"]["rows"] += [(6, "Wk 09"), (7, "Wk 09")]
        for r in (6, 7):
            entry["cells"][r, 1] = cell("Wk 09")
            entry["cells"][r, 2] = cell("Second lesson %s" % r)
            entry["cells"][r, 3] = cell("PRINT: sheet")
        self.assertEqual([l["row"] for l in s.day(date(2026, 10, 19))["classes"][0]["next_prep"]["lessons"]], [6, 7])

    def test_projection_adds_no_provider_calls_mutations_or_false_prep_outside_week(self):
        s = source()
        before = deepcopy(s.classes)
        with patch("hub_v2.google.get_json", side_effect=AssertionError("No provider call")):
            s.day(date(2026, 10, 19))
        self.assertEqual(s.classes, before)
        self.assertTrue(all("next_prep" not in cls for cls in s.day(date(2027, 4, 1))["classes"]))
