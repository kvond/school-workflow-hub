"""Private teacher report links are configuration-only handoffs."""
import unittest
from unittest.mock import patch

from hub_v2 import app, google, native_feedback


class TeacherRecordLinksTests(unittest.TestCase):
    def test_feedback_catalog_is_available_when_unrelated_agenda_is_unavailable(self):
        expected = {'configured': True, 'date': '2026-10-07', 'files': []}
        with patch.object(google, 'service_token', return_value='read'), \
                patch.object(app, 'load_agenda', side_effect=AssertionError('Unrelated Agenda read')), \
                patch.object(native_feedback, 'today_files', return_value=expected) as catalog:
            self.assertEqual(app.api('api/feedback/today', {'date': ['2026-10-07']}, {}), expected)
        catalog.assert_called_once_with('read', {}, date_et='2026-10-07')

    def test_separate_records_and_analysis_are_validated_without_school_reads(self):
        base = 'https://docs.google.com/spreadsheets/d/FictionalBiologyReport123/edit'
        analysis = 'https://docs.google.com/spreadsheets/d/FictionalAnalysis12345/edit#gid=2'
        index = 'https://docs.google.com/spreadsheets/d/FictionalWorkIndex12345/edit'
        checklist = 'https://docs.google.com/spreadsheets/d/FictionalChecklist12345/edit'
        config = {'HUB_GROWTH_REPORT_URL': base, 'HUB_GROWTH_TEACHING_URL': base + '#gid=1',
                  'HUB_GROWTH_REPORT_AS_OF': '2026-10-09', 'HUB_BIOLOGY_ANALYSIS_URL': analysis,
                  'HUB_STUDENT_WORK_INDEX_URL': index, 'HUB_FEEDBACK_CHECKLIST_URL': checklist}
        with patch.object(google, 'service_token', side_effect=AssertionError('School read')):
            result = app.api('api/learning-report', {}, config)
        self.assertEqual(result['class_key'], 'biology')
        self.assertEqual(result['analysis_url'], analysis)
        self.assertEqual(result['work_index_url'], index)
        self.assertEqual(result['checklist_url'], checklist)
        self.assertEqual(result['teaching_url'], base + '#gid=1')
        self.assertFalse(result['automatic_refresh'])

    def test_unsafe_or_non_sheet_links_are_not_exposed(self):
        base = 'https://docs.google.com/spreadsheets/d/FictionalBiologyReport123/edit'
        result = app.learning_report({'HUB_GROWTH_REPORT_URL': base,
            'HUB_BIOLOGY_ANALYSIS_URL': 'javascript:alert(1)',
            'HUB_STUDENT_WORK_INDEX_URL': base + '?sharing=public',
            'HUB_FEEDBACK_CHECKLIST_URL': 'https://example.test/checklist',
            'HUB_GROWTH_TEACHING_URL': 'https://docs.google.com/spreadsheets/d/AnotherWorkbook12345/edit'})
        self.assertEqual(result['analysis_url'], '')
        self.assertEqual(result['work_index_url'], '')
        self.assertEqual(result['checklist_url'], '')
        self.assertEqual(result['teaching_url'], base)
