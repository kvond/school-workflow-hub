"""Typed, read-only teacher transport failures stay distinct from source drift."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch
from requests.exceptions import ConnectionError, Timeout

from hub_v2 import completion, google
from test_hub_v2 import fixture, handout
from test_audit_form_source_readiness import FIRST, SEARCH, declaration, metadata


class GoogleReadTimeoutTests(unittest.TestCase):
    def request(self, method, error):
        session = Mock()
        session.request.side_effect = error
        with patch.object(google, '_session', return_value=session):
            with self.assertRaises(google.GoogleError) as caught:
                google.get_json('https://provider.example.test/read', 'synthetic-token', method=method)
        session.request.assert_called_once()
        self.assertNotIn('private', str(caught.exception))
        return caught.exception

    def test_only_exact_get_transport_timeout_is_tagged_without_repeating_request(self):
        for error in (Timeout('private body'), Timeout()):
            result = self.request('GET', error)
            self.assertEqual((result.status, result.reason), (None, 'read_timeout'))
        for method in ('POST', 'PATCH', 'PUT', 'DELETE', 'get'):
            self.assertIsNone(self.request(method, Timeout('private body')).reason)

    def test_arbitrary_connection_or_parse_failure_never_becomes_read_timeout(self):
        for error in (ConnectionError('private'), RuntimeError('private'), ValueError('private')):
            self.assertIsNone(self.request('GET', error).reason)
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.side_effect = ValueError('private')
        with patch.object(google, '_session', return_value=session), self.assertRaises(google.GoogleError) as caught:
            google.get_json('https://provider.example.test/read', 'synthetic-token')
        self.assertEqual(caught.exception.status, 200)
        self.assertIsNone(caught.exception.reason)
        session.request.assert_called_once()

    def test_provider_status_and_provider_read_timeout_string_are_not_transport_timeout(self):
        for status in (401, 403, 404, 429, 500):
            session = Mock()
            session.request.return_value.status_code = status
            session.request.return_value.json.return_value = {'error': {'errors': [{'reason': 'read_timeout'}]}}
            with patch.object(google, '_session', return_value=session), self.assertRaises(google.GoogleError) as caught:
                google.get_json('https://provider.example.test/read', 'synthetic-token')
            self.assertEqual(caught.exception.status, status)
            self.assertIsNone(completion._registered_teacher_read_failure(caught.exception, declaration(FIRST)))
            session.request.assert_called_once()


class RegisteredTeacherTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.agenda = fixture()
        self.binding = declaration(FIRST)
        self.config = {'HUB_FORM_TEACHER_SOURCES': json.dumps([self.binding])}
        self.failure = {'source': 'registered_teacher', 'reason': 'read_timeout',
                        'scope': {'class': 'forensics', 'week': 'Wk 04', 'form': FIRST}}

    def check(self, *, drive_error=None, late_error=None, content_error=None, unbound=False, metadata_change=None):
        reads = 0
        def drive(_token, fid, _fields=None):
            nonlocal reads
            reads += 1
            if drive_error:
                raise drive_error
            if late_error and reads > 6:
                raise late_error
            return {**metadata(fid), **(metadata_change or {})}
        def template(_token, fid):
            if content_error:
                raise content_error
            return metadata(fid), completion.read_deck(handout())
        with patch.object(google, 'drive_file', side_effect=drive), \
             patch.object(completion, '_teacher_template', side_effect=template), \
             patch.object(completion, 'student_coding_required', return_value=False), \
             patch.object(completion, 'score_file', side_effect=AssertionError('no scoring')), \
             patch.object(google, 'append_rows', side_effect=AssertionError('no write')), \
             patch.object(google, 'update_cells', side_effect=AssertionError('no write')):
            return completion.assignment_readiness('read-only', {} if unbound else self.config,
                                                   self.agenda, 'forensics', 'Wk 04', FIRST)

    def test_initial_registered_metadata_timeout_carries_exact_scope_only_on_failure(self):
        value = self.check(drive_error=google.GoogleError('Google did not answer.', reason='read_timeout'))
        self.assertEqual(value['status'], 'NOT_READY')
        self.assertEqual(value['read_failure'], self.failure)
        self.assertEqual(value['source_resolution']['read_failure'], self.failure)
        ready = self.check()
        self.assertEqual(ready['status'], 'READY')
        self.assertNotIn('read_failure', ready)
        self.assertNotIn('read_failure', ready['source_resolution'])

    def test_final_registered_source_bookend_preserves_typed_timeout_after_ready_contents(self):
        value = self.check(late_error=google.GoogleError('Google did not answer.', reason='read_timeout'))
        self.assertEqual(value['linked_decks'][0]['status'], 'READY')
        self.assertEqual(value['status'], 'NOT_READY')
        self.assertEqual(value['read_failure'], self.failure)
        self.assertEqual(value['linked_decks'][-1]['read_failure'], self.failure)
        self.assertNotIn('read_failure', value['source_resolution'])

    def test_registered_content_get_timeout_is_typed_but_unbound_reads_are_not(self):
        error = google.GoogleError('Google did not answer.', reason='read_timeout')
        self.assertEqual(self.check(content_error=error)['read_failure'], self.failure)
        self.assertNotIn('read_failure', self.check(content_error=error, unbound=True))

    def test_access_generic_error_missing_source_or_drift_never_acquires_retry_tag(self):
        for error in (google.GoogleError("Google didn't answer in time. Try again."),
                      google.GoogleError('Denied.', 403, 'read_timeout'),
                      google.GoogleError('Denied.', 401), google.GoogleError('Missing.', 404)):
            with self.subTest(error=error):
                self.assertNotIn('read_failure', self.check(drive_error=error))
                self.assertNotIn('read_failure', self.check(late_error=error))
        self.assertNotIn('read_failure', self.check(metadata_change={'trashed': True}))
        before = completion._registered_teacher_sources
        with patch.object(google, 'drive_file', side_effect=lambda _t, fid, _f=None: metadata(fid)):
            resolved = before('read', self.binding)
        changed = deepcopy(resolved)
        changed['decks'][0]['metadata']['version'] = '5'
        with patch.object(completion, '_registered_teacher_sources', return_value=changed):
            with self.assertRaises(completion.Unreadable):
                completion._registered_teacher_bookend('read', self.binding, resolved, [])

    def test_teacher_hold_returns_before_source_reads_and_cannot_borrow_timeout_tag(self):
        with patch('hub_v2.completion_holds.check', return_value={'held': True, 'status': 'NOT_READY',
                    'code': 'teacher_readiness_hold', 'label': 'Needs embedded codes', 'problem': 'Photosynthesis copies need codes.'}), \
             patch.object(google, 'drive_file') as drive:
            value = completion.assignment_readiness('read', self.config, self.agenda, 'forensics', 'Wk 04', FIRST)
        self.assertEqual(value['code'], 'teacher_readiness_hold')
        self.assertNotIn('read_failure', value)
        drive.assert_not_called()

    def test_morning_not_ready_reply_keeps_typed_context_and_never_constructs_or_scores_a_list(self):
        from test_morning_readiness import WholeAssignmentTests
        helper = WholeAssignmentTests()
        value = self.check(late_error=google.GoogleError('Google did not answer.', reason='read_timeout'))
        result, scorer, rows = helper.make(helper.form(), value, [])
        self.assertEqual(result['code'], 'coverage_incomplete')
        self.assertEqual(result['readiness']['read_failure'], self.failure)
        self.assertNotIn('list', result)
        scorer.assert_not_called()
        rows.assert_not_called()


if __name__ == '__main__':
    unittest.main()
