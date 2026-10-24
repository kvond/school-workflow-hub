"""Private teacher notes: concurrent edits, uncertain saves and HTTP boundary."""
import io
import json
import unittest
from unittest.mock import patch

from hub_v2 import app, google, class_notes as notes
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests.test_cloud_state import MemoryCAS


class ClassNotesTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.config = {'HUB_AGENDA_SPREADSHEET_ID': 'FICTIONALAGENDA001',
                       'HUB_STATE_PROBE_ORIGINS': 'https://hub.example'}
        self.store = patch.object(notes, 'configured_store', return_value=self.port)
        self.store.start(); self.addCleanup(self.store.stop)

    def save(self, text, revision=''):
        return notes.save(self.config, 'biology', text, revision, actor='teacher@school.example')

    def test_roundtrip_preserves_multiline_notes_and_immutable_versions(self):
        self.assertEqual(notes.read(self.config, 'biology'), {'text': '', 'revision': '', 'updated_at': None})
        first = self.save('Biology\nFix lesson links.\nKeep the original work.')
        second = self.save('Resolved links.\nNext: check printing.', first['revision'])
        self.assertEqual(notes.read(self.config, 'biology'), second)
        histories = [doc.value for ident, doc in self.port.rows.items() if ident.startswith('classnoteshistory_')]
        self.assertEqual(len(histories), 2)
        self.assertIn(first['text'], [v['text'] for v in histories])
        self.assertNotIn('updated_by', second)

    def test_stale_writer_cannot_erase_newer_notes(self):
        first = self.save('First')
        latest = self.save('Teacher edited elsewhere', first['revision'])
        with self.assertRaises(Conflict): self.save('Old tab draft', first['revision'])
        self.assertEqual(notes.read(self.config, 'biology'), latest)

    def test_lost_commit_reply_is_unconfirmed_but_read_recovers_exact_saved_text(self):
        self.port.fail_after_commit = True
        with self.assertRaises(StoreUnavailable): self.save('Keep my draft')
        saved = notes.read(self.config, 'biology')
        self.assertEqual(saved['text'], 'Keep my draft')
        with self.assertRaises(Conflict): self.save('Keep my draft')
        self.assertEqual(self.save(saved['text'], saved['revision']), saved)
        self.assertEqual(len(self.port.rows), 2)

    def test_scope_and_input_limits(self):
        self.save('Private original')
        other = {**self.config, 'HUB_AGENDA_SPREADSHEET_ID': 'FICTIONALAGENDA002'}
        self.assertEqual(notes.read(other, 'biology')['text'], '')
        for text, revision in [(None, ''), ('x' * 30001, ''), ('\x00', ''), ('ok', 'invented')]:
            with self.assertRaises(ValueError): self.save(text, revision)
        with self.assertRaises(StoreUnavailable): notes.read({}, 'biology')

    def request(self, method='GET', payload=None, origin='https://hub.example', signed=True):
        payload = {'class': 'biology', **payload} if payload is not None else None
        raw = json.dumps(payload).encode() if payload is not None else b''
        env = {'PATH_INFO': '/v2/api/classes/notes', 'REQUEST_METHOD': method, 'QUERY_STRING': 'class=biology',
               'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        result = {}
        def respond(status, headers): result['status'] = status
        with patch.object(google, 'verify_teacher', side_effect=None if signed else google.SignInRequired(), return_value='teacher@school.example'), \
             patch.object(google, 'service_token', side_effect=AssertionError('Notes do not read or write school sources')):
            result['body'] = json.loads(b''.join(app.application(env, respond, self.config)))
        return result

    def test_http_auth_origin_and_exact_json_contract(self):
        self.assertEqual(self.request(signed=False)['status'], '401 Unauthorized')
        self.assertEqual(self.request('POST', {'text': 'Note', 'revision': ''}, origin='https://other.example')['status'], '403 Forbidden')
        self.assertEqual(self.request('POST', {'text': 'Note', 'revision': '', 'extra': True})['status'], '400 Bad Request')
        result = self.request('POST', {'text': 'Note', 'revision': ''})
        self.assertEqual(result['status'], '200 OK')
        self.assertEqual(self.request()['body'], result['body'])
        self.assertEqual(self.request('POST', {'text': 'Stale', 'revision': ''})['status'], '409 Conflict')

    def test_unavailable_read_never_claims_empty_notes(self):
        with patch.object(self.port, 'read', side_effect=StoreUnavailable()):
            self.assertEqual(self.request()['status'], '503 Service Unavailable')

    def test_classes_and_agendas_are_separate_but_dates_and_forms_are_irrelevant(self):
        saved = self.save('Biology policy')
        self.assertEqual(notes.read(self.config, 'forensics')['text'], '')
        self.assertEqual(notes.read(self.config, 'anatomy')['text'], '')
        config = {**self.config, 'today': '2032-03-29', 'form': 'different-form'}
        self.assertEqual(notes.read(config, 'biology'), saved)
        with self.assertRaises(ValueError): notes.read(self.config, 'unknown')
        with self.assertRaises(ValueError): notes.read(self.config, [])
        self.assertEqual(self.request('POST', {'class': 'unknown', 'text': 'x', 'revision': ''})['status'], '400 Bad Request')
        self.assertEqual(self.request('POST', {'class': None, 'text': 'x', 'revision': ''})['status'], '400 Bad Request')

    def test_passive_read_does_not_create_and_unchanged_save_does_not_add_history(self):
        self.request()
        self.assertEqual(self.port.rows, {})
        saved = self.save('Keep this policy')
        count = len(self.port.rows)
        self.assertEqual(self.save(saved['text'], saved['revision']), saved)
        self.assertEqual(len(self.port.rows), count)

    def test_commit_race_rejects_without_overwriting(self):
        original = self.port.commit
        def raced(changes):
            with patch.object(self.port, 'commit', side_effect=original):
                self.save('Other writer')
            return original(changes)
        with patch.object(self.port, 'commit', side_effect=raced):
            with self.assertRaises(Conflict): self.save('Late draft')
        self.assertEqual(notes.read(self.config, 'biology')['text'], 'Other writer')
