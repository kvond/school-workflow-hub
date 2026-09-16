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
from tests.test_dated_agenda import fixtures
from unittest.mock import Mock


class TargetTests(unittest.TestCase):
    """Exercise the real source mapping rather than replacing it with a target."""

    def target(self, selected, course, mutate=None):
        metadata, mapping, data = fixtures()
        for index, sheet in enumerate(data['sheets']):
            sheet['properties']['sheetId'] = index + 10
        if mutate:
            mutate(metadata, mapping, data)
        reader = Mock()
        reader.read.side_effect = [metadata, mapping, data, data]
        with patch('school_workflow_hub.adapters.dated_agenda.authenticated_reader',
                   return_value=(reader, 'fictional_spreadsheet_12345')), \
             patch.object(api, 'authenticated_reader',
                          return_value=(reader, 'fictional_spreadsheet_12345')):
            return api.target('signed', selected, course, {})

    def test_wednesday_dashboard_draft_resolves_only_forensics_weekly_fields(self):
        identity, values, revision = self.target('2032-01-07', 'forensics')
        self.assertEqual(identity['sheet'], 11)
        self.assertEqual(identity['row'], 4)
        self.assertEqual(identity['columns'], {'title': 3, 'plan': 6, 'notes': 7})
        self.assertEqual(values, {'title': 'Fictional topic',
                                 'plan': 'Second plan', 'notes': 'Second note'})
        self.assertEqual(len(revision), 64)

    def test_dashboard_override_is_not_silently_overwritten_or_misfiled(self):
        with self.assertRaisesRegex(api.ConnectionError, 'agenda_override_needs_review'):
            self.target('2032-01-05', 'forensics')

    def test_anatomy_uses_thursday_column_and_rejects_nonmeeting_wednesday(self):
        identity, _, _ = self.target('2032-01-08', 'anatomy')
        self.assertEqual(identity['sheet'], 12)
        self.assertEqual(identity['columns']['plan'], 6)
        with self.assertRaisesRegex(api.ConnectionError, 'agenda_day_not_writable'):
            self.target('2032-01-07', 'anatomy')

    def test_formula_note_target_is_protected_even_when_display_matches(self):
        def formula(_metadata, _mapping, data):
            data['sheets'][1]['data'][1]['rowData'][0]['values'][6][
                'userEnteredValue'] = {'formulaValue': '=FICTIONAL()'}
        with self.assertRaisesRegex(api.ConnectionError, 'agenda_formula_not_writable'):
            self.target('2032-01-07', 'forensics', formula)

    def test_mapping_revision_changes_when_weekly_note_changes(self):
        _, _, original = self.target('2032-01-07', 'forensics')
        def note(_metadata, _mapping, data):
            data['sheets'][1]['data'][1]['rowData'][0]['values'][6][
                'formattedValue'] = 'New teacher note'
        _, _, updated = self.target('2032-01-07', 'forensics', note)
        self.assertNotEqual(original, updated)


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
