from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from school_workflow_hub.workflows.week_preflight import (
    PreflightInputError, render_text, run_preflight,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "week_preflight_synthetic.json"


def week():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def lesson(snapshot, lesson_id):
    return next(item for item in snapshot["lessons"] if item["lesson_id"] == lesson_id)


def codes(result):
    return {(item["lesson_id"], item["system"], item["code"]) for item in result["exceptions"]}


class CleanWeekTests(unittest.TestCase):
    def test_clean_week_is_ready_with_packet_list_and_no_exceptions(self):
        result = run_preflight(week())
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["exceptions"], [])
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)
        packet = [(row["day"], row["title"], row["copies"]) for row in result["print_packet"]]
        # 46 Biology students + 5 spares; 12 groups of four across two sections.
        self.assertEqual(packet, [("Monday 10/19", "Crossing Cases worksheet", 51),
                                  ("Wednesday 10/21", "Egg lab sheet", 12)])
        supplies = {row["item"]: (row["to_get"], row["grocery"]) for row in result["supplies"]}
        self.assertEqual(supplies["Raisins"], (24, True))
        self.assertEqual(supplies["Clear cups"], (14, False))  # 24 needed, 10 on hand
        self.assertEqual(supplies["Evidence bags"], (18, False))
        self.assertEqual(list(supplies)[:3], ["Raisins", "Eggs", "White vinegar"])

    def test_result_is_deterministic(self):
        self.assertEqual(run_preflight(week())["stable_id"], run_preflight(week())["stable_id"])

    def test_render_shows_only_ready_line_and_outputs(self):
        text = render_text(run_preflight(week()))
        self.assertTrue(text.startswith("Week synthetic-w08 preflight: ready to run"))
        self.assertNotIn("Fix before Monday", text)
        self.assertIn("Crossing Cases worksheet ×51", text)


class SyncTests(unittest.TestCase):
    def test_student_link_to_editable_deck_and_title_drift(self):
        snapshot = week()
        row = snapshot["systems"]["schoology"][0]
        row["deck_link_id"] = lesson(snapshot, row["lesson_id"])["deck"]["editable_id"]
        snapshot["systems"]["agenda"][1]["title"] = "Membrane crossings (old)"
        found = codes(run_preflight(snapshot))
        self.assertIn(("bio-w08-l1", "schoology", "student_link_points_to_editable"), found)
        self.assertIn(("bio-w08-l1", "agenda", "title_mismatch"), found)

    def test_title_punctuation_and_case_are_not_drift(self):
        snapshot = week()
        snapshot["systems"]["agenda"][0]["title"] = "membrane crossings!"
        self.assertEqual(run_preflight(snapshot)["status"], "READY")

    def test_missing_section_unpublished_unposted_material_and_orphan(self):
        snapshot = week()
        schoology = snapshot["systems"]["schoology"]
        schoology[:] = [row for row in schoology
                        if not (row["lesson_id"] == "bio-w08-l2" and row["section_id"] == "BIO-B")]
        schoology[0]["published"] = False
        schoology[0]["material_ids"] = []
        snapshot["systems"]["agenda"].append({"lesson_id": "bio-w07-l3", "section_id": "BIO-A",
                                              "title": "Last week", "date": "2026-10-19"})
        found = codes(run_preflight(snapshot))
        self.assertIn(("bio-w08-l2", "schoology", "missing_for_section"), found)
        self.assertIn(("bio-w08-l1", "schoology", "not_published"), found)
        self.assertIn(("bio-w08-l1", "schoology", "material_not_posted"), found)
        self.assertIn(("bio-w07-l3", "agenda", "orphan_entry"), found)

    def test_folder_copy_and_student_copy_identity(self):
        snapshot = week()
        target = lesson(snapshot, "for-w08-l1")
        target["deck"]["student_copy_id"] = target["deck"]["editable_id"]
        snapshot["systems"]["folder"][0]["material_ids"] = []
        found = codes(run_preflight(snapshot))
        self.assertIn(("for-w08-l1", "deck", "student_copy_is_editable"), found)
        self.assertIn(("bio-w08-l1", "folder", "material_missing_in_folder"), found)

    def test_turn_in_form_link_closed_and_stale(self):
        snapshot = week()
        target = lesson(snapshot, "bio-w08-l2")
        target["turn_in"]["accepting_responses"] = False
        target["deck"]["revision"] = "r2"
        snapshot["systems"]["agenda"][0]["form_id"] = "form-other"
        found = codes(run_preflight(snapshot))
        self.assertIn(("bio-w08-l2", "forms", "turn_in_closed"), found)
        self.assertIn(("bio-w08-l2", "forms", "turn_in_stale_deck"), found)
        self.assertIn(("bio-w08-l1", "agenda", "turn_in_link_mismatch"), found)


