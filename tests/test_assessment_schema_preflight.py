"""Native schema validation before claims and immediately before dispatch."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from school_workflow_hub.adapters import assessment_save as saving
from school_workflow_hub.core.cloud_state import CloudState
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from tests import test_assessment_review as fixtures


class SchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.legacy = deepcopy(self.f.packet)
        self.legacy['feedback'] = 'A complete explicit reviewed packet for this synthetic test.'
        self.modern = deepcopy(self.legacy)
        for answer in self.modern['responses']:
            answer.update(critical_aspect='', rubric_version='critical-aspect-4/v1', explanation_level='')
            if answer['judgment'] in {'blank', 'printed_copy'}:
                answer['conceptual_level'] = 'Evidence unavailable'
        records.validate_packet(self.modern, self.f.capture['text'])
        self.f.book['Submissions']['headers'].append('existing_teacher_note')
        self.assertEqual(len(self.f.book['Answers']['headers']), 18)
        self.assertEqual(len(self.f.book['Submissions']['headers']), 40)

    def rows(self, packet):
        return records.build_rows(packet, records.completion(packet), self.f.capture, now=fixtures.NOW)

    def payload(self, packet):
        return {'request_id': 'synthetic-schema-preflight-request', 'packet': packet}

    def result(self, missing=None, book=None):
        book = book or self.f.book
        return {'outcome': 'blocked' if missing else 'ready',
            'reason_code': 'assessment_schema_columns_missing' if missing else 'ready',
            'missing_columns': missing or {}, 'headers_sha256': content_hash(
                {title: book[title]['headers'] for title in ('Submissions', 'Answers')})}

    def assert_no_grade_claim_or_write(self, packet, expected):
        book = deepcopy(self.f.book)
        with patch.object(CloudState, 'create_task') as create, \
             patch.object(CloudState, 'claim') as claim, \
             patch.object(saving, '_append') as append, \
             patch.object(saving, '_batch') as batch, \
             patch.object(saving, 'service_token', return_value='fictional-read-token') as token:
            with self.assertRaises(saving.SchemaHeld) as caught:
                saving.save('fictional-assertion', self.payload(packet), fixtures.CONFIG)
            create.assert_not_called(); claim.assert_not_called()
            append.assert_not_called(); batch.assert_not_called()
            self.assertFalse(any(saving.WRITE_SCOPE in call.args for call in token.call_args_list))
        self.assertEqual(str(caught.exception), 'assessment_column_missing')
        self.assertEqual(caught.exception.code, 'assessment_column_missing')
        self.assertEqual({k: caught.exception.intake[k] for k in expected}, expected)
        self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 0)
        self.assertFalse(any(ident.startswith(('task_', 'run_', 'completed_')) for ident in self.f.port.rows))

    def test_pure_modern_packet_requires_both_real_rubric_columns_in_legacy_18_headers(self):
        rows = self.rows(self.modern); before = deepcopy((self.f.book, rows))
        result = saving.schema_compatibility(self.f.book, rows)
        self.assertEqual(result, self.result({'Answers': ['critical_aspect', 'rubric_version']}))
        self.assertEqual((self.f.book, rows), before)
        self.assertEqual(self.f.port.rows, {})

    def test_modern_18_column_save_holds_before_any_claim_append_or_write_token(self):
        self.assert_no_grade_claim_or_write(self.modern,
            self.result({'Answers': ['critical_aspect', 'rubric_version']}))

    def test_modern_20_column_save_preserves_existing_arithmetic_and_readback(self):
        self.f.book['Answers']['headers'] += ['critical_aspect', 'rubric_version']
        self.assertEqual(saving.schema_compatibility(self.f.book, self.rows(self.modern)),
            self.result())
        expected = records.completion(self.modern)
        self.assertEqual(expected['scores'], records.completion(self.legacy)['scores'])
        result = saving.save('fictional-assertion', self.payload(self.modern), fixtures.CONFIG)
        self.assertEqual(result['status'], 'saved')
        self.assertTrue(result['readback_verified'])
        self.assertEqual(result['content_score_exact'], expected['scores']['content_notes']['exact_value'])
        self.assertEqual(result['revision_score_exact'], expected['scores']['revision']['exact_value'])
        self.assertEqual(self.f.batches, 1)
        self.assertTrue(all(row['rubric_version'] == 'critical-aspect-4/v1'
            for _, row in self.f.book['Answers']['rows'][-len(self.modern['responses']):]))

    def test_legacy_packet_without_modern_fields_still_saves_to_18_columns(self):
        self.assertEqual(saving.schema_compatibility(self.f.book, self.rows(self.legacy)),
            self.result())
        result = saving.save('fictional-assertion', self.payload(self.legacy), fixtures.CONFIG)
        self.assertTrue(result['readback_verified'])
        self.assertEqual(self.f.batches, 1)
        self.assertFalse(any('rubric_version' in row or 'critical_aspect' in row
                             for _, row in self.f.book['Answers']['rows']))

    def test_already_saved_readback_does_not_gain_a_new_private_state_dependency(self):
        self.f.book['Answers']['headers'] += ['critical_aspect', 'rubric_version']
        payload = self.payload(self.modern)
        first = saving.save('fictional-assertion', payload, fixtures.CONFIG)
        before = deepcopy(self.f.port.rows)
        with patch.object(saving, 'configured_store', side_effect=AssertionError('Readback-only path needs no new state')):
            replay = saving.save('fictional-assertion', payload, fixtures.CONFIG)
        self.assertEqual(replay['status'], 'already_saved')
        self.assertEqual(replay['record_id'], first['record_id'])
        self.assertEqual(self.f.batches, 1)
        self.assertEqual(self.f.port.rows, before)

    def test_missing_submission_field_is_detected_dynamically_before_any_claim(self):
        self.f.book['Submissions']['headers'].remove('source_sha256')
        self.assert_no_grade_claim_or_write(self.legacy, self.result({'Submissions': ['source_sha256']}))

    def test_required_columns_union_all_answers_not_only_first_answer_or_nonempty_values(self):
        rows = {'submission': {'record_id': 'fictional-record'}, 'answers': [
            {'record_id': 'fictional-record', 'question_id': 'Q1'},
            {'record_id': 'fictional-record', 'question_id': 'Q2', 'critical_aspect': '',
             'rubric_version': 'critical-aspect-4/v1'}]}
        book = {'Submissions': {'headers': ['record_id']}, 'Answers': {'headers': ['record_id', 'question_id']}}
        self.assertEqual(saving.schema_compatibility(book, rows)['missing_columns'],
                         {'Answers': ['critical_aspect', 'rubric_version']})
        book['Answers']['headers'].append('critical_aspect')
        self.assertEqual(saving.schema_compatibility(book, rows)['missing_columns'],
                         {'Answers': ['rubric_version']})

    def test_missing_columns_from_both_tables_are_stable_and_unused_extra_headers_are_allowed(self):
        book = deepcopy(self.f.book)
        book['Submissions']['headers'].remove('source_sha256')
        book['Answers']['headers'].append('unused_existing_teacher_column')
        result = saving.schema_compatibility(book, self.rows(self.modern))
        self.assertEqual(result['missing_columns'], {'Submissions': ['source_sha256'],
            'Answers': ['critical_aspect', 'rubric_version']})
        self.assertEqual(saving.schema_compatibility(self.f.book, self.rows(self.legacy))['outcome'], 'ready')

    def test_header_proof_hash_binds_exact_header_order_and_contains_no_student_row_values(self):
        rows = self.rows(self.legacy)
        original = saving.schema_compatibility(self.f.book, rows)
        changed = deepcopy(self.f.book)
        changed['Submissions']['rows'][0][1]['feedback'] = 'Different fictional private row value'
        self.assertEqual(saving.schema_compatibility(changed, rows)['headers_sha256'], original['headers_sha256'])
        changed['Answers']['headers'].reverse()
        reordered = saving.schema_compatibility(changed, rows)
        self.assertEqual(reordered['outcome'], 'ready')
        self.assertNotEqual(reordered['headers_sha256'], original['headers_sha256'])

    def test_repeated_schema_hold_creates_no_assessment_claim_or_uncertain_grade_history(self):
        expected = self.result({'Answers': ['critical_aspect', 'rubric_version']})
        for _ in range(2):
            self.assert_no_grade_claim_or_write(deepcopy(self.modern), expected)
        self.assertEqual(self.f.book['Submissions']['rows'][0][1]['status'], 'assessed')
        self.assertEqual(self.f.book['Submissions']['rows'][0][1]['superseded_by'], '')

    def test_schema_changed_after_preflight_is_held_before_the_native_grade_batch(self):
        self.f.book['Answers']['headers'] += ['critical_aspect', 'rubric_version']
        self.f.capture['file'].update(version='42', modified_at='2026-09-19T14:00:00Z')
        self.modern['source'].update(version='42', modified_at='2026-09-19T14:00:00Z')
        original_submissions = deepcopy(self.f.book['Submissions']['rows'])
        original_answers = deepcopy(self.f.book['Answers']['rows'])
        def changed_header_token(*args):
            if saving.WRITE_SCOPE in args:
                self.f.book['Answers']['headers'].remove('rubric_version')
            return 'fictional-token'
        with patch.object(saving, 'service_token', side_effect=changed_header_token), \
             self.assertRaises(saving.SchemaHeld):
                saving.save('fictional-assertion', self.payload(self.modern), fixtures.CONFIG)
        self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.book['Submissions']['rows'], original_submissions)
        self.assertEqual(self.f.book['Answers']['rows'], original_answers)
