"""Synthetic acceptance for private feedback history and uncertain-write fencing."""
from copy import deepcopy
import hashlib
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from hub_v2 import google, student_archive as archive, student_feedback_checklist as checklist
from school_workflow_hub.core.cloud_state import Conflict
from tests.test_cloud_state import MemoryCAS
from tests.test_student_archive import FakeDrive, config, saved, ROOT, COURSES


class StudentFeedbackChecklistTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        self.updates = []
        patches = [patch.object(archive, "configured_store", return_value=self.port),
            patch.object(google, "drive_file", side_effect=self.drive.metadata),
            patch.object(google, "folder_children", side_effect=self.drive.children),
            patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
            patch.object(google, "archive_create", side_effect=self.drive.create),
            patch.object(google, "rename_item", side_effect=self.drive.rename),
            patch.object(google, "move_item", side_effect=self.drive.move),
            patch.object(archive, "_work_bytes", side_effect=lambda token, meta, heartbeat=None: self.drive.contents[meta["id"]]),
            patch.object(checklist, "_conditional_update", side_effect=self.update)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        # Historical reports are already archived, independent of this derivative.
        with patch.object(checklist, "refresh_run", return_value={"status": "deferred"}):
            result = archive.file_feedback("read", "write", self.config, saved(), b"%PDF fictional feedback")
        self.assertEqual(result["status"], "verified", result)
        self.student_key = result["student_key"]
        self.student_folder = result["student_folder_id"]
        self.first_report = result["feedback_id"]
        self.originals = deepcopy(self.drive.items)

    def update(self, run, meta, raw):
        self.updates.append(meta["id"])
        item = self.drive.items[meta["id"]]
        item.update(md5Checksum=hashlib.md5(raw).hexdigest(), size=str(len(raw)), version=str(int(item["version"]) + 1))
        self.drive.contents[meta["id"]] = raw
        return 200

    def refresh(self, **kwargs):
        return checklist.refresh("read", "write", self.config, "biology", **kwargs)

    def record(self):
        return self.port.rows[archive.key("archivefeedbackchecklist", [ROOT, self.student_key])].value

    def second_report(self, report_type="science_writing"):
        # Seed an already verified saved version, with the same real archive contract.
        run = archive._Run("read", "write", self.config, "biology")
        student = archive._read(self.port, self.student_key).value
        ident = "feedbacksource_" + "c" * 64
        artifact = run.create(archive.key("archivefeedback", [ROOT, ident]), "Second feedback.pdf", self.student_folder,
            archive.PDF, pdf=b"%PDF second report", attributes={"kind": "feedback", "date": "2026-10-08",
                "saved_at": "2026-10-08T12:00:00Z", "feedback_id": ident, "report_type": report_type,
                "class_key": "biology", "student_key": self.student_key, "assignment": "Energy flow writing",
                "presentation_version": 1})
        run.attach(student, artifact)
        with patch.object(checklist, "refresh_run", return_value={"status": "deferred"}):
            run.finish({"status": "verified"})
        return artifact

    def test_one_stable_readable_checklist_with_dates_types_links_and_no_delivery_inference(self):
        first = self.refresh()
        self.assertEqual(first["status"], "verified", first)
        self.assertEqual(first["updated"], 1)
        record = self.record()
        first_id = record["drive_file_id"]
        self.assertEqual(record["parent"], self.student_folder)
        self.assertEqual(record["confirmed"]["rows"][0]["type"], "critical_aspects")
        created = len(self.drive.created)
        second = self.refresh()
        self.assertEqual(second["unchanged"], 1, second)
        self.assertEqual(len(self.drive.created), created)
        self.assertEqual(self.updates, [])
        self.assertEqual(first_id, self.record()["drive_file_id"])
        from pypdf import PdfReader
        pdf = PdfReader(BytesIO(self.drive.contents[first_id]))
        text = " ".join(" ".join(page.extract_text() for page in pdf.pages).split())
        self.assertIn("Critical aspects", text)
        self.assertIn("2026-10-07", text)
        self.assertIn("Energy Flow", text)
        self.assertIn("does not mean printed", text)
        self.assertTrue(any(page.get("/Annots") for page in pdf.pages))
        self.assertTrue(all(self.drive.items[ident] == meta for ident, meta in self.originals.items()))

    def test_new_feedback_type_appends_and_updates_same_file_preserving_old_history(self):
        self.refresh(); prior = deepcopy(self.record())
        self.second_report()
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        current = self.record()
        self.assertEqual(current["drive_file_id"], prior["drive_file_id"])
        self.assertEqual(self.updates, [current["drive_file_id"]])
        self.assertEqual([r["type_label"] for r in current["confirmed"]["rows"]], ["Critical aspects", "Science writing"])
        histories = [d.value for d in self.port.rows.values() if d.value.get("schema") == checklist.SCHEMA]
        self.assertEqual(sorted(len(h["rows"]) for h in histories), [1, 2])
        self.assertTrue(all(r["made"] and "printed" not in r and "handed" not in r for r in current["confirmed"]["rows"]))

    def test_multiple_reports_same_lesson_and_other_types_are_not_deduplicated(self):
        self.second_report("supportive_contract")
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(len(self.record()["confirmed"]["rows"]), 2)
        self.assertEqual(self.record()["confirmed"]["rows"][1]["type_label"], "Supportive contract")

    def test_every_historical_version_remains_listed(self):
        second = self.second_report()
        # The archive's existing verifier owns chain proof; ensure history is
        # retained after that proof, rather than applying current-only filtering.
        doc = self.port.rows[archive.key("archivefeedback", [ROOT, self.first_report])]
        doc.value.update(superseded_by=second["feedback_id"])
        with patch.object(archive, "_feedback_successor", return_value=second) as verify:
            result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(len(self.record()["confirmed"]["rows"]), 2)
        self.assertTrue(self.record()["confirmed"]["rows"][0]["historical"])
        self.assertTrue(verify.called)

    def test_partial_scan_or_lost_previous_item_never_erases_checklist(self):
        self.refresh(); before = deepcopy(self.record()); before_bytes = self.drive.contents[before["drive_file_id"]]
        owner = self.port.rows[self.student_key]
        report_key = next(iter(owner.value["items"]))
        original = self.port.rows.pop(report_key)
        held = self.refresh()
        self.assertEqual(held["status"], "held")
        self.assertEqual(self.drive.contents[before["drive_file_id"]], before_bytes)
        self.assertEqual(self.record(), before)
        self.port.rows[report_key] = original
        owner.value["items"].pop(report_key)
        self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(self.updates, [])

    def test_public_or_relocated_source_or_student_folder_blocks_write(self):
        for ident in (self.student_folder, ROOT):
            with self.subTest(ident=ident):
                original = deepcopy(self.drive.items[ident])
                self.drive.items[ident]["permissions"].append({"type": "anyone", "role": "reader"})
                result = self.refresh()
                self.assertEqual(result["status"], "held", result)
                self.drive.items[ident] = original
        self.assertFalse(any(f["name"] == checklist.NAME for f in self.drive.created))

    def test_unindexed_same_name_is_preserved_without_duplicate(self):
        self.drive.add("FictionalExistingChecklist", checklist.NAME, archive.PDF, [self.student_folder])
        before = len(self.drive.created)
        self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(len(self.drive.created), before)

    def test_uncertain_create_is_never_repeated(self):
        self.drive.fail_create = True
        self.assertEqual(self.refresh()["status"], "held")
        count = len(self.drive.created)
        self.drive.fail_create = False
        self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(len(self.drive.created), count)

    def test_lost_create_response_recovers_exact_file_without_second_create(self):
        self.drive.fail_after_create = True
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(len([f for f in self.drive.created if f["name"] == checklist.NAME]), 1)

    def test_uncertain_update_fences_retries_and_retains_last_verified_history(self):
        self.refresh(); prior = deepcopy(self.record()["confirmed"])
        self.second_report()
        with patch.object(checklist, "_conditional_update", return_value=None) as update:
            result = self.refresh()
            self.assertEqual(result["status"], "held")
            self.assertEqual(self.record()["confirmed"], prior)
            self.assertIsNotNone(self.record()["pending"])
            self.assertEqual(self.refresh()["status"], "held")
            self.assertEqual(update.call_count, 1)

    def test_lost_update_reply_is_reconciled_by_exact_content(self):
        self.refresh(); self.second_report()
        def lost(run, meta, raw):
            self.update(run, meta, raw)
            return None
        with patch.object(checklist, "_conditional_update", side_effect=lost):
            result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertIsNone(self.record()["pending"])
        self.assertEqual(len(self.updates), 1)

    def test_changed_archive_or_lost_lease_before_write_cannot_publish(self):
        actual = checklist._snapshot
        calls = [0]
        def changed(run, student_key):
            result = actual(run, student_key); calls[0] += 1
            if calls[0] == 2:
                return result[0], result[1], "changed"
            return result
        before = len(self.drive.created)
        with patch.object(checklist, "_snapshot", side_effect=changed):
            self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(len(self.drive.created), before)
        with patch.object(archive._Run, "renew", side_effect=Conflict("lease_lost")):
            self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(len(self.drive.created), before)

    def test_backfill_bound_and_unknown_student_cannot_create_folders(self):
        before = len(self.drive.created)
        result = self.refresh(student_keys=["unknown-%s" % i for i in range(26)])
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.refresh(student_keys=["unknown"])["status"], "held")
        self.assertEqual(len(self.drive.created), before)

    def test_future_archive_filing_runs_checklist_after_artifact_is_attached(self):
        value = saved(); value["id"] = "feedbackdraft_" + "d" * 64
        result = archive.file_feedback("read", "write", self.config, value, b"%PDF new feedback")
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["feedback_checklists"]["status"], "verified", result)
        self.assertEqual(self.record()["confirmed"]["rows"][1]["report_id"], value["id"])

    def test_conditional_preflight_refusal_is_not_an_uncertain_write(self):
        self.refresh(); self.second_report()
        with patch.object(checklist, "_conditional_update", side_effect=archive.Held("ETag unavailable")):
            result = self.refresh()
        self.assertEqual(result["status"], "held")
        self.assertIsNone(self.record()["pending"])
        self.assertEqual(self.refresh()["status"], "verified")

    def test_http_update_uses_current_etag_once_and_retains_revisions(self):
        # Test the actual network adapter separately from the in-memory updater.
        original = checklist._conditional_update.side_effect
        self.assertTrue(callable(original))
        run = Mock(token="read", write_token="write")
        meta = {"id": "FictionalChecklistFile"}
        session = Mock()
        session.get.return_value = Mock(status_code=200, headers={"ETag": '"revision-1"'})
        session.get.return_value.json.return_value = meta
        session.patch.return_value.status_code = 200
        with patch.object(google, "_session", return_value=session):
            status = REAL_UPDATE(run, meta, b"%PDF checklist")
        self.assertEqual(status, 200)
        self.assertEqual(session.patch.call_count, 1)
        self.assertEqual(session.patch.call_args.kwargs["headers"]["If-Match"], '"revision-1"')
        self.assertEqual(session.patch.call_args.kwargs["params"]["keepRevisionForever"], "true")
        session.get.return_value.headers = {}
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            REAL_UPDATE(run, meta, b"%PDF checklist")
        self.assertEqual(session.patch.call_count, 1)


REAL_UPDATE = checklist._conditional_update
