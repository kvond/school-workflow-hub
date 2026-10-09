"""Daily student packet acceptance: fictional Drive, real PDF merge, isolated CAS."""
import hashlib
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from hub_v2 import app, daily_feedback_packet as daily, google, native_feedback
from tests.test_cloud_state import MemoryCAS
from school_workflow_hub.core.cloud_state import StoreUnavailable
from tests.test_student_archive import FakeDrive, PERMISSIONS

FOLDER = 'FictionalDailyPacketsFolder123'
DAY = '2026-10-07'


def pdf_bytes(width=612):
    writer, output = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=width, height=792); writer.write(output)
    return output.getvalue()


class DailyFeedbackPacketTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port = FakeDrive(), MemoryCAS()
        self.drive.add(FOLDER, 'Daily feedback documents', google.FOLDER)
        self.config = {daily.FOLDER_CONFIG: FOLDER}
        self.bytes, self.sources = {}, []
        for index, cls in enumerate(('forensics', 'biology', 'anatomy')):
            ident, raw = 'FictionalFeedbackPdf' + str(index), pdf_bytes(600 + index)
            self.bytes[ident] = raw
            meta = self.drive.add(ident, cls + ' feedback.pdf', daily.PDF)
            meta.update(md5Checksum=hashlib.md5(raw).hexdigest(), size=str(len(raw)))
            self.sources.append({'id': ident, 'name': meta['name'], 'course': cls, 'mime': daily.PDF,
                'feedback_id': 'feedbackdraft_' + str(index) * 64, 'created_at': '2026-10-08T12:00:00Z',
                'saved_at': DAY + 'T15:00:00Z', 'feedback_day': DAY, 'archived': True})
        def create(token, metadata, raw):
            self.bytes[metadata['id']] = raw
            return self.drive.create(token, metadata, raw)
        self.patches = [patch.object(daily, 'configured_store', return_value=self.port),
            patch.object(google, 'drive_file', side_effect=self.drive.metadata),
            patch.object(google, 'archive_file_id', side_effect=self.drive.reserve),
            patch.object(google, 'archive_create', side_effect=create),
            patch.object(google, 'printable_pdf_bytes', side_effect=lambda token, ident, mime: self.bytes[ident]),
            patch.object(native_feedback, 'daily_sources', side_effect=lambda token, config, day: deepcopy(self.sources)),
            patch.object(native_feedback, '_call', side_effect=AssertionError('no model call')),
            patch.object(google, 'append_rows', side_effect=AssertionError('no grade or delivery write'))]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def build(self):
        return daily.build('read', 'write', self.config, DAY)

    def bind_private_student_folders(self):
        for index, source in enumerate(self.sources):
            folder = 'FictionalStudentFeedbackFolder' + str(index)
            self.drive.add(folder, 'Private student feedback', google.FOLDER)
            self.drive.items[source['id']]['parents'] = [folder]
            source['student_folder_url'] = 'https://drive.google.com/drive/folders/' + folder

    def test_all_classes_merge_once_with_original_dates_and_repeat_reuses_the_saved_pdf(self):
        one, again = self.build(), self.build()
        self.assertEqual(one['id'], again['id'])
        self.assertTrue(again['cached'])
        self.assertEqual(len(self.drive.created), 1)
        raw, name = daily.pdf('read', self.config, one['id'])
        self.assertTrue(name.startswith(DAY + ' - Daily student feedback'))
        self.assertEqual([page.mediabox.width for page in PdfReader(io.BytesIO(raw)).pages], [601, 600, 602])
        self.assertEqual(one['classes'], ['biology', 'forensics', 'anatomy'])
        self.assertEqual(one['pages'], 3)
        self.assertEqual(one['individual_reports'], 3)
        manifest = self.port.read([one['id']]).documents[one['id']].value['manifest']
        self.assertTrue(all(item['saved_at'].startswith(DAY) and item['created_at'].startswith('2026-10-08') for item in manifest))
        self.assertEqual(daily.status('read', self.config, DAY)['id'], one['id'])
        self.assertFalse(one['delivery_changed'])

    def test_a_changed_manifest_creates_another_version_and_preserves_the_previous_document(self):
        previous = self.build()
        self.drive.items[self.sources[0]['id']]['version'] = '2'
        self.drive.items[self.sources[0]['id']]['modifiedTime'] = '2026-10-09T12:00:00Z'
        current = self.build()
        self.assertNotEqual(previous['id'], current['id'])
        self.assertEqual(len(self.drive.created), 2)
        self.assertEqual(daily.status('read', self.config, DAY)['id'], current['id'])
        self.assertTrue(daily.pdf('read', self.config, previous['id'])[0].startswith(b'%PDF'))

    def test_same_source_ids_with_a_new_revision_do_not_claim_the_old_pdf_is_current(self):
        saved = self.build()
        self.assertTrue(daily.matches_sources('read', saved, self.sources))
        self.drive.items[self.sources[0]['id']]['version'] = '2'
        self.assertFalse(daily.matches_sources('read', saved, self.sources))
        self.assertEqual(len(self.drive.created), 1)
        self.assertFalse(daily.matches_sources('read', saved, self.sources[:-1]))
        self.drive.items[self.sources[0]['id']]['permissions'] = [{'type': 'anyone', 'role': 'reader'}]
        with self.assertRaisesRegex(daily.Held, 'private access'):
            daily.matches_sources('read', saved, self.sources)

    def test_metadata_only_version_drift_reuses_saved_pdf_after_complete_byte_proof_without_writing(self):
        self.bind_private_student_folders()
        saved = self.build()
        state = deepcopy(self.port.rows)
        for source in self.sources:
            self.drive.items[source['id']]['version'] = '3'
        with patch.object(google, 'printable_pdf_bytes', wraps=google.printable_pdf_bytes) as download:
            self.assertTrue(daily.matches_sources('read', saved, self.sources))
            self.assertEqual(download.call_count, 3)
        self.assertEqual(self.port.rows, state)
        self.assertEqual(len(self.drive.created), 1)
        self.assertEqual(daily.status('read', self.config, DAY)['id'], saved['id'])

    def test_unchanged_revisions_keep_the_existing_fast_path_without_export(self):
        saved = self.build()
        with patch.object(google, 'printable_pdf_bytes', side_effect=AssertionError('no export needed')):
            self.assertTrue(daily.matches_sources('read', saved, self.sources))

    def test_metadata_fallback_requires_trusted_hash_context_and_known_private_parent(self):
        self.bind_private_student_folders()
        saved = self.build()
        ident = self.sources[0]['id']
        self.drive.items[ident]['version'] = '2'
        for field in ('source_pdf_sha256', 'source_identities'):
            weak = {k: v for k, v in saved.items() if k != field}
            with self.subTest(missing=field):
                self.assertFalse(daily.matches_sources('read', weak, self.sources))
        for field in ('md5Checksum', 'size', 'modifiedTime'):
            prior = self.drive.items[ident][field]
            self.drive.items[ident][field] = 'different'
            with self.subTest(content_metadata=field):
                self.assertFalse(daily.matches_sources('read', saved, self.sources))
            self.drive.items[ident][field] = prior
        self.drive.items[ident]['parents'] = ['AnotherPrivateFolder001']
        self.assertFalse(daily.matches_sources('read', saved, self.sources))

    def test_changed_dates_class_and_feedback_members_cannot_match_identical_pdf_bytes(self):
        self.bind_private_student_folders()
        saved = self.build()
        for field, value in (('feedback_day', '2026-10-09'), ('saved_at', '2026-10-09T15:00:00Z'),
                             ('course', 'another-class'), ('feedback_id', 'another-report'),
                             ('feedback_ids', ['another-member'])):
            sources = deepcopy(self.sources); sources[0][field] = value
            with self.subTest(field=field):
                self.assertFalse(daily.matches_sources('read', saved, sources))

    def test_changed_pdf_or_native_document_revision_never_uses_metadata_fallback(self):
        self.bind_private_student_folders()
        saved = self.build(); ident = self.sources[0]['id']
        self.drive.items[ident]['version'] = '2'
        original = self.bytes[ident]
        self.bytes[ident] = pdf_bytes(620)  # Same metadata cannot certify different downloaded bytes.
        self.assertFalse(daily.matches_sources('read', saved, self.sources))
        self.bytes[ident] = original
        native = deepcopy(saved); sources = deepcopy(self.sources)
        native['source_identities'][ident]['mime'] = google.DOC
        sources[0]['mime'] = google.DOC; self.drive.items[ident]['mimeType'] = google.DOC
        with patch.object(google, 'printable_pdf_bytes', side_effect=AssertionError('no native export fallback')):
            self.assertFalse(daily.matches_sources('read', native, sources))

    def test_privacy_or_location_loss_during_read_holds_the_saved_source(self):
        self.bind_private_student_folders()
        saved = self.build(); source = self.sources[0]; ident = source['id']
        self.drive.items[ident]['version'] = '2'
        original = deepcopy(self.drive.items[ident])
        for changed in ({'parents': ['AnotherPrivateFolder001']},
                        {'permissions': [{'type': 'anyone', 'role': 'reader'}]}):
            self.drive.items[ident] = deepcopy(original)
            def download(token, file_id, mime):
                self.drive.items[file_id].update(deepcopy(changed))
                return self.bytes[file_id]
            with self.subTest(change=changed), patch.object(google, 'printable_pdf_bytes', side_effect=download):
                with self.assertRaisesRegex(daily.Held, 'private access'):
                    daily.matches_sources('read', saved, self.sources)
        self.drive.items[ident] = deepcopy(original)
        folder = self.drive.items[ident]['parents'][0]
        self.drive.items[folder]['permissions'] = [{'type': 'group', 'role': 'reader', 'emailAddress': 'group@example.test'}]
        with self.assertRaisesRegex(daily.Held, 'private access'):
            daily.matches_sources('read', saved, self.sources)

    def test_dated_history_links_keep_earlier_days_and_immutable_versions_accessible(self):
        first = self.build()
        next_day = '2026-10-08'
        for source in self.sources:
            source.update(feedback_day=next_day, saved_at=next_day + 'T15:00:00Z')
        second = daily.build('read', 'write', self.config, next_day)
        result = daily.status('read', self.config)
        self.assertEqual(result['id'], second['id'])
        self.assertEqual([item['date'] for item in result['available_dates']], [next_day, DAY])
        self.assertEqual(result['available_dates'][1]['pdf_url'], first['pdf_url'])
        self.assertEqual(daily.pdf('read', self.config, first['id'])[0][:4], b'%PDF')

    def test_uncertain_success_is_recovered_by_exact_readback_and_an_unconfirmed_upload_is_not_repeated(self):
        self.drive.fail_after_create = True
        self.assertEqual(self.build()['status'], 'ready')
        self.assertEqual(len(self.drive.created), 1)
        self.drive.items[self.sources[0]['id']]['version'] = '2'
        self.drive.fail_create = True
        with self.assertRaises(daily.Held): self.build()
        self.assertEqual(len(self.drive.created), 2)
        with self.assertRaises(daily.Held): self.build()
        self.assertEqual(len(self.drive.created), 2)

    def test_public_source_or_changed_packet_bytes_hold_without_altering_sources(self):
        one = self.build()
        self.drive.items[one['drive_file_id']]['md5Checksum'] = '0' * 32
        with self.assertRaises(daily.Held): daily.pdf('read', self.config, one['id'])
        self.drive.items[self.sources[0]['id']]['permissions'] = [{'type': 'anyone'}]
        with self.assertRaises(daily.Held): self.build()
        self.assertEqual(self.drive.moved, [])

    def test_source_revision_change_mid_export_prevents_packet_upload(self):
        def changed(token, ident, mime):
            self.drive.items[ident]['version'] = 'changed'
            return self.bytes[ident]
        with patch.object(google, 'printable_pdf_bytes', side_effect=changed):
            with self.assertRaisesRegex(daily.Held, 'changed during'): self.build()
        self.assertEqual(self.drive.created, [])

    def test_interruption_before_upload_reconstructs_existing_sources_using_the_same_reserved_id(self):
        original, failed = daily._put, False
        def interrupted(port, ident, value, old=None):
            nonlocal failed
            if value.get('status') == 'creating' and not failed:
                failed = True
                raise StoreUnavailable('fictional process interruption before upload')
            return original(port, ident, value, old)
        with patch.object(daily, '_put', side_effect=interrupted):
            with self.assertRaises(StoreUnavailable): self.build()
        self.assertEqual(self.drive.created, [])
        reserved = self.drive.sequence
        self.assertEqual(self.build()['status'], 'ready')
        self.assertEqual(self.drive.sequence, reserved)
        self.assertEqual(len(self.drive.created), 1)

    def test_known_quota_failure_resumes_only_after_verified_real_shared_drive_migration_with_the_same_id(self):
        self.drive.fail_create = True
        with self.assertRaisesRegex(daily.Held, 'actual Shared Drive'): self.build()
        reserved = self.drive.sequence
        self.drive.fail_create = False
        with self.assertRaises(daily.Held): self.build()
        self.assertEqual(len(self.drive.created), 1)
        self.drive.items[FOLDER]['driveId'] = 'FictionalActualSharedDrive123'
        self.assertEqual(self.build()['status'], 'ready')
        self.assertEqual(self.drive.sequence, reserved)
        self.assertEqual(len(self.drive.created), 2)
        self.assertEqual(self.drive.created[0]['id'], self.drive.created[1]['id'])

    def test_exact_date_empty_source_and_missing_configuration_are_not_relabelled_success(self):
        self.sources.clear()
        with self.assertRaisesRegex(daily.Held, 'No verified saved feedback belongs to'): self.build()
        self.assertEqual(daily.status('read', {}, DAY)['status'], 'unconfigured')
        with self.assertRaises(daily.Held): daily.date_value('2026-02-30')
        self.assertEqual(self.drive.created, [])


