"""Source-bound numeric records and raster cards cannot invent student writing."""
from copy import deepcopy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
import zipfile

from hub_v2 import completion, response_binding


SIZE = (1000, 750)


def unit(key, text, box, **extra):
    return {"key": key, "text": text, "box": box, "title": False, **extra}


def assess(student, teacher):
    return completion.assess({"slides": [student], "size": SIZE}, {"slides": [teacher], "size": SIZE})


def numeric_activity():
    text = ("1. Count for 30 seconds, then double it. Write it down: ____ bpm.\n"
            "2. Do 30 jumping jacks.\n3. Count again. Write it down: ____ bpm.\n"
            "4. Wait 2 minutes. Count one more time: ____ bpm.")
    box = (20, 100, 900, 200)
    teacher = {"number": 1, "units": [unit("printed-records", text, box),
        unit("explain", "Explain what the measurements show.\n[[NOTES:C09:EXPLAIN]]", (20, 450, 900, 30)),
        unit("explanation", "", (20, 510, 900, 150)),
        unit("records-code", "[[NOTES:C09:RECORDS]]", (400, 200, 1, 1), binding={
            "kind": "numeric-record-group", "source_text": text, "source_box": box, "box": box,
            "placeholder": "____", "fields": 3, "unit": "bpm"})]}
    return teacher, deepcopy(teacher)


def raster_activity():
    background = {"sha256": hashlib.sha256(b"authentic original numbered graph").hexdigest(),
                  "appearance": {"crop": {}, "stretch": {}, "tile": None, "effects": []}}
    targets = [{"number": n, "box": (20 + ((n - 1) % 4) * 220, 70 + ((n - 1) // 4) * 130, 160, 90)}
               for n in range(1, 16) if n != 4]
    cards = [{"text": f"Original card {n}", "box": (20 + n * 65, 660, 55, 30)} for n in range(14)]
    given = [{"text": "Already supplied example", "box": (700, 100, 100, 30)}]
    data = {"kind": "raster-card-activity", "background": background, "page_size": list(SIZE),
            "cards": cards, "targets": targets, "given_cards": given, "given_target": 4}
    teacher = {"number": 1, "background": deepcopy(background), "units": [
        unit(f"tray-{n}", c["text"], c["box"]) for n, c in enumerate(cards)] + [
        unit("given", given[0]["text"], given[0]["box"]),
        unit("activity-code", "[[NOTES:C09:ORGANIZER]]", (1, 1, 1, 1), binding=data)]}
    return teacher, deepcopy(teacher)


def move_card(student, n, target):
    x, y, _, _ = student["units"][-1]["binding"]["targets"][target]["box"]
    student["units"][n]["box"] = (x + 30, y + 20, 55, 30)


class NumericRecordTests(unittest.TestCase):
    def test_printed_numbers_and_unchanged_blanks_are_not_student_measurements(self):
        teacher, student = numeric_activity()
        result = assess(student, teacher)
        records = next(i for i in result["items"] if i["id"] == "RECORDS")
        self.assertEqual(records["status"], "blank")
        self.assertEqual(records["answer"], "")
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 2})

    def test_each_retained_slot_is_independent_real_numeric_evidence_not_an_instruction_quote(self):
        for slot in range(3):
            teacher, student = numeric_activity()
            pieces = student["units"][0]["text"].split("____")
            student["units"][0]["text"] = "".join(p + ("30" if n == slot else "____") for n, p in enumerate(pieces[:-1])) + pieces[-1]
            result = assess(student, teacher)
            records = next(i for i in result["items"] if i["id"] == "RECORDS")
            self.assertEqual(records["status"], "done")
            self.assertEqual(records["recorded_fields"], [{"field": slot + 1, "value": "30"}])
            self.assertEqual(records["answer"], "")
            self.assertIn("Numeric measurement", records["completion_evidence"])

    def test_all_three_values_remain_one_activity_and_existing_explanation_is_preserved(self):
        teacher, student = numeric_activity()
        for value in ("72", "108", "81"):
            student["units"][0]["text"] = student["units"][0]["text"].replace("____", value, 1)
        student["units"][2]["text"] = "My pulse increased during the exercise and then returned toward its resting rate."
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 2, "required": 2})
        records = next(i for i in result["items"] if i["id"] == "RECORDS")
        self.assertEqual(records["recorded_fields"], [{"field": 1, "value": "72"}, {"field": 2, "value": "108"}, {"field": 3, "value": "81"}])
        self.assertEqual(records["answer"], "")
        self.assertEqual(next(i for i in result["items"] if i["id"] == "EXPLAIN")["answer"], student["units"][2]["text"])

    def test_changed_instructions_overlay_or_unknown_blank_format_holds_without_zero(self):
        for change in ("instruction", "overlay", "format", "missing-source", "duplicate"):
            teacher, student = numeric_activity()
            if change == "instruction":
                student["units"][0]["text"] = student["units"][0]["text"].replace("30 seconds", "45 seconds")
            elif change == "overlay":
                student["units"].append(unit("overlay", "72", (300, 160, 40, 30)))
            elif change == "format":
                student["units"][0]["text"] = student["units"][0]["text"].replace("____", "about 72", 1)
            elif change == "missing-source":
                teacher["units"].pop(0)
            else:
                student["units"].append(deepcopy(student["units"][0]))
            with patch.object(completion, "score", side_effect=AssertionError("A held source cannot calculate zero")):
                result = assess(student, teacher)
            self.assertIsNone(result["notes_score"])
            self.assertIsNone(result["revision_score"])
            self.assertIn("numeric activity", result["note"])

    def test_numeric_evidence_does_not_judge_physiological_correctness(self):
        teacher, student = numeric_activity()
        student["units"][0]["text"] = student["units"][0]["text"].replace("____", "0", 1)
        result = assess(student, teacher)
        self.assertEqual(next(i for i in result["items"] if i["id"] == "RECORDS")["status"], "done")


