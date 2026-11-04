"""Invented static overlap/anchor cases; no source/network/persistent writes."""
from copy import deepcopy
import io
from itertools import product
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

from hub_v2 import completion as c, combined_assessment, google, legacy_response_attribution as legacy, legacy_static_context as static, native_teacher_source as n
from test_explicit_response_binding import shape, export, SIZE, WORDS
from test_legacy_response_attribution import parse_table
from test_native_table_source import native as table_source, element as table_element
from test_hub_v2 import pptx

FILE = "FictionalDuplicateGeometrySource001"


def source(duplicate=True):
    units = [shape("prompt", "Explain the fictional observation.", [500000, 200000, 8000000, 500000]),
        shape("first-label", "Your first answer [[NOTES:C17:PAIR]]", [500000, 1000000, 3000000, 300000]),
        shape("first-answer", "", [500000, 1500000, 3000000, 3000000]),
        shape("revision-label", "Your revised answer [[DRAFT:C17:PAIR]]", [4800000, 1000000, 3000000, 300000]),
        shape("revision-answer", "", [4800000, 1500000, 3000000, 3000000])]
    if duplicate: units.insert(1, shape("empty-shadow", "", [500000, 1000000, 3000000, 300000]))
    header = "[[MARKER-INVENTORY:C17]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=PAIR DRAFT_IDS=PAIR OPTIONAL_IDS=- BANK_IDS=-"
    return {"presentationId": FILE, "pageSize": {"width": {"magnitude": SIZE[0], "unit": "EMU"},
        "height": {"magnitude": SIZE[1], "unit": "EMU"}}, "slides": [
        {"objectId": "inventory", "pageElements": [shape("inventory", header, [100000, 100000, 8000000, 500000])]},
        {"objectId": "question", "pageElements": units}]}


def element(raw, key):
    return next(e for p in raw["slides"] for e in p["pageElements"] if e["objectId"] == key)


def write(raw, key, text):
    element(raw, key)["shape"]["text"]["textElements"][0]["textRun"]["content"] = text + "\n"


def parsed(raw, form="pptx"):
    return n.read(raw, presentation_id=FILE, revision="1") if form == "native" else c.read_deck(export(raw))


def repack(raw, change):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as before, zipfile.ZipFile(out, "w") as after:
        for name in before.namelist(): after.writestr(name, change(name, before.read(name)))
    return out.getvalue()


class StaticContextTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_raw_pptx_distinguishes_static_group_and_unique_first_revised_fields(self):
        for duplicate in (False, True):
            before = source(duplicate)
            for target, scores in ((None, (0, 0)), ("first-answer", (10, 0)), ("revision-answer", (0, 10))):
                after = deepcopy(before)
                if target: write(after, target, WORDS)
                result = c.assess(parsed(after), parsed(before))
                self.assertIsNone(result["note"])
                self.assertEqual((result["notes_score"], result["revision_score"]), scores)
                if target: self.assertEqual(next(i["answer"] for i in result["items"] if i["status"] == "done"), WORDS)

    def test_native_and_cross_format_group_without_render_witness_remains_held(self):
        before = source(); after = deepcopy(before); write(after, "first-answer", WORDS)
        for a, b in product(("native", "pptx"), repeat=2):
            if a == b == "pptx": continue
            self.held(c.assess(parsed(after, b), parsed(before, a)))

    def test_renamed_and_colliding_aliases_never_turn_unchanged_group_into_writing(self):
        before = source(); after = deepcopy(before); write(after, "revision-answer", WORDS)
        element(after, "first-label")["objectId"] = "empty-shadow"
        element(after, "empty-shadow")["objectId"] = "new-empty-name"
        for index, e in enumerate(after["slides"][1]["pageElements"]): e["objectId"] = "alias" + str(index)
        result = c.assess(parsed(after), parsed(before))
        self.assertIsNone(result["note"])
        self.assertEqual((result["notes_score"], result["revision_score"]), (0, 10))

    def test_group_writing_deletion_order_geometry_multiplicity_and_style_changes_hold(self):
        for mode in ("write-empty", "write-label", "delete", "reorder", "geometry", "extra", "font", "hidden", "transparent", "answer-hidden"):
            before = source(); after = deepcopy(before); write(after, "first-answer", WORDS)
            units = after["slides"][1]["pageElements"]
            if mode == "write-empty": write(after, "empty-shadow", WORDS)
            if mode == "write-label": write(after, "first-label", "Your first answer [[NOTES:C17:PAIR]] " + WORDS)
            if mode == "delete": units.remove(element(after, "empty-shadow"))
            if mode == "reorder": units[1], units[2] = units[2], units[1]
            if mode == "geometry": element(after, "empty-shadow")["transform"]["translateX"] += 1
            if mode == "extra": units.insert(1, deepcopy(element(after, "empty-shadow")))
            if mode == "font": element(after, "first-label")["shape"]["text"]["textElements"][0]["textRun"]["style"]["fontFamily"] = "Georgia"
            if mode == "hidden": element(after, "empty-shadow")["hidden"] = "1"
            if mode in ("transparent", "answer-hidden"):
                key = "first-label" if mode == "transparent" else "first-answer"
                element(after, key)["shape"]["text"]["textElements"][0]["textRun"]["style"]["transparent"] = True
            with self.subTest(mode=mode): self.held(c.assess(parsed(after), parsed(before)))

    def test_ambiguous_answer_backplates_extra_practice_and_nonstandard_starters_stay_held(self):
        for mode in ("answer-duplicate", "practice", "starter"):
            before = source()
            if mode == "answer-duplicate": before["slides"][1]["pageElements"].append(deepcopy(element(before, "first-answer")))
            if mode == "practice": before["slides"][1]["pageElements"].append(shape("unscored", "", [500000, 5000000, 3000000, 300000]))
            if mode == "starter": write(before, "first-answer", "My reasoning and evidence:")
            after = deepcopy(before); write(after, "first-answer", WORDS)
            self.held(c.assess(parsed(after), parsed(before)))

    def test_raw_render_branches_and_changed_resource_context_hold(self):
        before = source(); after = deepcopy(before); write(after, "first-answer", WORDS)
        def timing(name, raw):
            if name == "ppt/slides/slide2.xml":
                root = E.fromstring(raw); E.SubElement(root, c.P + "timing"); return E.tostring(root)
            return raw
        self.held(c.assess(c.read_deck(repack(export(after), timing)), parsed(before)))
        def changed(name, raw):
            if name == "ppt/presentation.xml":
                root = E.fromstring(raw); E.SubElement(root, c.P + "notesSz", cx="1", cy="1"); return E.tostring(root)
            return raw
        self.held(c.assess(c.read_deck(repack(export(after), changed)), parsed(before)))

    def test_proof_is_not_serializable_or_replayable_after_parsed_mutation(self):
        before = parsed(source()); after = parsed(source())
        proof = after["slides"][1]._legacy_context_proof
        after["slides"][1]._legacy_context_proof = dict(proof._asdict())
        next(u for u in after["slides"][1]["units"] if u["key"] == "first-answer")["text"] = WORDS
        self.held(c.assess(after, before))
        after["slides"][1]._legacy_context_proof = proof
        self.held(c.assess(after, before))

    def test_unsupported_original_layout_and_duplicate_raw_metadata_never_receive_proof(self):
        for mode in ("insets", "paragraph", "list", "overflow", "duplicate-tree", "duplicate-fill", "root-transform"):
            def mutation(name, raw):
                if name != "ppt/slides/slide2.xml": return raw
                root = E.fromstring(raw); tree = root.find(c.P + "cSld/" + c.P + "spTree")
                answer = next(node for node in tree if node.find(c.P + "nvSpPr/" + c.P + "cNvPr").get("name") == "first-answer")
                body = answer.find(c.P + "txBody/" + c.A + "bodyPr")
                if mode == "insets": body.attrib.update(lIns="3000001", rIns="3000001", horzOverflow="clip", vertOverflow="clip")
                if mode == "overflow": body.set("vertOverflow", "clip")
                if mode == "paragraph": E.SubElement(answer.find(c.P + "txBody/" + c.A + "p"), c.A + "pPr", marL="9999999")
                if mode == "list": E.SubElement(answer.find(c.P + "txBody/" + c.A + "lstStyle"), c.A + "lvl1pPr", marL="9999999")
                if mode == "duplicate-tree":
                    for hidden in ("0", "1"):
                        node = E.Element(c.P + "nvGrpSpPr"); E.SubElement(node, c.P + "cNvPr", hidden=hidden); tree.insert(0, node)
                if mode == "duplicate-fill":
                    props = answer.find(c.P + "spPr"); props.append(deepcopy(props.find(c.A + "solidFill")))
                if mode == "root-transform":
                    props = E.Element(c.P + "grpSpPr"); xf = E.SubElement(props, c.A + "xfrm"); E.SubElement(xf, c.A + "off", x="99999999", y="0"); tree.insert(0, props)
                return E.tostring(root)
            before = source(); after = deepcopy(before); write(after, "first-answer", WORDS)
            original = c.read_deck(repack(export(before), mutation))
            changed = c.read_deck(repack(export(after), mutation))
            with self.subTest(mode=mode):
                self.assertIsNone(getattr(original["slides"][1], "_legacy_context_proof", None))
                self.held(c.assess(changed, original))

    def test_private_proof_can_be_hashed_but_a_json_roundtrip_is_not_authority(self):
        value = parsed(source()); encoded = json.dumps(value)
        self.assertNotIn("_legacy_context_proof", encoded)
        meta = {"id": FILE, "name": "Fictional", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged"}
        self.assertEqual(combined_assessment.source_basis({"metadata": meta, "deck": value})["hash_basis"], "parsed_completion_deck")
        after = json.loads(encoded)
        next(u for u in after["slides"][1]["units"] if u["key"] == "first-answer")["text"] = WORDS
        self.held(c.assess(after, value))


def anchor_source(starter="My reasoning and evidence:", width=5000):
    unit = lambda k, t, b: {"key": k, "text": t, "box": b, "title": False}
    return {"size": (10000, 6000), "slides": [{"number": 1, "units": [
        unit("prompt", "Explain an invented observation.", (100, 700, 2000, 300)),
        unit("response", starter, (2300, 700, 5000, 800)),
        unit("anchor", "[[NOTES:Z91:EXPLAIN]]", (2300, 650, width, 10))]}]}


class AnchorContextTests(unittest.TestCase):
    def test_complete_assessor_rejects_writing_only_on_code_anchor(self):
        for width in (5000, 2):
            before = anchor_source(width=width); after = deepcopy(before)
            after["slides"][0]["units"][-1]["text"] += "\n" + WORDS
            result = c.assess(after, before)
            self.assertIsNone(result["notes_score"])
            self.assertTrue(result["note"])
            self.assertFalse(any(i["status"] == "done" for i in result["items"]))
        self.assertEqual(c.assess(before, before)["notes_score"], 0)

    def test_recognized_placeholders_work_but_unknown_starters_do_not_create_roles(self):
        for starter, expected in (("Write your answer here", 10), ("My reasoning and evidence:", None)):
            before = anchor_source(starter); after = deepcopy(before)
            after["slides"][0]["units"][1]["text"] += "\n" + WORDS
            self.assertEqual(c.assess(after, before)["notes_score"], expected)

    def test_anchor_deleted_code_moved_or_overlaid_writing_cannot_become_a_field(self):
        before = anchor_source()
        for mode in ("deleted-code", "moved-code", "overlaid", "student-role-flag"):
            after = deepcopy(before); anchor = after["slides"][0]["units"][-1]
            anchor["text"] += "\n" + WORDS
            if mode == "deleted-code": anchor["text"] = WORDS
            if mode == "moved-code": anchor["box"] = (2300, 700, 5000, 800)
            if mode == "overlaid": after["slides"][0]["units"].append({**anchor, "key": "overlay"})
            if mode == "student-role-flag": anchor["legacy_table_cell"] = True
            self.assertIsNone(c.assess(after, before)["notes_score"])

    def test_native_export_parsed_code_only_table_cell_keeps_its_existing_field_role(self):
        before = table_source()
        cell = table_element(before)["table"]["tableRows"][1]["tableCells"][2]
        cell["text"] = {"textElements": [{"textRun": {"content": "[[NOTES:C17:CELL]]"}}]}
        after = deepcopy(before)
        table_element(after)["table"]["tableRows"][1]["tableCells"][2]["text"]["textElements"][0]["textRun"]["content"] += "\n" + WORDS
        for teacher, student in product(("native", "pptx"), repeat=2):
            result = c.assess(parse_table(after, student), parse_table(before, teacher), found_codes_only=True)
            self.assertIsNone(result["note"])
            self.assertEqual(result["notes_score"], 10)

    def test_anchor_negative_survives_actual_native_and_pptx_readers(self):
        before = anchor_source()
        after = deepcopy(before); after["slides"][0]["units"][-1]["text"] += "\n" + WORDS
        def read(value, fmt):
            if fmt == "pptx": return c.read_deck(pptx([[(u["key"], u["text"], u["box"]) for u in p["units"]] for p in value["slides"]], size=value["size"]))
            raw = {"presentationId": FILE, "pageSize": {"width": {"magnitude": value["size"][0], "unit": "EMU"}, "height": {"magnitude": value["size"][1], "unit": "EMU"}},
                   "slides": [{"objectId": "page", "pageElements": [shape(u["key"], u["text"], u["box"]) for u in value["slides"][0]["units"]]}]}
            return n.read(raw, presentation_id=FILE, revision="1")
        for teacher, student in product(("native", "pptx"), repeat=2):
            result = c.assess(read(after, student), read(before, teacher))
            self.assertIsNone(result["notes_score"])
            self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_host_path_holds_anchor_edit_without_changing_unknown_starter_policy(self):
        before = anchor_source(); before["slides"].insert(0, {"number": 1, "units": [{"key": "inventory", "box": (100, 100, 9000, 300),
            "text": "[[MARKER-INVENTORY:Z91]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=EXPLAIN DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-"}]})
        before["slides"][1]["number"] = 2
        after = deepcopy(before); after["slides"][1]["units"][-1]["text"] += "\n" + WORDS
        sid, tid, fid = "FictionalAnchorCopy00001", FILE, "FictionalAnchorForm00001"
        form = {"form_id": fid, "label": "Fictional lesson", "grade_readiness": {"status": "READY"}, "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
        lesson = {"topic": "Fictional lesson", "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
        agenda = SimpleNamespace(classes={"anatomy": {"name": "Fictional anatomy"}}, lessons=lambda *a: [lesson])
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        with patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=after), \
             patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, before)), \
             patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
            result = c.score_file(None, agenda, "anatomy", "W03", fid, sid, {"lessons": [lesson]})
        self.assertEqual(spy.call_count, 1)
        self.assertIsNone(result["notes_score"])
        self.assertTrue(result["note"])

    def test_plain_answer_backplate_and_coded_bank_practice_controls(self):
        before = anchor_source("")
        before["slides"][0]["units"][-1]["text"] = "Question [[NOTES:Z91:EXPLAIN]]"
        before["slides"][0]["units"].append(deepcopy(before["slides"][0]["units"][1]))
        after = deepcopy(before); after["slides"][0]["units"][1]["text"] = WORDS
        self.assertIsNone(c.assess(after, before)["notes_score"])
        unit = lambda k, t, b: {"key": k, "text": t, "box": b, "title": False}
        bank = {"size": (10000, 6000), "slides": [{"number": 1, "units": [
            unit("label-token", "Token [[BANK:Z91:Token]]", (100, 700, 2000, 300)),
            unit("response", "", (2300, 700, 5000, 800)), unit("panel-token", "", (100, 700, 8000, 800)),
            unit("panel-widget", "", (100, 1800, 8000, 800)),
            unit("label-widget", "Widget [[BANK:Z91:Widget]]", (100, 1800, 2000, 300)),
            unit("response-widget", "", (2300, 1800, 5000, 800)),
            unit("practice-label", "Unscored extra practice", (100, 4000, 2000, 300)),
            unit("practice-response", "", (2300, 4000, 5000, 800))]}]}
        for key, expected in (("response", 5), ("practice-response", None)):
            after = deepcopy(bank); next(u for u in after["slides"][0]["units"] if u["key"] == key)["text"] = WORDS
            result = c.assess(after, bank)
            self.assertEqual(result["notes_score"], expected)
            self.assertEqual([i["id"] for i in result["items"] if i["status"] == "done"], ["Token"] if expected else [])


if __name__ == "__main__":
    unittest.main()
