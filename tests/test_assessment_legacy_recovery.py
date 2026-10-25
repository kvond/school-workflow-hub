"""Surviving legacy evidence only: no UUID, original basis or raw HTTP status."""
from copy import deepcopy
from datetime import timedelta
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from hub_v2 import google
from school_workflow_hub.adapters import assessment_legacy_recovery as legacy
from school_workflow_hub.adapters import assessment_review as review, assessment_save as saving
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, instant, key
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from school_workflow_hub.web import application
from tests import test_assessment_fresh_review as fixtures


class LegacyInventoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.FreshReviewTests('runTest'); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture.f; self.config = fixtures.CONFIG
        self.packet = deepcopy(fixture.current)
        for answer in self.packet['responses']:
            answer.update(critical_aspect='', rubric_version='critical-aspect-4/v1', explanation_level='')
        self.record_id = self.f.rid
        self.scope = review.task_scope(self.packet)
        self.task_id = CloudState(self.f.port).create_task(self.scope)
        self.run_id = 'run_' + 'b' * 64  # opaque surviving ID, not a recoverable request UUID
        self.completed_id = key('completed', {'task': self.task_id, 'input_hash': content_hash(self.packet)})
        self.receipt_id = key('assessmentlegacyrecovery', {'task': self.task_id, 'run': self.run_id})
        self.attempted_id = records.record_id(self.packet)
        self.started = self.f.port.now.isoformat()
        expires = (self.f.port.now + timedelta(seconds=300)).isoformat()
        task_doc = self.f.port.rows[self.task_id]
        self.original_last_result = {'run_id': 'run_' + 'c' * 64, 'result_hash': 'd' * 64,
            'record_ids': {}, 'stored_at': self.started, 'checkpoint_token': None}
        task = {**task_doc.value, 'status': 'Working', 'active_run': self.run_id,
            'owner': 'historical-assessment-owner', 'fence': 2, 'expires_at': expires,
            'last_result': deepcopy(self.original_last_result), 'unknown_historical_field': 'preserve'}
        run = {'task_id': self.task_id, 'owner': task['owner'], 'input_hash': content_hash(self.packet),
            'fence': 2, 'expires_at': expires, 'started_at': self.started, 'receipt': None}
        self.f.port.commit({self.task_id: (task, task_doc.version), self.run_id: (run, None)})
        # The UI observed missing columns, but no raw status/basis/UUID survives.
        rows = records.build_rows(self.packet, records.completion(self.packet), self.f.capture, now=saving._now())
        with self.assertRaisesRegex(ValueError, 'assessment_column_missing'):
            saving._append(self.f.book['Answers'], rows['answers'][0])
        self.f.book['Answers']['headers'] += ['critical_aspect', 'rubric_version']
        self.first_absence = (self.f.port.now + timedelta(seconds=26)).isoformat()
        self.f.port.advance(301)
        self.actor = patch.object(google, 'verify_teacher', return_value='operator@example.invalid')
        self.actor.start(); self.addCleanup(self.actor.stop)
        self.evidence = {'schema': legacy.EVIDENCE_SCHEMA, 'observation': 'missing_report_columns_ui',
            'evidence_ref': 'private-synthetic-manifest#save_attempt_evidence',
            'attempt_started_at': self.started, 'first_absence_at': self.first_absence,
            'baseline_unchanged_attested': True, 'no_native_write_attested': True}

    def inspect(self, **options):
        return legacy.inspect('fictional-iap', self.record_id, self.config, **options)

    def payload(self):
        return {'mode': 'legacy_ui_prewrite', 'record_id': self.record_id, 'packet': deepcopy(self.packet),
                'challenge': self.inspect()['challenge'], 'historical_evidence': deepcopy(self.evidence)}

    def reconcile(self, payload):
        return legacy.reconcile('fictional-iap', payload, self.config)

    def assert_held(self, payload):
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with self.assertRaises((ValueError, Conflict, StoreUnavailable, saving.ConnectionError)):
            self.reconcile(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 0)

    def http(self, method='GET', payload=None, query=None, origin='https://fictional.example'):
        raw = json.dumps(payload).encode() if payload is not None else b''
        env = {'PATH_INFO': '/api/assessment-recovery', 'REQUEST_METHOD': method,
            'QUERY_STRING': urlencode(query or {}), 'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional-iap',
            'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        statuses = []
        with patch.dict(os.environ, {**self.config, 'HUB_STATE_PROBE_ORIGINS': origin}, clear=True):
            reply = b''.join(application(env, lambda status, headers: statuses.append(status)))
        return statuses[0], json.loads(reply)

    def test_surviving_inventory_requires_no_retained_uuid_original_basis_or_http_status(self):
        before = deepcopy(self.f.port.rows)
        loaded = self.inspect()
        self.assertEqual(loaded['status'], 'legacy_pending_review')
        self.assertEqual(loaded['challenge']['run_id'], self.run_id)
        self.assertEqual(loaded['challenge']['input_hash'], content_hash(self.packet))
        self.assertIsNone(loaded['retained_attempt'])
        self.assertFalse(loaded['original_review_basis_available'])
        self.assertIsNone(loaded['original_baseline_sha256'])
        for field in ('request_id', 'packet', 'review_basis', 'http_status', 'source'):
            self.assertNotIn(field, self.f.port.rows[self.run_id].value)
        self.assertEqual(len(self.packet['responses']), 29)
        self.assertEqual(sum(a['judgment'] == 'meaningful_attempt' for a in self.packet['responses']), 8)
        self.assertEqual(self.f.port.rows, before); self.assertEqual(self.f.batches, 0)

    def test_exact_attested_legacy_failure_closes_only_its_three_private_documents(self):
        payload = self.payload(); before, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with patch.object(CloudState, 'claim', side_effect=AssertionError('No claim adoption')), \
             patch.object(CloudState, 'renew', side_effect=AssertionError('No lease renewal')), \
             patch.object(CloudState, 'finish', side_effect=AssertionError('No grade finish')), \
             patch.object(saving, '_batch', side_effect=AssertionError('No native writes')):
            reply = self.reconcile(payload)
        self.assertEqual(reply['status'], 'reconciled'); self.assertEqual(reply['grade_writes'], 0)
        self.assertTrue(reply['readback_verified'])
        self.assertEqual(set(self.f.port.rows) - set(before), {self.receipt_id})
        task = self.f.port.rows[self.task_id].value
        for field, value in before[self.task_id].value.items():
            if field not in ('active_run', 'status', 'next_action', 'last_recovery'):
                self.assertEqual(task[field], value)
        self.assertIsNone(task['active_run']); self.assertEqual(task['fence'], 2)
        self.assertEqual(self.f.port.rows[self.run_id].value,
            {**before[self.run_id].value, 'reconciliation': {'record_id': self.receipt_id, 'status': 'verified_no_write',
                                                           'receipt_sha256': content_hash(reply['receipt'])}})
        receipt = reply['receipt']
        self.assertEqual(receipt['assessment_record_id'], self.attempted_id)
        self.assertIsNone(receipt['original_baseline_sha256'])
        self.assertFalse(receipt['original_review_basis_available'])
        self.assertEqual(receipt['historical_evidence'], self.evidence)
        self.assertIn('operator_attested_ui', receipt['historical_evidence_classification'])
        self.assertNotIn('http_status', receipt['historical_evidence'])
        self.assertEqual(self.f.book, book); self.assertEqual(self.f.batches, 0)

    def test_native_record_in_either_table_unknown_partial_or_changed_packet_stays_held(self):
        payload = self.payload(); original = deepcopy(self.f.book)
        rows = records.build_rows(self.packet, records.completion(self.packet), self.f.capture, now=saving._now())
        for title, added in [('Submissions', rows['submission']), ('Answers', rows['answers'][0])]:
            self.f.book = deepcopy(original)
            self.f.book[title]['rows'].append((999, added))
            with self.subTest(title=title): self.assert_held(payload)
        self.f.book = original
        changed = deepcopy(payload); changed['packet']['feedback'] += ' Unapproved change.'
        self.assert_held(changed)

    def test_missing_packet_status_or_historical_attestation_is_not_invented(self):
        payload = self.payload()
        variants = []
        missing = deepcopy(payload); del missing['packet']; variants.append(missing)
        for field, value in [('observation', 'unknown_failure'), ('no_native_write_attested', False),
                ('baseline_unchanged_attested', False), ('evidence_ref', ''),
                ('attempt_started_at', '2020-01-01T00:00:00Z'), ('first_absence_at', '2999-01-01T00:00:00Z')]:
            changed = deepcopy(payload); changed['historical_evidence'][field] = value; variants.append(changed)
        invented = deepcopy(payload); invented['historical_evidence']['http_status'] = 400; variants.append(invented)
        for value in variants:
            with self.subTest(evidence=value.get('historical_evidence')): self.assert_held(value)

    def test_source_baseline_headers_owner_fence_versions_and_completion_changes_hold(self):
        payload = self.payload(); original_state, original_book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        for kind, field, value in [('task', 'owner', 'other-owner'), ('run', 'owner', 'other-owner'),
                ('task', 'fence', 3), ('run', 'fence', 3), ('run', 'input_hash', 'f' * 64),
                ('run', 'receipt', {'uncertain': True}), ('task', 'expires_at', '2999-01-01T00:00:00Z')]:
            self.f.port.rows = deepcopy(original_state)
            ident = self.task_id if kind == 'task' else self.run_id
            doc = self.f.port.rows[ident]; self.f.port.commit({ident: ({**doc.value, field: value}, doc.version)})
            with self.subTest(kind=kind, field=field): self.assert_held(payload)
        self.f.port.rows = deepcopy(original_state)
        self.f.port.commit({self.completed_id: ({'existing_completion': True}, None)}); self.assert_held(payload)
        self.f.port.rows = deepcopy(original_state)
        self.f.book['Answers']['headers'].remove('rubric_version'); self.assert_held(payload)
        self.f.book = deepcopy(original_book)
        self.f.book['Answers']['rows'][0][1]['conceptual_feedback'] = 'Changed teacher record'; self.assert_held(payload)
        self.f.book = original_book
        self.f.capture['file']['version'] = '35'; self.assert_held(payload)

    def test_authentication_precedes_private_state_source_and_workbook_reads(self):
        payload = self.payload()
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired), \
             patch.object(saving, 'configured_store', side_effect=AssertionError('No unauthenticated state')), \
             patch.object(review, 'read_workbook', side_effect=AssertionError('No unauthenticated book')):
            for action in (self.inspect, lambda: self.reconcile(payload)):
                with self.assertRaisesRegex(saving.ConnectionError, 'sign_in_required'): action()

    def test_lost_ack_reads_exact_closure_once_and_unavailable_readback_remains_unknown(self):
        payload = self.payload(); commit = self.f.port.commit; calls = []
        def lost(changes):
            calls.append(changes); commit(changes); raise StoreUnavailable('synthetic lost acknowledgement')
        with patch.object(self.f.port, 'commit', side_effect=lost):
            reply = self.reconcile(payload)
        self.assertTrue(reply['readback_verified']); self.assertEqual(len(calls), 1)
        state = deepcopy(self.f.port.rows)
        with patch.object(self.f.port, 'commit', side_effect=AssertionError('Readback only')):
            self.assertEqual(self.reconcile(payload)['status'], 'already_reconciled')
            self.assertEqual(self.inspect(run_id=self.run_id)['receipt'], reply['receipt'])
        self.assertEqual(self.f.port.rows, state)

    def test_normal_fresh_review_and_one_new_save_after_closure_preserve_old_history(self):
        payload = self.payload(); reply = self.reconcile(payload)
        loaded = review.load('fictional-iap', self.record_id, self.config, mode=review.FRESH_MODE)
        saved = saving.save('fictional-iap', {'request_id': 'new-ordinary-reviewed-save', 'packet': self.packet,
            'review_basis': {**loaded['review_basis'], 'packet_sha256': content_hash(self.packet)}}, self.config)
        self.assertTrue(saved['readback_verified']); self.assertEqual(self.f.batches, 1)
        state = deepcopy(self.f.port.rows)
        self.assertEqual(self.reconcile(payload)['receipt'], reply['receipt'])
        self.assertEqual(self.inspect(run_id=self.run_id)['receipt'], reply['receipt'])
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.port.rows[self.task_id].value['fence'], 3)
        # A later active operation does not hide or get cleared by this old receipt.
        later = CloudState(self.f.port).claim(self.task_id, owner='later-approved-owner',
            request_id='later-independent-assessment-request', input_hash='later-reviewed-input', ttl_seconds=300)
        state = deepcopy(self.f.port.rows)
        self.assertEqual(self.inspect(run_id=self.run_id)['receipt'], reply['receipt'])
        self.assertEqual(self.reconcile(payload)['receipt'], reply['receipt'])
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.port.rows[self.task_id].value['active_run'], later.run_id)
        with self.assertRaises(Conflict): self.inspect()

    def test_normal_authenticated_http_get_and_post_do_not_require_lost_fields(self):
        state = deepcopy(self.f.port.rows)
        status, loaded = self.http(query={'record_id': self.record_id})
        self.assertEqual(status, '200 OK'); self.assertEqual(loaded['status'], 'legacy_pending_review')
        self.assertEqual(self.f.port.rows, state)
        payload = self.payload()
        status, saved = self.http('POST', payload)
        self.assertEqual(status, '200 OK'); self.assertEqual(saved['grade_writes'], 0)
        status, readback = self.http(query={'record_id': self.record_id, 'run_id': self.run_id})
        self.assertEqual(status, '200 OK'); self.assertEqual(readback['receipt'], saved['receipt'])

    def test_corrupt_receipt_or_original_run_never_passes_historical_readback(self):
        payload = self.payload(); self.reconcile(payload); closed = deepcopy(self.f.port.rows)
        for field, value in [('historical_evidence', None), ('current_baseline_sha256', 'f' * 64),
                ('schema_proof', {}), ('actor', 'substituted@example.invalid')]:
            self.f.port.rows = deepcopy(closed)
            doc = self.f.port.rows[self.receipt_id]
            changed = deepcopy(doc.value)
            if value is None: del changed[field]
            else: changed[field] = value
            self.f.port.commit({self.receipt_id: (changed, doc.version)})
            state = deepcopy(self.f.port.rows)
            with self.subTest(field=field), self.assertRaises(Conflict): self.inspect(run_id=self.run_id)
            self.assertEqual(self.f.port.rows, state)
        self.f.port.rows = deepcopy(closed)
        run = self.f.port.rows[self.run_id]
        self.f.port.commit({self.run_id: ({**run.value, 'unknown_new_history': 'changed'}, run.version)})
        with self.assertRaises(Conflict): self.reconcile(payload)

    def test_competing_task_version_wins_without_closure_retry_or_other_operation_hold(self):
        payload = self.payload(); original_commit = self.f.port.commit; mutations = []
        def race(changes):
            mutations.append(changes)
            old = self.f.port.rows[self.task_id]
            original_commit({self.task_id: ({**old.value, 'owner': 'new-owner', 'fence': 3}, old.version)})
            original_commit(changes)
        with patch.object(self.f.port, 'commit', side_effect=race), self.assertRaises(Conflict):
            self.reconcile(payload)
        self.assertEqual(len(mutations), 1)
        self.assertNotIn(self.receipt_id, self.f.port.rows)
        self.assertEqual(self.f.port.rows[self.task_id].value['owner'], 'new-owner')
        self.assertEqual(self.f.batches, 0)
        # A separate student task remains processable and is never globally held.
        other_scope = {**self.scope, 'assignment_key': 'e' * 40}
        other = CloudState(self.f.port).create_task(other_scope)
        claimed = CloudState(self.f.port).claim(other, owner='unrelated-owner',
            request_id='unrelated-approved-request', input_hash='unrelated-current-input', ttl_seconds=300)
        self.assertEqual(claimed.owner, 'unrelated-owner')

    def test_source_change_during_last_native_read_leaves_original_attempt_held(self):
        payload = self.payload(); state = deepcopy(self.f.port.rows); calls = []
        def native_read(config):
            calls.append(True)
            if len(calls) == 2:
                self.f.capture['file']['version'] = '35'
            return deepcopy(self.f.book)
        with patch.object(review, 'read_workbook', side_effect=native_read), self.assertRaises(Conflict):
            self.reconcile(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.batches, 0)

    def test_unavailable_closure_acknowledgement_is_unknown_then_read_only_recoverable(self):
        payload = self.payload(); original_commit, original_read = self.f.port.commit, self.f.port.read
        committed = []
        def commit(changes):
            original_commit(changes); committed.append(True); raise StoreUnavailable('lost reply')
        def read(ids):
            if committed: raise StoreUnavailable('readback unavailable')
            return original_read(ids)
        with patch.object(self.f.port, 'commit', side_effect=commit), \
             patch.object(self.f.port, 'read', side_effect=read), self.assertRaises(StoreUnavailable):
            self.reconcile(payload)
        self.assertEqual(len(committed), 1)
        with patch.object(self.f.port, 'commit', side_effect=AssertionError('Never retry mutation')):
            self.assertEqual(self.inspect(run_id=self.run_id)['status'], 'already_reconciled')
        self.assertEqual(self.f.batches, 0)

    def test_future_retained_original_basis_is_additional_proof_never_discarded(self):
        from school_workflow_hub.adapters import assessment_attempts
        base = review.baseline(self.f.book, self.record_id)
        basis = {'mode': review.FRESH_MODE, 'record_id': self.record_id,
            'record_sha256': content_hash(base), 'source': review.source_binding(self.f.capture),
            'packet_sha256': content_hash(self.packet)}
        # This distinct future fixture has a retained request; the legacy case above does not.
        request_id = 'future-retained-reviewed-request'
        run_id = key('run', {'task': self.task_id, 'request': request_id})
        old_task = self.f.port.rows[self.task_id]
        old_run = self.f.port.rows[self.run_id]
        self.f.port.commit({self.task_id: ({**old_task.value, 'active_run': run_id}, old_task.version),
                            run_id: (deepcopy(old_run.value), None)})
        self.run_id = run_id
        request = {'request_id': request_id, 'packet': self.packet, 'review_basis': basis}
        assessment_attempts.record(self.f.port, self.task_id, request, review.source_binding(self.f.capture))
        loaded = self.inspect()
        self.assertTrue(loaded['original_review_basis_available'])
        self.assertEqual(loaded['original_baseline_sha256'], basis['record_sha256'])
        payload = {'mode': 'legacy_ui_prewrite', 'record_id': self.record_id, 'packet': self.packet,
            'challenge': loaded['challenge'], 'historical_evidence': self.evidence}
        result = self.reconcile(payload)
        self.assertTrue(result['receipt']['original_review_basis_available'])
        self.assertEqual(result['receipt']['original_baseline_sha256'], basis['record_sha256'])

    def test_stale_retained_original_basis_cannot_downgrade_to_legacy_missing_proof(self):
        from school_workflow_hub.adapters import assessment_attempts
        request_id = 'retained-stale-original-basis-request'
        run_id = key('run', {'task': self.task_id, 'request': request_id})
        old_task, old_run = self.f.port.rows[self.task_id], self.f.port.rows[self.run_id]
        self.f.port.commit({self.task_id: ({**old_task.value, 'active_run': run_id}, old_task.version),
                            run_id: (deepcopy(old_run.value), None)})
        self.run_id = run_id
        stale_basis = {'mode': review.FRESH_MODE, 'record_id': self.record_id,
            'record_sha256': 'f' * 64, 'source': review.source_binding(self.f.capture),
            'packet_sha256': content_hash(self.packet)}
        assessment_attempts.record(self.f.port, self.task_id,
            {'request_id': request_id, 'packet': self.packet, 'review_basis': stale_basis},
            review.source_binding(self.f.capture))
        before = deepcopy(self.f.port.rows)
        with self.assertRaises(Conflict): self.inspect()
        self.assertEqual(self.f.port.rows, before); self.assertEqual(self.f.batches, 0)
