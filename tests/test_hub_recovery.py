"""Private recovery receipts use fictional people and ledger rows."""
import io
import json
import unittest
from unittest.mock import patch
from hub_v2 import app, google, ledger, morning, recovery

ACTOR = 'teacher@example.test'
EVENT = {'event_id': '11111111-1111-4111-8111-111111111111',
         'run_id': '22222222-2222-4222-8222-222222222222',
         'type': 'verified', 'note': 'Saved, reloaded and checked the full panel.'}

class RecoveryTests(unittest.TestCase):
    def ledger(self, rows, append=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(ledger, 'find', return_value={'id': 'FICTIONAL'}))
        stack.enter_context(patch.object(ledger, '_first_tab', return_value="'Sheet1'"))
        stack.enter_context(patch.object(google, 'sheet_values', side_effect=lambda *a: rows.copy()))
        writer = stack.enter_context(patch.object(google, 'append_rows', side_effect=append))
        self.addCleanup(stack.close)
        return writer

    def test_validated_identity_and_private_diagnostics(self):
        recovery.validate(EVENT)
        value = {k: v for k, v in EVENT.items() if k != 'note'}
        value.update(type='readback', assignment='Matter', section='A4 Fictional',
                     column='notes', code='readback_incomplete', names=['Avery, Ann'])
        recovery.validate(value)
        for bad in ({**EVENT, 'by': 'spoof@example.test'}, {**EVENT, 'note': ''},
                    {**EVENT, 'at': 'yesterday'}, {**EVENT, 'run_id': 42},
                    {**value, 'column': 'anything'}, {**value, 'names': 'Avery'},
                    {**value, 'names': ['bad\nname']}):
            with self.assertRaises(ValueError): recovery.validate(bad)

    def test_audit_is_attributed_read_back_and_idempotent(self):
        rows = []
        append = self.ledger(rows, lambda token, sheet, tab, values: rows.extend(values))
        saved = recovery.record('read', 'write', {}, EVENT, ACTOR)
        self.assertEqual(saved['by'], ACTOR)
        self.assertTrue(saved['at'].endswith('+00:00'))
        self.assertEqual(recovery.records('read', {}), [saved])
        self.assertEqual(recovery.record('read', 'write', {}, EVENT, ACTOR), saved)
        append.assert_called_once()
        for event, actor in [({**EVENT, 'note': 'different'}, ACTOR), (EVENT, 'someone@example.test')]:
            with self.assertRaises(ValueError): recovery.record('read', 'write', {}, event, actor)
        self.assertEqual(len(rows), 1)

    def test_unreadable_destination_does_not_confirm_recovery(self):
        append = self.ledger([])
        with self.assertRaises(google.GoogleError): recovery.record('read', 'write', {}, EVENT, ACTOR)
        append.assert_called_once()
        with self.assertRaises(ValueError): recovery.record('read', 'write', {}, EVENT, '')

    def test_malformed_records_do_not_hide_valid_ones(self):
        saved = {**EVENT, 'by': ACTOR, 'at': '2026-10-02T12:00:00+00:00'}
        rows = [['', recovery.KIND, '', '', '', '', json.dumps(v)] for v in [None, {'wrong': 1}, saved]]
        self.ledger(rows)
        self.assertEqual(recovery.records('read', {}), [saved])

    def test_latest_attempt_on_same_day_is_shown_first(self):
        from test_hub_morning import report
        a = report(); b = {**a, 'run_id': EVENT['run_id'], 'code': 'panel_unusable'}
        values = [a, b, {**b, 'code': 'finished'}]
        self.ledger([['', morning.KIND, '', '', '', '', json.dumps(v)] for v in values])
        self.assertEqual(morning.reports('read', {}), [values[2], a])

class RecoveryEndpointTests(unittest.TestCase):
    def post(self, event=EVENT, origin='https://hub.example.test'):
        body = json.dumps(event).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/v2/api/morning/recovery',
               'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
        status = []
        result = app.application(env, lambda code, headers: status.append(code),
                                 {'HUB_STATE_PROBE_ORIGINS': 'https://hub.example.test'})
        return status[0], json.loads(b''.join(result))

    def test_auth_and_same_origin_are_required(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired), patch.object(recovery, 'record') as record:
            self.assertEqual(self.post()[0], '401 Unauthorized'); record.assert_not_called()
        with patch.object(google, 'verify_teacher', return_value=ACTOR), patch.object(recovery, 'record') as record:
            self.assertEqual(self.post(origin='https://other.example.test')[0], '403 Forbidden'); record.assert_not_called()
            self.assertEqual(self.post({**EVENT, 'by': 'spoof@example.test'})[0], '400 Bad Request'); record.assert_not_called()

    def test_server_identity_is_used_and_failed_readback_returns_failure(self):
        with patch.object(google, 'verify_teacher', return_value=ACTOR), patch.object(google, 'service_token', return_value='fictional'), patch.object(recovery, 'record', return_value={**EVENT, 'by': ACTOR, 'at': 'server time'}) as record:
            status, body = self.post()
            self.assertEqual(status, '200 OK'); self.assertTrue(body['recorded'])
            self.assertEqual(record.call_args.args[-1], ACTOR)
            record.side_effect = google.GoogleError('failed')
            self.assertEqual(self.post()[0], '502 Bad Gateway')

if __name__ == '__main__': unittest.main()
