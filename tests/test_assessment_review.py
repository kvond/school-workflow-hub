"""Existing review/successor workflow against synthetic workbook and CAS only."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_review as review, assessment_save as saving
from school_workflow_hub.adapters import assessment_provider as provider, grading_records
from school_workflow_hub.core.cloud_state import CloudState, Conflict, LeaseLost, key
from school_workflow_hub.workflows import assessment_records as records
from tests.test_assessment_records import packet, CAPTURE
from tests.test_cloud_state import MemoryCAS

NOW = "2026-09-18T15:05:00+00:00"
CONFIG = {"HUB_GRADING_SPREADSHEET_ID": "FictionalWorkbook" * 2,
          "HUB_READER_SERVICE_ACCOUNT": "reader@example.invalid"}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.packet = packet()
        self.capture = deepcopy(CAPTURE)
        self.port = MemoryCAS()
        built = records.build_rows(self.packet, records.completion(self.packet), self.capture, now=NOW)
        self.rid = built["record_id"]
        self.book = {}
        rows = {"Assignments": [{"assignment_key": self.packet["assignment_key"],
                                 "class_key": self.packet["class_key"], "assignment_title": "Fictional lesson"}],
                "Submissions": [built["submission"]], "Answers": built["answers"], "Class summaries": []}
        for number, (title, required) in enumerate(grading_records.HEADERS.items()):
            headers = list(dict.fromkeys([*required, *(k for row in rows[title] for k in row)]))
            self.book[title] = {"sheet_id": number, "headers": headers,
                "rows": [(i + 1, deepcopy(row)) for i, row in enumerate(rows[title])]}
        self.batches = 0
        patches = [patch.object(saving, "capture", side_effect=lambda *a: deepcopy(self.capture)),
            patch.object(saving, "_workbook", side_effect=lambda *a: deepcopy(self.book)),
            patch.object(saving, "service_token", return_value="fictional-token"),
            patch.object(review, "service_token", return_value="fictional-token"),
            patch.object(saving, "_now", return_value=NOW),
            patch.object(saving, "configured_store", return_value=self.port),
            patch.object(review, "configured_store", return_value=self.port),
            patch.object(saving, "read_records", side_effect=self.read_records),
            patch.object(grading_records, "read_records", side_effect=self.read_records),
            patch.object(saving, "_batch", side_effect=self.batch),
            patch.object(provider, "_call", side_effect=AssertionError("No model permitted")),
            patch.object(provider, "_teacher_prompts", side_effect=AssertionError("No provider prompts needed"))]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def read_records(self, *args):
        tables = {title: [deepcopy(row) for _, row in table["rows"]] for title, table in self.book.items()}
        for row in tables["Submissions"]:
            for field in saving.NUMERIC:
                if row[field] is None: row[field] = ""
        data = grading_records.project(tables)
        data["sections"] = [{k: self.packet[k] for k in ("class_key", "section_id", "section_name")}]
        return data

    def batch(self, source, token, requests):
        self.batches += 1
        for request in requests:
            if "appendCells" in request:
                body = request["appendCells"]
                table = next(t for t in self.book.values() if t["sheet_id"] == body["sheetId"])
                for row in body["rows"]:
                    values = [next(iter(c.get("userEnteredValue", {"stringValue": ""}).values())) for c in row["values"]]
                    table["rows"].append((len(table["rows"]) + 1, dict(zip(table["headers"], values))))
            else:
                body = request["updateCells"]; start = body["start"]
                table = next(t for t in self.book.values() if t["sheet_id"] == start["sheetId"])
                row = next(r for position, r in table["rows"] if position == start["rowIndex"])
                row[table["headers"][start["columnIndex"]]] = body["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]

    def load(self):
        return review.load("fictional-assertion", self.rid, CONFIG)

    def successor(self):
        loaded = self.load()
        changed = deepcopy(self.packet); changed["feedback"] = "Teacher-approved clerical report correction."
        return {"request_id": "synthetic-successor-request", "packet": changed,
                "review_basis": loaded["review_basis"]}

    def store_draft(self, item=None):
        provider._store_draft(CloudState(self.port), self.packet["source"]["file_id"],
            self.capture["sha256"], {"packet": item or self.packet}, self.packet)

    def test_missing_packet_returns_literal_original_rows_without_inventing_judgments_or_writes(self):
        before = deepcopy(self.port.rows)
        result = self.load()
        self.assertEqual(result["status"], "saved_packet_required")
        self.assertIsNone(result["packet"])
        self.assertEqual(result["base"]["submission"]["feedback"], self.packet["feedback"])
        self.assertEqual(result["live_model_calls"], 0)
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.batches, 0)

    def test_exact_original_cached_packet_loads_without_provider_configuration(self):
        self.store_draft()
        before = deepcopy(self.port.rows)
        result = self.load()
        self.assertEqual(result["status"], "saved_review_ready")
        self.assertEqual(result["packet"], self.packet)
        self.assertEqual(self.port.rows, before)

    def test_unreviewed_or_different_scope_draft_never_replaces_saved_evidence(self):
        for field, value in (("feedback", "Different provider draft"),
                             ("comparison_status", "Different comparison"),
                             ("ic_assignment_id", "different-map")):
            candidate = deepcopy(self.packet); candidate[field] = value
            with self.subTest(field=field):
                self.port.rows.clear(); self.store_draft(candidate)
                self.assertEqual(self.load()["status"], "saved_packet_required")

    def test_same_text_with_changed_native_revision_holds_load_and_save(self):
        payload = self.successor()
        self.capture["file"]["version"] = "42"
        for action in (self.load, lambda: saving.save("signed", payload, CONFIG)):
            with self.assertRaisesRegex(Conflict, "submission_changed"):
                action()
        self.assertEqual(self.batches, 0)

    def test_changed_original_source_hash_and_modified_time_hold(self):
        for kind in ("hash", "time"):
            self.capture = deepcopy(CAPTURE); payload = self.successor()
            if kind == "hash": self.capture["sha256"] = "b" * 64
            else: self.capture["file"]["modified_at"] = "2026-09-19T12:00:00Z"
            with self.subTest(kind=kind), self.assertRaises(Conflict):
                saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_changed_saved_feedback_or_answer_after_load_blocks_before_claim_or_write(self):
        for table, field in (("Submissions", "feedback"), ("Answers", "evidence_note")):
            original = deepcopy(self.book); payload = self.successor()
            self.book[table]["rows"][0][1][field] = "Concurrent teacher correction"
            with self.subTest(field=field), self.assertRaisesRegex(Conflict, "review_changed"):
                saving.save("signed", payload, CONFIG)
            self.assertEqual(self.port.rows, {})
            self.book = original
        self.assertEqual(self.batches, 0)

    def test_another_active_assessment_cannot_be_silently_superseded_by_loaded_review(self):
        payload = self.successor()
        concurrent = deepcopy(self.book["Submissions"]["rows"][0][1])
        concurrent["record_id"] = "asmt-concurrent"
        self.book["Submissions"]["rows"].append((2, concurrent))
        with self.assertRaisesRegex(Conflict, "review_changed"):
            saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_optional_rubric_evidence_cannot_be_dropped_by_prepared_successor(self):
        original = self.book["Answers"]["rows"][0][1]
        original["critical_aspect"] = "Original retained aspect"
        payload = self.successor()
        with self.assertRaisesRegex(ValueError, "protected_field"):
            saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_explicit_historical_notes_alias_loads_and_preserves_literal_history(self):
        self.packet["stage"] = "initial"
        built = records.build_rows(self.packet, records.completion(self.packet), self.capture, now=NOW)
        self.rid = built["record_id"]
        built["submission"]["assessment_stage"] = "initial_NOTES_checkpoint"
        self.book["Submissions"]["rows"] = [(1, built["submission"])]
        self.book["Answers"]["rows"] = [(i + 1, a) for i, a in enumerate(built["answers"])]
        self.store_draft()
        loaded = self.load()
        self.assertEqual(loaded["status"], "saved_review_ready")
        self.assertEqual(loaded["base"]["submission"]["assessment_stage"], "initial_NOTES_checkpoint")
        payload = self.successor()
        result = saving.save("signed", payload, CONFIG)
        self.assertTrue(result["readback_verified"])
        self.assertEqual(self.book["Submissions"]["rows"][0][1]["assessment_stage"], "initial_NOTES_checkpoint")
        self.assertEqual(self.book["Submissions"]["rows"][-1][1]["assessment_stage"], "initial")

    def test_initial_checkpoint_cannot_swap_hidden_required_and_optional_roles(self):
        for category, field, required in (("BANK", "bank_ids", "Term one"), ("DRAFT", "draft_ids", "D1")):
            self.packet = packet(stage="initial")
            self.packet["responses"][-1]["category"] = category
            built = records.build_rows(self.packet, records.completion(self.packet), self.capture, now=NOW)
            self.rid = built["record_id"]
            self.book["Submissions"]["rows"] = [(1, built["submission"])]
            self.book["Answers"]["rows"] = [(i + 1, a) for i, a in enumerate(built["answers"])]
            payload = self.successor(); changed = payload["packet"]
            changed["manifest"][field] = ["OPT1"]
            changed["manifest"]["optional_ids"] = [required]
            for response in changed["responses"]:
                if response["category"] == category and response["question_id"] == required:
                    response["optional"] = True
                elif response["question_id"] == "OPT1":
                    response["optional"] = False
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "protected_field"):
                saving.save("signed", payload, CONFIG)
            self.assertEqual(self.batches, 0); self.assertEqual(self.port.rows, {})

    def test_identity_requirements_evidence_and_checkpoint_are_not_import_edit_fields(self):
        for mutate in (
            lambda p: p["identity"].update(display_name="Different name"),
            lambda p: p.update(stage_reference="Different checkpoint"),
            lambda p: p["responses"][0].update(evidence_note="Changed evidence"),
            lambda p: p["responses"][0].update(response_excerpt="bank answer own words"),
        ):
            payload = self.successor(); mutate(payload["packet"])
            with self.assertRaisesRegex(ValueError, "protected_field"):
                saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_successor_uses_existing_save_and_preserves_every_original_evidence_row(self):
        payload = self.successor(); before = deepcopy(self.book)
        result = saving.save("signed", payload, CONFIG)
        self.assertTrue(result["readback_verified"])
        self.assertEqual(result["superseded"], [self.rid])
        old = self.book["Submissions"]["rows"][0][1]
        expected = {**before["Submissions"]["rows"][0][1], "status": "superseded", "superseded_by": result["record_id"]}
        self.assertEqual(old, expected)
        self.assertEqual(self.book["Answers"]["rows"][:5], before["Answers"]["rows"])
        self.assertEqual(self.batches, 1)
        loaded = review.load("signed", result["record_id"], CONFIG)
        self.assertEqual(loaded["packet"], payload["packet"])
        self.assertEqual(loaded["status"], "saved_review_ready")

    def test_repeated_successor_save_only_reads_back_and_does_not_append_again(self):
        payload = self.successor()
        first = saving.save("signed", payload, CONFIG)
        before = deepcopy(self.book); state = deepcopy(self.port.rows)
        second = saving.save("signed", payload, CONFIG)
        self.assertEqual(second["status"], "already_saved")
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(second["superseded"], [self.rid])
        self.assertEqual(self.book, before); self.assertEqual(self.port.rows, state)
        self.assertEqual(self.batches, 1)

    def test_unchanged_saved_review_does_not_claim_or_write(self):
        loaded = self.load()
        result = saving.save("signed", {"request_id": "synthetic-unchanged-request",
            "packet": self.packet, "review_basis": loaded["review_basis"]}, CONFIG)
        self.assertEqual(result["status"], "already_saved")
        self.assertEqual(self.batches, 0); self.assertEqual(self.port.rows, {})

    def test_active_or_expired_pending_save_is_preserved_and_never_adopted(self):
        payload = self.successor(); cloud = CloudState(self.port)
        task = cloud.create_task(review.task_scope(self.packet))
        cloud.claim(task, owner="other-writer", request_id="other-attempt", input_hash="prior", ttl_seconds=30)
        for elapsed in (0, 40):
            self.port.advance(elapsed); before = deepcopy(self.port.rows)
            for action in (self.load, lambda: saving.save("signed", payload, CONFIG)):
                with self.assertRaisesRegex(Conflict, "review_save_pending"):
                    action()
            self.assertEqual(self.port.rows, before)
        self.assertEqual(self.batches, 0)

    def test_lost_native_save_reply_allows_exact_readback_without_replay_or_fence_clearing(self):
        payload = self.successor(); native = self.batch
        def uncertain(*args):
            native(*args)
            raise saving.ConnectionError("assessment_write_uncertain")
        with patch.object(saving, "_batch", side_effect=uncertain):
            with self.assertRaisesRegex(saving.ConnectionError, "uncertain"):
                saving.save("signed", payload, CONFIG)
        state = deepcopy(self.port.rows)
        result = saving.save("signed", payload, CONFIG)
        self.assertEqual(result["status"], "already_saved")
        self.assertEqual(self.batches, 1); self.assertEqual(self.port.rows, state)
        with self.assertRaisesRegex(Conflict, "review_save_pending"):
            review.load("signed", result["record_id"], CONFIG)

    def test_writer_claimed_and_expired_between_preflight_and_claim_is_never_adopted(self):
        payload = self.successor(); original_check = review.no_pending_save
        preserved = {}
        def race(port, item):
            result = original_check(port, item)
            cloud = CloudState(port); task = cloud.create_task(review.task_scope(item))
            cloud.claim(task, owner="other-writer", request_id="racing-attempt", input_hash="prior", ttl_seconds=30)
            port.advance(40); preserved.update(deepcopy(port.rows))
            return result
        with patch.object(review, "no_pending_save", side_effect=race), self.assertRaisesRegex(Conflict, "review_save_pending"):
            saving.save("signed", payload, CONFIG)
        self.assertEqual(self.port.rows, preserved)
        self.assertEqual(self.batches, 0)

    def test_claim_cas_contention_does_not_retry_into_an_expired_foreign_run(self):
        payload = self.successor(); cloud = CloudState(self.port)
        task = cloud.create_task(review.task_scope(self.packet))
        commit = self.port.commit; preserved = {}; raced = False
        def competing(changes):
            nonlocal raced
            value = changes.get(task, ({}, None))[0]
            if not raced and value.get("owner", "").startswith("assess-"):
                raced = True
                cloud.claim(task, owner="foreign", request_id="foreign-cas-attempt", input_hash="foreign", ttl_seconds=30)
                self.port.advance(40); preserved.update(deepcopy(self.port.rows))
            return commit(changes)
        with patch.object(self.port, "commit", side_effect=competing), self.assertRaisesRegex(Conflict, "review_save_pending"):
            saving.save("signed", payload, CONFIG)
        self.assertTrue(raced); self.assertEqual(self.port.rows, preserved)
        self.assertEqual(self.batches, 0)

    def test_literal_feedback_and_each_answer_must_read_back_before_retention(self):
        for table, field in (("Submissions", "feedback"), ("Answers", "response"),
                             ("Answers", "evidence_note")):
            before = deepcopy(self.book); payload = self.successor(); native = self.batch
            def corrupt(*args):
                native(*args); self.book[table]["rows"][-1][1][field] = "Corrupt saved field"
            with patch.object(saving, "_batch", side_effect=corrupt), self.subTest(field=field):
                with self.assertRaisesRegex(saving.ConnectionError, "readback_failed"):
                    saving.save("signed", payload, CONFIG)
            self.assertFalse(any(d.value.get("name") == "reviewed_packet" for d in self.port.rows.values()))
            self.book = before; self.port.rows.clear()

    def test_source_or_record_changes_during_final_guard_stop_native_batch(self):
        payload = self.successor(); captures = 0
        def racing(*args):
            nonlocal captures
            captures += 1
            if captures == 2:
                self.book["Submissions"]["rows"][0][1]["feedback"] = "Racing teacher change"
            return deepcopy(self.capture)
        with patch.object(saving, "capture", side_effect=racing), self.assertRaisesRegex(Conflict, "review_changed"):
            saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_expired_final_claim_blocks_native_batch(self):
        payload = self.successor(); captures = 0
        def slow(*args):
            nonlocal captures
            captures += 1
            if captures == 2: self.port.advance(400)
            return deepcopy(self.capture)
        with patch.object(saving, "capture", side_effect=slow), self.assertRaises(LeaseLost):
            saving.save("signed", payload, CONFIG)
        self.assertEqual(self.batches, 0)

    def test_held_superseded_unknown_stage_and_duplicate_records_refuse_load(self):
        for updates in ({"hold_reason": "needs review"}, {"status": "superseded"},
                        {"assessment_stage": "unknown"}):
            old = deepcopy(self.book)
            self.book["Submissions"]["rows"][0][1].update(updates)
            with self.subTest(updates=updates), self.assertRaises(ValueError): self.load()
            self.book = old
        self.book["Submissions"]["rows"].append(deepcopy(self.book["Submissions"]["rows"][0]))
        with self.assertRaises(Exception): self.load()
        self.assertEqual(self.batches, 0)

    def test_get_route_uses_loader_and_rejects_extra_query_fields(self):
        from school_workflow_hub.web import application
        def call(query):
            status = []
            body = b"".join(application({"PATH_INFO": "/api/assessment-save", "REQUEST_METHOD": "GET",
                "QUERY_STRING": query}, lambda value, headers: status.append(value)))
            return status[0], json.loads(body)
        with patch.object(review, "load", return_value={"external_writes": 0}) as loader:
            self.assertEqual(call("record_id=fictional-record")[0], "200 OK")
            loader.assert_called_once()
            self.assertTrue(call("record_id=x&file_id=y")[0].startswith("400"))

    def test_loading_authentication_failure_precedes_capture_and_state_reads(self):
        before = deepcopy(self.port.rows)
        with patch.object(saving, "read_records", side_effect=saving.ConnectionError("sign_in_required")), \
             patch.object(saving, "capture") as capture:
            with self.assertRaisesRegex(saving.ConnectionError, "sign_in_required"): self.load()
            capture.assert_not_called()
        self.assertEqual(self.port.rows, before)
