"""Fictional prospective source maps; no school content or external writes."""
from copy import deepcopy
from itertools import product
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

from PIL import Image
from hub_v2 import completion as c, native_teacher_source as n, response_binding as rb, google, combined_assessment as combined
from hub_v2 import mapped_text_binding as m, mapped_text_scene as s
from test_explicit_response_binding import shape, STYLE

P, A, R = c.P, c.A, c.R
FILE, COPY = "FictionalMappedTeacherSource001", "FictionalMappedStudentCopy0001"
SIZE = (9144000, 6858000)
WORDS = "Two checks show a clear bounded result."
READERS = ("native", "pptx")


def elements(value):
    def walk(rows):
        for row in rows:
            yield row
            yield from walk(row.get("elementGroup", {}).get("children", []))
    return [e for page in value["slides"] for e in walk(page["pageElements"])]


def element(value, key):
    return next(e for e in elements(value) if e["objectId"] == key)


def write(value, key, words=WORDS):
    target = element(value, key)
    before = "".join(e.get("textRun", {}).get("content", "") for e in target["shape"].get("text", {}).get("textElements", []))
    target["shape"]["text"] = {"textElements": [{"textRun": {"content": ((before.strip() + " ") if before.strip() else "") + words + "\n", "style": deepcopy(STYLE)}}]}


