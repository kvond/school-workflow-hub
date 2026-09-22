"""Synthetic shared-Form intake; no school sources, tokens or external writes."""
import copy
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from school_workflow_hub.adapters import forms_submissions as forms
from school_workflow_hub.adapters import submission_inventory as inventory
from school_workflow_hub.adapters import submission_monitor as monitor
from school_workflow_hub.adapters import submission_sources as sources
from school_workflow_hub.adapters import dated_agenda, agenda_save
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import CloudState, key
from school_workflow_hub.workflows.submission_monitor import stream_scope
from tests.test_dated_agenda import fixtures, sheet
from tests import test_dated_agenda as dated_tests
from tests.test_submission_monitor_hosted import Harness, CONFIG as MONITOR_CONFIG, entry

FORM_ID = 'fictional_form_1234567890'
PUBLISHED = '1FAIpQLfictional_published_123456789'
FILE_A, FILE_B = 'a' * 24, 'b' * 24
LABEL = 'Biology · Wk example — Fictional topic'
URL = 'https://docs.google.com/forms/d/e/' + PUBLISHED + '/viewform'
PREFILLED = URL + '?' + urlencode({'entry.12345': LABEL})
BINDING = {'form_id': FORM_ID, 'assignment_question_id': 'assign',
           'assignment_entry_id': '12345', 'identity_questions': {'name': 'name', 'period': 'period'}}
CONFIG = {'HUB_SUBMISSION_FORMS': json.dumps({PUBLISHED: BINDING})}
LESSON = {'class_key': 'biology', 'turnin_form': PREFILLED, 'assignment_label': LABEL,
          'turnin_folder': 'https://drive.google.com/drive/folders/' + 'f' * 24}
SOURCE = sources.resolve_source(LESSON, CONFIG)
META = {'formId': FORM_ID, 'responderUri': URL, 'revisionId': 'v1', 'info': {'title': 'Shared intake'},
        'items': [{'questionItem': {'question': {'questionId': q, kind: {}}}}
                  for q, kind in [('assign', 'choiceQuestion'), ('name', 'textQuestion'),
                                  ('period', 'textQuestion'), ('upload', 'fileUploadQuestion')]]}


def text(value):
    return {'textAnswers': {'answers': [{'value': value}]}}


def response(rid='response-one', files=(FILE_A,), label=LABEL):
    return {'responseId': rid, 'lastSubmittedTime': '2026-09-15T10:00:00Z',
            'respondentEmail': 'fictional@example.invalid',
            'answers': {'assign': text(label), 'name': text('Fictional learner'), 'period': text('2'),
                        'upload': {'fileUploadAnswers': {'answers': [{'fileId': f} for f in files]}}}}


class Drive:
    def __init__(self):
        self.ids = []

    def metadata(self, fid):
        self.ids.append(fid)
        return {'id': fid, 'name': 'Fictional work', 'mimeType': 'application/vnd.google-apps.presentation',
                'trashed': False, 'modifiedTime': '2026-09-15T09:00:00Z', 'version': '7'}


def reader(pages):
    value = Mock()
    value.form.return_value = copy.deepcopy(META)
    value.responses.side_effect = pages
    return value


