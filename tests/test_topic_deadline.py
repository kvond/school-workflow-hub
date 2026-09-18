"""Third-meeting deadline rule: synthetic teacher notes and calendars only."""
import unittest

from school_workflow_hub.workflows.topic_deadline import (
    derive_deadline, detect_declarations, parse_week_calendar,
)
from datetime import date


def week(start, text):
    return parse_week_calendar(date.fromisoformat(start), text)


def calendar(**weeks):
    # keys like w20260914="..." -> "2026-09-14"
    return {f"{k[1:5]}-{k[5:7]}-{k[7:9]}": week(f"{k[1:5]}-{k[5:7]}-{k[7:9]}", text)
            for k, text in weeks.items()}


PLAIN = "Sep 14 – Sep 18, 2026\nFri 09/18 = A Friday → Bio + Forensics"
NEXT = "Sep 21 – Sep 25, 2026\n⚠ Mon 09/21 Schools Closed\nFri 09/25 = A Friday → Bio + Forensics"
BFRIDAY = "Sep 28 – Oct 02, 2026\nFri 10/02 = B Friday → A&P"


class DetectionTests(unittest.TestCase):
    def test_actual_start_in_plan_or_notes_binds_to_the_dashboard_date(self):
        for field in ("plan", "notes"):
            kwargs = {"plan": "", "notes": ""}
            kwargs[field] = "Started Energy today. Autotrophs vs heterotrophs."
            found = detect_declarations(kwargs["plan"], kwargs["notes"], "2026-09-14")
            self.assertEqual(found["start"], {"date": "2026-09-14", "field": field,
                                              "excerpt": "Started Energy today. Autotrophs vs heterotrophs."})

    def test_backdated_note_still_means_the_selected_date(self):
        found = detect_declarations("", "Started Energy.", "2026-09-10")
        self.assertEqual(found["start"]["date"], "2026-09-10")

    def test_intention_is_never_a_start(self):
        found = detect_declarations("Plan to start Energy on Wednesday.", "", "2026-09-14")
        self.assertIsNone(found["start"])
        self.assertIn("Plan to start", found["intention"]["excerpt"])
        also = detect_declarations("Will start Energy next class.", "", "2026-09-14")
        self.assertIsNone(also["start"])

    def test_explicit_deadline_exception_needs_a_real_date(self):
        found = detect_declarations("Deadline exception: due 10/3 for this one.", "", "2026-09-14")
        self.assertEqual(found["exception"]["date"], "2026-10-03")
        vague = detect_declarations("The deadline feels tight.", "", "2026-09-14")
        self.assertIsNone(vague["exception"])

    def test_plain_lesson_text_triggers_nothing(self):
        found = detect_declarations("Intro to Energy lesson · Autotroph vs heterotroph", "", "2026-09-14")
        self.assertEqual(found, {"start": None, "intention": None, "exception": None})


class CalendarTests(unittest.TestCase):
    def test_closures_and_friday_designation_parse_from_real_shapes(self):
        info = week("2026-09-21", NEXT)
        self.assertTrue(info["parsed"])
        self.assertEqual({d.isoformat() for d in info["closed"]}, {"2026-09-21"})
        self.assertEqual(info["friday_classes"], {"biology", "forensics"})
        self.assertEqual(week("2026-09-28", BFRIDAY)["friday_classes"], {"anatomy"})

    def test_mismatched_closure_dates_mark_the_week_unparsed(self):
        info = week("2026-09-21", "Sep 21 – Sep 25, 2026\n⚠ Mon 09/28 Wrong Week")
        self.assertFalse(info["parsed"])


class DeadlineTests(unittest.TestCase):
    def test_third_meeting_deadline_skips_closures_and_wrong_days(self):
        weeks = calendar(w20260914=PLAIN, w20260921=NEXT)
        # Biology starts Wed 9/16: meetings Wed 9/16, Fri 9/18 (A Friday),
        # then Mon 9/21 is closed, so meeting 3 is Wed 9/23.
        result = derive_deadline("biology", "2026-09-16", weeks)
        self.assertEqual(result["meetings"], ["2026-09-16", "2026-09-18", "2026-09-23"])
        self.assertEqual(result["due_date"], "2026-09-23")
        self.assertEqual(result["basis"], "third_meeting_rule")
        self.assertFalse(result["provisional"])
        self.assertEqual(result["ic_alignment"], "calculated_not_verified_in_ic")

    def test_b_friday_counts_only_for_anatomy(self):
        weeks = calendar(w20260928=BFRIDAY)
        weeks["2026-10-05"] = week("2026-10-05", "Oct 05 – Oct 09, 2026\nFri 10/09 = A Friday → Bio + Forensics")
        anatomy = derive_deadline("anatomy", "2026-09-29", weeks)
        # Tue 9/29, Thu 10/1, Fri 10/2 (B Friday) = third meeting.
        self.assertEqual(anatomy["meetings"], ["2026-09-29", "2026-10-01", "2026-10-02"])
        biology = derive_deadline("biology", "2026-09-28", weeks)
        # Mon 9/28, Wed 9/30, then B Friday is skipped; Mon 10/5 is meeting 3.
        self.assertEqual(biology["meetings"], ["2026-09-28", "2026-09-30", "2026-10-05"])

    def test_unknown_week_or_friday_is_provisional_not_guessed(self):
        weeks = calendar(w20260914=PLAIN)
        result = derive_deadline("biology", "2026-09-18", weeks)
        self.assertTrue(result["provisional"])
        self.assertIsNone(result["due_date"])
        self.assertEqual(result["basis"], "calendar_incomplete")
        no_friday = {"2026-09-14": {"closed": set(), "friday_classes": None, "parsed": True}}
        stuck = derive_deadline("biology", "2026-09-14", no_friday)
        self.assertTrue(stuck["provisional"])  # Wednesday counts, Friday unknowable

    def test_teacher_exception_overrides_and_start_on_closure_is_flagged(self):
        weeks = calendar(w20260921=NEXT)
        result = derive_deadline("biology", "2026-09-23", weeks, exception_date="2026-10-03")
        self.assertEqual(result["due_date"], "2026-10-03")
        self.assertEqual(result["basis"], "teacher_exception")
        flagged = derive_deadline("biology", "2026-09-21", weeks, exception_date="2026-10-03")
        self.assertIn("start_on_closure_needs_review", flagged["flags"])

    def test_the_deadline_never_moves_for_continuation(self):
        weeks = calendar(w20260914=PLAIN, w20260921=NEXT)
        first = derive_deadline("biology", "2026-09-16", weeks)
        again = derive_deadline("biology", "2026-09-16", weeks)
        self.assertEqual(first, again)  # nothing but start, calendar or exception feeds it


if __name__ == "__main__":
    unittest.main()
