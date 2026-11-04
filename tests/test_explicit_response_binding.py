"""Synthetic direct text-field binding; no live sources or grading writes."""
from copy import deepcopy
import io
import json
import unittest
import xml.etree.ElementTree as E
import zipfile

from unittest.mock import patch
from hub_v2 import completion as c, explicit_response_binding as b, native_teacher_source as n, response_binding as rb, google

P, A = c.P, c.A
FILE = "FictionalExplicitSource123456789"
SIZE = (9144000, 6858000)
TAG = "[[NOTES:C99:EXPLAIN]]"
WORDS = "The observed difference supports a distinct explanation of the synthetic process."
STYLE = {"fontSize": {"magnitude": 18, "unit": "PT"},
         "foregroundColor": {"opaqueColor": {"rgbColor": {}}}, "fontFamily": "Arial"}


def shape(key, text, box):
    x, y, w, h = box
    return {"objectId": key, "size": {"width": {"magnitude": w, "unit": "EMU"}, "height": {"magnitude": h, "unit": "EMU"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "EMU"},
            "shape": {"shapeType": "TEXT_BOX", "shapeProperties": {
                "autofit": {"autofitType": "NONE"},
                "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}, "alpha": 1}}},
                "text": {"textElements": [{"textRun": {"content": text + "\n", "style": deepcopy(STYLE)}}]}}}


def original():
    units = [shape("prompt", "Explain the synthetic observation.", [548640, 500000, 8046720, 300000])]
    for i in range(6):
        units.append(shape(f"practice{i}", "Working step", [548640 + 2700000 * (i % 3), 1800000 + 1000000 * (i // 3), 2500000, 650000]))
    units += [shape("label", TAG, [548640, 5410200, 8046600, 139800]),
              shape("answer", "", [548640, 5577840, 8046720, 1115568])]
    return {"presentationId": FILE, "pageSize": {"width": {"magnitude": SIZE[0], "unit": "EMU"},
            "height": {"magnitude": SIZE[1], "unit": "EMU"}}, "slides": [{"objectId": "page", "pageElements": units}]}


def element(value, key):
    return next(e for page in value["slides"] for e in page["pageElements"] if e["objectId"] == key)


def write(value, key, text):
    element(value, key)["shape"]["text"]["textElements"][0]["textRun"]["content"] = text + "\n"


def export(value):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        pres = E.Element(P + "presentation")
        E.SubElement(pres, P + "sldSz", cx=str(SIZE[0]), cy=str(SIZE[1]))
        archive.writestr("ppt/presentation.xml", E.tostring(pres))
        for number, page in enumerate(value["slides"], 1):
            root = E.Element(P + "sld")
            tree = E.SubElement(E.SubElement(root, P + "cSld"), P + "spTree")
            for e in page["pageElements"]:
                node = E.SubElement(tree, P + "sp")
                nv = E.SubElement(node, P + "nvSpPr")
                E.SubElement(nv, P + "cNvPr", name=e["objectId"], id=str(len(tree)), descr=e.get("description", ""), hidden=e.get("hidden", "0"))
                props = E.SubElement(node, P + "spPr")
                transform = e["transform"]
                xf = E.SubElement(props, A + "xfrm", **({"rot": "60000"} if transform.get("shearX") else {}))
                E.SubElement(xf, A + "off", x=str(transform["translateX"]), y=str(transform["translateY"]))
                E.SubElement(xf, A + "ext", cx=str(e["size"]["width"]["magnitude"]), cy=str(e["size"]["height"]["magnitude"]))
                paint = e["shape"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]
                rgb = paint["color"]["rgbColor"]
                E.SubElement(E.SubElement(props, A + "solidFill"), A + "srgbClr", val="".join(f"{round(rgb.get(k, 0) * 255):02X}" for k in ("red", "green", "blue")))
                body = E.SubElement(node, P + "txBody")
                E.SubElement(E.SubElement(body, A + "bodyPr"), A + "noAutofit")
                E.SubElement(body, A + "lstStyle")
                p = E.SubElement(body, A + "p")
                for entry in e["shape"]["text"]["textElements"]:
                    if "textRun" not in entry:
                        continue
                    data = entry["textRun"]
                    run = E.SubElement(p, A + "r")
                    style = data["style"]
                    rp = E.SubElement(run, A + "rPr", sz=str(round(style["fontSize"]["magnitude"] * 100)))
                    E.SubElement(rp, A + "latin", typeface=style.get("fontFamily", "Arial"))
                    rgb = style["foregroundColor"]["opaqueColor"]["rgbColor"]
                    color = E.SubElement(E.SubElement(rp, A + "solidFill"), A + "srgbClr", val="".join(f"{round(rgb.get(k, 0) * 255):02X}" for k in ("red", "green", "blue")))
                    if style.get("transparent"):
                        E.SubElement(color, A + "alpha", val="0")
                    E.SubElement(run, A + "t").text = data["content"]
            archive.writestr(f"ppt/slides/slide{number}.xml", E.tostring(root))
    return out.getvalue()


def parse(value, form, *, authoring=False, witness=True):
    if form == "native":
        parsed = n.read(value, presentation_id=FILE, revision="7", explicit_authoring=authoring)
        if not authoring and witness and any(b.bound(p) for p in parsed["slides"]):
            try:
                return b.with_export_witness(parsed, parse(value, "pptx"))
            except (ValueError, KeyError):
                pass  # invalid pairs retain no visibility authority and hold
        return parsed
    parsed = c.read_deck(export(value), explicit_authoring=authoring)
    parsed["explicit_source_id"] = FILE  # fictional trusted host metadata bookend
    parsed["explicit_source_revision"] = "7"
    return parsed


def prepared():
    value = original()
    data = b.author(parse(value, "native", authoring=True), parse(value, "pptx", authoring=True),
                    label_key="label", target_key="answer", practice_keys=[f"practice{i}" for i in range(6)])
    element(value, "label")["description"] = rb.PREFIX + json.dumps(data)
    return value


class ExplicitResponseTests(unittest.TestCase):
    def test_four_reader_combinations_keep_one_response_and_six_practice_negatives(self):
        template = prepared()
        for teacher_form in ("native", "pptx"):
            for student_form in ("native", "pptx"):
                for keys in ([], ["answer"], *[[f"practice{i}"] for i in range(6)], [f"practice{i}" for i in range(6)], ["answer", "practice0"]):
                    with self.subTest(teacher=teacher_form, student=student_form, keys=keys):
                        student = deepcopy(template)
                        for key in keys:
                            write(student, key, WORDS)
                        sources = parse(student, student_form), parse(template, teacher_form)
                        before = deepcopy(sources)
                        result = c.assess(*sources, found_codes_only=True)
                        self.assertIsNone(result["note"])
                        self.assertEqual(result["counts"]["NOTES"], {"done": int("answer" in keys), "required": 1})
                        self.assertEqual(result["items"][0]["answer"], WORDS if "answer" in keys else "")
                        self.assertEqual(sources, before)

    def assert_held(self, student, teacher=None):
        for tf in ("native", "pptx"):
            for sf in ("native", "pptx"):
                result = c.assess(parse(student, sf), parse(teacher if teacher is not None else prepared(), tf), found_codes_only=True)
                self.assertEqual((result["notes_score"], result["revision_score"]), (None, None))
                self.assertIn("explicit response", result["note"].lower())
                self.assertFalse(any(i["answer"] == WORDS for i in result["items"]))

    def test_descriptor_deletion_tampering_duplicates_and_marker_removal_never_downgrade(self):
        for mode in ("description", "marker", "kind", "target", "given", "scene", "surface", "extra", "duplicate_json"):
            with self.subTest(mode=mode):
                value = prepared(); write(value, "practice0", WORDS)
                label = element(value, "label")
                data = json.loads(label["description"][len(rb.PREFIX):])
                if mode == "description":
                    label.pop("description")
                elif mode == "marker":
                    write(value, "label", "Explain the observation.")
                elif mode == "duplicate_json":
                    label["description"] = rb.PREFIX + '{"kind":"ignored",' + json.dumps(data)[1:]
                else:
                    if mode == "kind": data["kind"] = "unknown"
                    if mode == "target": data["target"]["box"] = data["practice"][0]["box"]
                    if mode == "given": data["target"]["given"] = "New given"
                    if mode == "scene": data["scene_sha256"] = "0" * 64
                    if mode == "surface": data["surface_sha256"]["native"] = "0" * 64
                    if mode == "extra": data["allow_guess"] = True
                    label["description"] = rb.PREFIX + json.dumps(data)
                if mode == "kind":
                    # Native already rejects unknown activity metadata outright.
                    with self.assertRaises(n.Unreadable): parse(value, "native")
                    result = c.assess(parse(value, "pptx"), parse(prepared(), "pptx"), found_codes_only=True)
                    self.assertIsNone(result["notes_score"])
                else:
                    self.assert_held(value)

    def test_deleted_reordered_moved_duplicate_or_covered_objects_hold(self):
        for key in ("label", "answer", "practice0", "prompt"):
            for mode in ("delete", "move", "resize", "duplicate", "reorder"):
                with self.subTest(key=key, mode=mode):
                    value = prepared(); write(value, "answer", WORDS)
                    elements = value["slides"][0]["pageElements"]
                    target = element(value, key)
                    if mode == "delete": elements.remove(target)
                    if mode == "move": target["transform"]["translateX"] += 100
                    if mode == "resize": target["size"]["width"]["magnitude"] += 100
                    if mode == "duplicate":
                        copied = deepcopy(target); copied["objectId"] += "duplicate"; elements.append(copied)
                    if mode == "reorder": elements.remove(target); elements.insert(0 if key != "prompt" else 1, target)
                    self.assert_held(value)
        value = prepared(); write(value, "answer", WORDS)
        value["slides"][0]["pageElements"].append(shape("cover", "", [548640, 5577840, 8046720, 1115568]))
        self.assert_held(value)

    def test_visibility_changed_source_missing_source_and_aliases(self):
        for mode in ("tiny", "white", "transparent", "rotate", "dark_fill"):
            with self.subTest(mode=mode):
                value = prepared(); write(value, "answer", WORDS)
                target = element(value, "answer")
                style = target["shape"]["text"]["textElements"][0]["textRun"]["style"]
                if mode == "tiny": style["fontSize"]["magnitude"] = 1
                if mode == "white": style["foregroundColor"]["opaqueColor"]["rgbColor"] = dict(red=1, green=1, blue=1)
                if mode == "transparent": style["transparent"] = True
                if mode == "rotate": target["transform"]["shearX"] = 1
                if mode == "dark_fill": target["shape"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"] = {}
                self.assert_held(value)
        value = prepared(); write(value, "answer", WORDS)
        teacher = prepared(); write(teacher, "prompt", "A changed source question.")
        self.assert_held(value, teacher)
        for e in value["slides"][0]["pageElements"]: e["objectId"] += "renamed"
        result = c.assess(parse(value, "pptx"), parse(prepared(), "native"), found_codes_only=True)
        self.assertEqual(result["items"][0]["answer"], WORDS)
        teacher = parse(prepared(), "pptx"); teacher.pop("explicit_source_id")
        self.assertIsNone(c.assess(parse(value, "pptx"), teacher, found_codes_only=True)["notes_score"])

    def test_plain_source_does_not_authorize_student_descriptor_and_pure_deletion_is_blank(self):
        value = prepared(); write(value, "answer", WORDS)
        self.assert_held(value, original())
        raw = original(); write(raw, "answer", "Type here.")
        data = b.author(parse(raw, "native", authoring=True), parse(raw, "pptx", authoring=True),
                        label_key="label", target_key="answer", practice_keys=[f"practice{i}" for i in range(6)])
        element(raw, "label")["description"] = rb.PREFIX + json.dumps(data)
        student = deepcopy(raw); write(student, "answer", "")
        result = c.assess(parse(student, "native"), parse(raw, "pptx"), found_codes_only=True)
        self.assertEqual((result["notes_score"], result["items"][0]["answer"]), (0, ""))

    def test_bound_slide_deletion_duplicates_reorder_and_wrong_source_hold(self):
        template = prepared()
        extra = deepcopy(original()["slides"][0]); extra["objectId"] = "uncoded-page"
        extra["pageElements"] = [shape("context", "Uncoded context.", [500000, 500000, 3000000, 300000])]
        template["slides"].append(extra)
        for mode in ("delete", "reorder", "duplicate", "wrong_source"):
            with self.subTest(mode=mode):
                student = deepcopy(template); write(student, "answer", WORDS)
                if mode == "delete": student["slides"].pop(0)
                if mode == "reorder": student["slides"].reverse()
                if mode == "duplicate":
                    page = deepcopy(student["slides"][0]); page["objectId"] = "duplicate"
                    for e in page["pageElements"]: e["objectId"] += "duplicate"
                    student["slides"].append(page)
                if mode == "wrong_source":
                    for tf in ("native", "pptx"):
                        teacher = parse(template, tf)
                        teacher["explicit_source_id"] = "DifferentFictionalSource1234567"
                        result = c.assess(parse(student, "pptx"), teacher, found_codes_only=True)
                        self.assertIsNone(result["notes_score"])
                    continue
                self.assert_held(student, template)

    def test_host_export_identity_is_attached_and_no_template_or_raw_export_authority_holds(self):
        raw = export(prepared())
        with patch.object(google, "deck_bytes", return_value=raw):
            source = c._read("fictional-token", FILE, {"mimeType": c.SLIDES, "version": "7"}, source_role="teacher")
        self.assertEqual(source["explicit_source_id"], FILE)
        student = prepared(); write(student, "answer", WORDS)
        result = c.assess(parse(student, "native"), source, found_codes_only=True)
        self.assertEqual(result["notes_score"], 10)
        for teacher in (None, c.read_deck(raw)):
            self.assertIsNone(c.assess(parse(student, "native"), teacher, found_codes_only=True)["notes_score"])

    def test_author_rejects_wrong_roles_unresolved_visibility_and_cross_format_changes(self):
        for mode in ("wrong_label", "overlap", "missing", "unresolved", "changed_export", "extra_field"):
            with self.subTest(mode=mode):
                raw = original(); export_raw = deepcopy(raw)
                label, target = "label", "answer"
                practice = [f"practice{i}" for i in range(6)]
                if mode == "wrong_label": label = "practice0"
                if mode == "overlap": target = "practice0"
                if mode == "missing": target = "missing"
                if mode == "unresolved":
                    element(raw, "answer")["shape"]["text"]["textElements"][0]["textRun"]["style"].pop("foregroundColor")
                if mode == "changed_export": write(export_raw, "prompt", "A different source question.")
                if mode == "extra_field": practice.append("practice0")
                with self.assertRaises((ValueError, KeyError)):
                    b.author(parse(raw, "native", authoring=True), parse(export_raw, "pptx", authoring=True),
                             label_key=label, target_key=target, practice_keys=practice)

    def test_legacy_visible_above_field_heuristic_remains_unchanged_without_opt_in(self):
        source = original(); student = deepcopy(source); write(student, "practice0", WORDS)
        result = c.assess(parse(student, "pptx"), parse(source, "native"), found_codes_only=True)
        self.assertEqual(result["items"][0]["answer"], WORDS)
        self.assertEqual(result["notes_score"], 10)  # opt-in contract, no global policy change

    def test_unsupported_raw_objects_cannot_disappear_from_the_explicit_scene(self):
        teacher = prepared(); student = deepcopy(teacher); write(student, "answer", WORDS)
        student["slides"][0]["pageElements"].append({"objectId": "opaque-connector", "line": {}})
        result = c.assess(parse(student, "native"), parse(teacher, "native"), found_codes_only=True)
        self.assertIsNone(result["notes_score"])

        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(export(teacher))) as source, zipfile.ZipFile(out, "w") as target:
            for name in source.namelist():
                raw = source.read(name)
                if name == "ppt/slides/slide1.xml":
                    root = E.fromstring(raw); tree = root.find(P + "cSld/" + P + "spTree")
                    connector = E.SubElement(tree, P + "cxnSp")
                    props = E.SubElement(connector, P + "spPr")
                    E.SubElement(E.SubElement(E.SubElement(props, A + "ln", w="1270000"), A + "solidFill"), A + "srgbClr", val="000000")
                    raw = E.tostring(root)
                target.writestr(name, raw)
        parsed = c.read_deck(out.getvalue()); parsed["explicit_source_id"] = FILE
        result = c.assess(parsed, parse(teacher, "pptx"), found_codes_only=True)
        self.assertIsNone(result["notes_score"])

    def test_title_and_instruction_only_changes_never_supply_the_bound_answer(self):
        source = prepared()
        for field in ("prompt", "label"):
            with self.subTest(field=field):
                student = deepcopy(source)
                old = element(student, field)["shape"]["text"]["textElements"][0]["textRun"]["content"].strip()
                write(student, field, old + " " + WORDS)
                self.assert_held(student, source)

    def test_table_and_group_targets_remain_explicitly_unsupported_in_v1(self):
        from test_native_table_source import native as table_source
        value = prepared(); write(value, "answer", WORDS)
        table = deepcopy(table_source()["slides"][0]["pageElements"][0])
        table["objectId"] = "toy_table"
        value["slides"][0]["pageElements"].append(table)
        result = c.assess(parse(value, "native"), parse(prepared(), "native"), found_codes_only=True)
        self.assertIsNone(result["notes_score"])
        with self.assertRaises(ValueError):
            b.author(parse(value, "native", authoring=True), parse(prepared(), "pptx", authoring=True),
                     label_key="label", target_key="toy_table/r1c1", practice_keys=[])
        table["description"] = rb.PREFIX + json.dumps({"kind": b.KIND})
        with self.assertRaises(n.Unreadable): parse(value, "native")

        value = prepared()
        label = element(value, "label")
        value["slides"][0]["pageElements"].remove(label)
        value["slides"][0]["pageElements"].append({"objectId": "group", "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"},
                                                 "elementGroup": {"children": [label]}})
        with self.assertRaises(n.Unreadable): parse(value, "native")


    def test_native_witness_requires_exact_current_host_pair_and_cannot_be_replayed(self):
        source = prepared(); student = deepcopy(source); write(student, "answer", WORDS)
        teacher = parse(source, "native")
        raw_student = parse(student, "native", witness=False)
        self.assertIsNone(c.assess(raw_student, teacher, found_codes_only=True)["notes_score"])
        no_kind = deepcopy(raw_student); no_kind.pop("source_kind")
        self.assertIsNone(c.assess(no_kind, teacher, found_codes_only=True)["notes_score"])
        self.assertIsNone(c.assess(parse(student, "native"), parse(source, "native", witness=False), found_codes_only=True)["notes_score"])
        for change in ("answer", "revision", "id", "raw", "serialized", "kind_deleted", "kind_changed", "format"):
            current = parse(student, "native")
            if change == "answer": current["slides"][0]["units"][-1]["text"] = "Different current answer."
            if change == "revision": current["native_source_revision"] = "8"
            if change == "id": current["native_source_id"] = "AnotherFictionalCopy1234567"
            if change == "raw": current["native_source"]["revisionId"] = "new"
            if change == "serialized": current["_explicit_export_witness"] = {"snapshot": b._snapshot(raw_student)}
            if change == "kind_deleted": current.pop("source_kind")
            if change == "kind_changed":
                current["source_kind"] = "pptx"
                current["slides"][0]["units"][-1]["text"] = "A changed native answer."
            if change == "format": current["slides"][0]["units"][-1]["explicit_surface"]["format"] = "pptx"
            with self.subTest(change=change):
                self.assertIsNone(c.assess(current, teacher, found_codes_only=True)["notes_score"])
        for exported in (c.read_deck(export(student)), parse(source, "pptx")):
            with self.assertRaises(ValueError): b.with_export_witness(raw_student, exported)
        exported = parse(student, "pptx"); exported["explicit_source_revision"] = "6"
        with self.assertRaises(ValueError): b.with_export_witness(raw_student, exported)

    def test_student_native_copy_uses_its_own_export_witness(self):
        student = prepared(); write(student, "answer", WORDS)
        copy_id = "FictionalSubmittedCopy123456789"
        student["presentationId"] = copy_id
        native = n.read(student, presentation_id=copy_id, revision="8", source_role="student")
        exported = c.read_deck(export(student))
        exported.update(explicit_source_id=copy_id, explicit_source_revision="8")
        with self.assertRaises(ValueError): b.with_export_witness(native, parse(prepared(), "pptx"))
        witnessed = b.with_export_witness(native, exported)
        result = c.assess(witnessed, parse(prepared(), "native"), found_codes_only=True)
        self.assertEqual((result["notes_score"], result["items"][0]["answer"]), (10, WORDS))

    def test_draft_and_multiple_identity_pages_are_held_instead_of_silently_migrated(self):
        source = prepared()
        label = element(source, "label")
        data = json.loads(label["description"][len(rb.PREFIX):])
        data["category"] = "DRAFT"
        label["description"] = rb.PREFIX + json.dumps(data)
        write(source, "label", TAG.replace("NOTES", "DRAFT"))
        student = deepcopy(source); write(student, "prompt", WORDS)
        self.assert_held(student, source)
        raw = original(); write(raw, "prompt", "[[NOTES:C99:SECOND]]")
        with self.assertRaises(ValueError):
            b.author(parse(raw, "native", authoring=True), parse(raw, "pptx", authoring=True),
                     label_key="label", target_key="answer", practice_keys=[f"practice{i}" for i in range(6)])

    def test_slide_timing_transition_display_and_background_are_validated(self):
        teacher = prepared(); student = deepcopy(teacher); write(student, "answer", WORDS)
        for mode in ("timing", "transition", "hidden", "background", "duplicate_scene", "duplicate_tree"):
            with self.subTest(mode=mode):
                out = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(export(student))) as src, zipfile.ZipFile(out, "w") as dst:
                    for name in src.namelist():
                        raw = src.read(name)
                        if name == "ppt/slides/slide1.xml":
                            root = E.fromstring(raw)
                            if mode in ("timing", "transition"):
                                branch = E.SubElement(root, P + mode)
                                if mode == "timing":
                                    E.SubElement(E.SubElement(branch, P + "set"), P + "strVal", val="hidden")
                            if mode == "hidden": root.set("show", "0")
                            if mode == "background": E.SubElement(root.find(P + "cSld"), P + "bg")
                            if mode == "duplicate_scene": root.append(deepcopy(root.find(P + "cSld")))
                            if mode == "duplicate_tree": root.find(P + "cSld").append(deepcopy(root.find(P + "cSld/" + P + "spTree")))
                            raw = E.tostring(root)
                        dst.writestr(name, raw)
                parsed = c.read_deck(out.getvalue())
                parsed.update(explicit_source_id=FILE, explicit_source_revision="7")
                self.assertIsNone(c.assess(parsed, parse(teacher, "pptx"), found_codes_only=True)["notes_score"])
                with self.assertRaises(ValueError):
                    b.with_export_witness(parse(student, "native", witness=False), parsed)

    def test_inherited_theme_and_symbol_fonts_do_not_claim_resolved_visibility(self):
        for face in ("+mn-lt", "Wingdings", "Webdings"):
            with self.subTest(face=face):
                value = original()
                element(value, "answer")["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontFamily"] = face
                with self.assertRaises(ValueError):
                    b.author(parse(value, "native", authoring=True), parse(value, "pptx", authoring=True),
                             label_key="label", target_key="answer", practice_keys=[f"practice{i}" for i in range(6)])
                value = prepared(); write(value, "answer", WORDS)
                element(value, "answer")["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontFamily"] = face
                self.assert_held(value)

    def test_unresolved_shape_style_effect_references_are_not_supported(self):
        def referenced(raw):
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(out, "w") as dst:
                for name in src.namelist():
                    data = src.read(name)
                    if name == "ppt/slides/slide1.xml":
                        root = E.fromstring(data)
                        answer = next(s for s in root.iter(P + "sp") if s.find(P + "nvSpPr/" + P + "cNvPr").get("name") == "answer")
                        E.SubElement(E.SubElement(answer, P + "style"), A + "effectRef", idx="1")
                        data = E.tostring(root)
                    dst.writestr(name, data)
            return out.getvalue()
        with self.assertRaises(ValueError):
            b.author(parse(original(), "native", authoring=True), c.read_deck(referenced(export(original())), explicit_authoring=True),
                     label_key="label", target_key="answer", practice_keys=[f"practice{i}" for i in range(6)])
        current = prepared(); write(current, "answer", WORDS)
        parsed = c.read_deck(referenced(export(current)))
        parsed.update(explicit_source_id=FILE, explicit_source_revision="7")
        self.assertIsNone(c.assess(parsed, parse(prepared(), "pptx"), found_codes_only=True)["notes_score"])


if __name__ == "__main__":
    unittest.main()
