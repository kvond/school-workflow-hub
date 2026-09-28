"""Teaching Hub v2 on fictional data only; no Google call is made."""
import io
import json
import unittest
import unittest.mock
import zipfile
from datetime import date, datetime, timezone
from unittest.mock import patch

from hub_v2 import agenda, agenda_edit, answers, app, audit, completion, feedback, google, ledger, roster, routine, tidy, work

DIRECT = "https://docs.google.com/forms/d/FICTIONALFORMID1234567890abc/viewform"
SECOND = "https://docs.google.com/forms/d/FICTIONALFORMID0987654321xyz/viewform"
PUBLISHED = "https://docs.google.com/forms/d/e/1FAIpQLSfictionalPublishedId000/viewform"
DECK = "https://docs.google.com/presentation/d/FICTIONALDECK1234567890/edit"


def cell(value="", **extra):
    return {"formattedValue": value, **extra}


def grid(rows):
    return {(r, c): v if isinstance(v, dict) else cell(v)
            for r, values in rows.items() for c, v in enumerate(values, 1) if v != ""}


def class_tab(days, forms_cell):
    headers = ["Wk", "Dates & closures", "Topic"]
    for day in days:
        headers += ["%s — plan" % day, "📝 %s note" % day]
    headers += ["✅ To-do", "🧪 Lab / prep", "📄 Student deck", "👩‍🏫 Teacher deck", "📁 Folder",
                "🧪 Activity / print set", "_studentURL", "_teacherURL", "_folderURL", "_extraURL",
                "📤 Turn it in", "_turnInURL"]
    row = ["Wk 04", "Sep 21 – Sep 25", "Fictional topic"]
    for day in days:
        row += [day + " plan text", day + " note"]
    row += ["Copy handouts", "Goggles", "", "", "", "", DECK.replace("/edit", "/copy"), DECK,
            "https://drive.google.com/drive/folders/FICTIONALFOLDER123", "", forms_cell, DIRECT]
    return grid({1: ["Instructions"], 3: headers, 4: ["Wk 03", "", "Earlier topic"], 5: row})


def fixture(drop=()):
    two_links = cell("Reasoning · Observation", textFormatRuns=[
        {"startIndex": 0, "format": {"link": {"uri": DIRECT}}},
        {"startIndex": 9, "format": {}},
        {"startIndex": 12, "format": {"link": {"uri": SECOND}}}])
    tabs = {"🗂 Weeks": 1, "🌿 Biology": 2, "🔬 Forensics": 3, "🫀 A&P": 4, "🌿 Biology List": 5}
    grids = {
        "🗂 Weeks": grid({1: ["ENGINE"], 2: ["", "course row", "week", "Monday", "dates & closures"],
                          3: ["", "4", "Wk 03", "09/14/2026", "Mon closed"],
                          4: ["", "5", "Wk 04", "09/21/2026", ""]}),
        "🌿 Biology": class_tab(("Mon", "Wed", "Fri"), cell("Turn it in", hyperlink=DIRECT)),
        "🔬 Forensics": class_tab(("Mon", "Wed", "Fri"), two_links),
        "🫀 A&P": class_tab(("Tue", "Thu", "Fri"), cell(PUBLISHED)),
    }
    for name in drop:
        tabs.pop(name)
        grids.pop(name, None)
    return agenda.Agenda("FICTIONALSHEET", "Fictional Agenda", tabs, grids)


class LinkTests(unittest.TestCase):
    def test_rich_text_links_keep_their_labels(self):
        found = agenda.links(fixture().classes["forensics"]["cells"][5, 20])
        self.assertEqual(found, [(DIRECT, "Reasoning"), (SECOND, "Observation")])

    def test_hyperlink_formula_and_pasted_url(self):
        formula = cell("Lab", userEnteredValue={"formulaValue": '=HYPERLINK("%s","Lab report")' % DIRECT})
        self.assertEqual(agenda.links(formula), [(DIRECT, "Lab report")])
        self.assertEqual(agenda.links(cell("see %s." % DECK)), [(DECK, "")])

    def test_form_links(self):
        self.assertEqual(agenda.form_link(DIRECT)["form_id"], "FICTIONALFORMID1234567890abc")
        self.assertIsNone(agenda.form_link(PUBLISHED)["form_id"])
        self.assertIn("published", agenda.form_link(PUBLISHED)["problem"])
        self.assertIsNone(agenda.form_link(DECK))


class AgendaTests(unittest.TestCase):
    def test_day_shows_each_class_plan(self):
        day = fixture().day(date(2026, 9, 23))
        self.assertEqual(day["week"]["label"], "Wk 04")
        bio, forensics, anatomy = day["classes"]
        self.assertEqual(bio["lessons"][0]["today"]["plan"], "Wed plan text")
        self.assertEqual(bio["lessons"][0]["today"]["note"], "Wed note")
        self.assertEqual(bio["lessons"][0]["todo"], "Copy handouts")
        self.assertEqual([m["label"] for m in bio["lessons"][0]["materials"]],
                         ["Teacher deck", "Student copy", "Lesson folder"])
        self.assertIsNone(anatomy["lessons"][0]["today"])
        self.assertEqual([f["label"] for f in forensics["lessons"][0]["forms"]], ["Reasoning", "Observation"])
        # A&P's named link is a published Form link; the hidden direct link is used instead.
        self.assertEqual([(f["form_id"], f["problem"]) for f in anatomy["lessons"][0]["forms"]],
                         [("FICTIONALFORMID1234567890abc", None)])
        self.assertIn("#gid=3&range=A5", forensics["lessons"][0]["edit_url"])
        self.assertEqual(day["problems"], [])

    def test_missing_tab_only_affects_that_class(self):
        day = fixture(drop=("🫀 A&P",)).day(date(2026, 9, 21))
        self.assertIn("No 'A&P' tab", day["classes"][2]["problem"])
        self.assertEqual(day["classes"][0]["lessons"][0]["today"]["plan"], "Mon plan text")

    def test_missing_weeks_tab_is_a_note_not_a_failure(self):
        day = fixture(drop=("🗂 Weeks",)).day(date(2026, 9, 23))
        self.assertIsNone(day["week"])
        self.assertTrue(day["problems"])

    def test_date_outside_weeks(self):
        day = fixture().day(date(2027, 1, 4))
        self.assertIsNone(day["week"])
        self.assertIn("isn't in any week", day["problems"][0])

    def test_a_second_deck_column_is_read_as_a_lesson_material(self):
        cells = grid({1: ["Wk", "Topic", "🎞 2nd deck", "📤 Turn it in"],
                      2: ["Wk 03", "Two assignments", "https://docs.google.com/presentation/d/FICTIONALSECONDDECK12345/edit?slide=id.g1", ""]})
        layout, problem = agenda.parse_class_tab(cells)
        lesson = agenda.build_lesson(cells, layout, 2, "")
        self.assertEqual([(m["label"], m["url"]) for m in lesson["materials"]],
                         [("2nd deck", "https://docs.google.com/presentation/d/FICTIONALSECONDDECK12345/edit?slide=id.g1")])

    def test_plain_text_cells_are_editable_and_cells_with_links_or_formulas_are_not(self):
        lesson = fixture().day(date(2026, 9, 23))["classes"][0]["lessons"][0]
        self.assertEqual(sorted(lesson["editable"]), sorted(
            ["topic", "todo", "prep", "plan:Mon", "note:Mon", "plan:Wed", "note:Wed", "plan:Fri", "note:Fri"]))
        self.assertTrue(all(lesson["editable"].values()))
        self.assertFalse(agenda.plain(cell("Deck", hyperlink=DECK)))
        self.assertFalse(agenda.plain(cell("Deck", textFormatRuns=[{"startIndex": 0, "format": {"link": {"uri": DECK}}}])))
        self.assertFalse(agenda.plain(cell("4", userEnteredValue={"formulaValue": "=2+2"})))
        self.assertTrue(agenda.plain(cell("See " + DECK)))  # a URL typed as text is kept as typed
        self.assertTrue(agenda.plain(None))

    def test_a_second_turn_in_column_named_above_the_header_row_is_read(self):
        cells = grid({2: ["Notes above", "", "", "", "2nd turn it in"],
                      3: ["Wk", "Topic", "📤 Turn it in", "_turnInURL", ""],
                      4: ["Wk 05", "Two assignments", cell("Evidence", hyperlink=DIRECT), "",
                          cell("Second", hyperlink=SECOND)],
                      5: ["Wk 06", "Short link", cell("Main", hyperlink=DIRECT), "",
                          cell("Quick", hyperlink="https://forms.gle/fictional123")]})
        layout, problem = agenda.parse_class_tab(cells)
        self.assertIsNone(problem)
        two = agenda.build_lesson(cells, layout, 4, "")["forms"]
        self.assertEqual([(f["form_id"], f["label"]) for f in two],
                         [("FICTIONALFORMID1234567890abc", "Evidence"), ("FICTIONALFORMID0987654321xyz", "Second")])
        short = agenda.build_lesson(cells, layout, 5, "")["forms"]
        self.assertIn("forms.gle", short[1]["problem"])  # shown on Student work so the link gets replaced

    def test_week_options(self):
        options = fixture().week_options("forensics")
        self.assertEqual([(o["label"], o["topic"], o["forms"]) for o in options],
                         [("Wk 03", "Earlier topic", 0), ("Wk 04", "Fictional topic", 2)])


ROSTER = grid({1: ["Last Name", "First Name"], 2: ["Rivera", "Ana"], 3: ["Okafor", "Chidi (CJ)"],
               4: [""], 5: ["A3 Forensics"], 6: ["Lee, Sam"], 7: ["Moss", ""]})


class RosterTests(unittest.TestCase):
    def test_parse_tab_sections_nicknames_and_skips(self):
        students, skipped = roster.parse_tab(ROSTER, "forensics", "A2 Forensics")
        self.assertEqual([(s["last"], s["first"], s["section"]) for s in students],
                         [("Rivera", "Ana", "A2 Forensics"), ("Okafor", "Chidi (CJ)", "A2 Forensics"),
                          ("Lee", "Sam", "A3 Forensics")])
        self.assertEqual(skipped, 1)

    def setUp(self):
        roster._ORIENTATION.clear()

    def test_a_title_above_the_list_is_not_a_student_or_a_warning(self):
        cells = grid({1: ["0422-2 Biology · Roster 2026–27"], 2: [""], 3: ["Last Name", "First Name"],
                      4: ["Rivera", "Ana"], 5: ["Jane Doe"]})
        students, skipped = roster.parse_tab(cells, "biology", "A4 Biology")
        self.assertEqual([(s["last"], s["first"]) for s in students], [("Rivera", "Ana")])
        self.assertEqual(skipped, 1)  # a one-cell row among the students is still reported

    def test_an_added_column_gives_each_new_student_the_day_they_joined(self):
        cells = grid({1: ["Last Name", "First Name", "Added"], 2: ["Rivera", "Ana", ""],
                      3: ["Okafor", "Chidi", "10/01/2026"], 4: ["Lee", "Sam", "2026-10-06"], 5: ["Kim", "Jo", "soon"]})
        students, _ = roster.parse_tab(cells, "biology", "A4 Biology")
        self.assertEqual([(s["last"], s["added"]) for s in students],
                         [("Rivera", ""), ("Okafor", "2026-10-01"), ("Lee", "2026-10-06"), ("Kim", "")])
        # No header row: "Added" typed in row 1 beside the first student still marks the column.
        cells = grid({1: ["Rivera, Ana", "", "Added"], 2: ["Okafor, Chidi", "", "9/30/26"]})
        students, _ = roster.parse_tab(cells, "biology", "A4 Biology")
        self.assertEqual([s["added"] for s in students], ["", "2026-09-30"])

    def test_a_student_added_after_an_assignment_began_is_excused_not_zero(self):
        missing = [{"last": "Rivera", "added": ""}, {"last": "Okafor", "added": "2026-09-28"},
                   {"last": "Lee", "added": "2026-10-06"}]
        still, excused = work.excuse_new(missing, date(2026, 9, 28))
        self.assertEqual(([m["last"] for m in still], [m["last"] for m in excused]), (["Rivera", "Okafor"], ["Lee"]))
        self.assertEqual(work.excuse_new(missing, None), (missing, []))  # not begun: nothing to excuse

    def test_the_systems_own_documents_are_never_student_work(self):
        for name in ("Growth record — Forensics Wk 04 — Eyewitness — 2026-09-25",
                     "Class growth summary — Biology Wk 03 — Energy — 2026-09-25",
                     "Feedback — Forensics Wk 04 — Eyewitness — 2026-09-25",
                     "Summary ratings — Biology — 2026-10-05", "Teaching summary — Biology — 2026-10-05",
                     "Monday run — 2026-09-28",
                     # Teacher Claude sometimes joins the words with underscores or hyphens.
                     "Growth_record_Forensics_Week_04_Eyewitness_Accounts_2026-09-25",
                     "Class_growth_summary_Forensics_Week_04", "class-growth-summary-biology",
                     "Forensics_Week_04_Eyewitness_Accounts_Student_Feedback", "Teaching_summary_Biology"):
            self.assertTrue(work.NOT_STUDENT_WORK.search(name), name)
        self.assertIsNone(work.NOT_STUDENT_WORK.search("s.ana.rivera — 09-16 12.41 — Copy of Energy Flow - Ana Rivera"))

    def test_headerless_first_last_tab_is_turned_round_by_school_usernames(self):
        # Like the Biology tab: no header row, First | Last columns.
        tab = grid({1: ["Ana", "Rivera"], 2: ["Chidi", "Okafor"], 3: ["Sam", "Lee"]})
        students, _ = roster.parse_tab(tab, "biology", "A4 Biology")
        self.assertEqual(students[0]["last"], "Ana")  # assumed Last | First at first
        roster.orient(students, {roster.username_key("s.ana.rivera@school.example"),
                                 roster.username_key("s.chidi.okafor")})
        self.assertEqual([(s["last"], s["first"]) for s in students],
                         [("Rivera", "Ana"), ("Okafor", "Chidi"), ("Lee", "Sam")])
        self.assertEqual(roster.match_email("s.sam.lee@school.example", students)["last"], "Lee")
        # The decision is remembered for a later lesson with no submissions yet.
        again, _ = roster.parse_tab(tab, "biology", "A4 Biology")
        roster.orient(again, set())
        self.assertEqual(again[0]["last"], "Rivera")

    def test_configured_layout_and_header_rows_are_never_turned_round(self):
        tab = grid({1: ["Ana", "Rivera"], 2: ["Chidi", "Okafor"]})
        students, _ = roster.parse_tab(tab, "biology", "A4 Biology", {"A4 Biology": "first_last_columns"})
        self.assertEqual([(s["last"], s["order"]) for s in students], [("Rivera", "config"), ("Okafor", "config")])
        headed, _ = roster.parse_tab(ROSTER, "forensics", "A2 Forensics")
        roster.orient(headed, {"riveraana", "okaforchidi"})
        self.assertEqual(headed[0]["last"], "Rivera")

    def test_matching(self):
        students, _ = roster.parse_tab(ROSTER, "forensics", "A2 Forensics")
        self.assertEqual(roster.match_email("s.ana.rivera@school.example", students, {"school.example"})["last"], "Rivera")
        self.assertEqual(roster.match_email("s.cjokafor@school.example", students)["last"], "Okafor")
        self.assertIsNone(roster.match_email("s.ana.rivera@elsewhere.example", students, {"school.example"}))
        self.assertEqual(roster.match_name("Sam Lee", students)["last"], "Lee")
        self.assertEqual(roster.uploader_name("Eyewitness deck - Sam Lee.pptx"), "Sam Lee")


