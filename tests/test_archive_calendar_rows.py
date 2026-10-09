"""Filing-only calendar classification; fictional Agenda and disposable state."""
from copy import deepcopy
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from hub_v2 import agenda, google, student_archive as archive, work
from tests.test_cloud_state import MemoryCAS
from tests.test_hub_v2 import DIRECT, cell, grid
from tests.test_student_archive import FakeDrive, SOURCE, config, found, row


NO_FORM = "This lesson has no Form link in its 'Turn it in' column."
CALENDAR = "9/25 — A Friday, Bio + Forensics have it. A&P does not meet. (Not a real closure — only Mon 9/21 was closed.)"


def calendar_agenda():
    headers = ["Wk", "Dates & closures", "Topic", "Tue — plan", "📝 Tue note", "Thu — plan", "📝 Thu note",
               "Fri — plan", "📝 Fri note", "✅ To-do", "🧪 Lab / prep", "🎞 Student deck", "👩‍🏫 Teacher deck",
               "📁 Folder", "—", "_studentURL", "_teacherURL", "_folderURL", "_extraURL", "📤 Turn it in", "_turnInURL"]
    ap = grid({4: headers, 5: ["Wk 01", "Day one 9th only", "30 min class"],
               10: ["Wk 01", "Aug 31 – Sep 04", "Course Overview & First Day", "Basic Intro, Lab Safety",
                    "", "Body regions", "", "OFF — Schools Closed", "", "Safety sheets", "", "", "", "", "", "", "", "", "",
                    cell("Course Overview — Turn in", hyperlink=DIRECT), DIRECT],
               13: ["Wk 04", "Sep 21 – Sep 25", "", "", "", "", "", CALENDAR]})
    weeks = grid({2: ["week", "Monday"], 3: ["Wk 01", "08/31/2026"], 4: ["Wk 04", "09/21/2026"]})
    return agenda.Agenda("FictionalAgendaSource", "Fictional Agenda", {"🫀 A&P": 2, "🗂 Weeks": 1},
                         {"🫀 A&P": ap, "🗂 Weeks": weeks})


class CalendarFilingTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        stack = self.enterContext(ExitStack())
        for target, name, replacement in (
                (archive, "configured_store", lambda c: self.port),
                (google, "drive_file", self.drive.metadata), (google, "folder_children", self.drive.children),
                (google, "archive_file_id", self.drive.reserve), (google, "archive_create", self.drive.create),
                (google, "rename_item", self.drive.rename), (google, "move_item", self.drive.move)):
            stack.enter_context(patch.object(target, name, side_effect=replacement))

    def scan(self, week):
        source = calendar_agenda()
        with patch.object(work.roster, "load", return_value=({"anatomy": []}, [])), \
                patch.object(google, "form_responses", return_value=[]) as forms, \
                patch.object(work, "_metadata", return_value={}), \
                patch.object(work, "shared_submissions", return_value=([], None)), \
                patch.object(work, "lesson_folders", return_value=({}, None)), \
                patch.object(work, "list_tree", return_value=([], {})), \
                patch.object(work, "nearby_forms", return_value=[]), \
                patch.object(work, "roster_check", return_value={"sections": {}, "flags": []}):
            result = work.lesson_work("read", self.config, source, "anatomy", week)
        return source, result, forms.call_args_list

    def test_duplicate_week_still_scans_real_form_and_files_work_beside_calendar_row(self):
        source, data, calls = self.scan("Wk 01")
        # The existing menu's first-row forms count must not skip the week.
        self.assertEqual(source.week_options("anatomy")[0]["forms"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], "FICTIONALFORMID1234567890abc")
        timing, lesson = data["lessons"]
        self.assertTrue(timing["calendar_only"])
        self.assertEqual(timing["problem"], NO_FORM)  # Existing grading/work gate unchanged.
        self.assertFalse(lesson["calendar_only"])
        lesson["forms"][0]["students"] = [row(section="B1 A&P")]
        original = deepcopy(self.drive.items[SOURCE])
        result = archive.archive_work("read", "write", self.config, "anatomy", data)
        self.assertEqual((result["status"], result["filed"], result["held"]), ("verified", 1, []))
        self.assertEqual(self.drive.items[SOURCE], original)

    def test_blank_calendar_week_ignores_only_exact_no_folder_message(self):
        _, data, calls = self.scan("Wk 04")
        self.assertEqual(calls, [])
        self.assertTrue(data["lessons"][0]["calendar_only"])
        data["folder_note"] = "No turn-in folder for A&P Wk 04 was found in A&P."
        result = archive.archive_work("read", "write", self.config, "anatomy", data)
        self.assertEqual((result["status"], result["filed"], result["held"], result["problems"]), ("verified", 0, [], []))
        listing = archive.archive_listing("read", self.config, "anatomy")
        self.assertEqual(listing["filing"]["coverage"], "scanned_weeks_only")
        self.assertEqual(listing["filing"]["scans"][0]["week"], "Wk 04")

    def test_access_and_roster_failures_are_not_hidden_by_calendar_metadata(self):
        for problem in ("The A&P turn-in folder couldn't be read (denied).", "The current roster could not be read."):
            _, data, _ = self.scan("Wk 04")
            if "roster" in problem:
                data["problems"] = [problem]
            else:
                data["folder_note"] = problem
            result = archive.archive_work("read", "write", self.config, "anatomy", data)
            self.assertEqual(result["status"], "held")
            self.assertIn(problem, result["problems"])

    def test_calendar_week_with_real_source_keeps_source_and_missing_folder_hold(self):
        _, data, _ = self.scan("Wk 04")
        data["other_files"] = [row(section="B1 A&P")]
        data["folder_files"] = 1
        data["folder_note"] = "No turn-in folder for A&P Wk 04 was found in A&P."
        original = deepcopy(self.drive.items[SOURCE])
        result = archive.archive_work("read", "write", self.config, "anatomy", data)
        self.assertEqual((result["status"], result["filed"]), ("held", 1))
        self.assertIn(data["folder_note"], result["problems"])
        self.assertEqual(self.drive.items[SOURCE], original)

    def test_genuine_missing_form_and_any_other_lesson_error_remain_held(self):
        data = found()
        data["lessons"] = [{"topic": "Energy Flow", "forms": [], "calendar_only": False, "problem": NO_FORM}]
        result = archive.archive_work("read", "write", self.config, "biology", data)
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["held"][0]["problem"], NO_FORM)
        data["lessons"][0].update(calendar_only=True, problem="Source access could not be confirmed.")
        result = archive.archive_work("read", "write", self.config, "biology", data)
        self.assertEqual(result["held"][0]["problem"], "Source access could not be confirmed.")

    def test_calendar_classification_rejects_assignment_content_and_ambiguous_short_class(self):
        blank = {"topic": "", "forms": [], "materials": [], "plans": [], "todo": "", "prep": ""}
        self.assertTrue(work._calendar_only(blank))
        for update in ({"topic": "Cell Structure"}, {"topic": "—"}, {"topic": "细胞"}, {"forms": [{"form_id": "FictionalForm"}]},
                       {"materials": [{"url": "https://example.invalid/lesson"}]}, {"todo": "Finish notes"},
                       {"prep": "Lab"}, {"plans": [{"plan": "Do revisions", "note": ""}]},
                       {"plans": [{"plan": CALENDAR + " Complete the assignment.", "note": ""}]},
                       {"topic": "30 min class", "dates": "Sep 21 – Sep 25"}):
            self.assertFalse(work._calendar_only({**blank, **update}))


if __name__ == "__main__":
    unittest.main()
