"""Counts-only publication with real PDF bytes and isolated private CAS/Drive."""
import hashlib
import io
import unittest
from copy import deepcopy
from unittest.mock import patch
from pypdf import PdfWriter
from hub_v2 import app, google, printable_feedback_link as links, student_archive
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_student_archive import FakeDrive, ROOT

PDF, FOLDER, SHEET = 'FictionalSnapshotPdf123', 'FictionalTeacherReports123', 'FictionalFeedbackSheet123'


class PrintableFeedbackLinkTests(unittest.TestCase):
    def setUp(self):
        self.port, self.drive = MemoryCAS(), FakeDrive()
        self.config = {'HUB_AGENDA_SPREADSHEET_ID': 'FictionalAgenda123'}
        self.drive.add(FOLDER, 'Teacher reports', google.FOLDER, [ROOT])
        writer, out = PdfWriter(), io.BytesIO(); writer.add_blank_page(612, 792); writer.write(out)
        self.raw = out.getvalue()
        self.drive.add(PDF, 'Verified printable feedback.pdf', 'application/pdf', [FOLDER]).update(
            md5Checksum=hashlib.md5(self.raw).hexdigest(), size=str(len(self.raw)))
        self.manifest = {'date': '2032-01-05', 'sha256': hashlib.sha256(self.raw).hexdigest(),
                         'pages': 1, 'reports': 2, 'students': 1, 'report_type_gaps': 1,
                         'source_holds': 1, 'coverage': 'partial_verified_reports'}
        patches = [patch.object(links, 'configured_store', return_value=self.port),
                   patch.object(student_archive, '_configuration', return_value=(ROOT, {})),
                   patch.object(google, 'drive_file', side_effect=self.drive.metadata),
                   patch.object(google, 'printable_pdf_bytes', return_value=self.raw),
                   patch.object(google, 'batch_update', side_effect=AssertionError('No school write'))]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def publish(self, revision='', manifest=None):
        return links.publish('read', self.config, file_id=PDF, parent_id=FOLDER,
                             spreadsheet_id=SHEET, manifest=manifest or self.manifest, revision=revision)

    def test_verified_partial_packet_is_available_without_retired_catalog_or_delivery_state(self):
        self.assertEqual(links.read('read', self.config)['status'], 'unpublished')
        current = self.publish()
        self.assertEqual(current['status'], 'ready'); self.assertEqual(current['coverage'], 'partial_verified_reports')
        self.assertEqual(current['spreadsheet_url'], 'https://docs.google.com/spreadsheets/d/' + SHEET + '/edit')
        before = deepcopy(self.port.rows)
        with patch.object(google, 'printable_pdf_bytes', side_effect=AssertionError('No export on a link read')):
            self.assertEqual(links.read('read', self.config), current)
        self.assertEqual(self.port.rows, before)
        self.assertEqual(len(self.port.rows), 2)

    def test_full_bytes_and_page_count_must_match_before_any_store_write(self):
        for bad in ({**self.manifest, 'sha256': '0' * 64}, {**self.manifest, 'pages': 2}):
            with self.assertRaises(student_archive.Held): self.publish(manifest=bad)
        self.assertEqual(self.port.rows, {})

    def test_stale_publish_cannot_replace_newer_link_and_history_is_preserved(self):
        first = self.publish()
        with self.assertRaises(Conflict): self.publish()
        self.assertEqual(self.publish(first['revision']), first)
        self.drive.items[PDF]['version'] = '2'
        second = self.publish(first['revision'])
        self.assertEqual(second['status'], 'ready')
        self.assertEqual(len([k for k in self.port.rows if k.startswith('spreadsheetfeedbacklinkhistory_')]), 2)

    def test_changed_trashed_public_or_moved_pdf_never_returns_a_clickable_fallback(self):
        self.publish(); saved = deepcopy(self.drive.items[PDF])
        for changes in ({'version': '2'}, {'trashed': True}, {'parents': [ROOT]},
                        {'permissions': [{'type': 'anyone', 'role': 'reader'}]}):
            self.drive.items[PDF] = {**deepcopy(saved), **changes}
            result = links.read('read', self.config)
            self.assertEqual(result['status'], 'held'); self.assertNotIn('url', result)
        self.drive.items[PDF] = saved

    def test_bad_ancestry_and_material_changing_mid_publication_stop_before_save(self):
        self.drive.items[FOLDER]['parents'] = ['UnknownParent123']
        with self.assertRaises(google.GoogleError): self.publish()
        self.drive.items[FOLDER]['parents'] = [ROOT]
        def bytes_then_change(*_):
            self.drive.items[PDF]['version'] = '2'; return self.raw
        with patch.object(google, 'printable_pdf_bytes', side_effect=bytes_then_change):
            with self.assertRaises(student_archive.Held): self.publish()
        self.assertEqual(self.port.rows, {})

    def test_inconsistent_complete_coverage_extra_fields_and_invalid_ids_are_rejected(self):
        for bad in ({**self.manifest, 'coverage': 'complete_verified_reports'},
                    {**self.manifest, 'student_names': ['Private']}, {**self.manifest, 'pages': True}):
            with self.assertRaises(ValueError): self.publish(manifest=bad)
        with self.assertRaises(ValueError):
            links.publish('read', self.config, file_id='https://bad.test', parent_id=FOLDER,
                          spreadsheet_id=SHEET, manifest=self.manifest, revision='')

    def test_unavailable_store_is_not_unpublished_and_read_endpoint_has_no_query_or_write(self):
        with patch.object(self.port, 'read', side_effect=StoreUnavailable()):
            with self.assertRaises(StoreUnavailable): links.read('read', self.config)
        with patch.object(google, 'service_token', return_value='read'):
            self.assertEqual(app.api('api/spreadsheet-feedback/print-link', {}, self.config)['status'], 'unpublished')
            with self.assertRaises(ValueError): app.api('api/spreadsheet-feedback/print-link', {'id': ['foreign']}, self.config)

    def test_corrupt_private_publication_cannot_override_url_or_ready_status(self):
        self.publish()
        ident = links._id(self.config); old = self.port.rows[ident]
        bad = deepcopy(old.value); bad['manifest']['url'] = 'https://unverified.invalid'
        self.port.commit({ident: (bad, old.version)})
        result = links.read('read', self.config)
        self.assertEqual(result['status'], 'held'); self.assertNotIn('url', result)
