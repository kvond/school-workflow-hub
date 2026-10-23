"""Synthetic native captures and workbook transport; no school writes."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import unittest
from unittest.mock import Mock, patch

from hub_v2 import roster
from school_workflow_hub.adapters import roster_bootstrap as rb, roster_worker as rw, roster_report as rr
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import Conflict, LeaseLost, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_roster_report import raw_csv
from tests.test_roster_worker import Mirror as OrdinaryMirror


class GuardedCAS(MemoryCAS):
    def commit_guarded(self, changes, *, before_write):
        before_write()
        self.commit(changes)


class Workbook:
    def __init__(self, original):
        self.data, self.writes = deepcopy(original), 0
        self.lose_response = self.bad_readback = False

    def read(self):
        return deepcopy(self.data)

    def apply(self, plan, before_write):
        before_write(required_seconds=rw.NATIVE_WRITE_READBACK_SECONDS)
        if self.data != plan["original"]:
            raise ConnectionError("baseline_changed")
        self.writes += 1
        for target in plan["targets"]:
            sid = str(target["sheet_id"])
            self.data["sheets"][sid] = {"properties": {"sheetId": target["sheet_id"], "title": plan["titles"][sid],
                "gridProperties": {"rowCount": 200, "columnCount": target["column_limit"]}},
                "cells": {f"{r}:{c}": {"userEnteredValue": value} for r, row in enumerate(plan["canonical_rows"][sid])
                          for c, value in enumerate(row) if value}}
        if self.bad_readback:
            self.data["sheets"][str(plan["targets"][0]["sheet_id"])]["cells"]["1:0"] = {"userEnteredValue": {"stringValue": "unexpected"}}
        if self.lose_response:
            raise ConnectionError("response_lost")
        return self.read()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.port, self.guard = GuardedCAS(), Mock()
        sections = [("forensics", "A2 Forensics"), ("forensics", "A3 Forensics"),
                    ("biology", "A4 Biology"), ("anatomy", "B3 A&P"), ("study_hall", "Study Hall")]
        self.targets = [{"class_key": cls, "section_name": name, "section_id": str(10+i), "school_year": "2026-27",
                         "sheet_id": 101+i, "columns": deepcopy(rb.STUDY if cls == "study_hall" else rb.INSTRUCTIONAL),
                         "row_limit": 200, "column_limit": 9 if cls == "study_hall" else 8,
                         "identity_scheme": rw.REPORT_SCHEME} for i, (cls, name) in enumerate(sections)]
        self.titles = {str(t["sheet_id"]): "Current " + t["section_name"] for t in self.targets}
        self.config = {"HUB_ROSTER_SPREADSHEET_ID": "fictional_roster_workbook_12345",
            "HUB_ROSTER_TABS": {name: i+1 for i, (cls, name) in enumerate(sections) if cls != "study_hall"},
            "HUB_ROSTER_SECTION_IDS": {name: str(10+i) for i, (cls, name) in enumerate(sections) if cls != "study_hall"},
            "HUB_ROSTER_STATUS_COLUMNS": {}, "HUB_ROSTER_COLUMN_LAYOUTS": {},
            "HUB_ROSTER_ONLY_SECTIONS": {"Study Hall": {"class_key": "study_hall", "section_id": "14",
                "tab_id": 5, "school_year": "2026-27"}}}
        original = {"workbook": self.config["HUB_ROSTER_SPREADSHEET_ID"], "sheets": {str(i+1): {
            "properties": {"sheetId": i+1, "title": name, "gridProperties": {"rowCount": 200, "columnCount": 8}},
            "cells": {"0:0": {"userEnteredValue": {"stringValue": "First Name"}},
                      "0:1": {"userEnteredValue": {"stringValue": "Last Name"}},
                      "0:2": {"userEnteredValue": {"stringValue": "Added"}},
                      "1:0": {"userEnteredValue": {"stringValue": "Ada"}, "note": "Teacher keeps this"},
                      "1:1": {"userEnteredValue": {"stringValue": "Fictional"}},
                      "1:2": {"userEnteredValue": {"stringValue": "2026-09-02"}},
                      "1:3": {"userEnteredValue": {"formulaValue": "=1+2"}}}}
            for i, (_, name) in enumerate(sections)}}
        self.mirror = Workbook(original)
        self.context = {"origin": "https://campus.example.invalid", "account": "teacher@example.invalid",
            "school_id": "school1", "calendar_id": "calendar1", "structure_id": "structure1",
            "school_year": "2026-27", "term": "M1", "term_start": "2026-08-24", "term_end": "2026-11-01"}
        rows = [["03000", f"SYN-{i} Fictional Course", "100", "M1; M2", f"{i+1} A", "Fictional", "Ada",
                 "", "", "", "", "10", "09/10/2026", "", "00123"] for i in range(5)]
        self.raw = raw_csv(rows)
        self.bindings = {t["section_id"]: dict(zip(rr.SCOPE_FIELDS, row[:5])) for t, row in zip(self.targets, rows)}
        self.captures = [self.capture("first", 8), self.capture("second", 3)]
        self.reader = Mock()
        self.reader.read_complete.side_effect = lambda targets: deepcopy(self.captures.pop(0))
        self.worker = rb.BootstrapWorker(self.port, self.reader, self.mirror, read_config=lambda: deepcopy(self.config),
            targets=self.targets, titles=self.titles, before_write=self.guard)

    def capture(self, ident, seconds):
        observation = {"capture_id": ident, "captured_at": (self.port.now-timedelta(seconds=seconds)).isoformat(),
            "observed_at": (self.port.now-timedelta(seconds=seconds-1)).isoformat(),
            "csv_sha256": hashlib.sha256(self.raw).hexdigest(), "context": deepcopy(self.context),
            "effective_date": self.port.now.date().isoformat(), "options": deepcopy(rr.OPTIONS), "sections": []}
        for target in self.targets:
            state = "incoming" if target["class_key"] == "study_hall" else "active"
            counts = {s: int(s == state) for s in rw.SOURCE_STATUSES}
            observation["sections"].append({"section_id": target["section_id"], "scope": self.bindings[target["section_id"]],
                "complete": True, "filtered": False, "next_page": None, "counts": counts,
                "students": [{"student_number": "00123", "status": state}]})
        return rr.prepare_capture(self.raw, observation, expected_context=self.context,
                                  bindings=self.bindings, targets=self.targets, now=self.port.now)

    def apply(self, proposal):
        return self.worker.apply(proposal, expected_sha256=proposal["sha256"], owner="coordinator", request_id="bootstrap-1")

    def test_complete_five_section_bootstrap_preserves_every_legacy_cell(self):
        prior = self.mirror.read()
        proposal = self.worker.prepare()
        self.assertEqual(self.port.rows, {})
        result = self.apply(proposal)
        self.assertEqual(result["status"], "verified_not_activated")
        self.assertEqual(self.mirror.writes, 1)
        self.assertEqual(result["counts"]["active"], 4)
        self.assertEqual(result["counts"]["incoming"], 1)
        for sid, sheet in prior["sheets"].items():
            self.assertEqual(self.mirror.data["sheets"][sid], sheet)
        self.assertEqual(proposal["plan"]["legacy"][0]["status"], "legacy_unlinked")
        self.assertNotEqual(self.config["HUB_ROSTER_TABS"], result["proposed_config_updates"]["HUB_ROSTER_TABS"])
        self.assertEqual(result["proposed_config_updates"]["HUB_ROSTER_ONLY_SECTIONS"]["Study Hall"]["tab_id"], 105)
        self.assertEqual(self.worker.reconcile(), result)
        self.assertEqual(self.mirror.writes, 1)

    def test_same_capture_partial_or_changed_source_never_writes(self):
        first, second = deepcopy(self.captures)
        for variant in (deepcopy(first), {**second, "complete": False}):
            self.captures = [deepcopy(first), variant]
            with self.assertRaises(ConnectionError): self.worker.prepare()
        self.assertEqual(self.mirror.writes, 0)
        self.assertEqual(self.port.rows, {})

    def test_old_capture_expiry_and_changed_configuration_hold(self):
        proposal = self.worker.prepare()
        self.port.advance(901)
        with self.assertRaises(ConnectionError): self.apply(proposal)
        self.port.advance(-901)
        self.config["HUB_ROSTER_TABS"]["A4 Biology"] = 99
        with self.assertRaisesRegex(ConnectionError, "basis_changed"): self.apply(proposal)
        self.assertEqual(self.mirror.writes, 0)

    def test_existing_destination_and_partial_targets_refused(self):
        self.worker.titles["101"] = "A2 Forensics"
        with self.assertRaises(ConnectionError): self.worker.prepare()
        with self.assertRaises(ConnectionError): rb.targets_valid(self.targets[:4], self.titles)
        self.assertEqual(self.mirror.writes, 0)

    def test_changed_teacher_formula_or_proposal_stops_without_writes(self):
        proposal = self.worker.prepare()
        self.mirror.data["sheets"]["1"]["cells"]["1:3"]["userEnteredValue"]["formulaValue"] = "=2+2"
        with self.assertRaisesRegex(ConnectionError, "basis_changed"): self.apply(proposal)
        proposal["plan"]["counts"]["active"] = 99
        with self.assertRaisesRegex(ConnectionError, "hash_changed"): self.apply(proposal)
        self.assertEqual(self.mirror.writes, 0)

    def test_lost_batch_response_requires_read_only_reconciliation(self):
        proposal = self.worker.prepare()
        self.mirror.lose_response = True
        with self.assertRaisesRegex(ConnectionError, "uncertain_native_write"): self.apply(proposal)
        self.assertEqual(self.mirror.writes, 1)
        with self.assertRaisesRegex(ConnectionError, "existing_intent"): self.apply(proposal)
        result = self.worker.reconcile()
        self.assertEqual(result["status"], "verified_not_activated")
        self.assertEqual(self.mirror.writes, 1)

    def test_expired_ambiguous_batch_blocks_regular_worker_and_repeated_bootstrap(self):
        proposal = self.worker.prepare()
        self.mirror.lose_response = True
        with self.assertRaises(ConnectionError): self.apply(proposal)
        self.port.advance(901)
        with self.assertRaises(ConnectionError): self.apply(proposal)
        ordinary = OrdinaryMirror({})
        worker = rw.RosterWorker(self.port, Mock(read_complete=Mock()), ordinary, self.targets, before_write=Mock())
        with self.assertRaisesRegex(ConnectionError, "uncertain_write_requires_readback"):
            worker.run(owner="new-worker", request_id="next-run")
        with self.assertRaises(LeaseLost): self.worker.reconcile()
        self.assertEqual(ordinary.writes, 0)
        self.assertEqual(self.mirror.writes, 1)

    def test_uncertain_intent_never_dispatches_native_batch(self):
        proposal = self.worker.prepare()
        original = self.port.commit_guarded
        def lose(changes, **kwargs):
            if self.worker.intent_key in changes:
                self.port.fail_after_commit = True
            return original(changes, **kwargs)
        self.port.commit_guarded = lose
        with self.assertRaises(StoreUnavailable): self.apply(proposal)
        self.assertEqual(self.mirror.writes, 0)
        with self.assertRaisesRegex(ConnectionError, "existing_intent"): self.apply(proposal)

    def test_mismatched_readback_preserves_pending_marker(self):
        proposal = self.worker.prepare()
        self.mirror.bad_readback = True
        with self.assertRaisesRegex(ConnectionError, "uncertain_native"): self.apply(proposal)
        marker = self.port.rows[self.worker.sync_intent_key].value
        self.assertEqual(marker["state"], "pending")
        with self.assertRaises(ConnectionError): self.worker.reconcile()
        self.assertEqual(self.mirror.writes, 1)

    def test_slow_guard_loses_domain_ownership_before_native_dispatch(self):
        proposal = self.worker.prepare()
        def guard(**kwargs):
            if self.guard.call_count == 3:
                self.port.advance(301)
        self.guard.side_effect = guard
        with self.assertRaises(ConnectionError): self.apply(proposal)
        self.assertEqual(self.mirror.writes, 0)

    def test_native_batch_contains_only_five_new_tabs_and_their_owned_values(self):
        proposal = self.worker.prepare()
        plan = proposal["plan"]
        native = rb.SheetsBootstrap(self.config)
        # Real concrete request builder, transport mocked with synthetic inputs.
        self.mirror.apply(plan, lambda **kwargs: None)
        with patch.object(native, "read", side_effect=[plan["original"], self.mirror.read()]), \
             patch("hub_v2.google.service_token", return_value="synthetic-token"), \
             patch("hub_v2.google.batch_update") as write:
            actual = native.apply(plan, self.guard)
        self.assertTrue(rb.verify_destination(plan, actual))
        requests = write.call_args.args[2]
        self.assertEqual(len(requests), 10)
        self.assertEqual(sum("addSheet" in r for r in requests), 5)
        self.assertEqual({r["updateCells"]["start"]["sheetId"] for r in requests if "updateCells" in r}, {101,102,103,104,105})
        self.assertTrue(all(r["updateCells"]["fields"] == "userEnteredValue" for r in requests if "updateCells" in r))

    def test_explicit_start_date_controls_canonical_eligibility_and_legacy_added_remains(self):
        proposal = self.worker.prepare()
        rows = proposal["plan"]["canonical_rows"]["103"]
        cells = {(r+1,c+1): {"formattedValue": v.get("stringValue", "")} for r,row in enumerate(rows) for c,v in enumerate(row) if v}
        current, skipped = roster.parse_tab(cells, "biology", "A4 Biology", status_columns={"A4 Biology":3})
        self.assertEqual(skipped, 0)
        self.assertEqual(current[0]["added"], "2026-09-10")
        legacy = self.mirror.data["sheets"]["3"]["cells"]
        old = {tuple(int(v)+1 for v in k.split(":")): {"formattedValue": cell["userEnteredValue"].get("stringValue", "")}
               for k,cell in legacy.items()}
        prior, _ = roster.parse_tab(old, "biology", "A4 Biology")
        self.assertEqual(prior[0]["added"], "2026-09-02")
        self.assertEqual(self.config["HUB_ROSTER_STATUS_COLUMNS"], {})

    def test_concrete_reader_preserves_notes_formulas_format_and_complete_tab_metadata(self):
        native = rb.SheetsBootstrap(self.config)
        original = self.mirror.read()
        metadata = {"spreadsheetId": native.workbook, "sheets": [
            {"properties": deepcopy(v["properties"])} for v in original["sheets"].values()]}
        payload = {"spreadsheetId": native.workbook, "sheets": []}
        for sheet in original["sheets"].values():
            rows = [[{} for _ in range(8)] for _ in range(2)]
            for key, cell in sheet["cells"].items():
                r,c = map(int,key.split(":")); rows[r][c] = deepcopy(cell)
            payload["sheets"].append({"properties": deepcopy(sheet["properties"]),
                "data": [{"rowData": [{"values": row} for row in rows]}]})
        with patch("hub_v2.google.get_json", side_effect=[metadata, payload]) as read:
            self.assertEqual(native.read("synthetic"), original)
            self.assertTrue(all(c.kwargs["timeout"] == 20 for c in read.call_args_list))
        payload["sheets"].pop()
        with patch("hub_v2.google.get_json", side_effect=[metadata, payload]):
            with self.assertRaisesRegex(ConnectionError, "coverage_unverified"):
                native.read("synthetic")

    def test_native_readback_legacy_formula_edit_or_extra_tab_blocks(self):
        proposal = self.worker.prepare()
        self.mirror.apply(proposal["plan"], lambda **kwargs: None)
        actual = self.mirror.read()
        actual["sheets"]["1"]["cells"]["1:3"]["userEnteredValue"]["formulaValue"] = "=999"
        with self.assertRaisesRegex(ConnectionError, "legacy_preservation_unconfirmed"):
            rb.verify_destination(proposal["plan"], actual)
        actual = self.mirror.read()
        actual["sheets"]["999"] = deepcopy(actual["sheets"]["1"])
        with self.assertRaisesRegex(ConnectionError, "scope_mismatch"):
            rb.verify_destination(proposal["plan"], actual)

    def test_failed_final_state_commit_keeps_pending_barrier_and_reconciles_without_rewrite(self):
        proposal = self.worker.prepare()
        commit = self.port.commit_guarded
        def lost(changes, **kwargs):
            if self.worker.intent_key in changes and changes[self.worker.intent_key][0]["state"] == "verified":
                self.port.commit_guarded = commit
                raise StoreUnavailable("not_committed")
            return commit(changes, **kwargs)
        self.port.commit_guarded = lost
        with self.assertRaises(StoreUnavailable): self.apply(proposal)
        self.assertEqual(self.port.rows[self.worker.sync_intent_key].value["state"], "pending")
        self.assertEqual(self.worker.reconcile()["status"], "verified_not_activated")
        self.assertEqual(self.mirror.writes, 1)

    def test_completed_bootstrap_marker_same_request_never_uses_regular_parser(self):
        proposal = self.worker.prepare(); self.apply(proposal)
        ordinary = OrdinaryMirror({})
        worker = rw.RosterWorker(self.port, Mock(read_complete=Mock()), ordinary, self.targets, before_write=Mock())
        with self.assertRaisesRegex(ConnectionError, "bootstrap_requires_its_read_only_reconciliation"):
            worker.run(owner="coordinator", request_id="bootstrap-1")
        self.assertEqual(ordinary.writes, 0)

    def test_existing_added_column_precedes_native_start_date_when_both_exist(self):
        cells = {(1,1): {"formattedValue": "First Name"}, (1,2): {"formattedValue": "Last Name"},
                 (1,3): {"formattedValue": "Start date"}, (1,4): {"formattedValue": "Added"},
                 (2,1): {"formattedValue": "Ada"}, (2,2): {"formattedValue": "Fictional"},
                 (2,3): {"formattedValue": "2026-09-10"}, (2,4): {"formattedValue": "2026-09-02"}}
        students, _ = roster.parse_tab(cells, "biology", "A4 Biology")
        self.assertEqual(students[0]["added"], "2026-09-02")
