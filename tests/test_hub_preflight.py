"""Next-week preflight on fictional data only; no Google call is made."""
import io
import unittest
from datetime import date
from unittest.mock import patch

from hub_v2 import agenda, app, preflight

DECK_ID = "FICTIONALDECK1234567890"
NEXT_DECK = "FICTIONALNEXTDECK123456"
FOLDER_ID = "FICTIONALFOLDER1234567890"
FORM_ID = "FICTIONALFORMID1234567890abc"
SHEET_DOC = "FICTIONALDOCSHEET1234567890"
LAB_DOC = "FICTIONALLABDOC1234567890"
GUIDE_DOC = "FICTIONALGUIDEDOC1234567890"
DECK = "https://docs.google.com/presentation/d/%s/edit" % DECK_ID
NEXT = "https://docs.google.com/presentation/d/%s/edit" % NEXT_DECK
FOLDER = "https://drive.google.com/drive/folders/%s" % FOLDER_ID
FORM = "https://docs.google.com/forms/d/%s/viewform" % FORM_ID


def doc(file_id):
    return "https://docs.google.com/document/d/%s/edit" % file_id


def cell(value="", **extra):
    return {"formattedValue": value, **extra}


def grid(rows):
    return {(r, c): v if isinstance(v, dict) else cell(v)
            for r, values in rows.items() for c, v in enumerate(values, 1) if v != ""}


def slide(number, text, links=(), skipped=False):
    return {"number": number, "object_id": "obj%d" % number, "text": text, "links": list(links),
            "skipped": skipped}


def good_slides():
    """A fictional three-day deck in the teacher's format: two critical aspects, each with the
    full Variation Theory sequence, then a revision day that points back at the answer slides."""
    return [
        slide(1, "TEACHER REFERENCE — not projected to students\nCycle 9 · Fictional Energy\n"
                 "TO PRINT\nFictional Energy — CA1 worksheet · 1 per student\n"
                 "Fictional Energy — CA2 worksheet · 1 per student\nLab sheet — Gas Balloons\n"
                 "CRITICAL ASPECTS\n1. Storing energy\n2. Releasing energy\n"
                 "[[MARKER-INVENTORY:C09]] NOTES=6 DRAFT=2 OPTIONAL=0 BANK_TERMS=0",
              links=[("Fictional Energy — CA1 worksheet", doc(SHEET_DOC)),
                     ("Fictional Energy — CA2 worksheet", doc(GUIDE_DOC)),
                     ("Lab sheet — Gas Balloons", doc(LAB_DOC))]),
        slide(2, "TEACHER REFERENCE — not projected to students\nTeacher Prep\nBUY AHEAD\n"
                 "• Marshmallows and a lighter — flame demo (Day 1)\n"
                 "• Baking soda, vinegar, and balloons — gas balloon lab (Day 2)\n"
                 "PUT OUT FOR STUDENTS\n• Safety glasses and paper towels"),
        slide(3, "Day 1 of 3 — the most teaching\nToday on Tue 9/22 we start."),
        slide(4, "Critical aspect: Storing energy\nCASE A — a battery. CASE B — a sugar cube.\n"
                 "Why are they different? [[NOTES:C09:CA1-CONTRAST]]"),
        slide(5, "Critical aspect: Storing energy\nFinish this sentence as a rule: [[NOTES:C09:CA1-RULE]]"),
        slide(6, "Pattern break / Critical aspect: Storing energy\nBut what about fat? [[NOTES:C09:CA1-PATTERNBREAK]]"),
        slide(7, "Critical aspect: Storing energy\nGetting Started · Working On It · Mastery\n"
                 "[[NOTES:C09:CA1-MASTERY]] [[DRAFT:C09:CA1-MASTERY]]"),
        slide(8, "Day 2 of 3 — the rest of teaching"),
        slide(9, "Critical aspect: Releasing energy\nCASE A burning. CASE B a cell. Same fuel, different?"
                 " [[NOTES:C09:CA2-CONTRAST]]"),
        slide(10, "Critical aspect: Releasing energy\nFinish this sentence as a rule: [[NOTES:C09:CA2-RULE]]"),
        slide(11, "Pattern break / Critical aspect: Releasing energy\nA fever? [[NOTES:C09:CA2-PATTERNBREAK]]"),
        slide(12, "Critical aspect: Releasing energy\nMastery [[NOTES:C09:CA2-MASTERY]] [[DRAFT:C09:CA2-MASTERY]]"),
        slide(13, "Day 2 activity\nActivity — Gas Balloon Lab\nMix baking soda and vinegar; stretch a balloon "
                  "over the bottle.\nOpen: Lab sheet — Gas Balloons",
              links=[("Open: Lab sheet — Gas Balloons", doc(LAB_DOC))]),
        slide(14, "DAY 3 OF 3 — MAKE IT STRONGER\nGo back to your answers and revise them\n"
                  "Slide 7 — Critical Aspect 1 · Storing energy\nSlide 12 — Critical Aspect 2 · Releasing energy"),
        slide(15, "Activity and resource links\nFictional Energy — CA1 worksheet\n" + doc(SHEET_DOC)),
    ]


