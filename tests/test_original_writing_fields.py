"""Actual parser regressions for writing only in authenticated original fields."""
from copy import deepcopy
import io
import json
import unittest
import zipfile
import xml.etree.ElementTree as E

from hub_v2 import completion as c, response_binding as rb, writing_fields as w, workspace_binding as ws

P, A = c.P, c.A
SIZE = (1000000, 750000)
BOXES = [[100000, 150000, 750000, 120000], [100000, 350000, 750000, 120000]]
CODE = "[[NOTES:F04:ONE]]"


def run(text, size="1400", color="000000"):
    node = E.Element(A + "r")
    props = E.SubElement(node, A + "rPr", {"sz": size})
    E.SubElement(E.SubElement(props, A + "solidFill"), A + "srgbClr", {"val": color})
    E.SubElement(node, A + "t").text = text
    return node


def shape(name, text, box):
    node = E.Element(P + "sp")
    nv = E.SubElement(node, P + "nvSpPr")
    E.SubElement(nv, P + "cNvPr", {"id": str(sum(map(ord, name))), "name": name})
    E.SubElement(nv, P + "cNvSpPr", {"txBox": "1"})
    E.SubElement(nv, P + "nvPr")
    props = E.SubElement(node, P + "spPr")
    xf = E.SubElement(props, A + "xfrm")
    E.SubElement(xf, A + "off", {"x": str(box[0]), "y": str(box[1])})
    E.SubElement(xf, A + "ext", {"cx": str(box[2]), "cy": str(box[3])})
    E.SubElement(props, A + "noFill")
    E.SubElement(E.SubElement(props, A + "ln"), A + "noFill")
    body = E.SubElement(node, P + "txBody")
    E.SubElement(body, A + "bodyPr")
    E.SubElement(body, A + "lstStyle")
    E.SubElement(body, A + "p").append(run(text))
    return node


