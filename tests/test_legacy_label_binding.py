"""Invented declared labels; no school content, network or persistent state."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import completion as c, combined_assessment as combined, google
from test_legacy_context_overlap import parse, WORDS
from test_legacy_mixed_field_roles import unit, field, READERS

IDS = ("ALPHA", "BETA", "GAMMA")


def paired():
    inventory = "[[MARKER-INVENTORY:S99]] NOTES=3 DRAFT=3 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=" + ",".join(IDS) + \
        " DRAFT_IDS=" + ",".join(IDS) + " OPTIONAL_IDS=- BANK_IDS=-"
    page = {"number": 2, "units": [unit("title", "Invented paired questions", [40, 10, 640, 25])]}
    for n, qid in enumerate(IDS):
        y = 50 + 160 * n
        for role, category, x in (("first", "NOTES", 40), ("revised", "DRAFT", 380)):
            page["units"] += [unit(qid + "-" + role + "-label",
                f"Question {n + 1} - Your {role} answer [[{category}:S99:{qid}]]", [x, y, 300, 30]),
                unit(qid + "-" + role, "", [x, y + 40, 300, 80])]
    page["units"].append(unit("unused-child", "", [45, 55, 50, 15]))
    return {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", inventory, [0, 0, 700, 20])]}, page]}


def bank():
    inventory = "[[MARKER-INVENTORY:S99]] NOTES=0 DRAFT=0 OPTIONAL=0 BANK_TERMS=2 " \
        "NOTES_IDS=- DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=Amber,Cobalt"
    return {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", inventory, [0, 0, 700, 20])]},
        {"number": 2, "units": [unit("title", "Invented definitions", [40, 10, 640, 25]),
            unit("Amber-label", "Amber [[BANK:S99:Amber]]", [40, 60, 640, 30]),
            unit("Amber-answer", "", [40, 100, 640, 60]),
            unit("Cobalt-label", "Cobalt [[BANK:S99:Cobalt]]", [40, 165, 640, 30]),
            unit("Cobalt-answer", "", [40, 205, 640, 60])]}]}


def changed(before, key):
    after = deepcopy(before)
    if key: field(after, key)["text"] += "\n" + WORDS
    return after


def intended(kind):
    if kind == "bank": return [(term + "-answer", "BANK", term) for term in ("Amber", "Cobalt")]
    return [(qid + "-" + role, category, qid) for qid in IDS
            for role, category in (("first", "NOTES"), ("revised", "DRAFT"))]


def cases(kind):
    return [(None, None, None)] + intended(kind) + [
        ("Amber-label", None, None) if kind == "bank" else ("ALPHA-first-label", None, None)]


def footer(binding="unbound"):
    before = {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", "[[MARKER-INVENTORY:Z98]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=REASONING DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-", [0, 0, 700, 20])]},
        {"number": 2, "units": [unit("question", "Explain the invented observation. [[NOTES:Z98:REASONING]]", [43.2, 154.8, 633.6, 68.4]),
            unit("answer", "", [43.2, 237.6, 633.6, 241.2]), unit("strip", "", [36, 508.32, 648, 21.6])]}]}
    # Paint properties do not declare a response role. Serialize integral EMU.
    for page in before["slides"]:
        for u in page["units"]: u["box"] = [round(v) for v in u["box"]]
    if binding != "unbound": field(before, "question")["text"] = "Explain the invented observation."
    if binding == "direct": field(before, "answer")["text"] = "Write your answer here [[NOTES:Z98:REASONING]]"
    if binding == "point": before["slides"][1]["units"].append(unit("point", "[[NOTES:Z98:REASONING]]", [50, 245, 10, 5]))
    return before


def overhang(layout):
    header = "[[MARKER-INVENTORY:Z98]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=OVERHANG DRAFT_IDS=- OPTIONAL_IDS=- BANK_IDS=-"
    if layout == "question":
        units = [unit("background", "", [28.8, 198, 662.41, 63]),
            unit("question", "Explain an invented comparison. [[NOTES:Z98:OVERHANG]]", [40, 203, 639.99, 62.01]),
            unit("answer", "", [28.8, 273.6, 662.41, 94.49])]
    elif layout == "prompt":
        units = [unit("background", "", [28.8, 75.6, 388.8, 98.43]),
            unit("question", "Read this invented comparison before explaining.", [37, 81, 372, 96]),
            unit("label", "Explain the comparison. [[NOTES:Z98:OVERHANG]]", [28.8, 185.83, 662.41, 18]),
            unit("answer", "", [28.8, 208.66, 662.41, 149.6])]
    else:
        units = [unit("background", "", [28.8, 198, 662.41, 200]),
            unit("question", "An invented printed source question.", [40, 190, 639.99, 50]),
            unit("answer", "", [28.8, 273.6, 662.41, 94.49]),
            unit("point", "[[NOTES:Z98:OVERHANG]]", [50, 280, 10, 5])]
    before = {"size": (720 * 12700, 540 * 12700), "slides": [
        {"number": 1, "units": [unit("inventory", header, [0, 0, 700, 20])]}, {"number": 2, "units": units}]}
    for page in before["slides"]:
        for u in page["units"]: u["box"] = [round(v) for v in u["box"]]
    return before


def auxiliary(variant, binding="unbound"):
    before = footer(binding)
    field(before, "question")["box"] = [round(v * 12700) for v in (43.2, 90, 633.6, 50)]
    field(before, "answer")["box"] = [round(v * 12700) for v in (43.2, 200, 633.6, 270)]
    geometry = (644.4, 39.6, 39.6, 39.6) if variant == "corner" else (43.2, 151.2, 14.4, 14.4)
    field(before, "strip")["box"] = [round(v * 12700) for v in geometry]
    if binding == "point": field(before, "point")["box"] = [round(v * 12700) for v in (50, 210, 10, 5)]
    return before


class LabelBindingTests(unittest.TestCase):
    def held(self, result):
        self.assertIsNone(result["notes_score"])
        self.assertIsNone(result["revision_score"])
        self.assertTrue(result["note"])
        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def bound(self, result, category, qid, *, prefix=""):
        self.assertIsNone(result["note"])
        self.assertEqual([(i["category"], i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"],
                         [(category, prefix + qid, WORDS)])
        if category == "NOTES": self.assertEqual(result["counts"]["DRAFT"]["done"], 0)
        if category == "DRAFT": self.assertEqual(result["counts"]["NOTES"]["done"], 0)

    def test_every_paired_identity_and_bank_term_in_all_readers_and_policies(self):
        for kind, teacher, student, found in product(("paired", "bank"), READERS, READERS, (False, True)):
            before = bank() if kind == "bank" else paired(); original = deepcopy(before)
            self.assertEqual(c.assess(parse(before, student), parse(before, teacher), found_codes_only=found)["notes_score"], 0)
            for key, category, qid in intended(kind):
                with self.subTest(kind=kind, teacher=teacher, student=student, found=found, key=key):
                    self.bound(c.assess(parse(changed(before, key), student), parse(before, teacher), found_codes_only=found), category, qid)
            self.assertEqual(before, original)

    def test_role_prefix_suffix_alias_order_scale_and_unused_child_do_not_supply_identity(self):
        for variant, scale in product(("prefix", "suffix", "no-child", "background"), (.5, 1, 2)):
            before = paired()
            if variant == "suffix":
                for n, qid in enumerate(IDS):
                    for role, cat in (("first", "NOTES"), ("revised", "DRAFT")):
                        field(before, qid + "-" + role + "-label")["text"] = f"Your {role} answer - Question {n+1} [[{cat}:S99:{qid}]]"
            if variant == "no-child": before["slides"][1]["units"].remove(field(before, "unused-child"))
            if variant == "background": before["slides"][1]["units"].insert(0, unit("background", "", [0, 0, 720, 540]))
            before["size"] = tuple(int(v * scale) for v in before["size"])
            for page in before["slides"]:
                for u in page["units"]: u["box"] = [int(v * scale) for v in u["box"]]
            before["slides"][1]["units"].reverse()
            for teacher, student, (key, category, qid) in product(READERS, READERS, intended("paired")):
                after = changed(before, key)
                for i, u in enumerate(after["slides"][1]["units"]): u["key"] = "alias-" + str(i)
                self.bound(c.assess(parse(after, student), parse(before, teacher)), category, qid)

    def test_printed_labels_children_backgrounds_and_added_practice_do_not_gain_credit(self):
        before = paired()
        before["slides"][1]["units"] += [unit("background", "", [0, 0, 720, 540]),
            unit("extra-practice", "", [350, 495, 20, 20])]
        for key in ("title", "unused-child", "background", "extra-practice") + tuple(
                key + "-label" for key, _, _ in intended("paired")):
            self.held(c.assess(changed(before, key), before))
        before = bank()
        for key in ("title", "Amber-label", "Cobalt-label"):
            self.held(c.assess(changed(before, key), before))

    def test_competing_fields_labels_role_mismatch_and_detached_codes_hold(self):
        for mode in ("extra-field", "shared-field", "role-mismatch", "generic-label", "detached", "duplicate"):
            before = paired()
            if mode == "extra-field": before["slides"][1]["units"].append(unit("competing", "", [40, 90, 140, 80]))
            if mode == "shared-field":
                before["slides"][1]["units"].remove(field(before, "ALPHA-revised"))
                field(before, "ALPHA-first")["box"][2] = 640 * 12700
            if mode == "role-mismatch": field(before, "ALPHA-first-label")["text"] = "Your revised answer [[NOTES:S99:ALPHA]]"
            if mode == "generic-label": field(before, "ALPHA-first-label")["text"] = "An invented generic prompt [[NOTES:S99:ALPHA]]"
            if mode == "detached":
                for n, (key, cat, qid) in enumerate(intended("paired")):
                    label = field(before, key + "-label"); label["text"] = c._plain(label["text"])
                    before["slides"][1]["units"].append(unit("legend-" + str(n), f"[[{cat}:S99:{qid}]]", [10 + n * 110, 515, 100, 15]))
            if mode == "duplicate": field(before, "ALPHA-first-label")["text"] += " [[NOTES:S99:ALPHA]]"
            self.held(c.assess(changed(before, "ALPHA-first"), before))

    def test_generic_single_pair_with_unused_child_cannot_change_first_into_revision(self):
        before = paired()
        before["slides"][1]["units"] = [u for u in before["slides"][1]["units"] if u["key"].startswith("ALPHA-") or u["key"] == "unused-child"]
        field(before, "inventory")["text"] = "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=- BANK_IDS=-"
        for role, cat in (("first", "NOTES"), ("revised", "DRAFT")):
            field(before, "ALPHA-" + role + "-label")["text"] = f"An invented {role} prompt [[{cat}:S99:ALPHA]]"
        for teacher, student, role in product(READERS, READERS, ("first", "revised")):
            self.held(c.assess(parse(changed(before, "ALPHA-" + role), student), parse(before, teacher)))
        before["slides"][1]["units"].remove(field(before, "unused-child"))
        field(before, "ALPHA-first-label")["text"] = "An invented left revision prompt [[DRAFT:S99:ALPHA]]"
        field(before, "ALPHA-revised-label")["text"] = "An invented right initial prompt [[NOTES:S99:ALPHA]]"
        for teacher, student, role in product(READERS, READERS, ("first", "revised")):
            self.held(c.assess(parse(changed(before, "ALPHA-" + role), student), parse(before, teacher)))

    def test_canonical_pair_and_tight_complementary_point_preserve_exact_routes(self):
        before = paired()
        before["slides"][1]["units"] = [u for u in before["slides"][1]["units"] if u["key"].startswith("ALPHA-")]
        field(before, "inventory")["text"] = "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=- BANK_IDS=-"
        field(before, "ALPHA-first-label")["text"] = "[[NOTES:S99:ALPHA]]"
        field(before, "ALPHA-first-label")["box"] = [v * 12700 for v in (40, 70, 10, 10)]
        for teacher, student, role, cat in product(READERS, READERS, ("first", "revised"), ("NOTES", "DRAFT")):
            if cat == ("NOTES" if role == "first" else "DRAFT"):
                self.bound(c.assess(parse(changed(before, "ALPHA-" + role), student), parse(before, teacher)), cat, "ALPHA")
        for variant in ("far-point", "extra-blank", "generic-partner", "overlapping-candidates"):
            altered = deepcopy(before)
            if variant == "far-point": field(altered, "ALPHA-first-label")["box"][1] -= 40 * 12700
            if variant == "extra-blank": altered["slides"][1]["units"].append(unit("unused", "", [45, 10, 20, 10]))
            if variant == "generic-partner": field(altered, "ALPHA-revised-label")["text"] = "An invented revision prompt [[DRAFT:S99:ALPHA]]"
            if variant == "overlapping-candidates":
                for key, box in (("ALPHA-first-label", (400, 70, 10, 10)), ("ALPHA-revised-label", (380, 40, 300, 20)),
                                 ("ALPHA-first", (40, 85, 400, 80)), ("ALPHA-revised", (380, 85, 300, 80))):
                    field(altered, key)["box"] = [v * 12700 for v in box]
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(changed(altered, "ALPHA-first"), student), parse(altered, teacher)))

    def test_genuine_question_selection_and_quotation_with_explanation_remain_usable(self):
        before = paired()
        before["slides"][1]["units"] = [u for u in before["slides"][1]["units"] if u["key"].startswith("ALPHA-")]
        field(before, "inventory")["text"] = "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=- BANK_IDS=-"
        before["slides"][1]["units"].append(unit("options", "Select one supplied research question.\nWhy does it differ?\nHow does it grow?", [40, 10, 640, 25]))
        for teacher, student, answer in product(READERS, READERS,
                ("Why does it differ?", "Why does it differ? " + WORDS)):
            after = deepcopy(before); field(after, "ALPHA-revised")["text"] = answer
            result = c.assess(parse(after, student), parse(before, teacher))
            self.assertIsNone(result["note"])
            self.assertEqual(result["revision_score"], 10)
            self.assertEqual([(i["category"], i["id"], i["answer"]) for i in result["items"] if i["status"] == "done"], [("DRAFT", "ALPHA", answer)])

    def test_bank_shift_and_ties_do_not_transfer_to_neighbor(self):
        for shift in (0, -9):
            before = bank(); field(before, "Amber-answer")["box"][1] += shift * 12700
            for teacher, student in product(READERS, repeat=2):
                self.bound(c.assess(parse(changed(before, "Amber-answer"), student), parse(before, teacher)), "BANK", "Amber")
        before = bank(); field(before, "Amber-answer")["box"][1] = int(97.5 * 12700)
        self.held(c.assess(changed(before, "Amber-answer"), before))
        before = bank(); before["slides"][1]["units"].append(unit("competing", "", [40, 100, 300, 60]))
        self.held(c.assess(changed(before, "Amber-answer"), before))

    def test_same_row_bank_columns_bind_only_their_label_left_definition_right_pair(self):
        before = bank()
        for term, x in (("Amber", 40), ("Cobalt", 390)):
            field(before, term + "-label")["box"] = [v * 12700 for v in (x, 60, 100, 30)]
            field(before, term + "-answer")["box"] = [v * 12700 for v in (x + 110, 60, 180, 30)]
        for teacher, student, term in product(READERS, READERS, ("Amber", "Cobalt")):
            self.bound(c.assess(parse(changed(before, term + "-answer"), student), parse(before, teacher)), "BANK", term)
        # Reverse-side geometry does not declare a supported writing role.
        reverse = bank()
        field(reverse, "Amber-label")["box"] = [v * 12700 for v in (300, 60, 100, 30)]
        field(reverse, "Amber-answer")["box"] = [v * 12700 for v in (100, 60, 180, 30)]
        for teacher, student in product(READERS, repeat=2):
            self.held(c.assess(parse(changed(reverse, "Amber-answer"), student), parse(reverse, teacher)))

    def test_unique_detached_bank_labels_preserve_identity_but_never_count_printed_label_edits(self):
        before = bank()
        for n, term in enumerate(("Amber", "Cobalt")):
            field(before, term + "-label")["text"] = term
            before["slides"][1]["units"].append(unit("legend-" + term, f"[[BANK:S99:{term}]]", [10 + 120 * n, 480, 100, 15]))
        for teacher, student, key in product(READERS, READERS, ("Amber-answer", "Cobalt-answer", "Amber-label", "Cobalt-label")):
            result = c.assess(parse(changed(before, key), student), parse(before, teacher))
            if key.endswith("-answer"): self.bound(result, "BANK", key.split("-")[0])
            else: self.held(result)

    def test_conflicting_declared_label_and_point_or_direct_field_never_choose_precedence(self):
        for kind in ("point", "direct"):
            before = paired()
            field(before, "inventory")["text"] = field(before, "inventory")["text"].replace("NOTES=3", "NOTES=4").replace(
                "NOTES_IDS=ALPHA,BETA,GAMMA", "NOTES_IDS=ALPHA,BETA,GAMMA,POINT")
            if kind == "point": before["slides"][1]["units"].append(unit("point", "[[NOTES:S99:POINT]]", [45, 95, 10, 5]))
            else: field(before, "ALPHA-first")["text"] = "Write your answer here [[NOTES:S99:POINT]]"
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(changed(before, "ALPHA-first"), student), parse(before, teacher)))

    def test_missing_modified_student_codes_context_and_geometry_cannot_change_identity(self):
        before = paired()
        for mode in ("missing-code", "modified-code", "label-text", "deleted-label", "field-geometry", "both"):
            after = changed(before, "ALPHA-first")
            if mode == "missing-code": field(after, "ALPHA-first-label")["text"] = "Question 1 - Your first answer"
            if mode == "modified-code": field(after, "ALPHA-first-label")["text"] = "Question 1 - Your first answer [[DRAFT:S99:ALPHA]]"
            if mode == "label-text": field(after, "ALPHA-first-label")["text"] += " An altered source instruction."
            if mode == "deleted-label": after["slides"][1]["units"].remove(field(after, "ALPHA-first-label"))
            if mode == "field-geometry": field(after, "ALPHA-first")["box"] = field(after, "BETA-first")["box"][:]
            if mode == "both": field(after, "unused-child")["text"] = WORDS
            self.held(c.assess(after, before))

    def test_in_field_and_uniform_single_question_multipart_controls(self):
        before = paired()
        for key, cat, qid in intended("paired"):
            field(before, key + "-label")["text"] = c._plain(field(before, key + "-label")["text"])
            field(before, key)["text"] = f"Write your answer here [[{cat}:S99:{qid}]]"
        for teacher, student, (key, cat, qid) in product(READERS, READERS, intended("paired")):
            self.bound(c.assess(parse(changed(before, key), student), parse(before, teacher)), cat, qid)
        before = paired(); page = before["slides"][1]
        page["units"] = [unit("first-label", "Your first answer [[NOTES:S99:ALPHA]]", [40, 50, 300, 30]),
            unit("revised-label", "Your revised answer [[DRAFT:S99:ALPHA]]", [380, 50, 300, 30])]
        for n in range(3):
            for role, x in (("first", 40), ("revised", 380)):
                page["units"].append(unit(role + str(n), "", [x, 90 + 130 * n, 300, 80]))
        field(before, "inventory")["text"] = "[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=- BANK_IDS=-"
        for role, cat, n in product(("first", "revised"), ("NOTES", "DRAFT"), range(3)):
            if cat != ("NOTES" if role == "first" else "DRAFT"): continue
            self.bound(c.assess(changed(before, role + str(n)), before), cat, "ALPHA")

    def test_uniform_exception_never_discards_cross_category_label_conflicts(self):
        for extra in ("OPTIONAL", "BANK"):
            before = paired(); page = before["slides"][1]
            page["units"] = [unit("first-label", "Your first answer [[NOTES:S99:ALPHA]]", [40, 50, 300, 30]),
                unit("revised-label", "Your revised answer [[DRAFT:S99:ALPHA]]", [380, 50, 300, 30])]
            for n in range(3):
                for role, x in (("first", 40), ("revised", 380)):
                    page["units"].append(unit(role + str(n), "", [x, 90 + 130 * n, 300, 80]))
            page["units"].append(unit("extra-code", f"[[{extra}:S99:POINT]]", [45, 95, 10, 5]))
            if extra == "BANK": page["units"].append(unit("bank-label", "POINT", [40, 500, 100, 25]))
            field(before, "inventory")["text"] = ("[[MARKER-INVENTORY:S99]] NOTES=1 DRAFT=1 "
                + ("OPTIONAL=1 BANK_TERMS=0 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=POINT BANK_IDS=-" if extra == "OPTIONAL"
                   else "OPTIONAL=0 BANK_TERMS=1 NOTES_IDS=ALPHA DRAFT_IDS=ALPHA OPTIONAL_IDS=- BANK_IDS=POINT"))
            for teacher, student in product(READERS, repeat=2):
                self.held(c.assess(parse(changed(before, "first0"), student), parse(before, teacher)))

    def test_unbound_footer_cohort_holds_and_independent_field_roles_remain_usable(self):
        for binding in ("unbound", "direct", "point"):
            before = footer(binding)
            for teacher, student in product(READERS, repeat=2):
                self.assertEqual(c.assess(parse(before, student), parse(before, teacher))["notes_score"], 0)
                for key in ("strip", "answer"):
                    result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                    if binding == "unbound" or (binding == "direct" and key == "strip"): self.held(result)
                    elif key == "answer": self.bound(result, "NOTES", "REASONING")
                    else:
                        self.assertEqual(result["notes_score"], 0)
                        self.assertFalse(any(i["status"] == "done" for i in result["items"]))

    def test_printed_context_overhangs_never_make_backgrounds_writable(self):
        for layout in ("question", "prompt", "point"):
            before = overhang(layout)
            for teacher, student, found in product(READERS, READERS, (False, True)):
                self.assertEqual(c.assess(parse(before, student), parse(before, teacher), found_codes_only=found)["notes_score"], 0)
                self.bound(c.assess(parse(changed(before, "answer"), student), parse(before, teacher), found_codes_only=found), "NOTES", "OVERHANG")
                self.held(c.assess(parse(changed(before, "background"), student), parse(before, teacher), found_codes_only=found))

    def test_isolated_small_blanks_and_large_body_need_independent_roles(self):
        for variant, binding, teacher, student in product(("corner", "tier"), ("unbound", "direct", "point"), READERS, READERS):
            before = auxiliary(variant, binding)
            self.assertEqual(c.assess(parse(before, student), parse(before, teacher))["notes_score"], 0)
            for key in ("answer", "strip"):
                result = c.assess(parse(changed(before, key), student), parse(before, teacher))
                if binding != "unbound" and key == "answer": self.bound(result, "NOTES", "REASONING")
                elif binding == "point":
                    self.assertEqual(result["notes_score"], 0)
                    self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                else: self.held(result)

    def test_normal_host_both_policies_and_assignment_holds(self):
        fid, sid, tid = "FictionalLabelForm00001", "FictionalLabelCopy00001", "FictionalLabelSource001"
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        for kind, teacher, student, strict, held in product(("paired", "bank"), READERS, READERS, (False, True), (False, True)):
            before = bank() if kind == "bank" else paired()
            for key, category, qid in cases(kind):
                label = "Fictional photosynthesis task" if strict else "Fictional task"; cls = "biology" if strict else "anatomy"
                ready = {"status": "NOT_READY", "problem": "Fictional assignment hold"} if held else {"status": "READY"}
                form = {"form_id": fid, "label": label, "grade_readiness": ready,
                    "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
                lesson = {"topic": label, "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
                agenda = SimpleNamespace(classes={cls: {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
                original, submitted = parse(before, teacher), parse(changed(before, key), student)
                self.assertEqual(c.readiness(original)["status"], "READY")
                self.assertEqual(c.readiness(submitted)["status"], "READY")
                self.assertEqual(c.student_coding_required(agenda, cls, "W03", fid), strict)
                with patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted), \
                     patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, original)), \
                     patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
                    result = c.score_file(None, agenda, cls, "W03", fid, sid, {"lessons": [lesson]})
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready"); self.assertIsNone(result.get("notes_score"))
                elif key is None: self.assertEqual(result["notes_score"], 0)
                elif category: self.bound(result, category, qid)
                else: self.held(result)

    def test_footer_and_overhang_normal_hosts_keep_holds_and_exact_answer_controls(self):
        fixtures = [(footer(), "strip", None), (footer(), "answer", None),
            (footer("direct"), "strip", None), (footer("direct"), "answer", "REASONING"),
            (footer("point"), "strip", "zero"), (footer("point"), "answer", "REASONING")]
        fixtures += [(overhang(layout), key, "OVERHANG" if key == "answer" else None)
                     for layout, key in product(("question", "prompt", "point"), ("answer", "background"))]
        fixtures += [(auxiliary(variant, binding), key, "REASONING" if binding != "unbound" and key == "answer"
                      else "zero" if binding == "point" else None)
                     for variant, binding, key in product(("corner", "tier"), ("unbound", "direct", "point"), ("answer", "strip"))]
        fid, sid, tid = "FictionalContextForm0001", "FictionalContextCopy0001", "FictionalContextSource01"
        meta = {"id": sid, "name": "Fictional copy", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged", "trashed": False}
        for (before, key, expected), teacher, student, strict, held in product(fixtures, READERS, READERS, (False, True), (False, True)):
            label = "Fictional photosynthesis task" if strict else "Fictional task"; cls = "biology" if strict else "anatomy"
            ready = {"status": "NOT_READY", "problem": "Fictional assignment hold"} if held else {"status": "READY"}
            form = {"form_id": fid, "label": label, "grade_readiness": ready,
                "students": [{"files": [{"id": sid, "mime": c.SLIDES}]}]}
            lesson = {"topic": label, "forms": [form], "materials": [{"url": "https://docs.google.com/presentation/d/" + tid + "/edit"}]}
            agenda = SimpleNamespace(classes={cls: {"name": "Fictional class"}}, lessons=lambda *a: [lesson])
            original, submitted = parse(before, teacher), parse(changed(before, key), student)
            self.assertEqual(c.readiness(original)["status"], "READY")
            self.assertEqual(c.readiness(submitted)["status"], "READY")
            self.assertEqual(c.student_coding_required(agenda, cls, "W03", fid), strict)
            with self.subTest(key=key, expected=expected, teacher=teacher, student=student, strict=strict, held=held), \
                 patch.dict(c._results, {}, clear=True), patch.object(c, "_read", return_value=submitted), \
                 patch.object(c, "_teacher_template", return_value=({**meta, "id": tid}, original)), \
                 patch.object(google, "drive_file", return_value=meta), patch.object(c, "assess", wraps=c.assess) as spy:
                result = c.score_file(None, agenda, cls, "W03", fid, sid, {"lessons": [lesson]})
                self.assertEqual(spy.call_count, 0 if held else 1)
                if held:
                    self.assertEqual(result["code"], "deck_not_ready"); self.assertIsNone(result.get("notes_score"))
                elif expected == "zero":
                    self.assertEqual(result["notes_score"], 0)
                    self.assertFalse(any(i["status"] == "done" for i in result["items"]))
                elif expected: self.bound(result, "NOTES", expected)
                else: self.held(result)

    def test_combined_host_preserves_full_category_and_identity(self):
        meta = {"id": "FictionalCombinedSource001", "mimeType": c.SLIDES, "version": "1", "modifiedTime": "unchanged"}
        for kind, teacher, student in product(("paired", "bank"), READERS, READERS):
            before = bank() if kind == "bank" else paired()
            for key, cat, qid in cases(kind):
                originals = {"S99": {"metadata": meta, "sha256": "a" * 64, "deck": parse(before, teacher)}}
                copies = {"S99": {"metadata": {**meta, "id": "FictionalCombinedCopy001"}, "sha256": "b" * 64,
                    "deck": parse(changed(before, key), student)}}
                form = {"combined_assessment": {"scope": {"components": [{"cycle": "S99", "label": "Fictional task", "teacher_id": meta["id"]}]}}}
                row = {"combined_components": [{"cycle": "S99", "file_id": copies["S99"]["metadata"]["id"]}]}
                with patch.object(combined, "verify_sources", return_value=(originals, copies)):
                    result = combined.score_row(None, form, row)
                if key is None: self.assertEqual(result["notes_score"], 0)
                elif cat: self.bound(result, cat, qid, prefix="S99:")
                else: self.held(result)


if __name__ == "__main__":
    unittest.main()
