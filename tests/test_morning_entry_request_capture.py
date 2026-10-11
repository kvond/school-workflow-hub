"""Fictional receipt request sources only; no provider or ledger calls."""
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch
import io
import json
import unittest

from hub_v2 import app, combined_assessment as combined, completion, google, ledger, morning, morning_entry
from test_morning_eligible_entry import fixture, NOW

IDS = ['FictionalTeacher0001', 'FictionalTeacher0002', 'FictionalStudent0001', 'FictionalStudent0002']

class EntryRequestCaptureTests(unittest.TestCase):
    def setUp(self):
        self.metas = {fid: {'id': fid, 'mimeType': completion.SLIDES, 'trashed': False,
            'version': '7', 'parents': ['FictionalFolder0001'], 'properties': {}} for fid in IDS}
        self.make_calls = 0
        self.after_final = False
        self.final_failure = None
        self.changed_during_whole = None
        self.record_contexts = []
        self.payload, self.current = fixture(0)
        self.form = {'form_id': self.payload['scope']['form'], 'combined_assessment': {'fixture': True},
            'students': [{'matched': True, 'file': IDS[2]}, {'matched': True, 'file': IDS[3]}]}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def metadata(_token, fid, _fields):
            value = deepcopy(self.metas[fid])
            if self.after_final and fid == IDS[0]:
                if self.final_failure == 'version': value['version'] = '8'
                if self.final_failure == 'permission': raise google.GoogleError('Actual permission denied', 403, 'insufficientFilePermissions')
            return value
        self.metadata = self.stack.enter_context(patch.object(google, 'drive_file', side_effect=metadata))
        self.bytes = self.stack.enter_context(patch.object(google, 'deck_bytes', return_value=b'fictional-native-export'))
        self.stack.enter_context(patch.object(completion, 'read_deck', return_value={'slides': [{'units': []}]}))
        self.stack.enter_context(patch.object(google, 'get_json', side_effect=RuntimeError('No provider call permitted in pure controls')))
        self.stack.enter_context(patch.object(google, 'verify_teacher'))
        self.stack.enter_context(patch.object(google, 'service_token', return_value='fictional-token'))
        self.stack.enter_context(patch.object(app, 'load_agenda', return_value=object()))
        self.stack.enter_context(patch.object(app, 'lesson_work', return_value={'lessons': [{'forms': [self.form]}]}))
        self.stack.enter_context(patch.object(completion, 'student_coding_required', return_value=False))
        self.stack.enter_context(patch.object(completion, 'scoring_candidates', return_value=self.form['students']))
        self.stack.enter_context(patch.object(ledger, 'read', return_value={}))
        self.stack.enter_context(patch.object(morning, 'closed_recheck', return_value=True))
        clock = self.stack.enter_context(patch.object(morning_entry, 'datetime', wraps=datetime))
        clock.now.return_value = NOW
        def make(_token, _config, _agenda, _cls, _week, _fid, loader, _scorer):
            self.make_calls += 1
            loader()
            for fid in IDS: combined.read_source('fictional-token', fid)
            self.after_final = self.make_calls % 2 == 0
            return deepcopy(self.current)
        self.stack.enter_context(patch.object(morning, '_make_list', side_effect=make))
        def verify(_token, _form, row):
            if self.changed_during_whole:
                self.metas[IDS[0]].update(self.changed_during_whole)
            teachers = {fid: combined.read_source('fictional-token', fid) for fid in IDS[:2]}
            return teachers, {'fictional-cycle': combined.read_source('fictional-token', row['file'])}
        self.whole = self.stack.enter_context(patch.object(combined, 'verify_sources', side_effect=verify))
        def record(*args, **kwargs):
            self.record_contexts.append(combined._request_captures.get())
            return {**args[-1], 'whole_assignment_recorded': kwargs['whole_assignment']}
        self.writer = self.stack.enter_context(patch.object(morning_entry, 'record', side_effect=record))

    def request(self):
        self.after_final = False
        raw = json.dumps(self.payload).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/v2/api/morning/entry',
            'HTTP_ORIGIN': 'https://fictional.example', 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        status = []
        response = app.application(env, lambda code, headers: status.append(code),
            {'HUB_STATE_PROBE_ORIGINS': 'https://fictional.example'})
        return status[0], json.loads(b''.join(response))

    def test_each_source_exported_once_across_both_lists_and_real_whole_readiness_before_append(self):
        status, response = self.request()
        self.assertEqual(status, '200 OK')
        self.assertTrue(response['recorded'])
        self.assertEqual(self.bytes.call_count, 4)
        self.assertEqual(self.whole.call_count, 2)
        self.assertEqual(self.metadata.call_count, 32)
        self.assertEqual(self.record_contexts, [None])
        self.assertIsNone(combined._request_captures.get())

    def test_final_metadata_change_rejects_before_append_and_clears_capture(self):
        self.final_failure = 'version'
        status, response = self.request()
        self.assertEqual(status, '409 Conflict')
        self.assertEqual(response['code'], 'source_changed')
        self.writer.assert_not_called()
        self.assertIsNone(combined._request_captures.get())

    def test_final_permission_loss_rejects_before_append_without_raw_retry(self):
        self.final_failure = 'permission'
        status, response = self.request()
        self.assertEqual(status, '502 Bad Gateway')
        self.assertEqual(response['code'], 'receipt_unconfirmed')
        self.writer.assert_not_called()
        self.assertEqual(self.bytes.call_count, 4)
        self.assertIsNone(combined._request_captures.get())

    def test_whole_guard_fresh_teacher_parent_change_remains_held_before_append(self):
        self.changed_during_whole = {'parents': ['DifferentFictionalFolder']}
        status, response = self.request()
        self.assertEqual(status, '409 Conflict')
        self.assertEqual(response['code'], 'source_changed')
        self.writer.assert_not_called()
        self.assertEqual(self.bytes.call_count, 4)

    def test_successful_capture_does_not_survive_into_a_second_request(self):
        self.assertEqual(self.request()[0], '200 OK')
        self.assertEqual(self.request()[0], '200 OK')
        self.assertEqual(self.bytes.call_count, 8)
        self.assertEqual(self.writer.call_count, 2)
        self.assertEqual(self.record_contexts, [None, None])
        self.assertIsNone(combined._request_captures.get())

if __name__ == '__main__':
    unittest.main()
