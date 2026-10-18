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

    def test_display_only_omits_unsupported_lesson_decoration_and_preserves_supported_text(self):
        supported = "José Muñoz — β → ✔ & <lesson>"
        self.assertEqual(checklist.display_text(supported), supported)
        self.assertEqual(checklist.display_text(supported, decorative=True), supported)
        self.assertEqual(checklist.display_text("📤 Energy flow 👩‍🔬", decorative=True), "Energy flow")
        self.assertEqual(checklist.display_text("⚖️ Evidence", decorative=True), "Evidence")
        # Unknown spelling is never guessed or erased, including in a title.
        self.assertEqual(checklist.display_text("李 Ж"), "[U+674E] [U+0416]")
        self.assertEqual(checklist.display_text("李", decorative=True), "[U+674E]")

    def test_display_repair_keeps_original_rows_stable_file_history_and_link_targets(self):
        from pypdf import PdfReader
        legacy = self.legacy_report()
        self.port.rows[legacy["record_key"]].value["assignment"] = "📤 Energy flow & trophic pyramids"
        # Reproduce the previous renderer, then refresh only its presentation.
        with patch.object(checklist, "display_text", side_effect=lambda value, **kwargs: value):
            old = self.refresh()
        self.assertEqual(old["status"], "verified", old)
        record = deepcopy(self.record())
        file_id, rows = record["drive_file_id"], deepcopy(record["confirmed"]["rows"])
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.record()["drive_file_id"], file_id)
        self.assertEqual(self.record()["confirmed"]["rows"], rows)
        self.assertNotEqual(self.record()["confirmed"]["sha256"], record["confirmed"]["sha256"])
        histories = [doc.value for doc in self.port.rows.values()
                     if doc.value.get("drive_file_id") == file_id and "rows" in doc.value]
        self.assertEqual(len(histories), 2)
        pdf = PdfReader(BytesIO(self.drive.contents[file_id]))
        text = " ".join(page.extract_text() for page in pdf.pages)
        self.assertIn("Energy flow & trophic pyramids", text)
        self.assertNotIn("■", text)
        links = [annotation.get_object()["/A"]["/URI"] for page in pdf.pages
                 for annotation in page.get("/Annots", [])]
        self.assertEqual(links, [row["url"] for row in rows])
        self.assertEqual(self.refresh()["unchanged"], 1)

    def test_already_readable_render_bytes_unchanged_and_non_latin_fallback_visible(self):
        from pypdf import PdfReader
        student = {"name": "José Muñoz"}
        rows = [{"date": "2026-10-18", "type_label": "Critical aspects", "version": 1,
                 "historical": False, "lesson": "Énergie — β →", "url": "https://example.invalid/report"}]
        original = deepcopy((student, rows))
        with patch.object(checklist, "display_text", side_effect=lambda value, **kwargs: value):
            old = checklist.render(student, rows)
        self.assertEqual(checklist.render(student, rows), old)
        student["name"] = "李 Ж"
        pdf = PdfReader(BytesIO(checklist.render(student, rows)))
        self.assertIn("[U+674E] [U+0416]", pdf.pages[0].extract_text())
        self.assertEqual(student["name"], "李 Ж")
        self.assertEqual(rows, original[1])

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

    def legacy_report(self, *, native_document=False, docx_document=False):
        run = archive._Run("read", "write", self.config, "biology")
        student = archive._read(self.port, self.student_key).value
        feedback_id = "feedbacksource_" + "e" * 64
        source_id, revision, source_hash = "FictionalLegacySource001", "7", "f" * 64
        ranges = ({"docx_paragraph_indices": [0, 28], "pdf_pages_1_based": None} if docx_document else
                  {"native_doc_indices": [10, 250], "pdf_pages_1_based": None} if native_document else
                  {"pdf_pages_1_based": [2, 3]})
        ident = archive.key("archivelegacyfeedback", [ROOT, "biology", source_id, revision, source_hash, ranges, feedback_id])
        artifact = run.create(ident, "Fictional legacy excerpt.pdf", self.student_folder, archive.PDF,
            pdf=b"%PDF already audited legacy excerpt", attributes={"kind": "feedback", "date": "2026-09-18",
                "saved_at": "2026-09-18T13:00:00Z", "date_basis": "file_created", "class_key": "biology",
                "student_key": self.student_key, "assignment": "Fictional earlier lesson", "feedback_id": feedback_id,
                "feedback_origin": "legacy_packet", "source_kind": "legacy_student_excerpt", "source_document_id": source_id,
                "source_file_id": source_id, "source_revision": revision, "source_sha256": source_hash,
                "source_span": ranges, "source_ranges": ranges, "source_alias_ids": [],
                "identity_status": "identity_unverified", "identity_basis": "Previously audited unique roster label"})
        run.attach(student, artifact)
        with patch.object(checklist, "refresh_run", return_value={"status": "deferred"}):
            run.finish({"status": "verified"})
        return artifact

    def test_mixed_native_and_legacy_history_retains_real_type_and_date_evidence(self):
        legacy = self.legacy_report()
        self.second_report()
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        rows = self.record()["confirmed"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["type_label"] for r in rows],
                         ["Feedback (type not recorded)", "Critical aspects", "Science writing"])
        self.assertEqual(rows[0]["date"], "2026-09-18")
        self.assertEqual(rows[0]["date_basis"], "file_created")
        self.assertEqual(rows[0]["report_id"], legacy["feedback_id"])
        self.assertEqual(rows[0]["url"], archive._url(legacy["drive_file_id"]))
        from pypdf import PdfReader
        text = " ".join(" ".join(p.extract_text() for p in PdfReader(BytesIO(
            self.drive.contents[self.record()["drive_file_id"]])).pages).split())
        self.assertIn("Source file date: 2026-09-18", text)
        self.assertIn("Feedback (type not recorded)", text)
        before = len(self.drive.created)
        self.assertEqual(self.refresh()["unchanged"], 1)
        self.assertEqual(len(self.drive.created), before)

    def test_legacy_native_document_span_uses_exact_import_recipe(self):
        self.legacy_report(native_document=True)
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(len(self.record()["confirmed"]["rows"]), 2)

    def test_legacy_docx_span_accepts_zero_start_and_exclusive_end(self):
        artifact = self.legacy_report(docx_document=True)
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        run = Mock(root=ROOT); run.cls = "biology"
        for indices in ([0, 0], [-1, 28], [28, 0], [0], [False, 28]):
            bad = deepcopy(artifact)
            bad["source_ranges"] = bad["source_span"] = {"docx_paragraph_indices": indices, "pdf_pages_1_based": None}
            bad["record_key"] = archive.key("archivelegacyfeedback", [ROOT, "biology", bad["source_document_id"],
                bad["source_revision"], bad["source_sha256"], bad["source_ranges"], bad["feedback_id"]])
            with self.subTest(indices=indices), self.assertRaises(archive.Held):
                checklist._legacy_feedback_identity(run, bad)

    def selected_legacy_report(self, *, joint=False):
        cls = "forensics" if joint else "biology"
        run = archive._Run("read", "write", self.config, cls)
        if joint:
            identity = {**saved()["archive_identity"], "section": "A2 Forensics"}
            student = run.student(archive._identity(self.config, cls, identity))
        else:
            student = archive._read(self.port, self.student_key).value
        source, raw, stamp = "FictionalSelectedSource001", b"%PDF audited selected report", "2026-09-20T12:00:00Z"
        digest = hashlib.sha256(raw).hexdigest()
        fid = (archive.key("feedbacksource", [source, digest, stamp]) if joint else "feedbacksource_" + "2" * 64)
        ranges = ({"pages_1_based": [1, 2], "joint": True} if joint else
                  {"pages_1_based": [3, 4], "native_start_index": 12, "native_end_index": 250})
        attributes = {"kind": "feedback", "class_key": cls, "student_key": student["student_key"],
            "feedback_id": fid, "date": "2026-09-20", "saved_at": stamp, "date_basis": "file_created",
            "source_document_id": source, "source_file_id": source, "source_revision": "11", "source_sha256": digest,
            "source_ranges": ranges, "source_span": ranges, "identity_status": "identity_unverified",
            "identity_basis": "Previously audited student span", "feedback_origin": "legacy_packet",
            "source_kind": "audited_legacy_student_copy"}
        if joint:
            attributes.update(shared_source_key=archive.key("sharedfeedbacksource", [source, digest, stamp]),
                shared_source_document_id=source, shared_pdf_sha256=digest,
                covered_by_packet_manifest="feedbackpacketmanifest_" + "3" * 64,
                covered_by_packet_id="FictionalPrivatePacket001", teacher_joint_attribution=True)
        ident = archive.key("archiveselectedfeedback", [ROOT, cls, source, digest, student["student_key"], fid])
        artifact = run.create(ident, "Audited selected feedback.pdf", student["drive_file_id"], archive.PDF,
            pdf=raw, attributes=attributes)
        run.attach(student, artifact)
        with patch.object(checklist, "refresh_run", return_value={"status": "deferred"}):
            run.finish({"status": "verified"})
        return artifact, student

    def test_selected_native_copy_uses_its_original_student_bound_key(self):
        artifact, student = self.selected_legacy_report()
        result = self.refresh()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(len(self.record()["confirmed"]["rows"]), 2)
        self.assertTrue(any(r["record_key"] == artifact["record_key"] for r in self.record()["confirmed"]["rows"]))
        self.port.rows[self.student_key].value["items"].pop(artifact["record_key"])
        held = self.refresh()
        self.assertEqual(held["status"], "held")
        self.assertIn("unattached feedback", held["held"][0]["problem"])

    def test_teacher_attributed_joint_copy_checks_all_recorded_source_proofs(self):
        artifact, student = self.selected_legacy_report(joint=True)
        result = checklist.refresh("read", "write", self.config, "forensics", student_keys=[student["student_key"]])
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["checklists"][0]["reports"], 1)
        run = Mock(root=ROOT); run.cls = "forensics"
        changes = [{"teacher_joint_attribution": False}, {"shared_source_document_id": "DifferentSource"},
            {"shared_source_key": "sharedfeedbacksource_" + "4" * 64}, {"shared_pdf_sha256": "4" * 64},
            {"covered_by_packet_manifest": "missing"}, {"feedback_id": "feedbacksource_" + "4" * 64},
            {"student_key": "different-student"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(archive.Held):
                checklist._legacy_feedback_identity(run, {**artifact, **change})
        # The original selected-copy recipe binds each student's own history;
        # it does not globally deduplicate a teacher-attributed shared report.
        second = {**artifact, "student_key": "FictionalSecondStudent"}
        second["record_key"] = archive.key("archiveselectedfeedback", [ROOT, "forensics", second["source_document_id"],
            second["source_sha256"], second["student_key"], second["feedback_id"]])
        checklist._legacy_feedback_identity(run, second)
        self.assertNotEqual(second["record_key"], artifact["record_key"])

    def test_legacy_source_identity_span_and_student_corruption_never_publish(self):
        legacy = self.legacy_report()
        snapshot = deepcopy(self.port.rows); drive = deepcopy(self.drive.items)
        mutations = [lambda row: row.update(source_revision="8"),
            lambda row: row.update(source_sha256="1" * 64),
            lambda row: row.update(source_file_id="FictionalOtherSource001"),
            lambda row: row.update(source_document_id="FictionalOtherSource001"),
            lambda row: row.update(source_span={"pdf_pages_1_based": [4]}),
            lambda row: row.update(source_ranges={"pdf_pages_1_based": [4]}, source_span={"pdf_pages_1_based": [4]}),
            lambda row: row.update(feedback_id="feedbacksource_" + "1" * 64),
            lambda row: row.update(student_key="AnotherStudentKey"),
            lambda row: row.update(class_key="forensics"),
            lambda row: row.update(feedback_origin="native_saved"),
            lambda row: row.update(date_basis="unknown"),
            lambda row: row.update(identity_basis=""),
            lambda row: row.update(source_kind="unverified_excerpt")]
        before = len(self.drive.created)
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.port.rows = deepcopy(snapshot); self.drive.items = deepcopy(drive)
                mutate(self.port.rows[legacy["record_key"]].value)
                result = self.refresh()
                self.assertEqual(result["status"], "held", result)
                self.assertEqual(len(self.drive.created), before)

    def test_invalid_legacy_ranges_hold_even_with_recomputed_key(self):
        legacy = self.legacy_report()
        run = Mock(root=ROOT); run.cls = "biology"
        for ranges in ({"pdf_pages_1_based": []}, {"pdf_pages_1_based": [True]},
                       {"pdf_pages_1_based": [3, 2]}, {"pdf_pages_1_based": [0]},
                       {"native_doc_indices": [100, 50]}, {"native_doc_indices": [10]},
                       {"native_doc_indices": [1, 5], "unknown_span": [2]}):
            row = {**legacy, "source_span": ranges, "source_ranges": ranges}
            row["record_key"] = archive.key("archivelegacyfeedback", [ROOT, "biology", row["source_document_id"],
                row["source_revision"], row["source_sha256"], ranges, row["feedback_id"]])
            with self.subTest(ranges=ranges), self.assertRaises(archive.Held):
                checklist._legacy_feedback_identity(run, row)

    def test_unattached_legacy_pdf_or_changed_archive_tag_cannot_disappear_from_history(self):
        legacy = self.legacy_report()
        snapshot = deepcopy(self.port.rows)
        self.port.rows[self.student_key].value["items"].pop(legacy["record_key"])
        result = self.refresh()
        self.assertEqual(result["status"], "held", result)
        self.assertIn("unattached feedback", result["held"][0]["problem"])
        self.port.rows = snapshot
        self.drive.items[legacy["drive_file_id"]]["properties"][archive.TAG] = "changed"
        self.assertEqual(self.refresh()["status"], "held")
        self.assertFalse(any(f["name"] == checklist.NAME for f in self.drive.created))

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

    def network_update_fixture(self, *, pinned=True):
        self.refresh()
        meta = self.drive.metadata("read", self.record()["drive_file_id"])
        state = {"pinned": pinned, "head": "FictionalRevision1"}
        session = Mock()

        def get(url, **kwargs):
            current = self.drive.metadata("read", meta["id"])
            if "/revisions/" in url:
                value = {"id": state["head"], "etag": '"revision-%s"' % state["pinned"],
                    "mimeType": current["mimeType"], "pinned": state["pinned"],
                    "md5Checksum": current["md5Checksum"], "fileSize": current["size"]}
            else:
                self.assertIn("/drive/v2/files/", url)
                value = {"id": current["id"], "etag": '"file-%s"' % current["version"],
                    "version": current["version"], "headRevisionId": state["head"],
                    "md5Checksum": current["md5Checksum"], "fileSize": current["size"],
                    "mimeType": current["mimeType"], "labels": {"trashed": current["trashed"]},
                    "parents": [{"id": parent} for parent in current["parents"]], "title": current["name"]}
                # Real Shared Drive metadata omits the permissions enriched by
                # google.drive_file. No v3 ETag header is available or required.
                self.assertNotIn("permissions", value)
            response = Mock(status_code=200, headers={"ETag": value["etag"]})
            response.json.return_value = value
            return response

        def pin(url, **kwargs):
            self.assertIn("/revisions/", url)
            state["pinned"] = True
            self.drive.items[meta["id"]]["version"] = str(int(self.drive.items[meta["id"]]["version"]) + 1)
            return Mock(status_code=200)

        session.get.side_effect = get
        session.patch.side_effect = pin
        session.put.return_value.status_code = 200
        return meta, session, state

    def test_http_update_uses_bound_v2_etag_and_keeps_pinned_revision_without_duplicate_pin(self):
        meta, session, _ = self.network_update_fixture()
        run = Mock(token="read", write_token="write")
        with patch.object(google, "_session", return_value=session):
            status = REAL_UPDATE(run, meta, b"%PDF checklist")
        self.assertEqual(status, 200)
        session.patch.assert_not_called()
        self.assertEqual(session.put.call_count, 1)
        self.assertIn("/upload/drive/v2/files/", session.put.call_args.args[0])
        args = session.put.call_args.kwargs
        self.assertEqual(args["headers"]["If-Match"], '"file-%s"' % meta["version"])
        self.assertEqual(args["params"], {"uploadType": "media", "supportsAllDrives": "true",
                                        "newRevision": "true", "pinned": "true"})
        self.assertEqual(args["data"], b"%PDF checklist")

    def test_unpinned_prior_revision_is_pinned_read_back_and_file_validator_rebound(self):
        meta, session, state = self.network_update_fixture(pinned=False)
        run = Mock(token="read", write_token="write")
        with patch.object(google, "_session", return_value=session):
            self.assertEqual(REAL_UPDATE(run, meta, b"%PDF checklist"), 200)
        self.assertTrue(state["pinned"])
        self.assertEqual(session.patch.call_count, 1)
        self.assertEqual(session.patch.call_args.kwargs["json"], {"pinned": True})
        self.assertEqual(session.patch.call_args.kwargs["headers"]["If-Match"], '"revision-False"')
        self.assertEqual(session.put.call_args.kwargs["headers"]["If-Match"], '"file-%s"' % (int(meta["version"]) + 1))
        self.assertEqual(session.put.call_count, 1)

    def test_changed_or_missing_file_revision_and_weak_validators_refuse_before_writes(self):
        for change in ({"etag": None}, {"etag": 'W/"weak"'}, {"etag": "*"}, {"version": "999"},
                       {"md5Checksum": "f" * 32}, {"fileSize": "1"}, {"id": "OtherFile"},
                       {"parents": [{"id": "OtherParent"}]}, {"headRevisionId": None}):
            with self.subTest(change=change):
                meta, session, _ = self.network_update_fixture()
                real_get = session.get.side_effect
                def changed(url, **kwargs):
                    response = real_get(url, **kwargs)
                    if "/revisions/" not in url:
                        response.json.return_value.update(change)
                        response.headers = {}
                    return response
                session.get.side_effect = changed
                with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
                    REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist")
                session.patch.assert_not_called(); session.put.assert_not_called()

    def test_changed_shared_drive_acl_or_revision_refuses_before_preservation(self):
        for scope in ("acl", "revision"):
            with self.subTest(scope=scope):
                meta, session, _ = self.network_update_fixture(pinned=False)
                real_get = session.get.side_effect
                def changed(url, **kwargs):
                    response = real_get(url, **kwargs)
                    if scope == "acl":
                        self.drive.items[meta["id"]]["permissions"] = [{"type": "anyone"}]
                    elif "/revisions/" in url:
                        response.json.return_value["md5Checksum"] = "a" * 32
                    return response
                session.get.side_effect = changed
                with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
                    REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist")
                session.patch.assert_not_called(); session.put.assert_not_called()
                self.drive.items[meta["id"]] = meta

    def test_lost_pin_reply_reconciles_only_by_exact_revision_readback(self):
        meta, session, _ = self.network_update_fixture(pinned=False)
        pin = session.patch.side_effect
        def lost(*args, **kwargs):
            pin(*args, **kwargs)
            raise TimeoutError("reply lost")
        session.patch.side_effect = lost
        with patch.object(google, "_session", return_value=session):
            self.assertEqual(REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist"), 200)
        self.assertEqual(session.patch.call_count, 1)
        self.assertEqual(session.put.call_count, 1)

    def test_real_conditional_adapter_completes_stable_file_refresh_and_exact_readback(self):
        meta, session, state = self.network_update_fixture(pinned=False)
        prior = deepcopy(self.record())
        self.second_report()
        def put(url, **kwargs):
            self.assertTrue(state["pinned"])
            self.update(None, meta, kwargs["data"])
            state["head"] = "FictionalRevision2"
            return Mock(status_code=200)
        session.put.side_effect = put
        with patch.object(google, "_session", return_value=session), patch.object(checklist, "_conditional_update", side_effect=REAL_UPDATE):
            result = self.refresh()
            self.assertEqual(result["status"], "verified", result)
            self.assertEqual(result["updated"], 1)
            self.assertEqual(self.refresh()["unchanged"], 1)
        self.assertIsNone(self.record()["pending"])
        self.assertEqual(self.record()["drive_file_id"], prior["drive_file_id"])
        self.assertEqual(len(self.record()["confirmed"]["rows"]), 2)
        self.assertEqual(session.patch.call_count, 1)
        self.assertEqual(session.put.call_count, 1)

    def test_unverified_pin_stays_durably_fenced_without_media_or_pin_retry(self):
        meta, session, _ = self.network_update_fixture(pinned=False)
        self.second_report()
        session.patch.side_effect = TimeoutError("pin outcome unavailable")
        with patch.object(google, "_session", return_value=session), patch.object(checklist, "_conditional_update", side_effect=REAL_UPDATE):
            result = self.refresh()
            self.assertEqual(result["status"], "held")
            self.assertIsNotNone(self.record()["pending"])
            self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(session.patch.call_count, 1)
        session.put.assert_not_called()
        self.assertEqual(self.drive.metadata("read", meta["id"]), meta)

    def test_pin_cannot_rebind_changed_content_head_or_privacy(self):
        for change in ("head", "bytes", "privacy", "name", "parent", "modifiedTime"):
            with self.subTest(change=change):
                meta, session, state = self.network_update_fixture(pinned=False)
                prior_meta, prior_bytes = deepcopy(meta), self.drive.contents[meta["id"]]
                pin = session.patch.side_effect
                def changed(*args, **kwargs):
                    result = pin(*args, **kwargs)
                    if change == "head":
                        state["head"] = "ChangedHead"
                    elif change == "bytes":
                        self.drive.contents[meta["id"]] = b"changed bytes"
                    elif change == "privacy":
                        self.drive.items[meta["id"]]["permissions"] = [{"type": "anyone"}]
                    else:
                        field = "parents" if change == "parent" else change
                        self.drive.items[meta["id"]][field] = ["ChangedParent"] if change == "parent" else "changed"
                    return result
                session.patch.side_effect = changed
                with patch.object(google, "_session", return_value=session):
                    self.assertIsNone(REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist"))
                self.assertEqual(session.patch.call_count, 1); session.put.assert_not_called()
                self.drive.items[meta["id"]] = prior_meta
                self.drive.contents[meta["id"]] = prior_bytes

    def test_media_precondition_failure_or_lost_response_never_retries(self):
        for status in (412, 503, None):
            with self.subTest(status=status):
                meta, session, _ = self.network_update_fixture()
                if status is None:
                    session.put.side_effect = TimeoutError("reply lost")
                else:
                    session.put.return_value.status_code = status
                with patch.object(google, "_session", return_value=session):
                    self.assertEqual(REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist"), status)
                self.assertEqual(session.put.call_count, 1)

    def test_read_failure_is_prewrite_but_lost_lease_is_not_cleared_or_downgraded(self):
        meta, session, _ = self.network_update_fixture(pinned=False)
        session.get.side_effect = TimeoutError("read unavailable")
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            REAL_UPDATE(Mock(token="read", write_token="write"), meta, b"%PDF checklist")
        session.patch.assert_not_called(); session.put.assert_not_called()
        meta, session, _ = self.network_update_fixture(pinned=False)
        run = Mock(token="read", write_token="write")
        run.heartbeat.side_effect = Conflict("lease lost")
        with patch.object(google, "_session", return_value=session), self.assertRaises(Conflict):
            REAL_UPDATE(run, meta, b"%PDF checklist")
        session.patch.assert_not_called(); session.put.assert_not_called()

REAL_UPDATE = checklist._conditional_update