class SourceTests(unittest.TestCase):
    def test_url_shapes_raw_api_id_and_published_id(self):
        for link in [FORM_ID, 'https://docs.google.com/forms/d/' + FORM_ID + '/edit?usp=sharing',
                     'https://docs.google.com/forms/u/0/d/' + FORM_ID + '/viewform']:
            self.assertEqual(sources.parse_form(link)['id'], FORM_ID)
            self.assertEqual(sources.parse_form(link)['kind'], 'api')
        for link in [PUBLISHED, PREFILLED]:
            self.assertEqual(sources.parse_form(link)['kind'], 'published')

    def test_published_link_needs_explicit_api_mapping(self):
        with self.assertRaisesRegex(ConnectionError, 'mapping_required'):
            sources.resolve_source(LESSON, {})
        self.assertEqual(SOURCE['id'], FORM_ID)
        self.assertNotEqual(SOURCE['assignment_question_id'], '12345')

    def test_form_wins_and_no_broken_form_folder_fallback(self):
        self.assertIsInstance(sources.resolve_source(LESSON, CONFIG), dict)
        with self.assertRaises(ConnectionError):
            sources.resolve_source(dict(LESSON, turnin_form='https://example.invalid/form'), CONFIG)

    def test_prefill_is_exact_and_duplicate_values_fail(self):
        for url in [URL + '?entry.12345=Other', PREFILLED + '&entry.12345=Other']:
            with self.assertRaisesRegex(ConnectionError, 'assignment_mismatch'):
                sources.resolve_source(dict(LESSON, turnin_form=url), CONFIG)

    def test_unsafe_urls_and_config_rejected(self):
        for url in [URL.replace('https:', 'http:'), URL.replace('docs.google.com', 'docs.google.com.evil'),
                    URL.replace('docs.google.com', 'user@docs.google.com'), URL + '\n', URL.replace('/viewform', '/edit')]:
            with self.assertRaises(ConnectionError):
                sources.parse_form(url)
        for raw in ['[]', '{', json.dumps({PUBLISHED: {'form_id': PUBLISHED}})]:
            with self.assertRaises(ConnectionError):
                sources.resolve_source(LESSON, {'HUB_SUBMISSION_FORMS': raw})

    def test_direct_editor_id_cannot_be_remapped(self):
        config = {'HUB_SUBMISSION_FORMS': json.dumps({FORM_ID: dict(BINDING, form_id='x' * 24)})}
        with self.assertRaisesRegex(ConnectionError, 'mapping_conflict'):
            sources.resolve_source(dict(LESSON, turnin_form=FORM_ID), config)

    def test_shared_form_assignments_and_legacy_folder_streams_are_distinct(self):
        first = stream_scope('biology', SOURCE)
        second = stream_scope('biology', dict(SOURCE, assignment='Another lesson'))
        folder = stream_scope('biology', 'f' * 24)
        self.assertEqual(len({first['assignment_key'], second['assignment_key'], folder['assignment_key']}), 3)
        self.assertEqual(folder['assignment_key'], 'folder-' + 'f' * 24)
        self.assertEqual(first, stream_scope('biology', dict(SOURCE, published_id=None)))