class FakeSources:
    def __init__(self, decks=None, files=None, children=None, forms=None, pdfs=None):
        self.decks = decks if decks is not None else {DECK_ID: {"id": DECK_ID, "title": "Cycle 09 — Fictional Energy",
                                                                "slides": good_slides()}}
        self.files = files if files is not None else {FOLDER_ID: {"id": FOLDER_ID, "name": "Cycle 09 — Fictional Energy",
                                                                  "mimeType": "application/vnd.google-apps.folder"}}
        self.children_map = children or {}
        self.forms = forms if forms is not None else {FORM_ID: "Biology — Cycle 09 — Fictional Energy — Turn In"}
        self.pdfs = pdfs or {}
        self.calls = []

    def deck(self, deck_id):
        self.calls.append(("deck", deck_id))
        if deck_id not in self.decks:
            raise preflight.SourceError("The deck can't be opened: it was deleted, or it isn't shared with the Hub.")
        return self.decks[deck_id]

    def file(self, file_id):
        if file_id not in self.files:
            raise preflight.SourceError("A linked file can't be opened.")
        return self.files[file_id]

    def children(self, folder_id):
        return self.children_map.get(folder_id, [])

    def form(self, form_id):
        if form_id not in self.forms:
            raise preflight.SourceError("The Turn-in Form can't be opened.")
        return {"title": self.forms[form_id], "responder_url": "https://docs.google.com/forms/d/e/x/viewform"}

    def pdf(self, file_id, mime):
        return self.pdfs[file_id]


def bio_tab(rows):
    headers = ["Wk", "Dates & closures", "Topic", "Mon — plan", "📝 Mon note", "Wed — plan", "📝 Wed note",
               "Fri — plan", "📝 Fri note", "✅ To-do", "🧪 Lab / prep", "🎞 Student deck", "👩‍🏫 Teacher deck",
               "📁 Folder", "🧪 Activity / print set", "_studentURL", "_teacherURL", "_folderURL", "_extraURL",
               "📤 Turn it in", "_turnInURL"]
    out = {1: ["BIOLOGY"], 3: headers}
    for i, row in enumerate(rows, 4):
        out[i] = row
    return grid(out)


def bio_row(week, topic, mon="", wed="", fri="", prep="", deck=DECK, form=FORM, folder=FOLDER):
    return [week, "", topic, mon, "", wed, "", fri, "", "", prep, "", "", "", "",
            deck.replace("/edit", "/copy") if deck else "", deck, folder, "", "", form]


def weeks_tab():
    return grid({1: ["ENGINE"], 2: ["takes over on", "course row", "week", "Monday", "dates & closures"],
                 3: ["", "6", "Wk 03", "09/14/2026", "Sep 14 – Sep 18\nFri 09/18 = B Friday → A&P"],
                 4: ["", "7", "Wk 04", "09/21/2026", "Sep 21 – Sep 25\n⚠ Mon 09/21 Schools Closed\n"
                                                          "Fri 09/25 = A Friday → Bio + Forensics"],
                 5: ["", "8", "Wk 05", "09/28/2026", "Sep 28 – Oct 02\nFri 10/02 = B Friday → A&P"]})


def worksheets_tab(rows):
    return grid({1: ["Course", "Wk", "Topic", "Document", "Type", "Link", "_fileID"], **{
        i: row for i, row in enumerate(rows, 2)}})


