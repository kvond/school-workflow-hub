"""Reports retain denominators, source context and actual missing evidence."""
import unittest
from copy import deepcopy
from unittest.mock import patch
from hub_v2 import teaching_report as report
from hub_v2 import google
from school_workflow_hub.core.cloud_state import LeaseLost
from tests.test_cloud_state import MemoryCAS


class TeachingReportTests(unittest.TestCase):
    def observation(self, student='one', **changes):
        return {'student_key': student, 'lesson_key': 'lesson', 'dimension': report.DIMENSIONS[0],
                'date': '2026-10-01', 'level': 'Proficient', 'quote': 'Specific original answer',
                'source_url': 'https://fictional.example/original', **changes}

    def test_class_counts_each_student_once_and_keeps_missing_separate(self):
        scope = {'lesson_key': 'lesson', 'dimension': report.DIMENSIONS[0]}
        observations = [self.observation(), self.observation(date='2026-09-29', level='Beginning'),
                        self.observation('two', level='Evidence unavailable', quote='')]
        value = report.class_measures(observations, ['one', 'two', 'three'], [scope])[0]
        self.assertEqual((value['active'], value['rated'], value['evidence_unavailable'], value['not_assessed']), (3, 1, 1, 1))
        self.assertEqual((value['Beginning'], value['proficient_plus']), (0, 1))
        self.assertEqual(value['proficient_plus_rated'], 1)

    def test_withdrawn_history_is_retained_but_not_in_current_class_denominator(self):
        selected = report.latest_observations([self.observation('retained'), self.observation()], ['one'])
        self.assertEqual([o['student_key'] for o in selected], ['one'])

    def test_conflicting_duplicate_or_invented_date_or_quote_holds(self):
        for records in ([self.observation(), self.observation(level='Beginning')],
                        [self.observation(date='unknown')], [self.observation(quote='')]):
            with self.assertRaises(report.Held): report.latest_observations(records, ['one'])

    def test_unassessed_dimension_is_not_zero_proficient_evidence(self):
        value = report.class_measures([], ['one'], [{'lesson_key': 'lesson', 'dimension': report.DIMENSIONS[2]}])[0]
        self.assertEqual(value['not_assessed'], 1)
        self.assertEqual(value['rated'], 0)
        self.assertIsNone(value['proficient_plus_rated'])

    def saved(self, **changes):
        rows = [{'dimension': d, 'level': 'Proficient', 'question_id': 'question',
                 'stage': 'DRAFT', 'quote': 'Carbon becomes plant matter.',
                 'explanation': 'Connects the air carbon to plant matter.'} for d in report.DIMENSIONS]
        return {'saved_at': '2026-10-10T01:00:00+00:00', 'id': 'saved',
                'scope': {'class': 'biology'}, 'source': {'file': {'id': 'original', 'version': '8'}},
                'report': {'teacher_dimensions': rows}, **changes}

    def test_future_saved_dimensions_keep_original_date_and_never_reassess_old_reports(self):
        rows = report.native_observations(self.saved(), 'person', '2026-09-30', lesson_key='lesson')
        self.assertEqual([r['dimension_key'] for r in rows], ['vocabulary', 'conceptual', 'evidence', 'reasoning'])
        self.assertTrue(all(r['date'] == '2026-09-30' and r['assessed_at'] == '2026-10-09' for r in rows))
        self.assertEqual(report.native_observations(self.saved(report={'aspects': []}), 'person',
                         '2026-09-30', lesson_key='lesson'), [])

    def test_unsaved_or_mislabeled_or_unbound_native_evidence_cannot_change_counts(self):
        invalid = [self.saved(saved_at=''), self.saved(scope={'class': 'forensics'}),
                   self.saved(source={'file': {'id': 'original'}})]
        changed = self.saved(); changed['report']['teacher_dimensions'][1]['dimension'] = report.DIMENSIONS[0]
        invalid.append(changed)
        for value in invalid:
            with self.assertRaises(report.Held):
                report.native_observations(value, 'person', '2026-09-30', lesson_key='lesson')

    def test_same_source_new_revision_updates_current_observation_without_changing_work_date(self):
        first = self.observation(source_file_id='same', source_revision='7')
        revised = self.observation(source_file_id='same', source_revision='8', level='Developing')
        selected = report.latest_observations([revised, first], ['one'])
        self.assertEqual(selected, [revised])
        self.assertEqual(selected[0]['date'], '2026-10-01')
        with self.assertRaises(report.Held):
            report.latest_observations([first, {**revised, 'source_file_id': 'other'}], ['one'])


