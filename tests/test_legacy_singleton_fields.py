"""Invented scaled singleton-label fields; no school data or external writes."""
from copy import deepcopy
from itertools import product
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

from hub_v2 import completion as c, combined_assessment as combined, google, native_teacher_source as n
from test_explicit_response_binding import export, STYLE

SIZE = (9144000, 6858000)
FILE = "synthetic_reproduction"
TAG = "[[NOTES:TEST:ITEM]]"
WORDS = "I used two observations to support a bounded conclusion."
READERS = ("native", "pptx")
ICONS = tuple("synthetic_icon_" + str(i) for i in range(3))


def shape(key, y, sx, sy, *, text=None, color=None, kind="RECTANGLE"):
    rgb = color or {"red": .9411765, "green": .9411765, "blue": .9411765}
    properties = {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": rgb}, "alpha": 1}},
        "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": rgb}, "alpha": 1}},
            "weight": {"magnitude": 9525, "unit": "EMU"}, "dashStyle": "SOLID"},
        "shadow": {"type": "OUTER", "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
            "alignment": "BOTTOM_LEFT", "blurRadius": {"unit": "EMU"}, "color": {"rgbColor": {}},
            "alpha": 1, "rotateWithShape": False, "propertyState": "NOT_RENDERED"},
        "contentAlignment": "MIDDLE", "autofit": {"autofitType": "NONE", "fontScale": 1}}
    result = {"objectId": key, "size": {"width": {"magnitude": 3000000, "unit": "EMU"},
        "height": {"magnitude": 3000000, "unit": "EMU"}},
        "transform": {"scaleX": sx, "scaleY": sy, "translateX": 548640, "translateY": y, "unit": "EMU"},
        "shape": {"shapeType": kind, "shapeProperties": properties}}
    if text is not None:
        result["shape"]["text"] = {"textElements": [{"textRun": {"content": text + "\n", "style": {"fontSize": {"magnitude": 10, "unit": "PT"}}}}]}
    return result


def source():
    colors = ((.7529412, .22352941, .16862746), (.9372549, .8745098, .52156866), (.11764706, .5176471, .28627452))
    elements = [shape(key, y, .06096, .06096, kind="ROUND_RECTANGLE", color=dict(zip(("red", "green", "blue"), rgb)))
        for key, y, rgb in zip(ICONS, (1171000, 2085400, 3091240), colors)]
    label = shape("synthetic_label", 4937760, 2.6822399999999997, .1524,
        text="Working space: explain your reasoning. " + TAG, kind="TEXT_BOX", color={"red": 1, "green": 1, "blue": 1})
    properties = label["shape"]["shapeProperties"]
    properties["shapeBackgroundFill"]["propertyState"] = "NOT_RENDERED"
    properties["outline"]["propertyState"] = "NOT_RENDERED"
    properties["outline"]["outlineFill"]["solidFill"]["color"]["rgbColor"] = {}
    properties["contentAlignment"] = "TOP"
    response = shape("synthetic_response", 5486400, 2.6822399999999997, .28956)
    response["shape"]["shapeProperties"]["outline"]["outlineFill"]["solidFill"]["color"]["rgbColor"] = {k: .8 for k in ("red", "green", "blue")}
    elements += [label, response]
    return {"presentationId": FILE, "revisionId": "synthetic_1",
        "pageSize": {"width": {"magnitude": SIZE[0], "unit": "EMU"}, "height": {"magnitude": SIZE[1], "unit": "EMU"}},
        "slides": [{"objectId": "synthetic_page", "pageElements": elements}]}


