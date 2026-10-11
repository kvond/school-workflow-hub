"""Fictional raw-PPTX controls for authenticated original graphic fields."""
from copy import deepcopy
import hashlib
import io
import json
import unittest
import zipfile
from xml.etree import ElementTree as E

from hub_v2 import completion as c, original_field_graphics as g, response_binding as rb, writing_fields as w
from test_original_writing_fields import package, run, shape

P, A, R, CT = c.P, c.A, g.R, g.CT
SIZE = (1000000, 750000)
CODE = "[[NOTES:F04:GRAPHIC]]"
OPTIONS = [{"label": label, "box": [100000, 150000 + n * 80000, 20000, 10000]}
           for n, label in enumerate("ABCD")]
CIRCLE = [700000, 500000, 200000, 40000]
PHOTO = [250000, 150000, 250000, 60000]
REGION = [200000, 100000, 600000, 450000]


def circle():
    node = shape("circle", "", CIRCLE)
    props = node.find(P + "spPr")
    E.SubElement(props, A + "prstGeom", {"prst": "ellipse"})
    props.remove(props.find(A + "ln"))
    line = E.SubElement(props, A + "ln", {"w": "38100"})
    E.SubElement(E.SubElement(line, A + "solidFill"), A + "srgbClr", {"val": "0000FF"})
    return node


def fixture(mode):
    tree = E.Element(P + "spTree")
    tree.append(shape("directions", "Supply your own response.", [10000, 10000, 900000, 50000]))
    field = circle() if mode == "moved-ellipse" else shape("photo", "Paste picture here", PHOTO)
    tree.append(field)
    tree.append(shape("other", "Type here", [100000, 650000, 750000, 40000]))
    box = CIRCLE if mode == "moved-ellipse" else PHOTO
    props = field.find(P + "txBody/" + A + "p/" + A + "r/" + A + "rPr")
    data_field = {"box": box, "given": "" if mode == "moved-ellipse" else "Paste picture here",
                  "pptx_surface_sha256": w.xml_surface(field, movable=mode == "moved-ellipse"),
                  "visible_styles": [w.run_style(props)], "default_styles": [w.run_style(None)],
                  "mode": mode, "source_key": "circle" if mode == "moved-ellipse" else "photo"}
    if mode == "moved-ellipse":
        data_field["options"] = deepcopy(OPTIONS)
    else:
        data_field.update(image_region=REGION, original_media=[])
    data = {"kind": w.KIND, "page_size": list(SIZE), "fields": [data_field],
            "page_fields": [{k: data_field[k] for k in ("box", "pptx_surface_sha256", "source_key", "mode")},
                            {"box": [100000, 650000, 750000, 40000], "pptx_surface_sha256": w.xml_surface(tree[2])}],
            "supplied_text": ["Paste picture here", "Supply your own response.", "Type here"]
            if mode == "new-profile-image" else ["Supply your own response.", "Type here"]}
    field.find(P + "txBody/" + A + "p").append(run(CODE, "100", "FFFFFF"))
    nv = field.find(P + "nvSpPr/" + P + "cNvPr")
    nv.set("descr", rb.PREFIX + json.dumps(data))
    parsed = c.read_deck(package(tree))["slides"][0]
    data.update(page_scene=parsed["writing_scene"]["shapes"], page_style=parsed["writing_scene"]["style"],
                page_media=parsed["writing_scene"]["media"], resources=parsed["writing_scene"]["resources"])
    nv.set("descr", rb.PREFIX + json.dumps(data))
    return tree


def selected(teacher, number):
    tree = deepcopy(teacher)
    off = tree[1].find(P + "spPr/" + A + "xfrm/" + A + "off")
    off.set("x", "10000")
    off.set("y", str(135000 + number * 80000))
    return tree


def picture(box=None):
    node = E.Element(P + "pic")
    nv = E.SubElement(node, P + "nvPicPr")
    E.SubElement(nv, P + "cNvPr", {"id": "1000", "name": "new-image"})
    E.SubElement(nv, P + "cNvPicPr")
    E.SubElement(nv, P + "nvPr")
    fill = E.SubElement(node, P + "blipFill")
    E.SubElement(fill, A + "blip", {R + "embed": "rImage"})
    E.SubElement(E.SubElement(fill, A + "stretch"), A + "fillRect")
    props = E.SubElement(node, P + "spPr")
    xf = E.SubElement(props, A + "xfrm")
    x, y, width, height = box or [250000, 200000, 400000, 300000]
    E.SubElement(xf, A + "off", {"x": str(x), "y": str(y)})
    E.SubElement(xf, A + "ext", {"cx": str(width), "cy": str(height)})
    E.SubElement(E.SubElement(props, A + "prstGeom", {"prst": "rect"}), A + "avLst")
    return node