def package(tree, extras=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        pres = E.Element(P + "presentation")
        E.SubElement(pres, P + "sldSz", {"cx": str(SIZE[0]), "cy": str(SIZE[1])})
        archive.writestr("ppt/presentation.xml", E.tostring(pres))
        root = E.Element(P + "sld")
        E.SubElement(root, P + "cSld").append(deepcopy(tree))
        archive.writestr("ppt/slides/slide1.xml", E.tostring(root))
        for name, raw in (extras or {}).items():
            archive.writestr(name, raw)
    return out.getvalue()


def fixture(group=False, paragraph_props=None, extras=None):
    tree = E.Element(P + "spTree")
    tree.append(shape("directions", "Explain your observation.", [100000, 10000, 750000, 70000]))
    tree.append(shape("one", "Type date here", BOXES[0]))
    tree.append(shape("two", "Type here", BOXES[1]))
    tree.append(shape("uncoded-choice", "", [100000, 550000, 120000, 40000]))
    if paragraph_props is not None:
        for index in (1, 2):
            tree[index].find(P + "txBody/" + A + "p").insert(0, deepcopy(paragraph_props))
    plain = c.read_deck(package(tree, extras))["slides"][0]
    supplied = sorted({u["text"] for u in plain["units"] if u["text"]})
    field_nodes = [tree[1], tree[2]] if group else [tree[1]]
    fields = [{"box": BOXES[i], "given": c._text(node.find(P + "txBody")),
               "pptx_surface_sha256": w.xml_surface(node),
               "visible_styles": [w.run_style(node.find(P + "txBody/" + A + "p/" + A + "r/" + A + "rPr"))],
               "default_styles": [w.run_style(None)]} for i, node in enumerate(field_nodes)]
    tree[1].find(P + "txBody/" + A + "p").append(run(" " + CODE, "100", "FFFFFF"))
    others = [{"box": BOXES[1], "pptx_surface_sha256": w.xml_surface(tree[2])},
              {"box": [100000, 550000, 120000, 40000], "pptx_surface_sha256": w.xml_surface(tree[3])}]
    page_fields = [{k: f[k] for k in ('box', 'pptx_surface_sha256')} for f in fields]
    page_fields += [f for f in others if f['box'] not in [x['box'] for x in page_fields]]
    data = {"kind": w.KIND, "page_size": list(SIZE), "fields": fields, "page_fields": page_fields, "supplied_text": supplied}
    tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("descr", rb.PREFIX + json.dumps(data))
    parsed = c.read_deck(package(tree, extras))["slides"][0]
    data.update({"page_scene": parsed["writing_scene"]["shapes"], "page_style": parsed["writing_scene"]["style"],
                 "page_media": parsed["writing_scene"]["media"], "resources": parsed["writing_scene"]["resources"]})
    tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("descr", rb.PREFIX + json.dumps(data))
    return tree


def write(tree, text, index=1):
    tree = deepcopy(tree)
    body = tree[index].find(P + "txBody")
    body.find(A + "p/" + A + "r/" + A + "t").text = text
    return tree


def assess(student, teacher=None, extras=None, teacher_extras=None):
    return c.assess(c.read_deck(package(student, extras)), c.read_deck(package(teacher if teacher is not None else fixture(), teacher_extras)), found_codes_only=True)


class OriginalWritingFieldsTests(unittest.TestCase):
    def held(self, tree, teacher=None, extras=None, teacher_extras=None):
        result = assess(tree, teacher, extras, teacher_extras)
        self.assertTrue(result["note"])
        self.assertIsNone(result["notes_score"])
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_blank_given_and_shortened_placeholder_never_credit(self):
        for text in ("Type date here", "Type here", "Type", "", "..."):
            with self.subTest(text=text):
                result = assess(write(fixture(), text))
                self.assertFalse(result["note"])
                self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_visible_short_and_numeric_attempts_are_exact_own_answer(self):
        for text in ("1984", "Blood", "Type date here 1984"):
            result = assess(write(fixture(), text))
            self.assertFalse(result["note"])
            self.assertEqual(result["items"][0]["answer"], "1984" if "1984" in text else "Blood")
            self.assertEqual(result["counts"]["NOTES"]["done"], 1)

    def test_supplied_directions_and_other_placeholders_are_not_answers(self):
        for text in ("Explain your observation.", "Type here", "Type date here Explain your observation."):
            result = assess(write(fixture(), text))
            self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_other_question_choice_cannot_lend_its_response(self):
        for index in (2, 3):
            result = assess(write(fixture(), "A", index))
            self.assertFalse(result["note"])
            self.assertEqual(result["counts"]["NOTES"]["done"], 0)
            self.assertEqual(result["items"][0]["answer"], "")

    def test_two_original_fields_are_one_activity_not_two_questions(self):
        teacher = fixture(True)
        result = assess(write(teacher, "An original pro", 1), teacher)
        self.assertFalse(result["note"])
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertEqual(result["items"][0]["answer"], "An original pro")
        result = assess(write(write(teacher, "An original pro", 1), "An original con", 2), teacher)
        self.assertEqual(result["items"][0]["answer"], "An original pro An original con")

    def test_changed_source_teacher_descriptor_or_unknown_trust_holds(self):
        for mode in ("descriptor", "teacher", "marker", "untrusted"):
            student, teacher = write(fixture(), "Blood"), fixture()
            if mode == "descriptor":
                student[1].find(P + "nvSpPr/" + P + "cNvPr").set("descr", rb.PREFIX + "{}")
            if mode == "teacher":
                teacher[1].find(P + "spPr/" + A + "xfrm/" + A + "off").set("x", "100001")
            if mode == "marker":
                student[1].findall(P + "txBody/" + A + "p/" + A + "r")[-1].find(A + "t").text = "[[NOTES:F04:OTHER]]"
            if mode == "untrusted":
                teacher[1].find(P + "nvSpPr/" + P + "cNvPr").attrib.pop("descr")
            self.held(student, teacher)

    def test_hidden_alpha_white_tiny_effect_and_inherited_hidden_text_hold(self):
        for mode in ("hidden", "alpha", "white", "tiny", "effect", "inherited"):
            tree = write(fixture(), "Blood")
            props = tree[1].find(P + "txBody/" + A + "p/" + A + "r/" + A + "rPr")
            if mode == "hidden":
                tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("hidden", "1")
            if mode == "alpha":
                E.SubElement(props.find(A + "solidFill/" + A + "srgbClr"), A + "alpha", {"val": "0"})
            if mode == "white":
                props.find(A + "solidFill/" + A + "srgbClr").set("val", "FFFFFF")
            if mode == "tiny":
                props.set("sz", "100")
            if mode == "effect":
                E.SubElement(E.SubElement(props, A + "effectLst"), A + "outerShdw")
            if mode == "inherited":
                tree[1].find(P + "txBody/" + A + "p/" + A + "r").remove(props)
                default = E.SubElement(tree[1].find(P + "txBody/" + A + "p"), A + "endParaRPr", {"sz": "1400"})
                E.SubElement(E.SubElement(default, A + "solidFill"), A + "srgbClr", {"val": "FFFFFF"})
            self.held(tree)

    def test_actual_trusted_visible_typing_run_not_a_forged_student_style(self):
        teacher = fixture()
        sentinel = run("\u200b", "1200")
        sentinel.find(A + "rPr").set("kern", "1200")
        teacher[1].find(P + "txBody/" + A + "p").append(sentinel)
        student = write(teacher, "Blood")
        old = student[1].find(P + "txBody/" + A + "p/" + A + "r")
        old.remove(old.find(A + "rPr")); old.insert(0, deepcopy(sentinel.find(A + "rPr")))
        result = assess(student, teacher)
        self.assertFalse(result["note"])
        self.assertEqual(result["items"][0]["answer"], "Blood")
        self.held(student, fixture())  # student-only style cannot authorize itself
        invisible = deepcopy(teacher)
        invisible[1].findall(P + "txBody/" + A + "p/" + A + "r")[-1].find(A + "rPr/" + A + "solidFill/" + A + "srgbClr").set("val", "FFFFFF")
        self.held(student, invisible)

    def test_changed_paragraph_margin_indent_spacing_or_inherited_style_holds(self):
        for mode in ("margin", "indent", "spacing", "line-spacing", "bullet", "inherited", "duplicate", "paragraph-attribute"):
            tree = write(fixture(), "Blood")
            paragraph = tree[1].find(P + "txBody/" + A + "p")
            props = E.Element(A + "pPr")
            if mode == "margin":
                props.set("marL", "91440000")
            if mode == "indent":
                props.set("indent", "91440000")
            if mode in ("spacing", "line-spacing"):
                E.SubElement(E.SubElement(props, A + ("spcBef" if mode == "spacing" else "lnSpc")),
                             A + "spcPts", {"val": "100000"})
            if mode == "bullet":
                E.SubElement(props, A + "buChar", {"char": "\u2022"})
            if mode == "inherited":
                default = E.SubElement(props, A + "defRPr", {"sz": "100"})
                E.SubElement(E.SubElement(default, A + "solidFill"), A + "srgbClr", {"val": "FFFFFF"})
            if mode == "duplicate":
                paragraph.insert(0, E.Element(A + "pPr"))
                props.set("marL", "91440000")
            if mode == "paragraph-attribute":
                paragraph.set("unsupported", "1")
            else:
                paragraph.insert(0, props)
            with self.subTest(mode=mode):
                self.held(tree)

    def test_new_paragraph_repeats_only_exact_original_paragraph_layout(self):
        props = E.Element(A + "pPr", {"marL": "1000", "indent": "0", "algn": "l"})
        E.SubElement(E.SubElement(props, A + "lnSpc"), A + "spcPct", {"val": "100000"})
        teacher = fixture(paragraph_props=props)
        student = write(teacher, "Blood")
        body = student[1].find(P + "txBody")
        paragraph = E.SubElement(body, A + "p")
        paragraph.append(deepcopy(props))
        paragraph.append(run("A second observation"))
        result = assess(student, teacher)
        self.assertFalse(result["note"])
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
        self.assertIn("A second observation", result["items"][0]["answer"])
        changed = deepcopy(student)
        changed[1].findall(P + "txBody/" + A + "p")[-1].find(A + "pPr").set("marL", "1001")
        self.held(changed, teacher)

    def test_oversized_unknown_style_is_held_even_when_both_share_unknown_sentinel(self):
        key = "ppt/theme/theme1.xml"
        small = {key: b'<theme name="A"/>'}
        teacher = fixture(extras=small)
        result = assess(write(teacher, "Blood"), teacher, small, small)
        self.assertFalse(result["note"])
        self.assertEqual(result["counts"]["NOTES"]["done"], 1)
        self.held(write(teacher, "Blood"), teacher, {key: b'<theme name="B"/>'}, small)
        large = {key: b'<theme name="A">' + b' ' * 2_000_192 + b'</theme>'}
        large_changed = {key: large[key].replace(b'name="A"', b'name="B"')}
        teacher = fixture(extras=large)
        student = write(teacher, "Blood")
        parsed_teacher = c.read_deck(package(teacher, large))["slides"][0]
        parsed_student = c.read_deck(package(student, large_changed))["slides"][0]
        self.assertEqual(parsed_teacher["writing_scene"]["resources"], parsed_student["writing_scene"]["resources"])
        self.assertFalse(parsed_teacher["writing_scene"]["resources_readable"])
        self.held(student, teacher, large_changed, large)
        self.held(student, teacher, large, large)

    def test_scene_missing_duplicate_reordered_moved_opaque_or_unknown_cover_holds(self):
        for mode in ("missing", "duplicate", "order", "move", "cover", "unknown", "body", "group"):
            tree = write(fixture(), "Blood")
            if mode == "missing":
                tree.remove(tree[0])
            if mode == "duplicate":
                tree.append(deepcopy(tree[0]))
            if mode == "order":
                prompt = tree[0]; tree.remove(prompt); tree.append(prompt)
            if mode == "move":
                tree[1].find(P + "spPr/" + A + "xfrm/" + A + "off").set("x", "100001")
            if mode == "cover":
                cover = shape("cover", "", [0, 0, *SIZE]); props = cover.find(P + "spPr")
                props.remove(props.find(A + "noFill"))
                E.SubElement(E.SubElement(props, A + "solidFill"), A + "srgbClr", {"val": "FFFFFF"})
                tree.append(cover)
            if mode == "unknown":
                E.SubElement(tree, P + "unknownPayload")
            if mode == "body":
                tree[1].find(P + "txBody/" + A + "bodyPr").set("rot", "5400000")
            if mode == "group":
                field = tree[1]; tree.remove(field); E.SubElement(tree, P + "grpSp").append(field)
            self.held(tree)
        self.held(write(fixture(), "Blood"), extras={"ppt/theme/theme1.xml": b"<changed-theme/>"})


if __name__ == "__main__":
    unittest.main()
