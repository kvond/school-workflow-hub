"""Explicit Agenda dates and week identities; all input is fictional."""
from datetime import date, datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from hub_v2 import agenda, app, audit, ledger, work
from tests.test_hub_v2 import DIRECT, SECOND, cell, grid


def calendar_fixture():
    calendar = grid({
        1: ["takes over on", "week", "Monday", "dates & closures"],
        2: ["10/12/2026", "Wk 07", "10/12/2026", "Oct 12–16"],
        3: ["11/23/2026", "Wk 13 — closed all week", "11/23/2026", "No class"],
        4: ["12/14/2026", "Wk 16", "12/14/2026", "Dec 14–18"],
        5: ["12/21/2026", "Wk 16b — Mon/Tue only", "12/21/2026", "Dec 21–22"],
        6: ["12/23/2026", "winter break — next is Wk 17", "01/04/2027"],
        7: ["01/04/2027", "Wk 17", "01/04/2027", "Jan 4–8"],
        8: ["03/29/2027", "Wk 29 — spring break", "03/29/2027", "No class"],
        9: ["06/07/2027", "Wk 39 — last student day Fri 06/11", "06/07/2027", "Jun 7–11"],
    })
    classes = grid({
        1: ["Wk", "Dates & closures", "Topic", "Wed — plan", "Turn it in"],
        2: ["Wk 07", "Oct 12–16", "First topic", "First plan", cell("First", hyperlink=DIRECT)],
        3: ["Wk 07", "Oct 12–16", "Second topic", "Second plan", cell("Second", hyperlink=SECOND)],
        4: ["Wk 13", "Nov 23–27", "No class — 0 meetings"],
        5: ["Wk 16", "Dec 14–18", "Earlier topic"],
        6: ["Wk 16b", "Dec 21–22", "Short week topic"],
        7: ["Wk 17", "Jan 4–8", "Reopening topic"],
        8: ["Wk 29", "Mar 29–Apr 2", "No class — 0 meetings"],
        9: ["Wk 39", "Jun 7–11", "Final topic"],
    })
    return agenda.Agenda("FICTIONAL_AGENDA", "Fictional Agenda",
                         {"Weeks": 1, "Biology": 2}, {"Weeks": calendar, "Biology": classes})


class CalendarAlignmentTests(TestCase):
    def test_source_checks_do_not_jump_ahead_on_sunday_evening(self):
        # Cloud UTC has reached Monday; school time is still Sunday evening.
        with patch.object(ledger, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)
            self.assertEqual(audit.school_today(), date(2026, 10, 11))
            clock.now.return_value = datetime(2026, 10, 12, 11, tzinfo=timezone.utc)
            self.assertEqual(audit.school_today(), date(2026, 10, 12))

    def test_calendar_annotations_join_to_exact_class_week_rows(self):
        source = calendar_fixture()
        for day, label, topic in (
            ("2026-11-25", "Wk 13", "No class — 0 meetings"),
            ("2026-12-22", "Wk 16b", "Short week topic"),
            ("2027-03-31", "Wk 29", "No class — 0 meetings"),
            ("2027-06-09", "Wk 39", "Final topic"),
        ):
            with self.subTest(day=day):
                result = source.day(date.fromisoformat(day))
                self.assertEqual(result["week"]["label"], label)
                self.assertEqual(result["classes"][0]["lessons"][0]["topic"], topic)
                self.assertIsNone(result["classes"][0]["problem"])

    def test_short_week_stops_at_explicit_break_takeover(self):
        source = calendar_fixture()
        self.assertEqual(source.week_for(date(2026, 12, 21))["label"], "Wk 16b")
        self.assertEqual(source.week_for(date(2026, 12, 22))["label"], "Wk 16b")
        for day in (date(2026, 12, 23), date(2026, 12, 27), date(2027, 1, 3)):
            self.assertIsNone(source.week_for(day))

    def test_break_handoff_cannot_shadow_reopening_week(self):
        source = calendar_fixture()
        result = source.day(date(2027, 1, 4))
        self.assertEqual(result["week"]["label"], "Wk 17")
        self.assertEqual(result["classes"][0]["lessons"][0]["topic"], "Reopening topic")
        self.assertEqual([w["label"] for w in source.weeks].count("Wk 17"), 1)

    def test_grade_date_scope_uses_same_explicit_calendar_without_merging_16b(self):
        source = calendar_fixture()
        self.assertEqual(work.week_monday(source, "Wk 16"), date(2026, 12, 14))
        self.assertEqual(work.week_monday(source, "Wk 16b"), date(2026, 12, 21))
        self.assertEqual(work._school_year(source, "Wk 39"), "2026-27")

    def test_week_selector_preserves_all_lesson_rows_and_distinct_forms(self):
        source = calendar_fixture()
        result = source.day(date(2026, 10, 14))
        self.assertEqual(result["week"]["monday"], "2026-10-12")
        self.assertEqual([l["today"]["plan"] for l in result["classes"][0]["lessons"]],
                         ["First plan", "Second plan"])
        options = source.week_options("biology")
        self.assertEqual(options[0], {"label": "Wk 07", "topic": "First topic / Second topic",
                                      "monday": "2026-10-12", "forms": 2})
        self.assertTrue(all(w["monday"] for w in options))

    def test_api_current_week_is_a_selectable_class_week(self):
        source = calendar_fixture()
        with patch.object(app.google, "service_token", return_value="fictional"), \
                patch.object(app, "load_agenda", return_value=source):
            result = app.api("api/weeks", {"class": ["biology"], "today": ["2026-12-22"]}, {})
        self.assertEqual(result["current"], "Wk 16b")
        self.assertIn(result["current"], [w["label"] for w in result["weeks"]])
