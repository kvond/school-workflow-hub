"""Fictional named archives, disposable CAS and no provider operations."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import google, student_archive as a, named_archival_work as n
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_student_archive import FakeDrive, config, found, ROOT

ORIGINAL = "FictionalNativeNamedOriginal01"
COPY = "FictionalPrivateNamedCopy001"
CREATED = "2026-09-17T11:31:17.928Z"
CONTENT = {"basis": "native_presentation/v1", "sha256": "a" * 64, "slides": 24, "image_instances": 26}


def provenance(**updates):
    return {"provider": "named_drive_original_archive", "original_id": ORIGINAL,
            "original_created_at": CREATED, "canonical_identity_sha256": "b" * 64,
            "copy_reservation_sha256": "c" * 64, "private_copy_proof_sha256": "d" * 64, **updates}


class NamedArchive(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        patches = [patch.object(a, "configured_store", return_value=self.port),
            patch.object(google, "drive_file", side_effect=self.drive.metadata),
            patch.object(google, "folder_children", side_effect=self.drive.children),
            patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
            patch.object(google, "archive_create", side_effect=self.drive.create),
            patch.object(google, "rename_item", side_effect=self.drive.rename),
            patch.object(google, "move_item", side_effect=self.drive.move),
            patch.object(google, "get_json", side_effect=self.drive.copy),
            patch.object(a, "_work_content", side_effect=self.drive.content)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        self.assertEqual(a.archive_work("read", "write", self.config, "biology", found())["status"], "verified")
        self.student_key = next(k for k, doc in self.port.rows.items() if doc.value.get("student_key") == k and doc.value.get("items"))
        self.student = deepcopy(self.port.rows[self.student_key].value)
        self.folder_id = self.student["drive_file_id"]
        self.drive.add(ORIGINAL, "Full student school email and name — original", n.MIME, ["FictionalOriginalFolder001"])
        self.drive.items[ORIGINAL]["createdTime"] = CREATED
        # Original public sharing is read and preserved; only the copy must be private.
        self.drive.items[ORIGINAL]["permissions"] = [{"type": "anyone", "role": "reader"}]
        self.drive.add(COPY, "01 — Lab Safety — full canonical student — archived original created 2026-09-17", n.MIME, [self.folder_id])
        self.native = patch.object(a, "_native_presentation_proof", return_value=deepcopy(CONTENT)).start()
        self.addCleanup(patch.stopall)
        self.drive.created.clear(); self.drive.copied.clear(); self.drive.renamed.clear(); self.drive.moved.clear()
        self.before = deepcopy(self.port.rows)

    def kwargs(self, **updates):
        return {"archive_provenance": provenance(), "expected_original": self.drive.metadata("read", ORIGINAL),
            "expected_metadata": self.drive.metadata("read", COPY), "expected_student": deepcopy(self.port.rows[self.student_key].value),
            "expected_content_proof": deepcopy(CONTENT), "assignment": "Lab Safety — named archival copy",
            "source_context": {"class_key": "biology", "school_year": "2026-27"},
            "filing_note": "Original file created 2026-09-17; private archival copy filed separately. Submission receipt unavailable; not grade eligible.", **updates}

    def register(self, **updates):
        return n.register("read", self.config, "biology", self.student_key, COPY, **self.kwargs(**updates))

    def no_drive_writes(self):
        self.assertEqual(self.drive.created + self.drive.copied + self.drive.renamed + self.drive.moved, [])

    def test_existing_private_copy_has_durable_blank_submission_index_row_and_no_grade_claim(self):
        prior = a.work_index_rows("read", self.config)["rows"]
        result = self.register(); artifact = self.port.rows[result["record_key"]].value
        self.assertIsNone(artifact["date"]); self.assertFalse(artifact["grade_eligible"])
        self.assertEqual(artifact["source_created_at"], CREATED)
        self.assertEqual(artifact["date_basis"], "original_file_created_time")
        for name in ("form_id", "assignment_form_id", "submitted_at", "source_response_id"):
            self.assertIsNone(artifact[name])
        rows = a.work_index_rows("read", self.config)["rows"]
        new = next(r for r in rows if r[3] == artifact["assignment"])
        self.assertEqual(new[5:7], ["", ""]); self.assertIn(COPY, new[7]); self.assertIn(ORIGINAL, new[8])
        self.assertEqual(new[9], ""); self.assertEqual(new[11], n.STATUS)
        self.assertIn("created 2026-09-17", new[12]); self.assertEqual([r for r in rows if r[13] != new[13]], prior)
        # Future generated refresh retains the same row key rather than dropping an ad hoc native row.
        self.assertEqual(a.work_index_rows("read", self.config)["rows"], rows)
        self.no_drive_writes()

    def test_repeat_reuses_immutable_original_key_registration_time_and_no_commit(self):
        first = self.register(); sequence = self.port.sequence; previous = deepcopy(self.port.rows)
        second = self.register()
        self.assertEqual(first["record_key"], second["record_key"]); self.assertTrue(second["reused"])
        self.assertEqual(second["filed"], 0); self.assertEqual(self.port.sequence, sequence); self.assertEqual(self.port.rows, previous)
        self.no_drive_writes()

    def test_second_copy_same_original_or_changed_provenance_cannot_replace_registered_asset(self):
        first = self.register(); previous = deepcopy(self.port.rows)
        other = "FictionalPrivateNamedCopy002"; self.drive.items[other] = {**deepcopy(self.drive.items[COPY]), "id": other}
        with self.assertRaises(a.Held): n.register("read", self.config, "biology", self.student_key, other, **self.kwargs(expected_metadata=self.drive.metadata("read", other)))
        with self.assertRaises(a.Held): self.register(archive_provenance=provenance(canonical_identity_sha256="e"*64))
        self.assertEqual(self.port.rows, previous); self.assertIn(first["record_key"], self.port.rows[self.student_key].value["items"])
        self.no_drive_writes()

    def test_source_date_must_be_exact_original_creation_not_an_invented_submission_event(self):
        for p in (provenance(original_created_at="2026-10-11T00:00:00Z"), provenance(original_created_at="2026-09-17"),
                  {**provenance(), "submitted_at": CREATED}, provenance(private_copy_proof_sha256="")):
            with self.subTest(p=p), self.assertRaises(a.Held): self.register(archive_provenance=p)
        self.assertEqual(self.port.rows, self.before); self.no_drive_writes()

    def test_full_student_membership_metadata_copy_type_and_private_access_are_strict(self):
        for field, value in (("parents", ["OtherStudentFolder001"]), ("mimeType", google.PPTX),
            ("permissions", [{"type": "anyone", "role": "reader"}]), ("properties", {a.TAG: "other"})):
            old = deepcopy(self.drive.items[COPY]); self.drive.items[COPY][field] = value
            with self.subTest(field=field), self.assertRaises(a.Held): self.register()
            self.drive.items[COPY] = old
        student = deepcopy(self.student); student["name"] = "Other person"
        with self.assertRaises(a.Held): self.register(expected_student=student)
        metadata = self.drive.metadata("read", ORIGINAL); metadata["version"] = "old"
        with self.assertRaises(a.Held): self.register(expected_original=metadata)
        self.assertEqual(self.port.rows, self.before); self.no_drive_writes()

    def test_full_native_scene_or_image_change_blocks_before_CAS(self):
        for changed in ({**CONTENT, "sha256": "e"*64}, {**CONTENT, "slides": 23}, {**CONTENT, "image_instances": 25}):
            with patch.object(a, "_native_presentation_proof", side_effect=[CONTENT, changed]), self.assertRaises(a.Held): self.register()
        with self.assertRaises(a.Held): self.register(expected_content_proof={**CONTENT, "sha256":"e"*64})
        self.assertEqual(self.port.rows, self.before); self.no_drive_writes()

    def test_original_or_copy_metadata_drift_before_commit_preserves_all_existing_records(self):
        original = self.drive.metadata; seen = 0
        def change(token, ident, fields=None):
            nonlocal seen
            if ident == COPY:
                seen += 1
                if seen == 2: self.drive.items[COPY]["version"] = "2"
            return original(token, ident, fields)
        with patch.object(google, "drive_file", side_effect=change), self.assertRaises(a.Held): self.register()
        self.assertEqual(self.port.rows, self.before); self.no_drive_writes()

    def test_lost_commit_is_exact_GET_readback_with_one_attempt_and_unknown_never_replays(self):
        self.port.fail_after_commit = True
        with patch.object(self.port, "commit", wraps=self.port.commit) as commit:
            self.assertEqual(self.register()["status"], "verified"); self.assertEqual(commit.call_count, 1)
        self.no_drive_writes()

    def test_unwritten_unknown_or_concurrent_student_update_cannot_claim_registration(self):
        with patch.object(self.port, "commit", side_effect=StoreUnavailable("unknown")) as commit:
            with self.assertRaises(StoreUnavailable): self.register()
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(self.port.rows, self.before)
        commit = self.port.commit; calls = []
        def race(changes):
            calls.append(changes); owner = self.port.rows[self.student_key]
            commit({self.student_key: ({**owner.value, "teacher_fact": "preserved"}, owner.version)})
            return commit(changes)
        with patch.object(self.port, "commit", side_effect=race), self.assertRaises(Conflict): self.register()
        self.assertEqual(len(calls), 1); self.assertEqual(self.port.rows[self.student_key].value["teacher_fact"], "preserved")
        self.no_drive_writes()

    def test_future_listing_refuses_changed_private_revision_permissions_and_grade_claim(self):
        result = self.register(); record = self.port.rows[result["record_key"]].value
        for field, value in (("version", "2"), ("permissions", [{"type":"user","emailAddress":"new@example.com","role":"writer"}])):
            metadata = self.drive.metadata("read", COPY); metadata[field] = value
            with self.assertRaises(a.Held): n.verify(metadata, record)
        for field, value in (("submitted_at", CREATED), ("grade_eligible", True), ("date", "2026-09-17"), ("assignment_form_id", "PretendForm")):
            altered = deepcopy(record); altered[field] = value
            with self.assertRaises(a.Held): n.verify(self.drive.metadata("read", COPY), altered)
        self.no_drive_writes()

    def test_archive_listing_keeps_no_submission_date_with_explicit_original_creation_label(self):
        result = self.register(); listing = a.archive_listing("read", self.config, "biology")
        record = next(r for s in listing["students"] for r in s["records"] if r["drive_file_id"] == COPY)
        self.assertIsNone(record["date"]); self.assertEqual(record["date_label"], "Original file created 2026-09-17")
        self.assertFalse(record["grade_eligible"]); self.no_drive_writes()

    def test_unrelated_feedback_cannot_borrow_named_archive_even_matching_original_id(self):
        result = self.register(); record = self.port.rows[result["record_key"]].value
        report = {"kind":"feedback", "record_key":"FictionalReport001", "report_type":"science_writing", "source_records":[{"source_file_id":ORIGINAL,"source_version":None}], "date":"2026-10-11", "url":"https://drive.google.com/open?id=FictionalPDF"}
        student = self.port.rows[self.student_key].value
        with patch.object(a, "_indexed", side_effect=lambda token, conf, cls, **kw:(None,[(student,[record,report])] if cls=="biology" else [])):
            row = next(r for r in a.work_index_rows("read", self.config)["rows"] if r[3]==record["assignment"])
        self.assertEqual(row[9], ""); self.assertEqual(row[11], n.STATUS); self.no_drive_writes()

    def test_refresh_adds_archival_context_once_and_retains_existing_and_edited_teacher_notes(self):
        self.register(); data = a.work_index_rows("read", self.config)
        ident = "FictionalWorkIndexSheet001"; self.config[a.WORK_INDEX_CONFIG] = "https://docs.google.com/spreadsheets/d/%s/edit" % ident
        self.drive.add(ident, "Student work and feedback", "application/vnd.google-apps.spreadsheet", [ROOT])["properties"]["hubStudentWorkIndex"] = ROOT
        archive_row = next(r for r in data["rows"] if r[11]==n.STATUS); old_rows = [r for r in data["rows"] if r[13]!=archive_row[13]]
        headers={"values":[a._index_cell(v) for v in a.INDEX_HEADERS]}
        state={"rows":[headers]+[{"values":[a._index_cell(v)for v in r]}for r in old_rows],"writes":0}
        state["rows"][1]["values"][12]=a._index_cell("Teacher's existing note")
        def api(url,token,params=None,timeout=20,method="GET",body=None):
            if method=="POST":
                state["writes"]+=1
                for request in body["requests"]:
                    if "updateCells" not in request:continue
                    update=request["updateCells"];start=update["range"]["startRowIndex"];end=update["range"]["endRowIndex"]
                    state["rows"] += [{"values":[]} for _ in range(max(0,end-len(state["rows"])))]
                    state["rows"][start:end]=deepcopy(update["rows"])
                return {}
            return {"spreadsheetId":ident,"sheets":[{"properties":{"title":"Records","sheetId":17,"gridProperties":{"rowCount":200,"columnCount":14}},"data":[{"rowData":deepcopy(state["rows"])}]}]}
        with patch.object(google,"get_json",side_effect=api), patch.object(a,"work_index_rows",return_value=data):
            first=a.refresh_work_index("read","write",self.config);self.assertEqual(first["status"],"verified")
            row=next(r for r in state["rows"][1:]if r["values"][13]["userEnteredValue"]["stringValue"]==archive_row[13])
            self.assertEqual(row["values"][12]["userEnteredValue"]["stringValue"],archive_row[12]);self.assertEqual(row["values"][5].get("userEnteredValue",{}),{})
            row["values"][12]=a._index_cell("Teacher updated archival context")
            self.assertFalse(a.refresh_work_index("read","write",self.config)["changed"])
            self.assertEqual(row["values"][12]["userEnteredValue"]["stringValue"],"Teacher updated archival context")
            self.assertTrue(any(r["values"][12]["userEnteredValue"].get("stringValue")=="Teacher's existing note"for r in state["rows"][1:]))
            self.assertEqual(state["writes"],1)


if __name__ == "__main__": unittest.main()
