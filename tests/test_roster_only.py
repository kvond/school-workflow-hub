import copy
from datetime import datetime, timedelta, timezone
import unittest

from school_workflow_hub.adapters import roster_only, roster_matching
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


class RosterOnlyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 9, 13, tzinfo=timezone.utc)
        self.config = {"HUB_ROSTER_ONLY_SECTIONS": {"B4 Study Hall": {
            "class_key": "study_hall", "section_id": "55555", "tab_id": 99,
            "school_year": "2026-27"}},
            "HUB_ROSTER_SECTION_IDS": {"A4 Biology": "44444"},
            "HUB_ROSTER_TABS": {"A4 Biology": 88}}
        self.snapshot = {"source": "infinite_campus", "complete": True,
            "section_id": "55555", "school_year": "2026-27",
            "checked_at": self.now.isoformat(),
            "counts": {"active": 2, "incoming": 1, "dropped": 1}, "rows": [
                {"name": "Fictional, Ada", "status": "active"},
                {"name": "Fictional, Ben", "status": "active"},
                {"name": "Fictional, Cora", "status": "incoming", "start_date": "11/02/2026"},
                {"name": "Fictional, Davi", "status": "dropped", "end_date": "10/01/2026"}]}

    def plan(self, previous=None):
        return roster_only.plan_mirror(self.config, "B4 Study Hall", self.snapshot,
                                       previous or [], now=self.now)

    def test_complete_snapshot_retains_departure_and_incoming_history(self):
        previous = [["Fictional, Ada"], ["Fictional, Earlier"]]
        plan = self.plan(previous)
        self.assertEqual(plan["counts"], {"active": 2, "incoming": 1, "dropped": 1, "inactive": 1})
        mirror = [plan["headers"], *plan["rows"]]
        self.assertEqual(roster_only.active_rows(mirror), ["Fictional, Ada", "Fictional, Ben"])
        self.assertEqual(next(r for r in plan["rows"] if r[0].endswith("Earlier"))[1], "inactive")
        self.assertFalse(plan["grading_eligible"])
        self.assertEqual(previous, [["Fictional, Ada"], ["Fictional, Earlier"]])

    def test_later_join_departure_and_reactivation_keep_history(self):
        first = self.plan()
        self.snapshot["rows"][0]["status"] = "dropped"
        self.snapshot["rows"][3]["status"] = "active"
        second = self.plan(first["rows"])
        self.assertEqual(roster_only.active_rows([second["headers"], *second["rows"]]),
                         ["Fictional, Ben", "Fictional, Davi"])
        self.assertEqual(len(second["rows"]), 4)

    def test_names_only_capture_does_not_erase_previously_verified_dates(self):
        previous = [["Fictional, Ada", "active", "09/01/2026", "", "old check"]]
        plan = self.plan(previous)
        self.assertEqual(next(row for row in plan["rows"] if row[0] == "Fictional, Ada")[2], "09/01/2026")

    def test_malformed_history_row_is_held_instead_of_silently_discarded(self):
        with self.assertRaisesRegex(ConnectionError, "baseline_invalid"):
            self.plan([{"name": "Fictional, Earlier"}])

    def test_partial_scan_is_never_an_empty_or_departure_plan(self):
        self.snapshot["complete"] = False
        with self.assertRaisesRegex(ConnectionError, "complete_snapshot_required"):
            self.plan([["Fictional, Ada"]])

    def test_wrong_section_or_year_holds(self):
        for field, value in (("section_id", "55556"), ("school_year", "2025-26")):
            with self.subTest(field=field):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot[field] = value
                with self.assertRaisesRegex(ConnectionError, "complete_snapshot_required"):
                    roster_only.plan_mirror(self.config, "B4 Study Hall", snapshot, [], now=self.now)

    def test_reported_count_must_match_every_observed_name(self):
        self.snapshot["counts"]["active"] = 3
        with self.assertRaisesRegex(ConnectionError, "count_mismatch"):
            self.plan()

    def test_stale_or_future_capture_cannot_update(self):
        for delta in (timedelta(hours=-2), timedelta(minutes=1)):
            self.snapshot["checked_at"] = (self.now + delta).isoformat()
            with self.assertRaisesRegex(ConnectionError, "snapshot_stale"):
                self.plan()

    def test_duplicate_name_across_statuses_is_not_guessed(self):
        self.snapshot["rows"][2]["name"] = "Fictional, Ada"
        with self.assertRaisesRegex(ConnectionError, "ambiguous_name"):
            self.plan()

    def test_mirror_readback_has_to_match_entire_history_and_active_view(self):
        plan = self.plan()
        rows = [plan["headers"], *plan["rows"]]
        self.assertEqual(roster_only.verify_mirror(plan, rows)["status"], "verified")
        altered = copy.deepcopy(rows)
        altered[1][1] = "incoming"
        with self.assertRaisesRegex(ConnectionError, "readback_mismatch"):
            roster_only.verify_mirror(plan, altered)

    def test_grading_scope_remains_exactly_four_instructional_sections(self):
        self.assertNotIn("study_hall", roster_matching.CLASS_TABS)
        self.assertEqual(sum(map(len, roster_matching.CLASS_TABS.values())), 4)
        target = roster_only.configured_targets(self.config)["B4 Study Hall"]
        self.assertFalse(target["grading_eligible"])

    def test_conflicting_academic_section_or_tab_is_rejected(self):
        for field, value in (("section_id", "44444"), ("tab_id", 88)):
            with self.subTest(field=field):
                config = copy.deepcopy(self.config)
                config["HUB_ROSTER_ONLY_SECTIONS"]["B4 Study Hall"][field] = value
                with self.assertRaisesRegex(ConnectionError, "mapping_conflict"):
                    roster_only.configured_targets(config)

    def test_unknown_class_and_malformed_config_do_not_expand_scope(self):
        self.config["HUB_ROSTER_ONLY_SECTIONS"]["B4 Study Hall"]["class_key"] = "biology"
        with self.assertRaisesRegex(ConnectionError, "configuration_invalid"):
            roster_only.configured_targets(self.config)

    def test_missing_config_keeps_roster_only_scope_empty(self):
        self.assertEqual(roster_only.configured_targets({}), {})


if __name__ == "__main__":
    unittest.main()