def footer_source():
    prompt = shape("invented_prompt", 1508760, 2.6822399999999997, .27432,
        text="A sample model reveals ____ but not ____ without another observation. [[NOTES:DEMO:CHECK-RULE]]", kind="TEXT_BOX", color={"red": 1, "green": 1, "blue": 1})
    prompt["shape"]["text"]["textElements"][0]["textRun"]["style"] = {"fontFamily": "Arial", "fontSize": {"magnitude": 18, "unit": "PT"}}
    prompt["shape"]["shapeProperties"]["autofit"]["autofitType"] = "SHAPE_AUTOFIT"
    answer = shape("invented_answer", 2377440, 2.6822399999999997, .57912)
    answer["shape"]["shapeProperties"]["outline"]["outlineFill"]["solidFill"]["color"]["rgbColor"] = {k: .8 for k in ("red", "green", "blue")}
    footer = shape("invented_footer", 6455664, 2.7432, .09144, kind="TEXT_BOX", color={"red": 1, "green": 1, "blue": 1})
    footer["transform"]["translateX"] = 457200
    for e in (prompt, footer):
        p = e["shape"]["shapeProperties"]
        p["shapeBackgroundFill"]["propertyState"] = p["outline"]["propertyState"] = "NOT_RENDERED"
        p["outline"]["outlineFill"]["solidFill"]["color"]["rgbColor"] = {}
        p["contentAlignment"] = "TOP"
    value = source()
    value.update(presentationId="invented_response_example", title="Invented footer attribution example", revisionId="invented_revision_1")
    value["slides"] = [{"objectId": "invented_slide", "pageElements": [prompt, answer, footer]}]
    return value


# Complete invented OPC graph from the supplied footer reproduction, using
# readable namespace aliases. Shape
# paint and the far footer gap are preserved instead of using the first toy
# exporter's fill summary. Student mutation changes only the named txBody.
FOOTER_PARTS = {
    "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>',
    "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
    "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
    "ppt/_rels/presentation.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
    "ppt/slides/slide1.xml": '''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
<p:sp><p:nvSpPr><p:cNvPr id="6" name="invented_prompt"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="548640" y="1508760"/><a:ext cx="8046720" cy="822960"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="1800"/><a:t>A sample model reveals ____ but not ____ without another observation. [[NOTES:DEMO:CHECK-RULE]]</a:t></a:r></a:p></p:txBody></p:sp>
<p:sp><p:nvSpPr><p:cNvPr id="7" name="invented_answer"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="548640" y="2377440"/><a:ext cx="8046720" cy="1737360"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="F0F0F0"/></a:solidFill><a:ln cap="flat" cmpd="sng" w="9525"><a:solidFill><a:srgbClr val="CCCCCC"/></a:solidFill><a:prstDash val="solid"/><a:round/><a:headEnd len="sm" w="sm" type="none"/><a:tailEnd len="sm" w="sm" type="none"/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>
<p:sp><p:nvSpPr><p:cNvPr id="9" name="invented_footer"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="6455664"/><a:ext cx="8229600" cy="274320"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>'''
}


def footer_pptx(key=None):
    parts = dict(FOOTER_PARTS)
    if key:
        root = E.fromstring(parts["ppt/slides/slide1.xml"])
        target = next(s for s in root.iter(c.P + "sp") if s.find(c.P + "nvSpPr/" + c.P + "cNvPr").get("name") == key)
        body = target.find(c.P + "txBody")
        E.SubElement(E.SubElement(E.SubElement(body, c.A + "p"), c.A + "r"), c.A + "t").text = WORDS
        parts["ppt/slides/slide1.xml"] = E.tostring(root, encoding="unicode")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, value in parts.items(): archive.writestr(name, value.encode("utf-8"))
    return c.read_deck(out.getvalue())


def element(value, key):
    return next(e for page in value["slides"] for e in page["pageElements"] if e["objectId"] == key)


def text(value, key, content):
    element(value, key)["shape"]["text"] = {"textElements": [{"textRun": {"content": content + "\n", "style": deepcopy(STYLE)}}]}


def changed(value, *keys):
    result = deepcopy(value)
    for key in keys:
        entries = element(result, key)["shape"].get("text", {}).get("textElements", [])
        original = "".join(e.get("textRun", {}).get("content", "") for e in entries).strip()
        text(result, key, (original + "\n" + WORDS).strip())
    return result