class CollectionTests(unittest.TestCase):
    def collect(self, pages):
        drive = Drive()
        result = forms.collect(reader(pages), drive, SOURCE)
        return result, drive

    def test_empty_response_body_is_clean_zero(self):
        (_, files, coverage), drive = self.collect([{}])
        self.assertEqual(files, [])
        self.assertEqual(drive.ids, [])
        self.assertTrue(coverage['complete'])
        self.assertFalse(coverage['roster_complete'])

    def test_single_response_preserves_metadata_and_unverified_identity(self):
        (_, files, coverage), drive = self.collect([{'responses': [response()]}])
        self.assertEqual(drive.ids, [FILE_A])
        self.assertEqual(files[0]['version'], '7')
        self.assertEqual(files[0]['modifiedTime'], '2026-09-15T09:00:00Z')
        evidence = files[0]['form_responses'][0]
        self.assertFalse(evidence['identity_verified'])
        self.assertEqual(evidence['identity_answers']['period'], ['2'])
        self.assertEqual(coverage['responses_matched'], 1)

    def test_all_pages_multiple_uploads_dedupe_references_not_responses(self):
        r = reader([{'responses': [response(files=(FILE_A, FILE_B))], 'nextPageToken': 'page2'},
                    {'responses': [response('second', files=(FILE_A,))]}])
        _, files, coverage = forms.collect(r, Drive(), SOURCE)
        self.assertEqual(len(files), 2)
        self.assertEqual(len(files[0]['form_responses']), 2)
        self.assertEqual(r.responses.call_args_list[1].args, (FORM_ID, 'page2'))
        self.assertEqual(coverage['pages_read'], 2)

    def test_other_assignment_never_fetches_its_files(self):
        (_, files, coverage), drive = self.collect([{'responses': [response('other', (FILE_B,), 'Forensics lesson'), response()]}])
        self.assertEqual(drive.ids, [FILE_A])
        self.assertEqual(coverage['responses_matched'], 1)

    def test_response_order_does_not_change_normalized_evidence(self):
        a, b = response('alpha'), response('beta')
        first, _ = self.collect([{'responses': [a, b]}])
        second, _ = self.collect([{'responses': [b, a]}])
        self.assertEqual(first, second)

    def test_many_responses_and_two_upload_questions(self):
        r = reader([{'responses': [response(str(i), (FILE_A, FILE_B)) for i in range(50)]}])
        _, files, coverage = forms.collect(r, Drive(), SOURCE)
        self.assertEqual(coverage['responses_matched'], 50)
        self.assertEqual(len(files[0]['form_responses']), 50)
        value = response()
        value['answers']['second_upload'] = {'fileUploadAnswers': {'answers': [{'fileId': FILE_B}]}}
        r = reader([{'responses': [value]}])
        r.form.return_value['items'].append({'questionItem': {'question': {'questionId': 'second_upload', 'fileUploadQuestion': {}}}})
        _, files, _ = forms.collect(r, Drive(), SOURCE)
        self.assertEqual([f['id'] for f in files], [FILE_A, FILE_B])

    def test_missing_upload_is_counted_without_inventing_a_file(self):
        value = response(); value['answers'].pop('upload')
        (_, files, coverage), _ = self.collect([{'responses': [value]}])
        self.assertEqual(files, [])
        self.assertEqual(coverage['responses_without_upload'], 1)

    def test_missing_assignment_never_broadens_selection(self):
        value = response(); value['answers'].pop('assign')
        (_, files, coverage), _ = self.collect([{'responses': [value]}])
        self.assertEqual(files, [])
        self.assertEqual(coverage['unassigned_responses'], 1)

    def test_page_failure_duplicate_response_and_cursor_fail_whole_read(self):
        pages = [
            [{'responses': [response()], 'nextPageToken': 'p'}, ConnectionError('submission_forms_unavailable')],
            [{'responses': [response(), response()]}],
            [{'nextPageToken': 'p'}, {'nextPageToken': 'p'}],
            [{'responses': 'bad'}],
            [{'responses': [response(files=('bad/id',))]}]]
        for case in pages:
            drive = Drive()
            with self.assertRaises(ConnectionError):
                forms.collect(reader(case), drive, SOURCE)
            self.assertEqual(drive.ids, [])

    def test_bounds_fail_even_after_a_valid_first_page(self):
        with patch.object(forms, 'MAX_PAGES', 1):
            with self.assertRaisesRegex(ConnectionError, 'scan_incomplete'):
                self.collect([{'responses': [response()], 'nextPageToken': 'more'}])
        with patch.object(forms, 'MAX_FILES', 1):
            with self.assertRaisesRegex(ConnectionError, 'scan_incomplete'):
                self.collect([{'responses': [response(files=(FILE_A, FILE_B))]}])

    def test_permission_failure_and_trashed_upload_never_become_empty_success(self):
        drive = Mock()
        drive.metadata.side_effect = ConnectionError('submission_folder_access_required')
        with self.assertRaisesRegex(ConnectionError, 'submission_file_access_required'):
            forms.collect(reader([{'responses': [response()]}]), drive, SOURCE)
        drive.metadata.side_effect = None
        drive.metadata.return_value = dict(Drive().metadata(FILE_A), trashed=True)
        with self.assertRaisesRegex(ConnectionError, 'submission_file_access_required'):
            forms.collect(reader([{'responses': [response()]}]), drive, SOURCE)

    def test_wrong_form_question_and_changed_schema_stop_read(self):
        for mutation in [{'formId': 'x' * 24}, {'responderUri': URL.replace(PUBLISHED, 'y' * 24)}, {'items': []}]:
            r = reader([{}]); r.form.return_value.update(mutation)
            with self.assertRaises(ConnectionError):
                forms.collect(r, Drive(), SOURCE)
        r = reader([{}]); r.form.side_effect = [META, dict(META, revisionId='v2')]
        with self.assertRaisesRegex(ConnectionError, 'form_changed'):
            forms.collect(r, Drive(), SOURCE)