def response(email, when, *files, typed=()):
    answers = {"q1": {"fileUploadAnswers": {"answers": [
        {"fileId": f, "fileName": name, "mimeType": "application/vnd.google-apps.presentation"}
        for f, name in files]}}}
    if typed:
        answers["q2"] = {"textAnswers": {"answers": [{"value": v} for v in typed]}}
    return {"responseId": email + when, "respondentEmail": email, "lastSubmittedTime": when, "answers": answers}


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.students, _ = roster.parse_tab(ROSTER, "forensics", "A2 Forensics")

    def test_latest_response_wins_and_nobody_is_dropped(self):
        found = work.submissions([
            response("s.ana.rivera@school.example", "2026-09-21T14:00:00Z", ("FILEOLD", "Deck - Ana Rivera")),
            response("s.ana.rivera@school.example", "2026-09-22T14:00:00Z", ("FILENEW", "Deck - Ana Rivera")),
            response("12345@school.example", "2026-09-22T15:00:00Z", ("FILESAM", "Deck - Sam Lee")),
            response("visitor@school.example", "2026-09-22T16:00:00Z", ("FILEX", "Deck")),
        ])
        rows, missing = work.group(found, self.students, {"school.example"})
        self.assertEqual([(r["last"], r["matched_by"]) for r in rows],
                         [("Rivera", "email"), ("Lee", "file name"), ("", None)])
        self.assertEqual(rows[0]["files"][0]["id"], "FILENEW")
        self.assertEqual(rows[0]["earlier"][0]["files"][0]["id"], "FILEOLD")
        self.assertEqual(rows[2]["email"], "visitor@school.example")
        self.assertEqual([m["last"] for m in missing], ["Okafor"])

    def test_work_after_two_weeks_from_the_first_turn_in_is_late_and_not_graded(self):
        found = work.submissions([
            response("s.ana.rivera@school.example", "2026-09-25T14:00:00.123Z", ("ONTIME", "Deck - Ana Rivera")),
            response("s.ana.rivera@school.example", "2026-10-12T14:00:00Z", ("REDO", "Deck - Ana Rivera")),
            response("s.chidi.okafor@school.example", "2026-10-10T04:00:00Z", ("LATE", "Deck - Chidi Okafor")),
        ])
        # A copy's creation time isn't a turn-in, so it can't open the window early.
        found.append(work.folder_submission({"id": "COPY", "name": "Deck - Sam Lee", "createdTime": "2026-09-01T12:00:00Z"}))
        closes = work.window(found)  # first turn-in Fri Sep 25 -> open through Fri Oct 9
        self.assertEqual((closes["opened"], closes["label"], closes["at"].isoformat()),
                         ("Fri Sep 25", "Fri Oct 9, 11:59 PM", "2026-10-10T03:59:59+00:00"))
        rows, missing = work.group(found, self.students, {"school.example"}, cutoff=closes["at"])
        chidi, ana, sam = rows  # listed by section, then last name
        self.assertEqual((ana["late"], ana["files"][0]["id"], ana["earlier"][0]["late"]), (False, "ONTIME", True))
        self.assertEqual((chidi["late"], chidi["files"][0]["late"]), (True, True))
        self.assertEqual(missing, [])
        # A turn-in before the lesson's own Agenda week doesn't start the two weeks early.
        later = work.window(found, not_before=date(2026, 10, 5))
        self.assertEqual((later["opened"], later["label"]), ("Mon Oct 5", "Mon Oct 19, 11:59 PM"))
        # The page lists 0s only from the start of that day, school time.
        self.assertEqual(later["from"].isoformat(), "2026-10-05T04:00:00+00:00")
        self.assertEqual(work.window(found, not_before=date(2026, 9, 21))["opened"], "Fri Sep 25")

    def test_each_form_starts_no_earlier_than_its_first_agenda_week(self):
        starts = work.form_weeks(fixture(), "biology")
        self.assertEqual(starts, {"FICTIONALFORMID1234567890abc": date(2026, 9, 21)})

    def test_roster_check_flags_work_from_another_class_or_no_roster(self):
        biology, _ = roster.parse_tab(grid({1: ["Last Name", "First Name"], 2: ["Riggs", "Kay"]}),
                                      "biology", "A4 Biology")
        config = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
        responses = [response("s.ana.rivera@school.example", "2026-09-22T14:00:00Z", ("F1", "Deck")),
                     response("s.kay.riggs@school.example", "2026-09-22T14:00:00Z", ("F2", "Deck")),
                     response("s.new.student@school.example", "2026-09-22T14:00:00Z", ("F3", "Deck"))]
        config["HUB_SCHOOL_EMAIL_DOMAINS"] = "school.example"
        with patch.object(work.roster, "load", return_value=({"forensics": self.students, "biology": biology}, [])), \
                patch.object(work, "_now", return_value=datetime(2026, 9, 24, 12, tzinfo=timezone.utc)), \
                patch.object(google, "form_responses", side_effect=lambda t, form_id: responses
                             if form_id == "FICTIONALFORMID1234567890abc" else []), \
                patch.object(google, "drive_file", return_value={"name": "Deck", "trashed": False,
                             "mimeType": "application/vnd.google-apps.presentation"}):
            result = work.lesson_work("token", config, fixture(), "forensics", "Wk 04")
        check = result["roster_check"]
        self.assertEqual(check["sections"], {"A2 Forensics": 2, "A3 Forensics": 1})
        self.assertEqual([(f["kind"], f["who"], f["section"]) for f in check["flags"]],
                         [("other_class", "Riggs, Kay", "A4 Biology"),
                          ("no_roster", "s.new.student@school.example", "")])
        notes = [r.get("roster_note") for r in result["lessons"][0]["forms"][0]["students"] if not r["matched"]]
        self.assertEqual(notes, ["On the A4 Biology roster", "Not on any class roster"])
        # A week later the same work is old news: the flags are dropped.
        with patch.object(work, "_now", return_value=datetime(2026, 10, 1, 12, tzinfo=timezone.utc)):
            later = work.roster_check("token", config, {"forensics": self.students, "biology": biology}, "forensics",
                                      result["lessons"], [], {"school.example"})
        self.assertEqual(later["flags"], [])

    def test_lesson_work_reports_a_form_it_cannot_read(self):
        def responses(token, form_id):
            if form_id == "FICTIONALFORMID0987654321xyz":
                raise google.GoogleError("denied", 403)
            return [response("s.ana.rivera@school.example", "2026-09-22T14:00:00Z", ("FILENEW", "Deck"))]
        config = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", side_effect=responses), \
                patch.object(google, "drive_file", return_value={"name": "Ana deck", "trashed": False,
                             "mimeType": "application/vnd.google-apps.presentation"}):
            result = work.lesson_work("token", config, fixture(), "forensics", "Wk 04")
        first, second = result["lessons"][0]["forms"]
        self.assertEqual(first["students"][0]["files"][0]["name"], "Ana deck")
        self.assertTrue(first["students"][0]["files"][0]["url"].endswith("/FILENEW/edit"))
        self.assertIn("reader@fictional.example", second["problem"])
        self.assertEqual(result["problems"], [])


SLIDES = "application/vnd.google-apps.presentation"


def named(tree, name):
    """Fake Drive name search over a fake folder tree."""
    return [{**item, "parents": [parent]} for parent, items in tree.items() for item in items if item["name"] == name]


