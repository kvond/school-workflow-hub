"""Index labels use actual filing receipts, with only fictional private state."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import student_archive as archive
from tests import test_science_writing_gap_filing as gap_fixture
from tests.test_student_archive import SOURCE, EARLIER
from tests.test_imported_original_archive import FILE


ASSESSED = "Science writing assessment covered this submission"
HISTORICAL = "Science writing historical reference; no complete assessment recorded"
UNKNOWN = "Science writing historical reference; assessment coverage not verified"


class StudentArchiveAssessmentCoverageTests(unittest.TestCase):
    def setUp(self):
        fixture = gap_fixture.WritingGapFilingTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        filed = fixture.file()
        self.assertEqual(filed["status"], "verified", filed)
        self.student = fixture.student
        self.works = fixture.artifacts
        self.report = deepcopy(next(doc.value for doc in fixture.port.rows.values()
            if doc.value.get("drive_file_id") == filed["drive_file_id"]))
        # The real canonical filer assessed SOURCE and retained EARLIER/FILE
        # as unrated chart samples, without manufacturing observations.
        self.assertEqual([s["source_file_id"] for s in self.report["assessed_sources"]["sources"]], [SOURCE])

    def project(self, report=None, works=None):
        artifacts = (self.works if works is None else works) + [self.report if report is None else report]
        with patch.object(archive, "_indexed", side_effect=lambda token, config, cls, **kwargs:
                ({}, [(self.student, artifacts)] if cls == "biology" else [])), \
                patch.object(archive, "_filing", return_value={"scans": []}):
            result = archive.work_index_rows("read", {})
        result.pop("checked_at")
        return result

    def report_rows(self, result):
        return [row for row in result["rows"] if row[9]]

    def test_canonical_mixed_report_separates_assessed_sources_and_explicit_gaps(self):
        rows = self.report_rows(self.project())
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(row[11] for row in rows), sorted([ASSESSED, HISTORICAL, HISTORICAL]))
        assessed = next(row for row in rows if row[11] == ASSESSED)
        self.assertIn(SOURCE, assessed[8])
        self.assertTrue(all(row[7] and row[8] and row[9] for row in rows))

    def test_fully_assessed_report_needs_exact_canonical_source_receipt(self):
        report = deepcopy(self.report)
        report["source_records"] = [s for s in report["source_records"] if s["source_file_id"] == SOURCE]
        rows = self.report_rows(self.project(report))
        self.assertEqual([row[11] for row in rows], [ASSESSED])

    def test_historical_only_report_does_not_claim_assessment_or_missing_submission(self):
        report = deepcopy(self.report)
        report["source_records"] = [s for s in report["source_records"] if s["source_file_id"] in {EARLIER, FILE}]
        report["assessed_sources"]["sources"] = []
        self.assertEqual([row[11] for row in self.report_rows(self.project(report))], [HISTORICAL, HISTORICAL])

    def test_legacy_absent_or_unavailable_proof_keeps_coverage_unknown(self):
        for proof in (None, {}, "unavailable", {"schema": "unsupported"}):
            with self.subTest(proof=proof):
                report = deepcopy(self.report)
                report["assessed_sources"] = proof
                self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})
        report.pop("assessed_sources")
        self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})

    def test_changed_source_identity_revision_or_receipt_cannot_borrow_assessed_coverage(self):
        for field, value in (("source_file_id", EARLIER), ("source_version", "999"),
                ("source_modified_at", "2032-01-01T12:00:00Z"), ("date", "2032-01-01"),
                ("date_basis", "filename"), ("submitted_at", "2032-01-01T12:00:00Z"),
                ("source_form_id", "DifferentFictionalForm"), ("assignment_form_id", "DifferentFictionalAssignment"),
                ("source_response_id", "DifferentFictionalResponse"), ("observations_sha256", "invalid")):
            with self.subTest(field=field):
                report = deepcopy(self.report)
                report["assessed_sources"]["sources"][0][field] = value
                self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})

    def test_source_slot_mismatch_or_duplicate_proof_stays_unknown(self):
        for field, value in (("source_file_id", "DifferentFictionalOriginal"), ("source_version", "999"),
                             ("source_modified_at", "2032-01-01T12:00:00Z")):
            with self.subTest(field=field):
                report = deepcopy(self.report)
                next(s for s in report["source_records"] if s["source_file_id"] == SOURCE)[field] = value
                self.assertTrue(all(row[11].startswith(UNKNOWN) for row in self.report_rows(self.project(report))))
        report = deepcopy(self.report)
        report["assessed_sources"]["sources"] *= 2
        self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})

    def test_foreign_student_class_report_payload_and_invalid_proof_cannot_certify_coverage(self):
        for field, value in (("student_key", "DifferentFictionalStudent"), ("class_key", "forensics"),
                             ("report_source_sha256", "f" * 64)):
            with self.subTest(field=field):
                report = deepcopy(self.report)
                report[field] = value
                self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})
        for field, value in (("schema", "unsupported"), ("payload_sha256", "f" * 64),
                             ("observations_sha256", "invalid"), ("sources", [None])):
            with self.subTest(field=field):
                report = deepcopy(self.report)
                report["assessed_sources"][field] = value
                self.assertEqual({row[11] for row in self.report_rows(self.project(report))}, {UNKNOWN})

    def test_missing_filed_copy_is_separate_from_saved_assessment_evidence(self):
        rows = self.report_rows(self.project(works=[]))
        self.assertEqual(sorted(row[11] for row in rows), sorted([
            ASSESSED + "; filed work not located", HISTORICAL + "; filed work not located",
            HISTORICAL + "; filed work not located"]))
        self.assertTrue(all(row[7:9] == ["", ""] and row[9] for row in rows))

    def test_replay_preserves_anchor_keys_report_links_keyed_notes_and_old_pdf(self):
        legacy = deepcopy(self.report)
        legacy.pop("assessed_sources")
        old = self.project(legacy)
        current = self.project()
        self.assertEqual([row[13] for row in old["rows"]], [row[13] for row in current["rows"]])
        self.assertEqual([row[:11] for row in old["rows"]], [row[:11] for row in current["rows"]])
        cells = [{"values": [archive._index_cell(value) for value in archive.INDEX_HEADERS]}]
        notes = {}
        for row in old["rows"]:
            notes[row[13]] = "Existing teacher annotation for " + row[13]
            cells.append({"values": [archive._index_cell(value, link=column in {7, 8, 9, 10} and bool(value))
                                     for column, value in enumerate(row)]})
            cells[-1]["values"][12] = archive._index_cell(notes[row[13]])
        prepared, _ = archive._index_layout(cells, current["rows"])
        for row in prepared[1:]:
            values = archive._index_entered(row)
            self.assertEqual(values[12], {"stringValue": notes[values[13]["stringValue"]]})
        self.assertEqual(self.project(), current)
        historical = deepcopy(self.report)
        historical["supersession_verified"] = True
        retained = self.report_rows(self.project(historical))
        self.assertTrue(all(row[11].startswith("Superseded feedback; previous version retained. ") for row in retained))
        self.assertEqual([row[9] for row in retained], [row[9] for row in self.report_rows(current)])


if __name__ == "__main__":
    unittest.main()
