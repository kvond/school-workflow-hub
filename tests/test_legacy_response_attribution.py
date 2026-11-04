"""Synthetic existing-copy attribution; no school data, network or writes."""
from copy import deepcopy
from itertools import product
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

from hub_v2 import completion as c, combined_assessment as combined, google, native_teacher_source
from test_explicit_response_binding import shape, export, SIZE
from test_native_table_source import native as table_source, element as table_element, read as table_read
from test_anatomy_activity_binding import numeric_activity, SIZE as NUMERIC_SIZE

FILE = "SyntheticSubmittedFile000001"
TEACHER = "SyntheticTeacherFile0000001"
FORM = "SyntheticAssignmentForm0001"
WORDS = "Synthetic original explanation about a comparison and its observed relationship."


def unit(key, text, y, *, x=100000, width=2000000, height=300000):
    return {"key": key, "text": text, "box": (x, y, width, height)}


def source():
    return {"size": SIZE, "slides": [
        {"number": 1, "units": [unit("inventory", "[[MARKER-INVENTORY:C17]] NOTES=2 DRAFT=0 OPTIONAL=0 BANK_TERMS=1 "
            "NOTES_IDS=N1 DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=7. synthetic term", 100000)]},
        {"number": 2, "units": [unit("title", "Synthetic printed title", 100000),
            unit("label", "Question [[NOTES:C17:N1]]", 700000), unit("answer", "", 1100000),
            unit("instruction", "Compare observations before explaining the relationship.", 1800000)]},
        {"number": 3, "units": [unit("bank-title", "Synthetic bank heading", 100000),
            unit("bank-label", "7. synthetic term [[BANK:C17:7. synthetic term]]", 700000),
            unit("bank-answer", "", 1100000)]}]}


def field(value, key):
    return next(u for p in value["slides"] for u in p["units"] if u["key"] == key)


def parse(value, form):
    raw = {"presentationId": TEACHER,
        "pageSize": {"width": {"magnitude": value["size"][0], "unit": "EMU"},
                     "height": {"magnitude": value["size"][1], "unit": "EMU"}},
        "slides": [{"objectId": "page" + str(p["number"]), "pageElements": [
            shape(u["key"], u["text"], u["box"]) for u in p["units"]]} for p in value["slides"]]}
    return (native_teacher_source.read(raw, presentation_id=TEACHER, revision="1") if form == "native"
            else c.read_deck(export(raw)))


def parse_table(raw, form):
    parsed = table_read(raw)
    if form == "native": return parsed
    P, A = c.P, c.A
    rows = table_element(raw)["table"]["tableRows"]
    cols = len(rows[0]["tableCells"])
    units = parsed["slides"][0]["units"]
    root = E.Element(P + "sld")
    tree = E.SubElement(E.SubElement(root, P + "cSld"), P + "spTree")
    frame = E.SubElement(tree, P + "graphicFrame")
    nv = E.SubElement(frame, P + "nvGraphicFramePr")
    E.SubElement(nv, P + "cNvPr", name="export-table", id="2")
    transform = E.SubElement(frame, P + "xfrm")
    E.SubElement(transform, A + "off", x=str(int(units[0]["box"][0])), y=str(int(units[0]["box"][1])))
    E.SubElement(transform, A + "ext", cx=str(int(sum(u["box"][2] for u in units[:cols]))),
                 cy=str(int(sum(units[r * cols]["box"][3] for r in range(len(rows))))))
    table = E.SubElement(E.SubElement(E.SubElement(frame, A + "graphic"), A + "graphicData"), A + "tbl")
    grid = E.SubElement(table, A + "tblGrid")
    for u in units[:cols]: E.SubElement(grid, A + "gridCol", w=str(int(u["box"][2])))
    for r in range(len(rows)):
        row = E.SubElement(table, A + "tr", h=str(int(units[r * cols]["box"][3])))
        for col in range(cols):
            body = E.SubElement(E.SubElement(row, A + "tc"), A + "txBody")
            for line in units[r * cols + col]["text"].split("\n"):
                E.SubElement(E.SubElement(E.SubElement(body, A + "p"), A + "r"), A + "t").text = line
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        pres = E.Element(P + "presentation")
        E.SubElement(pres, P + "sldSz", cx=str(parsed["size"][0]), cy=str(parsed["size"][1]))
        z.writestr("ppt/presentation.xml", E.tostring(pres))
        z.writestr("ppt/slides/slide1.xml", E.tostring(root))
    return c.read_deck(out.getvalue())