def drive_tree():
    """00 ALL TURN IN / 🔬 Forensics / 📥 Wk 04 / Form (File responses) / question folder."""
    def item(ident, name, mime=SLIDES, created="2026-09-20T12:00:00Z"):
        return {"id": ident, "name": name, "mimeType": mime, "createdTime": created}
    folder = "application/vnd.google-apps.folder"
    return {
        "ROOT": [item("CLASSF", "🔬 Forensics", folder), item("CLASSB", "🌿 Biology", folder)],
        "CLASSF": [item("WK03", "📥 Wk 03 — Earlier — 2026-09-14", folder),
                   item("WK04", "📥 Wk 04 — Fictional topic — 2026-09-23", folder)],
        "WK04": [item("FORMFOLDER", "Fictional Wk 04 Form (File responses)", folder),
                 item("LOOSE", "Loose deck - Sam Lee")],
        "FORMFOLDER": [item("QFOLDER", "Upload your deck (File responses)", folder)],
        "QFOLDER": [item("FILENEW", "Copy of Week 04 deck - Ana Rivera"),
                    item("OLDANA", "s.ana.rivera — 09-16 12.40 — Copy of Week 04 deck - Ana Rivera"),
                    item("OLDCHIDI", "s.chidi.okafor — 09-16 12.41 — Copy of Week 04 deck - Chidi Okafor"),
                    item("FEEDBACK", "Growth Report - Ana Rivera", "application/vnd.google-apps.document")],
    }


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.students, _ = roster.parse_tab(ROSTER, "forensics", "A2 Forensics")
        self.config = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT", "HUB_SCHOOL_EMAIL_DOMAINS": "school.example",
                       "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}

    def run_work(self, responses, agenda_fixture=None):
        tree = drive_tree()

        names = {"FICTIONALFORMID1234567890abc": "Fictional Wk 04 Form",
                 "FICTIONALFORMID0987654321xyz": "Second Form",
                 "SHEPPARD": "Copy of Week 01 Sheppard deck - Ana Rivera"}

        def drive_file(token, file_id):
            return {"name": names.get(file_id, file_id), "mimeType": SLIDES, "trashed": False}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", side_effect=lambda t, form_id: responses.get(form_id, [])), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=drive_file):
            return work.lesson_work("token", self.config, agenda_fixture or fixture(), "forensics", "Wk 04")

    def test_folder_files_fill_in_work_the_form_missed(self):
        one_form = fixture()
        one_form.classes["forensics"]["cells"][5, 20] = cell("Turn it in", hyperlink=DIRECT)
        result = self.run_work({"FICTIONALFORMID1234567890abc": [
            response("s.ana.rivera@school.example", "2026-09-23T14:00:00Z", ("FILENEW", "Copy of Week 04 deck - Ana Rivera")),
            response("s.ana.rivera@school.example", "2026-09-23T14:05:00Z", ("SHEPPARD", "Copy of Week 01 Sheppard deck - Ana Rivera"))]},
            one_form)
        self.assertEqual([f["name"] for f in result["folders"]], ["📥 Wk 04 — Fictional topic — 2026-09-23"])
        form = result["lessons"][0]["forms"][0]
        rows = {r["last"]: r for r in form["students"]}
        # Ana: the Week 01 deck is newer but named for another week, so the Week 04 Form upload is current.
        self.assertEqual(rows["Rivera"]["files"][0]["id"], "FILENEW")
        self.assertEqual({(e["source"], e["files"][0]["id"]) for e in rows["Rivera"]["earlier"]},
                         {("form", "SHEPPARD"), ("folder", "OLDANA")})
        self.assertEqual([e for e in rows["Rivera"]["earlier"] if e["source"] == "form"][0]["files"][0]["other_week"], 1)
        # Chidi only exists in the folder, matched by the username in the file name.
        self.assertEqual((rows["Okafor"]["source"], rows["Okafor"]["matched_by"]), ("folder", "file name"))
        # A loose file in the week folder belongs to the lesson's only Form; feedback docs are skipped.
        self.assertEqual(rows["Lee"]["files"][0]["id"], "LOOSE")
        self.assertEqual(form["missing"], [])
        self.assertEqual(form["folder_files"], 3)
        self.assertNotIn("FEEDBACK", json.dumps(result))

    def test_two_forms_keep_loose_files_separate(self):
        result = self.run_work({"FICTIONALFORMID1234567890abc": [
            response("s.ana.rivera@school.example", "2026-09-23T14:00:00Z", ("FILENEW", "Copy of Week 04 deck"))]})
        first, second = result["lessons"][0]["forms"]
        self.assertEqual({r["last"] for r in first["students"]}, {"Rivera", "Okafor"})
        self.assertEqual(second["students"], [])
        self.assertEqual([r["last"] for r in result["other_files"]], ["Lee"])

    def test_missing_week_folder_is_a_note(self):
        tree = drive_tree()
        tree["CLASSF"] = tree["CLASSF"][:1]
        with patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)):
            roots, note = work.lesson_folders("token", self.config, "Forensics", "Wk 04", [], set())
        self.assertEqual(roots, {})
        self.assertIn("Wk 04", note)

    def test_form_upload_folders_are_found_by_title_anywhere_in_the_class_folder(self):
        folder = "application/vnd.google-apps.folder"
        tree = {"ROOT": [{"id": "BIO", "name": "🌿 Biology", "mimeType": folder}],
                "BIO": [{"id": "WK01", "name": "📥 Wk 01 — Intro", "mimeType": folder},
                        {"id": "CYC03", "name": "Biology — Cycle 03 — Energy Flow — Turn In (File responses)",
                         "mimeType": folder},
                        {"id": "CYC02", "name": "Biology — Cycle 02 — Ecosystems — Turn In (File responses)",
                         "mimeType": folder}]}
        with patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)):
            roots, note = work.lesson_folders("token", self.config, "Biology", "Wk 03",
                                              ["Biology — Cycle 03 — Energy Flow — Turn In"], set())
        self.assertEqual(list(roots), ["CYC03"])
        self.assertIsNone(note)

    def test_form_folder_moved_into_another_weeks_folder_is_still_found(self):
        folder = "application/vnd.google-apps.folder"
        tree = {"ROOT": [{"id": "BIO", "name": "🌿 Biology", "mimeType": folder}],
                "BIO": [{"id": "WK03", "name": "📥 Wk 03 — Energy Flow", "mimeType": folder}],
                "WK03": [{"id": "FORM", "name": "Energy Flow — Turn In (File responses)", "mimeType": folder}],
                "ELSEWHERE": [{"id": "OTHER", "name": "Energy Flow — Turn In (File responses)", "mimeType": folder}]}
        with patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)):
            roots, note = work.lesson_folders("token", self.config, "Biology", "Wk 04",
                                              ["Energy Flow — Turn In"], set())
        # Found inside Wk 03's folder; a same-named folder outside the class folder is ignored.
        self.assertEqual(list(roots), ["FORM"])
        self.assertIsNone(note)

    def test_uploads_moved_into_the_form_folder_never_pull_in_the_whole_class_folder(self):
        folder = "application/vnd.google-apps.folder"

        def item(ident, name, mime=SLIDES):
            return {"id": ident, "name": name, "mimeType": mime, "createdTime": "2026-09-20T12:00:00Z"}
        tree = {"ROOT": [item("BIO", "🌿 Biology", folder)],
                "BIO": [item("CYC", "Biology — Cycle 04 — Old title — Turn In (File responses)", folder),
                        item("WK01", "📥 Wk 01 — Intro", folder), item("WK05", "📥 Wk 05 — Later", folder)],
                "CYC": [item("R1", "Copy of Cycle 04 deck - Ana Rivera"), item("L1", "Copy of Cycle 04 deck - Sam Lee")],
                "WK01": [item("K1", "s.chidi.okafor — 09-17 07.31 — Copy of Week 1 deck - Chidi Okafor")],
                "WK05": [item("T1", "teacher — 09-22 08.02 — Test upload - Teacher")]}
        meta = {"R1": {"name": "Copy of Cycle 04 deck - Ana Rivera", "mimeType": SLIDES, "parents": ["CYC"]},
                "CYC": {"name": "Biology — Cycle 04 — Old title — Turn In (File responses)", "parents": ["BIO"]},
                # The Form was renamed, so its folder can only be found through its uploads.
                "FICTIONALFORMID1234567890abc": {"name": "Biology — Wk 04 — New title — Turn In"}}
        responses = {"FICTIONALFORMID1234567890abc": [
            response("s.ana.rivera@school.example", "2026-09-20T14:00:00Z", ("R1", "deck"))]}
        with patch.object(work.roster, "load", return_value=({"biology": self.students}, [])), \
                patch.object(google, "form_responses", side_effect=lambda t, form_id: responses.get(form_id, [])), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=lambda t, fid: meta.get(fid, {"name": fid})):
            result = work.lesson_work("token", self.config, fixture(), "biology", "Wk 04")
        form = result["lessons"][0]["forms"][0]
        self.assertEqual(sorted(f["id"] for r in form["students"] for f in r["files"]), ["L1", "R1"])
        self.assertEqual(result["other_files"], [])
        self.assertEqual([f["name"] for f in result["folders"]],
                         ["Biology — Cycle 04 — Old title — Turn In (File responses)"])

    def _shared_run(self, shared_responses=None, shared_error=None):
        work._shared_cache.clear()
        folder = "application/vnd.google-apps.folder"

        def item(ident, name, mime=SLIDES):
            return {"id": ident, "name": name, "mimeType": mime, "createdTime": "2026-09-22T12:00:00Z"}
        tree = {"ROOT": [item("CLASSF", "🔬 Forensics", folder)],
                "CLASSF": [item("WK04", "📥 Wk 04 — Fictional topic", folder)],
                # The old router moved SH2 here; SH1 is still in the shared Form's own folder.
                "WK04": [item("SH2", "s.chidi.okafor — 09-22 10.00 — Copy of Week 04 deck - Chidi Okafor")]}
        meta = {"SH1": {"name": "Copy of Week 04 deck - Ana Rivera", "mimeType": SLIDES, "parents": ["SHAREDFOLDER"]},
                "SH2": {"name": "s.chidi.okafor — 09-22 10.00 — Copy of Week 04 deck - Chidi Okafor",
                        "mimeType": SLIDES, "parents": ["WK04"]},
                "SHAREDFOLDER": {"name": "Turn in (File responses)", "parents": ["MYDRIVE"]}}
        shared = shared_responses if shared_responses is not None else [
            response("s.ana.rivera@school.example", "2026-09-22T13:00:00Z", ("SH1", "deck"),
                     typed=("🔬 Forensics · Wk 04 — Fictional topic",)),
            response("s.chidi.okafor@school.example", "2026-09-22T14:00:00Z", ("SH2", "deck"),
                     typed=("🔬 Forensics · Wk 04 — Fictional topic",)),
            response("s.sam.lee@school.example", "2026-09-15T14:00:00Z", ("SH3", "deck"),
                     typed=("🔬 Forensics · Wk 03 — Earlier",)),
            response("s.sam.lee@school.example", "2026-09-22T14:00:00Z", ("SH4", "deck"),
                     typed=("🌿 Biology · Wk 04 — Energy Flow",))]

        def form_responses(token, form_id):
            if form_id == "SHAREDFORMID":
                if shared_error:
                    raise google.GoogleError("denied", 403)
                return shared
            return []
        one_form = fixture()
        one_form.classes["forensics"]["cells"][5, 20] = cell("Turn it in", hyperlink=DIRECT)
        config = {**self.config, "HUB_TURNIN_FORM_ID": "SHAREDFORMID"}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", side_effect=form_responses), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=lambda t, fid: meta.get(fid, {"name": fid, "mimeType": SLIDES})):
            return work.lesson_work("token", config, one_form, "forensics", "Wk 04")

    def test_earlier_shared_form_uploads_are_collected_wherever_they_were_moved(self):
        result = self._shared_run()
        form = result["lessons"][0]["forms"][0]
        rows = {r["last"]: r for r in form["students"]}
        self.assertEqual(set(rows), {"Rivera", "Okafor"})  # Wk 03 and Biology responses are left out
        self.assertEqual((rows["Rivera"]["files"][0]["id"], rows["Rivera"]["matched_by"]), ("SH1", "email"))
        # Moved into the week folder by the old script, but listed once, by email.
        okafor_ids = [f["id"] for f in rows["Okafor"]["files"]] + [f["id"] for e in rows["Okafor"]["earlier"] for f in e["files"]]
        self.assertEqual((okafor_ids, rows["Okafor"]["matched_by"]), (["SH2"], "email"))
        self.assertTrue(rows["Rivera"]["files"][0]["from_shared"])
        self.assertEqual(form["shared_files"], 2)
        self.assertEqual(result["shared_form"]["submissions"], 2)
        # The shared Form's own upload folder (every week's files) is never read as a lesson folder.
        self.assertNotIn("SHAREDFOLDER", json.dumps(result["folders"]))

    def test_unreadable_shared_form_is_reported_and_the_rest_still_works(self):
        result = self._shared_run(shared_error=True)
        self.assertIn("earlier shared turn-in Form couldn't be read", " ".join(result["problems"]))
        rows = result["lessons"][0]["forms"][0]["students"]
        self.assertEqual([(r["last"], r["source"]) for r in rows], [("Okafor", "folder")])

    def test_section_and_lesson_folders_are_read_and_matched_to_their_assignment(self):
        folder = "application/vnd.google-apps.folder"

        def item(ident, name, mime=SLIDES):
            return {"id": ident, "name": name, "mimeType": mime, "createdTime": "2026-09-23T12:00:00Z"}
        # The new layout: class / section / one folder per lesson; Forms' folders under 📨 Form uploads.
        tree = {"ROOT": [item("CLASSF", "🔬 Forensics", folder)],
                "CLASSF": [item("A2", "A2 Forensics", folder), item("A3", "A3 Forensics", folder),
                           item("INBOX", "📨 Form uploads", folder)],
                "A2": [item("A2R", "📥 Wk 04 — Reasoning", folder), item("A2X", "📥 Wk 05 — Later", folder)],
                "A3": [item("A3O", "📥 Wk 04 — Observation", folder)],
                "A2R": [item("R1", "My deck - Ana Rivera")],
                "A3O": [item("O1", "My deck - Sam Lee")],
                "A2X": [item("LATER", "Later deck - Ana Rivera")],
                "INBOX": [item("FORMF", "Forensics — Week 04 — Observation — Turn In (File responses)", folder)],
                "FORMF": [item("NEW", "Copy of deck - Chidi Okafor")]}
        names = {"FICTIONALFORMID1234567890abc": "Forensics — Week 04 — Reasoning — Turn In",
                 "FICTIONALFORMID0987654321xyz": "Forensics — Week 04 — Observation — Turn In"}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", return_value=[]), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=lambda t, fid: {"name": names.get(fid, fid), "mimeType": SLIDES}):
            result = work.lesson_work("token", self.config, fixture(), "forensics", "Wk 04")
        reasoning, observation = result["lessons"][0]["forms"]
        self.assertEqual([f["id"] for r in reasoning["students"] for f in r["files"]], ["R1"])
        self.assertEqual(sorted(f["id"] for r in observation["students"] for f in r["files"]), ["NEW", "O1"])
        self.assertEqual(result["other_files"], [])
        self.assertEqual(work.lesson_folder_name("Wk 04", "📤 Eyewitness Testimony — Turn in"),
                         "📥 Wk 04 — Eyewitness Testimony")

    def test_a_stray_moved_into_a_lessons_form_folder_counts_for_that_lesson(self):
        folder = "application/vnd.google-apps.folder"

        def item(ident, name, mime=SLIDES):
            return {"id": ident, "name": name, "mimeType": mime, "createdTime": "2026-09-23T12:00:00Z"}
        # Like Samajah's "Samjay and Maxo": no deck name, so the teacher drags it
        # into the Reasoning Form's folder inside the week folder.
        tree = {"ROOT": [item("CLASSF", "🔬 Forensics", folder)],
                "CLASSF": [item("WK04", "📥 Wk 04 — Two assignments", folder)],
                "WK04": [item("RFORM", "Forensics — Week 04 — Reasoning — Turn In (File responses)", folder),
                         item("OFORM", "Forensics — Week 04 — Observation — Turn In (File responses)", folder)],
                "RFORM": [item("STRAY", "s.sam.lee — 09-16 11.38 — Samjay and Maxo - Sam Lee")]}
        names = {"FICTIONALFORMID1234567890abc": "Forensics — Week 04 — Reasoning — Turn In",
                 "FICTIONALFORMID0987654321xyz": "Forensics — Week 04 — Observation — Turn In"}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", return_value=[]), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=lambda t, fid: {"name": names.get(fid, fid), "mimeType": SLIDES}):
            result = work.lesson_work("token", self.config, fixture(), "forensics", "Wk 04")
        reasoning, observation = result["lessons"][0]["forms"]
        self.assertEqual([(r["last"], r["files"][0]["id"]) for r in reasoning["students"]], [("Lee", "STRAY")])
        self.assertEqual((observation["students"], result["other_files"]), ([], []))

    def test_shared_folder_files_are_sorted_by_the_deck_they_were_copied_from(self):
        folder = "application/vnd.google-apps.folder"

        def item(ident, name):
            return {"id": ident, "name": name, "mimeType": SLIDES, "createdTime": "2026-09-23T12:00:00Z"}
        tree = {"ROOT": [{"id": "CLASSF", "name": "🔬 Forensics", "mimeType": folder}],
                "CLASSF": [{"id": "WK04", "name": "📥 Wk 04 — Two assignments", "mimeType": folder}],
                "WK04": [item("R1", "s.ana.rivera — 09-16 11.36 — Copy of Inductive_Deductive_Forensics_Google_Slides - Ana Rivera"),
                         item("O1", "s.chidi.okafor — 09-16 11.25 — Copy of Observation_Deductive_Reasoning_VT_CSI_1_markered - Chidi Okafor"),
                         item("O2", "s.ana.rivera — 09-16 09.29 — _markered - Ana Rivera"),
                         item("X1", "s.sam.lee — 09-16 11.38 — Samjay and Maxo - Sam Lee")]}
        names = {"FICTIONALFORMID1234567890abc": "Forensics — Week 04 — Reasoning — Turn In",
                 "FICTIONALFORMID0987654321xyz": "Forensics — Week 04 — Observation — Turn In",
                 "FICTIONALDECK1234567890": "Inductive_Deductive_Forensics_Google_Slides",
                 "RESP1": "Copy of Observation_Deductive_Reasoning_VT_CSI_1_markered - Sam Lee"}
        responses = {"FICTIONALFORMID0987654321xyz": [
            response("s.sam.lee@school.example", "2026-09-23T14:00:00Z", ("RESP1", "deck"))]}
        with patch.object(work.roster, "load", return_value=({"forensics": self.students}, [])), \
                patch.object(google, "form_responses", side_effect=lambda t, form_id: responses.get(form_id, [])), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])), \
                patch.object(google, "folders_named", side_effect=lambda t, name: named(tree, name)), \
                patch.object(google, "drive_file", side_effect=lambda t, fid: {"name": names.get(fid, fid), "mimeType": SLIDES}):
            result = work.lesson_work("token", self.config, fixture(), "forensics", "Wk 04")
        reasoning, observation = result["lessons"][0]["forms"]
        self.assertEqual([(r["last"], r["files"][0]["id"]) for r in reasoning["students"]], [("Rivera", "R1")])
        self.assertEqual(sorted((r["last"], r["files"][0]["id"]) for r in observation["students"]),
                         [("Lee", "RESP1"), ("Okafor", "O1"), ("Rivera", "O2")])
        self.assertEqual([(r["last"], r["files"][0]["id"]) for r in result["other_files"]], [("Lee", "X1")])
        self.assertEqual({m["last"]: m["unsorted"] for m in reasoning["missing"]}, {"Okafor": False, "Lee": True})
        # A moved copy is dated by the time in its name, not when it was moved.
        rivera = next(r for r in reasoning["students"] if r["last"] == "Rivera")
        self.assertEqual(rivera["submitted_at"], "2026-09-16T15:36:00Z")

    def test_a_lessons_decks_are_paired_with_its_forms_together_by_shared_words(self):
        # The live Forensics Wk 03 case: one Form's only upload is another week's deck, the
        # other Form has none yet, and one deck's name shares two words with the wrong Form.
        observation_deck = "Observation_Deductive_Reasoning_VT_CSI_1_markered"
        inductive_deck = "Inductive_Deductive_Forensics_Google_Slides"
        forms = [{"form_id": "A", "label": "Inductive and Deductive Reasoning — Turn In", "name": "",
                  "signature": {work.deck_stem("Week 04 — Eyewitness Accounts (VT)")}},
                 {"form_id": "B", "label": "Observation Skills — Turn In", "name": "", "signature": set()}]
        stems = [work.deck_stem(observation_deck), work.deck_stem(inductive_deck)]
        work.pair_decks(forms, stems, [observation_deck, inductive_deck])
        self.assertIn(stems[1], forms[0]["signature"])
        self.assertIn(stems[0], forms[1]["signature"])
        copy = work.deck_stem("s.a.b — 09-16 11.25 — Copy of Observation_Deductive_Reasoning_VT_CSI_1_markered - A B")
        self.assertGreater(work._matches(copy, forms[1]["signature"]), work._matches(copy, forms[0]["signature"]))
        self.assertTrue(work.NOT_STUDENT_WORK.search(
            "Teacher notes (private) — Forensics Wk 03 — Inductive and Deductive Reasoning — Turn In — 2026-09-24"))

    def test_deck_words_leave_out_the_students_name_and_common_words(self):
        self.assertEqual(work.deck_words("s.a.b — 09-16 11.29 — Copy of ▶ LIVE — Week 04 — Eyewitness Accounts (VT) - Ana Bee.pptx"),
                         {"eyewitness", "accounts"})
        self.assertEqual(work.deck_words("Copy of Observation_Deductive_Reasoning_VT_CSI_1_markered - Ana"),
                         {"observation", "deductive", "reasoning"})

    def wrong_form_entries(self, current):
        def found(*names):
            return [{"source": "form", "files": [{"name": name}], "key": name} for name in names]
        eyewitness = "Copy of ▶ LIVE — Week 04 — Eyewitness Accounts (VT) - %s.pptx"
        lessons = {
            "Wk 03": ("Observation Skills & Deductive Reasoning", "Inductive and Deductive Reasoning — Turn In",
                      found("Inductive_Deductive_Forensics_Google_Slides - A", "Inductive_Deductive_Forensics_Google_Slides - B",
                            eyewitness % "Oladotun")),
            "Wk 04": ("Eyewitness Testimony & The Innocence Project", "📤 Eyewitness Testimony — Turn in",
                      found(eyewitness % "C", eyewitness % "D", eyewitness % "E")),
            "Wk 05": ("Types of Evidence", "📤 Types of Evidence — Turn in",
                      found(eyewitness % "Sabrina", "Copy of ▶ LIVE — Week 05 — Types of Evidence (VT) - Kamiyah.pptx",
                            "Copy of Untitled presentation - Sam")),
        }
        return [{"current": week == current, "week": week, "label": label, "found": subs,
                 **dict(zip(("base", "words"), work.lesson_words(topic, label, [], subs)))}
                for week, (topic, label, subs) in lessons.items()]

    def test_another_lessons_deck_turned_in_through_this_form_counts_for_that_lesson(self):
        entries = self.wrong_form_entries("Wk 05")
        moves = [(entries[i]["week"], s["files"][0]["name"], entries[j]["week"]) for i, s, j in work.wrong_form_moves(entries)]
        # Sabrina's Eyewitness deck leaves Types of Evidence; Kamiyah's Types deck and an untitled copy stay.
        # Oladotun's (Wk 03 Form → Wk 04) is between two other weeks, so it's left to those weeks' pages.
        self.assertEqual(moves, [("Wk 05", "Copy of ▶ LIVE — Week 04 — Eyewitness Accounts (VT) - Sabrina.pptx", "Wk 04")])
        entries = self.wrong_form_entries("Wk 04")
        moves = sorted((entries[i]["week"], entries[j]["week"]) for i, s, j in work.wrong_form_moves(entries))
        self.assertEqual(moves, [("Wk 03", "Wk 04"), ("Wk 05", "Wk 04")])  # both counted for Eyewitness

    def test_two_forms_in_one_row_are_told_apart_by_their_own_labels(self):
        # Wk 04's row has two Forms; its topic and deck name only one of them.
        entries = self.wrong_form_entries("Wk 04")
        investigative = [{"source": "form", "key": k, "files": [{"name": "▶ LIVE — Week 05 — Investigative Processes (VT) - %s" % k}]}
                         for k in "FG"]
        base, words = work.lesson_words("", "📤 Investigative Processes (Steps of CSI) — Turn in", [], investigative)
        entries.append({"current": True, "week": "Wk 04", "label": "Investigative", "found": investigative,
                        "base": base, "words": words})
        moves = sorted((entries[i]["week"], entries[j]["label"][:12]) for i, s, j in work.wrong_form_moves(entries))
        self.assertEqual(moves, [("Wk 03", "📤 Eyewitness"), ("Wk 05", "📤 Eyewitness")])

    def test_a_deck_that_names_its_own_lesson_or_two_others_stays(self):
        entries = self.wrong_form_entries("Wk 05")
        entries[2]["found"] += [
            {"source": "form", "key": "own", "files": [{"name": "Copy of Types and Eyewitness review - Lee.pptx"}]},
            {"source": "form", "key": "two", "files": [{"name": "Copy of Inductive Eyewitness mashup - Kim.pptx"}]}]
        moved = [s["key"] for i, s, j in work.wrong_form_moves(entries)]
        self.assertNotIn("own", moved)
        self.assertNotIn("two", moved)

    def test_a_pile_of_another_lessons_decks_doesnt_teach_a_lesson_their_name(self):
        wrong = [{"source": "form", "key": k, "files": [{"name": "Copy of ▶ LIVE — Week 04 — Eyewitness Accounts (VT) - %s" % k}]}
                 for k in "ABC"]
        base, words = work.lesson_words("Types of Evidence", "📤 Types of Evidence — Turn in", [], wrong)
        self.assertEqual(words, base)
        self.assertNotIn("eyewitness", words)

    def test_name_time_and_deck_stem(self):
        self.assertEqual(work.name_time("s.a.b — 09-16 11.29 — x", "2026-09-20T01:00:00Z"), "2026-09-16T15:29:00Z")
        self.assertEqual(work.name_time("s.a.b — 01-05 10.00 — x", "2027-01-06T01:00:00Z"), "2027-01-05T15:00:00Z")
        self.assertIsNone(work.name_time("Copy of deck - Ana", "2026-09-20T01:00:00Z"))
        self.assertEqual(work.deck_stem("s.a.b — 09-16 11.29 — Copy of ▶ LIVE — Week 04 — Eyewitness (VT) - Ana B.pptx"),
                         "week04eyewitnessvt")

    def test_a_moved_lesson_keeps_its_own_folder_and_never_takes_the_other_lessons(self):
        # Eyewitness (was Wk 04, deck named Week 04) and Evidence (was Wk 05) traded weeks.
        children = {"ROOT": [{"id": "FOR", "name": "Forensics", "mimeType": google.FOLDER}],
                    "FOR": [{"id": "EYE", "name": "📥 Wk 04 — Eyewitness Testimony — 2026-09-23", "mimeType": google.FOLDER},
                            {"id": "EVI", "name": "📥 Wk 05 — Types of Evidence", "mimeType": google.FOLDER},
                            {"id": "OLD", "name": "📥 Wk 04", "mimeType": google.FOLDER}]}
        config = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT"}
        eyewitness, evidence = ["📤 Eyewitness Testimony — Turn in"], ["📤 Types of Evidence — Turn in"]
        with patch.object(google, "folder_children", side_effect=lambda t, fid: children.get(fid, [])), \
                patch.object(google, "folders_named", return_value=[]):
            moved_later, _ = work.lesson_folders("t", config, "Forensics", "Wk 05", [], set(), (), eyewitness,
                                                 evidence)
            moved_earlier, _ = work.lesson_folders("t", config, "Forensics", "Wk 04", [], set(), (), evidence,
                                                   eyewitness)
        self.assertEqual(set(moved_later), {"EYE"})  # its own folder, found by its title under the old number
        self.assertEqual(set(moved_earlier), {"EVI", "OLD"})  # the untitled Wk 04 folder, never Eyewitness's

    def test_named_week(self):
        # A moved lesson accepts files named for its Agenda week or its deck's week.
        self.assertIsNone(work.named_week("Copy of ▶ LIVE — Week 04 — Eyewitness", {5, 4}))
        self.assertEqual(work.named_week("Copy of ▶ LIVE — Week 03 — Sheppard", {5, 4}), 3)
        self.assertEqual(work.named_week("Copy of ▶ LIVE — Week 01 — Sheppard", 4), 1)
        self.assertIsNone(work.named_week("Copy of ▶ LIVE — Week 04 — Eyewitness", 4))
        self.assertIsNone(work.named_week("My deck", 4))
        self.assertEqual(roster.file_username("s.ana.rivera — 09-16 12.40 — Copy of deck"), "s.ana.rivera")
        self.assertEqual(roster.file_username("Copy of deck - Ana Rivera"), "")


