"""Fictional saved summaries; reads never assess or export student work."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from types import SimpleNamespace
from threading import Event, Thread
import unittest
from unittest.mock import patch

from hub_v2 import app, completion, google, saved_completion as saved
from school_workflow_hub.core.cloud_state import StoreUnavailable
from tests.test_cloud_state import MemoryCAS

FILE, TEACHER, FORM = 'FictionalStudentSource001', 'FictionalTeacherSource001', 'FictionalAssignmentForm001'
STAMP = '2026-10-20T10:00:00Z'


class SavedCompletionTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.port.now = datetime(2026, 10, 21, 1, tzinfo=timezone.utc)
        self.config = {'HUB_STATE_BACKEND': 'firestore'}
        self.lessons = [{'topic': 'Fictional carbon lesson', 'forms': [{'form_id': FORM}],
                         'materials': [{'url': 'https://docs.google.com/presentation/d/' + TEACHER + '/edit'}]}]
        self.agenda = SimpleNamespace(spreadsheet_id='FictionalAgendaSheet001', title='Fictional Agenda',
                                     lessons=lambda *_: deepcopy(self.lessons))
        self.form = {'form_id': FORM, 'students': [{'matched': True, 'email': 'fictional@school.example',
            'first': 'Fictional', 'last': 'Student', 'section': 'A4', 'submitted_at': STAMP,
            'files': [{'id': FILE, 'mime': completion.SLIDES}]}], 'missing': [], 'late': []}
        self.found = {'class_key': 'biology', 'week': 'Wk 04', 'lessons': [{'forms': [self.form]}],
                      'other_files': [], 'problems': [], 'roster_size': 1, 'roster_check': {'at': STAMP}}
        self.result = {'file_id': FILE, 'file_updated': STAMP, 'template_id': TEACHER,
            'template_updated': STAMP, 'code': 'C04', 'scoring_basis': 'found_codes',
            'notes_score': 5.0, 'revision_score': 10.0,
            'counts': {'NOTES': {'done': 1, 'required': 2}, 'BANK': {'done': 0, 'required': 0},
                       'DRAFT': {'done': 1, 'required': 1}},
            'items': [{'answer': 'Fictional private student writing'}]}
        self.metas = {fid: {'id': fid, 'modifiedTime': STAMP, 'version': '7', 'parents': ['FictionalParent001'],
                           'mimeType': completion.SLIDES, 'trashed': False} for fid in (FILE, TEACHER)}
        self.cache = {(FILE, STAMP, '7'): {'template': (TEACHER, STAMP, '7'),
                      'student_coding_required': False, 'result': {k: v for k, v in self.result.items()
                                                                if k not in ('file_id', 'file_updated')}}}
        for context in (patch.object(saved, 'configured_store', return_value=self.port),
                        patch.dict(completion._results, self.cache, clear=True),
                        patch.object(google, 'drive_file', side_effect=lambda token, fid, fields: deepcopy(self.metas[fid])),
                        patch.object(completion, 'score_file', side_effect=AssertionError('No assessment on saved read')),
                        patch.object(google, 'deck_bytes', side_effect=AssertionError('No export on saved read'))):
            context.start(); self.addCleanup(context.stop)

    def retain(self):
        with saved.recording(self.config):
            returned = saved.retain('read', self.agenda, 'biology', 'Wk 04', FORM, FILE, self.found, self.result)
            self.assertIs(returned, self.result)

    def read(self):
        return saved.read('read', self.config, self.agenda, 'biology', 'Wk 04', FORM, FILE, self.found)

    def test_round_trip_summary_is_durable_but_contains_no_student_writing(self):
        self.retain()
        result = self.read()
        self.assertEqual(result['status'], 'saved', result)
        self.assertEqual((result['notes_score'], result['revision_score']), (5, 10))
        self.assertNotEqual(result['saved_at'], result['file_updated'])
        self.assertTrue(result['computed_at'])
        self.assertNotIn('private student writing', json.dumps(self.port.rows, default=str))
        self.assertNotIn('items', result)
        self.assertEqual(len(self.port.rows), 1)
        self.retain()
        self.assertEqual(len(self.port.rows), 1)

    def test_missing_result_is_not_a_zero_and_unconfigured_read_needs_no_store(self):
        self.assertEqual(self.read()['status'], 'not_graded')
        self.assertNotIn('notes_score', self.read())
        with patch.object(saved, 'configured_store', side_effect=AssertionError('No store needed')):
            result = saved.read(None, {}, None, 'biology', 'Wk 04', FORM, FILE, None)
        self.assertEqual(result['status'], 'unavailable')

    def test_current_roster_selection_policy_and_agenda_changes_hide_saved_numbers(self):
        self.retain()
        baseline = deepcopy(self.found)
        for change in ('roster', 'submission', 'agenda', 'policy'):
            self.found.clear(); self.found.update(deepcopy(baseline))
            self.config.pop('HUB_ASSIGNMENT_EXCLUSIONS', None)
            self.lessons[0]['topic'] = 'Fictional carbon lesson'
            if change == 'roster': self.found['lessons'][0]['forms'][0]['students'][0]['section'] = 'B3'
            elif change == 'submission': self.found['lessons'][0]['forms'][0]['students'][0]['submitted_at'] = '2026-10-21T00:00:00Z'
            elif change == 'agenda': self.lessons[0]['topic'] = 'Changed teacher instruction'
            else: self.config['HUB_ASSIGNMENT_EXCLUSIONS'] = '[{"changed":true}]'
            with self.subTest(change=change):
                result = self.read()
                self.assertEqual(result['status'], 'stale', result)
                self.assertNotIn('counts', result)

    def test_teacher_ownership_settings_are_part_of_the_current_result_basis(self):
        self.retain()
        for name in ('HUB_INTAKE_DECK_ASSIGNMENTS', 'HUB_ASSIGNMENT_FORM_IDS'):
            self.config[name] = '[{"different_form":"FictionalOtherForm001"}]'
            with self.subTest(name=name):
                self.assertEqual(self.read()['status'], 'stale')
            self.config.pop(name)

    def test_more_than_one_store_batch_retains_the_whole_bounded_class(self):
        students, results = [], {}
        for index in range(85):
            fid = 'FictionalClassStudent%04d' % index
            row = deepcopy(self.form['students'][0])
            row['files'][0]['id'] = fid
            row['email'] = 'fictional%04d@school.example' % index
            students.append(row)
            result = {**deepcopy(self.result), 'file_id': fid}
            results[fid] = result
            completion._results[(fid, STAMP, '7')] = {'template': (TEACHER, STAMP, '7'),
                'student_coding_required': False, 'result': {k: v for k, v in result.items()
                                                          if k not in ('file_id', 'file_updated')}}
            self.metas[fid] = {**deepcopy(self.metas[FILE]), 'id': fid}
        self.form['students'] = students
        self.found['roster_size'] = len(students)
        with patch.object(self.port, 'read', wraps=self.port.read) as reads:
            with saved.recording(self.config):
                for fid, result in results.items():
                    saved.retain('read', self.agenda, 'biology', 'Wk 04', FORM, fid, self.found, result)
            self.assertEqual(len(self.port.rows), 85)
            self.assertTrue(all(len(call.args[0]) <= 40 for call in reads.call_args_list))
        with patch.object(self.port, 'read', wraps=self.port.read) as reads, \
                patch.object(google, 'drive_file', side_effect=lambda token, fid, fields: deepcopy(self.metas[fid])) as metadata:
            result = saved.read_many('read', self.config, self.agenda, 'biology', 'Wk 04', FORM, self.found)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(len(result['results']), 85)
        self.assertTrue(all(score['status'] == 'saved' for score in result['results'].values()))
        self.assertEqual(reads.call_count, 3)
        self.assertEqual(sum(call.args[1] == TEACHER for call in metadata.call_args_list), 2)

    def test_batch_scope_errors_and_incomplete_store_reads_never_look_empty_successful(self):
        broken = deepcopy(self.found)
        broken['lessons'][0]['forms'][0].update(problem='Form could not be read', students=[])
        response = saved.read_many('read', self.config, self.agenda, 'biology', 'Wk 04', FORM, broken)
        self.assertEqual(response['status'], 'unavailable')
        self.assertEqual(response['results'], {})
        with patch.object(self.port, 'read', return_value=SimpleNamespace(documents={}, read_time=STAMP)):
            response = saved.read_many('read', self.config, self.agenda, 'biology', 'Wk 04', FORM, self.found)
        self.assertEqual(response['status'], 'unavailable')
        self.assertEqual(response['results'], {})

    def test_batch_final_metadata_change_hides_all_results(self):
        self.retain()
        calls = {}
        def changed(token, fid, fields):
            calls[fid] = calls.get(fid, 0) + 1
            result = deepcopy(self.metas[fid])
            if fid == FILE and calls[fid] > 1:
                result['version'] = '8'
            return result
        with patch.object(google, 'drive_file', side_effect=changed):
            response = saved.read_many('read', self.config, self.agenda, 'biology', 'Wk 04', FORM, self.found)
        self.assertEqual(response['status'], 'unavailable')
        self.assertEqual(response['results'][FILE]['status'], 'stale')
        self.assertNotIn('counts', response['results'][FILE])

    def test_changed_source_or_teacher_revision_and_failed_metadata_never_show_scores(self):
        self.retain()
        for fid in (FILE, TEACHER):
            self.metas[fid]['version'] = '8'
            with self.subTest(fid=fid):
                self.assertEqual(self.read()['status'], 'stale')
            self.metas[fid]['version'] = '7'
        with patch.object(google, 'drive_file', side_effect=google.GoogleError('sensitive provider body')):
            result = self.read()
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('sensitive', json.dumps(result))
        self.assertNotIn('counts', result)

    def test_metadata_parent_change_during_read_is_not_a_verified_snapshot(self):
        self.retain()
        calls = {}
        def metadata(token, fid, fields):
            calls[fid] = calls.get(fid, 0) + 1
            result = deepcopy(self.metas[fid])
            if fid == TEACHER and calls[fid] > 1:
                result['parents'] = ['ChangedParent001']
            return result
        with patch.object(google, 'drive_file', side_effect=metadata):
            self.assertEqual(self.read()['status'], 'stale')

    def test_store_failure_and_missing_exact_capture_do_not_change_worker_result(self):
        with patch.object(saved, 'configured_store', side_effect=StoreUnavailable('private failure')):
            self.retain()
            self.assertEqual(self.read()['status'], 'unavailable')
        self.assertEqual(self.port.rows, {})
        with patch.dict(completion._results, {}, clear=True):
            self.retain()
        self.assertEqual(self.port.rows, {})

    def test_batch_does_not_write_until_guarded_operation_finishes(self):
        with patch.object(self.port, 'commit', wraps=self.port.commit) as commit:
            with saved.recording(self.config):
                for _ in range(3):
                    saved.retain('read', self.agenda, 'biology', 'Wk 04', FORM, FILE, self.found, self.result)
                commit.assert_not_called()
            self.assertEqual(commit.call_count, 1)

    def test_optional_store_wait_cannot_delay_helper_response(self):
        entered, release, responded = Event(), Event(), Event()
        real_read = self.port.read
        def delayed(ids):
            entered.set()
            release.wait(5)
            return real_read(ids)
        body = [b'unchanged helper response']
        def operation(*_args):
            saved.retain('read', self.agenda, 'biology', 'Wk 04', FORM, FILE, self.found, self.result)
            return body
        response = []
        def call():
            response.append(app.application({}, lambda *args: None, self.config))
            responded.set()
        with patch.object(self.port, 'read', side_effect=delayed), \
                patch.object(app, '_application', side_effect=operation):
            thread = Thread(target=call)
            thread.start()
            try:
                self.assertTrue(entered.wait(1), 'The optional cache writer was not reached')
                self.assertTrue(responded.wait(1), 'The Helper response waited for optional display storage')
                self.assertIs(response[0], body)
            finally:
                release.set()
                thread.join(5)
                worker = saved._worker
                if worker:
                    worker.join(5)
        self.assertEqual(self.read()['status'], 'saved')

    def test_capture_context_does_not_leak_between_parallel_request_configs(self):
        from concurrent.futures import ThreadPoolExecutor
        other_port = MemoryCAS()
        other_port.now = self.port.now
        first = {**self.config, 'HUB_STATE_PROJECT': 'fictional-one'}
        second = {**self.config, 'HUB_STATE_PROJECT': 'fictional-two'}
        observed = []
        def store(config):
            observed.append(config['HUB_STATE_PROJECT'])
            return self.port if config['HUB_STATE_PROJECT'] == 'fictional-one' else other_port
        def run(config):
            with saved.recording(config):
                saved.retain('read', self.agenda, 'biology', 'Wk 04', FORM, FILE, self.found, self.result)
        with patch.object(saved, 'configured_store', side_effect=store), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, (first, second)))
        self.assertEqual(sorted(observed), ['fictional-one', 'fictional-two'])
        self.assertEqual((len(self.port.rows), len(other_port.rows)), (1, 1))


class DashboardReadRouteTests(unittest.TestCase):
    def request(self, path, config=None, query=''):
        status = []
        body = app.application({'REQUEST_METHOD': 'GET', 'PATH_INFO': path, 'QUERY_STRING': query},
                               lambda value, headers: status.append(value), config or {})
        return status[0], json.loads(b''.join(body))

    def test_dashboard_auth_precedes_links_and_unconfigured_links_need_no_google(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()), \
                patch.object(app.dashboard_records, 'records') as records:
            self.assertEqual(self.request('/v2/api/dashboard/records')[0], '401 Unauthorized')
            records.assert_not_called()
        with patch.object(google, 'verify_teacher'), \
                patch.object(google, 'service_token', side_effect=AssertionError('No runtime token needed')), \
                patch.object(app, 'load_agenda', side_effect=AssertionError('No Agenda scan')), \
                patch.object(completion, 'score_file', side_effect=AssertionError('No grading')):
            status, response = self.request('/v2/api/dashboard/records')
        self.assertEqual(status, '200 OK')
        self.assertEqual(response['schema_version'], 'hub-dashboard-records/v1')

    def test_unconfigured_saved_read_returns_explicit_unknown_without_runtime_token(self):
        query = 'class=biology&week=Wk%2004&form=' + FORM + '&file=' + FILE
        with patch.object(google, 'verify_teacher'), \
                patch.object(google, 'service_token', side_effect=AssertionError('No token needed')):
            status, response = self.request('/v2/api/scores/saved', query=query)
        self.assertEqual(status, '200 OK')
        self.assertEqual(response['status'], 'unavailable')
        self.assertNotIn('counts', response)
        with patch.object(google, 'verify_teacher'), \
                patch.object(google, 'service_token', side_effect=AssertionError('No token needed')):
            status, response = self.request('/v2/api/scores/saved', query=query.rsplit('&file=', 1)[0])
        self.assertEqual(status, '200 OK')
        self.assertEqual(response['schema_version'], 'hub-saved-completion-list/v1')
        self.assertEqual(response['status'], 'unavailable')

    def test_work_read_uses_discovery_without_readiness_and_helper_capture_is_unchanged_on_failure(self):
        agenda = SimpleNamespace(spreadsheet_id='FictionalAgendaSheet001', title='Fictional Agenda')
        found = {'lessons': []}
        with patch.object(google, 'verify_teacher'), patch.object(google, 'service_token', return_value='read'), \
                patch.object(app, 'load_agenda', return_value=agenda), \
                patch.object(app.work, 'lesson_work', return_value=found) as discovery, \
                patch.object(completion, 'assignment_readiness', side_effect=AssertionError('No readiness')):
            status, _ = self.request('/v2/api/work', query='class=biology&week=Wk%2004')
        self.assertEqual(status, '200 OK')
        self.assertTrue(discovery.call_args.kwargs['review_only'])


if __name__ == '__main__':
    unittest.main()