def source(pair=False, context=True):
    header = f"[[MARKER-INVENTORY:C99]] NOTES=1 DRAFT={int(pair)} OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=EXPLAIN DRAFT_IDS={'EXPLAIN' if pair else '-'} OPTIONAL_IDS=- BANK_IDS=-"
    rows = [shape("prompt", "Compare two invented observations.", [400000, 150000, 8000000, 300000]),
        shape("first_label", "Your first answer [[NOTES:C99:EXPLAIN]]", [400000, 800000, 3800000, 250000]),
        shape("first_answer", "", [400000, 1100000, 3800000, 3900000])]
    if pair:
        rows += [shape("revised_label", "Your revised answer [[DRAFT:C99:EXPLAIN]]", [4800000, 800000, 3800000, 250000]),
            shape("revised_answer", "", [4800000, 1100000, 3800000, 3900000])]
    if context:
        decoration = shape("decoration", "", [400000, 5200000, 300000, 300000]); decoration["shape"]["shapeType"] = "ROUND_RECTANGLE"
        decoration["shape"]["text"]["textElements"] = []
        child = shape("group_rectangle", "", [0, 0, 300000, 300000]); child["shape"]["text"]["textElements"] = []
        image = {"objectId": "context_picture", "size": {"width": {"magnitude": 300000, "unit": "EMU"}, "height": {"magnitude": 300000, "unit": "EMU"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 0, "unit": "EMU"},
            "image": {"contentUrl": "https://ephemeral.invalid/signed-teacher", "imageProperties": {"cropProperties": {}}}}
        group = {"objectId": "context_group", "transform": {"scaleX": 1, "scaleY": 1, "translateX": 1500000, "translateY": 5200000, "unit": "EMU"},
                 "elementGroup": {"children": [child, image]}}
        rows += [decoration, group]
    return {"presentationId": FILE, "revisionId": "fictional-native-revision", "pageSize": {"width": {"magnitude": SIZE[0], "unit": "EMU"}, "height": {"magnitude": SIZE[1], "unit": "EMU"}},
        "slides": [{"objectId": "inventory_page", "pageElements": [shape("inventory", header, [100000, 100000, 8500000, 400000])]},
                   {"objectId": "response_page", "pageElements": rows}]}


def metadata(value):
    return {"id": value["presentationId"], "name": "Fictional presentation", "mimeType": c.SLIDES, "trashed": False,
            "version": "7", "modifiedTime": "2026-11-05T00:00:00Z"}


def package(value, *, image_color="blue", theme="original", alias=False):
    out = io.BytesIO(); media = io.BytesIO(); Image.new("RGB", (3, 3), image_color).save(media, format="PNG")
    namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
    parts = {}; types = E.Element("{http://schemas.openxmlformats.org/package/2006/content-types}Types")
    ct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    E.SubElement(types, ct + "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    E.SubElement(types, ct + "Default", Extension="xml", ContentType="application/xml")
    E.SubElement(types, ct + "Default", Extension="png", ContentType="image/png")
    def override(name, kind): E.SubElement(types, ct + "Override", PartName="/" + name, ContentType="application/vnd.openxmlformats-officedocument." + kind + "+xml")
    override("ppt/presentation.xml", "presentationml.presentation.main")
    rel = "{" + namespace + "}"
    rootrels = E.Element(rel + "Relationships"); E.SubElement(rootrels, rel + "Relationship", Id="root", Type=R[1:-1] + "/officeDocument", Target="ppt/presentation.xml")
    parts["_rels/.rels"] = E.tostring(rootrels)
    presentation = E.Element(P + "presentation"); ids = E.SubElement(presentation, P + "sldIdLst")
    E.SubElement(presentation, P + "sldSz", cx=str(SIZE[0]), cy=str(SIZE[1]))
    prels = E.Element(rel + "Relationships")
    for number, page in enumerate(value["slides"], 1):
        override(f"ppt/slides/slide{number}.xml", "presentationml.slide")
        E.SubElement(ids, P + "sldId", {"id": str(255 + number), R + "id": "slide" + str(number)})
        E.SubElement(prels, rel + "Relationship", Id="slide" + str(number), Type=R[1:-1] + "/slide", Target=f"slides/slide{number}.xml")
        root = E.Element(P + "sld"); tree = E.SubElement(E.SubElement(root, P + "cSld"), P + "spTree")
        nvroot = E.SubElement(tree, P + "nvGrpSpPr"); E.SubElement(nvroot, P + "cNvPr", id="0", name="")
        E.SubElement(nvroot, P + "cNvGrpSpPr"); E.SubElement(nvroot, P + "nvPr"); E.SubElement(tree, P + "grpSpPr")
        slide_rels = E.Element(rel + "Relationships"); counter = [0]
        def walk(rows, parent):
            for item in rows:
                counter[0] += 1; ident = str(counter[0]); key = ("alias_" if alias else "") + item["objectId"]
                transform = item["transform"]
                x, y = transform.get("translateX", 0), transform.get("translateY", 0)
                if "elementGroup" in item:
                    node = E.SubElement(parent, P + "grpSp"); nv = E.SubElement(node, P + "nvGrpSpPr")
                    E.SubElement(nv, P + "cNvPr", id=ident, name=key)
                    E.SubElement(nv, P + "cNvGrpSpPr"); E.SubElement(nv, P + "nvPr")
                    xf = E.SubElement(E.SubElement(node, P + "grpSpPr"), A + "xfrm")
                    E.SubElement(xf, A + "off", x=str(round(x)), y=str(round(y)))
                    E.SubElement(xf, A + "ext", cx=str(round(1000000 * transform["scaleX"])), cy=str(round(600000 * transform["scaleY"])))
                    E.SubElement(xf, A + "chOff", x="0", y="0"); E.SubElement(xf, A + "chExt", cx="1000000", cy="600000")
                    walk(item["elementGroup"]["children"], node); continue
                picture = "image" in item; node = E.SubElement(parent, P + ("pic" if picture else "sp"))
                nv = E.SubElement(node, P + ("nvPicPr" if picture else "nvSpPr"))
                E.SubElement(nv, P + "cNvPr", id=ident, name=key, descr=item.get("description", ""))
                E.SubElement(nv, P + ("cNvPicPr" if picture else "cNvSpPr")); E.SubElement(nv, P + "nvPr")
                props = E.SubElement(node, P + "spPr"); xf = E.SubElement(props, A + "xfrm")
                E.SubElement(xf, A + "off", x=str(round(x)), y=str(round(y)))
                E.SubElement(xf, A + "ext", cx=str(round(item["size"]["width"]["magnitude"] * transform["scaleX"])), cy=str(round(item["size"]["height"]["magnitude"] * transform["scaleY"])))
                if picture:
                    image_name = "diagnostic_image.png" if alias else "image.png"
                    rid = "different-image-id" if alias else "image-id"
                    E.SubElement(slide_rels, rel + "Relationship", Id=rid, Type=R[1:-1] + "/image", Target="../media/" + image_name)
                    parts["ppt/media/" + image_name] = media.getvalue()
                    fill = E.SubElement(node, P + "blipFill"); E.SubElement(fill, A + "blip", {R + "embed": rid})
                    crop = item["image"].get("imageProperties", {}).get("cropProperties", {})
                    if crop: E.SubElement(fill, A + "srcRect", **{k: str(round(crop.get(v, 0) * 100000)) for k, v in (("l", "leftOffset"), ("r", "rightOffset"), ("t", "topOffset"), ("b", "bottomOffset"))})
                    E.SubElement(E.SubElement(fill, A + "stretch"), A + "fillRect")
                    node.remove(props); node.append(props); continue
                fill = item["shape"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]
                rgb = fill["color"]["rgbColor"]
                color = E.SubElement(E.SubElement(props, A + "solidFill"), A + "srgbClr", val="".join(f"{round(rgb.get(k,0)*255):02X}" for k in ("red", "green", "blue")))
                if fill.get("alpha", 1) != 1: E.SubElement(color, A + "alpha", val=str(round(fill["alpha"] * 100000)))
                E.SubElement(E.SubElement(props, A + "prstGeom", prst="roundRect" if item["shape"]["shapeType"] == "ROUND_RECTANGLE" else "rect"), A + "avLst")
                body = E.SubElement(node, P + "txBody"); E.SubElement(E.SubElement(body, A + "bodyPr", vertOverflow="clip", horzOverflow="clip"), A + "noAutofit"); E.SubElement(body, A + "lstStyle")
                paragraph = E.SubElement(body, A + "p")
                for entry in item["shape"].get("text", {}).get("textElements", []):
                    if "textRun" not in entry: continue
                    data = entry["textRun"]; style = data["style"]
                    run = E.SubElement(paragraph, A + "r"); rp = E.SubElement(run, A + "rPr", sz=str(round(style["fontSize"]["magnitude"]*100)))
                    E.SubElement(rp, A + "latin", typeface=style.get("fontFamily", "Arial"))
                    rgb = style.get("foregroundColor", {}).get("opaqueColor", {}).get("rgbColor", {})
                    E.SubElement(E.SubElement(rp, A + "solidFill"), A + "srgbClr", val="".join(f"{round(rgb.get(k,0)*255):02X}" for k in ("red", "green", "blue")))
                    E.SubElement(run, A + "t").text = data["content"].removesuffix("\n")
        walk(page["pageElements"], tree)
        parts[f"ppt/slides/slide{number}.xml"] = E.tostring(root)
        if len(slide_rels): parts[f"ppt/slides/_rels/slide{number}.xml.rels"] = E.tostring(slide_rels)
    override("ppt/theme/theme1.xml", "theme")
    parts["ppt/theme/theme1.xml"] = E.tostring(E.Element(A + "theme", name=theme))
    E.SubElement(prels, rel + "Relationship", Id="theme", Type=R[1:-1] + "/theme", Target="theme/theme1.xml")
    parts["ppt/presentation.xml"] = E.tostring(presentation); parts["ppt/_rels/presentation.xml.rels"] = E.tostring(prels)
    parts["[Content_Types].xml"] = E.tostring(types)
    with zipfile.ZipFile(out, "w") as archive:
        for key, data in parts.items(): archive.writestr(key, data)
    return out.getvalue()


def parsed_export(value, *, authoring=False, **kwargs):
    raw = package(value, **kwargs); parsed = c.read_deck(raw, explicit_authoring=authoring)
    return m.capture_export(parsed, metadata(value), metadata(value), raw)


def rewrite(raw, name, change):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as before, zipfile.ZipFile(out, "w") as after:
        for entry in before.namelist():
            data = before.read(entry)
            if entry == name:
                root = E.fromstring(data); change(root); data = E.tostring(root)
            after.writestr(entry, data)
    return out.getvalue()


def parse(value, mode, *, witness=True, **kwargs):
    if mode == "pptx": return parsed_export(value, **kwargs)
    parsed = n.read(value, presentation_id=value["presentationId"], revision="7", source_role="teacher" if value["presentationId"] == FILE else "student", explicit_authoring=True)
    return m.with_export_witness(parsed, parsed_export(value, **kwargs)) if witness else parsed


def prepared(pair=False, context=True):
    value = source(pair, context)
    native = parse(value, "native", witness=False)
    targets = [{"marker_key": "first_label", "target_key": "first_answer"}]
    if pair: targets.append({"marker_key": "revised_label", "target_key": "revised_answer"})
    data = m.author(native, parsed_export(value, authoring=True), targets=targets)
    element(value, "first_label")["description"] = rb.PREFIX + json.dumps(data)
    return value


def student(value, *keys, words=WORDS):
    result = deepcopy(value); result["presentationId"] = COPY
    if any("image" in e for e in elements(result)): element(result, "context_picture")["image"]["contentUrl"] = "https://ephemeral.invalid/signed-student"
    for key in keys: write(result, key, words)
    return result


class MappedTextTests(unittest.TestCase):
    def held(self, result):
        self.assertTrue(result["note"])
        self.assertIsNone(result["notes_score"]); self.assertIsNone(result["revision_score"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def reject(self, after, before, teacher, copy, **kwargs):
        try:
            submitted, original = parse(after, copy, **kwargs), parse(before, teacher)
        except ValueError:
            return  # unsupported capture cannot issue authority or a score
        self.held(c.assess(submitted, original))

    def test_single_and_pair_isolated_all_readers_and_policies(self):
        for pair, teacher, copy, found in product((False, True), READERS, READERS, (False, True)):
            before = prepared(pair)
            cases = [((), []), (("first_answer",), [("NOTES", "EXPLAIN")])]
            if pair: cases += [(("revised_answer",), [("DRAFT", "EXPLAIN")]), (("first_answer", "revised_answer"), [("NOTES", "EXPLAIN"), ("DRAFT", "EXPLAIN")])]
            for keys, expected in cases:
                with self.subTest(pair=pair, teacher=teacher, copy=copy, keys=keys):
                    actual = c.assess(parse(student(before, *keys), copy), parse(before, teacher), found_codes_only=found)
                    self.assertIsNone(actual["note"])
                    self.assertEqual([(i["category"], i["id"]) for i in actual["items"] if i["status"] == "done"], expected)
                    self.assertEqual(actual["counts"]["NOTES"]["required"], 1)
                    self.assertEqual(actual["counts"]["DRAFT"]["required"], int(pair))

    def test_non_target_edits_and_target_plus_context_never_supply_credit(self):
        before = prepared(True)
        for teacher, copy, key in product(READERS, READERS, ("prompt", "first_label", "revised_label", "decoration", "group_rectangle")):
            self.reject(student(before, key), before, teacher, copy)
            self.reject(student(before, "first_answer", key), before, teacher, copy)

    def test_missing_tampered_duplicate_unknown_and_student_only_metadata_hold(self):
        before = prepared(True)
        for variant, teacher, copy in product(("missing", "malformed", "duplicate", "unknown-version", "new-id", "forged-hash", "relocated", "missing-marker", "missing-target", "missing-page"), READERS, READERS):
            after = student(before, "first_answer"); carrier = element(after, "first_label")
            data = json.loads(carrier["description"][len(rb.PREFIX):])
            if variant == "missing": carrier.pop("description")
            if variant == "malformed": carrier["description"] = rb.PREFIX + '{"kind":"' + m.KIND + '",'
            if variant == "duplicate": carrier["description"] = rb.PREFIX + json.dumps(data)[:-1] + ',"kind":"' + m.KIND + '"}'
            if variant == "unknown-version": data["kind"] = "original-text-targets/v999"
            if variant == "new-id": data["responses"][0]["id"] = "OTHER"
            if variant == "forged-hash": data["scene_sha256"] = {"native": "0"*64, "pptx": "0"*64}
            if variant == "relocated": element(after, "prompt")["description"] = carrier.pop("description")
            if variant == "missing-marker": carrier["shape"]["text"]["textElements"][0]["textRun"]["content"] = "Your first answer\n"
            if variant == "missing-target": after["slides"][1]["pageElements"].remove(element(after, "first_answer"))
            if variant == "missing-page": after["slides"].pop()
            if variant in ("unknown-version", "new-id", "forged-hash"): carrier["description"] = rb.PREFIX + json.dumps(data)
            self.reject(after, before, teacher, copy)
        ordinary = source(True)
        for teacher, copy in product(READERS, repeat=2): self.reject(student(before, "first_answer"), ordinary, teacher, copy)

    def test_bookends_raw_exports_serialization_and_witness_lending_are_not_authority(self):
        before = prepared(); after = student(before, "first_answer")
        raw = package(after); parsed = c.read_deck(raw)
        meta = metadata(after)
        for mutation in ({"version": "8"}, {"id": FILE}, {"trashed": True}, {"modifiedTime": "changed"}, {"mimeType": "other"}):
            with self.assertRaises(ValueError): m.capture_export(parsed, meta, {**meta, **mutation}, raw)
        for mutation in ({"version": ""}, {"modifiedTime": ""}, {"trashed": True}):
            invalid = {**meta, **mutation}
            with self.assertRaises(ValueError): m.capture_export(parsed, invalid, invalid, raw)
        native = parse(after, "native", witness=False)
        with self.assertRaises(ValueError): m.with_export_witness(native, parsed)
        with self.assertRaises(ValueError): m.with_export_witness(native, parsed_export(before))
        good_source, good_copy = parse(before, "native"), parse(after, "native")
        for untrusted in (native, json.loads(json.dumps(good_copy))): self.held(c.assess(untrusted, good_source))
        for mode in READERS:
            source_deck, copy_deck = parse(before, mode), parse(after, mode)
            self.held(c.assess(json.loads(json.dumps(copy_deck)), source_deck))
            self.held(c.assess(copy_deck, json.loads(json.dumps(source_deck))))

    def test_raw_parsed_and_post_plan_mutations_invalidate_captures(self):
        before, after = prepared(), None
        after = student(before, "first_answer")
        original = parse(before, "native")
        for variant in ("raw-url", "raw-text", "parsed-text", "parsed-binding", "host-id"):
            current = parse(after, "native")
            if variant == "raw-url": element(current["native_source"], "context_picture")["image"]["contentUrl"] += "changed"
            if variant == "raw-text": write(current["native_source"], "prompt")
            if variant == "parsed-text": current["slides"][1]["units"][0]["text"] += WORDS
            if variant == "parsed-binding": current["slides"][1]["units"][1].pop("binding")
            if variant == "host-id": current["native_source_id"] = FILE
            self.held(c.assess(current, original))
        current = parse(after, "pptx"); teacher = parse(before, "pptx")
        references, hold = m.plan(current, teacher); self.assertFalse(hold)
        page = current["slides"][1]; page["units"][0]["text"] += WORDS
        markers = [x for x in c.markers(current) if x["slide"] == 2]
        assigned, _, basis = m.answers(markers, [], teacher["slides"][1], page, SIZE, c._plain, reference=references[2])
        self.assertEqual(basis, m.REVIEW); self.assertFalse(any(assigned.values()))

    def test_signed_url_and_diagnostic_alias_changes_preserve_actual_media_identity(self):
        before = prepared(); after = student(before, "first_answer")
        for teacher, copy in product(READERS, repeat=2):
            result = c.assess(parse(after, copy, alias=True), parse(before, teacher))
            self.assertIsNone(result["note"]); self.assertEqual(result["notes_score"], 10)
        self.assertNotIn("https://", element(before, "first_label")["description"])

    def test_media_substitution_crop_theme_ancestry_order_and_occlusion_hold(self):
        before = prepared(True)
        for teacher, copy, variant in product(READERS, READERS, ("media", "crop", "theme", "ancestry", "order", "cover", "extra", "group-target")):
            after = student(before, "first_answer"); options = {}
            if variant == "media": options["image_color"] = "red"
            if variant == "theme": options["theme"] = "changed"
            if variant == "crop": element(after, "context_picture")["image"]["imageProperties"]["cropProperties"] = {"leftOffset": .1}
            if variant == "ancestry":
                group = element(after, "context_group")
                group["elementGroup"]["children"] = [{"objectId": "new_parent", "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "EMU"}, "elementGroup": deepcopy(group["elementGroup"])}]
            if variant == "order": after["slides"][1]["pageElements"][-2:] = reversed(after["slides"][1]["pageElements"][-2:])
            if variant == "cover": element(after, "context_group")["transform"]["translateY"] = 1500000
            if variant == "extra": after["slides"][1]["pageElements"].append(shape("extra", "", [6000000, 5000000, 100000, 100000]))
            if variant == "group-target":
                target = element(after, "first_answer"); after["slides"][1]["pageElements"].remove(target)
                after["slides"][1]["pageElements"].append({"objectId": "response_group", "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "EMU"}, "elementGroup": {"children": [target]}})
            self.reject(after, before, teacher, copy, **options)

    def test_blank_without_fake_runs_inline_code_and_audited_small_target(self):
        for variant in ("blank-runless", "inline", "small"):
            value = source(False, False)
            if variant == "blank-runless": element(value, "first_answer")["shape"]["text"]["textElements"] = []
            if variant == "small":
                element(value, "first_answer")["size"] = {"width": {"magnitude": 400000, "unit": "EMU"}, "height": {"magnitude": 300000, "unit": "EMU"}}
            marker_key = "first_label"
            if variant == "inline":
                marker_key = "first_answer"
                element(value, "first_label")["shape"]["text"]["textElements"][0]["textRun"]["content"] = "Your first answer\n"
                element(value, "first_answer")["shape"]["text"]["textElements"][0]["textRun"]["content"] = "Type here [[NOTES:C99:EXPLAIN]]\n"
            data = m.author(parse(value, "native", witness=False), parsed_export(value, authoring=True), targets=[{"marker_key": marker_key, "target_key": "first_answer"}])
            element(value, marker_key)["description"] = rb.PREFIX + json.dumps(data)
            for teacher, copy in product(READERS, repeat=2):
                blank = c.assess(parse(student(value), copy), parse(value, teacher)); self.assertEqual(blank["notes_score"], 0)
                after = student(value, "first_answer", words="A")
                if variant == "small": element(after, "first_answer")["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontSize"]["magnitude"] = 10
                result = c.assess(parse(after, copy), parse(value, teacher))
                self.assertIsNone(result["note"]); self.assertEqual(result["notes_score"], 10)

    def test_current_echo_and_genuine_quotation_controls_use_existing_completion_rules(self):
        prompt = "Compare both groups and give reasons."
        # Establish this question as the unchanged current-page source first.
        ordinary = source(False, False)
        element(ordinary, "prompt")["shape"]["text"]["textElements"][0]["textRun"]["content"] = prompt + "\n"
        ordinary["slides"][1]["pageElements"].append(shape("printed_question_choice", "Why does it differ?", [400000, 6000000, 6000000, 300000]))
        data = m.author(parse(ordinary, "native", witness=False), parsed_export(ordinary, authoring=True), targets=[{"marker_key": "first_label", "target_key": "first_answer"}])
        element(ordinary, "first_label")["description"] = rb.PREFIX + json.dumps(data)
        for teacher, copy in product(READERS, repeat=2):
            copied = c.assess(parse(student(ordinary, "first_answer", words=prompt), copy), parse(ordinary, teacher))
            self.assertIsNone(copied["note"]); self.assertEqual(copied["notes_score"], 0)
            for words in (prompt + " I agree.", "Why does it differ?"):
                result = c.assess(parse(student(ordinary, "first_answer", words=words), copy), parse(ordinary, teacher))
                self.assertIsNone(result["note"]); self.assertEqual(result["notes_score"], 10)

    def test_normal_and_combined_readers_use_actual_metadata_bookends(self):
        before = prepared(True); after = student(before, "first_answer")
        for value in (before, after):
            raw, meta = package(value), metadata(value)
            with patch.object(google, "deck_bytes", return_value=raw), patch.object(google, "drive_file", return_value=meta) as bookend:
                ordinary = c._read(None, meta["id"], meta)
                self.assertEqual(m._capture(ordinary).file_id, meta["id"])
                bookend.assert_called_once()
            with patch.object(google, "deck_bytes", return_value=raw), patch.object(google, "drive_file", return_value={**meta, "version": "8"}):
                with self.assertRaises(c.Unreadable): c._read(None, meta["id"], meta)
            with patch.object(google, "deck_bytes", return_value=raw), patch.object(google, "drive_file", return_value=meta):
                capture = combined._capture_source(None, meta["id"], meta)
                self.assertEqual(m._capture(capture["deck"]).file_id, meta["id"])
            with patch.object(google, "deck_bytes", return_value=raw), patch.object(google, "drive_file", return_value={**meta, "version": "8"}):
                with self.assertRaises(combined.Held): combined._capture_source(None, meta["id"], meta)
        office = {**metadata(after), "mimeType": google.PPTX, "md5Checksum": "0"*32, "size": str(len(raw))}
        with self.assertRaises(ValueError): m.capture_export(c.read_deck(raw), office, office, raw)

    def test_normal_host_all_formats_policies_and_not_ready_before_assessment(self):
        before = prepared(True)
        formid = "FictionalMappedAssignment001"
        for teacher, copy, class_key, key, held in product(READERS, READERS, ("anatomy", "biology"),
                (None, "first_answer", "revised_answer", "prompt", "decoration"), (False, True)):
            label = "Fictional photosynthesis task" if class_key == "biology" else "Fictional task"
            form = {"form_id": formid, "label": label,
                    "grade_readiness": {"status": "NOT_READY", "problem": "Fictional assignment hold"} if held else {"status": "READY"},
                    "students": [{"files": [{"id": COPY, "mime": c.SLIDES}]}]}
            lesson = {"topic": label, "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + FILE + "/edit"}]}
            agenda = SimpleNamespace(classes={class_key: {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
            original = parse(before, teacher)
            try: submitted = parse(student(before, *([key] if key else [])), copy)
            except ValueError: submitted = None  # native export witness refuses the changed scene
            if submitted: self.assertEqual(c.readiness(submitted)["status"], "READY")
            self.assertEqual(c.student_coding_required(agenda, class_key, "W03", formid), class_key == "biology")
            with patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted,
                    side_effect=c.Unreadable("Fictional changed export witness") if submitted is None else None), \
                    patch.object(c, "_teacher_template", return_value=(metadata(before), original)), \
                    patch.object(google, "drive_file", return_value=metadata(student(before))), patch.object(c, "assess", wraps=c.assess) as spy:
                result = c.score_file(None, agenda, class_key, "W03", formid, COPY, {"lessons": [lesson]})
            with self.subTest(teacher=teacher, copy=copy, policy=class_key, key=key, held=held):
                self.assertEqual(spy.call_count, 0 if held or submitted is None else 1)
                if held or submitted is None:
                    self.assertEqual(result["code"], "deck_not_ready"); self.assertIsNone(result.get("notes_score"))
                elif key in ("prompt", "decoration"): self.held(result)
                else:
                    self.assertIsNone(result["note"])
                    self.assertEqual((result["notes_score"], result["revision_score"]),
                                     (10 if key == "first_answer" else 0, 10 if key == "revised_answer" else 0))
                    self.assertEqual(result["counts"]["NOTES"]["required"], 1)
                    self.assertEqual(result["counts"]["DRAFT"]["required"], 1)

    def test_combined_host_keeps_exact_category_and_context_holds(self):
        before = prepared(True)
        for teacher, copy, key in product(READERS, READERS, (None, "first_answer", "revised_answer", "prompt", "decoration")):
            originals = {"C99": {"metadata": metadata(before), "sha256": "a"*64, "deck": parse(before, teacher)}}
            try: submitted = parse(student(before, *([key] if key else [])), copy)
            except ValueError: submitted = None
            copies = {"C99": {"metadata": metadata(student(before)), "sha256": "b"*64, "deck": submitted}}
            form = {"combined_assessment": {"scope": {"components": [{"cycle": "C99", "label": "Fictional task", "teacher_id": FILE}]}}}
            row = {"combined_components": [{"cycle": "C99", "file_id": COPY}]}
            with patch.object(combined, "verify_sources", return_value=(originals, copies),
                    side_effect=combined.Held("Fictional changed export witness") if submitted is None else None), \
                    patch.object(c, "assess", wraps=c.assess) as spy:
                result = combined.score_row(None, form, row)
            self.assertEqual(spy.call_count, 0 if submitted is None else 1)
            if submitted is None:
                self.assertEqual(result["code"], "coverage_incomplete"); self.assertIsNone(result.get("notes_score"))
            elif key in ("prompt", "decoration"): self.held(result)
            else:
                self.assertIsNone(result["note"])
                self.assertEqual((result["notes_score"], result["revision_score"]),
                                 (10 if key == "first_answer" else 0, 10 if key == "revised_answer" else 0))

    def test_export_visibility_overflow_effects_animation_and_external_resources_reject(self):
        before = prepared(True); after = student(before, "first_answer")
        def target(root, key="first_answer"):
            return next(node for node in root.iter(P + "sp") if node.find(P + "nvSpPr/" + P + "cNvPr").get("name") == key)
        cases = [
            lambda root: E.SubElement(root, P + "timing"),
            lambda root: target(root).find(P + "nvSpPr/" + P + "cNvPr").set("hidden", "1"),
            lambda root: E.SubElement(target(root), P + "style"),
            lambda root: E.SubElement(target(root).find(P + "spPr"), A + "effectLst"),
            lambda root: target(root).find(P + "txBody/" + A + "bodyPr").set("vertOverflow", "overflow"),
            lambda root: target(root, "prompt").find(P + "txBody/" + A + "bodyPr").attrib.pop("horzOverflow"),
            lambda root: target(root).find(P + "txBody/" + A + "p/" + A + "r/" + A + "rPr/" + A + "latin").set("typeface", "+mn-lt"),
            lambda root: E.SubElement(target(root).find(P + "txBody/" + A + "p/" + A + "r/" + A + "rPr/" + A + "solidFill/" + A + "srgbClr"), A + "alpha", val="0"),
            lambda root: root.find(".//" + P + "grpSp/" + P + "grpSpPr/" + A + "xfrm").set("rot", "60000"),
        ]
        for mutate in cases:
            raw = rewrite(package(after), "ppt/slides/slide2.xml", mutate)
            with self.assertRaises(ValueError): m.capture_export(c.read_deck(raw), metadata(after), metadata(after), raw)
        def external(root):
            list(root)[0].set("TargetMode", "External"); list(root)[0].set("Target", "https://fictional.invalid/image.png")
        raw = rewrite(package(after), "ppt/slides/_rels/slide2.xml.rels", external)
        with self.assertRaises(ValueError): m.capture_export(c.read_deck(raw), metadata(after), metadata(after), raw)

    def test_duplicate_layout_branches_unknown_relationships_and_clipped_hidden_text_reject(self):
        before = prepared(True); after = student(before, "first_answer")
        def target(root):
            return next(node for node in root.iter(P + "sp") if node.find(P + "nvSpPr/" + P + "cNvPr").get("name") == "first_answer")
        def duplicate(root, path, child):
            parent = target(root).find(path); parent.append(deepcopy(parent.find(child)))
        cases = [
            lambda root: duplicate(root, P + "spPr", A + "xfrm"),
            lambda root: duplicate(root, P + "txBody", A + "bodyPr"),
            lambda root: duplicate(root, P + "txBody", A + "lstStyle"),
            lambda root: root.find(P + "cSld/" + P + "spTree").append(deepcopy(root.find(P + "cSld/" + P + "spTree/" + P + "grpSpPr"))),
            lambda root: target(root).find(P + "spPr/" + A + "xfrm").append(E.Element(A + "off", x="0", y="0")),
        ]
        for mutate in cases:
            raw = rewrite(package(before), "ppt/slides/slide2.xml", mutate)
            with self.assertRaises(ValueError):
                # Malformed originals cannot acquire authoring authority.
                m.capture_export(c.read_deck(raw, explicit_authoring=True), metadata(before), metadata(before), raw)
        def wrong_type(root): list(root)[0].set("Type", "https://fictional.invalid/image")
        raw = rewrite(package(after), "ppt/slides/_rels/slide2.xml.rels", wrong_type)
        with self.assertRaises(ValueError): m.capture_export(c.read_deck(raw), metadata(after), metadata(after), raw)
        def leading_paragraphs(root):
            body = target(root).find(P + "txBody")
            for _ in range(100):
                paragraph = E.Element(A + "p"); style = E.SubElement(paragraph, A + "endParaRPr", sz="1800")
                E.SubElement(style, A + "latin", typeface="Arial")
                E.SubElement(E.SubElement(style, A + "solidFill"), A + "srgbClr", val="000000")
                body.insert(2, paragraph)
        raw = rewrite(package(after), "ppt/slides/slide2.xml", leading_paragraphs)
        captured = m.capture_export(c.read_deck(raw), metadata(after), metadata(after), raw)
        self.held(c.assess(captured, parsed_export(before)))
        for text in (" " * 1000 + "A", "A " * 1000, "\t" + WORDS, "\n" + WORDS, "\u200b" + WORDS, "\u202e" + WORDS):
            bad = student(before, "first_answer", words=text)
            for teacher, copy in product(READERS, repeat=2): self.reject(bad, before, teacher, copy)

    def test_long_immutable_context_does_not_need_target_fit_but_oversized_givens_do(self):
        value = source(False, False)
        element(value, "prompt")["shape"]["text"]["textElements"][0]["textRun"]["content"] = ("Unchanged context " * 100) + "\n"
        data = m.author(parse(value, "native", witness=False), parsed_export(value, authoring=True), targets=[{"marker_key": "first_label", "target_key": "first_answer"}])
        element(value, "first_label")["description"] = rb.PREFIX + json.dumps(data)
        for teacher, copy in product(READERS, repeat=2):
            self.assertEqual(c.assess(parse(student(value, "first_answer"), copy), parse(value, teacher))["notes_score"], 10)
        oversized = source(False, False)
        write(oversized, "first_answer", "A " * 1000)
        with self.assertRaises(ValueError):
            m.author(parse(oversized, "native", witness=False), parsed_export(oversized, authoring=True), targets=[{"marker_key": "first_label", "target_key": "first_answer"}])

    def test_parser_raw_proof_replacement_cannot_borrow_another_scene(self):
        before = prepared(True); after = student(before, "first_answer")
        good = parsed_export(after)
        altered = deepcopy(after)
        element(altered, "group_rectangle")["shape"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"] = {"red": .9, "green": .9, "blue": .9}
        changed = parsed_export(altered)
        self.held(c.assess(changed, parsed_export(before)))
        # Parsed text/geometry alone cannot reveal this grouped paint change.
        changed["slides"][1]["units"]._mapped_proof = good["slides"][1]["units"]._mapped_proof
        self.held(c.assess(changed, parsed_export(before)))
        with self.assertRaises(ValueError): m._capture(changed)
        references, held = m.plan(good, parsed_export(before)); self.assertFalse(held)
        good["slides"][1]["units"]._mapped_proof = parsed_export(altered)["slides"][1]["units"]._mapped_proof
        assigned, _, basis = m.answers(c.markers(good)[-2:], [], references[2].page, good["slides"][1], good["size"], c._plain, reference=references[2])
        self.assertEqual(basis, m.REVIEW); self.assertFalse(any(assigned.values()))

    def test_inherited_defaults_and_deep_raw_structure_cannot_authorize_rendering(self):
        value = source(True)
        def inherited(root):
            defaults = E.SubElement(root, P + "defaultTextStyle")
            E.SubElement(defaults, A + "lvl1pPr", marL="9000000", indent="0")
        raw = rewrite(package(value), "ppt/presentation.xml", inherited)
        with self.assertRaises(ValueError):
            m.capture_export(c.read_deck(raw, explicit_authoring=True), metadata(value), metadata(value), raw)
        def object_defaults(root): E.SubElement(E.SubElement(root, A + "objectDefaults"), A + "spDef")
        raw = rewrite(package(value), "ppt/theme/theme1.xml", object_defaults)
        with self.assertRaises(ValueError):
            m.capture_export(c.read_deck(raw, explicit_authoring=True), metadata(value), metadata(value), raw)
        def media_type(root):
            next(n for n in root if n.get("Extension") == "png").set("ContentType", "image/jpeg")
        raw = rewrite(package(value), "[Content_Types].xml", media_type)
        with self.assertRaises(ValueError):
            m.capture_export(c.read_deck(raw, explicit_authoring=True), metadata(value), metadata(value), raw)
        def nested(root):
            node = root
            for _ in range(60): node = E.SubElement(node, A + "ext")
        raw = rewrite(package(value), "ppt/slides/slide2.xml", nested)
        with self.assertRaises(c.Unreadable): c.read_deck(raw, explicit_authoring=True)
        raw = rewrite(package(value), "ppt/theme/theme1.xml", nested)
        with self.assertRaises(ValueError):
            m.capture_export(c.read_deck(raw, explicit_authoring=True), metadata(value), metadata(value), raw)
        opted = prepared()
        element(opted, "first_label")["description"] = rb.PREFIX + '{"kind":"original-text-targets/v1","nested":' + '[' * 1500 + '0' + ']' * 1500 + '}'
        parsed = c.read_deck(package(opted))
        self.held(c.assess(parsed, parsed_export(prepared())))

    def test_own_canonical_role_echoes_are_copied_with_exact_denominators(self):
        for teacher, copy, key, label in product(READERS, READERS,
                ("first_answer", "revised_answer"), ("plain", "numbered")):
            value = source(True)
            first = "Your first answer" if label == "plain" else "Question 2 - Your first answer."
            revised = "Your revised answer" if label == "plain" else "Your revised answer - Question 2."
            element(value, "first_label")["shape"]["text"]["textElements"][0]["textRun"]["content"] = first + " [[NOTES:C99:EXPLAIN]]\n"
            element(value, "revised_label")["shape"]["text"]["textElements"][0]["textRun"]["content"] = revised + " [[DRAFT:C99:EXPLAIN]]\n"
            data = m.author(parse(value, "native", witness=False), parsed_export(value, authoring=True),
                            targets=[{"marker_key": "first_label", "target_key": "first_answer"},
                                     {"marker_key": "revised_label", "target_key": "revised_answer"}])
            element(value, "first_label")["description"] = rb.PREFIX + json.dumps(data)
            supplied = first if key == "first_answer" else revised
            # Use compact 10-point text for the longer numbered copied label.
            for echoed in (supplied.upper(), supplied + ".", supplied + "!!!", '"' + supplied + '"', "'" + supplied + "'"):
                after = student(value, key, words=echoed)
                element(after, key)["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontSize"]["magnitude"] = 10
                result = c.assess(parse(after, copy), parse(value, teacher))
                self.assertIsNone(result["note"])
                self.assertEqual((result["notes_score"], result["revision_score"]), (0, 0))
                own = next(i for i in result["items"] if i["category"] == ("NOTES" if key == "first_answer" else "DRAFT"))
                self.assertEqual(own["status"], "copied")
                self.assertEqual(result["counts"]["NOTES"]["required"], 1); self.assertEqual(result["counts"]["DRAFT"]["required"], 1)
            quoted = student(value, key, words=supplied + " I disagree.")
            element(quoted, key)["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontSize"]["magnitude"] = 10
            actual = c.assess(parse(quoted, copy), parse(value, teacher))
            self.assertIsNone(actual["note"])
            self.assertEqual(actual["notes_score"] if key == "first_answer" else actual["revision_score"], 10)

    def test_partial_given_echo_and_printed_short_question_choices_stay_distinct(self):
        value = source(True)
        element(value, "first_answer")["shape"]["text"]["textElements"][0]["textRun"]["content"] = "Your\n"
        value["slides"][1]["pageElements"].append(shape("question_choice", "Why does it differ?", [400000, 6000000, 6000000, 300000]))
        data = m.author(parse(value, "native", witness=False), parsed_export(value, authoring=True),
                        targets=[{"marker_key": "first_label", "target_key": "first_answer"},
                                 {"marker_key": "revised_label", "target_key": "revised_answer"}])
        element(value, "first_label")["description"] = rb.PREFIX + json.dumps(data)
        for teacher, copy in product(READERS, repeat=2):
            blank = c.assess(parse(student(value), copy), parse(value, teacher))
            self.assertEqual((blank["notes_score"], blank["revision_score"]), (0, 0))
            echo = c.assess(parse(student(value, "first_answer", words="first answer"), copy), parse(value, teacher))
            self.assertEqual(echo["notes_score"], 0); self.assertEqual(echo["items"][0]["status"], "copied")
            for key in ("first_answer", "revised_answer"):
                # The short choice is actually printed on this original page.
                chosen = c.assess(parse(student(value, key, words="Why does it differ?"), copy), parse(value, teacher))
                self.assertIsNone(chosen["note"])
                self.assertEqual(chosen["notes_score"] if key == "first_answer" else chosen["revision_score"], 10)

    def test_own_echo_requires_full_identity_category_separate_target_and_canonical_role(self):
        value = prepared(True)
        original = parsed_export(value); page = original["slides"][1]
        row = deepcopy(m.bound(page)[0]["binding"]["responses"][0])
        current = next(u for u in page["units"] if u["key"] == "first_answer")
        self.assertTrue(m._own_label_echo(row, page, current, "Your first answer", c._plain))
        for key, replacement in (("category", "DRAFT"), ("cycle", "OTHER"), ("id", "OTHER")):
            altered = {**row, key: replacement}
            self.assertFalse(m._own_label_echo(altered, page, current, "Your first answer", c._plain))
        same = deepcopy(row); same["target"]["box"] = same["marker_box"]
        self.assertFalse(m._own_label_echo(same, page, current, "Your first answer", c._plain))
        for prompt in ("Why does it differ?", "Question 2 - Your first answer - Question 3"):
            altered = deepcopy(page)
            next(u for u in altered["units"] if u["key"] == "first_label")["text"] = prompt + " [[NOTES:C99:EXPLAIN]]"
            self.assertFalse(m._own_label_echo(row, altered, current, prompt, c._plain))

    def test_own_echo_normal_and_combined_hosts_and_assignment_holds(self):
        value = prepared(True); formid = "FictionalOwnEchoAssignment001"
        for teacher, copy, key, held in product(READERS, READERS, ("first_answer", "revised_answer"), (False, True)):
            words = "Your first answer" if key == "first_answer" else "Your revised answer"
            original, submitted = parse(value, teacher), parse(student(value, key, words=words), copy)
            form = {"form_id": formid, "label": "Fictional task", "grade_readiness":
                    {"status": "NOT_READY", "problem": "Fictional hold"} if held else {"status": "READY"},
                    "students": [{"files": [{"id": COPY, "mime": c.SLIDES}]}]}
            lesson = {"topic": "Fictional task", "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + FILE + "/edit"}]}
            agenda = SimpleNamespace(classes={"anatomy": {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
            with patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted), \
                    patch.object(c, "_teacher_template", return_value=(metadata(value), original)), \
                    patch.object(google, "drive_file", return_value=metadata(student(value))), patch.object(c, "assess", wraps=c.assess) as spy:
                actual = c.score_file(None, agenda, "anatomy", "W03", formid, COPY, {"lessons": [lesson]})
            self.assertEqual(spy.call_count, 0 if held else 1)
            if held: self.assertEqual(actual["code"], "deck_not_ready")
            else:
                self.assertIsNone(actual["note"]); self.assertEqual((actual["notes_score"], actual["revision_score"]), (0, 0))
            originals = {"C99": {"metadata": metadata(value), "sha256": "a"*64, "deck": original}}
            copies = {"C99": {"metadata": metadata(student(value)), "sha256": "b"*64, "deck": submitted}}
            configured = {"combined_assessment": {"scope": {"components": [{"cycle": "C99", "label": "Fictional task", "teacher_id": FILE}]}}}
            row = {"combined_components": [{"cycle": "C99", "file_id": COPY}]}
            with patch.object(combined, "verify_sources", return_value=(originals, copies)):
                combined_result = combined.score_row(None, configured, row)
            self.assertIsNone(combined_result["note"])
            self.assertEqual((combined_result["notes_score"], combined_result["revision_score"]), (0, 0))

    def test_native_unresolved_styles_effects_geometry_and_unsupported_targets_reject(self):
        for variant in ("fractional", "shadow", "theme", "opacity", "rotated", "grouped", "table", "overlap", "extra-code", "duplicate-box"):
            value = source(True)
            target = element(value, "first_answer")
            if variant == "fractional": target["transform"]["translateX"] += .3
            if variant == "shadow": target["shape"]["shapeProperties"]["shadow"] = {"propertyState": "RENDERED"}
            if variant == "theme": element(value, "first_label")["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontFamily"] = "+mn-lt"
            if variant == "opacity": target["shape"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["alpha"] = .2
            if variant == "rotated": target["transform"]["shearX"] = .1
            if variant == "grouped":
                value["slides"][1]["pageElements"].remove(target); element(value, "context_group")["elementGroup"]["children"].append(target)
            if variant == "table": target["table"] = {}; target.pop("shape")
            if variant == "overlap": element(value, "context_group")["transform"]["translateY"] = 1000000
            if variant == "extra-code": write(value, "prompt", "[[NOTES:C99:OTHER]]")
            if variant == "duplicate-box":
                duplicate = deepcopy(target); duplicate["objectId"] = "same-box"; value["slides"][1]["pageElements"].append(duplicate)
            with self.subTest(variant=variant), self.assertRaises((ValueError, n.Unreadable)):
                # Export is the unchanged original; no source disagreement can
                # manufacture authoring evidence for a changed native scene.
                m.author(parse(value, "native", witness=False), parsed_export(source(True), authoring=True),
                         targets=[{"marker_key": "first_label", "target_key": "first_answer"},
                                  {"marker_key": "revised_label", "target_key": "revised_answer"}])


if __name__ == "__main__": unittest.main()