class TransportTests(unittest.TestCase):
    def test_only_get_and_distinct_readonly_tokens(self):
        r = forms.FormsReader('responses-token', 'drive-read-token')
        session = Mock(); r.session = session
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {}
        r.form(FORM_ID); r.responses(FORM_ID, 'next')
        first, second = session.get.call_args_list
        self.assertEqual(first.kwargs['headers']['Authorization'], 'Bearer drive-read-token')
        self.assertEqual(second.kwargs['headers']['Authorization'], 'Bearer responses-token')
        self.assertTrue(second.args[0].endswith('/' + FORM_ID + '/responses'))
        self.assertEqual(second.kwargs['params']['pageToken'], 'next')
        self.assertFalse(second.kwargs['allow_redirects'])
        self.assertEqual({call[0] for call in session.mock_calls}, {'get', 'get().json'})

    def test_provider_errors_are_redacted_and_actionable(self):
        for status, reason, expected in [(403, 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'scope_missing'),
                                          (403, 'SERVICE_DISABLED', 'api_disabled'),
                                          (403, 'forbidden', 'access_required'),
                                          (404, '', 'access_required'), (429, '', 'rate_limited'),
                                          (500, '', 'unavailable')]:
            r = forms.FormsReader('token', 'body')
            r.session = Mock()
            r.session.get.return_value.status_code = status
            r.session.get.return_value.json.return_value = {'error': {'message': 'PRIVATE', 'details': [{'reason': reason}]}}
            with self.assertRaisesRegex(ConnectionError, expected) as raised:
                r.responses(FORM_ID, None)
            self.assertNotIn('PRIVATE', str(raised.exception))