class DailySourceSelectionTests(unittest.TestCase):
    def joint_sources(self):
        reference = 'feedbackpacketmanifest_' + 'c' * 64
        digest = 'd' * 64
        packet = {'id': 'FictionalJointClassPdf001', 'name': 'Student feedback class packet.pdf', 'course': 'forensics',
                  'created_day': DAY, 'feedback_day': DAY, 'feedback_ids': [], 'student_only': True,
                  'source_manifest': reference, 'original_source_id': 'FictionalJointOriginalDoc001',
                  'audited_source_pdf_sha256': digest, 'proven_revision': {'version': '3'}}
        copies = [{'id': 'FictionalStudentCopy%03d' % number, 'name': 'Joint Feedback.pdf', 'course': 'forensics',
                   'created_day': '2026-10-09', 'feedback_day': DAY, 'archived': True, 'saved_at': DAY + 'T15:00:00Z',
                   'feedback_id': 'feedbacksource_' + 'a' * 64,
                   'shared_source_key': 'sharedfeedbacksource_' + 'b' * 64,
                   'shared_source_document_id': packet['original_source_id'], 'shared_pdf_sha256': digest,
                   'pdf_sha256': digest, 'covered_by_packet_manifest': reference, 'covered_by_packet_id': packet['id']}
                  for number in (1, 2)]
        return packet, copies

    def test_today_catalog_and_print_preview_use_the_same_exact_shared_source_suppression(self):
        packet, copies = self.joint_sources()
        for item in [packet] + copies:
            item.update(created_at=DAY + 'T15:00:00Z', mime=daily.PDF)
        available = {'configured': True, 'files': [packet] + copies}
        writer, output = PdfWriter(), io.BytesIO()
        writer.add_blank_page(width=612, height=792); writer.add_blank_page(width=612, height=792); writer.write(output)
        with patch.object(native_feedback, '_feedback_files', return_value=available), \
                patch.object(daily, 'status', return_value={'status': 'not_saved', 'configured': True, 'date': DAY}), \
                patch.object(google, 'printable_pdf_bytes', return_value=output.getvalue()) as download:
            config = {daily.FOLDER_CONFIG: FOLDER}
            today = native_feedback.today_files('read', config, date_et=DAY)
            self.assertEqual([item['id'] for item in today['files']], [packet['id']])
            raw, _ = native_feedback.print_today('read', config, [packet['id']], date_et=DAY)
            self.assertEqual(len(PdfReader(io.BytesIO(raw)).pages), 2)
            self.assertEqual(download.call_count, 1)

    def test_two_joint_student_copies_leave_existing_dated_packet_selection_unchanged_with_exact_readback_proof(self):
        from hub_v2 import student_archive
        packet, copies = self.joint_sources()
        port = MemoryCAS()
        meta = {'id': packet['id'], 'version': '3', 'modifiedTime': DAY + 'T15:00:00Z', 'trashed': False,
                'md5Checksum': 'e' * 32, 'size': '400'}
        proof_record = {'schema': 'hub-feedback-source-manifest/v1', 'drive_file_id': packet['id'],
                        'source_revision': daily._revision(meta), 'student_only': True, 'feedback_date': DAY,
                        'feedback_ids': [], 'original_source_id': packet['original_source_id'],
                        'audited_source_pdf_sha256': packet['audited_source_pdf_sha256']}
        port.commit({packet['source_manifest']: (proof_record, None)})
        stored = deepcopy(port.rows)
        archived = [{**item, 'drive_file_id': item['id'], 'status': 'verified', 'mime': 'application/pdf',
                     'class_key': item['course'], 'created_at': '2026-10-09T14:00:00Z', 'date': DAY,
                     'class_folder_id': 'FictionalForensicsCourse001'} for item in copies]
        with patch.object(native_feedback, 'configured_store', return_value=port), \
                patch.object(google, 'drive_file', return_value=meta), \
                patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'list_feedback', return_value=archived):
            actual_proof = native_feedback._legacy_packet_proof('read', {},
                {'id': packet['id'], 'properties': {'hubFeedbackManifest': packet['source_manifest']}}, DAY)
            fresh_packet = {**packet, **actual_proof}
            with patch.object(native_feedback, '_legacy_feedback_files', return_value={
                    'configured': True, 'files': [fresh_packet], 'problem': None}):
                available = native_feedback._feedback_files('read', {})
                for copy in (item for item in available['files'] if item.get('archived')):
                    self.assertEqual(copy['pdf_sha256'], copy['shared_pdf_sha256'])
                    self.assertEqual(copy['covered_by_packet_id'], packet['id'])
                self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)], [packet['id']])
        self.assertEqual(port.rows, stored)  # Existing class proof and dated snapshot need no rewrite.

    def test_joint_copies_print_once_when_their_covering_class_packet_is_absent(self):
        _, copies = self.joint_sources()
        with patch.object(native_feedback, '_feedback_files', return_value={'configured': True, 'files': copies}):
            selected = native_feedback.daily_sources('read', {}, DAY)
        self.assertEqual([item['id'] for item in selected], [copies[0]['id']])
        self.assertEqual(selected[0]['feedback_id'], copies[1]['feedback_id'])
        self.assertEqual(selected[0]['feedback_day'], DAY)

    def test_joint_coverage_does_not_hide_copies_on_partial_or_mismatched_source_proof(self):
        packet, copies = self.joint_sources()
        mismatches = {'shared_source_key': None, 'shared_source_document_id': 'AnotherOriginalSource001',
                      'shared_pdf_sha256': 'f' * 64, 'pdf_sha256': 'f' * 64,
                      'covered_by_packet_manifest': 'feedbackpacketmanifest_' + 'f' * 64,
                      'covered_by_packet_id': 'AnotherClassPacket001'}
        for field, value in mismatches.items():
            weak = [{**item, field: value} for item in copies]
            with self.subTest(field=field), patch.object(native_feedback, '_feedback_files', return_value={
                    'configured': True, 'files': [packet] + weak}):
                self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)],
                                 [packet['id'], copies[0]['id']])
        for field, value in {'source_manifest': None, 'original_source_id': None,
                             'audited_source_pdf_sha256': None, 'proven_revision': None, 'course': 'biology'}.items():
            with self.subTest(packet_field=field), patch.object(native_feedback, '_feedback_files', return_value={
                    'configured': True, 'files': [{**packet, field: value}] + copies}):
                self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)],
                                 [packet['id'], copies[0]['id']])
        other_day = {**packet, 'feedback_day': '2026-10-08'}
        with patch.object(native_feedback, '_feedback_files', return_value={'configured': True, 'files': [other_day] + copies}):
            self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)], [copies[0]['id']])

    def test_native_saved_dates_beat_migration_creation_dates_and_known_batch_overlap_is_suppressed(self):
        native = {'id': 'native', 'name': 'Feedback.pdf', 'course': 'biology', 'created_day': '2026-10-08',
            'feedback_day': DAY, 'archived': True, 'saved_at': DAY + 'T15:00:00Z', 'feedback_id': 'feedbackdraft_' + 'a' * 64}
        legacy = {'id': 'packet', 'name': 'Class feedback.pdf', 'course': 'biology', 'created_day': DAY,
            'feedback_day': DAY, 'feedback_ids': [native['feedback_id']], 'student_only': True}
        available = {'configured': True, 'files': [native, legacy]}
        with patch.object(native_feedback, '_feedback_files', return_value=available):
            self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)], ['native'])
            self.assertEqual(native_feedback.daily_sources('read', {}, '2026-10-08'), [])
            legacy['feedback_ids'].append('feedbackdraft_' + 'b' * 64)
            self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)], ['packet'])

    def test_unaudited_or_teacher_report_packet_cannot_enter_the_student_daily_document(self):
        legacy = {'id': 'packet', 'name': 'Class feedback.pdf', 'course': 'biology', 'created_day': DAY}
        with patch.object(native_feedback, '_feedback_files', return_value={'configured': True, 'files': [legacy]}):
            with self.assertRaisesRegex(native_feedback.Held, 'student-only'): native_feedback.daily_sources('read', {}, DAY)

    def test_proven_duplicate_packet_uses_only_canonical_original_and_partial_packet_overlap_is_held(self):
        canonical = {'id': 'FictionalCanonical123', 'name': 'Class feedback.pdf', 'course': 'anatomy',
            'created_day': DAY, 'student_only': True, 'feedback_ids': ['feedbacksource_' + 'a' * 64, 'feedbacksource_' + 'b' * 64]}
        duplicate = {**canonical, 'id': 'FictionalDuplicate123', 'canonical_source_id': canonical['id']}
        available = {'configured': True, 'files': [canonical, duplicate]}
        with patch.object(native_feedback, '_feedback_files', return_value=available):
            self.assertEqual([item['id'] for item in native_feedback.daily_sources('read', {}, DAY)], [canonical['id']])
            overlapping = {**canonical, 'id': 'FictionalPartial123', 'feedback_ids': ['feedbacksource_' + 'b' * 64, 'feedbacksource_' + 'c' * 64]}
            available['files'].append(overlapping)
            with self.assertRaisesRegex(native_feedback.Held, 'overlapping proven reports'):
                native_feedback.daily_sources('read', {}, DAY)

    def test_audited_large_legacy_membership_uses_private_revision_bound_manifest_and_distinct_import_ids(self):
        port = MemoryCAS()
        reference = 'feedbackpacketmanifest_' + 'c' * 64
        members = ['feedbacksource_' + 'a' * 64, 'feedbackdraft_' + 'b' * 64]
        meta = {'id': 'FictionalLegacyPacket123', 'version': '3', 'modifiedTime': DAY + 'T15:00:00Z',
                'trashed': False, 'md5Checksum': None, 'size': None}
        record = {'schema': 'hub-feedback-source-manifest/v1', 'drive_file_id': meta['id'],
                  'source_revision': daily._revision(meta), 'student_only': True, 'feedback_date': DAY, 'feedback_ids': members}
        port.commit({reference: (record, None)})
        item = {'id': meta['id'], 'properties': {'hubFeedbackManifest': reference}}
        with patch.object(native_feedback, 'configured_store', return_value=port), patch.object(google, 'drive_file', return_value=meta):
            proof = native_feedback._legacy_packet_proof('read', {}, item, '2026-10-08')
            self.assertEqual(proof['feedback_ids'], members)
            self.assertEqual(proof['feedback_day'], DAY)
            self.assertTrue(proof['student_only'])
            meta['version'] = '4'
            with self.assertRaisesRegex(native_feedback.Held, 'source revision'):
                native_feedback._legacy_packet_proof('read', {}, item, '2026-10-08')