def call(path, method="GET", query="", assertion=None, config=None):
    environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": query, "wsgi.input": io.BytesIO()}
    if assertion:
        environ["HTTP_X_GOOG_IAP_JWT_ASSERTION"] = assertion
    seen = {}

    def start(status, headers):
        seen["status"], seen["headers"] = status, dict(headers)
    body = b"".join(app.application(environ, start, config=config or {}))
    return seen["status"], seen["headers"], body


class AppTests(unittest.TestCase):
    def test_pages_and_redirect(self):
        self.assertEqual(call("/v2")[0], "302 Found")
        status, headers, body = call("/v2/")
        self.assertEqual(status, "200 OK")
        self.assertIn(b"Teaching Hub", body)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(call("/v2/app.js")[0], "200 OK")
        self.assertEqual(call("/v2/../web.py")[0], "404 Not Found")
        self.assertEqual(call("/v2/api/day", "POST")[0], "405 Method Not Allowed")

    def test_api_requires_sign_in_before_any_google_call(self):
        with patch.object(google, "service_token") as token:
            status, _, body = call("/v2/api/day", query="date=2026-09-23")
        self.assertEqual(status, "401 Unauthorized")
        token.assert_not_called()

    def test_day_api(self):
        app._cache["agenda"] = None
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(agenda, "load", return_value=fixture()):
            status, _, body = call("/v2/api/day", query="date=2026-09-23", assertion="signed",
                                   config={"HUB_AGENDA_SPREADSHEET_ID": "FICTIONALSHEET"})
            bad = call("/v2/api/day", query="date=nope", assertion="signed",
                       config={"HUB_AGENDA_SPREADSHEET_ID": "FICTIONALSHEET"})[0]
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["classes"][0]["lessons"][0]["today"]["plan"], "Wed plan text")
        self.assertEqual(bad, "400 Bad Request")

    def test_unreadable_agenda_names_the_fix(self):
        app._cache["agenda"] = None
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(agenda, "load", side_effect=google.GoogleError("x", 403)):
            status, _, body = call("/v2/api/day", query="date=2026-09-23", assertion="signed",
                                   config={"HUB_AGENDA_SPREADSHEET_ID": "FICTIONALSHEET",
                                           "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"})
        self.assertEqual(status, "502 Bad Gateway")
        self.assertIn("Share it with reader@fictional.example", json.loads(body)["error"])

    def test_v1_server_mounts_v2(self):
        from school_workflow_hub import web
        seen = {}
        body = b"".join(web.application({"PATH_INFO": "/v2/", "REQUEST_METHOD": "GET"},
                                        lambda s, h: seen.setdefault("status", s)))
        self.assertEqual(seen["status"], "200 OK")
        self.assertIn(b"Teaching Hub", body)


if __name__ == "__main__":
    unittest.main()


class FakeDrive:
    """A tiny Drive: items with one parent each; moves really move."""
    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self, editor=True, owner=False):
        self.items, self.editor, self.owner, self.trashed = {}, editor, owner, set()

    def add(self, ident, name, parent, folder=False, created="2026-09-20T12:00:00Z"):
        self.items[ident] = {"id": ident, "name": name, "parents": [parent] if parent else [],
                             "mimeType": self.FOLDER if folder else SLIDES, "createdTime": created}

    def children(self, token, fid):
        return [dict(i) for i in self.items.values() if i["parents"][:1] == [fid] and i["id"] not in self.trashed]

    def file(self, token, fid, fields=None):
        if fid not in self.items:
            raise google.GoogleError("missing", 404)
        return {**self.items[fid], "capabilities": {"canAddChildren": self.editor, "canRename": self.editor,
                                                    "canMoveItemWithinDrive": self.editor, "canTrash": self.owner}}

    def named(self, token, name):
        return [dict(i) for i in self.items.values() if i["name"] == name and i["mimeType"] == self.FOLDER]

    def move(self, token, fid, new_parent, old_parents):
        if not self.editor:
            raise google.GoogleError("denied", 403)
        self.items[fid]["parents"] = [new_parent]
        return {"id": fid, "parents": [new_parent]}

    def trash(self, token, fid):
        if not self.owner:
            raise google.GoogleError("only the owner can trash", 403)
        self.trashed.add(fid)
        return {"id": fid, "trashed": True}

    def rename(self, token, fid, name):
        if not self.editor:
            raise google.GoogleError("denied", 403)
        self.items[fid]["name"] = name
        return {"id": fid, "name": name}

    def create(self, token, name, parent):
        ident = "NEWFOLDER%d" % sum(1 for i in self.items if i.startswith("NEWFOLDER"))
        self.add(ident, name, parent, folder=True)
        return {"id": ident}

    def patches(self, responses):
        return [patch.object(google, "folder_children", side_effect=self.children),
                patch.object(google, "drive_file", side_effect=self.file),
                patch.object(google, "folders_named", side_effect=self.named),
                patch.object(google, "move_item", side_effect=self.move),
                patch.object(google, "trash_item", side_effect=self.trash),
                patch.object(google, "rename_item", side_effect=self.rename),
                patch.object(work.roster, "load", return_value=({"forensics": roster.parse_tab(
                    ROSTER, "forensics", "A2 Forensics")[0]}, [])),
                patch.object(google, "create_folder", side_effect=self.create),
                patch.object(google, "form_responses", side_effect=lambda t, form_id: responses.get(form_id, []))]


class TidyTests(unittest.TestCase):
    def setUp(self):
        work._shared_cache.clear()
        self.config = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT", "HUB_TURNIN_FORM_ID": "SHAREDFORMID",
                       "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
        self.agenda = fixture()
        self.agenda.classes["forensics"]["cells"][5, 20] = cell("Turn it in", hyperlink=DIRECT)

    def drive(self, editor=True):
        d = FakeDrive(editor)
        d.add("CLASSF", "🔬 Forensics", "ROOT", folder=True)
        d.add("KEEP", "📥 Wk 04 — Fictional topic", "CLASSF", folder=True, created="2026-09-18T12:00:00Z")
        d.add("FORMF", "Fictional Form — Turn In (File responses)", "KEEP", folder=True)
        d.add("Q", "Upload your deck (File responses)", "FORMF", folder=True)
        d.add("R1", "Copy of deck - Ana Rivera", "Q")
        d.add("DUP", "📥 Wk 04 — Fictional", "CLASSF", folder=True, created="2026-09-24T12:00:00Z")
        d.add("D1", "s.sam.lee — 09-24 09.00 — deck - Sam Lee", "DUP")
        d.add("D2", "some subfolder", "DUP", folder=True)
        d.add("LOOSEF", "Forensics — Wk 04 — Other — Turn In (File responses)", "CLASSF", folder=True)
        d.add("WK03", "📥 Wk 03 — Earlier", "CLASSF", folder=True)
        d.add("X1", "week 3 deck - Sam Lee", "WK03")
        d.add("SHAREDF", "Turn in (File responses)", "MYDRIVE", folder=True)
        d.add("S1", "deck - Chidi Okafor", "SHAREDF")
        d.add("S3", "old deck - Sam Lee", "SHAREDF")
        d.add("FICTIONALFORMID1234567890abc", "Fictional Form — Turn In", "KEEP")
        responses = {
            "FICTIONALFORMID1234567890abc": [response("s.ana.rivera@school.example", "2026-09-22T12:00:00Z", ("R1", "deck"))],
            "SHAREDFORMID": [
                response("s.chidi.okafor@school.example", "2026-09-22T12:00:00Z", ("S1", "deck"),
                         typed=("🔬 Forensics · Wk 04 — Fictional topic",)),
                response("s.sam.lee@school.example", "2026-09-15T12:00:00Z", ("S3", "deck"),
                         typed=("🔬 Forensics · Wk 03 — Earlier",))]}
        return d, responses

    def run_plan(self, d, responses):
        patches = d.patches(responses)
        for p in patches:
            p.start()
        try:
            return tidy.plan("token", self.config, self.agenda, "forensics", "Wk 04")
        finally:
            for p in patches:
                p.stop()

    def test_plan_lists_only_this_weeks_strays_and_keeps_the_form_folder_week(self):
        d, responses = self.drive()
        p = self.run_plan(d, responses)
        self.assertEqual(p["keep"]["id"], "KEEP")
        self.assertEqual(sorted(m["id"] for m in p["moves"]), ["D1", "D2", "LOOSEF", "S1"])
        self.assertEqual([e["id"] for e in p["empty_after"]], ["DUP"])
        self.assertTrue(p["can_move"])
        self.assertEqual(d.items["D1"]["parents"], ["DUP"])  # a preview moves nothing

    def test_apply_moves_exactly_the_plan_and_marks_emptied_folders_with_an_underscore(self):
        d, responses = self.drive()
        p = self.run_plan(d, responses)
        patches = d.patches(responses)
        for x in patches:
            x.start()
        try:
            work._shared_cache.clear()
            result = tidy.apply("token", "write", self.config, self.agenda, "forensics", "Wk 04", p["plan_id"])
        finally:
            for x in patches:
                x.stop()
        self.assertEqual(result["moved"], 4)
        self.assertEqual({i for i in ("D1", "D2", "LOOSEF", "S1") if d.items[i]["parents"] == ["KEEP"]},
                         {"D1", "D2", "LOOSEF", "S1"})
        self.assertEqual((d.items["X1"]["parents"], d.items["S3"]["parents"]), (["WK03"], ["SHAREDF"]))
        # The dashboard can't trash her folder, so it gets '_' in front instead.
        self.assertEqual(([e["id"] for e in result["marked"]], result["trashed"], result["left_empty"]),
                         (["DUP"], [], []))
        self.assertEqual(d.items["DUP"]["name"], "_📥 Wk 04 — Fictional")
        # A second preview finds nothing left to do.
        work._shared_cache.clear()
        again = self.run_plan(d, responses)
        self.assertEqual((again["moves"], again["empty_after"]), ([], []))

    def test_emptied_folders_go_to_the_trash_when_drive_allows_it(self):
        d, responses = self.drive()
        d.owner = True
        p = self.run_plan(d, responses)
        patches = d.patches(responses)
        for x in patches:
            x.start()
        try:
            work._shared_cache.clear()
            result = tidy.apply("token", "write", self.config, self.agenda, "forensics", "Wk 04", p["plan_id"])
        finally:
            for x in patches:
                x.stop()
        self.assertEqual(([e["id"] for e in result["trashed"]], result["marked"]), (["DUP"], []))
        self.assertIn("DUP", d.trashed)

    def test_late_work_moves_into_the_weeks_late_folder(self):
        d, responses = self.drive()
        d.add("R2", "s.sam.lee — 10-12 09.00 — deck - Sam Lee", "Q")
        responses["FICTIONALFORMID1234567890abc"].append(
            response("s.sam.lee@school.example", "2026-10-12T13:00:00Z", ("R2", "deck")))
        after = datetime(2026, 10, 12, 18, tzinfo=timezone.utc)
        patches = d.patches(responses)
        for x in patches:
            x.start()
        try:
            p = tidy.plan("token", self.config, self.agenda, "forensics", "Wk 04", now=after)
            work._shared_cache.clear()
            result = tidy.apply("token", "write", self.config, self.agenda, "forensics", "Wk 04", p["plan_id"],
                                now=after)
        finally:
            for x in patches:
                x.stop()
        self.assertEqual(p["create_late"], tidy.LATE_FOLDER)
        self.assertEqual([m["id"] for m in p["moves"] if m["to"] == "late"], ["R2"])
        late = [i for i in d.items.values() if i["name"] == tidy.LATE_FOLDER]
        self.assertEqual([i["parents"] for i in late], [["KEEP"]])
        self.assertEqual(d.items["R2"]["parents"], [late[0]["id"]])
        self.assertEqual(d.items["R1"]["parents"], ["Q"])  # on-time work stays with its Form
        self.assertEqual(result["failed"], [])

    def test_stale_preview_is_refused_and_nothing_moves(self):
        d, responses = self.drive()
        p = self.run_plan(d, responses)
        d.add("D9", "another stray", "DUP")
        patches = d.patches(responses)
        for x in patches:
            x.start()
        try:
            with self.assertRaises(tidy.Changed):
                tidy.apply("token", "write", self.config, self.agenda, "forensics", "Wk 04", p["plan_id"])
        finally:
            for x in patches:
                x.stop()
        self.assertEqual(d.items["D1"]["parents"], ["DUP"])

    def test_without_editor_access_the_plan_says_who_to_share_with(self):
        d, responses = self.drive(editor=False)
        p = self.run_plan(d, responses)
        self.assertFalse(p["can_move"])
        self.assertIn("share '00 REAL ALL TURN IN' with reader@fictional.example as an Editor", p["problem"])


