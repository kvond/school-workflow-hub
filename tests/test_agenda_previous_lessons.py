"""Lessons: last week's lesson links next to this week's; all input is fictional."""
from datetime import date
from unittest import TestCase

from hub_v2 import agenda
from tests.test_hub_v2 import DECK, DIRECT, SECOND, cell, grid


def fixture():
    calendar = grid({
        1: ["week", "Monday", "dates & closures"],
        2: ["Wk 05", "09/28/2026", "Sep 28–Oct 2"],
        3: ["Wk 06", "10/05/2026", "Oct 5–9"],
        4: ["Wk 07", "10/12/2026", "Oct 12–16"],
        5: ["Wk 08", "10/19/2026", "Oct 19–23"],
        6: ["Wk 09", "10/26/2026", "Oct 26–30"],
        7: ["Wk 10", "11/02/2026", "Nov 2–6"],
        8: ["Wk 11", "11/09/2026", "Nov 9–13"],
        9: ["Wk 12", "11/16/2026", "Nov 16–20"],
        10: ["Wk 13", "11/23/2026", "Nov 23–27"],
        11: ["Wk 14", "11/30/2026", "Nov 30–Dec 4"],
    })
    classes = grid({
        1: ["Wk", "Topic", "Teacher deck", "_teacherURL", "Turn it in"],
        2: ["Wk 05", "Oldest topic", "", "", ""],
        3: ["Wk 06", "Sixth topic", "", "", ""],
        4: ["Wk 07", "First topic", cell("Deck", hyperlink=DECK), "", cell("First", hyperlink=DIRECT)],
        5: ["Wk 07", "Second topic", "", "", cell("Second", hyperlink=SECOND)],
        6: ["Wk 08", "Current topic", "", "", ""],
        7: ["Wk 09", "Before the break", "", "", ""],
        8: ["Wk 10", "No class — 0 meetings", "", "", ""],
        9: ["Wk 11", "[EMPTY — plan later]", "", "", ""],
        10: ["Wk 12", "", "", "", ""],
        11: ["Wk 13", "No class — 0 meetings", "", "", ""],
        12: ["Wk 14", "Long after", "", "", ""],
    })
    return agenda.Agenda("FICTIONAL_AGENDA", "Fictional Agenda",
                         {"Weeks": 1, "Biology": 2}, {"Weeks": calendar, "Biology": classes})


def biology(source, day):
    return source.day(date.fromisoformat(day))["classes"][0]


class PreviousLessonsTests(TestCase):
    def test_previous_week_lists_every_lesson_with_its_links(self):
        previous = biology(fixture(), "2026-10-21")["previous"]
        self.assertEqual(previous["week"], "Wk 07")
        self.assertEqual(previous["monday"], "2026-10-12")
        self.assertEqual([l["topic"] for l in previous["lessons"]], ["First topic", "Second topic"])
        first = previous["lessons"][0]
        self.assertEqual(first["materials"], [{"label": "Teacher deck", "url": DECK}])
        self.assertEqual([f["url"] for f in first["forms"]], [DIRECT])
        self.assertEqual([f["url"] for f in previous["lessons"][1]["forms"]], [SECOND])
        self.assertTrue(first["edit_url"].endswith("range=A4"))

    def test_this_weeks_lessons_are_unchanged(self):
        result = biology(fixture(), "2026-10-21")
        self.assertEqual([l["topic"] for l in result["lessons"]], ["Current topic"])

    def test_break_and_unplanned_weeks_are_skipped(self):
        # From Wk 12: Wk 11 is an [EMPTY] placeholder and Wk 10 has no class,
        # so the last taught week is Wk 09.
        self.assertEqual(biology(fixture(), "2026-11-16")["previous"]["week"], "Wk 09")
        self.assertEqual(biology(fixture(), "2026-11-02")["previous"]["week"], "Wk 09")
        # From Wk 13: Wk 12 is blank, then Wk 11 and Wk 10; three weeks is the limit.
        self.assertIsNone(biology(fixture(), "2026-11-23")["previous"])

    def test_look_back_is_limited(self):
        # Wk 14: Wk 13, 12 and 11 are all without a lesson; nothing older is offered.
        self.assertIsNone(biology(fixture(), "2026-12-02")["previous"])

    def test_first_week_has_no_previous(self):
        self.assertIsNone(biology(fixture(), "2026-09-30")["previous"])

    def test_no_previous_key_outside_an_agenda_week(self):
        self.assertNotIn("previous", biology(fixture(), "2027-03-01"))
