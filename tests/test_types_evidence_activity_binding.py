"""Conditional evidence rows and genuine maze highlights preserve authored work."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from hub_v2 import completion, response_binding

SIZE = (1000, 750)


def unit(key, text, box, **extra):
    return {"key": key, "text": text, "box": box, "title": False, **extra}


def assess(student, teacher, size=SIZE):
    return completion.assess({"slides": [student], "size": size}, {"slides": [teacher], "size": SIZE})


def rows():
    headers = [{"text": "Direct or Circumstantial?", "box": (330, 30, 220, 25)},
               {"text": "Physical or Biological? (Circumstantial)", "box": (650, 30, 300, 25)}]
    source = {"number": 1, "units": [unit(f"header-{i}", h["text"], h["box"]) for i, h in enumerate(headers)]}
    for i, text in enumerate(("An original signed statement", "A fragment from a window")):
        y = 100 + i * 90
        item = {"text": text, "box": (30, y, 220, 50)}
        fields = [{"text": "Type here", "box": (330, y, 220, 50)},
                  {"text": "Type here", "box": (650, y, 220, 50)}]
        data = {"kind": "conditional-response-row", "page_size": list(SIZE),
                "item": item, "headers": deepcopy(headers), "fields": fields}
        source["units"].extend([unit(f"item-{i}", text, item["box"])] +
            [unit(f"field-{i}-{j}", f["text"], f["box"]) for j, f in enumerate(fields)] +
            [unit(f"marker-{i}", f"[[NOTES:F09:ROW-{i}]]", (400, y + 20, 1, 1), binding=data)])
    return source, deepcopy(source)


def set_text(slide, key, text):
    next(u for u in slide["units"] if u["key"] == key)["text"] = text


def maze():
    background = {"sha256": hashlib.sha256(b"authentic worksheet raster").hexdigest(),
                  "appearance": {"crop": {}, "stretch": {}, "tile": None, "effects": []}}
    stock = [{"sha256": "a" * 64, "box": (20, 650, 60, 30)} for _ in range(27)]
    given = [{"sha256": "a" * 64, "box": (500, 150, 60, 30)}]
    data = {"kind": "raster-highlight-activity", "page_size": list(SIZE), "background": background,
            "maze_region": (300, 70, 680, 600), "stock": stock, "given": given}
    teacher = {"number": 1, "background": deepcopy(background), "units": [
        unit(f"stock-{i}", "", s["box"], highlight_shape={"sha256": s["sha256"]}) for i, s in enumerate(stock)] +
        [unit("given", "", given[0]["box"], highlight_shape={"sha256": "a" * 64}),
         unit("maze-marker", "[[NOTES:F09:MAZE]]", (1, 1, 1, 1), binding=data)]}
    return teacher, deepcopy(teacher)


class ConditionalEvidenceRowTests(unittest.TestCase):
    def test_every_unchanged_placeholder_stays_blank_and_both_fields_are_one_row(self):
        teacher, student = rows()
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 2})
        self.assertEqual([i["answer"] for i in result["items"]], ["", ""])

    def test_direct_answer_never_requires_the_conditional_second_cell(self):
        teacher, student = rows()
        set_text(student, "field-0-0", "Direct")
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 2})
        self.assertEqual(result["items"][0]["answer"], "Direct")
        self.assertEqual(result["items"][1]["status"], "blank")

    def test_circumstantial_classification_and_detail_count_one_attempt_not_two_points(self):
        teacher, student = rows()
        set_text(student, "field-0-0", "Circumstantial")
        set_text(student, "field-0-1", "Biological")
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 2})
        self.assertEqual(result["items"][0]["answer"], "Circumstantial Biological")

    def test_each_authentic_cell_can_show_a_partial_meaningful_row_attempt(self):
        for row in range(2):
            for field in range(2):
                teacher, student = rows()
                set_text(student, f"field-{row}-{field}", "Physical" if field else "Direct")
                result = assess(student, teacher)
                self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 2})
                self.assertEqual(result["items"][row]["status"], "done")
                self.assertEqual(result["items"][1 - row]["status"], "blank")

    def test_deleted_partial_placeholder_and_punctuation_do_not_become_answers(self):
        for text in ("", "Type", "here", "Type here", "..."):
            teacher, student = rows()
            set_text(student, "field-0-0", text)
            self.assertEqual(assess(student, teacher)["counts"]["NOTES"]["done"], 0)

    def test_whole_supplied_item_or_both_printed_choices_cannot_count_as_a_classification(self):
        for text in ("Direct or Circumstantial?", "Physical or Biological? (Circumstantial)", "Physical or Biological", "An original signed statement"):
            teacher, student = rows()
            set_text(student, "field-0-0", text)
            self.assertEqual(assess(student, teacher)["counts"]["NOTES"]["done"], 0)

    def test_source_item_condition_geometry_or_teacher_trust_changes_hold_without_zero(self):
        for change in ("item", "condition", "moved-response", "changed-manifest", "teacher-binding", "page-size"):
            teacher, student = rows()
            set_text(student, "field-0-0", "Direct")
            if change == "item":
                set_text(student, "item-0", "A different item")
            elif change == "condition":
                set_text(student, "header-1", "Always required")
            elif change == "moved-response":
                next(u for u in student["units"] if u["key"] == "field-0-0")["box"] = (330, 410, 220, 50)
            elif change == "changed-manifest":
                next(u for u in student["units"] if u["key"] == "marker-0")["binding"]["fields"][0]["box"] = (20, 300, 10, 10)
            elif change == "teacher-binding":
                next(u for u in teacher["units"] if u["key"] == "marker-0").pop("binding")
            with patch.object(completion, "score", side_effect=AssertionError("Unknown bindings cannot calculate zeros")):
                result = assess(student, teacher, (1000, 800) if change == "page-size" else SIZE)
            self.assertIsNone(result["notes_score"], change)
            self.assertIsNone(result["revision_score"], change)

    def test_duplicate_overlaid_responses_and_unknown_pictures_hold(self):
        for change in ("duplicate", "overlay", "picture"):
            teacher, student = rows()
            set_text(student, "field-0-0", "Direct")
            actual = next(u for u in student["units"] if u["key"] == "field-0-0")
            if change == "duplicate":
                student["units"].append(deepcopy(actual))
            else:
                student["units"].append(unit("new", "An overlay" if change == "overlay" else "", (350, 110, 50, 20),
                                            **({"picture": {"sha256": "b" * 64}} if change == "picture" else {})))
            self.assertIsNone(assess(student, teacher)["notes_score"], change)

    def test_export_names_can_change_without_cross_row_credit(self):
        teacher, student = rows()
        for n, u in enumerate(student["units"]):
            u["key"] = f"renamed-{n}"
        student["units"][3]["text"] = "Direct"
        result = assess(student, teacher)
        self.assertEqual(result["items"][0]["status"], "done")
        self.assertEqual(result["items"][1]["status"], "blank")


class MazeHighlightTests(unittest.TestCase):
    def test_stock_duplicates_and_already_given_teacher_highlight_are_unscored(self):
        teacher, student = maze()
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "")

    def test_one_moved_original_highlighter_counts_one_activity_without_authored_prose(self):
        teacher, student = maze()
        student["units"][0]["box"] = (700, 200, 60, 30)
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "")
        self.assertIn("highlighter", result["items"][0]["completion_evidence"])
        self.assertEqual(result["uncoded"], [])

    def test_permitted_copy_paste_and_multiple_moved_shapes_are_still_one_activity(self):
        teacher, student = maze()
        duplicate = deepcopy(student["units"][0]); duplicate["key"] = "new-copy"; duplicate["box"] = (700, 200, 60, 30)
        student["units"].append(duplicate)
        student["units"][1]["box"] = (800, 300, 60, 30)
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "")

    def test_an_independent_proven_attempt_does_not_turn_an_off_target_shape_into_written_prose(self):
        teacher, student = maze()
        student["units"][0]["box"] = (700, 200, 60, 30)
        student["units"][1]["box"] = (170, 400, 60, 30)
        result = assess(student, teacher)
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "")
        self.assertEqual(result["uncoded"], [])

    def test_given_highlight_cannot_move_or_duplicate_without_review(self):
        for change in ("move", "duplicate"):
            teacher, student = maze()
            student["units"][0]["box"] = (700, 200, 60, 30)
            if change == "move":
                student["units"][-2]["box"] = (800, 300, 60, 30)
            else:
                student["units"].append(deepcopy(student["units"][-2]))
            self.assertIsNone(assess(student, teacher)["notes_score"])

    def test_unknown_or_hidden_changed_shapes_images_text_raster_or_missing_stock_hold(self):
        for change in ("style", "resize", "missing", "background", "crop", "teacher-trust", "page-size", "text", "image", "off-target"):
            teacher, student = maze()
            student["units"][0]["box"] = (700, 200, 60, 30)
            if change == "style":
                student["units"][0]["highlight_shape"]["sha256"] = "b" * 64
            elif change == "resize":
                student["units"][0]["box"] = (700, 200, 120, 30)
            elif change == "missing":
                student["units"].pop(1)
            elif change == "background":
                student["background"]["sha256"] = "b" * 64
            elif change == "crop":
                student["background"]["appearance"]["crop"] = {"r": "5000"}
            elif change == "teacher-trust":
                teacher["units"][-1].pop("binding")
            elif change == "text":
                student["units"].append(unit("text", "A hidden answer", (500, 400, 100, 30)))
            elif change == "image":
                student["units"].append(unit("picture", "", (500, 400, 100, 30), picture={"sha256": "b" * 64}))
            elif change == "off-target":
                student["units"][0]["box"] = (170, 400, 60, 30)
            with patch.object(completion, "score", side_effect=AssertionError("Unknown activity cannot calculate zeros")):
                result = assess(student, teacher, (1000, 800) if change == "page-size" else SIZE)
            self.assertIsNone(result["notes_score"], change)
            self.assertIsNone(result["revision_score"], change)

    def test_shape_translation_signature_retains_size_rotation_paint_and_visibility(self):
        xml = '<p:sp xmlns:p="%s" xmlns:a="%s"><p:nvSpPr><p:cNvPr id="1" name="shape"/></p:nvSpPr><p:spPr><a:xfrm rot="100"><a:off x="10" y="20"/><a:ext cx="60" cy="30"/></a:xfrm><a:solidFill><a:srgbClr val="00FF00"><a:alpha val="36870"/></a:srgbClr></a:solidFill></p:spPr></p:sp>' % (completion.P[1:-1], completion.A[1:-1])
        original = ET.fromstring(xml)
        signature = completion._highlight_shape(original, ())
        for change in ("translate", "resize", "rotation", "hidden", "paint"):
            node = deepcopy(original)
            if change == "translate":
                node.find(completion.P + "spPr/" + completion.A + "xfrm/" + completion.A + "off").set("x", "500")
            elif change == "resize":
                node.find(completion.P + "spPr/" + completion.A + "xfrm/" + completion.A + "ext").set("cx", "80")
            elif change == "rotation":
                node.find(completion.P + "spPr/" + completion.A + "xfrm").set("rot", "200")
            elif change == "hidden":
                node.find(completion.P + "nvSpPr/" + completion.P + "cNvPr").set("hidden", "1")
            else:
                node.find(completion.P + "spPr/" + completion.A + "solidFill/" + completion.A + "srgbClr").set("val", "FF0000")
            self.assertEqual(signature == completion._highlight_shape(node, ()), change == "translate", change)

    def test_identity_parent_group_is_visible_but_inherited_hidden_or_group_effects_change_evidence(self):
        P, A = completion.P, completion.A
        shape = ET.fromstring('<p:sp xmlns:p="%s" xmlns:a="%s"><p:nvSpPr><p:cNvPr id="1" name="shape"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="60" cy="30"/></a:xfrm><a:solidFill><a:srgbClr val="00FF00"/></a:solidFill></p:spPr></p:sp>' % (P[1:-1], A[1:-1]))
        signature = completion._highlight_shape(shape, ())
        for change in ("visible", "hidden", "effect"):
            root = ET.Element(P + 'spTree'); group = ET.SubElement(root, P + 'grpSp')
            nv = ET.SubElement(group, P + 'nvGrpSpPr'); props = ET.SubElement(nv, P + 'cNvPr', {'name': 'identity-group'})
            if change == 'hidden': props.set('hidden', '1')
            paint = ET.SubElement(group, P + 'grpSpPr'); xf = ET.SubElement(paint, A + 'xfrm')
            ET.SubElement(xf, A + 'off', {'x': '0', 'y': '0'}); ET.SubElement(xf, A + 'ext', {'cx': '1000', 'cy': '750'})
            ET.SubElement(xf, A + 'chOff', {'x': '0', 'y': '0'}); ET.SubElement(xf, A + 'chExt', {'cx': '1000', 'cy': '750'})
            if change == 'effect': ET.SubElement(ET.SubElement(paint, A + 'effectLst'), A + 'alphaModFix', {'amt': '0'})
            group.append(deepcopy(shape)); parsed = []
            completion._walk(root, parsed, (1, 1, 0, 0), highlighters=True)
            self.assertEqual(parsed[0]['box'], (10, 20, 60, 30))
            self.assertEqual(parsed[0]['highlight_shape'] == signature, change == 'visible', change)


if __name__ == '__main__':
    unittest.main()