def make_agenda(bio_rows, worksheet_rows=()):
    tabs = {"🗂 Weeks": 1, "🌿 Biology": 2, "📝 Worksheets": 3}
    grids = {"🗂 Weeks": weeks_tab(), "🌿 Biology": bio_tab(bio_rows),
             "📝 Worksheets": worksheets_tab(worksheet_rows)}
    return agenda.Agenda("FICTIONALSHEET", "Fictional Agenda", tabs, grids)


def standard_agenda(**overrides):
    rows = {
        "w3": bio_row("Wk 03", "Fictional Energy", mon="9/14\nEnergy lesson · flame demo",
                      wed="9/16\nComparison write-up"),
        "w4": bio_row("Wk 04", "Fictional Energy (cont.)",
                      wed="9/23\nGas balloon lab with baking soda and vinegar\n📝 DRAFT.Fictional Energy due 9/23",
                      fri="9/25 — B Friday — A&P has it. Biology does not meet.",
                      prep="🧪 LAB Gas balloons · BUY: baking soda, vinegar, balloons · PULL: Trays · "
                           "✔ on order — balloons; 🛒 vinegar"),
        "w5": bio_row("Wk 05", "Next Topic", mon="9/28\nNext lesson", deck=NEXT),
    }
    rows.update(overrides)
    return make_agenda(list(rows.values()), [["Biology", "Wk 04", "Fictional Energy", "Gas Balloons lab sheet",
                                              "Lab (week sheet)", "", ""]])


def texts(result, level=None):
    return [e["text"] for e in result["exceptions"] if level is None or e["level"] == level]


class ScheduleTests(unittest.TestCase):
    def test_closures_and_ab_fridays_decide_who_meets(self):
        week = {"label": "Wk 04", "monday": "2026-09-21",
                "closures": "⚠ Mon 09/21 Schools Closed\nFri 09/25 = A Friday → Bio + Forensics"}
        bio = preflight.meetings(week, ["Mon", "Wed", "Fri"])
        self.assertEqual([m["meets"] for m in bio], [False, True, True])
        anatomy = preflight.meetings(week, ["Tue", "Thu", "Fri"])
        self.assertEqual([m["meets"] for m in anatomy], [True, True, False])

    def test_reopen_line_and_whole_week_closures(self):
        monday = date(2027, 1, 4)
        self.assertEqual(preflight.closed_days(monday, "⚠ Winter break · schools reopen Mon 01/04"), {})
        self.assertEqual(len(preflight.closed_days(monday, "⚠ CLOSED ALL WEEK")), 5)
        only = preflight.closed_days(date(2026, 12, 21), "⚠ Mon–Tue only")
        self.assertEqual(sorted(d.day for d in only), [23, 24, 25])


