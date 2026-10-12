"""One identical bounded GET may repeat HTTP500, never a write or timeout."""
import unittest
from unittest.mock import Mock, patch
from requests.exceptions import ConnectionError, Timeout
from hub_v2 import google


def response(status, payload=None, *, unreadable=False):
    value = Mock(status_code=status)
    if unreadable:
        value.json.side_effect = ValueError('private unreadable provider text')
    else:
        value.json.return_value = payload if payload is not None else {
            'error': {'code': status, 'message': 'private provider text',
                      'errors': [{'reason': 'backendError', 'message': 'private provider detail'}]}}
    return value


class GoogleGetInternalErrorRetryTests(unittest.TestCase):
    def run_request(self, replies, *, method='GET'):
        session = Mock()
        session.request.side_effect = replies
        try:
            with patch.object(google, '_session', return_value=session):
                result = google.get_json('https://provider.example.test/exact-item', 'synthetic-token',
                    params={'fields': 'id,version'}, timeout=17, method=method, body=None)
        except google.GoogleError as error:
            self.assertNotIn('private', str(error))
            result = error
        return result, session

    def test_first_500_then_success_repeats_exact_bounded_get_once(self):
        expected = {'id': 'FICTIONAL_ITEM', 'version': '4'}
        first = response(500)
        result, session = self.run_request([first, response(200, expected)])
        self.assertEqual(result, expected)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.request.call_args_list[0], session.request.call_args_list[1])
        call = session.request.call_args_list[0]
        self.assertEqual(call.args, ('GET', 'https://provider.example.test/exact-item'))
        self.assertEqual(call.kwargs['params'], {'fields': 'id,version'})
        self.assertEqual(call.kwargs['timeout'], (5, 17))
        self.assertIs(call.kwargs['allow_redirects'], False)
        first.json.assert_not_called()

    def test_second_failure_is_final_and_never_becomes_a_third_request(self):
        for status in (500, 502, 503, 504, 401, 403, 404, 429):
            result, session = self.run_request([response(500), response(status)])
            self.assertIsInstance(result, google.GoogleError)
            self.assertEqual(result.status, status)
            self.assertEqual(session.request.call_count, 2)

    def test_only_observed_500_status_can_repeat_not_auth_other_errors_or_redirects(self):
        for status in (400, 401, 403, 404, 408, 429, 501, 502, 503, 504, 301, 302, 307, 308):
            result, session = self.run_request([response(status)])
            self.assertIsInstance(result, google.GoogleError)
            self.assertEqual(result.status, status)
            session.request.assert_called_once()

    def test_write_methods_and_lowercase_get_never_repeat_even_valid_500(self):
        for method in ('POST', 'PATCH', 'PUT', 'DELETE', 'HEAD', 'OPTIONS', 'get'):
            result, session = self.run_request([response(500)], method=method)
            self.assertEqual(result.status, 500)
            session.request.assert_called_once()

    def test_500_repeat_depends_only_on_status_and_does_not_read_error_json(self):
        malformed = [None, [], 'private text', {'error': []}, {'error': {}},
                     {'error': {'code': '500'}}, {'error': {'code': True}}, {'error': {'code': 503}}]
        for value in malformed:
            bad = response(500)
            bad.json.return_value = value
            result, session = self.run_request([bad, response(200, {'verified': True})])
            self.assertEqual(result, {'verified': True})
            self.assertEqual(session.request.call_count, 2)
            bad.json.assert_not_called()
        unreadable = response(500, unreadable=True)
        result, session = self.run_request([unreadable, response(200, {'verified': True})])
        self.assertEqual(result, {'verified': True})
        self.assertEqual(session.request.call_count, 2)
        unreadable.json.assert_not_called()

    def test_unreadable_success_json_never_repeats(self):
        result, session = self.run_request([response(200, unreadable=True)])
        self.assertEqual(result.status, 200)
        session.request.assert_called_once()

    def test_transport_timeout_keeps_typed_read_reason_without_repeating(self):
        for method in ('GET', 'POST', 'PATCH', 'PUT', 'DELETE'):
            result, session = self.run_request([Timeout('private transport text')], method=method)
            self.assertIsNone(result.status)
            self.assertEqual(result.reason, 'read_timeout' if method == 'GET' else None)
            session.request.assert_called_once()
        for error in (ConnectionError('private connection'), RuntimeError('private unexpected')):
            result, session = self.run_request([error])
            self.assertIsNone(result.reason)
            session.request.assert_called_once()

    def test_timeout_or_unreadable_json_after_first_500_is_final(self):
        for second in (Timeout('private'), response(200, unreadable=True), response(500, unreadable=True)):
            result, session = self.run_request([response(500), second])
            self.assertIsInstance(result, google.GoogleError)
            self.assertEqual(session.request.call_count, 2)
            if isinstance(second, Timeout):
                self.assertEqual((result.status, result.reason), (None, 'read_timeout'))

    def test_first_success_is_not_repeated_or_changed(self):
        expected = {'id': 'FICTIONAL_ITEM', 'version': '4', 'parents': ['FICTIONAL_PARENT']}
        result, session = self.run_request([response(200, expected)])
        self.assertEqual(result, expected)
        session.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