class RasterCardTests(unittest.TestCase):
    def test_unchanged_stock_and_supplied_example_never_receive_credit(self):
        teacher, student = raster_activity()
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "")

    def test_all_fourteen_original_cards_bind_to_all_fourteen_authentic_empty_targets(self):
        for n in range(14):
            teacher, student = raster_activity()
            move_card(student, n, n)
            result = assess(student, teacher)
            self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
            self.assertEqual(result["items"][0]["answer"], "")
            self.assertIn("Original card", result["items"][0]["completion_evidence"])
        teacher, student = raster_activity()
        for n in range(14):
            move_card(student, n, n)
        self.assertEqual(assess(student, teacher)["counts"]["NOTES"], {"done": 1, "required": 1})

    def test_teacher_manifest_and_exact_raster_background_are_mandatory(self):
        for change in ("hash", "crop", "missing-background", "student-target", "missing-teacher-binding"):
            teacher, student = raster_activity()
            move_card(student, 0, 0)
            if change == "hash":
                student["background"]["sha256"] = "0" * 64
            elif change == "crop":
                student["background"]["appearance"]["crop"] = {"l": "1000"}
            elif change == "missing-background":
                student.pop("background")
            elif change == "student-target":
                student["units"][-1]["binding"]["targets"][0]["box"] = (20, 70, 500, 500)
            else:
                teacher["units"][-1].pop("binding")
            with patch.object(completion, "score", side_effect=AssertionError("An unknown raster cannot calculate zero")):
                result = assess(student, teacher)
            self.assertIsNone(result["notes_score"])
            self.assertIn("original cards", result["note"])

    def test_changed_page_size_cannot_stretch_an_authentic_raster_into_guessed_targets(self):
        teacher, student = raster_activity()
        move_card(student, 0, 0)
        with patch.object(completion, "score", side_effect=AssertionError("A stretched raster cannot calculate zero")):
            result = completion.assess({"slides": [student], "size": (1000, 800)}, {"slides": [teacher], "size": SIZE})
        self.assertIsNone(result["notes_score"])
        self.assertIn("original cards", result["note"])

    def test_missing_duplicate_retyped_or_moved_given_card_is_held(self):
        for change in ("missing", "duplicate", "retyped", "given", "unknown-picture"):
            teacher, student = raster_activity()
            move_card(student, 0, 0)
            if change == "missing":
                student["units"].pop(1)
            elif change == "duplicate":
                student["units"].append(deepcopy(student["units"][1]))
            elif change == "retyped":
                student["units"].append(unit("typed", "A newly typed label", (30, 80, 80, 30)))
            elif change == "given":
                student["units"][-2]["box"] = (30, 80, 100, 30)
            else:
                student["units"].append(unit("new-picture", "", (30, 80, 100, 30), picture={"sha256": "0" * 64}))
            self.assertIsNone(assess(student, teacher)["notes_score"])

    def test_offtarget_original_movement_needs_an_independent_valid_attempt(self):
        teacher, student = raster_activity()
        student["units"][0]["box"] = (900, 560, 55, 30)
        self.assertIsNone(assess(student, teacher)["notes_score"])
        move_card(student, 1, 1)
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertTrue(any("Off-target" in item["excerpt"] for item in result["uncoded"]))

    def test_distinct_source_page_and_unique_teacher_manifest_are_required(self):
        teacher, student = raster_activity()
        move_card(student, 0, 0)
        student["number"] = 2
        d = {"slides": [{"number": 1, "units": []}, student], "size": SIZE}
        t = {"slides": [teacher], "size": SIZE}
        self.assertEqual(completion._align(d, t)[2]["number"], 1)
        other = deepcopy(teacher); other["number"] = 2
        self.assertIsNone(completion.assess(d, {"slides": [teacher, other], "size": SIZE})["notes_score"])


class ExplicitRasterParserTests(unittest.TestCase):
    def test_background_bytes_and_appearance_are_read_only_for_explicit_activity(self):
        p = "http://schemas.openxmlformats.org/presentationml/2006/main"
        a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        from xml.sax.saxutils import quoteattr
        marker = response_binding.PREFIX + json.dumps({"kind": "raster-card-activity"})
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("ppt/slides/slide1.xml", f'<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:bg><p:bgPr><a:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></a:blipFill></p:bgPr></p:bg><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="1" name="code" descr={quoteattr(marker)}/></p:nvSpPr><p:txBody><a:p><a:r><a:t>[[NOTES:C09:ORGANIZER]]</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
            z.writestr("ppt/slides/_rels/slide1.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{r}/image" Target="../media/background.png"/></Relationships>')
            z.writestr("ppt/media/background.png", b"authentic background bytes")
        deck = completion.read_deck(raw.getvalue())
        self.assertEqual(deck["slides"][0]["background"], {"sha256": hashlib.sha256(b"authentic background bytes").hexdigest(),
            "appearance": {"crop": {}, "stretch": {}, "tile": None, "effects": []}})
        self.assertEqual(len(deck["slides"][0]["units"]), 1, "Raster prompts never become invented native writing")


if __name__ == "__main__":
    unittest.main()
