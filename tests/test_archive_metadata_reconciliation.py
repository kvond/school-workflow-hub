"""Fictional kind-specific archive repair; no live provider calls or records."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import Mock, patch

from hub_v2 import archive_metadata_reconciliation as repair, google
from hub_v2 import named_archival_work as named, student_archive as a
from school_workflow_hub.core.cloud_state import CloudState, Conflict, LeaseLost, StoreUnavailable
from tests.test_student_archive import FakeDrive, GuardedMemoryCAS, config, found
from tests.test_named_archival_work import ORIGINAL, COPY, CREATED, CONTENT, provenance as named_provenance
from tests.test_imported_original_archive import RAW, provenance as import_provenance


class ArchiveMetadataRepair(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), GuardedMemoryCAS(), config()
        for item in [patch.object(a, "configured_store", return_value=self.port),
            patch.object(google, "drive_file", side_effect=self.drive.metadata),
            patch.object(google, "folder_children", side_effect=self.drive.children),
            patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
            patch.object(google, "archive_create", side_effect=self.drive.create),
            patch.object(google, "rename_item", side_effect=self.drive.rename),
            patch.object(google, "move_item", side_effect=self.drive.move),
            patch.object(google, "get_json", side_effect=self.drive.copy),
            patch.object(a, "_work_content", side_effect=self.content),
            patch.object(a, "_native_presentation_proof", return_value=deepcopy(CONTENT))]:
            item.start(); self.addCleanup(item.stop)
        self.assertEqual(a.archive_work("read", "write", self.config, "biology", found())["status"], "verified")
        self.owner_key = next(k for k, doc in self.port.rows.items()
            if doc.value.get("student_key") == k and doc.value.get("items"))
        self.folder = self.port.rows[self.owner_key].value["drive_file_id"]
        self.records = {}
        self.drive.add(ORIGINAL, "Fictional original", named.MIME, ["FictionalOriginalFolder001"])
        self.drive.items[ORIGINAL]["createdTime"] = CREATED
        self.drive.add(COPY, "Fictional native archival copy", named.MIME, [self.folder])
        result = named.register("read", self.config, "biology", self.owner_key, COPY,
            archive_provenance=named_provenance(), expected_original=self.drive.metadata("read", ORIGINAL),
            expected_metadata=self.drive.metadata("read", COPY),
            expected_student=deepcopy(self.port.rows[self.owner_key].value), expected_content_proof=CONTENT,
            assignment="Fictional archival lesson", source_context={"class_key": "biology", "school_year": "2026-27"},
            filing_note="Original creation time retained; no authenticated submission event or grade eligibility.")
        self.records["native"] = result["record_key"]
        for label, mime, raw in (("pdf", a.PDF, RAW), ("png", "image/png", b"\x89PNG\r\n\x1a\nfictional")):
            file_id = "FictionalImportedRepair" + label
            self.drive.add(file_id, "Fictional imported original." + label, mime, [self.folder])
            self.drive.items[file_id].update(md5Checksum=hashlib.md5(raw).hexdigest(), size=str(len(raw)))
            self.drive.contents[file_id] = raw
            result = a.register_imported_original("read", self.config, "biology", self.owner_key, file_id,
                provenance=import_provenance(attachment_path="Fictional Student/Revision 1/Original." + label),
                original_bytes=raw, expected_metadata=self.drive.metadata("read", file_id),
                expected_student=deepcopy(self.port.rows[self.owner_key].value), assignment="Fictional research",
                source_context={"class_key": "biology", "school_year": "2026-27", "week": "Wk 04"},
                filing_note="Exact authenticated original; no inferred Form event.")
            self.records[label] = result["record_key"]
        for ident in self.records.values():
            self.drive.items[self.port.rows[ident].value["drive_file_id"]]["version"] = "3"
        self.drive.created.clear(); self.drive.copied.clear(); self.drive.renamed.clear(); self.drive.moved.clear()
        self.before = deepcopy(self.port.rows)

    def content(self, token, meta, heartbeat=None):
        return deepcopy(CONTENT) if meta["mimeType"] == named.MIME else self.drive.content(token, meta)

    def record(self, label):
        return self.port.rows[self.records[label]].value

    def preview(self, label):
        return repair.preview("read", self.config, "biology", self.records[label])

    def apply(self, label, *, preview=None, guard=None):
        preview = preview or self.preview(label)
        return repair.reconcile("read", "write", self.config, "biology", self.records[label],
            expected_revision=preview["revision"], actor="fictional-operator", before_source_write=guard or Mock())

    def assert_no_native_writes(self):
        self.assertEqual(self.drive.created + self.drive.copied + self.drive.renamed + self.drive.moved, [])

    def assert_no_reconciliation(self):
        for ident in self.records.values():
            self.assertEqual(self.port.rows[ident], self.before[ident])
        self.assertFalse(any(k.startswith(repair.HISTORY_PREFIX + "_") for k in self.port.rows))
        self.assert_no_native_writes()

    def test_all_three_kinds_keep_original_validator_roles_and_immutable_history(self):
        original_metadata = self.drive.metadata
        def never_current_original(token, ident, fields=None):
            self.assertNotEqual(ident, ORIGINAL)
            return original_metadata(token, ident, fields)
        self.drive.items[ORIGINAL]["version"] = "99"
        owner_before = deepcopy(self.port.rows[self.owner_key].value)
        with patch.object(google, "drive_file", side_effect=never_current_original):
            for label in self.records:
                old = deepcopy(self.record(label))
                preview = self.preview(label)
                self.assertEqual(preview["status"], "ready")
                result = self.apply(label, preview=preview)
                field = preview["proof"]["revision_field"]
                expected = {**old, field: {**old[field], "version": "3"},
                            "last_metadata_reconciliation": result["history_key"]}
                self.assertEqual(self.record(label), expected)
                history = self.port.rows[result["history_key"]].value
                self.assertEqual(history["before"], old)
                self.assertEqual(history["source_kind"], old["source_kind"])
                self.assertEqual(history["actor"], "fictional-operator")
                metadata = self.drive.metadata("read", old["drive_file_id"])
                if label == "native":
                    named.verify(metadata, self.record(label), scope=self.config[a.ROOT_CONFIG])
                    self.assertFalse(self.record(label)["grade_eligible"])
                else:
                    a._verify_imported_original(metadata, self.record(label))
                before_repeat = deepcopy(self.port.rows)
                self.assertFalse(self.apply(label)["changed"])
                self.assertEqual(self.port.rows, before_repeat)
        self.assertEqual(self.port.rows[self.owner_key].value, owner_before)
        self.assert_no_native_writes()

    def test_missing_or_changed_native_image_and_byte_proofs_hold_every_kind(self):
        for label in self.records:
            old = deepcopy(self.record(label))
            for proof in ({}, {**old["copied_content_proof"], "sha256": "f" * 64}):
                with self.subTest(label=label, proof=proof):
                    self.record(label)["copied_content_proof"] = proof
                    with self.assertRaises(a.Held): self.preview(label)
            self.port.rows[self.records[label]].value.clear()
            self.port.rows[self.records[label]].value.update(old)
        for label in ("pdf", "png"):
            file_id = self.record(label)["drive_file_id"]
            self.drive.contents[file_id] += b"changed bytes with unchanged metadata"
            with self.assertRaises(a.Held): self.preview(label)
        with patch.object(a, "_work_content", return_value={**CONTENT, "image_instances": 25}), self.assertRaises(a.Held):
            self.preview("native")
        self.assert_no_reconciliation()

    def test_other_revision_fields_access_parent_name_and_properties_cannot_be_reconciled(self):
        changes = {"parents": ["FictionalDifferentFolder001"], "modifiedTime": "2026-10-09T00:00:00Z",
            "createdTime": "2026-10-09T00:00:00Z", "trashed": True, "version": "0", "name": "Renamed",
            "properties": {"unexpected": "tag"}, "permissions": [{"type": "domain", "domain": "school.example", "role": "reader"}]}
        for label in self.records:
            file_id = self.record(label)["drive_file_id"]
            for field, value in changes.items():
                before = deepcopy(self.drive.items[file_id])
                self.drive.items[file_id][field] = value
                with self.subTest(label=label, field=field), self.assertRaises(a.Held): self.preview(label)
                self.drive.items[file_id] = before
        self.assert_no_reconciliation()

    def test_existing_source_identity_role_and_provenance_cannot_be_relabelled(self):
        for label in self.records:
            old = deepcopy(self.record(label))
            edits = [dict(source_kind="teacher_attributed_joint_copy"), dict(class_key="forensics"),
                     dict(source_context={"class_key": "forensics"}), dict(record_key="archiveimportedwork_" + "f" * 64)]
            if label == "native":
                edits += [dict(source_file_id="FictionalWrongOriginal001"), dict(grade_eligible=True),
                          dict(original_file_revision={}), dict(date="2026-09-17")]
            else:
                edits += [dict(lesson_status="unverified"), dict(form_id="FictionalPretendForm001"),
                          dict(import_provenance={**old["import_provenance"], "revision": 2})]
            for edits_for_one in edits:
                self.port.rows[self.records[label]].value.update(edits_for_one)
                with self.subTest(label=label, edit=edits_for_one), self.assertRaises(a.Held): self.preview(label)
                self.port.rows[self.records[label]].value.clear(); self.port.rows[self.records[label]].value.update(deepcopy(old))
        with self.assertRaises(a.Held): repair.preview("read", self.config, "biology", "archivejointworkcopy_" + "a" * 64)
        self.assert_no_reconciliation()

    def test_index_owner_and_folder_identity_must_all_agree(self):
        old = deepcopy(self.port.rows[self.owner_key].value)
        self.port.rows[self.owner_key].value["items"].pop(self.records["native"])
        with self.assertRaises(a.Held): self.preview("native")
        self.port.rows[self.owner_key].value.clear(); self.port.rows[self.owner_key].value.update(old)
        self.drive.items[self.folder]["properties"][a.TAG] = "different"
        with self.assertRaises(a.Held): self.preview("pdf")
        self.assert_no_reconciliation()

    def test_content_and_cas_bookends_refuse_concurrent_changes(self):
        def changed_copy(token, meta, heartbeat=None):
            result = self.content(token, meta)
            self.drive.items[meta["id"]]["version"] = "4"
            return result
        with patch.object(a, "_work_content", side_effect=changed_copy), self.assertRaises(a.Held): self.preview("native")
        self.drive.items[COPY]["version"] = "3"
        def changed_owner(token, meta, heartbeat=None):
            owner = self.port.rows[self.owner_key]
            self.port.commit({self.owner_key: ({**owner.value, "teacher_note": "preserved"}, owner.version)})
            return self.content(token, meta)
        with patch.object(a, "_work_content", side_effect=changed_owner), self.assertRaises(a.Held): self.preview("pdf")
        self.assert_no_reconciliation()

    def test_stale_preview_and_changed_source_context_do_not_save(self):
        preview = self.preview("native")
        self.record("native")["source_context"]["school_year"] = "2027-28"
        with self.assertRaises(Conflict): self.apply("native", preview=preview)
        self.record("native")["source_context"]["school_year"] = "2026-27"
        self.assert_no_reconciliation()

    def test_long_proof_finishes_before_class_lease(self):
        preview = self.preview("native")
        def slow(token, meta, heartbeat=None):
            self.port.advance(121)
            return self.content(token, meta)
        with patch.object(a, "_work_content", side_effect=slow):
            self.assertEqual(self.apply("native", preview=preview)["status"], "verified")
        self.assert_no_native_writes()

    def test_shared_guard_failure_expiry_short_budget_and_takeover_make_no_data_write(self):
        for mode in ("stopped", "expired", "short", "takeover"):
            self.port.rows = deepcopy(self.before)
            calls = []
            def guard(**kwargs):
                calls.append(kwargs)
                if len(calls) != 3: return
                if mode == "stopped": raise Conflict("source operation stopped")
                self.port.advance(61 if mode == "short" else 121)
                if mode == "takeover":
                    task = next(k for k, doc in self.port.rows.items() if k.startswith("task_") and doc.value.get("active_run"))
                    CloudState(self.port).claim(task, owner="other", request_id="takeover", input_hash="new")
            with self.subTest(mode=mode), self.assertRaises(Conflict): self.apply("pdf", guard=guard)
            self.assertEqual(len(calls), 3)
            self.assert_no_reconciliation()

    def test_ownership_is_checked_after_source_guard_without_another_native_call(self):
        preview = self.preview("png")
        read, clock, calls = self.port.read, [0], []
        def guard(**kwargs):
            calls.append(kwargs)
        def slow_owner(ids):
            result = read(ids)
            if len(calls) == 3 and len(ids) == 1 and ids[0].startswith("task_"):
                clock[0] += 61  # response time after its unchanged server timestamp
            return result
        with patch.object(a, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.port, "read", side_effect=slow_owner), self.assertRaises(LeaseLost):
            self.apply("png", preview=preview, guard=guard)
        self.assertEqual(len(calls), 3)
        self.assert_no_reconciliation()

    def test_later_metadata_finalization_preserves_every_prior_history_entry(self):
        first = self.apply("native")
        history = deepcopy(self.port.rows[first["history_key"]])
        previous_record = deepcopy(self.record("native"))
        self.drive.items[COPY]["version"] = "4"
        second = self.apply("native")
        self.assertNotEqual(first["history_key"], second["history_key"])
        self.assertEqual(self.port.rows[first["history_key"]], history)
        self.assertEqual(self.port.rows[second["history_key"]].value["before"], previous_record)
        self.assert_no_native_writes()

    def test_unknown_cas_is_not_retried_and_exact_lost_response_can_be_verified(self):
        for commit_first in (False, True):
            self.port.rows = deepcopy(self.before)
            commit, attempts = self.port.commit, []
            def uncertain(changes):
                if any(k.startswith(repair.HISTORY_PREFIX + "_") for k in changes):
                    attempts.append(changes)
                    if commit_first: commit(changes)
                    raise StoreUnavailable("lost response")
                return commit(changes)
            with patch.object(self.port, "commit", side_effect=uncertain):
                if commit_first:
                    self.assertEqual(self.apply("pdf")["status"], "verified")
                else:
                    with self.assertRaises(StoreUnavailable): self.apply("pdf")
                    self.assert_no_reconciliation()
            self.assertEqual(len(attempts), 1)
        self.assert_no_native_writes()

    def test_changed_post_save_destination_stops_with_prior_history_retained(self):
        commit = self.port.commit
        attempts = []
        def after_save(changes):
            result = commit(changes)
            if any(k.startswith(repair.HISTORY_PREFIX + "_") for k in changes):
                attempts.append(changes)
                self.drive.items[self.folder]["permissions"].append({"type": "anyone", "role": "reader"})
            return result
        with patch.object(self.port, "commit", side_effect=after_save), self.assertRaises(a.Held): self.apply("png")
        self.assertEqual(len(attempts), 1)
        history_key = self.record("png")["last_metadata_reconciliation"]
        self.assertEqual(self.port.rows[history_key].value["before"], self.before[self.records["png"]].value)
        self.assert_no_native_writes()
