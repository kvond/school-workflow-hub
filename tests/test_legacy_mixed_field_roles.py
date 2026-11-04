"""Invented mixed-role rectangles; no school data, network or state writes."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import completion as c, combined_assessment as combined, google
from test_legacy_response_attribution import parse

EMU = 12700
WORDS = "I connect the stated observation to the proposed claim and explain why it supports the comparison."
READERS = ("native", "pptx")


def unit(key, text, box):
    return {"key": key, "text": text, "box": [v * EMU for v in box]}


def source(*, fixed=False):
    prompt = "Explain which observation supports the claim."
    tag = "[[NOTES:S99:EXPLAIN]]"
    page = {"number": 2, "units": [
        unit("prompt", prompt if fixed else prompt + " " + tag, [40, 40, 580, 60]),
        unit("instructions", "Move the two cards into matching categories before writing.", [40, 125, 620, 25]),
        unit("target-a", "", [300, 160, 140, 25]), unit("target-b", "", [460, 160, 140, 25]),
        unit("writer", "", [40, 220, 620, 260])]}
    if fixed: page["units"].append(unit("original-anchor", tag, [45, 225, 10, 5]))
    return {"size": (720 * EMU, 540 * EMU), "slides": [{"number": 1, "units": [unit("inventory",
        "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=EXPLAIN DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-", [0, 0, 700, 20])]}, page]}


def field(value, key):
    return next(u for page in value["slides"] for u in page["units"] if u["key"] == key)


class MixedFieldRoleTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_every_unbound_field_holds_in_all_reader_combinations(self):
        before = source()
        for teacher, student in product(READERS, repeat=2):
            self.assertEqual(c.assess(parse(before, student), parse(before, teacher))["notes_score"], 0)
            for key in ("target-a", "target-b", "writer"):
                after = deepcopy(before); field(after, key)["text"] = WORDS
                with self.subTest(teacher=teacher, student=student, key=key):
                    self.held(c.assess(parse(after, student), parse(before, teacher)))
            after = deepcopy(before)
            for key in ("target-a", "target-b", "writer"): field(after, key)["text"] = WORDS
            self.held(c.assess(parse(after, student), parse(before, teacher)))

    def test_independent_original_binding_preserves_writer_but_not_fallback_regions(self):
        before = source(fixed=True)
        for teacher, student in product(READERS, repeat=2):
            for key in ("writer", "target-a", "target-b"):
                after = deepcopy(before); field(after, key)["text"] = WORDS
                result = c.assess(parse(after, student), parse(before, teacher))
                if key == "writer":
                    self.assertIsNone(result["note"])
                    self.assertEqual(result["notes_score"], 10)
                    self.assertEqual([(i["category"], i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"], [("NOTES", "EXPLAIN", WORDS)])
                else:
                    self.assertIn(result["notes_score"], (None, 0))
                    self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_alias_order_instruction_and_scale_do_not_authorize_a_role(self):
        for scale in (.5, 1, 2):
            before = source()
            before["size"] = tuple(v * scale for v in before["size"])
            for p in before["slides"]:
                for u in p["units"]: u["box"] = [v * scale for v in u["box"]]
            field(before, "instructions")["text"] = "Fictional source directions without a role declaration."
            before["slides"][1]["units"].reverse()
            for key in ("writer", "target-b"):
                after = deepcopy(before); field(after, key)["text"] = WORDS
                for index, u in enumerate(after["slides"][1]["units"]): u["key"] = "renamed" + str(index)
                self.held(c.assess(after, before))

    def test_missing_code_and_copy_geometry_cannot_lend_another_field_credit(self):
        before = source()
        for mode in ("missing-code", "larger-widget", "moved-widget", "fake-role"):
            after = deepcopy(before); target = field(after, "target-a"); target["text"] = WORDS
            if mode == "missing-code": field(after, "prompt")["text"] = "Explain which observation supports the claim."
            if mode == "larger-widget": target["box"] = field(before, "writer")["box"][:]
            if mode == "moved-widget": target["box"][1] += 50 * EMU
            if mode == "fake-role": target["role"] = "scored explanation"
            self.held(c.assess(after, before))

    def test_uniform_multipart_originals_keep_their_established_identity(self):
        for arrangement in ("stacked", "row"):
            before = source(); page = before["slides"][1]
            page["units"] = [page["units"][0]] + [unit("fragment" + str(i), "",
                [40, 150 + i * 100, 620, 80] if arrangement == "stacked" else [40 + i * 205, 150, 190, 100]) for i in range(3)]
            for i in range(3):
                after = deepcopy(before); field(after, "fragment" + str(i))["text"] = WORDS
                result = c.assess(after, before)
                self.assertIsNone(result["note"])
                self.assertEqual(result["notes_score"], 10)
                self.assertEqual([x["id"] for x in result["items"] if x["status"] == "done"], ["EXPLAIN"])

    def test_normal_host_both_policies_preserve_attribution_and_assignment_holds(self):
        before = source()
        fid, sid, tid = "FictionalMixedForm000001", "FictionalMixedCopy000001", "FictionalMixedSource0001"
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        for class_key, teacher, student, key, held in product(
                ("anatomy", "biology"), READERS, READERS, (None, "target-a", "target-b", "writer"), (False, True)):
            ready = {"status": "NOT_READY", "problem": "Fictional assignment hold"} if held else {"status": "READY"}
            label = "Fictional photosynthesis task" if class_key == "biology" else "Fictional mixed task"
            form = {"form_id": fid, "label": label, "grade_readiness": ready,
                    "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
            lesson = {"topic": "Fictional mixed task", "forms": [form], "materials": [
                {"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
            agenda = SimpleNamespace(classes={class_key: {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
            after = deepcopy(before)
            if key: field(after, key)["text"] = WORDS
            parsed_teacher, parsed_student = parse(before, teacher), parse(after, student)
            self.assertEqual(c.readiness(parsed_teacher)["status"], "READY")
            self.assertEqual(c.readiness(parsed_student)["status"], "READY")
            self.assertEqual(c.student_coding_required(agenda, class_key, "W03", fid), class_key == "biology")
            with self.subTest(class_key=class_key, teacher=teacher, student=student, key=key, held=held), \
                 patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=parsed_student), \
                 patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, parsed_teacher)), \
                 patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
                result = c.score_file(None, agenda, class_key, "W03", fid, sid, {"lessons": [lesson]})
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready")
                    self.assertIsNone(result.get("notes_score"))
                elif key: self.held(result)
                else: self.assertEqual(result["notes_score"], 0)

    def test_combined_host_cannot_bypass_ambiguous_original_role_hold(self):
        before = source()
        meta = {"id": "FictionalCombinedSource0001", "name": "Fictional source", "mimeType": c.SLIDES,
                "version": "1", "modifiedTime": "unchanged"}
        for teacher, student, key in product(READERS, READERS, ("target-a", "target-b", "writer")):
            after = deepcopy(before); field(after, key)["text"] = WORDS
            originals = {"S99": {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, teacher)}}
            copies = {"S99": {"metadata": {**meta, "id": "FictionalCombinedCopy000001"},
                               "sha256": "b" * 64, "deck": parse(after, student)}}
            form = {"combined_assessment": {"scope": {"components": [
                {"cycle": "S99", "label": "Fictional task", "teacher_id": meta["id"]}]}}}
            row = {"combined_components": [{"cycle": "S99", "file_id": copies["S99"]["metadata"]["id"]}]}
            with patch.object(combined, "verify_sources", return_value=(originals, copies)), \
                 patch.object(c, "assess", wraps=c.assess) as spy:
                result = combined.score_row(None, form, row)
            self.assertEqual(spy.call_count, 1)
            self.held(result)


if __name__ == "__main__":
    unittest.main()
