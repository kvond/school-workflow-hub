"""Fictional configured links and run rows; no school sources or writes."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import dashboard_records as dashboard, google, morning

SHEET = 'https://docs.google.com/spreadsheets/d/FictionalOperations001/edit'
HEADERS = ['Run ID', 'Work / purpose', 'Actual outcome', 'What completed',
           'Unfinished scope', 'Results link', 'Evidence / details link', 'Started at', 'Finished at',
           'Recorded date / time ET', 'Next action', 'Owner']
ROWS = [['Run history'], ['Description'], [], [], HEADERS,
        ['RUN-1', 'Feedback filing', 'Partial', 'Verified saved reports', 'Some work remains',
         'https://drive.google.com/drive/folders/FictionalReports001', '', '2026-10-20T11:30:00Z', '2026-10-20T11:35:00Z',
         '2026-10-20T08:00:00-04:00', 'Check remaining sources', 'Existing task owner']]


class DashboardRecordsTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(google, 'service_token', return_value='fictional').start()
        self.tabs = patch.object(google, 'sheet_tabs', return_value=('Operations', {'Run history': 1})).start()
        self.metadata = patch.object(google, 'get_json', return_value={'sheets': [{'properties': {
            'title': 'Run history', 'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}]}).start()
        self.values = patch.object(google, 'sheet_values', return_value=deepcopy(ROWS)).start()
        self.helper = patch.object(morning, 'reports', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_unconfigured_dashboard_does_no_source_or_authentication_work(self):
        result = dashboard.records({})
        self.assertEqual(result['runs']['status'], 'not_configured')
        self.assertTrue(all(item['url'] is None and not item['configured'] for item in result['records']))
        for mock in (self.token, self.tabs, self.metadata, self.values, self.helper):
            mock.assert_not_called()

    def test_only_exact_vetted_links_and_explicit_no_token_without_run_sources(self):
        config = {'HUB_AGENDA_SPREADSHEET_ID': 'FictionalAgenda001',
                  'HUB_FEEDBACK_CHECKLIST_URL': SHEET + '?gid=12#gid=12',
                  'HUB_MODEL_TRIALS_FOLDER_URL': 'https://drive.google.com/drive/folders/FictionalTrials001',
                  'HUB_SYSTEM_NOTES_SLACK_URL': 'https://app.slack.com/client/T12345/C12345'}
        result = {item['key']: item for item in dashboard.records(config)['records']}
        self.assertTrue(result['agenda']['configured'])
        self.assertTrue(result['feedback_checklist']['configured'])
        self.assertTrue(result['model_trials_folder']['configured'])
        self.assertTrue(result['system_notes']['configured'])
        self.token.assert_not_called()
        for bad in ('https://docs.google.com.evil.example/spreadsheets/d/FictionalPrivate001/edit',
                    'https://user:secret@docs.google.com/spreadsheets/d/FictionalPrivate001/edit',
                    SHEET + '?token=private', SHEET + '#bad', 'javascript:alert(1)', 'http://docs.google.com/x'):
            config.update(HUB_OPERATIONS_URL=bad, HUB_SCORES_ENTERED_SPREADSHEET_ID='invalid')
            output = dashboard.records(config)
            self.assertEqual(output['runs']['status'], 'not_configured')
            self.assertNotIn('FictionalPrivate001', str(output))
        self.token.assert_not_called()

    def test_account_specific_drive_folder_links_do_not_trigger_source_reads(self):
        for url in ('https://drive.google.com/drive/folders/FictionalTrials001',
                    'https://drive.google.com/drive/u/0/folders/FictionalTrials001',
                    'https://drive.google.com/drive/u/1/folders/FictionalTrials001/'):
            with self.subTest(url=url):
                records = dashboard.records({'HUB_MODEL_TRIALS_FOLDER_URL': url})['records']
                folder = next(item for item in records if item['key'] == 'model_trials_folder')
                self.assertTrue(folder['configured'])
                self.assertEqual(folder['url'], url)
        for url in ('https://drive.google.com.evil.example/drive/u/0/folders/FictionalTrials001',
                    'https://user:secret@drive.google.com/drive/u/0/folders/FictionalTrials001',
                    'https://drive.google.com/drive/u/0/folders/FictionalTrials001?token=secret',
                    'https://drive.google.com/drive/u/0/folders/FictionalTrials001/other',
                    'http://drive.google.com/drive/u/0/folders/FictionalTrials001'):
            with self.subTest(url=url):
                records = dashboard.records({'HUB_MODEL_TRIALS_FOLDER_URL': url})['records']
                folder = next(item for item in records if item['key'] == 'model_trials_folder')
                self.assertFalse(folder['configured'])
                self.assertIsNone(folder['url'])
        for mock in (self.token, self.metadata, self.values, self.helper):
            mock.assert_not_called()

    def test_operations_reads_header_row_after_title_and_only_known_fields(self):
        output = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
        self.assertEqual(output['runs']['status'], 'available')
        item = output['runs']['items'][0]
        self.assertEqual(set(item), {'id', 'work', 'status', 'started_at', 'finished_at', 'summary', 'completed',
                                    'remaining', 'unfinished', 'next_action', 'owner', 'recorded_at', 'links'})
        self.assertEqual(item['status'], 'partial')
        self.assertEqual(item['remaining'], None)
        self.assertEqual(item['completed'], None)
        self.assertEqual(item['links'][0]['label'], 'Results')
        self.assertEqual(item['started_at'], '2026-10-20T11:30:00+00:00')
        self.assertEqual(item['recorded_at'], '2026-10-20T08:00:00-04:00')
        self.assertEqual(item['unfinished'], 'Some work remains')
        self.assertEqual(item['next_action'], 'Check remaining sources')
        self.assertEqual(item['owner'], 'Existing task owner')
        self.values.assert_called_once_with('fictional', 'FictionalOperations001', "'Run history'!A1:Z1000")
        self.helper.assert_not_called()

    def test_malformed_partial_ambiguous_or_error_reads_are_unavailable(self):
        for rows in ([[]], ROWS + [['RUN-1', 'Duplicate', 'Completed']],
                     [['Run ID', 'Work', 'Status', 'Outcome']], [[]] * 1001,
                     [['x'] * 31], [HEADERS, ['RUN-2', 'Feedback', 'Unknown']],
                     [HEADERS, ['RUN-2', 'Feedback', 'Completed', 'token=private']]):
            self.values.return_value = rows
            result = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
            self.assertEqual(result['runs']['status'], 'unavailable', rows[:2])
            self.assertEqual(result['runs']['items'], [])
            self.assertNotIn('token=private', str(result))
        self.values.side_effect = google.GoogleError('private provider detail token=secret')
        output = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
        self.assertNotIn('secret', str(output))
        self.assertEqual(output['runs']['status'], 'unavailable')

    def test_header_only_is_available_empty_not_a_completed_run(self):
        self.values.return_value = [HEADERS]
        result = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
        self.assertEqual(result['runs']['status'], 'available')
        self.assertEqual(result['runs']['items'], [])

    def test_sparse_later_rows_are_read_and_oversized_grid_is_unavailable(self):
        self.values.return_value = [HEADERS] + [[]] * 599 + [['LATER', 'Filing', 'Completed']]
        result = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
        self.assertEqual(result['runs']['items'][0]['id'], 'LATER')
        self.values.reset_mock()
        self.metadata.return_value['sheets'][0]['properties']['gridProperties']['rowCount'] = 2001
        self.assertEqual(dashboard.records({'HUB_OPERATIONS_URL': SHEET})['runs']['status'], 'unavailable')
        self.values.assert_not_called()

    def test_recorded_prose_is_not_a_start_or_finish_and_thread_links_are_vetted(self):
        row = ['RUN-3', 'Filing', 'Partial', 'Reports saved', 'One source held', '',
               'https://fictional-school.slack.com/archives/C12345/p1234567890123456', '', '', 'October 20, 8 a.m. ET']
        self.values.return_value = [HEADERS, row]
        item = dashboard.records({'HUB_OPERATIONS_URL': SHEET})['runs']['items'][0]
        self.assertIsNone(item['started_at'])
        self.assertIsNone(item['finished_at'])
        self.assertIsNone(item['recorded_at'])
        self.assertEqual(item['links'][0]['label'], 'Evidence')

    def test_helper_projection_is_counts_only_and_keeps_missing_timestamps_unknown(self):
        self.helper.return_value = [{'day': '2026-10-20', 'run_id': '00000000-0000-4000-8000-000000000001',
            'code': 'finished', 'roster': 'unavailable', 'items': [{'assignment': 'Fictional private assignment',
                'code': 'read_back', 'filled': 1, 'raised': 2, 'kept': 3, 'left': 0,
                'reasons': {'flag': 0, 'excused': 0, 'check': 0, 'name': 0, 'missing': 0}}]}]
        result = dashboard.records({'HUB_SCORES_ENTERED_SPREADSHEET_ID': 'FictionalLedger001'})
        item = result['runs']['items'][0]
        self.assertEqual((item['status'], item['completed'], item['remaining']), ('completed', 1, 0))
        self.assertIsNone(item['started_at'])
        self.assertIsNone(item['finished_at'])
        self.assertNotIn('Fictional private assignment', str(result))
        self.tabs.assert_not_called()

    def test_one_source_failure_preserves_other_items_but_not_full_availability(self):
        self.helper.side_effect = RuntimeError('private unavailable')
        output = dashboard.records({'HUB_OPERATIONS_URL': SHEET,
                                    'HUB_SCORES_ENTERED_SPREADSHEET_ID': 'FictionalLedger001'})
        self.assertEqual(output['runs']['status'], 'unavailable')
        self.assertEqual(len(output['runs']['items']), 1)
        self.assertEqual(output['runs']['sources'], {'operations': 'available', 'helper': 'unavailable'})
        self.assertNotIn('private unavailable', str(output))

    def test_full_operations_history_does_not_hide_helper_receipts(self):
        self.values.return_value = [HEADERS] + [[f'RUN-{n}', 'Filing', 'Completed'] for n in range(25)]
        self.helper.return_value = [{'day': '2026-10-20', 'run_id': '00000000-0000-4000-8000-000000000001',
                                    'code': 'running', 'roster': 'unavailable', 'items': []}]
        output = dashboard.records({'HUB_OPERATIONS_URL': SHEET,
                                    'HUB_SCORES_ENTERED_SPREADSHEET_ID': 'FictionalLedger001'})
        self.assertEqual(output['runs']['status'], 'available')
        self.assertEqual(len(output['runs']['items']), 11)
        self.assertEqual(output['runs']['items'][0]['work'], 'Helper score entry')

    def test_token_failure_has_fixed_unavailable_status_only(self):
        self.token.side_effect = RuntimeError('token=private-secret')
        output = dashboard.records({'HUB_OPERATIONS_URL': SHEET})
        self.assertEqual(output['runs']['status'], 'unavailable')
        self.assertNotIn('private-secret', str(output))
        self.values.assert_not_called()


if __name__ == '__main__':
    unittest.main()