class TidyEndpointTests(unittest.TestCase):
    def post(self, origin, body=b'{"class": "forensics", "week": "Wk 04", "plan_id": "abc"}'):
        environ = {"PATH_INFO": "/v2/api/tidy", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if origin:
            environ["HTTP_ORIGIN"] = origin
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(tidy, "apply", return_value={"moved": 0}) as applied:
            out = b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], out, applied

    def test_only_the_dashboard_page_can_run_tidy(self):
        status, _, applied = self.post(None)
        self.assertEqual(status, "403 Forbidden")
        applied.assert_not_called()
        status, _, applied = self.post("https://elsewhere.example")
        self.assertEqual(status, "403 Forbidden")
        status, _, applied = self.post("https://hub.fictional.example")
        self.assertEqual(status, "200 OK")
        applied.assert_called_once()


def pptx(slides, size=(1000, 600)):
    """A minimal fictional .pptx: slides are lists of (name, text, (x, y, w, h))."""
    from xml.sax.saxutils import escape
    ns = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        ids = "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 1) for i in range(len(slides)))
        z.writestr("ppt/presentation.xml", '<p:presentation %s><p:sldIdLst>%s</p:sldIdLst>'
                   '<p:sldSz cx="%d" cy="%d"/></p:presentation>' % (ns, ids, size[0], size[1]))
        rels = "".join('<Relationship Id="rId%d" Target="slides/slide%d.xml"/>' % (i + 1, i + 1)
                       for i in range(len(slides)))
        z.writestr("ppt/_rels/presentation.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
                   '</Relationships>' % rels)
        for number, shapes in enumerate(slides, 1):
            body = ""
            for i, (name, text, (x, y, w, h)) in enumerate(shapes):
                paragraphs = "".join("<a:p><a:r><a:t>%s</a:t></a:r></a:p>" % escape(line)
                                     for line in text.split("\n"))
                body += ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/></p:nvSpPr><p:spPr><a:xfrm>'
                         '<a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm></p:spPr>'
                         '<p:txBody>%s</p:txBody></p:sp>' % (i + 2, name, x, y, w, h, paragraphs))
            z.writestr("ppt/slides/slide%d.xml" % number,
                       '<p:sld %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>' % (ns, body))
    return out.getvalue()


HEADER = ("[[MARKER-INVENTORY:C09]] NOTES=3 DRAFT=1 OPTIONAL=0 BANK_TERMS=2 "
          "NOTES_IDS=Q-RULE,Q-COUNT,Q-MASTERY DRAFT_IDS=Q-MASTERY OPTIONAL_IDS= "
          "BANK_IDS=Producer,Trophic level COURSE=Biology VERSION=2026-09-18")


def handout(**changes):
    """The distributed copy, or a student's copy with some boxes changed."""
    def text(name, default):
        return changes.get(name, default)
    return pptx([
        [("S1", HEADER, (0, 0, 900, 40))],
        # Codes placed on their answer boxes.
        [("PROMPT", "Finish this sentence as a rule: energy enters through ____.", (20, 20, 900, 60)),
         ("RULE", text("RULE", "[ Write your rule here ]"), (20, 120, 400, 100)),
         ("COUNT", text("COUNT", "Producers Count: [ Enter # ]"), (500, 120, 300, 100)),
         ("CHOICE", text("CHOICE", "↑ ↓ ?"), (20, 300, 100, 40)),
         ("M1", "[[NOTES:C09:Q-RULE]]", (30, 130, 80, 10)),
         ("M2", "[[NOTES:C09:Q-COUNT]]", (510, 130, 80, 10))],
        # First and revised answer, codes listed together in a corner.
        [("L1", "Your first answer", (20, 80, 400, 30)),
         ("B1", text("B1", ""), (20, 120, 400, 150)),
         ("L2", "Your revised answer", (20, 320, 400, 30)),
         ("B2", text("B2", ""), (20, 360, 400, 150)),
         ("M3", "[[NOTES:C09:Q-MASTERY]]", (900, 540, 90, 10)),
         ("M4", "[[DRAFT:C09:Q-MASTERY]]", (900, 552, 90, 10))],
        # Concept Bank, codes listed together.
        [("T1", text("T1", "Producer  "), (20, 20, 400, 80)),
         ("T2", text("T2", "Trophic level  "), (20, 120, 400, 80)),
         ("M5", "[[BANK:C09:Producer]]\n[[BANK:C09:Trophic level]]", (900, 500, 90, 30))],
        # No codes here.
        [("FREE", text("FREE", "Notes space"), (20, 20, 800, 400))],
    ])


class CompletionTests(unittest.TestCase):
    def score(self, **changes):
        template = completion.read_deck(handout())
        student = completion.read_deck(handout(**changes))
        return completion.assess(student, template)

    def test_the_header_lists_what_is_required(self):
        code, required, basis, note = completion.inventory(completion.read_deck(handout()))
        self.assertEqual((code, basis, note), ("C09", "header", None))
        self.assertEqual(required["BANK"], ["Producer", "Trophic level"])
        self.assertEqual(required["NOTES"], ["Q-RULE", "Q-COUNT", "Q-MASTERY"])
        self.assertEqual(required["OPTIONAL"], [])

    def test_untouched_copy_scores_zero(self):
        result = self.score()
        self.assertEqual((result["notes_score"], result["revision_score"]), (0.0, 0.0))
        self.assertEqual(result["uncoded"], [])

    def test_boxes_the_student_wrote_in_count_and_nothing_else(self):
        result = self.score(RULE="Energy enters through producers", COUNT="Producers Count: 12",
                            B2="Plants catch light and make sugar", T1="Producer  makes its own food")
        status = {(i["category"], i["id"]): i["status"] for i in result["items"]}
        self.assertEqual(status[("NOTES", "Q-RULE")], "done")
        self.assertEqual(status[("NOTES", "Q-COUNT")], "done")
        self.assertEqual(status[("NOTES", "Q-MASTERY")], "blank")  # only the revised box was filled
        self.assertEqual(status[("DRAFT", "Q-MASTERY")], "done")
        self.assertEqual(status[("BANK", "Producer")], "done")
        self.assertEqual(status[("BANK", "Trophic level")], "blank")
        # Notes = 10 x (2 NOTES + 1 BANK) / (3 + 2); Revision = 10 x 1 / 1.
        self.assertEqual((result["notes_score"], result["revision_score"]), (6.0, 10.0))

    def test_first_answer_goes_to_notes_not_revision(self):
        result = self.score(B1="Only plants can use sunlight")
        status = {(i["category"], i["id"]): i["status"] for i in result["items"]}
        self.assertEqual((status[("NOTES", "Q-MASTERY")], status[("DRAFT", "Q-MASTERY")]), ("done", "blank"))

    def test_printed_prompt_copied_back_is_flagged_not_credited(self):
        result = self.score(RULE="Finish this sentence as a rule: energy enters through")
        item = next(i for i in result["items"] if i["id"] == "Q-RULE")
        self.assertEqual(item["status"], "copied")

    def test_writing_in_an_uncoded_box_is_reported_not_scored(self):
        result = self.score(FREE="Notes space my own notes about energy")
        self.assertEqual([u["slide"] for u in result["uncoded"]], [5])
        self.assertEqual(result["notes_score"], 0.0)

    def test_a_deck_without_a_header_uses_every_code_in_it(self):
        deck = completion.read_deck(pptx([[("M", "[[NOTES:C01:A]]", (0, 0, 10, 10)),
                                           ("N", "[[DRAFT:C01:A]]", (0, 20, 10, 10))]]))
        code, required, basis, _ = completion.inventory(deck)
        self.assertEqual((code, basis, required["NOTES"], required["DRAFT"]), ("C01", "census", ["A"], ["A"]))

    def test_scores_are_a_share_of_this_decks_boxes_on_a_ten_point_scale(self):
        self.assertEqual([completion.score(20, 27), completion.score(1, 2), completion.score(13, 13),
                          completion.score(0, 5), completion.score(0, 0)], [7.4, 5.0, 10.0, 0.0, None])

    def test_added_words(self):
        self.assertEqual(completion.added("[ Type name here ]", "Oak tree"), "oak tree")
        self.assertEqual(completion.added("Producers Count: [ Enter # ]", "Producers Count: 12"), "12")
        self.assertEqual(completion.added("↑ ↓ ?", "↑"), "chose ↑")
        self.assertEqual(completion.added("Your answer", "Your answer"), "")
        # As the student wrote it: capitals and punctuation kept, the printed words left out.
        self.assertEqual(completion.added("[ Type name here ]", "Oak Tree, mostly.", exact=True), "Oak Tree, mostly.")
        self.assertEqual(completion.added("Producers Count: [ Enter # ]", "Producers Count: 12", exact=True), "12")
        self.assertEqual(completion.added("↑ ↓ ?", "↑", exact=True), "chose ↑")

    def test_answers_are_kept_as_the_student_wrote_them_with_their_slide(self):
        result = self.score(RULE="Energy enters through PRODUCERS, like kelp!", B1="Only plants use sunlight.",
                            B2="Vent bacteria use chemical energy.")
        items = {(i["category"], i["id"]): i for i in result["items"]}
        self.assertEqual(items[("NOTES", "Q-RULE")]["answer"], "Energy enters through PRODUCERS, like kelp!")
        self.assertEqual(items[("NOTES", "Q-RULE")]["at"], 2)
        self.assertEqual(items[("NOTES", "Q-RULE")]["question"],
                         "Finish this sentence as a rule: energy enters through ____.")
        self.assertEqual(items[("NOTES", "Q-MASTERY")]["answer"], "Only plants use sunlight.")
        self.assertEqual(items[("DRAFT", "Q-MASTERY")]["answer"], "Vent bacteria use chemical energy.")
        self.assertEqual(items[("NOTES", "Q-COUNT")]["answer"], "")  # left blank

    def test_a_slide_is_named_by_its_title_box_else_its_top_line(self):
        titled = {"units": [{"text": "Body text", "box": (0, 10, 10, 10)},
                            {"text": "Your answer — Critical Aspect 1 · Energy flow", "box": None, "title": True}]}
        self.assertEqual(completion.slide_title(titled), "Your answer — Critical Aspect 1 · Energy flow")
        untitled = {"units": [{"text": "Lower", "box": (0, 300, 10, 10)},
                              {"text": "Top line\nsecond line", "box": (0, 20, 10, 10)},
                              {"text": "[[NOTES:C01:A]]", "box": (0, 0, 10, 10)}]}
        self.assertEqual(completion.slide_title(untitled), "Top line")

    def test_not_a_deck(self):
        with self.assertRaises(completion.Unreadable):
            completion.read_deck(b"%PDF-1.4 not a deck")


class ScoreFileTests(unittest.TestCase):
    FORM = "FICTIONALFORMID1234567890abc"

    def work_result(self, late=False):
        row = {"matched": True, "last": "Rivera", "first": "Ana", "section": "A2 Forensics",
               "files": [{"id": "STUDENTDECK123", "mime": completion.SLIDES}], "late": late, "earlier": []}
        return {"lessons": [{"forms": [{"form_id": self.FORM, "students": [] if late else [row],
                                        "late": [row] if late else []}]}]}

    def run_score(self, file_id="STUDENTDECK123", late=False):
        completion._templates.clear()
        completion._results.clear()
        decks = {"FICTIONALDECK1234567890": handout(), "STUDENTDECK123": handout(RULE="Through producers")}
        with patch.object(google, "drive_file", side_effect=lambda t, fid, fields=None: {
                    "id": fid, "name": "Deck", "mimeType": completion.SLIDES, "modifiedTime": "2026-09-22T00:00:00Z"}), \
                patch.object(google, "deck_bytes", side_effect=lambda t, fid, mime: decks[fid]) as read:
            result = completion.score_file("token", fixture(), "forensics", "Wk 04", self.FORM, file_id,
                                           self.work_result(late))
        return result, read

    def test_a_students_current_file_is_scored_against_the_lessons_deck(self):
        result, read = self.run_score()
        self.assertIsNone(result["problem"])
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 3})
        self.assertEqual(result["notes_score"], 2.0)  # 10 x 1 / (3 NOTES + 2 BANK)
        self.assertEqual(sorted(c.args[1] for c in read.call_args_list), ["FICTIONALDECK1234567890", "STUDENTDECK123"])

    def test_only_files_on_the_page_can_be_scored_and_late_work_never_is(self):
        with self.assertRaises(ValueError):
            self.run_score(file_id="SOMEOTHERFILE99")
        with self.assertRaises(ValueError):
            self.run_score(late=True)


