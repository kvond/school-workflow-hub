"""Fresh explicit reviews against fictional workbook/CAS state, never live data.

The historical packet is deliberately absent from every store. Its raw rows
protect history and scope; only the complete new packet supplies judgments.
"""
from copy import deepcopy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_review as review
from school_workflow_hub.adapters import assessment_save as saving
from school_workflow_hub.adapters import assessment_provider as provider, grading_records
from school_workflow_hub.core.cloud_state import CloudState, Conflict, LeaseLost, key
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from tests import test_assessment_review as fixtures
from tests import test_assessment_records as packet_fixtures


MODE = "fresh_current_review"
CONFIG = fixtures.CONFIG


def source_text(version, completed):
    ids = ["N%02d" % n for n in range(1, 30)]
    header = ("[[MARKER-INVENTORY:SYN29]]\nVERSION=SYN29-r%s\n"
              "NOTES=29 DRAFT=0 OPTIONAL=0 BANK_TERMS=0\nNOTES_IDS=\n" % version)
    header += "\n".join(ids) + "\nDRAFT_IDS=\nOPTIONAL_IDS=\nBANK_IDS=\n\n"
    answers = ["[[NOTES:SYN29:%s]] Question %s:\n%s" % (
        qid, qid, "Fictional authored observation " + qid + "." if n < completed else "")
        for n, qid in enumerate(ids)]
    return header + "\n".join(answers)


class FreshReviewTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids discovering the fixture TestCase a second time.
        self.f = fixtures.ReviewTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.old_capture = self.capture_for("9", 5)
        self.f.packet = self.packet_for(self.old_capture, 5)
        self.f.packet["feedback"] = "Preserved historical feedback; no new judgment inferred."
        old = records.build_rows(self.f.packet, records.completion(self.f.packet),
                                 self.old_capture, now=fixtures.NOW)
        self.f.rid = old["record_id"]
        self.f.book = {}
        tables = {"Assignments": [{"assignment_key": self.f.packet["assignment_key"],
                                   "class_key": self.f.packet["class_key"],
                                   "assignment_title": "Fictional 29-item lesson"}],
                  "Submissions": [old["submission"]], "Answers": old["answers"],
                  "Class summaries": []}
        for number, (title, required) in enumerate(grading_records.HEADERS.items()):
            rows = tables[title]
            headers = list(dict.fromkeys([*required, *(k for row in rows for k in row)]))
            self.f.book[title] = {"sheet_id": number, "headers": headers,
                "rows": [(i + 1, deepcopy(row)) for i, row in enumerate(rows)]}
        self.f.capture = self.capture_for("34", 8)
        self.current = self.packet_for(self.f.capture, 8)
        self.original = deepcopy(self.f.book)
        for name in ("_stored_draft", "draft", "stored_draft_for"):
            mocked = patch.object(provider, name, side_effect=AssertionError("No draft/cache recovery"))
            mocked.start(); self.addCleanup(mocked.stop)

    def capture_for(self, version, completed):
        text = source_text(version, completed)
        digest = hashlib.sha256(text.encode()).hexdigest()
        return {"schema_version": saving.CAPTURE_VERSION,
            "file": {"id": "f" * 24, "name": "Fictional 29-item submission", "mime_type": "p",
                     "version": version, "modified_at": "2026-09-%sT14:00:00Z" % ("16" if version == "9" else "18")},
            "text": text, "sha256": digest, "raw_sha256": digest,
            "metadata_sha256": hashlib.sha256(("metadata-" + version).encode()).hexdigest(),
            "captured_at": fixtures.NOW}

    def packet_for(self, capture, completed):
        item = packet_fixtures.packet(stage="initial")
        item["source"].update(file_id=capture["file"]["id"], sha256=capture["sha256"],
            version=capture["file"]["version"], modified_at=capture["file"]["modified_at"])
        item["manifest"] = records.derive_manifest(capture["text"])
        item["responses"] = [packet_fixtures.response(qid, "NOTES",
            "meaningful_attempt" if n < completed else "blank",
            "Fictional authored observation " + qid + "." if n < completed else "",
            conceptual="Developing" if n < completed else "Evidence unavailable",
            explanation="Names an idea" if n < completed else "Evidence unavailable")
            for n, qid in enumerate(item["manifest"]["notes_ids"])]
        item["stage_reference"] = "Explicit fresh teacher review of the current notes checkpoint."
        item["feedback"] = "Fresh explicit reviewed report about the current fictional work."
        records.validate_packet(item, capture["text"])
        return item

    def load(self):
        return review.load("signed", self.f.rid, CONFIG, mode=MODE)

    def payload(self, packet=None):
        item = deepcopy(packet or self.current)
        basis = self.load()["review_basis"]
        return {"request_id": "synthetic-fresh-review-request", "packet": item,
                "review_basis": {**basis, "packet_sha256": content_hash(item)}}

    def save(self, payload):
        return saving.save("signed", payload, CONFIG)

    def assert_preflight_hold(self, payload):
        before = deepcopy(self.f.book); state = deepcopy(self.f.port.rows)
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)):
            self.save(payload)
        self.assertEqual(self.f.book, before)
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.batches, 0)

    def assert_history(self, successor_id):
        old = self.f.book["Submissions"]["rows"][0][1]
        self.assertEqual(old, {**self.original["Submissions"]["rows"][0][1],
                              "status": "superseded", "superseded_by": successor_id})
        self.assertEqual(self.f.book["Answers"]["rows"][:29], self.original["Answers"]["rows"])

    def test_fresh_load_without_historical_packet_reads_current_binding_and_never_guesses(self):
        self.assertEqual(review.FRESH_MODE, MODE)
        result = self.load()
        self.assertEqual(result["status"], "fresh_packet_required")
        self.assertIsNone(result["packet"])
        self.assertEqual(result["base"], review.baseline(self.original, self.f.rid))
        self.assertEqual(result["review_basis"], {"mode": MODE, "record_id": self.f.rid,
            "record_sha256": content_hash(result["base"]), "source": review.source_binding(self.f.capture)})
        self.assertEqual(result["base"]["submission"]["source_revision"], "9")
        self.assertEqual(result["review_basis"]["source"]["version"], "34")
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)
        self.assertEqual(self.f.port.rows, {})
        self.assertEqual(self.f.book, self.original)

    def test_default_unchanged_source_review_is_not_silently_fresh_review(self):
        with self.assertRaisesRegex(Conflict, "submission_changed"):
            review.load("signed", self.f.rid, CONFIG)
        with self.assertRaises(ValueError):
            review.load("signed", self.f.rid, CONFIG, mode="unrecognized")
        self.assertEqual(self.f.port.rows, {})

    def test_fresh_load_authenticates_before_capture_or_state(self):
        with patch.object(saving, "read_records", side_effect=saving.ConnectionError("sign_in_required")), \
             patch.object(saving, "capture") as capture:
            with self.assertRaisesRegex(saving.ConnectionError, "sign_in_required"):
                self.load()
            capture.assert_not_called()
        self.assertEqual(self.f.port.rows, {})

    def test_http_fresh_review_get_and_explicit_import_post_use_existing_guarded_route(self):
        from school_workflow_hub.web import application
        origin = "https://teacher.example.invalid"

        def call(method, query="", payload=None):
            body = json.dumps(payload).encode() if payload is not None else b""
            captured = []
            environ = {"PATH_INFO": "/api/assessment-save", "REQUEST_METHOD": method,
                "QUERY_STRING": query, "HTTP_X_GOOG_IAP_JWT_ASSERTION": "fictional-assertion",
                "HTTP_ORIGIN": origin, "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
            response = b"".join(application(environ,
                lambda status, headers: captured.append((status, dict(headers)))))
            return captured[0][0], captured[0][1], json.loads(response)

        with patch.dict("os.environ", {**CONFIG, "HUB_STATE_PROBE_ORIGINS": origin}):
            query = "record_id=" + self.f.rid + "&mode=" + MODE
            status, headers, loaded = call("GET", query)
            self.assertEqual(status, "200 OK")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(loaded["status"], "fresh_packet_required")
            self.assertIsNone(loaded["packet"])
            self.assertEqual(loaded["review_basis"]["source"]["version"], "34")
            self.assertEqual(self.f.port.rows, {})
            for invalid in ("record_id=" + self.f.rid + "&mode=unknown", query + "&extra=x"):
                with self.subTest(query=invalid):
                    self.assertTrue(call("GET", invalid)[0].startswith("400"))
            self.assertEqual(self.f.batches, 0)
            payload = {"request_id": "synthetic-fresh-http-import", "packet": deepcopy(self.current),
                "review_basis": {**loaded["review_basis"], "packet_sha256": content_hash(self.current)}}
            self.assertEqual(payload["review_basis"]["mode"], MODE)
            status, headers, saved = call("POST", payload=payload)
            self.assertEqual(status, "200 OK")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(saved["status"], "saved")
            self.assertTrue(saved["readback_verified"])
            self.assertEqual(saved["answers"], 29)
            self.assertEqual(saved["source"]["version"], "34")
        self.assertEqual(self.f.batches, 1)
        self.assert_history(saved["record_id"])

    def test_complete_explicit_current_review_saves_once_preserves_history_and_existing_arithmetic(self):
        payload = self.payload()
        expected = records.completion(payload["packet"])
        self.assertEqual(expected["counts"]["NOTES"], {"completed": 8, "required": 29, "due": True})
        self.assertIsNone(expected["scores"]["revision"]["value"])
        result = self.save(payload)
        self.assertEqual(result["status"], "saved")
        self.assertTrue(result["readback_verified"])
        self.assertEqual(result["content_score_exact"], expected["scores"]["content_notes"]["exact_value"])
        self.assertEqual(result["revision_score"], None)
        self.assertEqual(result["superseded"], [self.f.rid])
        self.assertEqual(result["source"]["version"], "34")
        self.assertEqual(self.f.batches, 1)
        self.assertEqual(len(self.f.book["Answers"]["rows"]), 58)
        saved = self.f.book["Submissions"]["rows"][-1][1]
        self.assertEqual(saved["notes_completed"], "8")
        self.assertEqual(saved["notes_required"], "29")
        self.assertEqual(saved["feedback"], self.current["feedback"])
        self.assertEqual(saved["ic_posting_status"], "unposted")
        self.assert_history(result["record_id"])
        retained = [d.value["payload"] for d in self.f.port.rows.values()
                    if d.value.get("name") == "reviewed_packet"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["packet"], self.current)

    def test_fresh_review_allows_explicit_new_feedback_levels_and_stage_reference(self):
        item = deepcopy(self.current)
        item["stage_reference"] = "A separately explicit fresh checkpoint review."
        item["comparison_status"] = "No historical judgment reconstructed."
        item["feedback"] = "A fresh teacher-reviewed report, not old cached feedback."
        item["responses"][0].update(conceptual_level="Proficient", conceptual_feedback="New reviewed feedback")
        result = self.save(self.payload(item))
        self.assertTrue(result["readback_verified"])
        self.assert_history(result["record_id"])

    def test_exact_identity_scope_stage_file_and_ic_mapping_are_preserved(self):
        mutations = [lambda p: p.update(class_key="anatomy"),
            lambda p: p.update(assignment_key="OTHER-LESSON"),
            lambda p: p.update(section_id="different-section"),
            lambda p: p.update(section_name="Different section"),
            lambda p: p.update(stage="final"),
            lambda p: p.update(ic_assignment_id="different-ic-map"),
            lambda p: p["source"].update(file_id="g" * 24, url="https://docs.google.com/presentation/d/" + "g" * 24 + "/edit")]
        mutations += [(lambda field, value: lambda p: p["identity"].update({field: value}))(field, value)
            for field, value in (("student_id", "123456"), ("student_id_kind", "different-kind"),
                ("learner_key", "roster:" + "b" * 32), ("display_name", "Other Fictional Learner"),
                ("identity_basis", "different identity evidence"))]
        for n, mutate in enumerate(mutations):
            with self.subTest(mutation=n):
                payload = self.payload(); mutate(payload["packet"])
                payload["review_basis"]["packet_sha256"] = content_hash(payload["packet"])
                self.assert_preflight_hold(payload)

    def test_complete_packet_requires_every_required_item_and_literal_current_evidence(self):
        mutations = [lambda p: p["responses"].pop(),
            lambda p: p["responses"].append(deepcopy(p["responses"][0])),
            lambda p: p["responses"][0].update(response_excerpt="Fabricated words absent from the current source"),
            lambda p: p["responses"][0].update(judgment="review")]
        for n, mutate in enumerate(mutations):
            with self.subTest(mutation=n):
                payload = self.payload(); mutate(payload["packet"])
                payload["review_basis"]["packet_sha256"] = content_hash(payload["packet"])
                self.assert_preflight_hold(payload)

    def test_current_manifest_cannot_reclassify_required_notes_as_optional(self):
        payload = self.payload(); item = payload["packet"]
        item["manifest"]["notes_ids"].remove("N29")
        item["manifest"]["optional_ids"].append("N29")
        item["responses"][-1]["optional"] = True
        payload["review_basis"]["packet_sha256"] = content_hash(item)
        self.assert_preflight_hold(payload)

    def test_malformed_or_unknown_fresh_basis_never_falls_back_to_generic_save(self):
        for field in ("mode", "packet_sha256", "source", "record_sha256", "record_id"):
            with self.subTest(missing=field):
                payload = self.payload(); del payload["review_basis"][field]
                self.assert_preflight_hold(payload)
        for field, value in (("mode", "unknown"), ("packet_sha256", "0" * 64),
                             ("record_sha256", "0" * 64), ("source", {})):
            with self.subTest(field=field):
                payload = self.payload(); payload["review_basis"][field] = value
                self.assert_preflight_hold(payload)
        payload = self.payload(); payload["review_basis"]["unexpected"] = "field"
        self.assert_preflight_hold(payload)

    def test_changed_fresh_packet_requires_its_exact_explicit_digest(self):
        payload = self.payload(); payload["packet"]["feedback"] += " Unbound edit."
        self.assert_preflight_hold(payload)

    def test_each_packet_source_field_must_match_current_capture_even_with_updated_packet_digest(self):
        for field, value in (("sha256", "b" * 64), ("version", "35"),
                             ("modified_at", "2026-09-19T14:00:00Z")):
            with self.subTest(field=field):
                payload = self.payload(); payload["packet"]["source"][field] = value
                payload["review_basis"]["packet_sha256"] = content_hash(payload["packet"])
                self.assert_preflight_hold(payload)

    def test_source_changed_again_or_access_metadata_changed_after_load_holds(self):
        for field in ("sha256", "raw_sha256", "metadata_sha256", "version", "modified_at"):
            with self.subTest(field=field):
                payload = self.payload(); original = deepcopy(self.f.capture)
                target = self.f.capture["file"] if field in ("version", "modified_at") else self.f.capture
                target[field] = "35" if field == "version" else "2026-09-19T14:00:00Z" if field == "modified_at" else "b" * 64
                self.assert_preflight_hold(payload)
                self.f.capture = original

    def test_concurrent_predecessor_feedback_or_answer_change_blocks_before_claim(self):
        for table, field in (("Submissions", "feedback"), ("Answers", "evidence_note")):
            payload = self.payload(); old = deepcopy(self.f.book)
            self.f.book[table]["rows"][0][1][field] = "Concurrent historical correction"
            with self.subTest(field=field): self.assert_preflight_hold(payload)
            self.f.book = old

    def test_competing_active_assessment_blocks_fresh_successor(self):
        payload = self.payload()
        other = deepcopy(self.f.book["Submissions"]["rows"][0][1]); other["record_id"] = "asmt-concurrent"
        self.f.book["Submissions"]["rows"].append((2, other))
        self.assert_preflight_hold(payload)

    def test_active_and_expired_pending_claims_are_preserved_without_adoption(self):
        payload = self.payload(); cloud = CloudState(self.f.port)
        task = cloud.create_task(review.task_scope(self.current))
        cloud.claim(task, owner="foreign", request_id="foreign-attempt", input_hash="foreign", ttl_seconds=30)
        for elapsed in (0, 40):
            self.f.port.advance(elapsed); preserved = deepcopy(self.f.port.rows)
            with self.subTest(elapsed=elapsed):
                with self.assertRaises(Conflict): self.load()
                with self.assertRaises(Conflict): self.save(payload)
            self.assertEqual(self.f.port.rows, preserved)
        self.assertEqual(self.f.batches, 0)

    def test_writer_racing_after_preflight_then_expiring_is_never_adopted(self):
        from school_workflow_hub.adapters import assessment_attempts
        payload = self.payload(); check = review.no_pending_save; preserved = {}
        original_book = deepcopy(self.f.book)
        def race(port, item):
            result = check(port, item)
            cloud = CloudState(port); task = cloud.create_task(review.task_scope(item))
            cloud.claim(task, owner="foreign", request_id="racing-attempt", input_hash="foreign", ttl_seconds=30)
            port.advance(40); preserved.update(deepcopy(port.rows))
            return result
        with patch.object(review, "no_pending_save", side_effect=race), self.assertRaises(Conflict):
            self.save(payload)
        task = key('task', review.task_scope(payload['packet']))
        run = key('run', {'task': task, 'request': payload['request_id']})
        attempt_id = key('assessmentattempt', {'task': task, 'run': run})
        self.assertEqual(set(self.f.port.rows), set(preserved) | {attempt_id})
        self.assertEqual({ident: self.f.port.rows[ident] for ident in preserved}, preserved)
        retained = assessment_attempts.read(self.f.port, task, run)
        self.assertEqual(retained['request'], payload)
        self.assertEqual(retained['captured_source'], review.source_binding(self.f.capture))
        self.assertNotIn(run, self.f.port.rows)
        self.assertEqual(self.f.book, original_book)
        self.assertEqual(self.f.batches, 0)

    def test_claim_cas_contention_cannot_retry_into_expired_foreign_run(self):
        payload = self.payload(); cloud = CloudState(self.f.port)
        task = cloud.create_task(review.task_scope(self.current))
        commit = self.f.port.commit; preserved = {}; raced = False
        def competing(changes):
            nonlocal raced
            value = changes.get(task, ({}, None))[0]
            if not raced and value.get("owner", "").startswith("assess-"):
                raced = True
                cloud.claim(task, owner="foreign", request_id="foreign-cas", input_hash="foreign", ttl_seconds=30)
                self.f.port.advance(40); preserved.update(deepcopy(self.f.port.rows))
            return commit(changes)
        with patch.object(self.f.port, "commit", side_effect=competing), self.assertRaises(Conflict):
            self.save(payload)
        self.assertTrue(raced)
        self.assertEqual(self.f.port.rows, preserved)
        self.assertEqual(self.f.batches, 0)

    def test_expired_final_claim_stops_native_write(self):
        payload = self.payload(); count = 0
        def slow(*args):
            nonlocal count
            count += 1
            if count == 2: self.f.port.advance(400)
            return deepcopy(self.f.capture)
        with patch.object(saving, "capture", side_effect=slow), self.assertRaises(LeaseLost):
            self.save(payload)
        self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.book, self.original)

    def test_final_owner_or_fence_change_stops_native_write(self):
        payload = self.payload(); count = 0
        def takeover(*args):
            nonlocal count
            count += 1
            if count == 3:
                cloud = CloudState(self.f.port)
                task = cloud.create_task(review.task_scope(self.current))
                doc = self.f.port.read([task]).documents[task]
                value = {**doc.value, "owner": "foreign-owner", "fence": doc.value["fence"] + 1}
                self.f.port.commit({task: (value, doc.version)})
            return deepcopy(self.f.capture)
        with patch.object(saving, "capture", side_effect=takeover), self.assertRaises(LeaseLost):
            self.save(payload)
        self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.book, self.original)

    def test_source_change_during_final_workbook_read_is_caught_by_later_bookend(self):
        payload = self.payload(); count = 0
        def race(*args):
            nonlocal count
            count += 1
            if count == 2: self.f.capture["metadata_sha256"] = "b" * 64
            return deepcopy(self.f.book)
        with patch.object(saving, "_workbook", side_effect=race), self.assertRaises(Conflict):
            self.save(payload)
        self.assertGreaterEqual(count, 2)
        self.assertEqual(self.f.batches, 0)

    def test_source_or_historical_record_changes_in_final_capture_stop_batch(self):
        payload = self.payload(); count = 0
        def race(*args):
            nonlocal count
            count += 1
            if count == 2: self.f.book["Submissions"]["rows"][0][1]["feedback"] = "Racing correction"
            return deepcopy(self.f.capture)
        with patch.object(saving, "capture", side_effect=race), self.assertRaises(Conflict):
            self.save(payload)
        self.assertEqual(self.f.batches, 0)

    def test_repeat_save_reads_back_without_new_native_or_state_writes(self):
        payload = self.payload(); first = self.save(payload)
        book = deepcopy(self.f.book); state = deepcopy(self.f.port.rows)
        second = self.save(payload)
        self.assertEqual(second["status"], "already_saved")
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(second["superseded"], [self.f.rid])
        self.assertEqual(self.f.book, book); self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.batches, 1)
        self.assert_history(first["record_id"])

    def test_new_fresh_basis_on_active_successor_replays_only_the_identical_completed_packet(self):
        original_payload = self.payload(); first = self.save(original_payload)
        loaded = review.load("signed", first["record_id"], CONFIG, mode=MODE)
        replay = {"request_id": "synthetic-active-packet-replay", "packet": deepcopy(self.current),
            "review_basis": {**loaded["review_basis"], "packet_sha256": content_hash(self.current)}}
        task_id = key("task", review.task_scope(self.current))
        completed_id = key("completed", {"task": task_id, "input_hash": content_hash(self.current)})
        self.assertEqual(self.f.port.rows[completed_id].value["input_hash"], content_hash(self.current))
        self.assertTrue(self.f.port.rows[completed_id].value["receipt"])
        state = deepcopy(self.f.port.rows); book = deepcopy(self.f.book)
        result = self.save(replay)
        self.assertEqual(result["status"], "already_saved")
        self.assertEqual(result["record_id"], first["record_id"])
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 1)

    def test_same_record_id_and_projection_cannot_acknowledge_changed_full_manifest_packet(self):
        first = self.save(self.payload())
        loaded = review.load("signed", first["record_id"], CONFIG, mode=MODE)
        changed = deepcopy(self.current)
        changed["manifest"]["version_label"] = "SYN29-r34"
        records.validate_packet(changed, self.f.capture["text"])
        self.assertEqual(records.record_id(changed), first["record_id"])
        original = review.baseline(self.f.book, first["record_id"])
        self.assertEqual(review.projection(changed, self.f.capture, original),
                         review.projection(self.current, self.f.capture, original))
        self.assertNotEqual(content_hash(changed), content_hash(self.current))
        payload = {"request_id": "synthetic-manifest-collision", "packet": changed,
            "review_basis": {**loaded["review_basis"], "packet_sha256": content_hash(changed)}}
        state = deepcopy(self.f.port.rows); book = deepcopy(self.f.book)
        with self.assertRaisesRegex(Conflict, "^assessment_record_conflict$"):
            self.save(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 1)

    def test_complete_workbook_rows_and_self_asserted_hash_cannot_replace_missing_packet_proof(self):
        first = self.save(self.payload())
        self.f.port.rows.clear()
        loaded = review.load("signed", first["record_id"], CONFIG, mode=MODE)
        payload = {"request_id": "synthetic-unproved-active-replay", "packet": deepcopy(self.current),
            "review_basis": {**loaded["review_basis"], "packet_sha256": content_hash(self.current)}}
        book = deepcopy(self.f.book)
        with self.assertRaisesRegex(Conflict, "^assessment_record_conflict$"):
            self.save(payload)
        self.assertEqual(self.f.port.rows, {})
        self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 1)

    def test_completed_replay_requires_original_full_input_hash_and_completed_receipt(self):
        payload = self.payload(); self.save(payload)
        task_id = key("task", review.task_scope(self.current))
        completed_id = key("completed", {"task": task_id, "input_hash": content_hash(self.current)})
        original = deepcopy(self.f.port.rows)
        book = deepcopy(self.f.book)
        for field, value in (("input_hash", "0" * 64), ("task_id", "task-other"), ("receipt", None)):
            with self.subTest(field=field):
                self.f.port.rows = deepcopy(original)
                doc = self.f.port.rows[completed_id]
                altered = {**doc.value, field: value}
                self.f.port.commit({completed_id: (altered, doc.version)})
                state = deepcopy(self.f.port.rows)
                with self.assertRaisesRegex(Conflict, "^assessment_record_conflict$"):
                    self.save(payload)
                self.assertEqual(self.f.port.rows, state)
                self.assertEqual(self.f.book, book)
                self.assertEqual(self.f.batches, 1)

    def test_lost_native_reply_reconciles_exact_saved_rows_without_clearing_pending_claim(self):
        payload = self.payload(); native = self.f.batch
        def uncertain(*args):
            native(*args)
            raise saving.ConnectionError("assessment_write_uncertain")
        with patch.object(saving, "_batch", side_effect=uncertain), self.assertRaises(saving.ConnectionError):
            self.save(payload)
        self.f.port.advance(400)
        state = deepcopy(self.f.port.rows); book = deepcopy(self.f.book)
        task_id = key("task", review.task_scope(self.current))
        run_id = state[task_id].value["active_run"]
        self.assertEqual(state[run_id].value["input_hash"], content_hash(payload["packet"]))
        self.assertIsNone(state[run_id].value["receipt"])
        result = self.save(payload)
        self.assertEqual(result["status"], "already_saved")
        self.assertEqual(self.f.batches, 1)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertFalse(any(d.value.get("name") == "reviewed_packet" for d in state.values()))

    def test_pending_replay_cannot_use_a_different_input_or_unrelated_or_missing_run(self):
        payload = self.payload(); native = self.f.batch
        def uncertain(*args):
            native(*args)
            raise saving.ConnectionError("assessment_write_uncertain")
        with patch.object(saving, "_batch", side_effect=uncertain), self.assertRaises(saving.ConnectionError):
            self.save(payload)
        task_id = key("task", review.task_scope(self.current))
        original = deepcopy(self.f.port.rows)
        run_id = original[task_id].value["active_run"]
        book = deepcopy(self.f.book)
        for field, value in (("input_hash", "0" * 64), ("task_id", "task-other"), ("missing", None)):
            with self.subTest(field=field):
                self.f.port.rows = deepcopy(original)
                if field == "missing":
                    del self.f.port.rows[run_id]
                else:
                    doc = self.f.port.rows[run_id]
                    self.f.port.commit({run_id: ({**doc.value, field: value}, doc.version)})
                state = deepcopy(self.f.port.rows)
                with self.assertRaisesRegex(Conflict, "^assessment_(record_conflict|review_save_pending)$"):
                    self.save(payload)
                self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
                self.assertEqual(self.f.batches, 1)

    def test_uncertain_reply_without_saved_rows_never_replays_after_expiry(self):
        payload = self.payload()
        with patch.object(saving, "_batch", side_effect=saving.ConnectionError("assessment_write_uncertain")), \
             self.assertRaises(saving.ConnectionError):
            self.save(payload)
        self.f.port.advance(400); state = deepcopy(self.f.port.rows)
        with self.assertRaises(Conflict): self.save(payload)
        self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.book, self.original)

    def test_history_and_successor_content_corruption_fail_before_retaining_packet(self):
        cases = [("Answers", 0, "evidence_note"), ("Submissions", 0, "feedback"),
                 ("Answers", -1, "response"), ("Submissions", -1, "feedback")]
        for table, position, field in cases:
            with self.subTest(table=table, position=position, field=field):
                payload = self.payload(); native = self.f.batch
                def corrupt(*args):
                    native(*args); self.f.book[table]["rows"][position][1][field] = "Corrupt saved evidence"
                with patch.object(saving, "_batch", side_effect=corrupt), self.assertRaises(saving.ConnectionError):
                    self.save(payload)
                self.assertFalse(any(d.value.get("name") == "reviewed_packet" for d in self.f.port.rows.values()))
                self.f.book = deepcopy(self.original); self.f.port.rows.clear(); self.f.batches = 0

    def test_already_saved_replay_still_requires_exact_packet_basis_and_predecessor(self):
        payload = self.payload(); self.save(payload)
        for mutate in (lambda p: p["review_basis"].update(packet_sha256="0" * 64),
                       lambda p: p["review_basis"].update(mode="unknown"),
                       lambda p: p["review_basis"].update(record_sha256="0" * 64)):
            changed = deepcopy(payload); mutate(changed)
            state = deepcopy(self.f.port.rows); book = deepcopy(self.f.book)
            with self.assertRaises((ValueError, Conflict)): self.save(changed)
            self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.f.book["Answers"]["rows"][0][1]["evidence_note"] = "Changed predecessor"
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)): self.save(payload)
        self.assertEqual(self.f.batches, 1)

    def test_already_saved_predecessor_change_during_replay_readback_is_not_acknowledged(self):
        payload = self.payload(); self.save(payload)
        state = deepcopy(self.f.port.rows)
        def race(*args):
            result = self.f.read_records(*args)
            self.f.book["Answers"]["rows"][0][1]["evidence_note"] = "Changed during replay readback"
            return result
        with patch.object(saving, "read_records", side_effect=race), \
             self.assertRaises((Conflict, saving.ConnectionError)):
            self.save(payload)
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.batches, 1)

    def test_actual_oversized_retained_packet_holds_before_any_claim_or_native_write(self):
        item = deepcopy(self.current); excerpt = "λ" * 6000
        self.f.capture["text"] += "\n" + excerpt
        self.f.capture["sha256"] = hashlib.sha256(self.f.capture["text"].encode()).hexdigest()
        self.f.capture["raw_sha256"] = self.f.capture["sha256"]
        item["source"]["sha256"] = self.f.capture["sha256"]
        for n, answer in enumerate(item["responses"]):
            answer.update(response_excerpt=excerpt, judgment="meaningful_attempt" if n < 8 else "printed_copy")
        records.validate_packet(item, self.f.capture["text"])
        self.assert_preflight_hold(self.payload(item))


if __name__ == "__main__":
    unittest.main()
