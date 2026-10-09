"""Cross-week source routing and score holds with fictional school data."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from hub_v2 import completion, google, morning, work


class CrossWeekMatchingTests(unittest.TestCase):
    def entry(self, current, week, label, names=(), teacher=()):
        found = [{"files": [{"name": name}]} for name in names]
        base, words = work.lesson_words("", label, [], found)
        return {"current": current, "week": week, "label": label,
                "found": found, "base": base, "words": words,
                "teacher_signature": {work.deck_stem(name) for name in teacher}}

    def test_one_shared_word_does_not_import_another_weeks_deck(self):
        title = "Copy of ▶ LIVE — Week 07 — Evidence Collection & Chain of Custody (VT) - Fictional Student.pptx"
        entries = [self.entry(True, "Wk 05", "Types of Evidence — Turn In"),
                   self.entry(False, "Wk 06", "Turn In", [title])]
        held = []
        self.assertEqual(work.wrong_form_moves(entries, held), [])
        self.assertEqual([(i, j) for i, _, j in held], [(1, 0)])
        self.assertEqual(entries[1]["found"][0]["files"][0]["name"], title)

    def test_full_title_and_teacher_evidence_allow_a_correct_wrong_form_turn_in(self):
        title = "▶ LIVE — Week 04 — Eyewitness Accounts (VT)"
        entries = [self.entry(True, "Wk 05", "Eyewitness Testimony — Turn In", teacher=[title]),
                   self.entry(False, "Wk 06", "Turn In", ["Copy of " + title + " - Fictional Student.pptx"])]
        self.assertEqual([(i, j) for i, _, j in work.wrong_form_moves(entries)], [(1, 0)])
        entries = [self.entry(True, "Wk 05", "Investigative Processes (Steps of CSI) — Turn In"),
                   self.entry(False, "Wk 06", "Turn In", [
                       "Copy of ▶ LIVE — Week 05 — Investigative Processes (VT) - Fictional Student.pptx"])]
        self.assertEqual([(i, j) for i, _, j in work.wrong_form_moves(entries)], [(1, 0)])

    def test_mixed_upload_is_not_imported_as_one_lesson(self):
        target = self.entry(True, "Wk 05", "Types of Evidence — Turn In")
        source = self.entry(False, "Wk 06", "Turn In")
        source["found"] = [{"files": [{"name": "Week 05 — Types of Evidence"},
                                      {"name": "Week 07 — Evidence Collection & Chain of Custody"}]}]
        self.assertEqual(work.wrong_form_moves([target, source]), [])

    def test_wrong_week_stops_scoring_before_template_or_student_deck_reads(self):
        upload = {"id": "F", "name": "Week 07 — Evidence Collection & Chain of Custody",
                  "mime": google.PPTX, "other_week": 7}
        found = {"lessons": [{"forms": [{"form_id": "FORM", "students": [{"files": [upload]}]}]}]}
        with patch.object(completion, "lesson_decks") as decks, patch.object(google, "drive_file") as read:
            result = completion.score_file("token", object(), "forensics", "Wk 05", "FORM", "F", found)
        self.assertEqual(result["code"], "source_week_mismatch")
        self.assertIn("Week 7", result["problem"])
        decks.assert_not_called()
        read.assert_not_called()

    def test_moved_lesson_accepts_its_teacher_decks_original_week(self):
        name = "Copy of ▶ LIVE — Week 04 — Types of Evidence (VT) - Fictional Student.pptx"
        upload = {"id": "F", "name": name, "mime": google.PPTX,
                  "other_week": work.named_week(name, {5, 4})}
        found = {"lessons": [{"forms": [{"form_id": "FORM", "students": [{"files": [upload]}]}]}]}
        with patch.object(completion, "lesson_decks", return_value=[]), \
                patch.object(google, "drive_file", return_value={}), \
                patch.object(completion, "_read", side_effect=completion.Unreadable("fictional test")) as read:
            result = completion.score_file("token", object(), "forensics", "Wk 05", "FORM", "F", found)
        self.assertNotEqual(result.get("code"), "source_week_mismatch")
        read.assert_called_once()

    def test_rejected_nearby_import_remains_visible_and_never_becomes_a_zero(self):
        teacher = "▶ LIVE — Week 05 — Types of Evidence (VT)"
        wrong = "Copy of ▶ LIVE — Week 07 — Evidence Collection & Chain of Custody (VT) - Fictional Student.pptx"
        deck_id = "FICTIONALTYPESDECK000000"
        students = [{"first": "Fictional", "last": "Student", "section": "A2 Forensics"},
                    {"first": "Other", "last": "Student", "section": "A2 Forensics"}]

        class FakeAgenda:
            classes = {"forensics": {"name": "Forensics", "layout": {"rows": [(5, "Wk 05"), (6, "Wk 06")]}}}
            weeks = [{"label": "Wk 05", "monday": "2026-09-21"},
                     {"label": "Wk 06", "monday": "2026-09-28"}]

            def lessons(self, cls, week):
                label = "Types of Evidence — Turn In" if week == "Wk 05" else "Turn In"
                return [{"row": 5, "topic": "Types of Evidence" if week == "Wk 05" else "Laboratory Skills",
                         "edit_url": "", "forms": [{"form_id": week, "label": label, "url": "", "problem": None}],
                         "materials": [{"url": "https://docs.google.com/presentation/d/" + deck_id + "/edit"}]
                         if week == "Wk 05" else []}]

        current = {"source": "form", "event": True, "email": "s.other.student@school.example", "key": "CURRENT",
                   "submitted_at": "2026-09-21T12:00:00Z", "files": [{"id": "CURRENT", "name": teacher, "mime": google.PPTX}],
                   "typed": []}
        nearby = {"source": "form", "event": True, "email": "s.fictional.student@school.example", "key": "NEARBY",
                  "submitted_at": "2026-09-28T12:00:00Z", "files": [{"id": "NEARBY", "name": wrong, "mime": google.PPTX}],
                  "typed": []}
        current_form = {"form_id": "Wk 05", "label": "Types of Evidence — Turn In", "url": "", "problem": None,
                        "responses": 1, "found": [current], "name": "Types Form", "folders": set()}
        names = {deck_id: teacher, "CURRENT": teacher, "NEARBY": wrong}
        with patch.object(work.roster, "load", return_value=({"forensics": students}, [])), \
                patch.object(work, "read_form", return_value=current_form), \
                patch.object(work, "recent_submissions", return_value=[nearby]), \
                patch.object(google, "drive_file", side_effect=lambda token, fid, fields=None: {
                    "id": fid, "name": names.get(fid, fid), "mimeType": google.PPTX}):
            found = work.lesson_work("token", {"HUB_SCHOOL_EMAIL_DOMAINS": "school.example"},
                                     FakeAgenda(), "forensics", "Wk 05")
        form = found["lessons"][0]["forms"][0]
        self.assertEqual([f["id"] for row in form["students"] for f in row["files"]], ["CURRENT"])
        self.assertEqual(form["elsewhere"][0]["file"], wrong)
        self.assertIn("lesson match not confirmed", form["elsewhere"][0]["to"])
        values = morning.rows(form, [(form["students"][0], {"notes_score": 8, "revision_score": 7})],
                              datetime(2026, 10, 8, tzinfo=timezone.utc))
        review = next(row for row in values if row["first"] == "Fictional")
        self.assertEqual(review["kind"], "check")
        self.assertIsNone(review["notes"])
        self.assertIsNone(review["revision"])
        self.assertEqual(nearby["files"][0]["name"], wrong)


if __name__ == "__main__":
    unittest.main()
