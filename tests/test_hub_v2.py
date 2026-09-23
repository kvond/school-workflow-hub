"""Teaching Hub v2 on fictional data only; no Google call is made."""
import io
import json
import unittest
from datetime import date
from unittest.mock import patch

from hub_v2 import agenda, app, google, roster, work

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
                patch.object(google, "drive_file", side_effect=drive_file):
            return work.lesson_work("token", self.config, agenda_fixture or fixture(), "forensics", "Wk 04")

    def test_folder_files_fill_in_work_the_form_missed(self):
        one_form = fixture()
        one_form.classes["forensics"]["cells"][5, 20] = cell("Turn it in", hyperlink=DIRECT)
        result = self.run_work({"FICTIONALFORMID1234567890abc": [
            response("s.ana.rivera@school.example", "2026-09-23T14:00:00Z", ("FILENEW", "Copy of Week 04 deck - Ana Rivera")),
            response("s.ana.rivera@school.example", "2026-09-23T14:05:00Z", ("SHEPPARD", "Copy of Week 01 Sheppard deck - Ana Rivera"))]},
            one_form)
        self.assertEqual(result["folder"]["name"], "📥 Wk 04 — Fictional topic — 2026-09-23")
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
        with patch.object(google, "folder_children", side_effect=lambda t, fid: tree.get(fid, [])):
            folder, note = work.turnin_folder("token", self.config, "Forensics", "Wk 04")
        self.assertIsNone(folder)
        self.assertIn("Wk 04", note)

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