class DeckTests(unittest.TestCase):
    def info(self, slides=None):
        return preflight.read_deck({"id": DECK_ID, "title": "Cycle 09", "slides": slides or good_slides()})

    def test_good_deck_keeps_its_variation_theory_structure(self):
        info = self.info()
        self.assertEqual(info["aspects"], ["Storing energy", "Releasing energy"])
        self.assertEqual([s["label"] for s in info["segments"]], [1, 2, 3])
        self.assertTrue(info["segments"][2]["revision"])
        self.assertEqual(preflight.vt_check(info), [])

    def test_missing_and_reordered_phases_are_reported(self):
        slides = good_slides()
        slides[5] = slide(6, "Critical aspect: Storing energy\nJust a reading.")
        slides[3], slides[4] = (slide(4, slides[4]["text"]), slide(5, slides[3]["text"]))
        found = " ".join(t for _, t, _ in preflight.vt_check(self.info(slides)))
        self.assertIn("“Storing energy” is missing its pattern break", found)
        self.assertIn("build-a-rule (slide 4) now comes before the contrast set (slide 5)", found)

    def test_revision_steps_must_point_at_revision_boxes(self):
        slides = good_slides()
        slides[13] = slide(14, slides[13]["text"].replace("Slide 7 —", "Slide 6 —"))
        found = [t for _, t, _ in preflight.vt_check(self.info(slides))]
        self.assertTrue(any("sends students to slide 6" in t and "now slide 7" in t for t in found))

    def test_links_that_open_the_wrong_file(self):
        slides = good_slides()
        slides[12] = slide(13, "Open: Science sheet", links=[("Open: Science sheet", doc(SHEET_DOC))])
        slides.append(slide(16, "Links", links=[("https://drive.google.com/file/d/%s/view" % SHEET_DOC,
                                                   "https://drive.google.com/file/d/OTHERFILE1234567890abc/view")]))
        found = [t for _, t, _ in preflight.link_check(self.info(slides))]
        self.assertTrue(any("open the same file" in t for t in found))
        self.assertTrue(any("shows one file's address" in t for t in found))

    def test_author_notes_are_not_marker_codes(self):
        slides = good_slides() + [slide(16, "Case\n[ image: thin cells ] and [DRAFT — confirm the exam]")]
        notes = preflight.author_notes(self.info(slides))
        self.assertEqual([n for n, _ in notes], [16, 16])

    def test_presentation_slides_reads_the_slides_api(self):
        pres = {"slides": [{"objectId": "p1", "slideProperties": {"isSkipped": True}, "pageElements": [
            {"shape": {"text": {"textElements": [{"textRun": {"content": "Open: Sheet\n",
                                                              "style": {"link": {"url": doc(SHEET_DOC)}}}}]}}},
            {"elementGroup": {"children": [{"shape": {"text": {"textElements": [{"textRun": {"content": "Grouped"}}]}}}]}},
            {"table": {"tableRows": [{"tableCells": [{"text": {"textElements": [{"textRun": {"content": "Cell"}}]}}]}]}}]}]}
        parsed = preflight.presentation_slides(pres)
        self.assertEqual(parsed[0]["text"], "Open: Sheet\nGrouped\nCell")
        self.assertEqual(parsed[0]["links"], [("Open: Sheet", doc(SHEET_DOC))])
        self.assertTrue(parsed[0]["skipped"])

    def test_supply_lines_merge_agenda_status_with_the_deck(self):
        info = self.info()
        rows = preflight.supply_lines("BUY: baking soda, vinegar, balloons · PULL: Trays · "
                                      "✔ on order — balloons; 🛒 vinegar", [info], {DECK_ID: [1]})
        kinds = {r["item"]: r["kind"] for r in rows}
        self.assertEqual(kinds["balloons"], "on order")
        self.assertEqual(kinds["vinegar"], "buy")
        self.assertEqual(kinds["Trays"], "pull")
        self.assertEqual(kinds["Safety glasses"], "pull")
        self.assertNotIn("Marshmallows", kinds)  # the Day 1 demo isn't this week's

    def test_split_items_respects_parentheses_and_or(self):
        items, purpose = preflight._split_items(
            "Water (left out overnight, or treated), a skewer, or tongs, and cups — demo")
        self.assertEqual(items, ["Water (left out overnight, or treated)", "a skewer, or tongs", "cups"])
        self.assertEqual(purpose, "demo")