class LegacyAttributionTests(unittest.TestCase):
    def assess(self, before, after, teacher="native", student="pptx"):
        return c.assess(parse(after, student), parse(before, teacher), found_codes_only=True)

    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_four_format_blank_positive_and_every_source_context_negative(self):
        before = source()
        for teacher, student in product(("native", "pptx"), repeat=2):
            with self.subTest(teacher=teacher, student=student):
                self.assertEqual(self.assess(before, before, teacher, student)["notes_score"], 0)
                for key, identity in (("answer", "N1"), ("bank-answer", "7. synthetic term")):
                    after = deepcopy(before); field(after, key)["text"] = WORDS
                    result = self.assess(before, after, teacher, student)
                    self.assertEqual(result["notes_score"], 5)
                    self.assertEqual([(i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"], [(identity, WORDS)])
                for key in ("title", "label", "instruction", "bank-title", "bank-label"):
                    after = deepcopy(before); field(after, key)["text"] += "\n" + WORDS
                    self.held(self.assess(before, after, teacher, student))

    def test_source_role_cannot_be_created_by_rewriting_renaming_or_moving_context(self):
        for teacher, student in product(("native", "pptx"), repeat=2):
            for text in ("Type here.", "[[NOTES:C17:N1]] " + WORDS, "7. synthetic term", WORDS):
                before = source(); after = deepcopy(before)
                field(after, "title").update(text=text, key="renamed-answer")
                self.held(self.assess(before, after, teacher, student))
            before = source(); after = deepcopy(before)
            heading = field(after, "title")
            heading.update(text=WORDS, box=field(after, "answer")["box"])
            after["slides"][1]["units"].remove(field(after, "answer"))
            self.held(self.assess(before, after, teacher, student))

    def test_unknown_duplicate_or_overlapping_response_geometry_holds_without_partial_credit(self):
        for mode in ("unknown", "duplicate", "original_duplicate", "missing_context"):
            before = source(); after = deepcopy(before)
            field(after, "answer")["text"] = WORDS
            if mode == "unknown": field(after, "answer")["box"] = (4000000, 4000000, 1000000, 500000)
            if mode == "duplicate": after["slides"][1]["units"].append({**field(after, "answer"), "key": "duplicate"})
            if mode == "original_duplicate": before["slides"][1]["units"].append({**field(before, "answer"), "key": "duplicate"})
            if mode == "missing_context": after["slides"][1]["units"].remove(field(after, "instruction"))
            self.held(self.assess(before, after))

    def test_complete_original_fields_preserve_isolated_second_answer_and_alias_changes(self):
        before = source(); before["slides"] = before["slides"][1:2]
        before["slides"][0]["units"] += [unit("label2", "Question [[NOTES:C17:N2]]", 2500000), unit("answer2", "", 2900000)]
        for teacher, student in product(("native", "pptx"), repeat=2):
            for key, identity in (("answer", "N1"), ("answer2", "N2")):
                after = deepcopy(before); field(after, key)["text"] = WORDS
                for index, u in enumerate(after["slides"][0]["units"]): u["key"] = "renamed" + str(index)
                result = self.assess(before, after, teacher, student)
                self.assertIsNone(result["note"])
                self.assertEqual([(i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"], [(identity, WORDS)])
        before["slides"][0]["units"].reverse()
        after = deepcopy(before); field(after, "answer2")["text"] = WORDS
        result = self.assess(before, after)
        self.assertEqual([i["id"] for i in result["items"] if i["status"] == "done"], ["N2"])
        for key in ("answer", "answer2"):
            after = deepcopy(before)
            field(after, "label")["text"] = "Question"
            field(after, key)["text"] = WORDS
            result = self.assess(before, after)
            if key == "answer": self.held(result)
            else:
                self.assertIsNone(result["note"])
                self.assertEqual(result["notes_score"], 10)
                self.assertEqual([i["id"] for i in result["items"] if i["status"] == "done"], ["N2"])

    def test_in_field_full_identity_isolated_and_conflicting_codes_hold(self):
        before = {"size": SIZE, "slides": [{"number": 1, "units": [unit("title", "Synthetic heading", 100000)] + [
            unit("field" + str(i), f"Type here. [[NOTES:C17:N{i}]]", 700000 + i * 500000) for i in range(4)]}]}
        for teacher, student in product(("native", "pptx"), repeat=2):
            for i in range(4):
                after = deepcopy(before); field(after, "field" + str(i))["text"] += "\n" + WORDS
                result = self.assess(before, after, teacher, student)
                self.assertIsNone(result["note"])
                self.assertEqual([x["id"] for x in result["items"] if x["status"] == "done"], ["N" + str(i)])
        for code in ("[[NOTES:C17:N0]]", "[[DRAFT:C17:N1]]", "[[NOTES:C18:N1]]", "[[NOTES:C17:OTHER]]"):
            after = deepcopy(before); field(after, "field1")["text"] = "Type here. " + code + "\n" + WORDS
            self.held(self.assess(before, after))

    def test_native_export_existing_table_cells_and_header_index_controls(self):
        before = table_source()
        rows = table_element(before)["table"]["tableRows"]
        rows.append(deepcopy(rows[-1]))
        table_element(before)["table"]["rows"] += 1
        for r, row in enumerate(rows):
            for col, cell in enumerate(row["tableCells"]):
                cell["location"]["rowIndex"] = r
                value = f"Header {col}" if r == 0 else str(r) if col == 0 else f"Type here. [[NOTES:C17:R{r}C{col}]]"
                cell["text"] = {"textElements": [{"textRun": {"content": value}}]}
        for r, row in enumerate(rows):
            for col, _ in enumerate(row["tableCells"]):
                after = deepcopy(before)
                table_element(after)["table"]["tableRows"][r]["tableCells"][col]["text"]["textElements"][0]["textRun"]["content"] += "\n" + WORDS
                for teacher, student in product(("native", "pptx"), repeat=2):
                    result = c.assess(parse_table(after, student), parse_table(before, teacher), found_codes_only=True)
                    if r and col:
                        self.assertIsNone(result["note"])
                        self.assertEqual([i["id"] for i in result["items"] if i["status"] == "done"], [f"R{r}C{col}"])
                    else: self.held(result)

    def test_same_field_cannot_complete_two_identities(self):
        before = source()
        field(before, "answer")["text"] = "Type here. [[NOTES:C17:N1]] [[NOTES:C17:N2]]"
        after = deepcopy(before); field(after, "answer")["text"] += "\n" + WORDS
        self.held(self.assess(before, after))

    def test_first_revised_labels_and_inline_bank_compatibility(self):
        before = {"size": SIZE, "slides": [{"number": 1, "units": [
            unit("first", "Your first answer [[NOTES:C17:Q]]", 100000), unit("answer", "", 500000),
            unit("revised", "Your revised answer [[DRAFT:C17:Q]]", 1100000), unit("revision", "", 1500000)]}]}
        for teacher, student in product(("native", "pptx"), repeat=2):
            for key, category in (("answer", "NOTES"), ("revision", "DRAFT")):
                after = deepcopy(before); field(after, key)["text"] = WORDS
                result = self.assess(before, after, teacher, student)
                self.assertIsNone(result["note"])
                self.assertEqual([(i["category"], i["id"]) for i in result["items"] if i["status"] == "done"], [(category, "Q")])
            for key in ("first", "revised"):
                after = deepcopy(before); field(after, key)["text"] += "\n" + WORDS
                self.held(self.assess(before, after, teacher, student))
            inline = source(); inline["slides"] = inline["slides"][2:]
            inline["slides"][0]["units"].remove(field(inline, "bank-answer"))
            after = deepcopy(inline); field(after, "bank-label")["text"] += "\n" + WORDS
            result = self.assess(inline, after, teacher, student)
            self.assertIsNone(result["note"])
            self.assertEqual(result["notes_score"], 10)
            self.assertEqual(result["items"][0]["answer"], WORDS)

    def test_ambiguous_order_holds_and_late_failure_discards_all_partial_answers(self):
        before = source(); before["slides"] = before["slides"][1:2]
        field(before, "label")["text"] += " [[NOTES:C17:N2]]"
        after = deepcopy(before); field(after, "answer")["text"] = WORDS
        self.held(self.assess(before, after))
        before = source(); after = deepcopy(before)
        field(after, "answer")["text"] = WORDS
        after["slides"][1]["units"].append(unit("unbound", WORDS, 3000000))
        self.held(self.assess(before, after))

    def test_inline_term_survives_a_different_terms_separate_blank(self):
        before = {"size": SIZE, "slides": [{"number": 1, "units": [
            unit("alpha", "Alpha [[BANK:C17:Alpha]]", 100000),
            unit("beta", "Beta [[BANK:C17:Beta]]", 1100000),
            unit("beta-answer", "", 1100000, x=2500000)]}]}
        for teacher, student in product(("native", "pptx"), repeat=2):
            for key, identity in (("alpha", "Alpha"), ("beta-answer", "Beta")):
                after = deepcopy(before); field(after, key)["text"] += "\n" + WORDS
                result = self.assess(before, after, teacher, student)
                self.assertIsNone(result["note"])
                self.assertEqual([(i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"], [(identity, WORDS)])
            after = deepcopy(before); field(after, "beta")["text"] += "\n" + WORDS
            self.held(self.assess(before, after, teacher, student))

    def test_tied_separate_bank_labels_remain_context(self):
        before = {"size": SIZE, "slides": [{"number": 1, "units": [
            unit("left", "Alpha [[BANK:C17:Alpha]]", 100000, x=100000),
            unit("right", "Beta [[BANK:C17:Beta]]", 100000, x=4100000),
            unit("answer", "", 100000, x=2600000, width=1000000)]}]}
        after = deepcopy(before); field(after, "left")["text"] += "\n" + WORDS
        self.held(self.assess(before, after))

    def test_score_file_found_code_policy_does_not_waive_attribution_or_assignment_hold(self):
        meta = {"id": FILE, "name": "Synthetic submission", "mimeType": c.SLIDES, "trashed": False,
                "version": "1", "modifiedTime": "2026-11-04T19:00:00Z"}
        before = source(); teacher = parse(before, "native")
        self.assertEqual(c.readiness(teacher)["status"], "NOT_READY")
        for case in ("blank", "answer", "title", "instruction", "bank-title"):
            after = deepcopy(before)
            if case != "blank": field(after, case)["text"] += "\n" + WORDS
            for held in (False, True):
                ready = {"status": "NOT_READY", "label": "Needs check", "problem": "Synthetic assignment hold"} if held else {"status": "READY"}
                form = {"form_id": FORM, "label": "Synthetic lesson", "grade_readiness": ready,
                        "students": [{"files": [{"id": FILE, "mime": c.SLIDES, "name": "Synthetic submission"}]}]}
                lesson = {"topic": "Synthetic lesson", "forms": [form], "materials": [
                    {"url": "https://docs.google.com/presentation/d/" + TEACHER + "/edit"}]}
                agenda = SimpleNamespace(classes={"anatomy": {"name": "Synthetic anatomy"}}, lessons=lambda *a: [lesson])
                with patch.dict(c._results, {}, clear=True), patch.object(google, "drive_file", return_value=meta), \
                     patch.object(c, "_read", return_value=parse(after, "pptx")), \
                     patch.object(c, "_teacher_template", return_value=({**meta, "id": TEACHER}, teacher)), \
                     patch.object(c, "assess", wraps=c.assess) as spy:
                    result = c.score_file(None, agenda, "anatomy", "W03", FORM, FILE, {"lessons": [lesson]})
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready")
                    self.assertIsNone(result.get("notes_score"))
                elif case == "blank": self.assertEqual(result["notes_score"], 0)
                elif case == "answer": self.assertEqual(result["notes_score"], 5)
                else: self.held(result)

    def test_combined_assessor_cannot_bypass_shared_attribution_guard(self):
        meta = {"id": TEACHER, "name": "Synthetic teacher", "mimeType": c.SLIDES,
                "version": "1", "modifiedTime": "unchanged"}
        before = source(); after = deepcopy(before); field(after, "title")["text"] += WORDS
        teacher = {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, "native")}
        student = {"metadata": {**meta, "id": FILE}, "sha256": "b" * 64, "deck": parse(after, "pptx")}
        declaration = {"components": [{"cycle": "C17", "label": "Synthetic component", "teacher_id": TEACHER}]}
        form = {"combined_assessment": {"scope": declaration}}
        row = {"combined_components": [{"cycle": "C17", "file_id": FILE}]}
        with patch.object(combined, "verify_sources", return_value=({"C17": teacher}, {"C17": student})), \
             patch.object(c, "assess", wraps=c.assess) as spy:
            result = combined.score_row(None, form, row)
        self.assertEqual(spy.call_count, 1)
        self.held(result)

    def test_mixed_numeric_validator_does_not_exempt_ordinary_source_prose(self):
        for measured in (False, True):
            for key in ("blank", "explain", "explanation"):
                teacher, student = numeric_activity()
                if measured: student["units"][0]["text"] = student["units"][0]["text"].replace("____", "72", 1)
                if key != "blank": next(u for u in student["units"] if u["key"] == key)["text"] += "\n" + WORDS
                result = c.assess({"slides": [student], "size": NUMERIC_SIZE}, {"slides": [teacher], "size": NUMERIC_SIZE})
                if key == "explain": self.held(result)
                else:
                    self.assertIsNone(result["note"])
                    self.assertEqual(result["counts"]["NOTES"]["done"], int(measured) + int(key == "explanation"))
                    if key == "explanation": self.assertEqual(next(i for i in result["items"] if i["id"] == "EXPLAIN")["answer"], WORDS)


if __name__ == "__main__":
    unittest.main()
