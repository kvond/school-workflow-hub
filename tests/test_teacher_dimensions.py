"""Science-writing reports retain old call reservations and exact answer evidence."""
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from hub_v2 import feedback_pdf, native_feedback as fb, student_archive, teaching_report
from school_workflow_hub.adapters import assessment_provider
from school_workflow_hub.core.cloud_state import key
from school_workflow_hub.core.runs import content_hash
from tests.test_cloud_state import MemoryCAS
from tests.test_assessment_provider import OPENAI_CONFIG
from tests.test_native_feedback import inputs, report


def legacy_fingerprint(value):
    return {'version': fb.VERSION, 'scope': {k: v for k, v in value['scope'].items() if k != 'week'},
        'person': value['person'], 'assignment': value['assignment'],
        'source': value['source']['sha256'], 'reference': value['reference']['sha256'],
        'prompt': content_hash([fb.LEGACY_POLICY, value['prompt']]),
        'model': assessment_provider.model_name(OPENAI_CONFIG)}


class TeacherDimensionsTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.storage = patch.object(fb, 'configured_store', return_value=self.port)
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def test_unchanged_legacy_saved_report_reuses_its_id_without_any_call(self):
        value = inputs()
        ident = key('feedbackdraft', legacy_fingerprint(value))
        legacy = report()
        legacy.pop('teacher_dimensions')
        stored = {'schema': fb.VERSION, 'id': ident, 'scope': value['scope'], 'person': value['person'],
            'assignment': value['assignment'], 'source': value['source'], 'report': legacy,
            'created_at': '2026-10-07T10:00:00Z', 'saved_at': '2026-10-07T10:01:00Z', 'usage': {}}
        self.port.commit({ident: (stored, None)})
        with patch.object(fb, '_call', side_effect=AssertionError('Repeated provider call')):
            result = fb.generate(OPENAI_CONFIG, value)
        self.assertEqual(result['id'], ident)
        self.assertEqual(result['saved_at'], stored['saved_at'])
        self.assertNotIn('teacher_dimensions', result['report'])

    def test_uncertain_legacy_call_blocks_upgrade_instead_of_rebilling(self):
        ident = key('feedbackcall', legacy_fingerprint(inputs()))
        self.port.commit({ident: ({'status': 'started', 'at': '2026-10-07T10:00:00Z'}, None)})
        with patch.object(fb, '_call', side_effect=AssertionError('Reservation bypass')):
            with self.assertRaisesRegex(fb.Held, 'earlier call was interrupted'):
                fb.generate(OPENAI_CONFIG, inputs())

    def test_returned_legacy_call_can_be_recovered_without_new_dimensions_or_call(self):
        fingerprint = legacy_fingerprint(inputs())
        legacy = report()
        legacy.pop('teacher_dimensions')
        self.port.commit({key('feedbackcall', fingerprint): ({'status': 'returned',
            'text': json.dumps(legacy), 'usage': {}}, None)})
        with patch.object(fb, '_call', side_effect=AssertionError('Legacy response replay')):
            result = fb.generate(OPENAI_CONFIG, inputs())
        self.assertEqual(result['id'], key('feedbackdraft', fingerprint))
        self.assertNotIn('teacher_dimensions', result['report'])

    def test_new_source_requires_all_dimensions_and_exact_attributable_quotes(self):
        invalid = report()
        invalid['teacher_dimensions'][0]['quote'] = 'Invented scientific response'
        with self.assertRaisesRegex(fb.Held, 'named answer field'):
            fb.validate_report(invalid, inputs(), require_teacher_dimensions=True)
        missing = report()
        missing.pop('teacher_dimensions')
        with self.assertRaises(fb.Held):
            fb.validate_report(missing, inputs(), require_teacher_dimensions=True)
        ambiguous = inputs()
        ambiguous['answers'] = [{'id': 'CA1-MASTERY', 'category': 'DRAFT',
            'answer': 'Carbon comes from the air.'}] * 2
        with self.assertRaisesRegex(fb.Held, 'named answer field'):
            fb.validate_report(report(), ambiguous, require_teacher_dimensions=True)

    def test_fresh_feedback_keeps_teacher_dimensions_off_student_pdf(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})) as call:
            draft = fb.generate(OPENAI_CONFIG, inputs())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(draft['report']['teacher_dimensions']), 4)
        value = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        from pypdf import PdfReader
        text = PdfReader(io.BytesIO(feedback_pdf.render(value))).pages[0].extract_text()
        self.assertNotIn('Private science-writing evidence', text)
        self.assertIn('HOW I CAN IMPROVE', text)

    def saved_value(self):
        value = inputs()
        value['source']['file']['version'] = '42'
        value['submitted_at'] = '2026-09-30T15:00:00Z'
        value['submission_receipt'] = {
            'source_file_id': value['scope']['file'], 'source_form_id': value['scope']['form'],
            'source_response_id': 'FictionalResponse12345', 'assignment_form_id': value['scope']['form'],
            'submitted_at': value['submitted_at']}
        draft = fb.generate(OPENAI_CONFIG, value)
        saved = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        saved['saved_at'] = '2026-10-09T11:00:00Z'
        saved['archive'] = {'status': 'verified', 'student_key': 'fictional-student'}
        return saved

    def test_saved_dimensions_refresh_existing_teacher_tables_with_original_work_dates(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'https://docs.google.com/spreadsheets/d/FictionalAnalysis12345/edit',
                  'HUB_GROWTH_REPORT_URL': 'https://docs.google.com/spreadsheets/d/FictionalReport12345/edit'}
        roster = [{'student_key': 'fictional-student', 'name': 'Ana R.', 'section': 'A4 Biology'}]
        with patch.object(student_archive, 'report_roster', return_value=roster), \
                patch.object(teaching_report, 'save_observations', return_value={'observations': 4}) as persist, \
                patch.object(teaching_report, 'refresh_writing_tables', return_value={'status': 'verified'}) as refresh, \
                patch.object(fb, '_call', side_effect=AssertionError('Report rerating')):
            result = fb._refresh_teacher_analysis(config, saved, 'read', 'write')
        self.assertEqual(result['status'], 'verified')
        rows = persist.call_args.args[1]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row['date'] == '2026-09-30' and row['assessed_at'] == '2026-10-09'
                            and row['source_date_basis'] == 'form_receipt'
                            and row['submitted_at'] == saved['submitted_at'] for row in rows))
        refresh.assert_called_once_with('read', 'write', config, roster)

    def test_later_code_repair_modified_time_cannot_create_a_new_work_date(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        saved['source']['file']['modified_at'] = '2026-10-10T18:00:00Z'
        before = deepcopy(saved)
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'configured', 'HUB_GROWTH_REPORT_URL': 'configured'}
        with patch.object(student_archive, 'report_roster', return_value=[{'student_key': 'fictional-student'}]), \
                patch.object(teaching_report, 'save_observations', return_value={'observations': 4}) as persist, \
                patch.object(teaching_report, 'refresh_writing_tables', return_value={'status': 'verified'}), \
                patch.object(fb, '_call', side_effect=AssertionError('Report rerating')):
            result = fb._refresh_teacher_analysis(config, saved, 'read', 'write')
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(all(row['date'] == '2026-09-30' for row in persist.call_args.args[1]))
        self.assertEqual(saved, before)

    def test_missing_unverified_or_mismatched_receipt_holds_reporting_and_preserves_saved_pdf(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'configured', 'HUB_GROWTH_REPORT_URL': 'configured'}
        changes = [lambda value: value.pop('submission_receipt'),
                   lambda value: value['submission_receipt'].pop('source_response_id'),
                   lambda value: value['submission_receipt'].update(source_file_id='DifferentFile'),
                   lambda value: value['submission_receipt'].update(assignment_form_id='DifferentForm'),
                   lambda value: value['submission_receipt'].update(submitted_at='2026-09-30T15:00:00'),
                   lambda value: value.update(submitted_at='2026-10-10T18:00:00Z')]
        for change in changes:
            value = deepcopy(saved); change(value); before = deepcopy(value)
            with self.subTest(change=change), \
                    patch.object(student_archive, 'report_roster', return_value=[{'student_key': 'fictional-student'}]), \
                    patch.object(teaching_report, 'save_observations') as persist, \
                    patch.object(teaching_report, 'refresh_writing_tables') as refresh, \
                    patch.object(fb, '_call', side_effect=AssertionError('Report rerating')):
                result = fb._refresh_teacher_analysis(config, value, 'read', 'write')
                self.assertEqual(result['status'], 'held')
                persist.assert_not_called(); refresh.assert_not_called()
            self.assertEqual(value, before)

    def test_original_receipt_uses_eastern_work_day_separately_from_assessment_day(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        saved['submitted_at'] = '2026-10-01T00:15:00Z'
        saved['submission_receipt']['submitted_at'] = saved['submitted_at']
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'configured', 'HUB_GROWTH_REPORT_URL': 'configured'}
        with patch.object(student_archive, 'report_roster', return_value=[{'student_key': 'fictional-student'}]), \
                patch.object(teaching_report, 'save_observations', return_value={'observations': 4}) as persist, \
                patch.object(teaching_report, 'refresh_writing_tables', return_value={'status': 'verified'}):
            fb._refresh_teacher_analysis(config, saved, 'read', 'write')
        self.assertTrue(all(row['date'] == '2026-09-30' and row['assessed_at'] == '2026-10-09'
                            for row in persist.call_args.args[1]))

    def test_fresh_same_event_receipt_backfills_saved_metadata_without_rebilling_or_delivery_changes(self):
        current = inputs(); current['submitted_at'] = '2026-09-30T15:00:00Z'
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            draft = fb.generate(OPENAI_CONFIG, current)
            fb.save(OPENAI_CONFIG, draft['id'], current)
        before = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        delivery = {'printed': True, 'handed_to_student': True}
        self.port.commit({'fictional-delivery-state': (delivery, None)})
        current['submission_receipt'] = {
            'source_file_id': current['scope']['file'], 'source_form_id': current['scope']['form'],
            'source_response_id': 'FictionalResponse12345', 'assignment_form_id': current['scope']['form'],
            'submitted_at': current['submitted_at']}
        with patch.object(fb, '_call', side_effect=AssertionError('Repeated provider call')):
            saved = fb.save(OPENAI_CONFIG, draft['id'], current)
            cached = fb.generate(OPENAI_CONFIG, current)
        after = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        self.assertEqual(after.pop('submission_receipt'), current['submission_receipt'])
        self.assertEqual(after, before)
        self.assertEqual(saved['saved_at'], before['saved_at'])
        self.assertEqual(cached['id'], draft['id']); self.assertTrue(cached['cached'])
        self.assertEqual(self.port.read(['fictional-delivery-state']).documents['fictional-delivery-state'].value, delivery)

    def test_different_event_cannot_relabel_unchanged_cached_legacy_report(self):
        current = inputs(); current['submitted_at'] = '2026-09-30T15:00:00Z'
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            draft = fb.generate(OPENAI_CONFIG, current)
            fb.save(OPENAI_CONFIG, draft['id'], current)
        before = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        current['submitted_at'] = '2026-10-10T15:00:00Z'
        current['submission_receipt'] = {
            'source_file_id': current['scope']['file'], 'source_form_id': current['scope']['form'],
            'source_response_id': 'FictionalNewResponse', 'assignment_form_id': current['scope']['form'],
            'submitted_at': current['submitted_at']}
        with patch.object(fb, '_call', side_effect=AssertionError('Repeated provider call')):
            fb.save(OPENAI_CONFIG, draft['id'], current)
        self.assertEqual(self.port.read([draft['id']]).documents[draft['id']].value, before)

    def test_legacy_unknown_original_timestamp_cannot_be_filled_from_a_later_current_receipt(self):
        current = inputs()
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            draft = fb.generate(OPENAI_CONFIG, current)
            fb.save(OPENAI_CONFIG, draft['id'], current)
        before = deepcopy(self.port.read([draft['id']]).documents[draft['id']].value)
        current['submitted_at'] = '2026-10-10T15:00:00Z'
        current['submission_receipt'] = {
            'source_file_id': current['scope']['file'], 'source_form_id': current['scope']['form'],
            'source_response_id': 'FictionalCurrentResponse', 'assignment_form_id': current['scope']['form'],
            'submitted_at': current['submitted_at']}
        with patch.object(fb, '_call', side_effect=AssertionError('Repeated provider call')):
            fb.save(OPENAI_CONFIG, draft['id'], current)
            fb.save(OPENAI_CONFIG, draft['id'], current)
        self.assertEqual(self.port.read([draft['id']]).documents[draft['id']].value, before)

    def test_invalid_receipt_is_held_before_generation_reservation_or_call(self):
        current = inputs(); current['submitted_at'] = '2026-09-30T15:00:00Z'
        current['submission_receipt'] = {
            'source_file_id': 'DifferentFile', 'source_form_id': current['scope']['form'],
            'source_response_id': 'FictionalResponse', 'assignment_form_id': current['scope']['form'],
            'submitted_at': current['submitted_at']}
        with patch.object(fb, '_call', side_effect=AssertionError('Invalid evidence provider call')):
            with self.assertRaises(fb.Held):
                fb.generate(OPENAI_CONFIG, current)
        self.assertEqual(self.port.rows, {})

    def test_existing_observation_batch_is_not_silently_rewritten_with_a_corrected_date(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'https://docs.google.com/spreadsheets/d/FictionalAnalysis12345/edit',
                  'HUB_GROWTH_REPORT_URL': 'https://docs.google.com/spreadsheets/d/FictionalReport12345/edit'}
        historic = teaching_report.native_observations(saved, 'fictional-student', '2026-10-01',
            lesson_key=saved['reference']['file']['id'], date_basis='source_modified', submitted_at=saved['submitted_at'])
        with patch.object(teaching_report, 'configured_store', return_value=self.port):
            prior = teaching_report.save_observations(config, historic)
            before = deepcopy(self.port.read([prior['batch_key']]).documents[prior['batch_key']].value)
            with patch.object(student_archive, 'report_roster', return_value=[{'student_key': 'fictional-student'}]), \
                    patch.object(teaching_report, 'refresh_writing_tables') as refresh, \
                    patch.object(fb, '_call', side_effect=AssertionError('Report rerating')):
                result = fb._refresh_teacher_analysis(config, saved, 'read', 'write')
                self.assertEqual(result['status'], 'held')
                self.assertIn('original evidence', result['problem'])
                self.assertIn('Biology / Ana R. / Fictional carbon lesson', result['problem'])
                refresh.assert_not_called()
        self.assertEqual(self.port.read([prior['batch_key']]).documents[prior['batch_key']].value, before)

    def test_teacher_refresh_hold_preserves_saved_pdf_and_legacy_report_is_not_rerated(self):
        with patch.object(fb, '_call', return_value=(json.dumps(report()), {})):
            saved = self.saved_value()
        config = {'HUB_BIOLOGY_ANALYSIS_URL': 'configured', 'HUB_GROWTH_REPORT_URL': 'configured'}
        with patch.object(student_archive, 'report_roster', side_effect=student_archive.Held('Fresh roster unavailable')), \
                patch.object(teaching_report, 'save_observations', side_effect=AssertionError('Unknown roster write')):
            result = fb._refresh_teacher_analysis(config, saved, 'read', 'write')
        self.assertEqual(result['status'], 'held')
        self.assertEqual(saved['saved_at'], '2026-10-09T11:00:00Z')
        self.assertEqual(saved['archive']['status'], 'verified')
        saved['report'].pop('teacher_dimensions')
        with patch.object(student_archive, 'report_roster', side_effect=AssertionError('Legacy rerating')):
            self.assertIsNone(fb._refresh_teacher_analysis(config, saved, 'read', 'write'))