def image_bytes(mode="visible", format="PNG"):
    from PIL import Image
    image = Image.new("RGBA", (64, 64))
    image.putdata([(x * 4, y * 4, (x + y) * 2, 0 if mode == "transparent" else 255)
                   if mode != "uniform" else (0, 0, 0, 255) for y in range(64) for x in range(64)])
    if mode == "nearly-blank":
        image.putdata([(x * 40, y * 40, 100, 255) if x < 2 and y < 2 else (255, 255, 255, 255)
                       for y in range(64) for x in range(64)])
    if format == "JPEG":
        image = image.convert("RGB")
    out = io.BytesIO(); image.save(out, format=format)
    return out.getvalue()


def image_parts(mode="visible", mime="image/png", format="PNG"):
    root = E.Element("{http://schemas.openxmlformats.org/package/2006/relationships}Relationships")
    E.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                 {"Id": "rImage", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", "Target": "../media/new.png"})
    types = E.Element(CT + "Types")
    E.SubElement(types, CT + "Default", {"Extension": "png", "ContentType": mime})
    return {"ppt/slides/_rels/slide1.xml.rels": E.tostring(root), "[Content_Types].xml": E.tostring(types),
            "ppt/media/new.png": image_bytes(mode, format)}


def assess(student, teacher, extras=None, teacher_extras=None):
    return c.assess(c.read_deck(package(student, extras)), c.read_deck(package(teacher, teacher_extras)), found_codes_only=True)


def raster_package(tree, extras):
    raw = package(tree, extras)
    with zipfile.ZipFile(io.BytesIO(raw)) as old:
        parts = {name: old.read(name) for name in old.namelist()}
    root = E.fromstring(parts["ppt/slides/slide1.xml"])
    background = E.Element(P + "bg")
    fill = E.SubElement(E.SubElement(background, P + "bgPr"), A + "blipFill")
    E.SubElement(fill, A + "blip", {R + "embed": "rImage"})
    E.SubElement(E.SubElement(fill, A + "stretch"), A + "fillRect")
    root.find(P + "cSld").insert(0, background)
    parts["ppt/slides/slide1.xml"] = E.tostring(root)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, value in parts.items(): archive.writestr(name, value)
    return out.getvalue()


def raster_teacher(extras):
    tree = fixture("moved-ellipse")
    nv = tree[1].find(P + "nvSpPr/" + P + "cNvPr")
    data = json.loads(nv.get("descr")[len(rb.PREFIX):])
    scene = c.read_deck(raster_package(tree, extras))["slides"][0]["writing_scene"]
    data.update(page_scene=scene["shapes"], page_style=scene["style"],
                page_media=scene["media"], resources=scene["resources"])
    nv.set("descr", rb.PREFIX + json.dumps(data))
    return tree


class OriginalGraphicFieldsTests(unittest.TestCase):
    def held(self, student, teacher, extras=None):
        result = assess(student, teacher, extras)
        self.assertTrue(result["note"]); self.assertIsNone(result["notes_score"])
        self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_original_parked_circle_blank_and_four_own_options_count_once(self):
        teacher = fixture("moved-ellipse")
        blank = assess(teacher, teacher)
        self.assertFalse(blank["note"]); self.assertEqual(blank["counts"]["NOTES"]["done"], 0)
        for n, label in enumerate("ABCD"):
            with self.subTest(label=label):
                result = assess(selected(teacher, n), teacher)
                self.assertFalse(result["note"])
                self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
                self.assertEqual(result["items"][0]["recorded_choice"], label)
                self.assertEqual(result["items"][0]["answer"], "")

    def test_original_raster_declared_mime_and_decoded_format_remain_source_bound(self):
        extras = image_parts(); teacher = raster_teacher(extras)
        template = c.read_deck(raster_package(teacher, extras))
        own = selected(teacher, 0)
        positive = c.assess(c.read_deck(raster_package(own, extras)), template, found_codes_only=True)
        self.assertFalse(positive["note"]); self.assertEqual(positive["counts"]["NOTES"]["done"], 1)
        for mode in ("wrong", "absent", "duplicate", "format"):
            changed = deepcopy(extras); types = E.fromstring(changed["[Content_Types].xml"])
            if mode == "wrong": types[0].set("ContentType", "application/octet-stream")
            if mode == "absent": types.remove(types[0])
            if mode == "duplicate": types.append(deepcopy(types[0]))
            if mode == "format": types[0].set("ContentType", "image/jpeg")  # actual bytes remain PNG
            changed["[Content_Types].xml"] = E.tostring(types)
            result = c.assess(c.read_deck(raster_package(own, changed)), template, found_codes_only=True)
            with self.subTest(mode=mode):
                self.assertTrue(result["note"]); self.assertIsNone(result["notes_score"])
                self.assertEqual(result["counts"]["NOTES"]["done"], 0)
            # Even a descriptor built from the same bad teacher bytes cannot
            # equate unknown declarations/decoded formats into trusted evidence.
            bad_teacher = raster_teacher(changed)
            paired = c.assess(c.read_deck(raster_package(selected(bad_teacher, 0), changed)),
                              c.read_deck(raster_package(bad_teacher, changed)), found_codes_only=True)
            with self.subTest(paired_source=mode):
                self.assertTrue(paired["note"]); self.assertIsNone(paired["notes_score"])

    def test_other_question_typed_letter_and_partial_or_multiple_selection_hold(self):
        teacher = fixture("moved-ellipse")
        for mode in ("typed", "other", "partial", "multiple", "offpage"):
            tree = selected(teacher, 0)
            off = tree[1].find(P + "spPr/" + A + "xfrm/" + A + "off")
            if mode == "typed": tree[1].find(P + "txBody/" + A + "p").append(run("A"))
            if mode == "other": off.set("y", "620000")
            if mode == "partial": off.set("x", "110000")
            if mode == "offpage": off.set("x", "-1")
            if mode == "multiple": tree[1].find(P + "spPr/" + A + "xfrm/" + A + "ext").set("cy", "160000")
            with self.subTest(mode=mode): self.held(tree, teacher)

    def test_circle_visibility_geometry_unknown_style_order_and_source_changes_hold(self):
        teacher = fixture("moved-ellipse")
        for mode in ("hidden", "alpha", "fill", "rotate", "resize", "unknown", "duplicate", "order", "descriptor", "teacher"):
            tree, current_teacher = selected(teacher, 0), deepcopy(teacher)
            props = tree[1].find(P + "spPr")
            if mode == "hidden": tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("hidden", "1")
            if mode == "alpha": E.SubElement(props.find(A + "ln/" + A + "solidFill/" + A + "srgbClr"), A + "alpha", {"val": "0"})
            if mode == "fill": E.SubElement(E.SubElement(props, A + "solidFill"), A + "srgbClr", {"val": "FFFFFF"})
            if mode == "rotate": props.find(A + "xfrm").set("rot", "1")
            if mode == "resize": props.find(A + "xfrm/" + A + "ext").set("cx", "200001")
            if mode == "unknown": E.SubElement(tree[1], P + "unknown")
            if mode == "duplicate": tree.append(deepcopy(tree[1]))
            if mode == "order": field = tree[1]; tree.remove(field); tree.append(field)
            if mode == "descriptor": tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("descr", rb.PREFIX + "{}")
            if mode == "teacher": current_teacher[0].find(P + "txBody/" + A + "p/" + A + "r/" + A + "t").text = "Changed authentic prompt"
            with self.subTest(mode=mode): self.held(tree, current_teacher)

    def test_stroke_touching_a_second_label_is_not_an_unambiguous_selection(self):
        teacher = fixture("moved-ellipse")
        nv = teacher[1].find(P + "nvSpPr/" + P + "cNvPr")
        data = json.loads(nv.get("descr")[len(rb.PREFIX):])
        data["fields"][0]["options"][1]["box"][1] = 185000
        nv.set("descr", rb.PREFIX + json.dumps(data))
        self.assertFalse(assess(teacher, teacher)["note"])
        tree = selected(teacher, 0)
        self.assertTrue(g.ellipse_contains([10000, 135000, 200000, 40000], data["fields"][0]["options"][0]["box"]))
        self.assertFalse(g.ellipse_contains([10000, 135000, 200000, 40000], data["fields"][0]["options"][1]["box"]))
        self.held(tree, teacher)

    def test_new_visible_png_and_jpeg_are_own_picture_attempt_not_answer_prose(self):
        teacher = fixture("new-profile-image")
        blank = assess(teacher, teacher)
        self.assertFalse(blank["note"]); self.assertEqual(blank["counts"]["NOTES"]["done"], 0)
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
            tree = deepcopy(teacher); tree.append(picture())
            result = assess(tree, teacher, image_parts(mime=mime, format=fmt))
            self.assertFalse(result["note"])
            self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})
            self.assertEqual(result["items"][0]["answer"], "")
            self.assertEqual(result["items"][0]["completion_evidence"], "New profile picture placed in the original response region")

    def test_picture_center_inside_original_placeholder_is_owned_response_not_cross_question(self):
        teacher = fixture("new-profile-image")
        tree = deepcopy(teacher); tree.append(picture([250000, 100000, 250000, 150000]))
        result = assess(tree, teacher, image_parts())
        self.assertFalse(result["note"])
        self.assertEqual(result["counts"]["NOTES"], {"done": 1, "required": 1})

    def test_picture_visibility_position_coverage_and_painter_order_hold(self):
        teacher = fixture("new-profile-image")
        for mode in ("transparent", "uniform", "nearly-blank", "outside", "tiny", "underlay", "duplicate", "crop", "opacity", "hidden", "rotate", "unknown", "geometry-attribute", "transform-child", "visibility-attribute"):
            tree = deepcopy(teacher); pic = picture(); tree.append(pic)
            extras = image_parts(mode if mode in ("transparent", "uniform", "nearly-blank") else "visible")
            if mode == "outside": pic.find(P + "spPr/" + A + "xfrm/" + A + "off").set("x", "700000")
            if mode == "tiny": pic.find(P + "spPr/" + A + "xfrm/" + A + "ext").set("cx", "100")
            if mode == "underlay": tree.remove(pic); tree.insert(0, pic)
            if mode == "duplicate": tree.append(deepcopy(pic))
            if mode == "crop": E.SubElement(pic.find(P + "blipFill"), A + "srcRect", {"l": "50000"})
            if mode == "opacity": E.SubElement(pic.find(P + "blipFill/" + A + "blip"), A + "alphaModFix", {"amt": "0"})
            if mode == "hidden": pic.find(P + "nvPicPr/" + P + "cNvPr").set("hidden", "1")
            if mode == "rotate": pic.find(P + "spPr/" + A + "xfrm").set("rot", "1")
            if mode == "unknown": E.SubElement(pic.find(P + "nvPicPr/" + P + "nvPr"), P + "unknown")
            if mode == "geometry-attribute": pic.find(P + "spPr/" + A + "prstGeom").set("unsupported", "1")
            if mode == "transform-child": E.SubElement(pic.find(P + "spPr/" + A + "xfrm"), A + "unknown")
            if mode == "visibility-attribute": pic.find(P + "nvPicPr/" + P + "cNvPr").set("unsupported", "1")
            with self.subTest(mode=mode): self.held(tree, teacher, extras)

    def test_image_mime_duplicate_relationship_and_authentic_scene_boundaries_hold(self):
        teacher = fixture("new-profile-image")
        for mode in ("mime", "undeclared", "duplicate-type", "duplicate-rel", "external", "stock", "original-changed", "prompt-changed", "unknown-cover"):
            tree = deepcopy(teacher); tree.append(picture())
            extras = image_parts()
            if mode in ("mime", "undeclared", "duplicate-type"):
                types = E.fromstring(extras["[Content_Types].xml"])
                if mode == "mime": types[0].set("ContentType", "image/jpeg")
                if mode == "undeclared": types.remove(types[0])
                if mode == "duplicate-type": types.append(deepcopy(types[0]))
                extras["[Content_Types].xml"] = E.tostring(types)
            if mode in ("duplicate-rel", "external"):
                rels = E.fromstring(extras["ppt/slides/_rels/slide1.xml.rels"])
                if mode == "duplicate-rel": rels.append(deepcopy(rels[0]))
                else: rels[0].set("TargetMode", "External")
                extras["ppt/slides/_rels/slide1.xml.rels"] = E.tostring(rels)
            if mode in ("stock", "original-changed"):
                nv = teacher[1].find(P + "nvSpPr/" + P + "cNvPr")
                data = json.loads(nv.get("descr")[len(rb.PREFIX):])
                data["fields"][0]["original_media"] = [["rImage", hashlib.sha256(extras["ppt/media/new.png"]).hexdigest() if mode == "stock" else "0" * 64]]
                nv.set("descr", rb.PREFIX + json.dumps(data)); tree[1].find(P + "nvSpPr/" + P + "cNvPr").set("descr", nv.get("descr"))
            if mode == "prompt-changed": tree[0].find(P + "txBody/" + A + "p/" + A + "r/" + A + "t").text = "Other question"
            if mode == "unknown-cover": E.SubElement(tree, P + "unknownPayload")
            with self.subTest(mode=mode): self.held(tree, teacher, extras)


if __name__ == "__main__": unittest.main()
