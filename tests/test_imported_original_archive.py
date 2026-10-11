"""Audited external originals use fictional files and disposable CAS only."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from hub_v2 import google, student_archive as archive
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_student_archive import FakeDrive, config, found

FILE = "FictionalImportedOriginal001"
RAW = b"%PDF-1.7\nFictional original attachment\n%%EOF"


def provenance(**updates):
    return {"provider": "schoology", "original_url": "https://fictional.schoology.com/assignment/12345/dropbox/view/23456",
        "assignment_id": "12345", "user_id": "23456", "revision": 1,
        "attachment_path": "Fictional Student/Revision 1/Original worksheet.pdf", "archive_sha256": "a" * 64,
        "source_receipt_sha256": "b" * 64, "identity_proof_sha256": "c" * 64,
        "submitted_local_date": "2026-09-16", "submitted_local_time": "17:22", "submitted_timezone": None,
        "worksheet_teacher_id": "FictionalWorksheetTeacher01", "worksheet_role": "Research Banner", **updates}


class ImportedOriginalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        patches = [patch.object(archive, "configured_store", return_value=self.port),
                   patch.object(google, "drive_file", side_effect=self.drive.metadata),
                   patch.object(google, "folder_children", side_effect=self.drive.children),
                   patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
                   patch.object(google, "archive_create", side_effect=self.drive.create),
                   patch.object(google, "rename_item", side_effect=self.drive.rename),
                   patch.object(google, "move_item", side_effect=self.drive.move),
                   patch.object(google, "get_json", side_effect=self.drive.copy),
                   patch.object(archive, "_work_content", side_effect=self.drive.content)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        self.assertEqual(archive.archive_work("read", "write", self.config, "biology", found())["status"], "verified")
        self.student_key = next(k for k, doc in self.port.rows.items()
            if doc.value.get("record_key") == k and doc.value.get("student_key") == k and doc.value.get("items"))
        self.folder_id = self.port.rows[self.student_key].value["drive_file_id"]
        self.drive.add(FILE, "Original recovered worksheet.pdf", archive.PDF, [self.folder_id])
        self.drive.items[FILE].update(md5Checksum=hashlib.md5(RAW).hexdigest(), size=str(len(RAW)))
        self.drive.contents[FILE] = RAW
        self.drive.created.clear(); self.drive.copied.clear(); self.drive.renamed.clear(); self.drive.moved.clear()
        self.baseline = deepcopy(self.port.rows)

    def kwargs(self, **updates):
        return {"provenance": provenance(), "original_bytes": RAW,
                "expected_metadata": self.drive.metadata("read", FILE),
                "expected_student": deepcopy(self.port.rows[self.student_key].value),
                "assignment": "Fictional Eyewitness — Research Banner",
                "source_context": {"class_key": "biology", "school_year": "2026-27", "week": "Wk 04"},
                "filing_note": "Authenticated original; filing time is separate from submission date.", **updates}

    def register(self, **updates):
        return archive.register_imported_original("read", self.config, "biology", self.student_key, FILE, **self.kwargs(**updates))

    def assert_no_Drive_writes(self):
        self.assertEqual(self.drive.created + self.drive.copied + self.drive.renamed + self.drive.moved, [])

    def test_existing_original_registers_atomically_and_future_index_keeps_source_and_date(self):
        prior_student = deepcopy(self.port.rows[self.student_key].value)
        result = self.register()
        artifact = self.port.rows[result["record_key"]].value
        self.assertEqual(result["filed"], 1)
        self.assertEqual(artifact["date"], "2026-09-16")
        self.assertNotEqual(artifact["registered_at"][:10], artifact["date"])
        self.assertTrue(set(prior_student["items"]) < set(self.port.rows[self.student_key].value["items"]))
        for field in ("source_file_id", "form_id", "assignment_form_id", "submitted_at", "source_response_id"):
            self.assertNotIn(field, artifact)
        rows = archive.work_index_rows("read", self.config)["rows"]
        imported = [r for r in rows if r[3] == artifact["assignment"]]
        self.assertEqual(len(imported), 1)
        row = imported[0]
        self.assertEqual(row[5], archive._sheet_date("2026-09-16"))
        self.assertEqual(row[6], "")
        self.assertIn(FILE, row[7])
        self.assertIn(provenance()["original_url"], row[8])
        self.assertEqual(row[9], "")
        self.assertEqual(row[11], "Original filed; worksheet feedback pending")
        self.assertEqual(row[12], artifact["filing_note"])
        self.assert_no_Drive_writes()

    def test_repeat_is_deduplicated_with_same_artifact_registration_time_and_no_commit(self):
        first = self.register(); prior = deepcopy(self.port.rows); sequence = self.port.sequence
        second = self.register()
        self.assertEqual(first["record_key"], second["record_key"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["filed"], 0)
        self.assertEqual(self.port.sequence, sequence)
        self.assertEqual(self.port.rows, prior)
        self.assert_no_Drive_writes()

    def test_existing_asset_cannot_replace_file_bytes_role_or_date(self):
        self.register()
        for updates in ({"provenance": provenance(worksheet_role="Another activity")},
                        {"provenance": provenance(submitted_local_date="2026-09-17")},
                        {"filing_note": "Changed provenance"}):
            with self.subTest(updates=updates), self.assertRaises(archive.Held): self.register(**updates)
        self.assert_no_Drive_writes()

    def test_same_attachment_cannot_be_registered_as_a_different_file(self):
        first = self.register(); artifact = deepcopy(self.port.rows[first["record_key"]].value)
        other = "FictionalImportedOriginal002"
        self.drive.items[other] = {**deepcopy(self.drive.items[FILE]), "id": other}
        with self.assertRaises(archive.Held):
            archive.register_imported_original("read", self.config, "biology", self.student_key, other,
                **self.kwargs(expected_metadata=self.drive.metadata("read", other)))
        self.assertEqual(self.port.rows[first["record_key"]].value, artifact)

    def test_one_file_cannot_claim_a_second_attachment_receipt(self):
        self.register()
        with self.assertRaises(archive.Held): self.register(provenance=provenance(attachment_path="Other/Original worksheet.pdf"))

    def test_distinct_files_in_one_authenticated_submission_remain_separate_originals(self):
        first = self.register()
        other = "FictionalImportedOriginal002"
        self.drive.items[other] = {**deepcopy(self.drive.items[FILE]), "id": other}
        second = archive.register_imported_original("read", self.config, "biology", self.student_key, other,
            **self.kwargs(expected_metadata=self.drive.metadata("read", other),
                          provenance=provenance(attachment_path="Fictional Student/Revision 1/Doodle notes.pdf", worksheet_role="Doodle Notes"),
                          assignment="Fictional Eyewitness — Doodle Notes"))
        self.assertNotEqual(first["record_key"], second["record_key"])
        self.assertEqual(len([r for r in archive.work_index_rows("read", self.config)["rows"] if "Fictional Eyewitness" in r[3]]), 2)
        self.assert_no_Drive_writes()

    def test_wrong_private_parent_public_permission_checksum_and_existing_tag_hold(self):
        for field, value in (("parents", ["ForeignStudentFolder0001"]),
                             ("permissions", [{"type": "anyone", "role": "reader"}]),
                             ("md5Checksum", "0" * 32), ("size", "1"),
                             ("properties", {archive.TAG: "ForeignArchiveRecord"})):
            original = deepcopy(self.drive.items[FILE]); self.drive.items[FILE][field] = value
            with self.subTest(field=field), self.assertRaises(archive.Held): self.register()
            self.drive.items[FILE] = original
        self.assertEqual(self.port.rows, self.baseline)
        self.assert_no_Drive_writes()

    def test_changed_expected_student_class_membership_or_full_metadata_cannot_register(self):
        wrong = deepcopy(self.port.rows[self.student_key].value); wrong["name"] = "Another student"
        with self.assertRaises(archive.Held): self.register(expected_student=wrong)
        meta = self.drive.metadata("read", FILE); meta["version"] = "0"
        with self.assertRaises(archive.Held): self.register(expected_metadata=meta)
        index_key = archive.key("archiveclass", [self.config[archive.ROOT_CONFIG], "biology"])
        self.port.rows[index_key].value["students"][self.student_key] = "ForeignStudentFolder0001"
        with self.assertRaises(archive.Held): self.register()
        self.assert_no_Drive_writes()

    def test_source_receipt_url_identity_revision_date_and_path_are_exact(self):
        bad = [{"original_url": "https://foreign.example/assignment/12345/dropbox/view/23456"},
               {"user_id": "99999"}, {"assignment_id": "99999"}, {"revision": True},
               {"submitted_local_date": "2026-02-30"}, {"submitted_local_time": "25:10"},
               {"attachment_path": "Student/../other.pdf"}, {"source_receipt_sha256": ""},
               {"identity_proof_sha256": ""}, {"submitted_timezone": "inferred from upload"}]
        for updates in bad:
            with self.subTest(updates=updates), self.assertRaises(archive.Held): self.register(provenance=provenance(**updates))
        wrong = provenance(); wrong["source_response_id"] = "PretendFormResponse"
        with self.assertRaises(archive.Held): self.register(provenance=wrong)
        self.assertEqual(self.port.rows, self.baseline)

    def test_mid_registration_file_or_destination_drift_stops_before_CAS(self):
        original = self.drive.metadata; seen = 0
        def changing(token, ident, fields=None):
            nonlocal seen
            if ident == FILE:
                seen += 1
                if seen == 2: self.drive.items[FILE]["version"] = "2"
            return original(token, ident, fields)
        with patch.object(google, "drive_file", side_effect=changing), self.assertRaises(archive.Held): self.register()
        self.assertEqual(self.port.rows, self.baseline)

    def test_CAS_conflict_preserves_competing_student_update_without_retry(self):
        commit = self.port.commit; calls = []
        def conflict(changes):
            calls.append(changes)
            owner = self.port.rows[self.student_key]
            commit({self.student_key: ({**owner.value, "teacher_fact": "Retain this"}, owner.version)})
            return commit(changes)
        with patch.object(self.port, "commit", side_effect=conflict), self.assertRaises(Conflict): self.register()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.port.rows[self.student_key].value["teacher_fact"], "Retain this")
        self.assertFalse(any(d.value.get("source_kind") == archive.IMPORTED_ORIGINAL for d in self.port.rows.values()))
        self.assert_no_Drive_writes()

    def test_lost_atomic_commit_response_requires_exact_readback_and_never_replays(self):
        self.port.fail_after_commit = True
        commit = self.port.commit
        with patch.object(self.port, "commit", wraps=commit) as writer:
            result = self.register()
        self.assertEqual(writer.call_count, 1)
        self.assertIn(result["record_key"], self.port.rows[self.student_key].value["items"])
        self.assert_no_Drive_writes()

    def test_unwritten_unknown_commit_cannot_claim_filing_or_retry(self):
        with patch.object(self.port, "commit", side_effect=StoreUnavailable("unknown")) as writer:
            with self.assertRaises(StoreUnavailable): self.register()
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.port.rows, self.baseline)

    def test_provider_drift_during_CAS_keeps_registered_history_but_holds_acceptance(self):
        commit = self.port.commit
        def drift(changes):
            commit(changes)
            self.drive.items[FILE]["version"] = "2"
        with patch.object(self.port, "commit", side_effect=drift) as writer, self.assertRaises(archive.Held): self.register()
        self.assertEqual(writer.call_count, 1)
        artifacts = [d.value for d in self.port.rows.values() if d.value.get("source_kind") == archive.IMPORTED_ORIGINAL]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["imported_file_revision"]["version"], "1")
        self.assert_no_Drive_writes()

    def test_future_refresh_holds_changed_file_revision_properties_or_private_access(self):
        result = self.register(); artifact = self.port.rows[result["record_key"]].value
        self.assertEqual(archive._verify_imported_original(self.drive.metadata("read", FILE), artifact)["id"], FILE)
        for field, value in (("version", "2"), ("properties", {"foreign": "property"}),
                             ("permissions", [{"type": "user", "role": "writer", "emailAddress": "new@school.example"}])):
            meta = self.drive.metadata("read", FILE); meta[field] = value
            with self.subTest(field=field), self.assertRaises(archive.Held): archive._verify_imported_original(meta, artifact)

    def test_actual_PNG_type_can_register_without_native_deck_or_feedback_claim(self):
        raw = b"\x89PNG\r\n\x1a\nFictional raster worksheet"
        self.drive.items[FILE].update(mimeType="image/png", md5Checksum=hashlib.md5(raw).hexdigest(), size=str(len(raw)))
        result = self.register(original_bytes=raw)
        self.assertEqual(self.port.rows[result["record_key"]].value["mime"], "image/png")
        self.assert_no_Drive_writes()

    def test_import_never_borrows_unrelated_student_feedback_even_without_source_id(self):
        result = self.register()
        student = deepcopy(self.port.rows[self.student_key].value)
        imported = deepcopy(self.port.rows[result["record_key"]].value)
        report = {"kind": "feedback", "record_key": "FictionalUnrelatedReport", "report_type": "science_writing",
                  "source_records": [{"source_file_id": None}], "date": "2026-10-09", "url": "https://drive.google.com/open?id=OtherFeedback"}
        with patch.object(archive, "_indexed", side_effect=lambda token, conf, cls, **kw: (None, [(student, [imported, report])] if cls == "biology" else [])):
            rows = archive.work_index_rows("read", self.config)["rows"]
        work_rows = [r for r in rows if r[3] == imported["assignment"]]
        self.assertEqual(len(work_rows), 1)
        self.assertEqual(work_rows[0][9], "")
        self.assertEqual(work_rows[0][11], "Original filed; worksheet feedback pending")


if __name__ == "__main__": unittest.main()