class RunTests(unittest.TestCase):
    def run_week(self, agenda_obj=None, sources=None, sizes=None):
        return preflight.run(agenda_obj or standard_agenda(), "Wk 04", sources or FakeSources(),
                             sizes or {"biology": [24, 26]})

    def test_a_real_shaped_week_reports_only_what_needs_the_teacher(self):
        result = self.run_week()
        fixes = texts(result, "fix")
        self.assertIn("Biology meets (A Friday), but the plan says it doesn't.", fixes)
        self.assertTrue(any("DRAFT.Fictional Energy (revision) is due Wed 9/23" in t for t in fixes))
        self.assertTrue(any("Student slides still give dates" in t and "Tue 9/22" in t for t in fixes))
        self.assertTrue(any("Gas Balloons lab sheet" in t and "no document" in t for t in fixes))
        self.assertEqual(result["checked"], {"lessons": 1, "decks": 1, "links": 2})

    def test_the_wednesday_lab_maps_to_deck_day_two(self):
        result = self.run_week()
        wed = next(m for m in result["meetings"] if m["day"] == "Wed 9/23")
        self.assertEqual(wed["deck_day"], 2)
        self.assertIn("balloon", wed["matched_by"])

    def test_print_list_is_this_weeks_only_with_copies(self):
        result = self.run_week()
        names = {p["name"]: p for p in result["print"]}
        self.assertNotIn("Fictional Energy — CA1 worksheet", names)  # taught last week
        self.assertEqual(names["Fictional Energy — CA2 worksheet"]["copies"], "8 support copies")
        self.assertEqual(names["Lab sheet — Gas Balloons"]["copies"], "52 (1 per student + 2 spare)")
        self.assertEqual(names["Lab sheet — Gas Balloons"]["day"], "Wed 9/23")
        digital = {d["name"]: d for d in result["digital"]}
        self.assertEqual(digital["Fictional Energy — CA2 worksheet"]["kind"], "in deck")

    def test_supplies_and_schoology_links(self):
        result = self.run_week()
        kinds = {r["item"]: r["kind"] for r in result["supplies"]}
        self.assertEqual((kinds["balloons"], kinds["vinegar"], kinds["Trays"]), ("on order", "buy", "pull"))
        self.assertEqual({s["what"] for s in result["schoology"]}, {"Turn-in Form", "Student deck (make a copy)"})

    def test_unreadable_deck_and_folder_are_fixes_not_crashes(self):
        result = self.run_week(sources=FakeSources(decks={}, files={}))
        fixes = texts(result, "fix")
        self.assertTrue(any("The deck can't be opened" in t for t in fixes))
        self.assertTrue(any("lesson folder link is broken" in t for t in fixes))

    def test_empty_meeting_day_names_the_next_deck_day(self):
        result = self.run_week(standard_agenda(w4=bio_row("Wk 04", "Fictional Energy (cont.)")))
        wed = next(e for e in result["exceptions"] if e["day"] == "Wed 9/23")
        self.assertEqual(wed["text"], "Biology meets, but there is no plan.")
        self.assertIn("Next in the deck: day 3", wed["action"])

    def test_week_labels_and_next_week(self):
        agenda_obj = standard_agenda()
        self.assertEqual(preflight.next_week(agenda_obj, date(2026, 9, 17))["label"], "Wk 04")
        self.assertEqual(preflight._week_key("Wk 13 — closed all week"), "wk13")
        with self.assertRaises(ValueError):
            preflight.run(agenda_obj, "Wk 40", FakeSources())

    def test_worksheets_tab_is_read_by_header(self):
        rows = standard_agenda().worksheets
        self.assertEqual(rows[0]["document"], "Gas Balloons lab sheet")
        self.assertEqual(rows[0]["file_id"], "")


class PacketTests(unittest.TestCase):
    def test_packet_has_a_cover_and_the_printable_docs(self):
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas
        page = io.BytesIO()
        sheet = canvas.Canvas(page)
        sheet.drawString(72, 720, "Lab sheet")
        sheet.save()
        sources = FakeSources(files={
            FOLDER_ID: {"id": FOLDER_ID, "name": "Cycle 09", "mimeType": "application/vnd.google-apps.folder"},
            LAB_DOC: {"id": LAB_DOC, "name": "Lab", "mimeType": "application/vnd.google-apps.document"}},
            pdfs={LAB_DOC: page.getvalue()})
        result = preflight.run(standard_agenda(), "Wk 04", sources, {"biology": [24, 26]})
        raw, problems = preflight.packet(result, sources)
        reader = PdfReader(io.BytesIO(raw))
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertIn("Print / lab packet", reader.pages[0].extract_text())


class RouteTests(unittest.TestCase):
    def call(self, path, query=""):
        captured = {}

        def start(status, headers):
            captured["status"], captured["headers"] = status, dict(headers)
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query}
        with patch.object(app.google, "verify_teacher", return_value=None), \
                patch.object(app.google, "service_token", return_value="token"), \
                patch.object(app, "load_agenda", return_value=standard_agenda()), \
                patch.object(preflight.roster, "load", return_value=({}, [])), \
                patch.object(preflight, "GoogleSources", return_value=FakeSources()):
            body = b"".join(app.application(environ, start, {}))
        return captured["status"], captured["headers"], body

    def test_preflight_route_and_query_guard(self):
        import json
        status, _, body = self.call("/v2/api/preflight", "week=Wk+04")
        self.assertEqual(status, "200 OK")
        data = json.loads(body)
        self.assertEqual(data["week"], "Wk 04")
        self.assertIn("Wk 05", data["weeks"])
        self.assertEqual(self.call("/v2/api/preflight", "class=biology")[0], "400 Bad Request")

    def test_packet_route_returns_a_pdf(self):
        status, headers, body = self.call("/v2/api/preflight/packet", "week=Wk+04")
        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(body.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
