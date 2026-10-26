"""Disposable Helper audit fixtures; no IC, grade, model or provider calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from datetime import timedelta
import json
from threading import Barrier, Lock
import unittest
from unittest.mock import patch

from hub_v2 import google, ledger, student_observations as observations
from school_workflow_hub.core.cloud_state import Document, StoreUnavailable
from test_cloud_state import MemoryCAS
from test_morning_eligible_entry import fixture as entry_fixture, NOW, SENT, AT


ACTOR = 'teacher@example.test'


def fixture():
    original, current = entry_fixture(checks=1)
    basis = {'schema': observations.BASIS_SCHEMA, 'revision': original['revision'], 'rows': []}
    for index, row in enumerate(original['list']['rows']):
        basis['rows'].append({'row_index': index,
            'identity': {**{k: row[k] for k in ('first', 'last', 'section')}, 'match_basis': 'email'},
            'sources': [{'file_id': 'FICTIONAL_SOURCE_%d' % index, 'version': '34',
                         'modified_at': SENT, 'source_form_id': original['scope']['form'],
                         'source_response_id': 'fictional-response-%d' % index, 'source_submitted_at': SENT}]})
    payload = {'schema': observations.SCHEMA, 'phase': 'prepared', 'run_id': original['run_id'],
        'list': original['list'], 'scope': original['scope'], 'revision': original['revision'],
        'target': original['targets'][0], 'basis': basis, 'observation': None}
    current['observation_basis'] = deepcopy(basis)
    return payload, current


def result(prepared):
    payload = deepcopy(prepared)
    payload['phase'] = 'result'
    payload['observation'] = {'schema': observations.PANEL_SCHEMA,
        'started_at': (NOW - timedelta(minutes=2)).isoformat(), 'finished_at': AT,
        'save_attempted': True, 'save_confirmed': True, 'readback_status': 'complete',
        'code': 'read_back', 'rows': [
            {'hub_row_index': 0, 'ic_name': 'Avery, Alex',
             'label': 'W03F MP: Marking Period Score input for Avery, Alex',
             'planned_action': 'raise', 'reason_code': 'lower_score_raise',
             'before': 8, 'proposed': 10, 'before_flags': [], 'fill_attempted': True,
             'fill_returned': True, 'readback': 'reloaded_match', 'after': 10,
             'after_flags': [], 'observed_at': AT},
            {'hub_row_index': 1, 'ic_name': 'Held0, Alex',
             'label': 'W03F MP: Marking Period Score input for Held0, Alex',
             'planned_action': 'leave', 'reason_code': 'source_needs_check',
             'before': None, 'proposed': None, 'before_flags': [], 'fill_attempted': False,
             'fill_returned': False, 'readback': 'reloaded_match', 'after': None,
             'after_flags': [], 'observed_at': AT}]}
    return payload


class StudentObservationTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.current = fixture()
        self.port = MemoryCAS()
        self.port.now = NOW
        self.rows = []
        self.rows_lock = Lock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(ledger, 'find', return_value={'id': 'FICTIONAL_LEDGER'}))
        stack.enter_context(patch.object(ledger, '_first_tab', return_value="'Existing receipts'"))
        self.read = stack.enter_context(patch.object(google, 'sheet_values', side_effect=self._read))
        self.append = stack.enter_context(patch.object(google, 'append_rows', side_effect=self._append))

    def _read(self, *_args):
        with self.rows_lock:
            return deepcopy(self.rows)

    def _append(self, _token, _sheet, _area, rows):
        with self.rows_lock:
            self.rows.extend(deepcopy(rows))

    def save(self, payload=None, *, current=None, actor=ACTOR):
        return observations.record('fictional-read', 'fictional-write', {},
            self.payload if payload is None else payload, actor,
            current=self.current if current is None else current, port=self.port)

    def values(self, phase):
        return [json.loads(row[6]) for row in self.rows if len(row) == 7 and row[1] == observations.KIND
                and json.loads(row[6])['phase'] == phase]

    def test_prepared_exact_source_evidence_private_rows_and_ack(self):
        before = deepcopy((self.payload, self.current))
        ack = self.save()
        self.assertEqual((self.payload, self.current), before)
        self.assertEqual(ack, {**observations._ack(self.payload, observations.event_id(self.payload), 2)})
        values = self.values('prepared')
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]['basis_row'], self.payload['basis']['rows'][0])
        self.assertEqual(values[0]['actor'], ACTOR)
        self.assertEqual(values[0]['provenance'], 'prepared_source_list')
        self.assertFalse(values[0]['server_verified_ic'])
        self.assertTrue(all(v['observation'] is None and v['panel'] is None for v in values))
        self.assertEqual({row[1] for row in self.rows[1:]}, {observations.KIND})
        self.assertEqual(len(self.port.rows), 1)  # No CloudState task, run or grade claim.

    def test_prepared_duplicate_readback_retains_original_server_time(self):
        ack = self.save()
        original = deepcopy(self.port.rows)
        self.port.advance(86400)
        self.assertEqual(self.save(current={'code': 'source_changed'}), ack)
        self.append.assert_called_once()
        self.assertEqual(self.port.rows, original)

    def test_unknown_source_version_and_original_event_fields_remain_unknown(self):
        for field in observations.SOURCE_FIELDS - {'file_id'}:
            self.payload['basis']['rows'][1]['sources'][0][field] = None
        self.payload['basis']['rows'][0]['identity']['match_basis'] = 'file name'
        self.current['observation_basis'] = deepcopy(self.payload['basis'])
        self.save()
        self.assertIsNone(self.values('prepared')[1]['basis_row']['sources'][0]['version'])

    def test_prepared_changed_source_rows_mapping_or_basis_reject_before_cas(self):
        changes = [lambda c: c.update(revision='b' * 64),
                   lambda c: c['list']['rows'][0].update(notes=9),
                   lambda c: c['targets'][0].update(assignment_id='999'),
                   lambda c: c['observation_basis']['rows'][0]['sources'][0].update(version='35')]
        for change in changes:
            current = deepcopy(self.current)
            change(current)
            with self.subTest(current=current), self.assertRaises(observations.Refused):
                self.save(current=current)
        self.assertEqual(self.port.rows, {})
        self.append.assert_not_called()

    def test_prepared_three_hour_ttl_and_explicit_identity_shape(self):
        old = deepcopy(self.payload)
        old['list']['sent_at'] = (NOW - timedelta(hours=4)).isoformat()
        with self.assertRaisesRegex(observations.Refused, 'list_expired'):
            self.save(old)
        injected = deepcopy(self.payload)
        injected['basis']['rows'][0]['identity']['official_student_id'] = '999'
        with self.assertRaises(observations.Refused):
            self.save(injected)
        self.append.assert_not_called()

    def test_result_retains_actual_raise_held_row_and_flags_without_ic_stamp(self):
        self.save()
        payload = result(self.payload)
        ack = self.save(payload)
        values = self.values('result')
        self.assertEqual(ack['row_count'], 2)
        self.assertEqual(values[0]['observation'], payload['observation']['rows'][0])
        self.assertEqual(values[1]['hub_row']['kind'], 'check')
        self.assertFalse(any(row[1] == ledger.KINDS['ic'] for row in self.rows))
        self.assertTrue(all(not v['server_verified_ic'] for v in values))

    def test_later_result_binds_old_prepared_evidence_without_current_source_claim(self):
        self.save()
        self.port.advance(7 * 86400)
        self.save(result(self.payload), current={'code': 'source_changed'})
        self.assertEqual(self.values('result')[0]['basis_row']['sources'][0]['version'], '34')
        self.assertEqual(self.values('result')[0]['source_freshness'], 'verified_at_prepare_only')

    def test_unavailable_operation_reply_records_unknown_for_each_prepared_identity(self):
        self.save()
        payload = {**deepcopy(self.payload), 'phase': 'result'}
        self.save(payload)
        values = self.values('result')
        self.assertEqual(len(values), 2)
        self.assertTrue(all(v['provenance'] == 'observation_reply_unavailable' and
                            v['observation'] is None and v['panel'] is None for v in values))
        self.assertEqual(values[0]['basis_row']['identity']['last'], 'Avery')

    def test_result_requires_exact_prepared_request_and_same_authenticated_actor(self):
        with self.assertRaisesRegex(observations.Refused, 'prepared_missing'):
            self.save(result(self.payload))
        self.save()
        for change in (lambda p: p['basis']['rows'][0]['sources'][0].update(version='35'),
                       lambda p: p['list']['rows'][0].update(reason='different')):
            payload = result(self.payload); change(payload)
            with self.assertRaisesRegex(observations.Refused, 'conflict'):
                self.save(payload)
        with self.assertRaisesRegex(observations.Refused, 'conflict'):
            self.save(result(self.payload), actor='other@example.test')
        self.append.assert_called_once()

    def test_unknown_ic_name_stays_unmapped_and_cannot_receive_fill(self):
        self.save()
        payload = result(self.payload)
        unknown = deepcopy(payload['observation']['rows'][1])
        unknown.update(hub_row_index=None, ic_name='Fictional, Unlisted', reason_code='no_hub_match',
                       label='W03F MP: Marking Period Score input for Fictional, Unlisted')
        payload['observation']['rows'][1] = unknown
        self.save(payload)
        saved = self.values('result')[1]
        self.assertIsNone(saved['basis_row']); self.assertIsNone(saved['hub_row'])
        self.assertIsNone(saved['observation']['hub_row_index'])

    def test_keep_higher_actual_ic_value_above_ten_is_not_clamped(self):
        self.save()
        payload = result(self.payload)
        panel = payload['observation']
        panel.update(code='nothing_to_enter', save_attempted=False, save_confirmed=False, readback_status='not_attempted')
        for row in panel['rows']:
            row.update(readback='saved_panel_checked', fill_attempted=False, fill_returned=False)
        panel['rows'][0].update(planned_action='keep', reason_code='higher_ic_score', before=11, after=11)
        self.save(payload)
        self.assertEqual(self.values('result')[0]['observation']['after'], 11)

    def test_incomplete_reload_retains_save_confirmation_but_no_per_row_readback(self):
        self.save()
        payload = result(self.payload)
        payload['observation'].update(code='readback_incomplete', readback_status='incomplete')
        for row in payload['observation']['rows']:
            row.update(readback='unavailable', after=None, after_flags=None, observed_at=None)
        self.save(payload)
        self.assertTrue(self.values('result')[0]['panel']['save_confirmed'])
        self.assertEqual(self.values('result')[0]['observation']['readback'], 'unavailable')

    def test_complete_readback_mismatch_retains_actual_value_without_success_claim(self):
        self.save()
        payload = result(self.payload)
        payload['observation']['code'] = 'readback_mismatch'
        payload['observation']['rows'][0].update(after=9, readback='reloaded_mismatch')
        self.save(payload)
        saved = self.values('result')[0]
        self.assertEqual(saved['observation']['after'], 9)
        self.assertEqual(saved['observation']['readback'], 'reloaded_mismatch')
        self.assertFalse(saved['server_verified_ic'])

    def test_existing_keep_and_readback_float_tolerance_is_preserved(self):
        self.save()
        payload = result(self.payload)
        panel = payload['observation']
        panel.update(code='nothing_to_enter', save_attempted=False, save_confirmed=False, readback_status='not_attempted')
        for row in panel['rows']:
            row.update(readback='saved_panel_checked', fill_attempted=False, fill_returned=False)
        panel['rows'][0].update(planned_action='keep', reason_code='same_score',
                                before=9.9999999995, after=10)
        self.save(payload)
        self.assertEqual(self.values('result')[0]['observation']['before'], 9.9999999995)

    def test_internally_consistent_future_result_is_not_native_past_evidence(self):
        self.save()
        payload = result(self.payload)
        panel = payload['observation']
        panel.update(started_at=(NOW + timedelta(hours=1)).isoformat(),
                     finished_at=(NOW + timedelta(hours=1, minutes=1)).isoformat())
        for row in panel['rows']:
            row['observed_at'] = panel['finished_at']
        with self.assertRaises(observations.Refused):
            self.save(payload)
        self.append.assert_called_once()

    def test_contradictory_plan_boolean_nonfinite_or_fake_confirmation_reject(self):
        self.save()
        changes = [lambda p: p['observation']['rows'][0].update(hub_row_index=1),
                   lambda p: p['observation']['rows'][0].update(before=True),
                   lambda p: p['observation']['rows'][0].update(after=float('nan')),
                   lambda p: p['observation']['rows'][0].update(after=9),
                   lambda p: p['observation']['rows'][1].update(planned_action='fill', proposed=0, fill_attempted=True),
                   lambda p: p['observation']['rows'][0].update(planned_action='keep', reason_code='same_score', fill_attempted=False, fill_returned=False),
                   lambda p: p['observation'].update(save_attempted=False),
                   lambda p: p['observation']['rows'][0].update(label='different destination'),
                   lambda p: p['observation']['rows'][0].update(before_flags=['Exempt']),
                   lambda p: p['observation']['rows'][0].update(observed_at=(NOW + timedelta(days=1)).isoformat())]
        for change in changes:
            payload = result(self.payload); change(payload)
            with self.subTest(payload=payload), self.assertRaises(observations.Refused):
                self.save(payload)
        self.append.assert_called_once()

    def test_same_fixed_event_key_rejects_changed_payload_instead_of_new_record(self):
        ack = self.save()
        changed = deepcopy(self.payload)
        changed['basis']['rows'][0]['sources'][0]['version'] = '35'
        self.assertEqual(observations.event_id(changed), ack['event_id'])
        with self.assertRaisesRegex(observations.Refused, 'conflict'):
            self.save(changed)
        self.append.assert_called_once()

    def test_lost_append_acknowledgement_recovers_exact_rows_without_repeat(self):
        def append(*args):
            self._append(*args)
            raise google.GoogleError('fictional lost append acknowledgement', status=503)
        self.append.side_effect = append
        ack = self.save()
        self.assertTrue(ack['recorded'])
        self.save()
        self.append.assert_called_once()

    def test_uncertain_missing_append_never_repeats_after_restart(self):
        self.append.side_effect = google.GoogleError('fictional append unavailable', status=503)
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        self.port.advance(86400)
        for _ in range(2):
            with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
                self.save(current={'code': 'source_changed'})
        self.append.assert_called_once()
        self.assertEqual(next(iter(self.port.rows.values())).value['state'], 'append_started')

    def test_partial_append_stays_held_and_exact_external_readback_can_settle(self):
        def append(_token, _sheet, _area, rows):
            self.rows.extend(deepcopy(rows[:-1]))
        self.append.side_effect = append
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        state = next(iter(self.port.rows.values())).value
        self.rows.append(deepcopy(state['ledger_rows'][-1]))
        self.assertTrue(self.save()['recorded'])
        self.append.assert_called_once()

    def test_lost_reservation_ack_uses_exact_own_nonce_then_one_append(self):
        self.port.fail_after_commit = True
        self.assertTrue(self.save()['recorded'])
        self.append.assert_called_once()

    def test_unconfirmed_reservation_never_dispatches_append(self):
        self.port.commit = lambda _changes: (_ for _ in ()).throw(StoreUnavailable('fictional unavailable'))
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        self.append.assert_not_called()
        self.assertEqual(self.port.rows, {})

    def test_two_concurrent_publishers_have_one_append_owner(self):
        gate, original = Barrier(2), self.port.commit
        def commit(changes):
            if next(iter(changes.values()))[1] is None:
                gate.wait(timeout=3)
            return original(changes)
        self.port.commit = commit
        def attempt(_index):
            try:
                return self.save()
            except observations.Refused:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(attempt, range(2)))
        self.assertTrue(any(value and value['recorded'] for value in values))
        self.append.assert_called_once()
        self.assertEqual(len(self.values('prepared')), 2)
        self.assertTrue(self.save()['recorded'])

    def test_confirmation_commit_lost_ack_is_recovered_readonly(self):
        original = self.port.commit
        def commit(changes):
            if next(iter(changes.values()))[0]['state'] == 'recorded':
                self.port.fail_after_commit = True
            return original(changes)
        self.port.commit = commit
        self.assertTrue(self.save()['recorded'])
        self.append.assert_called_once()

    def test_recorded_receipt_requires_actual_ledger_readback_and_no_duplicate_rows(self):
        self.save()
        last = self.rows.pop()
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        self.rows.extend([last, deepcopy(last)])
        with self.assertRaisesRegex(observations.Refused, 'append_uncertain'):
            self.save()
        self.append.assert_called_once()

    def test_history_cap_and_sentinel_stop_without_state_or_deletion(self):
        for count, cap in ((4, 4), (5, 4), (3, 4)):
            self.rows = [['historical']] * count
            before = deepcopy(self.rows)
            with self.subTest(count=count), patch.object(ledger, 'MAX_ROWS', cap):
                with self.assertRaisesRegex(observations.Refused, 'history_full'):
                    self.save()
            self.assertEqual(self.rows, before)
            self.assertEqual(self.port.rows, {})
        self.assertTrue(all(call.args[2].endswith('G5') for call in self.read.call_args_list))
        self.append.assert_not_called()

    def test_malformed_private_ledger_history_and_changed_state_hold(self):
        self.rows = [['', observations.KIND, '', '', '', '', '{broken']]
        with self.assertRaisesRegex(observations.Refused, 'history_unverified'):
            self.save()
        self.assertEqual(self.port.rows, {})
        self.rows = []
        self.save()
        ident = observations.event_id(self.payload)
        old = self.port.rows[ident]
        value = deepcopy(old.value); value['payload_sha256'] = 'b' * 64
        self.port.rows[ident] = Document(value, old.version)
        with self.assertRaisesRegex(observations.Refused, 'state_conflict'):
            self.save()
        self.append.assert_called_once()


if __name__ == '__main__':
    unittest.main()
