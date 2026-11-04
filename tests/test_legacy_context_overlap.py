"""Invented instruction-overlap evidence; no school data or external writes."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import completion as c, combined_assessment as combined, google
from test_legacy_mixed_field_roles import field, unit, READERS
from test_legacy_response_attribution import parse as parse_fixture

WORDS = "I compare the stated evidence with the claim and explain the limit of this interpretation."


def parse(value, reader):
    # The shared toy exporter otherwise fixes its presentation size. Serialize
    # the actual source size so scaling tests reparse a valid sized PPTX too.
    with patch("test_explicit_response_binding.SIZE", value["size"]):
        return parse_fixture(value, reader)


def source():
    return {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=0 OPTIONAL=0 "
            "BANK_TERMS=0 NOTES_IDS=EXPLAIN DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-", [0, 0, 700, 20])]},
        {"number": 2, "units": [
            unit("title", "A comparison from observed results", [40, 8, 650, 22]),
            unit("instructions", "Compare the two examples. Explain which observation supports the proposed claim. "
                 "Identify a limit to the conclusion.", [43, 54, 634, 230]),
            unit("empty-placeholder", "", [43, 98, 634, 24]),
            unit("resource-label", "Background reference [[NOTES:S99:EXPLAIN]]", [43, 330, 634, 25]),
            unit("answer", "", [43, 365, 634, 130])]}]}


def changed(before, key):
    after = deepcopy(before)
    if key: field(after, key)["text"] += "\n" + WORDS
    return after


class ContextOverlapTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def completed(self, result, identity="EXPLAIN", score=10):
        self.assertIsNone(result["note"])
        self.assertEqual(result["notes_score"], score)
        self.assertEqual([(i["category"], i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"],
                         [("NOTES", identity, WORDS)])

    def test_full_assess_preserves_blank_and_separate_answer_but_holds_source_children(self):
        before = source(); original = deepcopy(before)
        for teacher, student, key in product(READERS, READERS,
                (None, "answer", "empty-placeholder", "title", "instructions", "resource-label")):
            with self.subTest(teacher=teacher, student=student, key=key):
                result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                if key is None: self.assertEqual(result["notes_score"], 0)
                elif key == "answer": self.completed(result)
                else: self.held(result)
        self.assertEqual(before, original)

    def test_original_placeholder_text_order_aliases_and_scale_cannot_grant_a_role(self):
        for starter, scale in product(("", "Write your answer here"), (.5, 1, 2)):
            before = source(); field(before, "empty-placeholder")["text"] = starter
            before["size"] = tuple(int(v * scale) for v in before["size"])
            for page in before["slides"]:
                for u in page["units"]: u["box"] = [int(v * scale) for v in u["box"]]
            field(before, "instructions")["text"] = "An unchanged printed source passage without semantic role hints."
            before["slides"][1]["units"].reverse()
            for teacher, student, key in product(READERS, READERS, ("empty-placeholder", "answer")):
                after = changed(before, key)
                for i, u in enumerate(after["slides"][1]["units"]): u["key"] = "export-alias-" + str(i)
                result = c.assess(parse(after, student), parse(before, teacher))
                if key == "answer": self.completed(result)
                else: self.held(result)

    def test_containing_prompt_marker_center_does_not_authenticate_its_child(self):
        before = source()
        field(before, "instructions")["text"] += " [[NOTES:S99:EXPLAIN]]"
        field(before, "resource-label")["text"] = "Background reference"
        # The large printed prompt's marker center now happens to fall in the
        # child. That legacy fixed-map heuristic supplies no independent role.
        field(before, "empty-placeholder")["box"][1] = 157 * 12700
        for teacher, student in product(READERS, repeat=2):
            self.held(c.assess(parse(changed(before, "empty-placeholder"), student), parse(before, teacher)))
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))

    def test_original_census_retains_the_separate_later_identity(self):
        before = source()
        field(before, "inventory")["text"] = field(before, "inventory")["text"].replace(
            "NOTES=1", "NOTES=2").replace("NOTES_IDS=EXPLAIN", "NOTES_IDS=FIRST,SECOND")
        field(before, "resource-label")["text"] = "First reference [[NOTES:S99:FIRST]]"
        before["slides"][1]["units"].append(unit("second-label", "Second reference [[NOTES:S99:SECOND]]", [43, 510, 634, 25]))
        for teacher, student in product(READERS, repeat=2):
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)), "SECOND", 5)
            self.held(c.assess(parse(changed(before, "empty-placeholder"), student), parse(before, teacher)))

    def test_invalid_original_geometry_cannot_restore_fallback_ownership(self):
        for mode in ("context-outside-page", "child-outside-page", "copy-page-mismatch"):
            before = source()
            if mode == "context-outside-page": field(before, "instructions")["box"][2] = 700 * 12700
            if mode == "child-outside-page":
                field(before, "instructions")["box"][2] = 710 * 12700
                field(before, "empty-placeholder")["box"][2] = 705 * 12700
            after = changed(before, "empty-placeholder")
            if mode == "copy-page-mismatch": after["size"] = (400 * 12700, 540 * 12700)
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(after, student), parse(before, teacher)))

    def test_off_canvas_printed_context_still_contains_children_but_not_external_answers(self):
        before = source()
        field(before, "instructions")["box"][0] = -10 * 12700
        field(before, "instructions")["box"][2] = 750 * 12700
        before["slides"][1]["units"].append(unit("outside-context", "Unrelated printed source", [740, 30, 200, 20]))
        for teacher, student in product(READERS, repeat=2):
            self.held(c.assess(parse(changed(before, "empty-placeholder"), student), parse(before, teacher)))
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))

    def test_unusable_printed_context_geometry_holds_fallback_writing(self):
        for box in (None, [43 * 12700, 54 * 12700, 0, 230 * 12700], [float("nan"), 0, 100, 100]):
            before = source(); field(before, "instructions")["box"] = box
            self.held(c.assess(changed(before, "empty-placeholder"), before))

    def test_student_marker_role_geometry_and_context_edits_cannot_override_original(self):
        before = source()
        for mode in ("insert-code", "missing-code", "role", "moved", "duplicate", "context", "both"):
            after = changed(before, "empty-placeholder")
            child = field(after, "empty-placeholder")
            if mode == "insert-code": child["text"] += " [[NOTES:S99:EXPLAIN]]"
            if mode == "missing-code": field(after, "resource-label")["text"] = "Background reference"
            if mode == "role": child["role"] = "answer"
            if mode == "moved": child["box"][1] += 240 * 12700
            if mode == "duplicate": child["box"] = field(after, "answer")["box"][:]
            if mode == "context": field(after, "instructions")["text"] += "\n" + WORDS
            if mode == "both": field(after, "answer")["text"] = WORDS
            self.held(c.assess(after, before))

    def test_score_file_both_policies_and_assignment_gate(self):
        before = source()
        fid, sid, tid = "FictionalOverlapForm00001", "FictionalOverlapCopy00001", "FictionalOverlapSource001"
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        for class_key, teacher, student, key, held in product(("anatomy", "biology"), READERS, READERS,
                (None, "answer", "empty-placeholder", "instructions", "title"), (False, True)):
            ready = {"status": "NOT_READY", "problem": "Fictional assignment hold"} if held else {"status": "READY"}
            label = "Fictional photosynthesis task" if class_key == "biology" else "Fictional task"
            form = {"form_id": fid, "label": label, "grade_readiness": ready,
                    "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
            lesson = {"topic": label, "forms": [form], "materials": [
                {"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
            agenda = SimpleNamespace(classes={class_key: {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
            original, submitted = parse(before, teacher), parse(changed(before, key), student)
            self.assertEqual(c.readiness(original)["status"], "READY")
            self.assertEqual(c.readiness(submitted)["status"], "READY")
            self.assertEqual(c.student_coding_required(agenda, class_key, "W03", fid), class_key == "biology")
            with patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted), \
                 patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, original)), \
                 patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
                result = c.score_file(None, agenda, class_key, "W03", fid, sid, {"lessons": [lesson]})
            with self.subTest(class_key=class_key, teacher=teacher, student=student, key=key, held=held):
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready")
                    self.assertIsNone(result.get("notes_score"))
                elif key is None: self.assertEqual(result["notes_score"], 0)
                elif key == "answer": self.completed(result)
                else: self.held(result)

    def test_combined_host_uses_the_same_containment_refusal(self):
        before = source()
        meta = {"id": "FictionalCombinedSource0001", "name": "Fictional source", "mimeType": c.SLIDES,
                "version": "1", "modifiedTime": "unchanged"}
        for teacher, student, key in product(READERS, READERS, (None, "answer", "empty-placeholder", "instructions")):
            originals = {"S99": {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, teacher)}}
            copies = {"S99": {"metadata": {**meta, "id": "FictionalCombinedCopy000001"},
                               "sha256": "b" * 64, "deck": parse(changed(before, key), student)}}
            form = {"combined_assessment": {"scope": {"components": [
                {"cycle": "S99", "label": "Fictional task", "teacher_id": meta["id"]}]}}}
            row = {"combined_components": [{"cycle": "S99", "file_id": copies["S99"]["metadata"]["id"]}]}
            with patch.object(combined, "verify_sources", return_value=(originals, copies)), \
                 patch.object(c, "assess", wraps=c.assess) as spy:
                result = combined.score_row(None, form, row)
            self.assertEqual(spy.call_count, 1)
            if key is None: self.assertEqual(result["notes_score"], 0)
            elif key == "answer": self.completed(result, "S99:EXPLAIN")
            else: self.held(result)


if __name__ == "__main__":
    unittest.main()
