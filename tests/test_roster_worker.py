"""Synthetic worker/transport acceptance, never a live IC integration claim."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import Mock, patch

from school_workflow_hub.adapters import roster_worker as rw, roster_matching, roster_only
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from hub_v2 import roster
from tests.test_cloud_state import MemoryCAS
from tests.test_roster_matching import sheet


def target(section="A4 Biology", sid="42", tab=7, class_key="biology"):
    fields = list(rw.FIELDS) if class_key != "study_hall" else ["name", "status", "start_date", "end_date",
                                                               "checked_at", "last", "first", "student_id", "membership_id"]
    return {"class_key": class_key, "section_name": section, "section_id": sid,
            "school_year": "2026-27", "sheet_id": tab, "columns": {f: i+1 for i, f in enumerate(fields)},
            "row_limit": 20, "column_limit": 10}


def person(student="student-1", status="active", member="enroll-1", first="Ada"):
    return {"student_id": student, "membership_id": member, "first": first, "last": "Fictional",
            "status": status, "start_date": "2026-09-01", "end_date": ""}


def cells(t, people=()):
    rows = [[{} for _ in range(t["column_limit"])] for _ in range(t["row_limit"])]
    for field, col in t["columns"].items():
        rows[0][col-1] = {"stringValue": rw.owned_headers(t)[field]}
    for n, student in enumerate(people, 1):
        for field, col in t["columns"].items():
            value = student.get(field, "2026-09-14T12:00:00+00:00")
            rows[n][col-1] = {"stringValue": value} if value else {}
    return rows


class Mirror:
    def __init__(self, data):
        self.data, self.writes = deepcopy(data), 0
        self.lose_response = False
        self.bad_readback = False

    def read(self):
        return deepcopy(self.data)

    def apply(self, plan, before_write):
        before_write(required_seconds=rw.NATIVE_WRITE_READBACK_SECONDS)
        if self.data != plan["before"]:
            raise ConnectionError("changed")
        self.writes += bool(plan["changes"])
        self.data = deepcopy(plan["after"])
        if self.bad_readback:
            self.data[next(iter(self.data))][1][0] = {"stringValue": "changed"}
        if self.lose_response:
            raise ConnectionError("lost response")
        return self.read()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.targets = [target()]
        self.source = self.capture([[person()]])
        self.reader = Mock()
        self.reader.read_complete.side_effect = lambda targets: deepcopy(self.source)
        self.mirror = Mirror({"7": cells(self.targets[0], [person()])})
        self.guard = Mock()

    def capture(self, groups):
        return {"source": "infinite_campus", "reader_id": "accepted-ic-reader", "complete": True,
                "errors": [], "sections": [{"section_id": t["section_id"], "school_year": t["school_year"],
                    "complete": True, "filtered": False, "next_page": None, "revision": "revision-1",
                    "checked_at": self.port.now.isoformat(), "rows": deepcopy(rows), "expected_count": len(rows)}
                    for t, rows in zip(self.targets, groups)]}

    def worker(self):
        return rw.RosterWorker(self.port, self.reader, self.mirror, self.targets, before_write=self.guard)

    def run_worker(self, request="check-1"):
        return self.worker().run(owner="trusted-worker", request_id=request)

    def test_complete_native_identity_mirror_and_immutable_receipt(self):
        result = self.run_worker()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["counts"]["active"], 1)
        self.assertEqual(result["counts"]["unchanged"], 1)
        self.assertEqual(result["counts"]["updated"], 0)
        self.assertEqual(self.mirror.writes, 1)
        self.assertGreaterEqual(self.guard.call_count, 3)
        records = [d.value for key, d in self.port.rows.items() if key.startswith("record_")]
        self.assertEqual(records[0]["payload"]["source"], self.source)
        self.assertEqual(records[0]["payload"]["after"], self.mirror.data)

    def test_duplicate_request_reads_back_without_another_native_write(self):
        result = self.run_worker()
        self.assertEqual(self.run_worker(), result)
        self.assertEqual(self.mirror.writes, 1)
        with self.assertRaisesRegex(ConnectionError, "owner_conflict"):
            self.worker().run(owner="different-worker", request_id="check-1")

    def test_partial_stale_filtered_changed_scope_or_unavailable_keeps_prior_data(self):
        variants = [lambda s: s.update(complete=False), lambda s: s.update(errors=["unavailable"]),
                    lambda s: s["sections"][0].update(expected_count=2),
                    lambda s: s["sections"][0].update(filtered=True),
                    lambda s: s["sections"][0].update(next_page="more"),
                    lambda s: s["sections"][0].update(section_id="43"),
                    lambda s: s["sections"][0].update(school_year="2025-26"),
                    lambda s: s["sections"][0].update(checked_at=(self.port.now-timedelta(minutes=16)).isoformat()),
                    lambda s: s["sections"][0].update(checked_at=(self.port.now+timedelta(seconds=1)).isoformat())]
        original = deepcopy(self.source)
        baseline = self.mirror.read()
        for change in variants:
            with self.subTest(change=change):
                self.source = deepcopy(original)
                change(self.source)
                with self.assertRaises(ConnectionError):
                    self.run_worker()
                self.assertEqual(self.mirror.data, baseline)
                self.assertEqual(self.mirror.writes, 0)
        self.reader.read_complete.side_effect = ConnectionError("provider unavailable")
        with self.assertRaises(ConnectionError):
            self.run_worker()
        self.assertEqual(self.port.rows, {})

    def test_no_native_id_crosswalk_is_never_inferred_from_names(self):
        self.mirror.data["7"][1][3] = {}
        with self.assertRaisesRegex(ConnectionError, "crosswalk"):
            self.run_worker()
        self.assertEqual(self.mirror.writes, 0)

    def test_preserves_row_attendance_and_formulas_for_departures(self):
        self.mirror.data["7"][1][8] = {"formulaValue": "=SUM(J2:J5)"}
        self.mirror.data["7"][1][9] = {"stringValue": "teacher note"}
        self.source["sections"][0].update(rows=[], expected_count=0)
        result = self.run_worker()
        self.assertEqual(result["counts"]["inactive"], 1)
        self.assertEqual(result["counts"]["departed"], 1)
        self.assertEqual(result["counts"]["updated"], 1)
        self.assertEqual(self.mirror.data["7"][1][2], {"stringValue": "inactive"})
        self.assertEqual(self.mirror.data["7"][1][0], {"stringValue": "Fictional"})
        self.assertEqual(self.mirror.data["7"][1][8:], [{"formulaValue": "=SUM(J2:J5)"}, {"stringValue": "teacher note"}])

    def test_join_withdraw_rejoin_membership_history_retained(self):
        self.source["sections"][0]["rows"] = [person(status="withdrawn")]
        self.assertEqual(self.run_worker()["counts"]["withdrawn"], 1)
        self.port.advance(60)
        self.source = self.capture([[person(member="enroll-2"), person("student-2", member="enroll-3", first="Ben")]])
        result = self.run_worker("check-2")
        self.assertEqual(result["counts"]["reactivated"], 1)
        self.assertEqual(result["counts"]["added"], 1)
        records = [d.value["payload"] for k, d in self.port.rows.items() if k.startswith("record_")]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["before"]["7"][1][4], {"stringValue": "enroll-1"})
        self.assertEqual(records[1]["after"]["7"][1][4], {"stringValue": "enroll-2"})

    def test_unavailable_dates_preserve_same_membership_but_not_new_membership(self):
        self.source["sections"][0]["rows"][0]["start_date"] = None
        self.run_worker()
        self.assertEqual(self.mirror.data["7"][1][5], {"stringValue": "2026-09-01"})
        self.port.advance(60)
        self.source["sections"][0]["checked_at"] = self.port.now.isoformat()
        self.source["sections"][0]["rows"][0]["membership_id"] = "new-enrollment"
        self.run_worker("new-membership")
        self.assertEqual(self.mirror.data["7"][1][5], {})

    def test_receipt_commit_lost_response_recovers_without_rewriting_mirror(self):
        commit = self.port.commit
        def fail_receipt(changes):
            if any(k.startswith("record_") for k in changes):
                self.port.commit = commit
                self.port.fail_after_commit = True
            return commit(changes)
        self.port.commit = fail_receipt
        with self.assertRaises(StoreUnavailable):
            self.run_worker()
        result = self.worker().reconcile()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.mirror.writes, 1)

    def test_initial_intent_lost_response_never_retries_native_write(self):
        commit = self.port.commit
        def fail_intent(changes):
            if self.worker().intent_key in changes:
                self.port.commit = commit
                self.port.fail_after_commit = True
            return commit(changes)
        self.port.commit = fail_intent
        with self.assertRaises(StoreUnavailable):
            self.run_worker()
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.run_worker("another-check")
        self.assertEqual(self.mirror.writes, 0)

    def test_study_hall_incoming_and_multi_section_memberships_stay_separate(self):
        self.targets += [target("Study Hall", "99", 8, "study_hall")]
        self.source = self.capture([[person()], [person(status="incoming", member="study-enroll")]])
        self.mirror.data["8"] = cells(self.targets[1])
        result = self.run_worker()
        self.assertEqual(result["counts"]["active"], 1)
        self.assertEqual(result["counts"]["incoming"], 1)
        self.assertEqual(self.mirror.data["8"][1][1], {"stringValue": "incoming"})
        self.assertEqual(self.mirror.data["8"][1][0], {"stringValue": "Fictional, Ada"})
        self.port.advance(60)
        self.source = self.capture([[person()], [person(status="withdrawn", member="study-enroll")]])
        self.run_worker("study-departure")
        study_rows = [[cell.get("stringValue", "") for cell in row[:5]] for row in self.mirror.data["8"][:2]]
        self.assertEqual(study_rows[1][1], "dropped")
        self.assertEqual(roster_only.active_rows(study_rows), [])

    def test_duplicate_student_or_conflicting_native_membership_is_held(self):
        for rows in ([person(), person(member="other")],
                     [person(), person(student="student-2", first="Ben")]):
            self.source = self.capture([rows])
            with self.assertRaisesRegex(ConnectionError, "identity_conflict"):
                self.run_worker()
        self.assertEqual(self.mirror.writes, 0)

    def test_source_bookend_change_prevents_write(self):
        second = deepcopy(self.source)
        second["sections"][0]["revision"] = "revision-2"
        self.reader.read_complete.side_effect = [self.source, second]
        with self.assertRaisesRegex(ConnectionError, "source_changed"):
            self.run_worker()
        self.assertEqual(self.mirror.writes, 0)

    def test_source_guard_cutoff_prevents_native_write(self):
        self.guard.side_effect = ConnectionError("cutoff")
        with self.assertRaisesRegex(ConnectionError, "cutoff"):
            self.run_worker()
        self.assertEqual(self.mirror.writes, 0)

    def test_lease_expiry_during_source_guard_prevents_write(self):
        calls = 0
        def delayed_guard(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.port.advance(301)
        self.guard.side_effect = delayed_guard
        with self.assertRaises(Conflict):
            self.run_worker()
        self.assertEqual(self.mirror.writes, 0)

    def test_lost_response_blocks_new_write_then_reconciles_exact_readback(self):
        self.mirror.lose_response = True
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.run_worker()
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.run_worker("another-request")
        result = self.worker().reconcile()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.mirror.writes, 1)
        self.assertEqual(self.worker().reconcile(), result)

    def test_mismatched_uncertain_readback_is_not_cleared_or_retried(self):
        self.mirror.bad_readback = True
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.run_worker()
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.worker().reconcile()
        with self.assertRaisesRegex(ConnectionError, "uncertain"):
            self.run_worker("next-day")
        self.assertEqual(self.mirror.writes, 1)

    def test_owned_formula_and_nonempty_new_row_are_preserved(self):
        self.mirror.data["7"][2][9] = {"stringValue": "another teacher row"}
        self.source = self.capture([[person(), person("student-2", member="enroll-2", first="Ben")]])
        self.run_worker()
        self.assertEqual(self.mirror.data["7"][2][0], {})
        self.assertEqual(self.mirror.data["7"][3][1], {"stringValue": "Ben"})
        self.mirror.data["7"][1][2] = {"formulaValue": '=IF(TRUE,"active","inactive")'}
        with self.assertRaisesRegex(ConnectionError, "owned_formula"):
            self.run_worker("new-check")


class NativeMirrorTests(unittest.TestCase):
    def setUp(self):
        self.target = target()
        self.config = {"HUB_ROSTER_SPREADSHEET_ID": "fictional-roster-workbook-0001",
                       "HUB_ROSTER_TABS": {"A4 Biology": 7},
                       "HUB_ROSTER_SECTION_IDS": {"A4 Biology": "42"},
                       "HUB_ROSTER_STATUS_COLUMNS": {"A4 Biology": 3}}
        self.mirror = rw.SheetsRosterMirror(self.config, [self.target])
        self.baseline = {"7": cells(self.target, [person()])}

    def test_native_grid_uses_exact_stable_sheet_and_missing_coverage_holds(self):
        metadata = {"spreadsheetId": self.mirror.workbook, "sheets": [{"properties": {
            "sheetId": 7, "title": "Renamed roster", "gridProperties": {"rowCount": 20, "columnCount": 10}}}]}
        grid = {"spreadsheetId": self.mirror.workbook, "sheets": [{"properties": {"sheetId": 7},
            "data": [{"rowData": [{"values": [{"userEnteredValue": c} for c in row]} for row in self.baseline["7"]]}]}]}
        with patch.object(rw.google, "service_token", return_value="opaque-test-token"), \
                patch.object(rw.google, "get_json", side_effect=[metadata, grid]) as get:
            self.assertEqual(self.mirror.read(), self.baseline)
            self.assertIn(("ranges", "'Renamed roster'!A1:J20"), get.call_args.args[2])
        grid["sheets"] = []
        with patch.object(rw.google, "service_token", return_value="opaque-test-token"), \
                patch.object(rw.google, "get_json", side_effect=[metadata, grid]):
            with self.assertRaisesRegex(ConnectionError, "coverage"):
                self.mirror.read()

    def test_native_writer_changes_only_declared_value_cell_without_reordering(self):
        after = deepcopy(self.baseline)
        after["7"][1][2] = {"stringValue": "inactive"}
        plan = {"before": self.baseline, "after": after,
                "changes": [{"sheet_id": 7, "row": 1, "column": 2, "value": {"stringValue": "inactive"}}]}
        guard = Mock()
        with patch.object(self.mirror, "read", return_value=self.baseline), \
                patch.object(rw.google, "service_token", return_value="opaque-test-token"), \
                patch.object(rw.google, "batch_update") as write:
            self.mirror.apply(plan, guard)
        request = write.call_args.args[2][0]["updateCells"]
        self.assertEqual(request["fields"], "userEnteredValue")
        self.assertEqual(request["range"], {"sheetId": 7, "startRowIndex": 1, "endRowIndex": 2,
                                            "startColumnIndex": 2, "endColumnIndex": 3})
        self.assertEqual(guard.call_count, 1)
        plan["changes"][0]["column"] = 9
        with self.assertRaisesRegex(ConnectionError, "unowned"):
            self.mirror.apply(plan, guard)

    def test_reader_configuration_cannot_leave_departures_active(self):
        config = deepcopy(self.config)
        config.pop("HUB_ROSTER_STATUS_COLUMNS")
        with self.assertRaisesRegex(ConnectionError, "consumer_mapping"):
            rw.SheetsRosterMirror(config, [self.target])

    def test_populated_owned_rows_outside_supported_range_are_not_truncated(self):
        metadata = {"spreadsheetId": self.mirror.workbook, "sheets": [{"properties": {
            "sheetId": 7, "title": "Roster", "gridProperties": {"rowCount": 1000, "columnCount": 10}}}]}
        grid = {"spreadsheetId": self.mirror.workbook, "sheets": [{"properties": {"sheetId": 7},
            "data": [{"startRow": 30, "rowData": [{"values": [{"userEnteredValue": {"stringValue": "Another student"}}]}]}]}]}
        with patch.object(rw.google, "service_token", return_value="opaque-test-token"), \
                patch.object(rw.google, "get_json", side_effect=[metadata, grid]):
            with self.assertRaisesRegex(ConnectionError, "outside_supported"):
                self.mirror.read()

    def test_native_baseline_change_never_sends_write(self):
        with patch.object(self.mirror, "read", return_value={}), patch.object(rw.google, "batch_update") as write, \
                patch.object(rw.google, "service_token", return_value="opaque-test-token"):
            with self.assertRaisesRegex(ConnectionError, "destination_changed"):
                self.mirror.apply({"before": self.baseline, "after": self.baseline, "changes": []}, Mock())
        write.assert_not_called()


class StatusReaderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [["Last Name", "First Name", "Roster status"],
                     ["Fictional", "Ada", "active"], ["Fictional", "Ben", "incoming"],
                     ["Fictional", "Cora", "withdrawn"], ["Fictional", "Davi", "inactive"]]
        self.columns = {"A4 Biology": 3}

    def parse_both(self, rows):
        native = {(r, c): {"formattedValue": value} for r, row in enumerate(rows, 1)
                  for c, value in enumerate(row, 1)}
        first, skipped = roster.parse_tab(native, "biology", "A4 Biology", status_columns=self.columns)
        second = roster_matching.parse_roster({"sheets": [sheet("A4 Biology", rows)]}, status_columns=self.columns)
        return first, second

    def test_both_existing_readers_include_only_explicit_active_memberships(self):
        first, second = self.parse_both(self.rows)
        self.assertEqual([r["first"] for r in first], ["Ada"])
        self.assertEqual([r["first"] for r in second], ["Ada"])

    def test_missing_unknown_or_blank_status_is_availability_failure(self):
        for bad in ("", "unknown", "Active", "pending"):
            rows = deepcopy(self.rows)
            rows[1][2] = bad
            with self.assertRaisesRegex(ConnectionError, "status_unverified"):
                self.parse_both(rows)
        self.rows[0][2] = "Teacher notes"
        with self.assertRaisesRegex(ConnectionError, "header_unverified"):
            self.parse_both(self.rows)

    def test_unconfigured_legacy_rosters_are_unchanged(self):
        first = roster.parse_tab({(1, 1): {"formattedValue": "Fictional"},
                                  (1, 2): {"formattedValue": "Ada"}}, "biology", "A4 Biology")[0]
        self.assertEqual(len(first), 1)
        self.assertEqual(len(roster_matching.parse_roster({"sheets": [sheet("A4 Biology", [["Fictional", "Ada"]])]})), 1)


if __name__ == "__main__":
    unittest.main()
