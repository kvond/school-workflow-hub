"""Synthetic source/receipt integration; no school or model calls."""
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import unittest

from hub_v2 import app, completion, google, morning
import test_morning_row_selection_holds as row_holds


class ObservationSourceTests(unittest.TestCase):
    def test_same_capture_adds_source_evidence_without_extra_reads_or_changed_scores(self):
        original = row_holds.MorningRowSelectionHoldTests()
        original.setUp()
        self.addCleanup(original.tearDown)
        with ExitStack() as stack:
            metadata, read = original.source_patches(stack)
            value = original.make()
        self.assertEqual(value['code'], 'ready')
        self.assertEqual([(r['kind'], r['notes']) for r in value['list']['rows']], [('score', 10), ('check', None)])
        basis = value['observation_basis']
        self.assertEqual(basis['revision'], value['revision'])
        self.assertEqual(basis['rows'][0]['sources'][0]['file_id'], row_holds.VALID)
        self.assertEqual(basis['rows'][0]['sources'][0]['version'], '1')
        self.assertEqual(basis['rows'][1]['sources'], [])
        self.assertEqual(metadata.call_count, 1)
        read.assert_called_once()
        self.assertNotIn('student_id', json.dumps(basis))

    def test_per_file_events_and_combined_sources_remain_distinct(self):
        value = {'first': 'Alex', 'last': 'Avery', 'section': 'A2 Fictional'}
        row = {**value, 'matched_by': 'email', 'files': [
            {'id': 'FICTIONAL_SOURCE_A', 'source_form_id': 'FICTIONAL_ORIGINAL_FORM',
             'source_response_id': 'FICTIONAL_EVENT_A', 'source_submitted_at': '2026-10-01T12:00:00Z'},
            {'id': 'FICTIONAL_SOURCE_B', 'source_response_id': 'FICTIONAL_EVENT_B'}]}
        score = {'aggregate_provenance': {'sources': [
            {'source': {'file_id': 'FICTIONAL_SOURCE_A', 'version': '7', 'modified_at': '2026-10-01T13:00:00Z'}},
            {'source': {'file_id': 'FICTIONAL_SOURCE_B', 'version': '3', 'modified_at': '2026-10-02T13:00:00Z'}}]}}
        before = deepcopy((value, row, score))
        result = morning.observation_rows([value], [(row, score)])
        self.assertEqual([s['version'] for s in result[0]['sources']], ['7', '3'])
        self.assertEqual([s['source_response_id'] for s in result[0]['sources']], ['FICTIONAL_EVENT_A', 'FICTIONAL_EVENT_B'])
        self.assertIsNone(result[0]['sources'][1]['source_form_id'])
        self.assertEqual((value, row, score), before)

    def test_missing_source_evidence_never_invents_metadata_or_holds_a_row(self):
        value = {'first': 'Alex', 'last': 'Avery', 'section': 'A2 Fictional', 'kind': 'score', 'notes': 8}
        self.assertEqual(morning.observation_rows([value], [({**value}, {'notes_score': 8})])[0]['sources'], [])
        self.assertEqual(value['notes'], 8)


class ObservationRouteTests(unittest.TestCase):
    def request(self, payload, current=None, denied=False):
        from hub_v2 import student_observations
        raw = json.dumps(payload).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/v2/api/morning/entry',
            'HTTP_ORIGIN': 'https://hub.fictional.example', 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        statuses = []
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired() if denied else None,
                          return_value='teacher@fictional.example'), \
                patch.object(google, 'service_token', return_value='fictional-token'), \
                patch.object(app, 'load_agenda') as agenda, \
                patch.object(morning, 'make_list', return_value=current) as make, \
                patch.object(student_observations, 'record', return_value={'recorded': True}) as record:
            result = app.application(env, lambda code, headers: statuses.append(code),
                {'HUB_STATE_PROBE_ORIGINS': 'https://hub.fictional.example'})
        return statuses[0], json.loads(b''.join(result)), record, make, agenda

    def test_result_publishes_only_evidence_without_source_assessment_or_new_grade_action(self):
        status, _, record, make, agenda = self.request({
            'schema': 'hub-morning-student-observations/v1', 'phase': 'result'})
        self.assertEqual(status, '200 OK')
        self.assertIsNone(record.call_args.kwargs['current'])
        make.assert_not_called(); agenda.assert_not_called()

    def test_prepared_uses_current_source_under_existing_route(self):
        current = {'code': 'ready'}
        status, _, record, make, _ = self.request({
            'schema': 'hub-morning-student-observations/v1', 'phase': 'prepared',
            'scope': {'class': 'biology', 'week': 'Wk 05', 'form': 'FICTIONAL_FORM_1234'}}, current)
        self.assertEqual(status, '200 OK')
        self.assertEqual(record.call_args.kwargs['current'], current)
        make.assert_called_once()

    def test_authentication_precedes_any_observation_read_or_write(self):
        status, _, record, make, agenda = self.request({
            'schema': 'hub-morning-student-observations/v1', 'phase': 'result'}, denied=True)
        self.assertEqual(status, '401 Unauthorized')
        record.assert_not_called(); make.assert_not_called(); agenda.assert_not_called()