class LedgerAndFeedbackTests(unittest.TestCase):
    CONFIG = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT", "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
    FORM = "FICTIONALFORMID1234567890abc"

    def sheet(self, rows):
        return [patch.object(google, "files_named_in", return_value=[{"id": "SHEETID", "name": "Scores entered (school workflow-hub)"}]),
                patch.object(google, "sheet_tabs", return_value=("Scores entered", {"Sheet1": 0})),
                patch.object(google, "sheet_values", return_value=rows)]

    def test_the_latest_note_per_assignment_wins_and_other_assignments_are_ignored(self):
        rows = [ledger.HEADER,
                ["2026-09-24 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM],
                ["2026-10-01 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM],
                ["2026-10-01 10:05", "Feedback made", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM],
                ["2026-10-09 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Other", "OTHERFORMID12345"],
                # An older per-student row still reads as a note for its assignment.
                ["2026-09-20 09:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM, "A2", "Rivera", "Ana", "8"]]
        patches = self.sheet(rows)
        for x in patches:
            x.start()
        try:
            out = ledger.read("token", self.CONFIG, self.FORM)
        finally:
            for x in patches:
                x.stop()
        self.assertEqual(out["entered_at"], {"label": "2026-10-01 10:00", "at": "2026-10-01T14:00:00Z"})
        self.assertEqual(out["feedback_at"]["at"], "2026-10-01T14:05:00Z")
        self.assertEqual(out["name"], "Scores entered (school workflow-hub)")  # the page names the sheet

    def test_no_sheet_yet_says_how_to_make_one(self):
        with patch.object(google, "files_named_in", return_value=[]):
            out = ledger.read("token", self.CONFIG, self.FORM)
        self.assertFalse(out["configured"])
        self.assertIn("Scores entered", out["setup"])

    def test_the_sheet_may_have_more_after_its_name(self):
        sheets = [{"id": "LATERCOPY", "name": "Scores entered (copy)", "createdTime": "2026-10-01T00:00:00Z"},
                  {"id": "HERS", "name": "Scores entered (school workflow-hub)", "createdTime": "2026-09-24T12:00:00Z"},
                  {"id": "OTHER", "name": "Old scores entered", "createdTime": "2026-09-01T00:00:00Z"}]
        with patch.object(google, "files_named_in", return_value=sheets) as search:
            self.assertEqual(ledger.find("token", self.CONFIG)["id"], "HERS")
        self.assertEqual(search.call_args.kwargs, {"prefix": True})
        with patch.object(google, "files_named_in", return_value=sheets + [{"id": "EXACT", "name": "Scores entered"}]):
            self.assertEqual(ledger.find("token", self.CONFIG)["id"], "EXACT")

    def test_record_adds_one_row_for_the_class_and_assignment(self):
        context = {"class": "🔬 Forensics", "week": "Wk 04", "assignment": "Reasoning", "form_id": self.FORM}
        patches = self.sheet([])
        for x in patches:
            x.start()
        try:
            with patch.object(google, "append_rows") as append:
                ledger.record("token", "write", self.CONFIG, "ic", context)
        finally:
            for x in patches:
                x.stop()
        written = append.call_args.args[3]
        self.assertEqual(written[0], ledger.HEADER)
        self.assertEqual(written[1][1:], ["Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM])
        self.assertEqual(len(written), 2)
        self.assertEqual(append.call_args.args[0], "write")  # only the write token writes

    def test_feedback_lists_students_with_revisions_turned_in_since_the_last_feedback(self):
        def row(last, when):
            return {"matched": True, "last": last, "first": "A", "section": "A2 Forensics", "submitted_at": when,
                    "files": [{"id": "F" + last, "url": "https://docs.example/" + last}], "earlier": []}
        def score(last, drafts):
            return {"file_id": "F" + last, "problem": None, "counts": {"DRAFT": {"done": drafts, "required": 2}}}
        scored = [(row("Before", "2026-09-30T12:00:00Z"), score("Before", 2)),
                  (row("After", "2026-10-02T12:00:00Z"), score("After", 1)),
                  (row("NoRevision", "2026-10-02T12:00:00Z"), score("NoRevision", 0))]
        context = {"class": "c", "week": "w", "assignment": "a", "date": "d", "folder": None}
        with patch.object(app, "_form_rows", return_value=(context, scored)), \
                patch.object(ledger, "read", return_value={"configured": True, "feedback_at":
                                                           {"label": "2026-10-01 10:00", "at": "2026-10-01T14:00:00Z"}}), \
                patch.object(feedback, "load_prompt", return_value=("Prompt 2", "https://agenda.example")):
            out = app.feedback_list("token", {}, fixture(), "forensics", "Wk 04", self.FORM)
        self.assertEqual([s["name"] for s in out["students"]], ["A A."])
        self.assertEqual((out["done_before"], out["no_revision"]), (1, 1))

    def test_short_names_add_letters_only_where_two_would_match(self):
        students = [{"first": "Ana", "last": "Rivera"}, {"first": "Ana", "last": "Rios"},
                    {"first": "Chidi (CJ)", "last": "Okafor"}]
        names = feedback.short_names(students)
        self.assertEqual([names[id(s)] for s in students], ["Ana Riv.", "Ana Rio.", "Chidi O."])

    def test_the_prompt_lists_this_rounds_students_with_links_and_two_pages_each(self):
        students = [{"first": "Ana", "last": "Rivera", "section": "A2 Forensics", "url": "https://docs.example/latest",
                     "when": "Oct 01", "earlier": [{"url": "https://docs.example/first", "when": "Sep 22"}],
                     "draft_done": 1, "draft_required": 2}]
        context = {"class": "🔬 Forensics", "week": "Wk 04", "assignment": "Reasoning", "date": "2026-10-02"}
        text_out = feedback.build(context, students, "Prompt 2 — feedback for students\\nPART 2 ...")
        self.assertIn("1. Ana R. — A2 Forensics — Latest: https://docs.example/latest (Oct 01) — "
                      "Earlier: https://docs.example/first (Sep 22) — Revised answers done: 1 of 2", text_out)
        self.assertIn("exactly two pages", text_out)
        self.assertIn("If you can't open a link, tell me which ones and I'll upload those decks.", text_out)
        self.assertIn("Save it in my Google Drive", text_out)  # no week folder known
        context["folder"] = {"name": "📥 Wk 04 — Fictional", "url": "https://drive.google.com/drive/folders/FICTIONALWK04"}
        self.assertIn("Save it in this week's turn-in folder, “📥 Wk 04 — Fictional”: "
                      "https://drive.google.com/drive/folders/FICTIONALWK04",
                      feedback.build(context, students, "Prompt 2"))
        self.assertNotIn("Rivera", text_out)
        self.assertTrue(text_out.rstrip().endswith("PART 2 ..."))

    def test_the_feedback_prompt_is_read_from_the_agenda(self):
        cells = {(7, 1): cell("Feedback — Prompt 2"), (7, 2): cell("Prompt 2 — feedback for students\\n" + "x" * 300)}
        feedback._cache.clear()
        with patch.object(google, "sheet_tabs", return_value=("Agenda", {"Feedback Prompts": 42})), \
                patch.object(google, "sheet_grid", return_value={"Feedback Prompts": cells}):
            prompt, where = feedback.load_prompt("token", fixture())
        self.assertTrue(prompt.startswith("Prompt 2"))
        self.assertTrue(where.endswith("gid=42&range=B7"))


class RecordEndpointTests(unittest.TestCase):
    def post(self, origin, body):
        environ = {"PATH_INFO": "/v2/api/record", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        context = {"class": "c", "week": "w", "assignment": "a", "form_id": "f", "date": "d", "folder": None}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(app, "_form_context", return_value=(context, {}, {})), \
                patch.object(ledger, "read", return_value={"configured": True}), \
                patch.object(ledger, "record", return_value=1) as record:
            b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], record

    def test_noting_an_assignment_needs_the_dashboard_page(self):
        body = json.dumps({"class": "forensics", "week": "Wk 04", "form": "FICTIONALFORMID1234567890abc",
                           "kind": "ic"}).encode()
        status, record = self.post("https://elsewhere.example", body)
        self.assertEqual(status, "403 Forbidden")
        record.assert_not_called()
        status, record = self.post("https://hub.fictional.example", body)
        self.assertEqual(status, "200 OK")
        self.assertEqual(record.call_args.args[3], "ic")
        status, record = self.post("https://hub.fictional.example", body.replace(b'"ic"', b'"grades"'))
        self.assertEqual(status, "400 Bad Request")


class AgendaEditTests(unittest.TestCase):
    CONFIG = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
    # The fictional Biology tab: Wk, Dates, Topic, then Mon/Wed/Fri plan and note, To-do, Lab / prep.

    def save(self, changes, week="Wk 04", row=5, agenda_=None, readback=None, write_error=None):
        agenda_ = agenda_ or fixture()
        after = readback if readback is not None else {
            (5, 3): cell("Fictional topic"), (5, 6): cell("New Wed plan\nSecond line"), (5, 10): cell("Copy handouts")}
        with patch.object(google, "update_cells", side_effect=write_error) as write, \
                patch.object(google, "sheet_grid", return_value={"🌿 Biology": after}) as read:
            result = agenda_edit.save("token", "write", self.CONFIG, agenda_, "biology", week, row, changes)
        return result, write, read

    def test_only_changed_plain_cells_are_written_as_text_and_read_back(self):
        result, write, read = self.save({
            "plan:Wed": {"from": "Wed plan text", "to": "New Wed plan  \r\nSecond line"},
            "todo": {"from": "Copy handouts", "to": "Copy handouts"}})
        self.assertEqual(write.call_args.args[0], "write")  # only the write token writes
        self.assertEqual(write.call_args.args[2], [("'🌿 Biology'!F5", "New Wed plan\nSecond line")])
        self.assertEqual(read.call_args.args[2], ["'🌿 Biology'!F5"])
        self.assertEqual(result, {"saved": ["plan:Wed"], "unconfirmed": []})

    def test_a_cell_changed_in_the_agenda_meanwhile_is_returned_and_nothing_is_written(self):
        with self.assertRaises(agenda_edit.Changed) as caught:
            self.save({"plan:Wed": {"from": "What the page showed", "to": "Mine"},
                       "topic": {"from": "Fictional topic", "to": "New topic"}})
        self.assertEqual(caught.exception.current, {"plan:Wed": "Wed plan text"})

    def test_a_row_that_moved_is_refused(self):
        with self.assertRaises(agenda_edit.Changed):
            self.save({"topic": {"from": "Earlier topic", "to": "New"}}, week="Wk 04", row=4)

    def test_cells_with_links_and_columns_not_offered_are_never_written(self):
        linked = fixture()
        linked.classes["biology"]["cells"][(5, 10)] = cell("Copy handouts", hyperlink=DECK)
        for changes, agenda_ in (({"todo": {"from": "Copy handouts", "to": "x"}}, linked),
                                 ({"turnin": {"from": "", "to": "x"}}, None), ({}, None)):
            with self.assertRaises(ValueError):
                self.save(changes, agenda_=agenda_)

    def test_a_save_that_doesnt_read_back_is_reported(self):
        result, _, _ = self.save({"topic": {"from": "Fictional topic", "to": "New topic"}},
                                 readback={(5, 3): cell("Fictional topic")})
        self.assertEqual(result, {"saved": [], "unconfirmed": ["topic"]})

    def test_without_editor_access_the_error_says_who_to_share_with(self):
        with self.assertRaises(google.GoogleError) as caught:
            self.save({"topic": {"from": "Fictional topic", "to": "New"}},
                      write_error=google.GoogleError("no", 403))
        self.assertIn("reader@fictional.example as an Editor", str(caught.exception))

    def test_cells_are_written_as_plain_text(self):
        with patch.object(google, "get_json") as call_google:
            google.update_cells("write", "SHEETID", [("'Tab'!A1", "=1+1")])
        self.assertEqual(call_google.call_args.kwargs["body"]["valueInputOption"], "RAW")
        self.assertEqual(call_google.call_args.kwargs["method"], "POST")


class RoutineTests(unittest.TestCase):
    CONFIG = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT"}

    def status(self, fail=None):
        found = {
            "Monday run": [{"id": "OLDRUN", "name": "Monday run — 2026-09-28", "createdTime": "2026-09-28T12:00:00Z"},
                           {"id": "NEWRUN", "name": "Monday run — 2026-10-05", "createdTime": "2026-10-05T12:00:00Z"},
                           {"id": "NOTE", "name": "Notes about the Monday run", "createdTime": "2026-10-06T00:00:00Z"}],
            "Scores entered": [{"id": "LEDGER", "name": "Scores entered (On Dashboard)", "createdTime": "2026-09-26T00:00:00Z"}],
            "Student answers": [{"id": "ANSWERS", "name": "Student answers — Biology", "createdTime": "2026-09-26T00:00:00Z"}]}

        def search(token, name, mime, parent, prefix=False, order=None):
            if fail == name:
                raise google.GoogleError("Not found, or not shared with the dashboard's account.", 404)
            return found[name]
        rows = {"'Sheet1'!A2:A": [["2026-09-28 08:10"], [""], ["2026-10-05 08:05"]]}
        with patch.object(google, "files_named_in", side_effect=search), \
                patch.object(google, "sheet_tabs", return_value=("x", {"Sheet1": 0})), \
                patch.object(google, "sheet_values", side_effect=lambda t, sid, r: rows.get(r, [])):
            return routine.status("token", self.CONFIG)

    def test_the_routine_page_shows_the_newest_monday_run_and_each_sheets_last_row(self):
        out = self.status()
        self.assertEqual(out["monday_run"]["name"], "Monday run — 2026-10-05")
        self.assertTrue(out["monday_run"]["url"].endswith("/document/d/NEWRUN/edit"))
        self.assertEqual((out["scores_entered"]["name"], out["scores_entered"]["last"]),
                         ("Scores entered (On Dashboard)", "2026-10-05 08:05"))
        self.assertEqual(out["research"]["last"], "2026-10-05 08:05")

    def test_one_unreadable_record_doesnt_hide_the_others(self):
        out = self.status(fail="Monday run")
        self.assertIn("not shared", out["monday_run"]["problem"])
        self.assertEqual(out["scores_entered"]["name"], "Scores entered (On Dashboard)")
        with patch.object(google, "files_named_in", return_value=[]):
            self.assertEqual(routine.status("token", {}), {"monday_run": None, "scores_entered": None, "research": None})


class SweepEndpointTests(unittest.TestCase):
    def post(self, origin, payload):
        body = json.dumps(payload).encode()
        environ = {"PATH_INFO": "/v2/api/answers/sweep", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(answers, "sweep", return_value={"configured": True, "saved": 0, "more": False}) as sweep:
            b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], sweep

    def test_the_sweep_needs_the_dashboard_page_and_a_research_class(self):
        status, sweep = self.post("https://elsewhere.example", {"class": "biology"})
        self.assertEqual(status, "403 Forbidden")
        sweep.assert_not_called()
        status, sweep = self.post("https://hub.fictional.example", {"class": "biology"})
        self.assertEqual((status, sweep.call_args.args[4]), ("200 OK", "biology"))
        status, sweep = self.post("https://hub.fictional.example", {"class": "forensics"})
        self.assertEqual(status, "400 Bad Request")
        sweep.assert_not_called()


class LinkCheckTests(unittest.TestCase):
    TITLES = {"FICTIONALFORMID1234567890abc": "Forensics — Week 03 — Reasoning — Turn In",
              "FICTIONALFORMID0987654321xyz": "Forensics — Week 04 — Observation — Turn In",
              "FICTIONALDECK1234567890": "▶ LIVE — Week 04 — Eyewitness Accounts (VT)"}

    def check(self, agenda_, class_key, titles=None):
        titles = titles or self.TITLES
        def drive(token, file_id, fields=None):
            if file_id not in titles:
                raise google.GoogleError("Not found, or not shared with the dashboard's account.", 404)
            return {"name": titles[file_id]}
        def info(token, form_id):
            return {"info": {"title": "Student title " + form_id[-3:]},
                    "responderUri": "https://docs.google.com/forms/d/e/1FAIpQLSpublished%s/viewform" % form_id[-12:]}
        with patch.object(google, "drive_file", side_effect=drive), patch.object(google, "form_info", side_effect=info):
            return audit.check("token", agenda_, class_key)

    def kinds(self, out):
        return sorted((f["week"], f["kind"]) for f in out["flags"])

    def test_a_form_named_for_another_week_and_a_lesson_with_no_form_are_flagged(self):
        out = self.check(fixture(), "forensics")
        self.assertEqual(self.kinds(out), [("Wk 03", "no-form"), ("Wk 04", "other-week")])
        other = next(f for f in out["flags"] if f["kind"] == "other-week")
        self.assertIn("for Week 3", other["message"])
        forms = [f for f in out["rows"][1]["forms"] if f["where"] == "Turn it in"]
        self.assertEqual([f["title"] for f in forms], ["Forensics — Week 03 — Reasoning — Turn In",
                                                       "Forensics — Week 04 — Observation — Turn In"])
        # Each Form carries its published id and student-facing title, to match Schoology's published links.
        self.assertEqual(forms[0]["published_id"], "1FAIpQLSpublished" + "234567890abc")
        self.assertEqual(forms[0]["student_title"], "Student title abc")

    def test_a_published_link_a_form_linked_in_two_weeks_and_a_stale_hidden_link_are_flagged(self):
        out = self.check(fixture(), "anatomy")  # A&P: a published "Turn it in" link, direct hidden link
        self.assertIn(("Wk 04", "unreadable"), self.kinds(out))
        shared = fixture()
        cells = shared.classes["biology"]["cells"]
        cells[(4, 20)] = cell("Turn in", hyperlink=DIRECT)
        cells[(5, 21)] = cell(SECOND)  # the hidden link opens a different Form than the visible one
        kinds = self.kinds(self.check(shared, "biology"))
        self.assertIn(("Wk 03, Wk 04", "shared-form"), kinds)  # one line for the Form, naming both weeks
        self.assertIn(("Wk 04", "hidden-differs"), kinds)

    def test_no_class_weeks_are_skipped_and_an_unopenable_deck_is_its_own_kind(self):
        quiet = fixture()
        quiet.classes["forensics"]["cells"][(4, 3)] = cell("No class — 0 meetings")
        titles = dict(self.TITLES)
        titles.pop("FICTIONALDECK1234567890")
        out = self.check(quiet, "forensics", titles)
        self.assertEqual(self.kinds(out), [("Wk 04", "deck-unreadable"), ("Wk 04", "other-week")])
        self.assertEqual([r["week"] for r in out["rows"]], ["Wk 04"])

    def test_a_form_the_dashboard_cant_open_is_flagged(self):
        titles = dict(self.TITLES)
        titles.pop("FICTIONALFORMID0987654321xyz")
        out = self.check(fixture(), "forensics", titles)
        self.assertIn(("Wk 04", "unreadable"), self.kinds(out))


def moving_fixture(extra=None):
    """The fictional Biology tab with more weeks: Wk 03 and Wk 04 lessons (rows 4, 5),
    Wk 05 closed (6), Wk 06 lesson (7), Wk 07 nothing planned (8), Wk 08 lesson (9),
    Wk 09 an [EMPTY …] slack week (10)."""
    base = fixture()
    cells = dict(base.classes["biology"]["cells"])
    for row, (label, topic) in {6: ("Wk 05", "[CLOSED ALL WEEK — no class]"), 7: ("Wk 06", "Photosynthesis"),
                                8: ("Wk 07", ""), 9: ("Wk 08", "Respiration"),
                                10: ("Wk 09", "[EMPTY WEEK — slack]")}.items():
        cells[(row, 1)] = cell(label)
        if topic:
            cells[(row, 3)] = cell(topic)
    cells.update(extra or {})
    tabs = {"🗂 Weeks": 1, "🌿 Biology": 2, "🔬 Forensics": 3, "🫀 A&P": 4}
    return agenda.Agenda("FICTIONALSHEET", "Fictional Agenda", tabs, {"🌿 Biology": cells})


class MoveLessonTests(unittest.TestCase):
    CONFIG = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}

    def move(self, to_week, week, row, agenda_=None, readback=None):
        agenda_ = agenda_ or moving_fixture()
        cells = agenda_.classes["biology"]["cells"]
        topic = lambda r: (cells.get((r, 3)) or {}).get("formattedValue", "")
        def grid_back(token, sid, ranges):
            if readback is not None:
                return {"🌿 Biology": readback}
            # What the copies leave: each destination row shows its source's topic.
            copies = [r["copyPaste"] for r in batch.call_args.args[2] if "copyPaste" in r]
            chain = list(dict.fromkeys(c["source"]["startRowIndex"] + 1 for c in copies
                                       if c["destination"]["sheetId"] != 2))
            shown = {}
            for c in copies:
                if c["destination"]["sheetId"] == 2 and c["source"]["startColumnIndex"] == 2:
                    shown[(c["destination"]["startRowIndex"] + 1, 3)] = cell(topic(chain[c["source"]["startRowIndex"]]))
            for r in batch.call_args.args[2]:
                if "updateCells" in r:
                    shown[(r["updateCells"]["range"]["startRowIndex"] + 1, 3)] = cell("")
            return {"🌿 Biology": shown}
        with patch.object(google, "batch_update") as batch, patch.object(google, "update_cells") as plain_write, \
                patch.object(google, "sheet_grid", side_effect=grid_back):
            result = agenda_edit.move("token", "write", self.CONFIG, agenda_, "biology", week, row, to_week)
        plain_write.assert_not_called()
        return result, batch

    def test_lessons_slide_over_and_closed_or_empty_weeks_are_skipped(self):
        result, batch = self.move("Wk 03", "Wk 08", 9)  # Respiration to Wk 03; the others move one week later
        requests = batch.call_args.args[2]
        self.assertEqual(batch.call_args.args[0], "write")
        self.assertIn("addSheet", requests[0])
        self.assertEqual(requests[-1], {"deleteSheet": {"sheetId": requests[0]["addSheet"]["properties"]["sheetId"]}})
        copies = [r["copyPaste"] for r in requests if "copyPaste" in r]
        # Only the lesson columns move: never Wk (A) or Dates (B), so the Agenda stays in week order.
        self.assertTrue(all(c["source"]["startColumnIndex"] == 2 for c in copies))
        back = [(c["destination"]["startRowIndex"] + 1) for c in copies if c["destination"]["sheetId"] == 2]
        self.assertEqual(back, [4, 9, 7, 5])
        self.assertEqual(result["moved"], {"from": "Wk 08", "to": "Wk 03", "topic": "Respiration"})
        self.assertEqual(result["shifted"], [{"topic": "Photosynthesis", "from": "Wk 06", "to": "Wk 08"},
                                             {"topic": "Fictional topic", "from": "Wk 04", "to": "Wk 06"},
                                             {"topic": "Earlier topic", "from": "Wk 03", "to": "Wk 04"}])
        self.assertEqual((result["skipped"], result["unconfirmed"]), (["Wk 07", "Wk 05"], []))

    def test_a_lesson_moves_into_an_empty_week_and_the_weeks_note_stays_behind(self):
        result, batch = self.move("Wk 09", "Wk 04", 5)
        requests = batch.call_args.args[2]
        back = [(c["copyPaste"]["destination"]["startRowIndex"] + 1) for c in requests
                if "copyPaste" in c and c["copyPaste"]["destination"]["sheetId"] == 2]
        self.assertEqual(back, [10, 5])
        cleared = [r["updateCells"]["range"] for r in requests if "updateCells" in r]
        self.assertEqual([(c["startRowIndex"] + 1, c["startColumnIndex"] + 1) for c in cleared], [(5, 3)])
        self.assertEqual((result["shifted"], result["skipped"], result["unconfirmed"]), ([], [], []))

    def test_closed_weeks_non_lessons_moved_rows_and_formulas_that_point_elsewhere_are_refused(self):
        with self.assertRaises(agenda_edit.Refused):
            self.move("Wk 05", "Wk 04", 5)  # no class that week
        with self.assertRaises(agenda_edit.Refused):
            self.move("Wk 03", "Wk 07", 8)  # nothing to move
        with self.assertRaises(agenda_edit.Refused):
            self.move("Wk 40", "Wk 04", 5)
        with self.assertRaises(agenda_edit.Changed):
            self.move("Wk 03", "Wk 03", 5)
        with self.assertRaises(ValueError):
            self.move("Wk 04", "Wk 04", 5)
        for formula in ("=Weeks!C3", "=C6", "=$C$7"):
            elsewhere = moving_fixture({(7, 4): cell("x", userEnteredValue={"formulaValue": formula})})
            with self.assertRaises(agenda_edit.Refused):
                self.move("Wk 03", "Wk 08", 9, elsewhere)
        same_row = moving_fixture({(7, 12): cell("Student", userEnteredValue={
            "formulaValue": '=IF(P7="","",HYPERLINK(P7,"🎞 Student copy ↗"))'})})
        self.assertEqual(self.move("Wk 03", "Wk 08", 9, same_row)[0]["unconfirmed"], [])

    def test_a_move_that_doesnt_read_back_is_reported(self):
        result, _ = self.move("Wk 03", "Wk 08", 9, readback={(4, 3): cell("Respiration")})
        self.assertEqual(result["unconfirmed"], ["Wk 08", "Wk 06", "Wk 04"])


class LinkSaveTests(unittest.TestCase):
    CONFIG = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
    NEW_DECK = "https://docs.google.com/presentation/d/FICTIONALNEWDECK0987654321/edit?usp=sharing"

    def save(self, changes, readback=None):
        with patch.object(google, "batch_update") as batch, patch.object(google, "update_cells") as plain_write, \
                patch.object(google, "sheet_grid", return_value={"🌿 Biology": readback or {}}):
            result = agenda_edit.save("token", "write", self.CONFIG, fixture(), "biology", "Wk 04", 5, changes)
        return result, batch, plain_write

    def written(self, batch):
        return {r["updateCells"]["range"]["startColumnIndex"] + 1: r["updateCells"]["rows"][0]["values"][0]
                for r in batch.call_args.args[2]}

    def test_a_deck_link_sets_the_teacher_link_and_the_students_copy_link(self):
        edit = "https://docs.google.com/presentation/d/FICTIONALNEWDECK0987654321/edit"
        copy = edit[:-4] + "copy"
        result, batch, plain_write = self.save({"link:deck": {"from": DECK, "to": self.NEW_DECK}},
                                               readback={(5, 17): cell(edit), (5, 16): cell(copy)})
        plain_write.assert_not_called()
        self.assertEqual(self.written(batch), {17: {"userEnteredValue": {"stringValue": edit}},
                                               16: {"userEnteredValue": {"stringValue": copy}}})
        self.assertEqual(result, {"saved": ["link:deck"], "unconfirmed": []})

    def test_a_turn_in_form_link_is_a_plain_link_and_a_labelled_link_never_a_formula(self):
        form = "https://docs.google.com/forms/d/FICTIONALNEWFORM0987654321abc/viewform"
        result, batch, _ = self.save({"link:turnin": {"from": DIRECT, "to": form + "?usp=sharing"},
                                      "topic": {"from": "Fictional topic", "to": "Types of Evidence"}},
                                     readback={(5, 21): cell(form), (5, 3): cell("Types of Evidence"),
                                               (5, 20): cell("📤 Types of Evidence — Turn in", hyperlink=form)})
        cells = self.written(batch)
        self.assertEqual(cells[21], {"userEnteredValue": {"stringValue": form}})
        self.assertEqual(cells[20], {"userEnteredValue": {"stringValue": "📤 Types of Evidence — Turn in"},
                                     "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": form}}}]})
        self.assertTrue(all("formulaValue" not in c["userEnteredValue"] for c in cells.values()))
        self.assertEqual(result["unconfirmed"], [])

    def test_unreadable_links_changed_links_and_missing_columns_are_refused(self):
        for to in (PUBLISHED, "https://forms.gle/fictional", "https://example.com/form"):
            with self.assertRaises(agenda_edit.Refused):
                self.save({"link:turnin": {"from": DIRECT, "to": to}})
        with self.assertRaises(agenda_edit.Refused):
            self.save({"link:deck": {"from": DECK, "to": "https://example.com/slides"}})
        with self.assertRaises(agenda_edit.Changed) as caught:
            self.save({"link:deck": {"from": "https://docs.google.com/presentation/d/SOMETHINGELSE12345678/edit",
                                     "to": self.NEW_DECK}})
        self.assertEqual(caught.exception.current, {"link:deck": DECK})
        with self.assertRaises(ValueError):
            self.save({"link:turnin2": {"from": "", "to": SECOND}})  # this tab has no "2nd turn it in" column

    def test_the_second_turn_in_can_be_named_without_changing_its_link(self):
        cells = dict(fixture().classes["biology"]["cells"])
        cells[(3, 22)] = cell("2nd turn it in")
        cells[(5, 22)] = cell("📤 2nd turn in", hyperlink=SECOND)
        agenda_ = agenda.Agenda("FICTIONALSHEET", "Fictional Agenda", {"🌿 Biology": 2}, {"🌿 Biology": cells})
        lesson = agenda_.lessons("biology", "Wk 04")[0]
        self.assertEqual((lesson["links"]["turnin2"], lesson["link_labels"]),
                         (SECOND, {"turnin": "Turn it in", "turnin2": "📤 2nd turn in"}))
        with patch.object(google, "batch_update") as batch, patch.object(google, "update_cells"), \
                patch.object(google, "sheet_grid", return_value={"🌿 Biology": {(5, 22): cell("x", hyperlink=SECOND)}}):
            result = agenda_edit.save("token", "write", self.CONFIG, agenda_, "biology", "Wk 04", 5, {
                "link:turnin2": {"from": SECOND, "to": SECOND, "label": "📤 Investigative Processes — Turn in"}})
        written = batch.call_args.args[2][0]["updateCells"]["rows"][0]["values"][0]
        self.assertEqual(written["userEnteredValue"], {"stringValue": "📤 Investigative Processes — Turn in"})
        self.assertEqual(result, {"saved": ["link:turnin2"], "unconfirmed": []})
        with self.assertRaises(ValueError):  # only Turn-in Forms take a name
            self.save({"link:activity": {"from": "", "to": "", "label": "x"}})

    def test_the_main_turn_in_can_be_named_and_a_second_deck_linked(self):
        cells = dict(fixture().classes["biology"]["cells"])
        cells[(3, 22)] = cell("2nd deck")
        agenda_ = agenda.Agenda("FICTIONALSHEET", "Fictional Agenda", {"🌿 Biology": 2}, {"🌿 Biology": cells})
        second = "https://docs.google.com/presentation/d/FICTIONALSECONDDECK12345/edit"
        with patch.object(google, "batch_update") as batch, patch.object(google, "update_cells"), \
                patch.object(google, "sheet_grid", return_value={"🌿 Biology": {(5, 22): cell(second), (5, 21): cell(DIRECT),
                                                                                (5, 20): cell("x", hyperlink=DIRECT)}}):
            result = agenda_edit.save("token", "write", self.CONFIG, agenda_, "biology", "Wk 04", 5, {
                "link:deck2": {"from": "", "to": second + "?usp=sharing"},
                "link:turnin": {"from": DIRECT, "to": DIRECT, "label": "Searching the Scene — Turn in"}})
        cells_written = {r["updateCells"]["range"]["startColumnIndex"] + 1: r["updateCells"]["rows"][0]["values"][0]
                         for r in batch.call_args.args[2]}
        self.assertEqual(cells_written[22], {"userEnteredValue": {"stringValue": second}})
        self.assertEqual(cells_written[20]["userEnteredValue"], {"stringValue": "📤 Searching the Scene — Turn in"})
        self.assertEqual(result["unconfirmed"], [])

    def test_clearing_a_link_empties_its_cells(self):
        result, batch, _ = self.save({"link:activity": {"from": "", "to": ""}})
        batch.assert_not_called()
        self.assertEqual(result, {"saved": [], "unconfirmed": []})


class MoveEndpointTests(unittest.TestCase):
    def post(self, origin, payload):
        body = json.dumps(payload).encode()
        environ = {"PATH_INFO": "/v2/api/agenda/move", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(agenda_edit, "move", return_value={"moved": {}, "swapped": []}) as move:
            b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], move

    def test_moving_a_lesson_needs_the_dashboard_page(self):
        payload = {"class": "biology", "week": "Wk 04", "row": 5, "to_week": "Wk 03"}
        status, move = self.post("https://elsewhere.example", payload)
        self.assertEqual(status, "403 Forbidden")
        move.assert_not_called()
        status, move = self.post("https://hub.fictional.example", payload)
        self.assertEqual((status, move.call_args.args[4:]), ("200 OK", ("biology", "Wk 04", 5, "Wk 03")))
        for bad in ({**payload, "row": "5"}, {**payload, "to_week": ""}, {**payload, "class": "chemistry"}):
            status, _ = self.post("https://hub.fictional.example", bad)
            self.assertEqual(status, "400 Bad Request")


class AgendaEndpointTests(unittest.TestCase):
    def post(self, origin, payload, outcome=None):
        body = json.dumps(payload).encode()
        environ = {"PATH_INFO": "/v2/api/agenda", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(agenda_edit, "save", side_effect=outcome,
                             return_value={"saved": ["topic"], "unconfirmed": []}) as save:
            out = b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], json.loads(out or b"{}"), save

    def test_saving_a_lesson_needs_the_dashboard_page_and_reports_a_changed_cell(self):
        payload = {"class": "biology", "week": "Wk 04", "row": 5,
                   "changes": {"topic": {"from": "Fictional topic", "to": "New topic"}}}
        status, _, save = self.post("https://elsewhere.example", payload)
        self.assertEqual(status, "403 Forbidden")
        save.assert_not_called()
        status, body, save = self.post("https://hub.fictional.example", payload)
        self.assertEqual((status, body["saved"]), ("200 OK", ["topic"]))
        self.assertEqual(save.call_args.args[4:], ("biology", "Wk 04", 5, payload["changes"]))
        status, body, _ = self.post("https://hub.fictional.example", payload,
                                    outcome=agenda_edit.Changed({"topic": "Theirs"}, "changed"))
        self.assertEqual((status, body["current"]), ("409 Conflict", {"topic": "Theirs"}))
        for bad in ({**payload, "row": "5"}, {**payload, "row": True}, {**payload, "changes": {"topic": {"to": "x"}}}):
            status, _, _ = self.post("https://hub.fictional.example", bad)
            self.assertEqual(status, "400 Bad Request")


class ResearchSheetTests(unittest.TestCase):
    CONFIG = {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT", "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
    FORM = "FICTIONALFORMID1234567890abc"
    CONTEXT = {"class": "🧬 Biology", "week": "Wk 03", "assignment": "Energy flow — Turn in", "form_id": FORM}
    CLOSED = {"window": {"at": "2026-09-20T03:59:00Z", "label": "Sat Sep 19, 11:59 PM"}}
    NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)

    def scored(self):
        row = {"matched": True, "last": "Rivera", "first": "Ana", "section": "A4 Biology",
               "submitted_at": "2026-09-15T14:30:00Z",
               "files": [{"id": "DECK1", "url": "https://docs.google.com/presentation/d/DECK1/edit"}]}
        result = {"file_id": "DECK1", "problem": None, "file_updated": "2026-09-16T01:00:00Z", "items": [
            {"category": "NOTES", "id": "CA1", "status": "done", "answer": "=Energy flows one way", "at": 4,
             "question": "Your answer — Critical Aspect 1 · Energy flow"},
            {"category": "DRAFT", "id": "CA1", "status": "blank", "answer": "", "at": 4,
             "question": "Your answer — Critical Aspect 1 · Energy flow"}]}
        unscorable = ({**row, "last": "Lee", "first": "Bo", "files": [{"id": "DECK2"}]},
                      {"file_id": "DECK2", "problem": "Couldn't read this deck."})
        return [(row, result), unscorable]

    def run_save(self, header, keys, form=None, sheets=None):
        sheets = sheets if sheets is not None else [{"id": "SHEETID", "name": "Student answers — Biology"}]
        values = lambda token, sid, cell_range: header if cell_range.endswith("!1:1") else keys
        with patch.object(google, "files_named_in", return_value=sheets) as search, \
                patch.object(google, "sheet_tabs", return_value=("Student answers", {"Answers": 0})), \
                patch.object(google, "sheet_values", side_effect=values) as read, \
                patch.object(google, "append_rows") as append:
            out = answers.save("token", "write", self.CONFIG, "biology", self.CONTEXT, "Energy flow",
                               form or self.CLOSED, self.scored(), now=self.NOW)
        return out, append, read, search

    def test_a_closed_assignment_adds_one_row_per_box_and_never_twice(self):
        out, append, _, _ = self.run_save([], [])
        self.assertEqual((out["saved"], out["already"]), (2, 0))
        self.assertEqual(append.call_args.args[0], "write")  # only the write token writes
        written = append.call_args.args[3]
        self.assertEqual(written[0], answers.HEADER)
        first = dict(zip(answers.HEADER, written[1]))
        self.assertEqual(first["Student"], "Rivera, Ana")
        self.assertEqual((first["Section"], first["Week"], first["Lesson"]), ("A4 Biology", "Wk 03", "Energy flow"))
        self.assertEqual((first["Slide"], first["Type"], first["Code"]), (4, "NOTES", "CA1"))
        self.assertEqual(first["Answer"], "=Energy flows one way")  # written as text, never run
        self.assertEqual((first["Status"], first["Turned in"]), ("answered", "2026-09-15 10:30"))
        self.assertEqual(dict(zip(answers.HEADER, written[2]))["Status"], "blank")
        self.assertEqual(len(written), 3)  # the deck that couldn't be scored adds nothing
        keys = [[r[-1]] for r in written[1:]]
        out, append, _, _ = self.run_save([answers.HEADER], keys)
        self.assertEqual((out["saved"], out["already"]), (0, 2))
        append.assert_not_called()

    def test_keys_are_found_by_their_heading_if_a_column_was_added(self):
        _, _, read, _ = self.run_save([["Code it"] + answers.HEADER], [])
        self.assertIn("!R2:R200000", read.call_args_list[-1].args[2])

    def test_an_open_assignment_waits_and_touches_nothing(self):
        form = {"window": {"at": "2026-09-29T03:59:00Z", "label": "Mon Sep 28, 11:59 PM"}}
        out, append, read, search = self.run_save([], [], form=form)
        self.assertEqual(out["waiting"], "Mon Sep 28, 11:59 PM")
        search.assert_not_called()
        read.assert_not_called()
        append.assert_not_called()

    def test_only_the_sheet_with_the_exact_name_is_written_to(self):
        sheets = [{"id": "COPY", "name": "Copy of Student answers — Biology", "createdTime": "2026-09-01T00:00:00Z"},
                  {"id": "CODED", "name": "Student answers — Biology coded", "createdTime": "2026-09-02T00:00:00Z"},
                  {"id": "HERS", "name": "student answers - Biology", "createdTime": "2026-09-03T00:00:00Z"}]
        with patch.object(google, "files_named_in", return_value=sheets):
            self.assertEqual(answers.find("token", self.CONFIG, "biology")["id"], "HERS")
        out, append, _, _ = self.run_save([], [], sheets=sheets[:2])
        self.assertEqual(out["configured"], False)
        self.assertIn("Student answers — Biology", out["setup"])
        append.assert_not_called()

    def test_rows_are_appended_as_plain_text(self):
        with patch.object(google, "get_json") as call_google:
            google.append_rows("write", "SHEETID", "'Answers'!A1", [["=1+1"]])
        self.assertEqual(call_google.call_args.args[2]["valueInputOption"], "RAW")

    def run_sweep(self, keys, now, form_window=None, clock=None):
        form = {"form_id": self.FORM, "label": "Turn in", "window": form_window or self.CLOSED["window"]}
        found = {"lessons": [{"topic": "Fictional topic", "forms": [form]}]}
        load = unittest.mock.Mock(return_value=found)
        score = unittest.mock.Mock(return_value=self.scored())
        header = [answers.HEADER] if keys else []
        values = lambda token, sid, cell_range: header if cell_range.endswith("!1:1") else keys
        with patch.object(google, "files_named_in", return_value=[{"id": "SHEETID", "name": "Student answers — Biology"}]), \
                patch.object(google, "sheet_tabs", return_value=("Student answers", {"Answers": 0})), \
                patch.object(google, "sheet_values", side_effect=values), \
                patch.object(google, "append_rows") as append:
            out = answers.sweep("token", "write", self.CONFIG, fixture(), "biology", load, score, now=now,
                                **({"clock": clock} if clock else {}))
        return out, load, score, append

    def test_the_daily_sweep_adds_closed_assignments_without_opening_each_week(self):
        out, load, score, append = self.run_sweep([], self.NOW.replace(month=10, day=10))
        load.assert_called_once_with("Wk 04")  # Wk 03 has no Form, so Drive isn't read for it
        self.assertEqual(score.call_args.args[0], "Wk 04")
        written = append.call_args.args[3]
        self.assertEqual(written[0], answers.HEADER)
        self.assertEqual(dict(zip(answers.HEADER, written[1]))["Week"], "Wk 04")
        self.assertEqual((out["saved"], out["more"]), (2, False))
        self.assertEqual(out["forms"], [{"week": "Wk 04", "assignment": "Turn in", "saved": 2}])
        # Next day: this Form already has rows, so nothing is read or written.
        keys = [[r[-1]] for r in written[1:]]
        out, load, _, append = self.run_sweep(keys, self.NOW.replace(month=10, day=11))
        load.assert_not_called()
        append.assert_not_called()
        self.assertEqual(out["saved"], 0)

    def test_the_sweep_skips_weeks_not_started_and_waits_for_open_assignments(self):
        _, load, _, append = self.run_sweep([], datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc))
        load.assert_not_called()  # Wk 04 starts Monday Sep 21
        open_window = {"at": "2026-10-09T03:59:00Z", "label": "Thu Oct 8, 11:59 PM"}
        out, load, score, append = self.run_sweep([], self.NOW, form_window=open_window)
        load.assert_called_once_with("Wk 04")
        score.assert_not_called()
        self.assertEqual(out["saved"], 0)

    def test_the_sweep_stops_at_its_time_limit_and_asks_to_be_called_again(self):
        ticks = iter([0, answers.SWEEP_SECONDS + 1])
        out, load, _, _ = self.run_sweep([], self.NOW.replace(month=10, day=10), clock=lambda: next(ticks))
        self.assertTrue(out["more"])
        load.assert_not_called()

    def test_only_biology_has_a_research_sheet(self):
        with self.assertRaises(ValueError):
            answers.save("token", "write", self.CONFIG, "forensics", self.CONTEXT, "", self.CLOSED, [], now=self.NOW)


