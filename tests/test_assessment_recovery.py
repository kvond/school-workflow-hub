"""One attested pre-write failure closes in fictional private state only."""
from copy import deepcopy
import io
import json
import os
import unittest
from unittest.mock import patch

from hub_v2 import google
from school_workflow_hub.adapters import assessment_recovery as recovery
from school_workflow_hub.adapters import assessment_review as review, assessment_save as saving
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from school_workflow_hub.web import application
from tests import test_assessment_fresh_review as fixtures


class PrewriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FreshReviewTests('runTest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f; self.config = fixtures.CONFIG
        self.packet = deepcopy(self.fixture.current)
        for answer in self.packet['responses']:
            answer.update(critical_aspect='', rubric_version='critical-aspect-4/v1', explanation_level='')
        self.save_payload = self.fixture.payload(self.packet)
        self.store = CloudState(self.f.port)
        self.task_id = self.store.create_task(review.task_scope(self.packet))
        self.claim = self.store.claim(self.task_id, owner='original-save-owner',
            request_id=self.save_payload['request_id'], input_hash=content_hash(self.packet), ttl_seconds=300)
        self.run_id = self.claim.run_id
        self.new_id = records.record_id(self.packet)
        self.completed_id = key('completed', {'task': self.task_id, 'input_hash': content_hash(self.packet)})
        self.recovery_id = key('assessmentprewriterecovery', {'task': self.task_id, 'run': self.run_id})
        rows = records.build_rows(self.packet, records.completion(self.packet), self.f.capture, now=saving._now())
        # Reproduce the original old-code pre-write failure after its claim;
        # the current saver detects this earlier and does not need recovery.
        with self.assertRaisesRegex(ValueError, 'assessment_column_missing'):
            saving._append(self.f.book['Answers'], rows['answers'][0])
        self.assertEqual(self.f.batches, 0)
        self.f.book['Answers']['headers'] += ['critical_aspect', 'rubric_version']
        self.f.port.advance(301)
        self.payload = {**deepcopy(self.save_payload), 'failure_receipt': {
            'schema': recovery.FAILURE_SCHEMA, 'run_id': self.run_id, 'fence': self.claim.fence,
            'http_status': 400, 'error_code': 'assessment_column_missing',
            'evidence_ref': 'fictional-original-http-response.private.json', 'prewrite_failure_attested': True}}
        self.actor = patch.object(google, 'verify_teacher', return_value='teacher@example.invalid')
        self.actor.start(); self.addCleanup(self.actor.stop)

    def reconcile(self, payload=None):
        return recovery.reconcile('fictional-signed-assertion', payload or self.payload, self.config)

    def assert_held(self, payload=None):
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError, KeyError)):
            self.reconcile(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book); self.assertEqual(self.f.batches, 0)

    def test_reconcile_changes_exactly_three_private_documents_preserving_original_history(self):
        task, run = deepcopy(self.f.port.rows[self.task_id].value), deepcopy(self.f.port.rows[self.run_id].value)
        before, book = set(self.f.port.rows), deepcopy(self.f.book)
        with patch.object(CloudState, 'claim', side_effect=AssertionError('No claim')), \
             patch.object(CloudState, 'renew', side_effect=AssertionError('No renew')), \
             patch.object(CloudState, 'release_for_review', side_effect=AssertionError('No release')), \
             patch.object(CloudState, 'finish', side_effect=AssertionError('No finish')), \
             patch.object(saving, '_batch', side_effect=AssertionError('No grade write')):
            result = self.reconcile()
        self.assertEqual(result['status'], 'reconciled'); self.assertTrue(result['readback_verified']); self.assertEqual(result['grade_writes'], 0)
        self.assertEqual(set(self.f.port.rows) - before, {self.recovery_id})
        expected_task = {**task, 'active_run': None, 'status': 'Needs you', 'next_action': 'verified_prewrite_failure_recovered'}
        self.assertEqual(self.f.port.rows[self.task_id].value, expected_task)
        self.assertEqual(self.f.port.rows[self.run_id].value, {**run, 'reconciliation': {'record_id': self.recovery_id, 'status': 'verified_no_write'}})
        receipt = result['receipt']; self.assertEqual(receipt['actor'], 'teacher@example.invalid')
        self.assertEqual(receipt['attestation_classification'], 'operator_attested_prewrite_failure')
        self.assertEqual(receipt['failure_receipt'], self.payload['failure_receipt'])
        self.assertNotIn('fictional-signed-assertion', str(receipt)); self.assertEqual(self.f.book, book); self.assertEqual(self.f.batches, 0)

    def test_headers_repaired_then_recovery_allows_only_new_fresh_load_and_one_normal_save(self):
        with self.assertRaisesRegex(Conflict, 'assessment_review_save_pending'):
            self.fixture.load()
        self.reconcile()
        with self.assertRaises(Conflict):
            saving.save('signed', self.save_payload, self.config)  # Old request cannot claim again.
        self.assertEqual(self.f.batches, 0)
        fresh = self.fixture.load()
        payload = {'request_id': 'new-teacher-reviewed-save-request', 'packet': self.packet,
            'review_basis': {**fresh['review_basis'], 'packet_sha256': content_hash(self.packet)}}
        result = saving.save('signed', payload, self.config)
        self.assertTrue(result['readback_verified']); self.assertEqual(self.f.batches, 1)
        old_run = deepcopy(self.f.port.rows[self.run_id])
        again = self.reconcile()
        self.assertEqual(again['status'], 'already_reconciled'); self.assertEqual(self.f.batches, 1)
        self.assertEqual(self.f.port.rows[self.run_id], old_run)
        self.assertEqual(self.f.port.rows[self.task_id].value['fence'], self.claim.fence + 1)

    def test_identical_replay_is_read_only_and_changed_attestation_conflicts(self):
        first = self.reconcile(); state = deepcopy(self.f.port.rows)
        with patch.object(saving, 'capture', side_effect=AssertionError('Idempotent read needs no new capture')), \
             patch.object(review, 'read_workbook', side_effect=AssertionError('No native revalidation after closure')):
            again = self.reconcile()
        self.assertEqual(again['receipt'], first['receipt']); self.assertEqual(again['status'], 'already_reconciled')
        self.assertEqual(self.f.port.rows, state)
        changed = deepcopy(self.payload); changed['failure_receipt']['evidence_ref'] = 'different-proof'
        self.assert_held(changed)

    def test_replay_refuses_any_changed_original_run_history(self):
        self.reconcile(); closed = deepcopy(self.f.port.rows)
        for field, value in [('owner', 'changed-owner'), ('expires_at', '2999-01-01T00:00:00Z'),
                ('started_at', '2001-01-01T00:00:00Z'), ('unexpected_historical_field', 'injected')]:
            self.f.port.rows = deepcopy(closed); old = self.f.port.rows[self.run_id]
            self.f.port.commit({self.run_id: ({**old.value, field: value}, old.version)})
            with self.subTest(field=field): self.assert_held()
        self.f.port.rows = deepcopy(closed)
        doc = self.f.port.rows[self.recovery_id]
        self.f.port.commit({self.recovery_id: ({**doc.value, 'run_id': 'run_' + 'f' * 64}, doc.version)})
        self.assert_held()

    def test_explicit_current_409_schema_failure_is_also_eligible(self):
        self.payload['failure_receipt']['http_status'] = 409
        result = self.reconcile()
        self.assertEqual(result['status'], 'reconciled'); self.assertEqual(self.f.batches, 0)
        self.assertEqual(result['receipt']['failure_receipt']['http_status'], 409)

    def test_only_exact_teacher_attested_original_coded_response_is_eligible(self):
        for field, value in [('schema', 'unknown'), ('http_status', 500), ('http_status', True),
                ('error_code', 'assessment_write_uncertain'), ('prewrite_failure_attested', False),
                ('fence', True), ('fence', self.claim.fence + 1), ('run_id', 'run_' + 'a' * 64),
                ('evidence_ref', ''), ('evidence_ref', 'x' * 401), ('evidence_ref', 'bad\nreference')]:
            changed = deepcopy(self.payload); changed['failure_receipt'][field] = value
            with self.subTest(field=field, value=value): self.assert_held(changed)
        extra = deepcopy(self.payload); extra['retry_save'] = True; self.assert_held(extra)

    def test_authentication_failure_precedes_private_state_and_source_reads(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired), \
             patch.object(saving, 'read_records', side_effect=AssertionError('Unauthenticated read')), \
             patch.object(saving, 'configured_store', side_effect=AssertionError('Unauthenticated state')):
            with self.assertRaisesRegex(saving.ConnectionError, 'sign_in_required'): self.reconcile()

    def test_unexpired_changed_owner_fence_hash_receipt_and_completed_index_hold(self):
        saved = deepcopy(self.f.port.rows)
        variants = [('task', 'owner', 'other-owner'), ('task', 'fence', 2), ('task', 'active_run', 'run-other'),
            ('run', 'owner', 'other-owner'), ('run', 'fence', 2), ('run', 'input_hash', 'a' * 64),
            ('run', 'receipt', {'uncertain': True}), ('task', 'expires_at', '2999-01-01T00:00:00Z'),
            ('run', 'expires_at', '2999-01-01T00:00:00Z')]
        for kind, field, value in variants:
            self.f.port.rows = deepcopy(saved); ident = self.task_id if kind == 'task' else self.run_id
            doc = self.f.port.rows[ident]
            self.f.port.commit({ident: ({**doc.value, field: value}, doc.version)})
            with self.subTest(kind=kind, field=field): self.assert_held()
        self.f.port.rows = deepcopy(saved); self.f.port.commit({self.completed_id: ({'unexpected': True}, None)})
        self.assert_held()

    def test_schema_missing_or_any_new_record_presence_in_either_native_table_holds(self):
        original = deepcopy(self.f.book)
        self.f.book['Answers']['headers'].remove('rubric_version'); self.assert_held()
        self.f.book = deepcopy(original)
        for title in ('Submissions', 'Answers'):
            self.f.book = deepcopy(original)
            row = records.build_rows(self.packet, records.completion(self.packet), self.f.capture, now=saving._now())
            extra = row['submission'] if title == 'Submissions' else row['answers'][0]
            self.f.book[title]['rows'].append((999, extra))
            self.assert_held()

    def test_native_source_basis_and_header_races_hold_without_private_closure(self):
        for mutate in (lambda: self.f.book['Submissions']['rows'][0][1].update(feedback='Concurrent teacher change'),
                       lambda: self.f.book['Answers']['headers'].append('Concurrent column')):
            before_book, before_capture = deepcopy(self.f.book), deepcopy(self.f.capture)
            calls = 0
            def workbook(config):
                nonlocal calls
                calls += 1
                if calls == 2: mutate()
                return deepcopy(self.f.book)
            state = deepcopy(self.f.port.rows)
            with patch.object(review, 'read_workbook', side_effect=workbook):
                with self.assertRaises((ValueError, Conflict)): self.reconcile()
            self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.batches, 0)
            self.f.book, self.f.capture = before_book, before_capture
        for mutate in (lambda: self.f.capture['file'].update(version='35'),
                       lambda: self.f.capture.update(raw_sha256='f' * 64)):
            before_capture = deepcopy(self.f.capture); calls = 0
            def capture(*args):
                nonlocal calls
                calls += 1
                if calls == 2: mutate()
                return deepcopy(self.f.capture)
            state = deepcopy(self.f.port.rows)
            with patch.object(saving, 'capture', side_effect=capture):
                with self.assertRaises((ValueError, Conflict)): self.reconcile()
            self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.batches, 0)
            self.f.capture = before_capture

    def test_task_version_race_at_commit_is_not_cleared_or_retried(self):
        original = self.f.port.commit; commits = []
        def commit(changes):
            commits.append(set(changes))
            if self.recovery_id in changes:
                doc = self.f.port.rows[self.task_id]
                original({self.task_id: ({**doc.value, 'owner': 'concurrent-owner'}, doc.version)})
            return original(changes)
        self.f.port.commit = commit
        with self.assertRaises(Conflict): self.reconcile()
        self.assertNotIn(self.recovery_id, self.f.port.rows)
        self.assertEqual(self.f.port.rows[self.task_id].value['owner'], 'concurrent-owner')
        self.assertEqual(len(commits), 1); self.assertEqual(self.f.batches, 0)

    def test_source_changes_during_final_workbook_read_never_close_original_run(self):
        reads = 0
        def workbook(config):
            nonlocal reads
            reads += 1
            if reads == 2: self.f.capture['file']['version'] = '35'
            return deepcopy(self.f.book)
        state = deepcopy(self.f.port.rows)
        with patch.object(review, 'read_workbook', side_effect=workbook), \
             patch.object(self.f.port, 'commit', wraps=self.f.port.commit) as commit:
            with self.assertRaisesRegex(Conflict, 'submission_changed'): self.reconcile()
        commit.assert_not_called(); self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.port.rows[self.task_id].value['active_run'], self.run_id)
        self.assertEqual(self.f.batches, 0)

    def test_lost_cas_acknowledgement_reads_exact_closure_without_another_mutation(self):
        self.f.port.fail_after_commit = True
        with patch.object(self.f.port, 'commit', wraps=self.f.port.commit) as commit:
            result = self.reconcile()
        self.assertEqual(result['status'], 'reconciled'); self.assertEqual(commit.call_count, 1)
        self.assertEqual(self.f.batches, 0)

    def test_unreadable_cas_outcome_stays_unknown_then_exact_replay_reads_existing_receipt(self):
        original_commit, original_read = self.f.port.commit, self.f.port.read
        unreadable = False
        def commit(changes):
            nonlocal unreadable
            original_commit(changes); unreadable = True
            raise StoreUnavailable('lost acknowledgement')
        def read(ids):
            if unreadable: raise StoreUnavailable('readback unavailable')
            return original_read(ids)
        self.f.port.commit, self.f.port.read = commit, read
        with self.assertRaises(StoreUnavailable): self.reconcile()
        unreadable = False
        self.assertEqual(self.reconcile()['status'], 'already_reconciled'); self.assertEqual(self.f.batches, 0)

    def http(self, method='POST', origin='https://fictional-hub.example', assertion='fictional-signed-assertion'):
        raw = json.dumps(self.payload).encode(); response = {}
        def start(status, headers): response.update(status=status, headers=dict(headers))
        environ = {'PATH_INFO': '/api/assessment-recovery', 'REQUEST_METHOD': method,
            'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
            'QUERY_STRING': '', 'wsgi.input': io.BytesIO(raw)}
        if assertion is not None: environ['HTTP_X_GOOG_IAP_JWT_ASSERTION'] = assertion
        with patch.dict(os.environ, {**self.config, 'HUB_STATE_PROBE_ORIGINS': 'https://fictional-hub.example'}):
            response['body'] = json.loads(b''.join(application(environ, start)))
        return response

    def test_http_same_origin_recovery_returns_zero_grade_writes(self):
        response = self.http()
        self.assertEqual(response['status'], '200 OK')
        self.assertEqual(response['body']['status'], 'reconciled')
        self.assertEqual(response['body']['grade_writes'], 0)
        self.assertTrue(response['body']['readback_verified'])
        self.assertEqual(self.f.batches, 0)

    def test_http_missing_query_wrong_origin_and_missing_auth_preserve_original_pending_run(self):
        state = deepcopy(self.f.port.rows)
        self.assertEqual(self.http(method='GET')['status'], '400 Bad Request')
        self.assertEqual(self.http(method='DELETE')['status'], '405 Method Not Allowed')
        self.assertEqual(self.http(origin='https://untrusted.example')['status'], '403 Forbidden')
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired):
            missing = self.http(assertion=None)
        self.assertEqual(missing['status'], '403 Forbidden')
        self.assertEqual(missing['body']['error'], 'sign_in_required')
        self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.port.rows[self.task_id].value['active_run'], self.run_id)
        self.assertEqual(self.f.batches, 0)