class StructureTests(unittest.TestCase):
    def test_operational_edit_dropping_contrast_step_is_reported(self):
        snapshot = week()
        slides = lesson(snapshot, "bio-w08-l1")["deck"]["slides"]
        # A tidy-up edit replaces the two-substance comparison with one example.
        slides[0] = slides[0].replace("CA1-CONTRAST", "CA1-EXAMPLE")
        slides[1] = slides[1].replace("cross differently from", "cross like")
        slides[2] = "Critical aspect: selective permeability\nLook at the cell.\n" \
                    "[[NOTES:C06:CA1-EXAMPLE]]"
        result = run_preflight(snapshot)
        actions = [item for item in result["exceptions"]
                   if item["code"] == "variation_pattern_missing"]
        self.assertEqual(len(actions), 1)
        self.assertIn("contrast", actions[0]["action"])
        self.assertEqual([item["code"] for item in result["exceptions"]],
                         ["variation_pattern_missing"])

    def test_intended_aspect_dropped_and_unplanned_aspect_needs_decision(self):
        snapshot = week()
        lesson(snapshot, "bio-w08-l1")["intended_critical_aspects"] = ["diffusion gradient"]
        result = run_preflight(snapshot)
        found = codes(result)
        self.assertIn(("bio-w08-l1", "deck", "critical_aspect_dropped"), found)
        unplanned = [item for item in result["exceptions"]
                     if item["code"] == "critical_aspect_unplanned"]
        self.assertEqual(unplanned[0]["severity"], "decide")
        # The later lesson builds on an aspect the edited plan no longer introduces.
        self.assertNotIn(("bio-w08-l2", "sequence", "prerequisite_not_introduced"), found)

    def test_sequence_prerequisite_order_and_prior_weeks(self):
        snapshot = week()
        lesson(snapshot, "bio-w08-l1")["sequence"] = 3
        found = codes(run_preflight(snapshot))
        self.assertIn(("bio-w08-l2", "sequence", "prerequisite_not_introduced"), found)
        snapshot["prior_critical_aspects"] = ["Selective permeability"]
        found = codes(run_preflight(snapshot))
        self.assertNotIn(("bio-w08-l2", "sequence", "prerequisite_not_introduced"), found)

    def test_ninety_minute_flow_checks(self):
        snapshot = week()
        flow = lesson(snapshot, "for-w08-l1")["flow"]
        flow[1].update(minutes=40, mode="lecture")
        flow[3], flow[4] = flow[4], flow[3]
        lesson(snapshot, "bio-w08-l2")["flow"] = lesson(snapshot, "bio-w08-l2")["flow"][:3]
        lesson(snapshot, "bio-w08-l1")["flow"] = []
        found = codes(run_preflight(snapshot))
        self.assertIn(("for-w08-l1", "plan", "flow_over_time"), found)
        self.assertIn(("for-w08-l1", "plan", "flow_long_passive"), found)
        self.assertIn(("for-w08-l1", "plan", "flow_phase_order"), found)
        self.assertIn(("bio-w08-l2", "plan", "flow_under_time"), found)
        self.assertIn(("bio-w08-l2", "plan", "flow_phase_missing"), found)
        self.assertIn(("bio-w08-l1", "plan", "flow_unplanned"), found)


class PacketTests(unittest.TestCase):
    def test_paper_work_is_not_offered_for_conversion(self):
        result = run_preflight(week())
        converted = {row["material_id"] for row in result["conversions"]}
        self.assertEqual(converted, {"m-bio1-ws"})  # lab table stays on paper
        prompts = [item["prompt"] for item in result["conversions"][0]["items"]]
        self.assertEqual(prompts, ["Why does water cross faster than glucose?",
                                   "State the rule in one sentence."])
        self.assertEqual(result["conversions"][0]["target"], "linked_response")

    def test_conversion_without_form_targets_slide_text_boxes(self):
        snapshot = week()
        lesson(snapshot, "bio-w08-l1")["turn_in"] = None
        result = run_preflight(snapshot)
        self.assertEqual(result["conversions"][0]["target"], "slide_text_boxes")
        self.assertIn(("bio-w08-l1", "forms", "turn_in_missing"), codes(result))

    def test_print_and_supply_gaps_are_exceptions(self):
        snapshot = week()
        material = lesson(snapshot, "bio-w08-l2")["materials"][0]
        material["file_revision"] = ""
        material["per"] = "someone"
        lesson(snapshot, "for-w08-l1")["supplies"].append({"item": "Ink pads", "per": "group"})
        found = codes(run_preflight(snapshot))
        self.assertIn(("bio-w08-l2", "folder", "print_source_missing"), found)
        self.assertIn(("bio-w08-l2", "plan", "print_quantity_unknown"), found)
        self.assertIn(("for-w08-l1", "plan", "supply_quantity_missing"), found)

    def test_supplies_merge_across_lessons_by_item_and_unit(self):
        snapshot = week()
        lesson(snapshot, "for-w08-l1")["supplies"].append(
            {"item": "raisins", "quantity": 1, "per": "class", "unit": "boxes", "grocery": True})
        raisins = [row for row in run_preflight(snapshot)["supplies"] if row["item"] == "Raisins"]
        self.assertEqual(raisins[0]["to_get"], 25)
        self.assertEqual(raisins[0]["lessons"], ["bio-w08-l1", "for-w08-l1"])


class InputTests(unittest.TestCase):
    def test_unusable_snapshots_are_rejected_before_checking(self):
        for mutate, code in (
            (lambda s: s.update(schema_version="v0"), "snapshot_schema_unsupported"),
            (lambda s: s["lessons"].append(copy.deepcopy(s["lessons"][0])), "lesson_id_duplicated"),
            (lambda s: s["systems"].pop("schoology"), "systems_incomplete"),
            (lambda s: s.update(week_start="next monday"), "week_start_invalid"),
        ):
            snapshot = week()
            mutate(snapshot)
            with self.assertRaises(PreflightInputError) as caught:
                run_preflight(snapshot)
            self.assertEqual(str(caught.exception), code)


if __name__ == "__main__":
    unittest.main()