class DailyPacketRouteTests(unittest.TestCase):
    def request(self, method, path, payload=None, query='', origin='https://hub.example'):
        raw = json.dumps(payload or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
            'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw),
            'HTTP_ORIGIN': origin, 'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional'}
        seen = {}
        result = b''.join(app.application(env, lambda status, headers: seen.update(status=status, headers=dict(headers)),
            {'HUB_STATE_PROBE_ORIGINS': 'https://hub.example'}))
        return seen, result

    def test_teacher_auth_and_exact_origin_are_required_before_daily_build_or_private_pdf_access(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()), patch.object(daily, 'build') as build:
            self.assertTrue(self.request('POST', '/v2/api/feedback/daily', {'date': DAY})[0]['status'].startswith('401'))
            build.assert_not_called()
        with patch.object(google, 'verify_teacher', return_value='teacher'), patch.object(daily, 'build') as build:
            self.assertTrue(self.request('POST', '/v2/api/feedback/daily', {'date': DAY}, origin='https://other.example')[0]['status'].startswith('403'))
            build.assert_not_called()
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()), patch.object(daily, 'pdf') as pdf:
            self.assertTrue(self.request('GET', '/v2/api/feedback/daily/pdf', query='id=private')[0]['status'].startswith('401'))
            pdf.assert_not_called()

    def test_finalize_and_private_download_use_existing_tokens_without_agenda_or_model_reads(self):
        with patch.object(google, 'verify_teacher', return_value='teacher'), patch.object(google, 'service_token', return_value='token'), \
                patch.object(app, 'load_agenda', side_effect=AssertionError('unnecessary Agenda')), \
                patch.object(daily, 'build', return_value={'status': 'ready', 'date': DAY}) as build, \
                patch.object(daily, 'pdf', return_value=(b'%PDF fictional', DAY + ' - Daily feedback.pdf')):
            seen, raw = self.request('POST', '/v2/api/feedback/daily', {'date': DAY})
            self.assertEqual(seen['status'], '200 OK')
            self.assertEqual(json.loads(raw)['date'], DAY)
            build.assert_called_once()
            seen, raw = self.request('GET', '/v2/api/feedback/daily/pdf', query='id=dailyfeedbackpacket_' + 'a' * 64)
            self.assertEqual(seen['headers']['Content-Type'], 'application/pdf')
            self.assertEqual(seen['headers']['Cache-Control'], 'no-store')
            self.assertIn(DAY, seen['headers']['Content-Disposition'])
            self.assertTrue(raw.startswith(b'%PDF'))