class ExecutorLedgerIntegrationTests(unittest.TestCase):
    """Exercise actual JS executor output through the actual private recorder."""
    def test_saved_partial_and_no_change_panels_use_the_same_receipt_contract(self):
        from hub_v2 import student_observations as observations
        from school_workflow_hub.core.runs import content_hash
        from test_cloud_state import MemoryCAS
        from test_student_observations import fixture
        node = r"""
const fs=require('node:fs'),E=require('./browser-extension/entry.js');
const {payload,mode}=JSON.parse(fs.readFileSync(0,'utf8')),list=payload.list;
const rows=list.rows.map((r,i)=>({label:payload.target.label_code+' MP: Marking Period Score input for '+r.last+', '+r.first,
 name:r.last+', '+r.first,score:i===0?(mode==='checked'?10:mode==='manual_higher'?11:8):null,other:null,flags:[]}));
let state={context:{points:10,...payload.target},saved:true,rows},saves=0,reloads=0;
const clone=v=>JSON.parse(JSON.stringify(v)),plan=E.buildPlan(list,clone(state),'notes');
(async()=>{const result=await E.execute(plan,{
 snapshot:async()=>clone(state),fill:async c=>{state.rows.find(r=>r.label===c.label).score=c.value;state.saved=false;},
 save:async()=>{saves++;state.saved=true;},reload:async()=>{reloads++;if(mode==='partial')state.rows[0].score=9;return clone(state);}});
 process.stdout.write(JSON.stringify({observation:E.observation(plan,result),code:result.code,saves,reloads,
  reasons:E.REASON_CODES,codes:E.OBSERVATION_CODES,summary:E.hubResult(plan,result,'Fictional assignment')}));})();
"""
        for mode in ('saved', 'partial', 'checked', 'manual_higher'):
            with self.subTest(mode=mode):
                prepared, current = fixture()
                prepared['list']['sent_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                current['list']['sent_at'] = prepared['list']['sent_at']
                port = MemoryCAS(); port.now = datetime.now(timezone.utc)
                rows = []
                def append(_token, _sheet, _area, values):
                    rows.extend(deepcopy(values))
                with patch.object(observations.ledger, 'find', return_value={'id': 'FICTIONAL_LEDGER'}), \
                        patch.object(observations.ledger, '_first_tab', return_value="'Existing receipts'"), \
                        patch.object(google, 'sheet_values', side_effect=lambda *args: deepcopy(rows)), \
                        patch.object(google, 'append_rows', side_effect=append) as writer:
                    observations.record('read', 'write', {}, prepared, 'teacher@fictional.example', current=current, port=port)
                    output = subprocess.run(['node', '-e', node],
                        input=json.dumps({'payload': prepared, 'mode': mode}), text=True, capture_output=True,
                        cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)
                    actual = json.loads(output.stdout)
                    self.assertEqual(set(actual['reasons']), set(observations.REASON_CODES))
                    self.assertEqual(set(actual['codes']), set(observations.OBSERVATION_CODES))
                    result = {**prepared, 'phase': 'result', 'observation': actual['observation']}
                    port.now = datetime.now(timezone.utc)
                    ack = observations.record('read', 'write', {}, result, 'teacher@fictional.example', port=port)
                    self.assertEqual(ack['payload_sha256'], content_hash(result))
                    calls = writer.call_count
                    observations.record('read', 'write', {}, result, 'teacher@fictional.example', port=port)
                    self.assertEqual(writer.call_count, calls)
                values = [json.loads(row[6]) for row in rows if row[1] == observations.KIND
                          and json.loads(row[6])['phase'] == 'result']
                self.assertEqual(len(values), 2)
                self.assertEqual(values[0]['basis_row']['sources'][0]['version'], '34')
                self.assertFalse(values[0]['server_verified_ic'])
                self.assertEqual(values[0]['source_freshness'], 'verified_at_prepare_only')
                self.assertTrue(all(row[1] != 'Entered in IC' for row in rows))
                if mode in ('checked', 'manual_higher'):
                    self.assertEqual((actual['saves'], actual['reloads']), (0, 0))
                    self.assertEqual(values[0]['observation']['readback'], 'saved_panel_checked')
                    self.assertEqual(values[0]['observation']['after'], 11 if mode == 'manual_higher' else 10)
                else:
                    self.assertEqual((actual['saves'], actual['reloads']), (1, 1))
                    self.assertEqual(values[0]['observation']['after'], 9 if mode == 'partial' else 10)


if __name__ == '__main__':
    unittest.main()
