"""Authenticated filing boundaries, with fictional sources only."""
import io
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import app, google, student_archive


class StudentRecordsApiTests(unittest.TestCase):
    def request(self, method='POST', payload=None, origin='https://hub.example.test'):
        raw = json.dumps(payload or {'class': 'biology', 'week': 'Wk 05'}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': '/v2/api/student-records/archive',
               'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        statuses = []
        body = app.application(env, lambda status, headers: statuses.append(status),
                               {'HUB_STATE_PROBE_ORIGINS': 'https://hub.example.test'})
        return statuses[0], json.loads(b''.join(body))

    def test_filing_requires_teacher_and_exact_origin(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired), \
                patch.object(student_archive, 'archive_work') as archive:
            self.assertEqual(self.request()[0], '401 Unauthorized')
            archive.assert_not_called()
        with patch.object(google, 'verify_teacher'), patch.object(student_archive, 'archive_work') as archive:
            self.assertEqual(self.request(origin='https://elsewhere.example.test')[0], '403 Forbidden')
            archive.assert_not_called()

    def test_read_listing_never_files_and_does_not_need_agenda(self):
        with patch.object(google, 'service_token', return_value='read'), \
                patch.object(app, 'load_agenda') as agenda, \
                patch.object(student_archive, 'archive_listing', return_value={'status': 'held'}) as listing, \
                patch.object(student_archive, 'archive_work') as archive:
            self.assertEqual(app.api('api/student-records', {'class': ['biology']}, {}), {'status': 'held'})
            listing.assert_called_once_with('read', {}, 'biology')
            agenda.assert_not_called()
            archive.assert_not_called()

    def test_archive_reads_fresh_sources_and_returns_partial_coverage(self):
        agenda = Mock()
        agenda.week_options.return_value = [{'label': 'Wk 05'}]
        found = {'class_key': 'biology', 'lessons': []}
        held = {'status': 'held', 'filed': 2, 'held': [{'problem': 'Source unavailable'}]}
        with patch.object(google, 'verify_teacher'), \
                patch.object(google, 'service_token', side_effect=['read', 'write']), \
                patch.object(app, 'load_agenda', return_value=agenda), \
                patch.object(app, 'lesson_work', return_value=found) as load, \
                patch.object(student_archive, 'archive_work', return_value=held) as archive:
            self.assertEqual(self.request(), ('200 OK', held))
            load.assert_called_once_with('read', {'HUB_STATE_PROBE_ORIGINS': 'https://hub.example.test'},
                                         agenda, 'biology', 'Wk 05', fresh=True)
            self.assertEqual(archive.call_args.args[0:2], ('read', 'write'))
            self.assertIs(archive.call_args.args[-1], found)

    def test_payload_cannot_supply_student_identity_or_unregistered_week(self):
        agenda = Mock()
        agenda.week_options.return_value = [{'label': 'Wk 05'}]
        with patch.object(google, 'verify_teacher'), patch.object(google, 'service_token'), \
                patch.object(app, 'load_agenda', return_value=agenda), \
                patch.object(student_archive, 'archive_work') as archive:
            self.assertEqual(self.request(payload={'class': 'biology', 'week': 'Wk 05', 'email': 'fake'})[0], '400 Bad Request')
            self.assertEqual(self.request(payload={'class': 'biology', 'week': 'Wk 99'})[0], '400 Bad Request')
            archive.assert_not_called()
