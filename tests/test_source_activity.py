"""Synthetic host evidence only; no native/browser/school reads or writes."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2.source_activity import PostRunSourceActivity, ActivityHold, i12_record, CLAIM_ROLES
from hub_v2.source_operations import SourceOperations, Held
from school_workflow_hub.core.cloud_state import CloudState, Document
from test_cloud_state import MemoryCAS


class SourceActivityTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.port.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        self.claims = {role: CloudState(self.port).create_task({"workflow": role,
                       "class_key": "fictional", "assignment_key": "scope"}) for role in CLAIM_ROLES}
        self.modes = {"agent1": "code_read_only", "agent2": "code_read_only", "agent3": "code_read_only",
                      "feedback": "deferred", "secondary": "deferred", "consultant": "consulting_only"}
        self.record = {"schema": "source-activity/v1", "record_id": "I-12", "source_scope": "fictional-sources",
                       "owner": "coordinator", "sole_live_executor": "coordinator", "authorization_ref": "decision-1",
                       "authorized_from": "2026-10-23T11:00:00Z", "authorized_until": "2026-10-24T02:00:00Z",
                       "claim_owners": {role: "coordinator" for role in CLAIM_ROLES},
                       "participants": {name: {"mode": mode, "evidence_ref": "existing-private-reference",
                           "valid_until": "2026-10-24T02:00:00Z"} for name, mode in self.modes.items()}}
        self.envelope = i12_record(record_id="I-12", value={"stringValue": "existing status"},
                                  note=json.dumps({"source_activity": self.record}))
        self.helper = {"schema": "native-helper-observation/v1", "observed_at": self.port.now.isoformat(),
                       "observer": "coordinator", "evidence_ref": "private-observation", "account": "teacher@example.invalid",
                       "source_origin": "https://hub.example.invalid", "destination_origin": "https://ic.example.invalid",
                       "busy": False, "blocked": False, "ready": True, "run_now_visible": True, "run_now_enabled": True,
                       "last_attempt_day": "2026-10-23", "last_attempt_code": "finished"}
        self.report = {"day": "2026-10-23", "run_id": "00000000-0000-4000-8000-000000000001",
                       "code": "finished", "roster": "unavailable", "items": []}
        self.receipts = {"complete": True, "reports": [self.report]}
        self.read_record, self.read_helper, self.read_receipts = Mock(), Mock(), Mock()
        self.read_record.side_effect = lambda: deepcopy(self.envelope)
        self.read_helper.side_effect = lambda: deepcopy(self.helper)
        self.read_receipts.side_effect = lambda: deepcopy(self.receipts)
        self.owned = []
        self.grant = {"schema": "school-source-maintenance/v1", "authorization_ref": "decision-1",
            "authorization_revision": self.envelope["revision"], "status": "authorized",
            "source_scope": "fictional-sources", "owner": "coordinator", "request_id": "maintenance-1",
            "input_hash": "input", "operation_scope": "source_repair", "starts_at": self.record["authorized_from"],
            "expires_at": self.record["authorized_until"]}
        self.activity = PostRunSourceActivity(port=self.port, known_claims=self.claims, read_record=self.read_record,
            read_helper=self.read_helper, read_receipts=self.read_receipts, expected_revision=self.envelope["revision"],
            source_scope="fictional-sources", owner="coordinator", account="teacher@example.invalid",
            source_origin="https://hub.example.invalid", destination_origin="https://ic.example.invalid",
            participants=self.modes, maintenance_authorization=self.grant, owned_claims=lambda: self.owned)

    def held_without_write(self, pattern="activity_"):
        write = Mock()
        with self.assertRaisesRegex(ActivityHold, pattern):
            self.activity()
            write()
        write.assert_not_called()
        self.assertIsNone(self.activity.last_evidence)

    def test_complete_evidence_passes_and_records_correlation_honestly(self):
        before = deepcopy(self.port.rows)
        self.assertIsNone(self.activity())
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.activity.last_evidence["correlation_basis"], "unique_day_and_outcome")
        self.assertEqual(self.activity.last_evidence["receipt_run_id"], self.report["run_id"])
        self.assertEqual(self.read_record.call_count, 2)
        self.assertEqual(self.read_receipts.call_count, 2)

    def coverage(self):
        reasons = {k: 0 for k in ("flag", "exempt", "excused", "check", "name", "missing")}
        hold = {"assignment": "Fictional lesson", "class_name": "Fictional class", "week": "Week 9",
                "code": "coverage_incomplete", "filled": 0, "raised": 0, "kept": 0, "left": 0, "reasons": reasons}
        compared = {**deepcopy(hold), "assignment": "Compared lesson", "code": "nothing_to_enter",
                    "kept": 15, "left": 2, "reasons": {**reasons, "name": 2}}
        self.report.update(code="coverage_incomplete", items=[hold, compared])
        self.helper.update(last_attempt_code="coverage_incomplete", run_now_visible=False,
            run_now_enabled=False, recovery_visible=False,
            morning_text="Weekday mornings enabled. Last attempt: 2026-10-23, coverage incomplete. See the Hub morning summary.",
            next_text="The run stopped at Fictional lesson (coverage incomplete). No completion is confirmed for those items. "
                      "Open the named assignment in IC, check its scores and the Hub morning summary, "
                      "then correct/save only if needed; do not retry automatically.")

    def test_exact_terminal_coverage_is_source_idle_without_reentry_controls(self):
        self.coverage()
        before = deepcopy(self.port.rows)
        self.activity()
        self.assertEqual(self.port.rows, before)
        self.assertFalse(self.activity.last_evidence["reentry_available"])
        self.assertEqual(self.activity.last_evidence["source_idle_correlation"], "unique_day_and_outcome")

    def test_terminal_gap_can_include_verified_readback_but_not_uncertain_items(self):
        self.coverage()
        item = self.report["items"][1]
        item.update(code="read_back", filled=1, raised=2)
        self.activity()
        for code in ("running", "receipt_unconfirmed", "save_unconfirmed", "readback_incomplete", "interrupted"):
            item["code"] = code
            self.held_without_write()

    def test_top_ready_or_partial_terminal_text_cannot_replace_morning_idle_evidence(self):
        self.coverage()
        for field, value in (("morning_text", "Ready."), ("next_text", "The run stopped at"),
                             ("morning_text", "The class run is active. Keep the Hub and Infinite Campus tabs open."),
                             ("recovery_visible", True), ("busy", True), ("blocked", True),
                             ("last_attempt_day", "2026-10-22")):
            prior = self.helper[field]
            self.helper[field] = value
            self.held_without_write()
            self.helper[field] = prior

    def test_coverage_contradicted_hold_comparison_counts_and_empty_batch_hold(self):
        self.coverage()
        hold, compared = self.report["items"]
        for field in ("filled", "raised", "kept", "left"):
            hold[field] = 1
            self.held_without_write("activity_coverage_counts_contradicted")
            hold[field] = 0
        hold["reasons"]["name"] = 1
        self.held_without_write("activity_coverage_counts_contradicted")
        hold["reasons"]["name"] = 0
        compared["filled"] = 1
        self.held_without_write("activity_coverage_counts_contradicted")
        compared["filled"] = 0
        compared["left"] = 3
        self.held_without_write("activity_coverage_counts_contradicted")
        self.report["items"] = []
        self.held_without_write("activity_coverage_outcome_contradicted")

    def test_competing_current_receipt_with_in_progress_item_holds(self):
        self.coverage()
        competitor = deepcopy(self.report)
        competitor.update(code="finished", run_id="00000000-0000-4000-8000-000000000002")
        competitor["items"][0]["code"] = "running"
        self.receipts["reports"].append(competitor)
        self.held_without_write("activity_receipt_unsettled")

    def test_note_hash_includes_actual_cell_value_and_preserves_siblings(self):
        note = json.dumps({"source_activity": self.record, "teacher_text": "Keep this."})
        first = i12_record(record_id="I-12", value="one", note=note)
        other = i12_record(record_id="I-12", value="two", note=note)
        self.assertNotEqual(first["revision"], other["revision"])
        self.assertEqual(json.loads(note)["teacher_text"], "Keep this.")
        for invalid in ("", "[]", "{}", '{"source_activity": false}'):
            with self.assertRaises(ActivityHold):
                i12_record(record_id="I-12", value="x", note=invalid)

    def test_stale_missing_future_or_preseven_popup_is_not_idle(self):
        for timestamp in ("2026-10-23T11:58:59Z", "2026-10-23T12:00:01Z", "2026-10-23T10:59:59Z", None):
            with self.subTest(timestamp=timestamp):
                self.helper["observed_at"] = timestamp
                self.held_without_write()
        self.read_helper.side_effect = lambda: None
        self.held_without_write()

    def test_busy_blocked_disabled_hidden_or_missing_gates_block(self):
        for field in ("busy", "blocked", "ready", "run_now_visible", "run_now_enabled"):
            original = self.helper[field]
            with self.subTest(field=field):
                self.helper[field] = not original
                self.held_without_write("activity_helper_not_idle")
                del self.helper[field]
                self.held_without_write("activity_helper_not_idle")
                self.helper[field] = original

    def test_wrong_profile_origins_or_observer_blocks(self):
        for field in ("account", "source_origin", "destination_origin", "observer", "evidence_ref"):
            original = self.helper[field]
            self.helper[field] = "" if field == "evidence_ref" else "other"
            self.held_without_write("activity_helper_identity_unverified")
            self.helper[field] = original

    def test_expired_authorization_changed_revision_or_deferral_blocks(self):
        self.envelope["record"]["authorized_until"] = "2026-10-23T12:00:00Z"
        self.held_without_write("activity_maintenance_binding_changed")
        self.envelope["record"]["authorized_until"] = "2026-10-24T02:00:00Z"
        self.envelope["record"]["participants"]["feedback"]["mode"] = "active"
        self.held_without_write("activity_participant_deferral_unavailable")
        self.envelope["revision"] = "changed"
        self.held_without_write("activity_record_revision_changed")

    def test_bookend_permutations_block_changed_notes_receipts_and_claims(self):
        for reader, value in ((self.read_record, self.envelope), (self.read_receipts, self.receipts)):
            changed = deepcopy(value)
            changed["changed"] = True
            reader.side_effect = [value, changed]
            self.held_without_write("changed_during_read")
            reader.side_effect = lambda value=value: deepcopy(value)
        original = self.port.read
        calls = []
        def changed(ids):
            calls.append(1)
            if len(calls) == 2:
                key = self.claims["roster"]
                doc = self.port.rows[key]
                self.port.rows[key] = Document(doc.value, "new-version")
            return original(ids)
        self.port.read = changed
        self.held_without_write("activity_claim_changed_during_read")

    def test_unexpired_foreign_claim_blocks_with_exact_private_context(self):
        claim = CloudState(self.port).claim(self.claims["roster"], owner="other-worker", request_id="req", input_hash="hash")
        with self.assertRaises(ActivityHold) as caught:
            self.activity()
        self.assertEqual(caught.exception.private_detail["task_id"], claim.task_id)
        self.assertEqual(caught.exception.private_detail["owner"], claim.owner)
        self.assertEqual(caught.exception.private_detail["expires_at"], claim.expires_at)

    def test_expired_claim_is_historical_unchanged_not_cleared(self):
        key = self.claims["grading"]
        doc = self.port.rows[key]
        self.port.rows[key] = Document({**doc.value, "active_run": "old-run", "owner": "old-worker",
                                       "expires_at": "2026-09-23T12:00:00Z"}, doc.version)
        before = deepcopy(self.port.rows)
        self.activity()
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.activity.last_evidence["historical_expired_claims"], ["grading"])

    def test_same_owner_label_is_insufficient_exact_real_claim_and_run_required(self):
        claim = CloudState(self.port).claim(self.claims["roster"], owner="coordinator", request_id="req", input_hash="hash")
        self.held_without_write("activity_foreign_claim_active")
        self.owned = [claim]
        self.activity()
        self.owned = [replace(claim, input_hash="wrong")]
        self.held_without_write("activity_owned_run_unverified")
        self.owned = [replace(claim, fence=claim.fence+1)]
        self.held_without_write("activity_foreign_claim_active")

    def test_missing_claim_is_not_evidence_of_idleness(self):
        del self.port.rows[self.claims["roster"]]
        self.held_without_write("activity_claim_unavailable")

    def test_missing_partial_ambiguous_or_current_unsettled_receipts_block(self):
        for value in ({"complete": False, "reports": [self.report]}, {"complete": True, "reports": []},
                      {"complete": True, "reports": [self.report, {**self.report, "run_id": "00000000-0000-4000-8000-000000000002"}]},
                      {"complete": True, "reports": [{**self.report, "code": "receipt_unconfirmed"}]},
                      {"complete": True, "reports": [{**self.report, "code": "running"}]}):
            self.receipts = value
            self.held_without_write()

    def test_pending_automatic_run_and_wrong_popup_day_block(self):
        self.helper["last_attempt_day"] = "2026-10-22"
        self.held_without_write("activity_helper_today_not_settled")
        self.helper["last_attempt_day"] = "2026-10-23"
        self.helper["last_attempt_code"] = "receipt_unconfirmed"
        self.held_without_write("activity_helper_today_not_settled")

    def test_unchanged_historical_receipt_does_not_override_current_settled_run(self):
        self.receipts["reports"].append({**self.report, "day": "2026-10-22", "code": "receipt_unconfirmed",
            "run_id": "00000000-0000-4000-8000-000000000002"})
        self.activity()

    def test_failed_reads_are_holds_without_writes(self):
        self.read_receipts.side_effect = RuntimeError("private provider failure")
        self.held_without_write("activity_evidence_unavailable")

    def test_slow_check_and_response_time_are_counted(self):
        def slow():
            self.port.advance(61)
            return deepcopy(self.helper)
        self.read_helper.side_effect = slow
        self.held_without_write("activity_read_window_stale")
        self.port.advance(-61)
        self.read_helper.side_effect = lambda: deepcopy(self.helper)
        with patch("hub_v2.source_activity.monotonic", side_effect=[100.0, 161.0]):
            self.held_without_write("activity_read_window_stale")

    def test_adapter_does_not_override_source_guard_cutoff_or_create_task(self):
        rows = deepcopy(self.port.rows)
        with self.assertRaisesRegex(Held, "source_mutation_window_closed"):
            SourceOperations.acquire(self.port, source_scope="fictional-sources", owner="coordinator",
                request_id="req", input_hash="hash", check_activity=self.activity)
        self.assertEqual(self.port.rows, rows)

    def maintenance(self, owner="coordinator", request="maintenance-1"):
        grant = {**self.grant, "owner": owner, "request_id": request}
        return SourceOperations.acquire(self.port, source_scope="fictional-sources", owner=owner,
            request_id=request, input_hash="input", check_activity=self.activity,
            maintenance_authorization=grant, read_maintenance_authorization=lambda ref: deepcopy(grant),
            operation_scope="source_repair")

    def test_real_maintenance_guard_reserves_write_and_excludes_second_request(self):
        from school_workflow_hub.core.cloud_state import Busy
        guard = self.maintenance()
        guard.before_write(required_seconds=45)
        with self.assertRaises(Busy):
            self.maintenance(request="maintenance-2")

    def test_expired_shared_lease_after_slow_activity_stops_native_write(self):
        from school_workflow_hub.core.cloud_state import LeaseLost
        guard = self.maintenance()
        read_record = self.read_record.side_effect
        def delayed():
            self.port.advance(121)
            self.helper["observed_at"] = self.port.now.isoformat()
            self.read_record.side_effect = read_record
            return read_record()
        self.read_record.side_effect = delayed
        native = Mock()
        with self.assertRaises(LeaseLost):
            guard.before_write(required_seconds=45)
            native()
        native.assert_not_called()

    def test_after_acquisition_busy_helper_stops_all_native_writes(self):
        guard = self.maintenance()
        self.helper["busy"] = True
        native = Mock()
        with self.assertRaisesRegex(ActivityHold, "activity_helper_not_idle"):
            guard.before_write(required_seconds=45)
            native()
        native.assert_not_called()
        self.assertTrue(guard.stopped)

    def test_native_sheet_reader_uses_exact_row_and_note_without_writes(self):
        from hub_v2.source_activity import read_i12_cell
        cells = [{"userEnteredValue": {"stringValue": "I-12"}}, {}, {}, {}, {},
                 {"userEnteredValue": {"stringValue": "status"}, "note": json.dumps({"source_activity": self.record})}]
        data = {"spreadsheetId": "fictional-sheet", "sheets": [{"properties": {"title": "Issues"},
                "data": [{"startRow": 16, "rowData": [{"values": cells}]}]}]}
        with patch("hub_v2.google.get_json", return_value=data) as get:
            result = read_i12_cell("synthetic", spreadsheet_id="fictional-sheet", tab="Issues", row=17)
            self.assertEqual(result["record"], self.record)
            self.assertIn("note", get.call_args.args[2]["fields"])
            self.assertEqual(get.call_args.kwargs["timeout"], 30)
            cells[0]["userEnteredValue"]["stringValue"] = "I-13"
            with self.assertRaisesRegex(ActivityHold, "activity_record_identity_changed"):
                read_i12_cell("synthetic", spreadsheet_id="fictional-sheet", tab="Issues", row=17)

    def test_full_ledger_reader_projects_same_run_and_rejects_conflicting_history(self):
        from hub_v2.source_activity import read_morning_receipts
        with patch("hub_v2.morning._report_target", return_value=("fictional-sheet", "Ledger")), \
             patch("hub_v2.google.sheet_values", return_value=[["", "Morning helper", "", "", "", "", json.dumps(r)]
                for r in ({**self.report, "code": "running"}, self.report)]):
            self.assertEqual(read_morning_receipts("synthetic", {}), self.receipts)
        with patch("hub_v2.morning._report_target", return_value=None):
            with self.assertRaises(ActivityHold):
                read_morning_receipts("synthetic", {})
        from hub_v2 import google
        with patch("hub_v2.morning._report_target", return_value=("fictional-sheet", "Ledger")), \
             patch("hub_v2.google.sheet_values", return_value=[["", "Morning helper", "", "", "", "", json.dumps(r)]
                for r in (self.report, {**self.report, "code": "receipt_unconfirmed"})]):
            with self.assertRaises(google.GoogleError):
                read_morning_receipts("synthetic", {})

    def test_malformed_or_truncated_native_receipt_row_is_not_complete_coverage(self):
        from hub_v2.source_activity import read_morning_receipts
        for rows in ([["", "Morning helper"]], [["", "Morning helper", "", "", "", "", "malformed"]]):
            with patch("hub_v2.morning._report_target", return_value=("fictional-sheet", "Ledger")), \
                 patch("hub_v2.google.sheet_values", return_value=rows):
                with self.assertRaisesRegex(ActivityHold, "activity_receipt_coverage_unverified"):
                    read_morning_receipts("synthetic", {})

    def test_elapsed_authority_and_mismatched_maintenance_expiry_block(self):
        self.port.now = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
        self.helper["observed_at"] = self.port.now.isoformat()
        self.held_without_write("activity_authorization_expired")
        self.port.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        self.envelope["record"]["authorized_until"] = "2026-10-23T21:00:00Z"
        self.held_without_write("activity_maintenance_binding_changed")

    def test_real_foreign_owned_claim_is_rejected_outside_pinned_domain_owner(self):
        foreign = CloudState(self.port).claim(self.claims["archive_biology"], owner="foreign-worker",
                                             request_id="foreign-request", input_hash="source-proof")
        self.owned = [foreign]
        self.held_without_write("activity_owned_claims_owner_mismatch")

    def test_pinned_domain_owner_needs_exact_executor_acquired_claim_and_run(self):
        # Pin the actual permitted domain owner in the immutable I-12 basis.
        self.envelope["record"]["claim_owners"]["archive_biology"] = "archive-domain"
        self.envelope = i12_record(record_id="I-12", value="status",
                                  note=json.dumps({"source_activity": self.envelope["record"]}))
        self.activity.expected_revision = self.envelope["revision"]
        claim = CloudState(self.port).claim(self.claims["archive_biology"], owner="archive-domain",
                                           request_id="own-archive-request", input_hash="source-proof")
        self.held_without_write("activity_foreign_claim_active")
        self.owned = [claim]
        self.activity()
        self.owned = [replace(claim, input_hash="wrong")]
        self.held_without_write("activity_owned_run_unverified")

    def test_missing_or_changed_domain_owner_pin_is_not_an_exemption(self):
        del self.envelope["record"]["claim_owners"]["archive_biology"]
        self.held_without_write("activity_claim_owners_unverified")