class AnswersEndpointTests(unittest.TestCase):
    def post(self, origin, body):
        environ = {"PATH_INFO": "/v2/api/answers", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                   "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
        seen = {}
        config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
        form_id = "FICTIONALFORMID1234567890abc"
        found = {"lessons": [{"topic": "Energy flow", "forms": [{"form_id": form_id}]}]}
        with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                patch.object(google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=fixture()), \
                patch.object(app, "_form_context", return_value=({"form_id": form_id}, {}, found)), \
                patch.object(answers, "closed", return_value=True), \
                patch.object(app, "_score_rows", return_value=[]), \
                patch.object(answers, "save", return_value={"configured": True, "saved": 0}) as save:
            b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
        return seen["status"], save

    def test_saving_answers_needs_the_dashboard_page_and_a_research_class(self):
        body = json.dumps({"class": "biology", "week": "Wk 03", "form": "FICTIONALFORMID1234567890abc"}).encode()
        status, save = self.post("https://elsewhere.example", body)
        self.assertEqual(status, "403 Forbidden")
        save.assert_not_called()
        status, save = self.post("https://hub.fictional.example", body)
        self.assertEqual(status, "200 OK")
        self.assertEqual(save.call_args.args[3:6], ("biology", {"form_id": "FICTIONALFORMID1234567890abc"}, "Energy flow"))
        status, save = self.post("https://hub.fictional.example", body.replace(b"biology", b"forensics"))
        self.assertEqual(status, "400 Bad Request")
        save.assert_not_called()


def bank_deck(**changes):
    """A fictional Biology-style deck: the code list split over several boxes,
    a Concept Bank whose codes sit together in a corner, and drag cards."""
    text = lambda name, default: changes.get(name, default)
    return pptx([
        [("TITLE", "Biology 09 — response manifest", (0, 0, 900, 30)),
         ("HEAD", "[[MARKER-INVENTORY:C09]]\nVERSION=C09-r1\nNOTES=1  DRAFT=0  OPTIONAL=0  BANK_TERMS=2", (0, 40, 900, 60)),
         ("LISTS", "NOTES_IDS=\nSQUID-CLASSIFY\nDRAFT_IDS=\n\nOPTIONAL_IDS=\n\nBANK_IDS=\nProducer\nTrophic level\n"
                   "CHECKPOINTS=UNVERIFIED; do not infer due dates.", (0, 110, 900, 300))],
        [("Q", "Name the type of symbiosis.", (20, 20, 800, 60)),
         ("A", text("A", ""), (20, 100, 800, 200)),
         ("M", "[[NOTES:C09:The bobtail squid]]", (20, 110, 150, 10))],
        [("L1", "Producer  ", (20, 20, 200, 40)), ("A1", text("A1", ""), (240, 20, 500, 40)),
         ("L2", "Trophic level  ", (20, 100, 200, 40)), ("A2", text("A2", ""), (240, 100, 500, 40)),
         ("M1", "[[BANK:C09:Producer]]", (900, 540, 90, 10)), ("M2", "[[BANK:C09:Trophic level]]", (900, 552, 90, 10))],
        [("CARD", "Hawk", changes.get("CARD", (20, 400, 80, 30)))],
    ])


class RealDeckShapeTests(unittest.TestCase):
    def assess(self, **changes):
        return completion.assess(completion.read_deck(bank_deck(**changes)), completion.read_deck(bank_deck()))

    def test_a_code_list_split_over_several_boxes_is_read(self):
        code, required, basis, note = completion.inventory(completion.read_deck(bank_deck()))
        self.assertEqual((code, basis), ("C09", "header"))
        self.assertEqual((required["NOTES"], required["BANK"]), (["SQUID-CLASSIFY"], ["Producer", "Trophic level"]))

    def test_concept_bank_answers_go_to_the_term_beside_them(self):
        result = self.assess(A2="one step in a food chain")
        status = {i["id"]: i["status"] for i in result["items"] if i["category"] == "BANK"}
        self.assertEqual(status, {"Producer": "blank", "Trophic level": "done"})

    def test_a_code_list_with_a_bank_count_but_no_bank_ids_uses_the_decks_bank_codes(self):
        deck = completion.read_deck(pptx([
            [("H", "[[MARKER-INVENTORY:F09]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=2 NOTES_IDS=Q1 DRAFT_IDS= OPTIONAL_IDS=",
              (0, 0, 900, 60))],
            [("M0", "[[NOTES:F09:Q1]]", (0, 0, 10, 10)), ("M1", "[[BANK:F09:Premise]]", (0, 20, 10, 10)),
             ("M2", "[[BANK:F09:Hypothesis]]", (0, 40, 10, 10))]]))
        code, required, basis, note = completion.inventory(deck)
        self.assertEqual((basis, required["BANK"], note), ("header", ["Premise", "Hypothesis"], None))

    def test_a_camelcase_bank_code_matches_its_spaced_label(self):
        deck = lambda **c: pptx([
            [("L1", "inductive reasoning", (20, 20, 300, 30)), ("A1", c.get("A1", ""), (20, 60, 300, 80)),
             ("L2", "hypothesis", (20, 200, 300, 30)), ("A2", c.get("A2", ""), (20, 240, 300, 80)),
             ("M1", "[[BANK:F09:InductiveReasoning]]", (900, 540, 90, 10)),
             ("M2", "[[BANK:F09:Hypothesis]]", (900, 552, 90, 10))]])
        result = completion.assess(completion.read_deck(deck(A1="patterns from examples")), completion.read_deck(deck()))
        status = {i["id"]: i["status"] for i in result["items"]}
        self.assertEqual(status, {"InductiveReasoning": "done", "Hypothesis": "blank"})

    def test_a_dragged_card_is_not_uncoded_writing(self):
        result = self.assess(CARD=(600, 100, 80, 30))
        self.assertEqual(result["uncoded"], [])

    def test_codes_that_dont_match_the_code_list_are_named(self):
        result = self.assess()
        self.assertEqual(result["coding"], {"missing": ["NOTES SQUID-CLASSIFY"], "extra": ["NOTES The bobtail squid"]})

    def test_original_title(self):
        self.assertEqual(completion.original_title("s.ana.rivera — 09-16 12.41 — Copy of ▶ LIVE — Week 03 — Observation - Ana Rivera.pptx"),
                         "▶ LIVE — Week 03 — Observation")
        self.assertEqual(completion.original_title("Copy of Observation Skills - Sam Lee"), "Observation Skills")
        self.assertEqual(completion.original_title("Deck"), "")

    def test_a_deck_shared_only_by_link_is_found_beside_the_linked_deck(self):
        completion._templates.clear()
        completion._results.clear()
        completion._folders.clear()
        decks = {"FICTIONALDECK1234567890": handout(), "ORIGINALDECK123456789": bank_deck(),
                 "STUDENTDECK123": bank_deck(A2="one step in a food chain")}
        row = {"matched": True, "last": "Ennis", "first": "Tre", "section": "A2 Forensics", "earlier": [], "late": False,
               "files": [{"id": "STUDENTDECK123", "mime": completion.SLIDES,
                          "name": "s.tre.ennis — 09-16 11.29 — Copy of Bank_Deck_markered - Tre Ennis"}]}
        found = {"lessons": [{"forms": [{"form_id": ScoreFileTests.FORM, "students": [row], "late": []}]}]}
        lesson_folder = [{"id": "FICTIONALDECK1234567890", "name": "Linked deck", "mimeType": completion.SLIDES},
                         {"id": "ORIGINALDECK123456789", "name": "Bank_Deck_markered", "mimeType": completion.SLIDES},
                         {"id": "NOTES", "name": "Bank_Deck_markered", "mimeType": "application/vnd.google-apps.document"}]
        with patch.object(google, "drive_file", side_effect=lambda t, fid, fields=None: {
                    "id": fid, "name": "Deck", "mimeType": completion.SLIDES, "modifiedTime": "t", "parents": ["LESSONFOLDER"]}), \
                patch.object(google, "deck_bytes", side_effect=lambda t, fid, mime: decks[fid]), \
                patch.object(google, "files_named", return_value=[]), \
                patch.object(google, "folder_children", side_effect=lambda t, fid: lesson_folder if fid == "LESSONFOLDER" else []):
            result = completion.score_file("token", fixture(), "forensics", "Wk 04", ScoreFileTests.FORM,
                                           "STUDENTDECK123", found)
        self.assertIsNone(result["problem"])
        self.assertEqual(result["counts"]["BANK"], {"done": 1, "required": 2})

    def test_the_handed_out_deck_is_found_by_name_when_the_agenda_links_another(self):
        completion._templates.clear()
        completion._results.clear()
        decks = {"FICTIONALDECK1234567890": handout(), "ORIGINALDECK123456789": bank_deck(),
                 "STUDENTDECK123": bank_deck(A2="one step in a food chain")}
        row = {"matched": True, "last": "Rivera", "first": "Ana", "section": "A2 Forensics", "earlier": [], "late": False,
               "files": [{"id": "STUDENTDECK123", "mime": completion.SLIDES, "name": "Copy of Bank Deck - Ana Rivera"}]}
        found = {"lessons": [{"forms": [{"form_id": ScoreFileTests.FORM, "students": [row], "late": []}]}]}
        with patch.object(google, "drive_file", side_effect=lambda t, fid, fields=None: {
                    "id": fid, "name": "Deck", "mimeType": completion.SLIDES, "modifiedTime": "t"}), \
                patch.object(google, "deck_bytes", side_effect=lambda t, fid, mime: decks[fid]), \
                patch.object(google, "files_named", return_value=[{"id": "ORIGINALDECK123456789"}]) as search:
            result = completion.score_file("token", fixture(), "forensics", "Wk 04", ScoreFileTests.FORM,
                                           "STUDENTDECK123", found)
        search.assert_called_once_with("token", "Bank Deck", completion.SLIDES)
        self.assertIsNone(result["problem"])
        self.assertEqual(result["counts"]["BANK"], {"done": 1, "required": 2})
