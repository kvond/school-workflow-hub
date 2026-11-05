"""Invented blank source containers, without school content or external writes."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import completion as c, combined_assessment as combined, google
from test_legacy_context_overlap import parse, WORDS, source as child_source
from test_legacy_mixed_field_roles import field, unit, READERS


def source(kind="page"):
    if kind == "bank": return bank_source()
    units = [unit("title", "An invented comparison", [40, 10, 640, 25]),
        unit("prompt", "Explain the invented observation. [[NOTES:S99:EXPLAIN]]", [40, 50, 640, 40]),
        unit("answer", "", [40, 380, 640, 120])]
    if kind in ("page", "point"):
        units.insert(0, unit("background", "", [0, 0, 720, 540]))
        if kind == "point":
            units[2]["text"] = "Explain the invented observation."
            units.append(unit("point-anchor", "[[NOTES:S99:EXPLAIN]]", [45, 385, 10, 5]))
    else:
        units[1:1] = [unit("left-panel", "", [40, 120, 300, 220]),
            unit("left-case", "A fictional first case with printed evidence.", [55, 145, 270, 150]),
            unit("right-panel", "", [380, 120, 300, 220]),
            unit("right-case", "A fictional second case with printed evidence.", [395, 145, 270, 150])]
    return {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=0 OPTIONAL=0 "
            "BANK_TERMS=0 NOTES_IDS=EXPLAIN DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-", [0, 0, 700, 20])]},
        {"number": 2, "units": units}]}


def keys(kind):
    if kind in ("page", "point"): return (None, "answer", "background", "title")
    if kind == "bank": return (None, "answer-0", "panel-0", "panel-1", "panel-2", "title")
    return (None, "answer", "left-panel", "right-panel", "title")


def bank_source():
    before = source()
    field(before, "inventory")["text"] = "[[MARKER-INVENTORY:S99]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=3 " \
        "NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=Amber,Cobalt,Jade"
    before["slides"][1]["units"] = [unit("title", "Invented terms", [40, 10, 640, 25])]
    for i, term in enumerate(("Amber", "Cobalt", "Jade")):
        y = 70 + i * 140
        before["slides"][1]["units"] += [unit("panel-" + str(i), "", [40, y, 640, 110]),
            unit("label-" + str(i), term + " [[BANK:S99:" + term + "]]", [50, y + 10, 120, 25]),
            unit("answer-" + str(i), "", [180, y + 10, 450, 80])]
    return before


def changed(before, key):
    after = deepcopy(before)
    if key: field(after, key)["text"] += "\n" + WORDS
    return after


class ContextContainerTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def completed(self, result, identity="EXPLAIN", score=10, category="NOTES"):
        self.assertIsNone(result["note"])
        self.assertEqual(result["notes_score"], score)
        self.assertEqual([(i["category"], i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"],
                         [(category, identity, WORDS)])

    def test_full_page_and_panels_hold_container_edits_preserving_exact_answer(self):
        for kind, teacher, student in product(("page", "panels"), READERS, READERS):
            before = source(kind); untouched = deepcopy(before)
            for key in keys(kind):
                with self.subTest(kind=kind, teacher=teacher, student=student, key=key):
                    result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                    if key is None: self.assertEqual(result["notes_score"], 0)
                    elif key == "answer": self.completed(result)
                    else: self.held(result)
            self.assertEqual(before, untouched)

    def test_twenty_independent_questions_keep_every_exact_response_identity(self):
        before = source(); prototype = before["slides"].pop()
        identities = ["WRITE-" + str(i).zfill(2) for i in range(1, 21)]
        inventory = field(before, "inventory")
        inventory["text"] = inventory["text"].replace("NOTES=1", "NOTES=20").replace("NOTES_IDS=EXPLAIN", "NOTES_IDS=" + ",".join(identities))
        for i, identity in enumerate(identities):
            page = deepcopy(prototype); page["number"] = i + 2
            for u in page["units"]:
                u["key"] = identity + "/" + u["key"]
                u["text"] = u["text"].replace("[[NOTES:S99:EXPLAIN]]", "[[NOTES:S99:" + identity + "]]")
            before["slides"].append(page)
        for teacher, student in product(READERS, repeat=2):
            self.assertEqual(c.assess(parse(before, student), parse(before, teacher))["notes_score"], 0)
            for identity, role in product(identities, ("answer", "background")):
                result = c.assess(parse(changed(before, identity + "/" + role), student), parse(before, teacher))
                if role == "answer": self.completed(result, identity, .5)
                else: self.held(result)

    def test_original_aliases_order_scaling_and_placeholders_do_not_grant_container_roles(self):
        for kind, starter, scale in product(("page", "panels"), ("", "Write your answer here"), (.5, 1, 2)):
            before = source(kind)
            containers = ("background",) if kind == "page" else ("left-panel", "right-panel")
            for key in containers: field(before, key)["text"] = starter
            before["size"] = tuple(int(v * scale) for v in before["size"])
            for page in before["slides"]:
                for u in page["units"]: u["box"] = [int(v * scale) for v in u["box"]]
            before["slides"][1]["units"].reverse()
            for teacher, student, key in product(READERS, READERS, containers + ("answer",)):
                after = changed(before, key)
                for i, u in enumerate(after["slides"][1]["units"]): u["key"] = "alias-" + str(i)
                result = c.assess(parse(after, student), parse(before, teacher))
                if key == "answer": self.completed(result)
                else: self.held(result)

    def test_student_marker_role_geometry_and_combined_edits_cannot_override_original(self):
        before = source()
        for mode in ("insert-code", "missing-code", "role", "duplicate-geometry", "context", "both"):
            after = changed(before, "background"); container = field(after, "background")
            if mode == "insert-code": container["text"] += " [[NOTES:S99:EXPLAIN]]"
            if mode == "missing-code": field(after, "prompt")["text"] = "Explain the invented observation."
            if mode == "role": container["role"] = "answer"
            if mode == "duplicate-geometry": container["box"] = field(after, "answer")["box"][:]
            if mode == "context": field(after, "prompt")["text"] += "\n" + WORDS
            if mode == "both": field(after, "answer")["text"] = WORDS
            self.held(c.assess(after, before))

    def test_direct_in_field_identity_retains_its_existing_validator(self):
        before = source()
        field(before, "prompt")["text"] = "Explain the invented observation."
        field(before, "answer")["text"] = "Write your answer here [[NOTES:S99:EXPLAIN]]"
        for teacher, student in product(READERS, repeat=2):
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))
            self.held(c.assess(parse(changed(before, "background"), student), parse(before, teacher)))

    def test_containment_tolerance_keeps_separate_external_answer(self):
        before = source("panels")
        # Printed source nearly flush with its panel still cannot authenticate
        # writing on the enclosing panel, including sub-point export rounding.
        field(before, "left-case")["box"] = field(before, "left-panel")["box"][:]
        field(before, "left-case")["box"][0] -= .25 * 12700
        field(before, "left-case")["box"][2] -= 2 * 12700
        field(before, "left-case")["box"][3] -= 2 * 12700
        for teacher, student in product(READERS, repeat=2):
            # The toy PPTX serializer requires integral EMU coordinates.
            for u in before["slides"][1]["units"]: u["box"] = [int(v) for v in u["box"]]
            self.held(c.assess(parse(changed(before, "left-panel"), student), parse(before, teacher)))
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))

    def test_reversed_original_child_fixtures_keep_blank_answer_and_source_controls(self):
        for scenario in ("page", "panel"):
            before = child_source()
            if scenario == "page": field(before, "empty-placeholder")["box"] = [0, 0, 720 * 12700, 540 * 12700]
            else:
                field(before, "empty-placeholder")["box"] = [43 * 12700, 54 * 12700, 634 * 12700, 230 * 12700]
                field(before, "instructions")["box"] = [50 * 12700, 65 * 12700, 620 * 12700, 210 * 12700]
            for teacher, student, key in product(READERS, READERS, (None, "empty-placeholder", "answer", "instructions")):
                result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                if key is None: self.assertEqual(result["notes_score"], 0)
                elif key == "answer": self.completed(result)
                else: self.held(result)

    def test_large_separate_answer_keeps_its_role_without_a_size_threshold(self):
        before = source()
        field(before, "answer")["box"] = [10 * 12700, 100 * 12700, 700 * 12700, 430 * 12700]
        for teacher, student in product(READERS, repeat=2):
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))
            self.held(c.assess(parse(changed(before, "background"), student), parse(before, teacher)))

    def test_point_route_background_refusal_preserves_genuine_and_thin_point_answers(self):
        before = source("point")
        for teacher, student in product(READERS, repeat=2):
            self.assertEqual(c.assess(parse(before, student), parse(before, teacher))["notes_score"], 0)
            self.completed(c.assess(parse(changed(before, "answer"), student), parse(before, teacher)))
            self.held(c.assess(parse(changed(before, "background"), student), parse(before, teacher)))
            after = deepcopy(before)
            after["slides"][1]["units"].append(unit("thin-answer", WORDS, [43, 380, 300, 10]))
            self.completed(c.assess(parse(after, student), parse(before, teacher)))

    def test_point_route_new_resized_or_reordered_containers_cannot_erase_original_context(self):
        before = source("point")
        for mode in ("new", "resized", "moved", "deleted-context", "both", "aliases"):
            after = changed(before, "background")
            if mode == "new":
                field(after, "background")["text"] = ""
                after["slides"][1]["units"].append(unit("new-container", WORDS, [0, 0, 710, 530]))
            if mode == "resized": field(after, "background")["box"][2] -= 5 * 12700
            if mode == "moved": field(after, "background")["box"][0] += 5 * 12700
            if mode == "deleted-context": after["slides"][1]["units"] = [u for u in after["slides"][1]["units"] if u["key"] != "prompt"]
            if mode == "both": field(after, "answer")["text"] = WORDS
            if mode == "aliases":
                after["slides"][1]["units"].reverse()
                for i, u in enumerate(after["slides"][1]["units"]): u["key"] = "alias-" + str(i)
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(after, student), parse(before, teacher)))

    def test_each_bank_panel_holds_without_spilling_to_its_real_or_neighbor_answer(self):
        before = source("bank")
        for teacher, student, i in product(READERS, READERS, range(3)):
            term = ("Amber", "Cobalt", "Jade")[i]
            self.completed(c.assess(parse(changed(before, "answer-" + str(i)), student), parse(before, teacher)), term, 3.3, "BANK")
            self.held(c.assess(parse(changed(before, "panel-" + str(i)), student), parse(before, teacher)))

    def test_score_file_both_policies_and_explicit_assignment_holds(self):
        fid, sid, tid = "FictionalContainerForm001", "FictionalContainerCopy001", "FictionalContainerSource1"
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        for kind, class_key, teacher, student, held in product(("page", "panels", "point", "bank"), ("anatomy", "biology"), READERS, READERS, (False, True)):
            before = source(kind)
            for key in keys(kind):
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
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready")
                    self.assertIsNone(result.get("notes_score"))
                elif key is None: self.assertEqual(result["notes_score"], 0)
                elif kind == "point" and key == "title":
                    self.assertEqual(result["notes_score"], 0)
                    self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                elif key == "answer": self.completed(result)
                elif key == "answer-0": self.completed(result, "Amber", 3.3, "BANK")
                else: self.held(result)

    def test_combined_host_retains_the_same_container_refusal(self):
        meta = {"id": "FictionalCombinedSource001", "name": "Fictional source", "mimeType": c.SLIDES,
                "version": "1", "modifiedTime": "unchanged"}
        for kind, teacher, student in product(("page", "panels", "point", "bank"), READERS, READERS):
            before = source(kind)
            for key in keys(kind):
                originals = {"S99": {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, teacher)}}
                copies = {"S99": {"metadata": {**meta, "id": "FictionalCombinedCopy00001"},
                                   "sha256": "b" * 64, "deck": parse(changed(before, key), student)}}
                form = {"combined_assessment": {"scope": {"components": [
                    {"cycle": "S99", "label": "Fictional task", "teacher_id": meta["id"]}]}}}
                row = {"combined_components": [{"cycle": "S99", "file_id": copies["S99"]["metadata"]["id"]}]}
                with patch.object(combined, "verify_sources", return_value=(originals, copies)), \
                     patch.object(c, "assess", wraps=c.assess) as spy:
                    result = combined.score_row(None, form, row)
                self.assertEqual(spy.call_count, 1)
                if key is None: self.assertEqual(result["notes_score"], 0)
                elif kind == "point" and key == "title":
                    self.assertEqual(result["notes_score"], 0)
                    self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                elif key == "answer": self.completed(result, "S99:EXPLAIN")
                elif key == "answer-0": self.completed(result, "S99:Amber", 3.3, "BANK")
                else: self.held(result)


if __name__ == "__main__":
    unittest.main()
