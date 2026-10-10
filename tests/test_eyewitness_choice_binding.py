"""A single authentic circle and a later written revision remain distinct."""
from copy import deepcopy
import io
import json
import unittest
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

from hub_v2 import completion, graphical_choice, response_binding

P, A = completion.P, completion.A
SIZE = (1000000, 750000)


def shape(name, text, box, geometry="rect", outline=False, fill=None, **attributes):
    x, y, w, h = box
    extra = " ".join(f'{k}="{v}"' for k, v in attributes.items())
    line = '<a:solidFill><a:srgbClr val="0066CC"/></a:solidFill>' if outline else '<a:noFill/>'
    paper = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else '<a:noFill/>'
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="{name}" {extra}/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
            f'<a:prstGeom prst="{geometry}"><a:avLst/></a:prstGeom>{paper}<a:ln w="10000">{line}</a:ln></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>')


def raw(shapes):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{P[1:-1]}"><p:sldSz cx="{SIZE[0]}" cy="{SIZE[1]}"/></p:presentation>')
        archive.writestr("ppt/slides/slide1.xml", f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>')
    return out.getvalue()


def fixture():
    content = shape("inventory", "[[MARKER-INVENTORY:F04]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q-MASTERY-4 DRAFT_IDS=Q-MASTERY-4 OPTIONAL_IDS=- BANK_IDS=-", (10000, 10000, 600000, 10000))
    content += shape("original-initial-paper", "", (50000, 100000, 400000, 350000), fill="F8FAFC")
    content += shape("question", "Can a confident witness still be wrong? Circle one:", (50000, 100000, 400000, 80000))
    content += shape("yes-button", "", (60000, 230000, 90000, 40000), fill="FFFFFF") + shape("yes", "YES", (60000, 230000, 90000, 40000))
    content += shape("no-button", "", (190000, 230000, 90000, 40000), fill="FFFFFF") + shape("no", "NO", (190000, 230000, 90000, 40000))
    content += shape("revision-label", "Your revised answer. Explain your reasoning. [[DRAFT:F04:Q-MASTERY-4]]", (550000, 100000, 400000, 70000))
    content += shape("revision-response", "", (550000, 200000, 400000, 220000))
    tree = ET.fromstring(raw_xml(content)).find(P + "cSld/" + P + "spTree")
    shapes = graphical_choice.read_shapes(tree, box=completion._box, affine=completion._affine, text=completion._text, name=completion._name)
    region = [50000, 100000, 400000, 350000]
    data = {"kind": graphical_choice.KIND, "marker_text": "[[NOTES:F04:Q-MASTERY-4]]", "page_size": list(SIZE), "region": region,
            "prompt": {"text": "Can a confident witness still be wrong? Circle one:", "box": [50000, 100000, 400000, 80000]},
            "options": [{"text": label, "box": list(box)} for label, box in (("YES", (60000, 230000, 90000, 40000)), ("NO", (190000, 230000, 90000, 40000)))],
            "original_shapes": [{"sha256": s["sha256"], "box": list(s["box"])} for s in shapes if graphical_choice._overlaps(s["box"], region)],
            "revision": {"category": "DRAFT", "id": "Q-MASTERY-4", "box": [550000, 200000, 400000, 220000],
                         "prompt": {"text": "Your revised answer. Explain your reasoning.", "box": [550000, 100000, 400000, 70000]}}}
    desc = escape(response_binding.PREFIX + json.dumps(data), {'"': '&quot;', '\n': '&#10;'})
    content += shape("choice-code", "[[NOTES:F04:Q-MASTERY-4]]", (900000, 50000, 1000, 1000), descr=desc)
    return content


def raw_xml(content):
    return f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><p:cSld><p:spTree>{content}</p:spTree></p:cSld></p:sld>'


def circle(label="YES", **attributes):
    return shape("student-circle-" + label, "", (55000 if label == "YES" else 185000, 225000, 100000, 50000), "ellipse", True, **attributes)


def assess(content, base=None):
    return completion.assess(completion.read_deck(raw(content)), completion.read_deck(raw(base or fixture())))


class EyewitnessChoiceTests(unittest.TestCase):
    def test_original_supplied_buttons_and_printed_yes_no_are_blank(self):
        result = assess(fixture())
        self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
        self.assertEqual(result["counts"]["DRAFT"], {"done": 0, "required": 1})
        self.assertIsNone(result["note"])

    def test_either_single_visible_circle_is_one_completion_without_authored_answer(self):
        for label in ("YES", "NO"):
            result = assess(fixture() + circle(label))
            notes = next(i for i in result["items"] if i["category"] == "NOTES")
            self.assertEqual(notes["status"], "done")
            self.assertEqual(notes["answer"], "")
            self.assertEqual(notes["recorded_choice"], label)
            self.assertEqual(notes["completion_evidence"], "One original YES/NO option circled")
            self.assertEqual(result["counts"]["DRAFT"]["done"], 0)

    def test_written_revision_cannot_fill_initial_choice(self):
        base = fixture()
        filled = base.replace('name="revision-response"', 'name="revision-response"', 1)
        start = filled.index('name="revision-response"')
        target = filled.index('<a:t></a:t>', start)
        filled = filled[:target] + '<a:t>A reliable witness had good lighting and a clear view.</a:t>' + filled[target + len('<a:t></a:t>'):]
        result = assess(filled)
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)
        self.assertEqual(result["counts"]["DRAFT"]["done"], 1)
        self.assertIn("good lighting", next(i["answer"] for i in result["items"] if i["category"] == "DRAFT"))
        result = assess(filled + circle())
        self.assertEqual([result["counts"][c]["done"] for c in ("NOTES", "DRAFT")], [1, 1])

    def assert_held(self, content):
        result = assess(content)
        self.assertTrue(result["note"])
        self.assertIsNone(result["notes_score"])
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_ambiguous_choice_hold_names_actual_teacher_action(self):
        result = assess(fixture() + circle() + circle("NO"))
        self.assertIn("YES/NO", result["note"])
        self.assertIn("visible choice mark and separate revision box", result["note"])

    def test_both_circles_and_unknown_marks_hold(self):
        for extra in (circle() + circle("NO"), circle() + circle(), shape("unknown", "", (60000, 220000, 90000, 40000), "rect", True),
                      shape("typed-overlay", "YES", (60000, 230000, 90000, 40000)),
                      shape("huge-circle", "", (40000, 210000, 280000, 90000), "ellipse", True)):
            with self.subTest(extra=extra[:100]):
                self.assert_held(fixture() + extra)

    def test_hidden_transparent_filled_rotated_and_inherited_effect_marks_hold(self):
        hidden_parent = ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="2" name="hidden-group" hidden="1"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                         '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="750000"/>'
                         '<a:chOff x="0" y="0"/><a:chExt cx="1000000" cy="750000"/></a:xfrm></p:grpSpPr>' + circle() + '</p:grpSp>')
        effect_parent = hidden_parent.replace(' hidden="1"', '').replace('</p:grpSpPr>', '<a:effectLst><a:alphaModFix amt="0"/></a:effectLst></p:grpSpPr>')
        for extra in (circle(hidden="1"), circle().replace('val="0066CC"/>', 'val="0066CC"><a:alpha val="0"/></a:srgbClr>'),
                      circle().replace('<a:noFill/>', '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>', 1),
                      circle().replace('<a:xfrm>', '<a:xfrm rot="60000">'), hidden_parent, effect_parent):
            self.assert_held(fixture() + extra)

    def test_source_prompt_labels_buttons_geometry_and_descriptor_changes_hold(self):
        base = fixture()
        for student in (base.replace('Can a confident witness still be wrong? Circle one:', 'A changed printed prompt.', 1),
                        base.replace('<a:t>YES</a:t>', '<a:t>MAYBE</a:t>', 1),
                        base.replace('name="yes-button"', 'name="yes-button" hidden="1"', 1),
                        base.replace('x="60000"', 'x="65000"', 1),
                        base.replace('circled-choice', 'unknown-choice', 1)):
            self.assert_held(student + circle())

    def test_student_only_forged_descriptor_or_changed_page_size_cannot_supply_trust(self):
        student = completion.read_deck(raw(fixture() + circle()))
        teacher = completion.read_deck(raw(fixture()))
        teacher["slides"][0]["units"] = [u for u in teacher["slides"][0]["units"] if "binding" not in u]
        result = completion.assess(student, teacher)
        self.assertIsNone(result["notes_score"])
        teacher = completion.read_deck(raw(fixture()))
        student["size"] = (SIZE[0] + 10000, SIZE[1])
        result = completion.assess(student, teacher)
        self.assertIsNone(result["notes_score"])

    def test_export_shape_name_and_id_changes_keep_authentic_geometry_and_words(self):
        base = fixture()
        student = base.replace('name="yes"', 'name="renumbered-yes"').replace('name="question"', 'name="renumbered-question"')
        student = student.replace('id="1"', 'id="998"')
        result = assess(student + circle())
        self.assertEqual(result["counts"]["NOTES"]["done"], 1)

    def test_new_revision_overlay_elsewhere_is_loose_and_not_either_answer(self):
        result = assess(fixture() + shape("other-answer", "Some unrelated writing", (500000, 500000, 400000, 80000)))
        self.assertEqual([result["counts"][c]["done"] for c in ("NOTES", "DRAFT")], [0, 0])
        self.assertEqual(len(result["uncoded"]), 1)

    def test_new_text_box_inside_authentic_revision_blank_remains_written_revision(self):
        result = assess(fixture() + shape("new-written-revision", "A clear view and good lighting improve reliability.", (570000, 220000, 350000, 70000)))
        self.assertEqual([result["counts"][c]["done"] for c in ("NOTES", "DRAFT")], [0, 1])
        self.assertIn("good lighting", next(i["answer"] for i in result["items"] if i["category"] == "DRAFT"))
        self.assert_held(fixture() + shape("overlapping-ambiguous-revision", "My response", (530000, 200000, 450000, 90000)))

    def test_changed_choice_code_cannot_credit_another_question(self):
        # Change only the visible marker; leave the trusted descriptor unaltered.
        student = fixture().replace('<a:t>[[NOTES:F04:Q-MASTERY-4]]</a:t>', '<a:t>[[NOTES:F04:Q-MASTERY]]</a:t>', 1)
        self.assert_held(student + circle())

    def test_malformed_trusted_metadata_fails_closed_without_a_parser_exception(self):
        for value in (None, [None], [{"text": "YES"}, {"text": "NO"}, None]):
            teacher = completion.read_deck(raw(fixture()))
            student = completion.read_deck(raw(fixture() + circle()))
            for deck in (teacher, student):
                code = next(u for u in deck["slides"][0]["units"] if "binding" in u)
                code["binding"]["options"] = value
            result = completion.assess(student, teacher)
            self.assertIsNone(result["notes_score"])
            self.assertTrue(result["note"])

    def test_outline_matching_authentic_paper_or_concealed_behind_original_shapes_holds(self):
        for color in ("FFFFFF", "F8FAFC", "F5F5F5"):
            self.assert_held(fixture() + circle().replace('val="0066CC"', f'val="{color}"'))
        self.assert_held(circle() + fixture())
        self.assert_held(fixture().replace('<p:sp><p:nvSpPr><p:cNvPr id="1" name="yes-button"',
            circle() + '<p:sp><p:nvSpPr><p:cNvPr id="1" name="yes-button"', 1))

    def test_unknown_original_paper_fill_cannot_establish_visible_choice(self):
        teacher = completion.read_deck(raw(fixture()))
        student = completion.read_deck(raw(fixture() + circle()))
        for deck in (teacher, student):
            for s in deck["slides"][0]["choice_shapes"]:
                s["fill_rgb"] = None
        result = completion.assess(student, teacher)
        self.assertIsNone(result["notes_score"])

    def test_subpixel_or_group_compressed_outline_is_held(self):
        self.assert_held(fixture() + circle().replace('w="10000"', 'w="1"'))
        group = ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="2" name="scaled-group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                 '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="750000"/>'
                 '<a:chOff x="0" y="0"/><a:chExt cx="1000000000" cy="750000000"/></a:xfrm></p:grpSpPr>')
        huge = circle().replace('x="55000"', 'x="55000000"').replace('y="225000"', 'y="225000000"').replace(
            'cx="100000" cy="50000"', 'cx="100000000" cy="50000000"')
        self.assert_held(fixture() + group + huge + '</p:grpSp>')

    def test_relative_painter_order_of_original_paper_question_and_options_is_preserved(self):
        content = fixture()
        tree = ET.fromstring(raw_xml(content)).find(P + "cSld/" + P + "spTree")
        paper = next(n for n in tree if n.find(P + "nvSpPr/" + P + "cNvPr").get("name") == "original-initial-paper")
        tree.remove(paper)
        tree.append(paper)
        shifted = ''.join(ET.tostring(n, encoding="unicode") for n in tree)
        self.assert_held(shifted + circle())
        # The same descriptor on a changed teacher pane cannot bless its own
        # hidden question or reorder the preserved original baseline manifest.
        result = assess(shifted + circle(), base=shifted)
        self.assertIsNone(result["notes_score"])

    def test_unverified_circle_shape_attributes_and_extension_payloads_hold(self):
        for mark in (circle().replace('<p:sp>', '<p:sp useBgFill="1">', 1),
                     circle().replace('<p:sp>', '<p:sp useBgFill="0">', 1),
                     circle().replace('<p:spPr>', '<p:spPr bwMode="white">', 1),
                     circle().replace('</p:sp>', '<p:extLst><p:ext uri="unknown-rendering-payload"/></p:extLst></p:sp>')):
            self.assert_held(fixture() + mark)


if __name__ == "__main__":
    unittest.main()
