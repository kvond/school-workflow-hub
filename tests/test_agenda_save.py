import copy
import io
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import agenda_save as api
from school_workflow_hub.core.cloud_state import CloudState, Conflict
from tests.test_cloud_state import MemoryCAS
from tests.test_web_hosting import request
from tests.test_hosted_agenda import CONFIG


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.values = {'title': 'Fictional topic', 'plan': 'Plan', 'notes': ''}
        self.identity = {'source': 'fictional_spreadsheet_12345', 'sheet': 12, 'row': 6,
                         'columns': {'title': 3, 'plan': 6, 'notes': 7}}
        self.payload = {'date': '2032-01-07', 'class_key': 'forensics', 'revision': 'a'*64,
                        'values': {**self.values, 'notes': '=literal text'}, 'request_id': 'request-fictional-1'}

    def run_save(self, target=None, batch=None):
        before = (self.identity, self.values, 'a'*64)
        after = (self.identity, self.payload['values'], 'b'*64)
        with patch.object(api, 'target', side_effect=target or [before, before, after]), \
             patch.object(api, 'service_token', return_value='synthetic'), \
             patch.object(api, 'configured_store', return_value=self.port), \
             patch.object(api, 'batch', side_effect=batch) as writer:
            result = api.save('assertion', self.payload, {'HUB_READER_SERVICE_ACCOUNT': 'fictional'})
            return result, writer

    def test_only_changed_note_is_written_as_literal_and_read_back(self):
        result, writer = self.run_save()
        request = writer.call_args.args[2][0]['updateCells']
        self.assertEqual(request['start'], {'sheetId': 12, 'rowIndex': 5, 'columnIndex': 6})
        self.assertEqual(request['fields'], 'userEnteredValue')
        self.assertEqual(request['rows'][0]['values'][0]['userEnteredValue'], {'stringValue': '=literal text'})
        self.assertTrue(result['readback_verified'])

    def test_stale_base_never_writes(self):
        before = (self.identity, self.values, 'c'*64)
        with self.assertRaises(Conflict):
            self.run_save([before, before])
        self.assertFalse(any(k.startswith('record_') for k in self.port.rows))

    def test_readback_mismatch_cannot_report_saved(self):
        before = (self.identity, self.values, 'a'*64)
        with self.assertRaisesRegex(api.ConnectionError, 'readback_failed'):
            self.run_save([before, before, before])

    def test_repeat_completed_request_reads_destination_without_rewriting(self):
        self.run_save()
        after = (self.identity, self.payload['values'], 'b'*64)
        result, writer = self.run_save([after, after])
        writer.assert_not_called()
        self.assertTrue(result['readback_verified'])

    def test_uncertain_write_is_not_automatically_repeated(self):
        with self.assertRaises(api.ConnectionError):
            self.run_save(batch=api.ConnectionError('agenda_save_response_uncertain'))
        with self.assertRaises(Conflict):
            self.run_save()

    def test_extra_target_and_oversized_text_rejected(self):
        for payload in ({**self.payload, 'sheetId': 99},
                        {**self.payload, 'values': {**self.values, 'notes': 'x'*20001}}):
            with self.assertRaises(ValueError):
                api.save('assertion', payload, {})

    def test_unsigned_request_does_not_call_writer(self):
        with patch.dict('os.environ', CONFIG, clear=True), patch.object(api, 'save') as writer:
            response = request('/api/agenda-save', 'POST')
            self.assertEqual(response['status'], '403 Forbidden')
            writer.assert_not_called()

    def test_cross_origin_request_does_not_call_writer(self):
        with patch.dict('os.environ', {**CONFIG, 'HUB_STATE_PROBE_ORIGINS':'https://hub.example'}, clear=True), \
             patch('school_workflow_hub.adapters.hosted_agenda.authorize'), patch.object(api, 'save') as writer:
            response = request('/api/agenda-save', 'POST', HTTP_ORIGIN='https://other.example', CONTENT_TYPE='application/json')
            self.assertEqual(response['status'], '403 Forbidden')
            writer.assert_not_called()