def bound_source(kind):
    value = source()
    text(value, "synthetic_label", "Working space: explain your reasoning.")
    if kind == "direct": text(value, ICONS[0], "Write your answer here " + TAG)
    elif kind == "declared":
        text(value, "synthetic_label", "Your first answer " + TAG)
        element(value, "synthetic_label")["transform"]["translateY"] = 650000
    else:
        anchor = shape("source_point", 1181000, 1, 1, text=TAG, kind="TEXT_BOX")
        anchor["size"] = {"width": {"magnitude": 10000, "unit": "EMU"}, "height": {"magnitude": 10000, "unit": "EMU"}}
        anchor["transform"]["translateX"] += 10000
        value["slides"][0]["pageElements"].append(anchor)
    return value


def one_field():
    value = source()
    value["slides"][0]["pageElements"] = value["slides"][0]["pageElements"][-2:]
    return value


def with_inventory(value):
    value = deepcopy(value)
    cycle, qid = ("TEST", "ITEM") if value["presentationId"] == FILE else ("DEMO", "CHECK-RULE")
    header = f"[[MARKER-INVENTORY:{cycle}]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS={qid} DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-"
    value["slides"].insert(0, {"objectId": "synthetic_inventory_page", "pageElements": [shape("inventory", 100000, 2.68224, .1, text=header)]})
    return value


def parse(value, mode):
    parsed = n.read(value, presentation_id=value["presentationId"], revision=value["revisionId"])
    if mode == "native": return parsed
    # The shared toy exporter accepts absolute EMU bounds. Bake the actual
    # native affine dimensions, preserving the complete original object census.
    normalized = deepcopy(value)
    for page, original in zip(normalized["slides"], parsed["slides"]):
        units = {u["key"]: u for u in original["units"]}
        for e in page["pageElements"]:
            x, y, w, h = units[e["objectId"]]["box"]
            e["size"] = {"width": {"magnitude": round(w), "unit": "EMU"}, "height": {"magnitude": round(h), "unit": "EMU"}}
            e["transform"] = {"scaleX": 1, "scaleY": 1, "translateX": round(x), "translateY": round(y), "unit": "EMU"}
            e["shape"].setdefault("text", {"textElements": []})
            for entry in e["shape"]["text"]["textElements"]:
                if "textRun" in entry:
                    entry["textRun"]["style"] = {**deepcopy(STYLE), **entry["textRun"].get("style", {})}
    with patch("test_explicit_response_binding.SIZE", tuple(parsed["size"])):
        return c.read_deck(export(normalized))


class SingletonFieldTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))
        self.assertEqual(result["counts"]["NOTES"]["required"], 1)

    def completed(self, result, qid="ITEM"):
        self.assertIsNone(result["note"])
        self.assertEqual(result["notes_score"], 10)
        self.assertEqual([(i["category"], i["id"]) for i in result["items"] if i["status"] == "done"], [("NOTES", qid)])
        self.assertEqual(result["counts"]["NOTES"]["required"], 1)

    def test_exact_scaled_five_object_source_retains_all_fields_and_denominators(self):
        before = source(); snapshot = deepcopy(before)
        for mode in READERS:
            units = parse(before, mode)["slides"][0]["units"]
            self.assertEqual(len(units), 5)
            for i, y in enumerate((1171000, 2085400, 3091240)):
                self.assertEqual(list(units[i]["box"]), [548640, y, 182880, 182880])
            self.assertEqual(list(units[-1]["box"]), [548640, 5486400, 8046720, 868680])
        for teacher, student, found in product(READERS, READERS, (False, True)):
            result = c.assess(parse(before, student), parse(before, teacher), found_codes_only=found)
            self.assertEqual(result["notes_score"], 0)
            self.assertEqual(result["counts"]["NOTES"], {"done": 0, "required": 1})
            for key in ICONS + ("synthetic_response",):
                with self.subTest(teacher=teacher, student=student, found=found, key=key):
                    self.held(c.assess(parse(changed(before, key), student), parse(before, teacher), found_codes_only=found))
        self.assertEqual(before, snapshot)

    def test_far_invisible_footer_actual_zip_parts_and_native_keep_all_other_units_unchanged(self):
        before = footer_source()
        for teacher, student, key in product(READERS, READERS, (None, "invented_footer", "invented_answer")):
            original = parse(before, "native") if teacher == "native" else footer_pptx()
            after = changed(before, key) if key else before
            submitted = parse(after, "native") if student == "native" else footer_pptx(key)
            source_units = (parse(before, "native") if student == "native" else footer_pptx())["slides"][0]["units"]
            self.assertEqual(len(source_units), 3)
            for old, actual in zip(source_units, submitted["slides"][0]["units"]):
                if old["key"] != key: self.assertEqual(actual, old)
                else: self.assertEqual(actual["box"], old["box"])
            result = c.assess(submitted, original)
            if key: self.held(result)
            else: self.assertEqual(result["notes_score"], 0)

    def test_direct_declared_and_genuine_point_small_fields_keep_exact_identity(self):
        for kind, teacher, student in product(("direct", "declared", "point"), READERS, READERS):
            before = bound_source(kind)
            self.completed(c.assess(parse(changed(before, ICONS[0]), student), parse(before, teacher)))
            for key in ICONS[1:] + ("synthetic_response",):
                result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                self.assertIn(result["notes_score"], (None, 0))
                self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                self.assertEqual(result["counts"]["NOTES"]["required"], 1)

    def test_single_field_uniform_multipart_and_nested_response_controls(self):
        for variant in ("single", "uniform", "nested"):
            before = source() if variant == "uniform" else one_field()
            if variant == "uniform":
                for key, y in zip(ICONS, (500000, 1800000, 3100000)):
                    e = element(before, key)
                    e["transform"].update(scaleX=2.6822399999999997, scaleY=.28956, translateY=y)
            if variant == "nested":
                parent = shape("blank_parent", 5400000, 2.6822399999999997, 1400000 / 3000000)
                before["slides"][0]["pageElements"].insert(0, parent)
            for teacher, student in product(READERS, repeat=2):
                for key in (ICONS + ("synthetic_response",) if variant == "uniform" else ("synthetic_response",)):
                    self.completed(c.assess(parse(changed(before, key), student), parse(before, teacher)))

    def test_printed_backdrop_does_not_make_the_separate_single_response_ambiguous(self):
        before = one_field()
        backdrop = shape("printed_backdrop", 0, SIZE[0] / 3000000, SIZE[1] / 3000000)
        backdrop["transform"]["translateX"] = 0
        before["slides"][0]["pageElements"].insert(0, backdrop)
        for teacher, student in product(READERS, repeat=2):
            self.completed(c.assess(parse(changed(before, "synthetic_response"), student), parse(before, teacher)))
            self.held(c.assess(parse(changed(before, "printed_backdrop"), student), parse(before, teacher)))

    def test_standalone_marker_inside_printed_context_cannot_exempt_the_fallback(self):
        before = source()
        text(before, "synthetic_label", "Working space: explain your reasoning.")
        anchor = shape("source_anchor", 4990000, 1, 1, text=TAG, kind="TEXT_BOX")
        anchor["size"] = {"width": {"magnitude": 127000, "unit": "EMU"}, "height": {"magnitude": 127000, "unit": "EMU"}}
        before["slides"][0]["pageElements"].append(anchor)
        for teacher, student, key in product(READERS, READERS, ICONS + ("synthetic_response",)):
            self.held(c.assess(parse(changed(before, key), student), parse(before, teacher)))

    def test_original_census_survives_alias_order_scale_size_and_student_deletions(self):
        for variant in ("aliases", "reverse", "half", "double", "shorter-response", "deleted-icons", "missing-code", "changed-code"):
            before = source()
            if variant in ("half", "double"):
                scale = .5 if variant == "half" else 2
                for dimension in ("width", "height"): before["pageSize"][dimension]["magnitude"] *= scale
                for e in before["slides"][0]["pageElements"]:
                    for axis in ("translateX", "translateY"): e["transform"][axis] *= scale
                    for axis in ("scaleX", "scaleY"): e["transform"][axis] *= scale
            if variant == "shorter-response": element(before, "synthetic_response")["transform"]["scaleY"] = .06
            after = changed(before, "synthetic_response")
            if variant == "aliases":
                for i, e in enumerate(after["slides"][0]["pageElements"]): e["objectId"] = "export_alias_" + str(i)
            if variant == "reverse": after["slides"][0]["pageElements"].reverse()
            if variant == "deleted-icons": after["slides"][0]["pageElements"] = after["slides"][0]["pageElements"][-2:]
            if variant == "missing-code": text(after, "synthetic_label", "Working space: explain your reasoning.")
            if variant == "changed-code": text(after, "synthetic_label", "Working space: explain your reasoning. [[NOTES:TEST:OTHER]]")
            for teacher, student in product(READERS, repeat=2):
                result = c.assess(parse(after, student), parse(before, teacher))
                self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                self.assertIn(result["notes_score"], (None, 0))

    def test_combined_edits_do_not_resolve_original_ambiguity(self):
        before = source()
        for keys in ((ICONS[0], "synthetic_response"), ICONS, ICONS + ("synthetic_response",)):
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(changed(before, *keys), student), parse(before, teacher)))

    def test_normal_host_both_policies_preserves_denominators_and_assignment_gates(self):
        fid, sid, tid = "InventedSingletonForm0001", "InventedSingletonCopy0001", "InventedSingletonSource01"
        meta = {"id": sid, "name": "Invented copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        scenarios = [(source(), key, False) for key in (None,) + ICONS + ("synthetic_response",)]
        scenarios += [(footer_source(), key, False) for key in (None, "invented_footer", "invented_answer")]
        scenarios += [(one_field(), "synthetic_response", True), (bound_source("direct"), ICONS[0], True)]
        for (before, key, positive), teacher, student, strict, held in product(scenarios, READERS, READERS, (False, True), (False, True)):
            before = with_inventory(before)
            cls = "biology" if strict else "anatomy"; label = "Invented photosynthesis task" if strict else "Invented task"
            ready = {"status": "NOT_READY", "problem": "Invented assignment hold"} if held else {"status": "READY"}
            form = {"form_id": fid, "label": label, "grade_readiness": ready, "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
            lesson = {"topic": label, "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
            agenda = SimpleNamespace(classes={cls: {"name": "Invented class"}}, lessons=lambda *a: [lesson])
            original = parse(before, teacher); submitted = parse(changed(before, key), student) if key else parse(before, student)
            self.assertEqual(c.readiness(original)["status"], "READY")
            self.assertEqual(c.readiness(submitted)["status"], "READY")
            self.assertEqual(c.student_coding_required(agenda, cls, "W03", fid), strict)
            with self.subTest(key=key, teacher=teacher, student=student, strict=strict, held=held), \
                 patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted), \
                 patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, original)), \
                 patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
                result = c.score_file(None, agenda, cls, "W03", fid, sid, {"lessons": [lesson]})
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready"); self.assertIsNone(result.get("notes_score"))
                elif key is None: self.assertEqual(result["notes_score"], 0)
                elif positive: self.completed(result)
                else: self.held(result)

    def test_combined_host_cannot_bypass_singleton_uncertainty(self):
        meta = {"id": "InventedCombinedSource001", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged"}
        for teacher, student, key in product(READERS, READERS, ICONS + ("synthetic_response",)):
            before = with_inventory(source())
            originals = {"TEST": {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, teacher)}}
            copies = {"TEST": {"metadata": {**meta, "id": "InventedCombinedCopy001"}, "sha256": "b" * 64, "deck": parse(changed(before, key), student)}}
            form = {"combined_assessment": {"scope": {"components": [{"cycle": "TEST", "label": "Invented task", "teacher_id": meta["id"]}]}}}
            row = {"combined_components": [{"cycle": "TEST", "file_id": copies["TEST"]["metadata"]["id"]}]}
            with patch.object(combined, "verify_sources", return_value=(originals, copies)):
                result = combined.score_row(None, form, row)
            self.held(result)


if __name__ == "__main__":
    unittest.main()
