"""Saved native observations produce separate PDFs using fictional Drive/CAS."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch
from pypdf import PdfReader

from hub_v2 import native_feedback as native, science_writing_native as writing, student_archive as archive
from tests.test_native_feedback import inputs, report
from tests import test_student_archive as fixture_module
from tests.test_student_archive import SOURCE, found, row
from tests.test_assessment_provider import OPENAI_CONFIG


class NativeWritingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.StudentArchiveTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.port, self.drive, self.config = self.fixture.port, self.fixture.drive, self.fixture.config
        self.config.update(OPENAI_CONFIG)
        sections = {'biology': 'A4 Biology', 'forensics': 'A2 Forensics', 'anatomy': 'B3 A&P'}
        active = patch.object(archive, 'report_roster', side_effect=lambda token, config, course:
            [d.value for d in self.port.rows.values() if d.value.get('student_key') == d.value.get('record_key')
             and d.value.get('section') == sections[course]])
        active.start(); self.addCleanup(active.stop)
        for item in (patch.object(writing, 'configured_store', return_value=self.port),
                     patch.object(native, 'configured_store', return_value=self.port),
                     patch.object(native, 'now', return_value='2026-10-09T14:00:00Z')):
            item.start(); self.addCleanup(item.stop)
        self.model_report = report()
        for observation, explanation in zip(self.model_report['teacher_dimensions'], (
                'You identify a source of carbon. Use the scientific term as you explain the next step.',
                'You name where carbon comes from. Explain the process that connects it to the plant.',
                'You name a source. Include a specific observation that supports your answer.',
                'Connect your observation to the claim so the reader can follow your reasoning.')):
            observation['explanation'] = explanation
        self.provider = patch.object(native, '_call', return_value=(json.dumps(self.model_report), {'input_tokens': 10}))
        self.call = self.provider.start(); self.addCleanup(self.provider.stop)

    def data(self, course='biology'):
        section = {'biology': 'A4 Biology', 'forensics': 'A2 Forensics', 'anatomy': 'B3 A&P'}[course]
        submitted = row(section=section)
        submitted['files'][0].update(source_form_id='FictionalForm000000001',
            source_response_id='FictionalResponse000001', source_submitted_at=submitted['submitted_at'])
        scan = found(submitted); scan['class_key'] = course
        result = archive.archive_work('read', 'write', self.config, course, scan)
        self.assertEqual(result['status'], 'verified', result)
        data = inputs()
        data['scope'] = native.target(course, scan['week'], 'FictionalForm000000001', SOURCE)
        data['person']['section'] = section
        data['source']['file'] = {'id': SOURCE, 'version': '1', 'modified_at': self.drive.items[SOURCE]['modifiedTime'],
                                 'mime_type': archive.google.PPTX, 'name': 'Fictional submitted work'}
        data['reference']['file'].update(version='1', mime_type=archive.google.PPTX,
                                        modified_at='2026-10-01T12:00:00Z', name='Fictional teacher source')
        data['archive_identity'] = {'school_email': submitted['email'], 'first': submitted['first'],
            'last': submitted['last'], 'section': submitted['section'], 'matched_by': 'email', 'source': 'form'}
        data['submitted_at'] = submitted['submitted_at']
        data['submission_receipt'] = {'source_file_id': SOURCE, 'source_form_id': 'FictionalForm000000001',
            'source_response_id': 'FictionalResponse000001', 'assignment_form_id': 'FictionalForm000000001',
            'submitted_at': submitted['submitted_at']}
        return data

    def saved_ca(self, course='biology'):
        data = self.data(course)
        draft = native.generate(self.config, data)
        with patch.object(writing, 'file_saved', return_value={'status': 'held', 'problem': 'Test preflight only'}):
            result = native.save(self.config, draft['id'], data, token='read', write_token='write')
        return data, native.get_saved(self.config, draft['id']).value, result

    def test_save_files_a_separate_real_two_page_pdf_for_all_courses_without_rerating(self):
        for course in ('biology', 'forensics', 'anatomy'):
            with self.subTest(course=course):
                data = self.data(course)
                draft = native.generate(self.config, data)
                saved = native.save(self.config, draft['id'], data, token='read', write_token='write')
                receipt = saved['science_writing']
                self.assertEqual(receipt['status'], 'verified', receipt)
                self.assertTrue(receipt['readback_verified'])
                self.assertNotEqual(receipt['drive_file_id'], saved['archive']['drive_file_id'])
                raw = self.drive.contents[receipt['drive_file_id']]
                pdf = PdfReader(io.BytesIO(raw), strict=True)
                self.assertEqual(len(pdf.pages), 2)
                text = '\n'.join(page.extract_text() for page in pdf.pages)
                self.assertIn('Carbon comes from the air.', text)
                self.assertNotIn('Private concern never printed', text)
                self.assertIn('Earlier reports remain available', text)
                self.assertIn('One rated observation. No trend.', text)
                record = next(d.value for d in self.port.rows.values()
                              if d.value.get('schema') == writing.VERSION and d.value.get('native_feedback_id') == draft['id'])
                payload = record['payload']
                self.assertEqual(payload['class_key'], course)
                self.assertEqual(payload['lessons'][0]['source_date'], '2026-10-07')
                self.assertEqual(payload['prepared_at'], '2026-10-09')
                self.assertEqual(payload['provenance']['submission_receipt'], data['submission_receipt'])
                self.assertTrue(payload['provenance']['history_not_reconstructed'])
                for original, dimension, observation in zip(self.model_report['teacher_dimensions'], payload['dimensions'],
                                                           payload['provenance']['source_observations']):
                    self.assertEqual((dimension['level'], dimension['example'], dimension['explanation']),
                                     (original['level'], original['quote'], original['explanation']))
                    self.assertEqual({k: observation[k] for k in original}, original)
                created = len(self.drive.created)
                with patch.object(archive, 'file_science_writing', wraps=archive.file_science_writing) as filing:
                    again = native.save(self.config, draft['id'], data, token='read', write_token='write')
                filing.assert_not_called()
                self.assertEqual(again['science_writing']['drive_file_id'], receipt['drive_file_id'])
                self.assertEqual(len(self.drive.created), created)
        self.assertEqual(self.call.call_count, 3)

    def test_changed_source_wrong_owner_or_missing_receipt_hold_without_new_pdf(self):
        data, saved, _ = self.saved_ca()
        baseline = deepcopy(saved)
        for issue in ('source', 'owner', 'receipt', 'saved_observation'):
            changed = deepcopy(baseline)
            if issue == 'source': self.drive.items[SOURCE]['version'] = '2'
            if issue == 'owner': changed['archive_identity']['first'] = 'Different'
            if issue == 'receipt': changed['submission_receipt']['submitted_at'] = '2026-10-08T13:00:00Z'
            if issue == 'saved_observation': changed['report']['teacher_dimensions'][0]['level'] = 'Extending'
            created = len(self.drive.created)
            result = writing.file_saved('read', 'write', self.config, changed)
            with self.subTest(issue=issue):
                self.assertEqual(result['status'], 'held', result)
                self.assertEqual(len(self.drive.created), created)
            self.drive.items[SOURCE]['version'] = '1'
        self.assertEqual(self.call.call_count, 1)

    def test_writer_hold_preserves_saved_ca_and_existing_payload_for_filing_resume(self):
        data, saved, _ = self.saved_ca()
        with patch.object(archive, 'file_science_writing', return_value={'status': 'held', 'problem': 'Temporary writer hold'}):
            response = native.save(self.config, saved['id'], data, token='read', write_token='write')
        self.assertTrue(response['readback_verified'])
        self.assertEqual(response['archive']['status'], 'verified')
        self.assertEqual(response['science_writing']['status'], 'held')
        prepared = deepcopy(next(d.value for d in self.port.rows.values() if d.value.get('schema') == writing.VERSION)['payload'])
        with patch.object(native, 'now', return_value='2026-10-10T14:00:00Z'):
            result = native.save(self.config, saved['id'], data, token='read', write_token='write')
        self.assertEqual(result['science_writing']['status'], 'verified', result)
        self.assertEqual(next(d.value for d in self.port.rows.values() if d.value.get('schema') == writing.VERSION)['payload'], prepared)
        self.assertEqual(self.call.call_count, 1)

    def test_daily_packet_runs_after_separate_writing_filing(self):
        from hub_v2 import daily_feedback_packet
        data, saved, _ = self.saved_ca()
        order = []
        real = writing.file_saved
        def file_writing(*args):
            result = real(*args); self.assertEqual(result['status'], 'verified', result)
            order.append('writing'); return result
        def packet(*args):
            order.append('packet'); return {'status': 'verified'}
        with patch.object(writing, 'file_saved', side_effect=file_writing), \
                patch.object(daily_feedback_packet, 'enabled', return_value=True), \
                patch.object(daily_feedback_packet, 'build', side_effect=packet):
            native.save(self.config, saved['id'], data, token='read', write_token='write')
        self.assertEqual(order, ['writing', 'packet'])

    def test_later_writing_backfill_finalizes_its_actual_day_without_redating_ca(self):
        from hub_v2 import daily_feedback_packet
        data, saved, _ = self.saved_ca()
        with patch.object(native, 'now', return_value='2026-10-10T14:00:00Z'), \
                patch.object(daily_feedback_packet, 'enabled', return_value=True), \
                patch.object(daily_feedback_packet, 'build', return_value={'status': 'verified'}) as packet:
            result = native.save(self.config, saved['id'], data, token='read', write_token='write')
        self.assertEqual(result['saved_at'], '2026-10-09T14:00:00Z')
        self.assertEqual(result['science_writing']['saved_at'], '2026-10-10T14:00:00Z')
        packet.assert_called_once_with('read', 'write', self.config, '2026-10-10')

    def test_missing_work_snapshot_does_not_claim_separate_writing_complete(self):
        _, saved, _ = self.saved_ca()
        owner = self.port.rows[saved['archive']['student_key']].value
        owner['items'] = {k: v for k, v in owner['items'].items() if v.get('kind') != 'work'}
        count = len(self.drive.created)
        result = writing.file_saved('read', 'write', self.config, saved)
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('work snapshot', result['problem'])
        self.assertEqual(len(self.drive.created), count)

    def test_trusted_backfill_uses_saved_ca_without_generate_or_ca_save(self):
        _, saved, _ = self.saved_ca('forensics')
        with patch.object(native, 'generate', side_effect=AssertionError('No generation')), \
                patch.object(native, 'save', side_effect=AssertionError('No CA save')):
            result = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(self.call.call_count, 1)

    def test_legacy_missing_dimensions_holds_daily_packet_without_rerating(self):
        from hub_v2 import daily_feedback_packet
        data, saved, _ = self.saved_ca()
        doc = self.port.rows[saved['id']]
        doc.value['report'].pop('teacher_dimensions')
        with patch.object(daily_feedback_packet, 'enabled', return_value=True), \
                patch.object(daily_feedback_packet, 'build') as packet:
            result = native.save(self.config, saved['id'], data, token='read', write_token='write')
        packet.assert_not_called()
        self.assertTrue(result['readback_verified'])
        self.assertEqual(result['archive']['status'], 'verified')
        self.assertEqual(result['science_writing']['status'], 'held')
        self.assertIn('do not rerate', result['science_writing']['problem'])
        self.assertEqual(result['daily_packet']['status'], 'held')
        self.assertEqual(self.call.call_count, 1)

    def test_writing_hold_skips_daily_finalization_and_preserves_ca_pdf(self):
        from hub_v2 import daily_feedback_packet
        data, saved, _ = self.saved_ca()
        with patch.object(writing, 'file_saved', return_value={'status': 'held', 'problem': 'Writing source held'}), \
                patch.object(daily_feedback_packet, 'enabled', return_value=True), \
                patch.object(daily_feedback_packet, 'build') as packet:
            result = native.save(self.config, saved['id'], data, token='read', write_token='write')
        packet.assert_not_called()
        self.assertTrue(result['pdf_url'])
        self.assertEqual(result['daily_packet']['status'], 'held')
        self.assertIn('Writing source held', result['daily_packet']['problem'])

    def test_lost_bridge_readback_resumes_same_payload_and_pdf_without_duplicate_create(self):
        _, saved, _ = self.saved_ca()
        with patch.object(writing, '_verified_receipt', side_effect=archive.Held('Readback not confirmed')):
            held = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(held['status'], 'held')
        count = len(self.drive.created)
        prepared = deepcopy(next(d.value for d in self.port.rows.values() if d.value.get('schema') == writing.VERSION)['payload'])
        result = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(len(self.drive.created), count)
        self.assertEqual(next(d.value for d in self.port.rows.values() if d.value.get('schema') == writing.VERSION)['payload'], prepared)
        self.assertEqual(self.call.call_count, 1)

    def manual_report(self, saved, stamp='2026-10-09T15:00:00Z'):
        student = self.port.rows[saved['archive']['student_key']].value
        payload, pdf = writing.prepare(saved, student, '2026-10-07', prepared_at=stamp)
        result = archive.file_science_writing('read', 'write', self.config, payload, pdf)
        self.assertEqual(result['status'], 'verified', result)
        return payload, result

    def test_existing_assessed_writing_is_reused_without_new_preparation_or_filing(self):
        _, saved, _ = self.saved_ca()
        _, existing = self.manual_report(saved)
        count = len(self.drive.created)
        with patch.object(writing, 'prepare', side_effect=AssertionError('No duplicate preparation')), \
                patch.object(archive, 'file_science_writing', side_effect=AssertionError('No duplicate filing')):
            result = writing.ensure_saved('read', 'write', self.config, saved['id'])
            replay = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(result['reused_existing'])
        self.assertEqual(replay['drive_file_id'], existing['drive_file_id'])
        self.assertEqual(len(self.drive.created), count)

    def test_legacy_or_wrong_assessed_source_proof_holds_instead_of_guessing_coverage(self):
        _, saved, _ = self.saved_ca()
        _, existing = self.manual_report(saved)
        artifact = next(d.value for d in self.port.rows.values() if d.value.get('drive_file_id') == existing['drive_file_id'])
        baseline = deepcopy(artifact['assessed_sources'])
        for issue in ('legacy', 'revision', 'event', 'payload'):
            artifact['assessed_sources'] = deepcopy(baseline)
            if issue == 'legacy': artifact.pop('assessed_sources')
            if issue == 'revision': artifact['assessed_sources']['sources'][0]['source_version'] = '999'
            if issue == 'event': artifact['assessed_sources']['sources'][0]['source_response_id'] = 'WrongEvent'
            if issue == 'payload': artifact['assessed_sources']['payload_sha256'] = '0' * 64
            count = len(self.drive.created)
            result = writing.ensure_saved('read', 'write', self.config, saved['id'])
            with self.subTest(issue=issue):
                self.assertEqual(result['status'], 'held', result)
                self.assertIn('unverified assessed coverage', result['problem'])
                self.assertEqual(len(self.drive.created), count)

    def test_multiple_current_assessments_hold_without_choosing_a_report(self):
        _, saved, _ = self.saved_ca()
        self.manual_report(saved)
        self.manual_report(saved, stamp='2026-10-09T16:00:00Z')
        count = len(self.drive.created)
        result = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('Multiple current writing reports', result['problem'])
        self.assertEqual(len(self.drive.created), count)

    def test_former_student_does_not_receive_a_new_current_writing_report(self):
        _, saved, _ = self.saved_ca()
        count = len(self.drive.created)
        with patch.object(archive, 'report_roster', return_value=[]):
            result = writing.ensure_saved('read', 'write', self.config, saved['id'])
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('outside the current report roster', result['problem'])
        self.assertEqual(len(self.drive.created), count)
