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
        self.assertTrue(all(row['date'] == '2026-10-01' and row['assessed_at'] == '2026-10-09'
                            and row['source_date_basis'] == 'source_modified'
                            and row['submitted_at'] == saved['submitted_at'] for row in rows))
        refresh.assert_called_once_with('read', 'write', config, roster)

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