class PersistentWritingReportTests(TeachingReportTests):
    config = {'HUB_BIOLOGY_ANALYSIS_URL': 'https://docs.google.com/spreadsheets/d/FictionalAnalysis00001/edit',
              'HUB_GROWTH_REPORT_URL': 'https://docs.google.com/spreadsheets/d/FictionalDTGSS000001/edit'}

    def setUp(self):
        self.port = MemoryCAS()
        self.patch = patch.object(report, 'configured_store', return_value=self.port); self.patch.start()
        self.addCleanup(self.patch.stop)
        self.rows = [{**self.observation(), 'dimension': d, 'source_file_id': 'original', 'source_revision': '7',
                      'assessed_at': '2026-10-09', 'lesson': 'Fictional carbon lesson'} for d in report.DIMENSIONS]
        self.active = [{'student_key': 'one', 'name': 'Rivera, Ana', 'section': 'A4 Biology'},
                       {'student_key': 'two', 'name': 'Bennett, Ben', 'section': 'A4 Biology'}]

    def test_immutable_baseline_and_saved_future_batch_are_idempotent(self):
        first = report.save_observations(self.config, self.rows, baseline=True)
        self.assertEqual(report.save_observations(self.config, self.rows, baseline=True), first)
        future = [{**o, 'source_revision': '8', 'level': 'Developing', 'saved_feedback_id': 'future'} for o in self.rows]
        report.save_observations(self.config, future)
        self.assertEqual(len(report.read_observations(self.config)), 8)
        tables = report.writing_tables(report.read_observations(self.config), self.active)
        self.assertEqual(len(tables['Student evidence']), 9)
        self.assertEqual(tables['Whole class'][1][3:10], [0, 1, 0, 0, 0, 1, 1])
        with self.assertRaises(report.Held):
            report.save_observations(self.config, [{**o, 'level': 'Beginning'} for o in self.rows], baseline=True)

    def test_missing_private_batch_is_not_silently_a_zero(self):
        saved = report.save_observations(self.config, self.rows, baseline=True)
        del self.port.rows[saved['batch_key']]
        with self.assertRaises(report.Held): report.read_observations(self.config)

    def sheet(self, title, sid, rows):
        return {'properties': {'title': title, 'sheetId': sid, 'gridProperties': {'rowCount': 1000}},
                'data': [{'rowData': [{'values': [{'userEnteredValue': {'numberValue': x} if isinstance(x, (int, float))
                    else {'stringValue': x}} for x in r]} for r in rows]}]}

    def fake_books(self):
        historic = ['Fictional carbon lesson', 'Historic critical aspect', 2, 1, 0, 0, 0, '', 1, 1, 0, 0, 0, .5,
                    'Historical work', '2026-10-01', 'Saved assessment']
        summary = [['Report', 'Prepared 2026-10-01'], ['Current class', 2], ['Located', 0], ['Files', 0],
                   ['Original versions', 1], ['Method', 'Current understanding'], ['Detail', 'Link'], report.MEASURE_HEADER, historic]
        analysis = {'sheets': [self.sheet('Student evidence', 1, [report.WRITING_HEADER]),
                               self.sheet('Whole class', 2, [report.MEASURE_HEADER])]}
        dtgss = {'sheets': [self.sheet('Report', 3, summary), self.sheet('Lesson measures', 4, [report.MEASURE_HEADER, historic]),
                            self.sheet('Teaching plan', 5, [['Done', 'Teacher note'], ['TRUE', 'Keep my own teaching action']]),
                            self.sheet('Guide', 6, [['Guide', 'Biology only']])]}
        return {'FictionalAnalysis00001': analysis, 'FictionalDTGSS000001': dtgss}

    def run_refresh(self, *, public=False, lose_response=False, fail_write=False, expire_before_write=False):
        report.save_observations(self.config, self.rows, baseline=True)
        books = self.fake_books(); calls = []; self.calls = calls
        def meta(token, ident, fields):
            return {'id': ident, 'trashed': False, 'mimeType': 'application/vnd.google-apps.spreadsheet',
                    'capabilities': {'canEdit': True}, 'permissions': [{'type': 'anyone'}] if public
                    else [{'type': 'user', 'emailAddress': 'teacher@fictional.example'}]}
        def api(url, token, params=None, method='GET', body=None):
            ident = url.split('/spreadsheets/')[1].split(':')[0]
            if method == 'GET' and expire_before_write and ident == 'FictionalAnalysis00001':
                self.port.advance(121)
            if method == 'POST':
                calls.append((ident, deepcopy(body)))
                if fail_write: raise google.GoogleError('No confirmed Sheet write', 503)
                for request in body['requests']:
                    if 'updateCells' not in request: continue
                    value = request['updateCells']; sid = value['range']['sheetId']
                    sheet = next(s for s in books[ident]['sheets'] if s['properties']['sheetId'] == sid)
                    sheet['data'] = [{'rowData': value['rows']}]
                if lose_response: raise google.GoogleError('Lost response', 503)
            return deepcopy(books[ident])
        with patch.object(report.google, 'drive_file', side_effect=meta), patch.object(report.google, 'get_json', side_effect=api):
            result = report.refresh_writing_tables('read', 'write', self.config, self.active)
        return result, books, calls

    def test_refresh_preserves_manual_actions_and_confirms_lost_response_without_post_retry(self):
        result, books, calls = self.run_refresh(lose_response=True)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(len(calls), 2)
        edited = {r['updateCells']['range']['sheetId'] for _, body in calls for r in body['requests'] if 'updateCells' in r}
        self.assertEqual(edited, {1, 2, 3, 4})
        self.assertEqual(report._sheet_rows(books['FictionalDTGSS000001']['sheets'][2])[1], ['TRUE', 'Keep my own teaching action'])

    def test_public_destination_or_unconfirmed_write_holds(self):
        with self.assertRaises(report.student_archive.Held): self.run_refresh(public=True)
        self.port.advance(121)
        with self.assertRaises(report.Held): self.run_refresh(fail_write=True)

    def test_formatting_only_blank_rows_do_not_become_roster_or_measure_evidence(self):
        sheet = self.sheet('Test', 1, [['Meaning', 0], ['', ''], ['', '']])
        self.assertEqual(report._sheet_rows(sheet), [['Meaning', 0]])

    def test_observed_server_lease_loss_prevents_any_sheet_mutation(self):
        with self.assertRaises(LeaseLost): self.run_refresh(expire_before_write=True)
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