class WiringTests(unittest.TestCase):
    def test_agenda_reads_U_and_keeps_folder_and_form_separate(self):
        metadata, mapping, data = fixtures()
        for s in data['sheets']:
            s['data'][0]['rowData'][0]['values'] += [{'formattedValue': v} for v in ['_extraURL', 'Submit', '_turnInURL']]
            s['data'][1]['rowData'][0]['values'] += [{'formattedValue': v} for v in ['', 'Submit', PREFILLED]]
        turnin = sheet('🗂 TurnIn Map', {1: ['assignment key', 'target folder id', 'course', 'week'],
                                       2: [LABEL, 'f' * 24, '🌿 Biology', 'Wk example']})
        metadata['sheets'].append({'properties': turnin['properties']}); data['sheets'].append(turnin)
        result = dated_tests.DatedTests().run_day(data=(metadata, mapping, data))
        lesson = result['lessons'][0]
        self.assertEqual(lesson['turnin_form'], PREFILLED)
        self.assertTrue(lesson['turnin_folder'].endswith('f' * 24))
        self.assertEqual(sources.resolve_source(lesson, CONFIG), SOURCE)

    def test_date_inventory_fetches_by_form_without_folder_scan(self):
        page = {'lessons': [LESSON], 'week': 'Wk example'}
        result = forms.collect(reader([{'responses': [response()]}]), Drive(), SOURCE)
        with patch.object(inventory, 'read_day', return_value=page), \
             patch.object(forms, 'read', return_value=result), patch.object(inventory, 'collect') as folders:
            found = inventory.read_inventory('signed', '2026-09-15', 'biology', CONFIG)
        folders.assert_not_called()
        self.assertEqual(found['files'][0]['file_id'], FILE_A)
        self.assertEqual(found['coverage'], 'selected_form_assignment_only')

    def test_monitor_adapter_normalizes_form_files_and_rejects_agenda_drift(self):
        page = {'lessons': [LESSON], 'week': 'Wk example'}
        result = forms.collect(reader([{'responses': [response()]}]), Drive(), SOURCE)
        with patch.object(monitor, 'read_day', return_value=page), \
             patch.object(forms, 'read', return_value=result), patch.object(monitor, 'collect') as folders:
            scan = monitor._drive_scan('signed', '2026-09-15', 'biology', CONFIG)
        folders.assert_not_called()
        self.assertNotIn('folder_id', scan)
        self.assertEqual(scan['files'][0]['modified_at'], '2026-09-15T09:00:00Z')
        changed = {'lessons': [dict(LESSON, assignment_label='Changed')]}
        with patch.object(monitor, 'read_day', side_effect=[page, changed]), patch.object(forms, 'read', return_value=result):
            with self.assertRaisesRegex(ConnectionError, 'submission_agenda_changed'):
                monitor._drive_scan('signed', '2026-09-15', 'biology', CONFIG)

    def test_registered_lesson_needs_no_previous_student_records(self):
        records = {'assignments': [{'class_key': 'biology', 'assignment_key': 'bio:fictional',
                                    'turnin_form': PREFILLED, 'turnin_assignment': LABEL}], 'submissions': []}
        result = forms.collect(reader([{'responses': [response()]}]), Drive(), SOURCE)
        with patch.object(inventory, 'read_records', return_value=records), patch.object(forms, 'read', return_value=result):
            found = inventory.read_lesson_inventory('signed', 'biology', 'bio:fictional', CONFIG)
        self.assertEqual(found['files'][0]['stage'], 'awaiting_assessment')

    def test_config_can_bind_a_registered_lesson_without_workbook_writes(self):
        config = {'HUB_SUBMISSION_FORMS': json.dumps({PUBLISHED: dict(BINDING, lessons={'biology': {'bio:fictional': LABEL}})})}
        records = {'assignments': [{'class_key': 'biology', 'assignment_key': 'bio:fictional'}]}
        binding = inventory._registered_form(records, 'biology', 'bio:fictional', config)
        self.assertEqual(sources.resolve_source(binding, config), SOURCE)

    def test_monitor_empty_rerun_and_failed_read_preserve_baseline(self):
        with Harness() as h:
            h.scan = dict(h.scan, source=SOURCE, source_name='Shared Form', assignment_label=LABEL, files=[])
            h.scan.pop('folder_id'); h.scan.pop('folder_name')
            with patch.object(monitor, '_lesson', return_value=(None, LESSON, SOURCE)):
                first = monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                self.assertEqual(first['counts']['present'], 0)
                h.port.advance(120)
                h.scan['observed_at'] = '2026-09-15T11:02:00+00:00'
                self.assertTrue(monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)['unchanged_rerun'])
                with patch.object(monitor, '_drive_scan', side_effect=ConnectionError('submission_form_scan_incomplete')):
                    with self.assertRaises(ConnectionError):
                        monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                status = monitor.read_status(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                self.assertEqual(status['counts']['present'], 0)
                self.assertEqual(status['last_attempt']['outcome'], 'failed')

    def test_topic_save_uses_same_form_assignment_scope_as_monitor(self):
        with patch.object(agenda_save, 'read_day', return_value={'lessons': [LESSON]}), \
             patch.object(agenda_save.topic_starts, 'record_declarations', return_value={}) as record, \
             patch.object(agenda_save.topic_starts, 'deadline_status', return_value={}):
            agenda_save._topic_record('signed', '2026-09-14', 'biology', {'plan': 'Started', 'notes': ''}, CONFIG, Mock())
        self.assertEqual(record.call_args.args[1], stream_scope('biology', SOURCE))

    def test_new_form_stream_keeps_old_folder_records_and_failed_form_baseline(self):
        with Harness() as h:
            monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)
            store = CloudState(h.port)
            old_scope = stream_scope('biology', 'f' * 24)
            old_task = copy.deepcopy(store.read_task(key('task', old_scope)))
            old_rows = set(h.port.rows)
            h.port.advance(120)
            h.scan.update(source=SOURCE, source_name='Form', assignment_label=LABEL,
                          observed_at='2026-09-15T11:02:00+00:00')
            h.scan.pop('folder_id'); h.scan.pop('folder_name')
            with patch.object(monitor, '_lesson', return_value=(None, LESSON, SOURCE)):
                new = monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                self.assertEqual(new['files'][0]['file_id'], FILE_A)
                self.assertEqual(new['events_last_scan'][0]['kind'], 'first_observed')
                self.assertTrue(old_rows <= set(h.port.rows))
                self.assertEqual(store.read_task(key('task', old_scope)), old_task)
                with patch.object(monitor, '_drive_scan', side_effect=ConnectionError('submission_file_access_required')):
                    with self.assertRaises(ConnectionError):
                        monitor.scan_and_persist(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                kept = monitor.read_status(None, '2026-09-14', 'biology', MONITOR_CONFIG)
                self.assertEqual(kept['counts']['present'], 1)
                self.assertEqual(kept['last_attempt']['outcome'], 'failed')


if __name__ == '__main__':
    unittest.main()
