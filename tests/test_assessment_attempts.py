"""Future Save evidence on fictional inputs and an isolated private CAS store."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_attempts as attempts
from school_workflow_hub.adapters import assessment_review as review, assessment_save as saving
from school_workflow_hub.core.cloud_state import CloudState, Conflict, Document, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from tests.test_cloud_state import MemoryCAS
from tests.test_assessment_records import packet, CAPTURE
from tests import test_assessment_review as fixtures


class AssessmentAttemptTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.packet = packet()
        self.source = review.source_binding(deepcopy(CAPTURE))
        self.request = {'request_id': 'fictional-original-save-request', 'packet': self.packet}
        self.task = CloudState(self.port).create_task(review.task_scope(self.packet))
        self.run = key('run', {'task': self.task, 'request': self.request['request_id']})
        self.ident = key('assessmentattempt', {'task': self.task, 'run': self.run})

    def record(self, request=None, source=None):
        return attempts.record(self.port, self.task,
                               self.request if request is None else request,
                               self.source if source is None else source)

    def test_exact_envelope_is_read_back_without_claim_grade_or_model_calls(self):
        before = deepcopy(self.port.rows[self.task])
        with patch.object(CloudState, 'claim', side_effect=AssertionError('No claim')), \
             patch.object(CloudState, 'finish', side_effect=AssertionError('No finish')), \
             patch.object(saving, '_batch', side_effect=AssertionError('No grades')):
            value = self.record()
        self.assertEqual(value, {'schema': attempts.SCHEMA, 'task_id': self.task, 'run_id': self.run,
            'input_hash': content_hash(self.packet), 'assessment_record_id': records.record_id(self.packet),
            'request': self.request, 'captured_source': self.source, 'created_at': self.port.now.isoformat()})
        self.assertEqual(attempts.read(self.port, self.task, self.run), value)
        self.assertEqual(self.port.rows[self.task], before)
        self.assertEqual(set(self.port.rows), {self.task, self.ident})
        self.assertEqual(set(value), attempts.FIELDS)

    def test_missing_optional_basis_and_explicit_empty_basis_are_preserved_exactly(self):
        self.assertNotIn('review_basis', self.record()['request'])
        request = {**deepcopy(self.request), 'request_id': 'fictional-save-empty-basis', 'review_basis': {}}
        value = self.record(request)
        self.assertEqual(value['request'], request)
        self.assertEqual(value['request']['review_basis'], {})
        for request in ({**deepcopy(self.request), 'review_basis': {}},
                        {**deepcopy(self.request), 'review_basis': {'record_id': 'different'}}):
            with self.subTest(request=request), self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
                self.record(request)
        with self.assertRaisesRegex(ValueError, 'invalid_assessment_attempt'):
            self.record({**deepcopy(self.request), 'review_basis': None})

    def test_all_returned_and_input_json_is_detached_from_durable_state(self):
        value = self.record(); original = deepcopy(value)
        value['request']['packet']['feedback'] = 'Changed returned object'
        self.packet['feedback'] = 'Changed caller object'
        self.source['version'] = '999'
        self.assertEqual(attempts.read(self.port, self.task, self.run), original)

    def test_identical_replay_keeps_original_server_timestamp_and_never_commits(self):
        first = self.record(); before = deepcopy(self.port.rows)
        self.port.advance(600)
        with patch.object(self.port, 'commit', side_effect=AssertionError('Replay is read-only')):
            self.assertEqual(self.record(), first)
        self.assertEqual(self.port.rows, before)

    def test_same_request_cannot_change_packet_basis_or_capture_provenance(self):
        basis = {'record_id': 'fictional-old-record', 'record_sha256': 'b' * 64, 'source': self.source}
        request = {**deepcopy(self.request), 'review_basis': basis}
        self.record(request); before = deepcopy(self.port.rows)
        changed = deepcopy(request); changed['packet']['feedback'] = 'Changed explicit reviewed packet'
        changed_basis = deepcopy(request); changed_basis['review_basis']['record_sha256'] = 'c' * 64
        for candidate in (changed, changed_basis):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
                self.record(candidate)
        with self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
            self.record(request, {**self.source, 'raw_sha256': 'd' * 64})
        self.assertEqual(self.port.rows, before)

    def test_source_identity_hash_and_valid_capture_shape_are_required(self):
        changes = [{'file_id': 'different-file-12345'}, {'sha256': 'f' * 64}, {'version': 999},
                   {'modified_at': 'not-a-timestamp'}, {'unexpected': 'not captured'},
                   {'raw_sha256': 'not-a-hash'}]
        before = deepcopy(self.port.rows)
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'invalid_assessment_attempt'):
                self.record(source={**self.source, **change})
        self.assertEqual(self.port.rows, before)

    def test_source_optional_raw_and_metadata_hashes_survive(self):
        source = {**self.source, 'raw_sha256': 'b' * 64, 'metadata_sha256': 'c' * 64}
        self.assertEqual(self.record(source=source)['captured_source'], source)

    def test_empty_native_revision_retains_existing_packet_compatibility(self):
        self.packet['source']['version'] = ''
        self.source['version'] = ''
        value = self.record()
        self.assertEqual(value['captured_source']['version'], '')
        self.assertEqual(attempts.read(self.port, self.task, self.run), value)

    def test_request_and_fresh_capture_metadata_are_retained_separately(self):
        source = {**self.source, 'version': '42', 'modified_at': '2026-09-19T14:00:00Z'}
        value = self.record(source=source)
        self.assertEqual(value['request'], self.request)
        self.assertEqual(value['captured_source'], source)
        self.assertNotEqual(value['request']['packet']['source']['version'], source['version'])
        self.assertEqual(attempts.read(self.port, self.task, self.run), value)
        with self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
            self.record(source={**source, 'version': '43'})

    def test_invalid_or_oversized_request_never_creates_attempt(self):
        changed = deepcopy(self.request); changed['packet']['feedback'] = 'x' * 250_000
        malformed = deepcopy(self.request); del malformed['packet']['manifest']
        for candidate in (changed, malformed, {**self.request, 'request_id': 'short'},
                          {**self.request, 'unexpected': 'not a Save request'}):
            with self.subTest(candidate_keys=set(candidate)), self.assertRaises(ValueError):
                self.record(candidate)
        self.assertNotIn(self.ident, self.port.rows)
        self.assertNotIn(self.run, self.port.rows)

    def test_lost_create_acknowledgement_uses_exact_readback_with_one_mutation(self):
        self.port.fail_after_commit = True
        with patch.object(self.port, 'commit', wraps=self.port.commit) as commit:
            value = self.record()
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(attempts.read(self.port, self.task, self.run), value)
        self.assertEqual(set(self.port.rows), {self.task, self.ident})

    def test_unknown_commit_without_persistence_remains_held_without_retry(self):
        before = deepcopy(self.port.rows)
        with patch.object(self.port, 'commit', side_effect=StoreUnavailable('unknown')) as commit:
            with self.assertRaisesRegex(StoreUnavailable, 'assessment_attempt_readback_unconfirmed'):
                self.record()
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(self.port.rows, before)

    def test_uncertain_ack_requires_exact_timestamp_readback_not_only_matching_input(self):
        original_commit = self.port.commit
        def uncertain_other_envelope(changes):
            value, expected = deepcopy(changes[self.ident])
            value['created_at'] = '2026-09-15T11:59:59+00:00'
            original_commit({self.ident: (value, expected)})
            raise StoreUnavailable('unknown writer')
        with patch.object(self.port, 'commit', side_effect=uncertain_other_envelope) as commit:
            with self.assertRaisesRegex(StoreUnavailable, 'assessment_attempt_readback_unconfirmed'):
                self.record()
        self.assertEqual(commit.call_count, 1)
        self.assertNotIn(self.run, self.port.rows)

    def test_lost_ack_and_unavailable_readback_stays_unknown_then_restart_is_read_only(self):
        original_read = self.port.read
        calls = []
        def unavailable_after_first(ids):
            calls.append(ids)
            if len(calls) > 1:
                raise StoreUnavailable('readback unavailable')
            return original_read(ids)
        self.port.fail_after_commit = True
        with patch.object(self.port, 'read', side_effect=unavailable_after_first), \
             patch.object(self.port, 'commit', wraps=self.port.commit) as commit:
            with self.assertRaisesRegex(StoreUnavailable, 'readback unavailable'):
                self.record()
            self.assertEqual(commit.call_count, 1)
        with patch.object(self.port, 'commit', side_effect=AssertionError('No replayed write')):
            recovered = self.record()
        self.assertEqual(recovered, attempts.read(self.port, self.task, self.run))

    def test_identical_concurrent_create_retains_first_timestamp_but_different_input_conflicts(self):
        original_commit = self.port.commit
        def concurrent(changes):
            value, expected = deepcopy(changes[self.ident])
            value['created_at'] = '2026-09-15T11:59:59+00:00'
            original_commit({self.ident: (value, expected)})
            raise Conflict('another creator won')
        with patch.object(self.port, 'commit', side_effect=concurrent) as commit:
            value = self.record()
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(value['created_at'], '2026-09-15T11:59:59+00:00')
        request = deepcopy(self.request); request['packet']['feedback'] = 'Conflicting request'
        with self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
            self.record(request)

    def test_read_refuses_changed_envelope_key_request_hash_and_record_binding(self):
        self.record(); original = deepcopy(self.port.rows[self.ident])
        changes = [('schema', 'unknown'), ('task_id', 'task_' + 'a' * 64),
                   ('run_id', 'run_' + 'b' * 64), ('input_hash', 'c' * 64),
                   ('assessment_record_id', 'asmt-different'), ('created_at', 'not-server-time'),
                   ('unexpected', 'historical mutation')]
        for field, value in changes:
            altered = deepcopy(original.value); altered[field] = value
            self.port.rows[self.ident] = Document(altered, original.version)
            with self.subTest(field=field), self.assertRaisesRegex(Conflict, 'assessment_attempt_conflict'):
                attempts.read(self.port, self.task, self.run)
        self.port.rows[self.ident] = original
        self.assertIsNone(attempts.read(self.port, self.task, 'run_' + 'f' * 64))


class SaveAttemptOrderingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.packet = deepcopy(self.f.packet)
        self.packet['feedback'] = 'A complete explicitly reviewed fictional correction.'
        self.payload = {'request_id': 'fictional-ordered-save-request', 'packet': self.packet}
        self.task = key('task', review.task_scope(self.packet))
        self.run = key('run', {'task': self.task, 'request': self.payload['request_id']})

    def save(self):
        return saving.save('fictional-signed-assertion', self.payload, fixtures.CONFIG)

    def test_attempt_is_durable_before_claim_and_existing_native_save(self):
        original_claim = CloudState.claim
        def claim(store, *args, **kwargs):
            retained = attempts.read(self.f.port, self.task, self.run)
            self.assertEqual(retained['request'], self.payload)
            self.assertEqual(retained['captured_source'], review.source_binding(self.f.capture))
            self.assertNotIn(self.run, self.f.port.rows)
            return original_claim(store, *args, **kwargs)
        with patch.object(CloudState, 'claim', autospec=True, side_effect=claim):
            result = self.save()
        self.assertTrue(result['readback_verified']); self.assertEqual(self.f.batches, 1)
        self.assertEqual(attempts.read(self.f.port, self.task, self.run)['request'], self.payload)

    def test_record_failure_stops_before_claim_append_or_grade_write(self):
        before = deepcopy(self.f.book)
        with patch.object(attempts, 'record', side_effect=StoreUnavailable('attempt record unavailable')), \
             patch.object(CloudState, 'claim') as claim, patch.object(saving, '_append') as append:
            with self.assertRaisesRegex(StoreUnavailable, 'attempt record unavailable'):
                self.save()
        claim.assert_not_called(); append.assert_not_called()
        self.assertEqual(self.f.batches, 0); self.assertEqual(self.f.book, before)
        self.assertEqual(set(self.f.port.rows), {self.task})
        self.assertIsNone(self.f.port.rows[self.task].value['active_run'])

    def test_cached_already_saved_readback_has_no_new_state_dependency(self):
        first = self.save(); before = deepcopy(self.f.port.rows)
        with patch.object(attempts, 'record', side_effect=AssertionError('No new attempt')), \
             patch.object(saving, 'configured_store', side_effect=AssertionError('No new state dependency')):
            again = self.save()
        self.assertEqual(again['status'], 'already_saved')
        self.assertEqual(again['record_id'], first['record_id'])
        self.assertEqual(self.f.port.rows, before); self.assertEqual(self.f.batches, 1)

    def test_initial_schema_hold_precedes_attempt_or_task_creation(self):
        self.f.book['Answers']['headers'].remove('question_id')
        with patch.object(attempts, 'record') as record, patch.object(CloudState, 'create_task') as create:
            with self.assertRaises(saving.SchemaHeld):
                self.save()
        record.assert_not_called(); create.assert_not_called()
        self.assertEqual(self.f.port.rows, {}); self.assertEqual(self.f.batches, 0)

    def test_save_with_existing_empty_native_revision_contract_still_succeeds(self):
        self.packet['source']['version'] = ''
        self.f.capture['file']['version'] = ''
        result = self.save()
        self.assertTrue(result['readback_verified']); self.assertEqual(self.f.batches, 1)
        self.assertEqual(attempts.read(self.f.port, self.task, self.run)['captured_source']['version'], '')

    def test_ordinary_save_keeps_existing_same_text_metadata_drift_behavior(self):
        self.f.capture['file'].update(version='42', modified_at='2026-09-19T14:00:00Z')
        result = self.save()
        self.assertTrue(result['readback_verified']); self.assertEqual(self.f.batches, 1)
        value = attempts.read(self.f.port, self.task, self.run)
        self.assertEqual(value['request'], self.payload)
        self.assertEqual(value['captured_source'], review.source_binding(self.f.capture))
        self.assertEqual(value['request']['packet']['source']['version'], '41')
        self.assertEqual(value['captured_source']['version'], '42')

    def test_reviewed_successor_keeps_existing_strict_source_guard_before_attempt(self):
        payload = self.f.successor()
        original_book = deepcopy(self.f.book)
        self.f.capture['file'].update(version='42', modified_at='2026-09-19T14:00:00Z')
        with patch.object(attempts, 'record') as record:
            with self.assertRaisesRegex(Conflict, 'submission_changed'):
                saving.save('fictional-signed-assertion', payload, fixtures.CONFIG)
        record.assert_not_called()
        self.assertEqual(self.f.port.rows, {}); self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.book, original_book)


if __name__ == '__main__':
    unittest.main()
