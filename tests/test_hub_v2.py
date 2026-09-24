"""Teaching Hub v2 on fictional data only; no Google call is made."""
import io
import json
import unittest
import zipfile
from datetime import date, datetime, timezone
from unittest.mock import patch

from hub_v2 import agenda, app, completion, feedback, google, ledger, roster, tidy, work

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

    def test_roster_check_flags_work_from_another_class_or_no_roster(self):
        biology, _ = roster.parse_tab(grid({1: ["Last Name", "First Name"], 2: ["Riggs", "Kay"]}),
                                      "biology", "A4 Biology")
        config = {"HUB_READER_SERVICE_ACCOUNT": "reader@fictional.example"}
        responses = [response("s.ana.rivera@school.example", "2026-09-22T14:00:00Z", ("F1", "Deck")),
                     response("s.kay.riggs@school.example", "2026-09-22T14:00:00Z", ("F2", "Deck")),
                     response("s.new.student@school.example", "2026-09-22T14:00:00Z", ("F3", "Deck"))]
        config["HUB_SCHOOL_EMAIL_DOMAINS"] = "school.example"
        with patch.object(work.roster, "load", return_value=({"forensics": self.students, "biology": biology}, [])), \
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

    def test_name_time_and_deck_stem(self):
        self.assertEqual(work.name_time("s.a.b — 09-16 11.29 — x", "2026-09-20T01:00:00Z"), "2026-09-16T15:29:00Z")
        self.assertEqual(work.name_time("s.a.b — 01-05 10.00 — x", "2027-01-06T01:00:00Z"), "2027-01-05T15:00:00Z")
        self.assertIsNone(work.name_time("Copy of deck - Ana", "2026-09-20T01:00:00Z"))
        self.assertEqual(work.deck_stem("s.a.b — 09-16 11.29 — Copy of ▶ LIVE — Week 04 — Eyewitness (VT) - Ana B.pptx"),
                         "week04eyewitnessvt")

    def test_named_week(self):
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

    def test_added_words(self):
        self.assertEqual(completion.added("[ Type name here ]", "Oak tree"), "oak tree")
        self.assertEqual(completion.added("Producers Count: [ Enter # ]", "Producers Count: 12"), "12")
        self.assertEqual(completion.added("↑ ↓ ?", "↑"), "chose ↑")
        self.assertEqual(completion.added("Your answer", "Your answer"), "")

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

    def test_latest_row_per_student_wins_and_other_forms_are_ignored(self):
        rows = [ledger.HEADER,
                ["2026-09-24 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM,
                 "A2 Forensics", "Rivera", "Ana", "6", "0", "F1", ""],
                ["2026-10-01 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM,
                 "A2 Forensics", "Rivera", "Ana", "8", "10", "F2", ""],
                ["2026-10-01 10:05", "Feedback made", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM,
                 "A2 Forensics", "Rivera", "Ana", "8", "10", "F2", ""],
                ["2026-10-01 10:00", "Entered in IC", "🔬 Forensics", "Wk 04", "Other", "OTHERFORMID12345",
                 "A2 Forensics", "Okafor", "Chidi", "9", "9", "F3", ""]]
        with patch.object(google, "files_named_in", return_value=[{"id": "SHEETID"}]), \
                patch.object(google, "sheet_tabs", return_value=("Scores entered", {"Sheet1": 0})), \
                patch.object(google, "sheet_values", return_value=rows):
            out = ledger.read("token", self.CONFIG, self.FORM)
        key = ledger.student_key("A2 Forensics", "Rivera", "Ana")
        self.assertEqual((out["configured"], out["entered"][key]["notes"], out["entered"][key]["revision"]),
                         (True, "8", "10"))
        self.assertIn(key, out["feedback"])
        self.assertEqual(len(out["entered"]), 1)

    def test_no_sheet_yet_says_how_to_make_one(self):
        with patch.object(google, "files_named_in", return_value=[]):
            out = ledger.read("token", self.CONFIG, self.FORM)
        self.assertFalse(out["configured"])
        self.assertIn("Scores entered", out["setup"])

    def test_record_writes_a_header_once_and_one_row_per_entry(self):
        entry = {"section": "A2 Forensics", "last": "Rivera", "first": "Ana", "notes": "8", "revision": "10",
                 "file_id": "F2", "file_updated": "2026-10-01T00:00:00Z"}
        context = {"class": "🔬 Forensics", "week": "Wk 04", "assignment": "Reasoning", "form_id": self.FORM}
        with patch.object(google, "files_named_in", return_value=[{"id": "SHEETID"}]), \
                patch.object(google, "sheet_tabs", return_value=("Scores entered", {"Sheet1": 0})), \
                patch.object(google, "sheet_values", return_value=[]), \
                patch.object(google, "append_rows") as append:
            ledger.record("token", "write", self.CONFIG, "ic", context, [entry])
        written = append.call_args.args[3]
        self.assertEqual(written[0], ledger.HEADER)
        self.assertEqual(written[1][1:], ["Entered in IC", "🔬 Forensics", "Wk 04", "Reasoning", self.FORM,
                                          "A2 Forensics", "Rivera", "Ana", "8", "10", "F2", "2026-10-01T00:00:00Z"])
        self.assertEqual(append.call_args.args[0], "write")  # only the write token writes

    def test_score_text_matches_the_page(self):
        self.assertEqual([ledger.score_text(v) for v in (None, 8.0, 6.5, 10.0)], ["N/A", "8", "6.5", "10"])

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
    def test_recording_needs_the_dashboard_page_and_recomputes_scores(self):
        body = json.dumps({"class": "forensics", "week": "Wk 04", "form": "FICTIONALFORMID1234567890abc",
                           "kind": "ic", "files": ["STUDENTDECK123"]}).encode()

        def post(origin):
            environ = {"PATH_INFO": "/v2/api/record", "REQUEST_METHOD": "POST", "QUERY_STRING": "",
                       "HTTP_X_GOOG_IAP_JWT_ASSERTION": "signed", "CONTENT_TYPE": "application/json",
                       "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "HTTP_ORIGIN": origin}
            seen = {}
            config = {"HUB_STATE_PROBE_ORIGINS": "https://hub.fictional.example", "HUB_AGENDA_SPREADSHEET_ID": "X"}
            row = {"matched": True, "last": "Rivera", "first": "Ana", "section": "A2 Forensics",
                   "files": [{"id": "STUDENTDECK123", "mime": completion.SLIDES}], "earlier": []}
            scored = [(row, {"file_id": "STUDENTDECK123", "notes_score": 8.0, "revision_score": None,
                             "file_updated": "t", "problem": None})]
            with patch.object(google, "verify_teacher", return_value="teacher@fictional.example"), \
                    patch.object(google, "service_token", return_value="token"), \
                    patch.object(app, "load_agenda", return_value=fixture()), \
                    patch.object(app, "_form_rows", return_value=({"class": "c", "week": "w", "assignment": "a",
                                                                  "form_id": "f", "date": "d"}, scored)), \
                    patch.object(ledger, "record", return_value=1) as record:
                b"".join(app.application(environ, lambda s, h: seen.setdefault("status", s), config=config))
            return seen["status"], record

        status, record = post("https://elsewhere.example")
        self.assertEqual(status, "403 Forbidden")
        record.assert_not_called()
        status, record = post("https://hub.fictional.example")
        self.assertEqual(status, "200 OK")
        entries = record.call_args.args[5]
        self.assertEqual((entries[0]["notes"], entries[0]["revision"]), ("8", "N/A"))
