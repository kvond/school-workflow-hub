"""First explicit assessment on fictional current work; no provider or live data."""
from copy import deepcopy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_initial_review as initial
from school_workflow_hub.adapters import assessment_attempts as attempts
from school_workflow_hub.adapters import assessment_provider as provider
from school_workflow_hub.adapters import assessment_review as review, assessment_save as saving, grading_records
from school_workflow_hub.core.cloud_state import CloudState, Conflict, LeaseLost, key
from school_workflow_hub.core.runs import content_hash
from school_workflow_hub.workflows import assessment_records as records
from school_workflow_hub.web import application
from tests import test_assessment_review as fixtures, test_assessment_records as packet_fixtures


MODE = 'first_current_review'
CONFIG = fixtures.CONFIG
LEARNER = 'roster:' + 'a' * 16


def first_case():
    notes = ['N%02d' % n for n in range(1, 15)]
    bank = ['Term %02d' % n for n in range(1, 9)]
    drafts = ['D%02d' % n for n in range(1, 4)]
    text = '\n'.join(['[[MARKER-INVENTORY:FIRST25]]', 'VERSION=fictional-first-v1',
        'NOTES=14 DRAFT=3 OPTIONAL=0 BANK_TERMS=8', 'NOTES_IDS=', *notes,
        'DRAFT_IDS=', *drafts, 'OPTIONAL_IDS=', 'BANK_IDS=', *bank, '',
        *['[[%s:FIRST25:%s]] Current prompt %s:\n%s' % (category, qid, qid,
            '' if qid == 'N14' else 'Fictional own response ' + qid + '.')
          for category, ids in (('NOTES', notes), ('BANK', bank), ('DRAFT', drafts)) for qid in ids]])
    digest = hashlib.sha256(text.encode()).hexdigest()
    capture = {'schema_version': saving.CAPTURE_VERSION,
        'file': {'id': 'f' * 24, 'name': 'Fictional first assessment', 'mime_type': 'p',
                 'version': '34', 'modified_at': '2026-09-18T14:00:00Z'},
        'text': text, 'sha256': digest, 'raw_sha256': digest,
        'metadata_sha256': content_hash({'fictional_metadata': '34'}), 'captured_at': fixtures.NOW}
    packet = packet_fixtures.packet(stage='final')
    packet['source'].update(file_id=capture['file']['id'], sha256=digest,
        version=capture['file']['version'], modified_at=capture['file']['modified_at'])
    packet['manifest'] = records.derive_manifest(text)
    packet['stage_reference'] = 'Explicit teacher review of the current final checkpoint.'
    packet['feedback'] = 'Explicitly reviewed fictional current evidence and feedback.'
    packet['responses'] = []
    for category, ids in (('NOTES', notes), ('BANK', bank), ('DRAFT', drafts)):
        for qid in ids:
            blank = qid == 'N14'
            response = packet_fixtures.response(qid, category,
                'blank' if blank else 'meaningful_attempt',
                '' if blank else 'Fictional own response ' + qid + '.',
                conceptual='Evidence unavailable' if blank else 'Developing', explanation='')
            response.update(critical_aspect='', rubric_version='critical-aspect-4/v1')
            packet['responses'].append(response)
    records.validate_packet(packet, text)
    return packet, capture


class InitialReviewTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.packet, self.f.capture = first_case()
        self.f.packet = deepcopy(self.packet)
        built = self.rows()
        self.intake = {name: '' for name in built['submission']}
        self.intake.update({name: deepcopy(built['submission'][name]) for name in (
            'assignment_key', 'section_id', 'section_name', 'student_id', 'student_id_kind',
            'learner_key', 'display_name', 'identity_basis', 'source_url', 'source_file_id')})
        self.intake.update(record_id='intake-fictional-first', status='roster_matched',
                           checked_at='2026-09-17T12:00:00Z', ic_posting_status='not_posted')
        self.f.book['Submissions']['headers'] = list(dict.fromkeys([
            *self.f.book['Submissions']['headers'], *built['submission']]))
        self.f.book['Answers']['headers'] = list(dict.fromkeys([
            *self.f.book['Answers']['headers'], *(k for row in built['answers'] for k in row)]))
        self.f.book['Submissions']['rows'] = [(1, deepcopy(self.intake))]
        self.f.book['Answers']['rows'] = []
        self.original = deepcopy(self.f.book)
        connected = patch.object(initial, 'configured_store', return_value=self.f.port)
        connected.start(); self.addCleanup(connected.stop)
        for name in ('_stored_draft', 'draft', 'stored_draft_for'):
            guard = patch.object(provider, name, side_effect=AssertionError('No provider or cache lookup'))
            guard.start(); self.addCleanup(guard.stop)

    def rows(self, packet=None):
        value = packet or self.packet
        return records.build_rows(value, records.completion(value), self.f.capture, now=fixtures.NOW)

    def prepare(self, packet=None):
        return initial.prepare('fictional-signed-assertion', {'packet': deepcopy(packet or self.packet)}, CONFIG)

    def payload(self, packet=None):
        prepared = self.prepare(packet)
        return {'request_id': 'fictional-first-current-review', 'packet': prepared['packet'],
                'review_basis': prepared['review_basis']}

    def save(self, payload):
        return saving.save('fictional-signed-assertion', payload, CONFIG)

    def readback(self, payload):
        return initial.readback('fictional-signed-assertion',
            {name: deepcopy(payload[name]) for name in ('packet', 'review_basis')}, CONFIG)

    def http(self, path, payload, *, method='POST', origin='https://hub.example.invalid', assertion='fictional-iap'):
        raw = json.dumps(payload).encode(); result = {}
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '',
            'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
            'wsgi.input': io.BytesIO(raw), 'HTTP_X_GOOG_IAP_JWT_ASSERTION': assertion}
        with patch.dict('os.environ', CONFIG | {'HUB_STATE_PROBE_ORIGINS': 'https://hub.example.invalid'}):
            response = application(environ, lambda status, headers: result.update(status=status, headers=headers))
        result['body'] = json.loads(b''.join(response)); return result

    def add_assessment(self, **changes):
        packet = deepcopy(self.packet)
        for key_name, value in changes.items():
            if key_name == 'source_file_id':
                packet['source']['file_id'] = value
                packet['source']['url'] = 'https://docs.google.com/presentation/d/' + value + '/edit'
            else:
                packet[key_name] = value
        packet['feedback'] += ' Another already reviewed assessment.'
        built = self.rows(packet)
        self.f.book['Submissions']['rows'].append((len(self.f.book['Submissions']['rows']) + 1, built['submission']))
        self.f.book['Answers']['rows'] += [(len(self.f.book['Answers']['rows']) + n + 1, row)
                                          for n, row in enumerate(built['answers'])]

    def intake_identity(self, record_id, file_id, *, native=True, learner=False):
        row = deepcopy(self.intake)
        row.update(record_id=record_id, source_file_id=file_id,
            source_url='https://docs.google.com/presentation/d/' + file_id + '/edit',
            student_id=self.packet['identity']['student_id'] if native else '',
            learner_key=LEARNER if learner else '')
        return row

    def assert_prepare_held(self, packet=None):
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)):
            self.prepare(packet)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 0)

    def assert_save_preflight_held(self, payload):
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)):
            self.save(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        self.assertEqual(self.f.batches, 0)

    def test_prepare_returns_exact_reviewed_packet_scores_and_absence_proof_without_writes(self):
        prepared = self.prepare(); expected = records.completion(self.packet)
        self.assertEqual(initial.MODE, MODE)
        self.assertEqual(prepared['packet'], self.packet)
        self.assertEqual(prepared['scores'], expected['scores'])
        self.assertEqual(prepared['counts'], expected['counts'])
        self.assertEqual(prepared['record_id'], records.record_id(self.packet))
        self.assertEqual(prepared['scores']['content_notes']['exact_value'], '105/11')
        self.assertEqual(prepared['scores']['revision']['exact_value'], '10')
        basis = prepared['review_basis']
        self.assertEqual(set(basis), {'mode', 'source', 'packet_sha256', 'target_sha256', 'absence_sha256'})
        self.assertEqual(basis['mode'], MODE)
        self.assertEqual(basis['source'], review.source_binding(self.f.capture))
        self.assertEqual(basis['packet_sha256'], content_hash(self.packet))
        for field in ('target_sha256', 'absence_sha256'):
            self.assertRegex(basis[field], r'^[a-f0-9]{64}$')
        self.assertEqual(prepared['external_writes'], 0); self.assertEqual(prepared['live_model_calls'], 0)
        self.assertEqual(self.f.book, self.original); self.assertEqual(self.f.port.rows, {})
        prepared['packet']['responses'][0]['judgment'] = 'printed_copy'
        self.assertEqual(self.packet['responses'][0]['judgment'], 'meaningful_attempt')

    def test_first_save_preserves_identity_only_intake_and_existing_final_arithmetic(self):
        payload = self.payload(); result = self.save(payload)
        self.assertTrue(result['readback_verified']); self.assertEqual(result['superseded'], [])
        self.assertEqual(self.f.batches, 1)
        self.assertEqual(self.f.book['Submissions']['rows'][0][1], self.intake)
        self.assertEqual(len(self.f.book['Submissions']['rows']), 2)
        saved = self.f.book['Submissions']['rows'][-1][1]
        self.assertEqual(saved['assessment_stage'], 'final')
        self.assertEqual(saved['content_score_exact'], '105/11')
        self.assertEqual(saved['revision_score_exact'], '10')
        self.assertEqual(saved['ic_posting_status'], 'unposted')
        self.assertEqual(len(self.f.book['Answers']['rows']), 25)
        self.assertTrue(all(row['counts_toward_current_score'] == 'TRUE' for _, row in self.f.book['Answers']['rows']))

    def test_teacher_approved_redirected_answer_is_preserved_without_reassessment(self):
        quote = 'Fictional redirected N01 explanation written in the labelled N02 response field.'
        note = 'Teacher approved the N01 answer in labelled N02; retain its meaningful-attempt judgment.'
        self.f.capture['text'] = self.f.capture['text'].replace(
            'Current prompt N01:\nFictional own response N01.',
            'Current prompt N01:\nSee the N02 response field.').replace(
            'Current prompt N02:\nFictional own response N02.',
            'Current prompt N02:\nFictional own response N02.\n' + quote)
        digest = hashlib.sha256(self.f.capture['text'].encode()).hexdigest()
        self.f.capture.update(sha256=digest, raw_sha256=digest)
        self.packet['source']['sha256'] = digest
        approved = next(r for r in self.packet['responses'] if r['question_id'] == 'N01')
        approved.update(response_excerpt=quote, evidence_note=note)
        self.assertEqual(approved['judgment'], 'meaningful_attempt')
        n01_field = self.f.capture['text'].split('[[NOTES:FIRST25:N01]]', 1)[1].split(
            '[[NOTES:FIRST25:N02]]', 1)[0]
        n02_field = self.f.capture['text'].split('[[NOTES:FIRST25:N02]]', 1)[1].split(
            '[[NOTES:FIRST25:N03]]', 1)[0]
        self.assertNotIn(quote, n01_field); self.assertIn(quote, n02_field)
        payload = self.payload()
        self.assertEqual(payload['packet']['responses'], self.packet['responses'])
        result = self.save(payload)
        self.assertTrue(result['readback_verified'])
        self.assertEqual(result['content_score_exact'], '105/11')
        self.assertEqual(result['revision_score_exact'], '10')
        saved = next(a for _, a in self.f.book['Answers']['rows']
                     if a['stage'] == 'NOTES' and a['question_id'] == 'N01')
        self.assertEqual((saved['response'], saved['evidence_note'], saved['meaningful_complete']),
                         (quote, note, 'TRUE'))
        task = key('task', review.task_scope(self.packet))
        run = key('run', {'task': task, 'request': payload['request_id']})
        retained = attempts.read(self.f.port, task, run)['request']['packet']
        self.assertEqual(retained['responses'], self.packet['responses'])
        self.assertEqual(self.f.book['Submissions']['rows'][0][1], self.intake)
        self.assertEqual(self.f.batches, 1)

    def test_explicit_initial_checkpoint_keeps_bank_and_revision_deferred_without_inference(self):
        packet = deepcopy(self.packet); packet['stage'] = 'initial'
        packet['stage_reference'] = 'Explicit reviewed initial NOTES checkpoint.'
        prepared = self.prepare(packet)
        self.assertEqual(prepared['scores']['content_notes']['exact_value'], '65/7')
        self.assertIsNone(prepared['scores']['revision']['value'])
        result = self.save(self.payload(packet)); self.assertTrue(result['readback_verified'])
        saved = self.f.book['Submissions']['rows'][-1][1]
        self.assertEqual(saved['assessment_stage'], 'initial')
        self.assertEqual(self.f.book['Submissions']['rows'][0][1], self.intake)
        for _, answer in self.f.book['Answers']['rows']:
            self.assertEqual(answer['counts_toward_current_score'], 'TRUE' if answer['stage'] == 'NOTES' else 'FALSE')

    def test_prepare_authenticates_before_capture_state_or_provider(self):
        with patch.object(grading_records, 'read_records', side_effect=saving.ConnectionError('sign_in_required')), \
             patch.object(saving, 'read_records', side_effect=saving.ConnectionError('sign_in_required')), \
             patch.object(saving, 'capture') as capture, patch.object(saving, 'configured_store') as store:
            with self.assertRaises(saving.ConnectionError): self.prepare()
        capture.assert_not_called(); store.assert_not_called()

    def test_prepare_accepts_absence_without_even_an_intake_row(self):
        self.f.book['Submissions']['rows'] = []
        prepared = self.prepare()
        self.assertEqual(prepared['packet'], self.packet)
        self.assertTrue(self.save(self.payload())['readback_verified'])
        self.assertEqual(len(self.f.book['Submissions']['rows']), 1)

    def test_source_four_keys_and_optional_capture_hashes_cannot_change_after_prepare(self):
        original = deepcopy(self.f.capture)
        for field, value in (('version', '35'), ('modified_at', '2026-09-19T14:00:00Z'),
                             ('id', 'differentfictionalfile12345'), ('sha256', 'b' * 64),
                             ('raw_sha256', 'c' * 64), ('metadata_sha256', 'd' * 64)):
            self.f.capture = deepcopy(original); payload = self.payload()
            if field in self.f.capture['file']: self.f.capture['file'][field] = value
            else: self.f.capture[field] = value
            with self.subTest(field=field): self.assert_save_preflight_held(payload)
        self.f.capture = original

    def test_packet_digest_scope_stage_and_feedback_cannot_change_after_review(self):
        for mutate in (lambda p: p.update(stage='initial'), lambda p: p.update(feedback='Changed after review'),
                       lambda p: p.update(section_name='Another fictional section'),
                       lambda p: p['identity'].update(student_id='12345')):
            payload = self.payload(); mutate(payload['packet'])
            with self.subTest(packet=payload['packet']['stage']): self.assert_save_preflight_held(payload)

    def test_manifest_roles_cannot_be_changed_even_with_recomputed_packet_digest(self):
        payload = self.payload(); packet = payload['packet']
        packet['manifest']['notes_ids'].remove('N14'); packet['manifest']['optional_ids'].append('N14')
        next(r for r in packet['responses'] if r['question_id'] == 'N14')['optional'] = True
        payload['review_basis']['packet_sha256'] = content_hash(packet)
        self.assert_save_preflight_held(payload)

    def test_all_required_responses_and_literal_current_evidence_are_required(self):
        for mutate in (lambda p: p['responses'].pop(),
                       lambda p: p['responses'][0].update(response_excerpt='Invented current evidence'),
                       lambda p: p['responses'][0].update(judgment='review')):
            packet = deepcopy(self.packet); mutate(packet)
            with self.subTest(responses=len(packet['responses'])): self.assert_prepare_held(packet)

    def test_existing_assessment_in_any_stage_or_source_is_never_silently_superseded(self):
        original = deepcopy(self.f.book)
        for changes in ({}, {'stage': 'initial'}, {'source_file_id': 'differentfictionalfile12345'}):
            self.f.book = deepcopy(original); self.add_assessment(**changes)
            with self.subTest(changes=changes): self.assert_prepare_held()
        self.f.book = original

    def test_unknown_checkpoint_held_intake_scores_and_intake_answers_require_decision(self):
        original = deepcopy(self.f.book)
        for changes in ({'hold_reason': 'Retained source hold'}, {'content_score': '0'},
                        {'revision_score': '10'}, {'assessment_stage': 'initial'},
                        {'status': 'identity_unverified'}):
            self.f.book = deepcopy(original); self.f.book['Submissions']['rows'][0][1].update(changes)
            with self.subTest(changes=changes): self.assert_prepare_held()
        self.f.book = deepcopy(original)
        answer = deepcopy(self.rows()['answers'][0]); answer['record_id'] = self.intake['record_id']
        self.f.book['Answers']['rows'] = [(1, answer)]
        self.assert_prepare_held(); self.f.book = original

    def test_same_file_conflicting_identity_and_orphan_deterministic_answers_hold(self):
        original = deepcopy(self.f.book)
        other = deepcopy(self.intake); other.update(record_id='intake-conflicting-first', student_id='12345')
        self.f.book['Submissions']['rows'].append((2, other)); self.assert_prepare_held()
        self.f.book = deepcopy(original)
        orphan = deepcopy(self.rows()['answers'][0])
        self.f.book['Answers']['rows'] = [(1, orphan)]; self.assert_prepare_held()
        self.f.book = original

    def test_imported_packet_cannot_bridge_disconnected_existing_identity_rows(self):
        packet = deepcopy(self.packet); packet['identity']['learner_key'] = LEARNER
        native_file = 'fictionalNativeIdentitySource12345'
        learner_file = 'fictionalLearnerIdentitySource12345'
        native = self.intake_identity('intake-native-only', native_file)
        learner = self.intake_identity('intake-learner-only', learner_file, native=False, learner=True)
        self.assertNotIn(packet['source']['file_id'], (native_file, learner_file))
        self.f.book['Submissions']['rows'] = [(1, native), (2, learner)]
        # Different existing files prevent read_target's same-file check from
        # masking whether the imported packet is improperly used as a bridge.
        self.assert_prepare_held(packet)
        bridge = self.intake_identity('intake-existing-bridge', 'fictionalExistingBridgeSource12345', learner=True)
        self.f.book['Submissions']['rows'] = [(1, native)]
        payload = self.payload()  # one canonical task identity at review time
        self.f.book['Submissions']['rows'] += [(2, learner), (3, bridge)]
        self.assert_prepare_held(packet)  # even a saved bridge requires reconciliation
        self.assert_save_preflight_held(payload)

    def test_same_file_disconnected_rows_and_learner_only_native_promotion_hold(self):
        packet = deepcopy(self.packet); packet['identity']['learner_key'] = LEARNER
        native = self.intake_identity('intake-native-only', packet['source']['file_id'])
        learner = self.intake_identity('intake-learner-only', packet['source']['file_id'], native=False, learner=True)
        self.f.book['Submissions']['rows'] = [(1, native), (2, learner)]
        self.assert_prepare_held(packet)
        # A single learner-key record on another source also cannot authorize
        # promoting the packet's newly supplied native student ID.
        learner = self.intake_identity('intake-learner-only', 'fictionalOtherLearnerSource12345', native=False, learner=True)
        self.f.book['Submissions']['rows'] = [(1, learner)]
        self.assert_prepare_held(packet)
        bridge = self.intake_identity('intake-existing-bridge', 'fictionalExistingBridgeSource12345', learner=True)
        self.f.book['Submissions']['rows'] = [(1, native)]
        payload = self.payload()
        self.f.book['Submissions']['rows'] += [(2, learner), (3, bridge)]
        self.assert_save_preflight_held(payload)

    def test_verified_native_learner_bridge_cannot_bypass_expired_learner_task(self):
        native = self.intake_identity('intake-native-only', 'fictionalNativeIdentitySource12345')
        learner = self.intake_identity('intake-learner-only', 'fictionalLearnerIdentitySource12345', native=False, learner=True)
        bridge = self.intake_identity('intake-existing-bridge', 'fictionalExistingBridgeSource12345', learner=True)
        self.f.book['Submissions']['rows'] = [(1, native)]
        self.assertEqual(self.packet['identity']['learner_key'], '')  # canonical native target
        payload = self.payload()
        self.f.book['Submissions']['rows'] += [(2, learner), (3, bridge)]
        learner_packet = deepcopy(self.packet)
        learner_packet['identity'].update(student_id='', learner_key=LEARNER)
        cloud = CloudState(self.f.port)
        learner_task = cloud.create_task(review.task_scope(learner_packet))
        self.assertNotEqual(learner_task, key('task', review.task_scope(self.packet)))
        claim = cloud.claim(learner_task, owner='original-learner-task-owner', request_id='original-learner-task-request',
                            input_hash='retained-original-learner-input', ttl_seconds=30)
        self.f.port.advance(31); preserved = deepcopy(self.f.port.rows)
        book = deepcopy(self.f.book)
        for action in (self.prepare, lambda: self.save(payload)):
            with self.assertRaisesRegex(Conflict, 'assessment_initial_identity_reconciliation_required'):
                action()
        self.assertEqual(self.f.port.rows, preserved)
        self.assertEqual(self.f.book, book); self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.port.rows[learner_task].value['active_run'], claim.run_id)
        self.assertIsNone(self.f.port.rows[claim.run_id].value['receipt'])

    def test_dual_key_input_without_any_prior_rows_requires_identity_reconciliation(self):
        self.f.book['Submissions']['rows'] = []
        packet = deepcopy(self.packet); packet['identity']['learner_key'] = LEARNER
        book, state = deepcopy(self.f.book), deepcopy(self.f.port.rows)
        with self.assertRaisesRegex(Conflict, 'assessment_initial_identity_reconciliation_required'):
            self.prepare(packet)
        self.assertEqual(self.f.book, book); self.assertEqual(self.f.port.rows, state)
        self.assertEqual(self.f.batches, 0)

    def test_first_packet_url_requires_exact_document_path_not_file_id_in_query(self):
        packet = deepcopy(self.packet)
        packet['source']['url'] = ('https://docs.google.com/presentation/d/fictionalWrongDocument12345/edit?expected='
                                   + packet['source']['file_id'])
        # The existing broad packet validator accepts this historical shape;
        # FIRST mode adds an exact path identity requirement without changing it.
        self.assertIs(records.validate_packet(packet, self.f.capture['text']), packet)
        self.assert_prepare_held(packet)
        response = self.http('/api/assessment-review', {'packet': packet})
        self.assertEqual(response['status'], '400 Bad Request', response)
        self.assertEqual(response['body']['error'], 'invalid_assessment_initial_review')
        self.assertEqual(self.f.port.rows, {}); self.assertEqual(self.f.batches, 0)

    def test_nonempty_intake_counts_ic_entry_or_assessment_dates_are_not_identity_only(self):
        original = deepcopy(self.f.book)
        fields = [('notes_completed', '0'), ('notes_required', '14'),
                  ('bank_completed', '0'), ('bank_required', '8'),
                  ('drafts_completed', '0'), ('drafts_required', '3'),
                  ('ic_posting_status', 'entered_in_ic'), ('checked_date', '2026-09-18'),
                  ('source_snapshot_date', '2026-09-18')]
        for field, value in fields:
            self.f.book = deepcopy(original)
            self.f.book['Submissions']['rows'][0][1][field] = value
            with self.subTest(field=field): self.assert_prepare_held()
        self.f.book = original
        self.assertTrue(self.intake['checked_at'])
        self.assertEqual(self.intake['ic_posting_status'], 'not_posted')
        self.assertEqual(self.prepare()['packet'], self.packet)

    def test_changed_intake_or_new_predecessor_after_prepare_stops_before_claim(self):
        original = deepcopy(self.f.book)
        payload = self.payload(); self.f.book['Submissions']['rows'][0][1]['checked_at'] = '2026-09-18T13:00:00Z'
        self.assert_save_preflight_held(payload)
        self.f.book = deepcopy(original); payload = self.payload(); self.add_assessment()
        self.assert_save_preflight_held(payload); self.f.book = original

    def test_section_projection_change_before_save_or_final_dispatch_holds(self):
        payload = self.payload(); original_read = self.f.read_records
        def changed(*args):
            value = original_read(*args); value['sections'][0]['section_name'] = 'Renamed fictional section'
            return value
        with patch.object(saving, 'read_records', side_effect=changed), \
             patch.object(grading_records, 'read_records', side_effect=changed):
            self.assert_save_preflight_held(payload)
        changed_at_dispatch = False
        def current(*args):
            value = original_read(*args)
            if changed_at_dispatch: value['sections'][0]['section_name'] = 'Renamed fictional section'
            return value
        def token(*args):
            nonlocal changed_at_dispatch
            if saving.WRITE_SCOPE in args: changed_at_dispatch = True
            return 'fictional-token'
        with patch.object(saving, 'read_records', side_effect=current), \
             patch.object(grading_records, 'read_records', side_effect=current), \
             patch.object(saving, 'service_token', side_effect=token), self.assertRaises((ValueError, Conflict)):
            self.save(payload)
        self.assertTrue(changed_at_dispatch); self.assertEqual(self.f.batches, 0)
        self.assertEqual(self.f.book, self.original)

    def test_pending_active_or_expired_save_is_preserved_without_adoption(self):
        for expired in (False, True):
            self.f.port.rows.clear()
            task = CloudState(self.f.port).create_task(review.task_scope(self.packet))
            CloudState(self.f.port).claim(task, owner='foreign-original-owner', request_id='foreign-original-request',
                                        input_hash='foreign-original-input', ttl_seconds=300)
            if expired: self.f.port.advance(301)
            with self.subTest(expired=expired): self.assert_prepare_held()

    def test_claim_race_that_expires_is_never_adopted_or_cleared(self):
        payload = self.payload(); original_create = CloudState.create_task; preserved = {}
        def race(store, scope):
            task = original_create(store, scope)
            CloudState(self.f.port).claim(task, owner='foreign-racing-owner', request_id='foreign-racing-request',
                                        input_hash='foreign-racing-input', ttl_seconds=30)
            self.f.port.advance(31); preserved.update(deepcopy(self.f.port.rows))
            return task
        with patch.object(CloudState, 'create_task', autospec=True, side_effect=race), self.assertRaises(Conflict):
            self.save(payload)
        self.assertTrue(preserved); self.assertEqual(self.f.batches, 0)
        for ident, document in preserved.items(): self.assertEqual(self.f.port.rows[ident], document)
        task = key('task', review.task_scope(self.packet))
        self.assertNotIn(key('run', {'task': task, 'request': payload['request_id']}), self.f.port.rows)

    def test_final_workbook_and_later_source_bookends_stop_native_batch(self):
        payload = self.payload(); captures = 0
        def changed_intake(*args):
            nonlocal captures
            captures += 1
            if captures == 2: self.f.book['Submissions']['rows'][0][1]['checked_at'] = '2026-09-18T13:00:00Z'
            return deepcopy(self.f.capture)
        with patch.object(saving, 'capture', side_effect=changed_intake), self.assertRaises(Conflict): self.save(payload)
        self.assertEqual(self.f.batches, 0)
        self.f.book = deepcopy(self.original); self.f.port.rows.clear(); payload = self.payload(); reads = 0
        def changed_source(*args):
            nonlocal reads
            reads += 1
            if reads == 2: self.f.capture['metadata_sha256'] = 'e' * 64
            return deepcopy(self.f.book)
        with patch.object(saving, '_workbook', side_effect=changed_source), self.assertRaises(Conflict): self.save(payload)
        self.assertGreaterEqual(reads, 2); self.assertEqual(self.f.batches, 0)

    def test_expired_final_lease_stops_before_native_batch(self):
        payload = self.payload(); captures = 0
        def slow(*args):
            nonlocal captures
            captures += 1
            if captures == 2: self.f.port.advance(400)
            return deepcopy(self.f.capture)
        with patch.object(saving, 'capture', side_effect=slow), self.assertRaises((LeaseLost, Conflict)): self.save(payload)
        self.assertEqual(self.f.batches, 0)

    def test_schema_hold_precedes_any_assessment_claim_or_native_write(self):
        self.f.book['Answers']['headers'].remove('rubric_version')
        with patch.object(CloudState, 'claim') as claim, patch.object(saving, '_batch') as batch:
            with self.assertRaises(saving.SchemaHeld):
                payload = self.payload(); self.save(payload)
        claim.assert_not_called(); batch.assert_not_called(); self.assertEqual(self.f.port.rows, {})

    def test_exact_repeat_is_readback_only_and_durable_attempt_precedes_claim(self):
        payload = self.payload(); task = key('task', review.task_scope(self.packet))
        run_id = key('run', {'task': task, 'request': payload['request_id']}); original_claim = CloudState.claim
        def checked(store, *args, **kwargs):
            self.assertEqual(attempts.read(self.f.port, task, run_id)['request'], payload)
            self.assertNotIn(run_id, self.f.port.rows)
            return original_claim(store, *args, **kwargs)
        with patch.object(CloudState, 'claim', autospec=True, side_effect=checked): first = self.save(payload)
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with patch.object(saving, '_batch', side_effect=AssertionError('No repeated native batch')), \
             patch.object(CloudState, 'claim', side_effect=AssertionError('No repeated claim')):
            again = self.save(payload)
        self.assertEqual(again['status'], 'already_saved'); self.assertEqual(again['record_id'], first['record_id'])
        self.assertEqual(self.f.batches, 1); self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)

    def test_lost_native_reply_reads_saved_rows_without_rebatch_or_pending_fence_closure(self):
        payload = self.payload()
        def wrote_but_lost(*args):
            self.f.batch(*args); raise saving.ConnectionError('assessment_write_uncertain')
        with patch.object(saving, '_batch', side_effect=wrote_but_lost), self.assertRaises(saving.ConnectionError): self.save(payload)
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        self.f.port.advance(301)
        with patch.object(saving, 'save', side_effect=AssertionError('Readback must not call Save')), \
             patch.object(saving, '_batch', side_effect=AssertionError('No replay after uncertain write')), \
             patch.object(self.f.port, 'commit', side_effect=AssertionError('Readback must not mutate state')):
            result = self.readback(payload)
        self.assertTrue(result['readback_verified']); self.assertEqual(result['status'], 'already_saved')
        self.assertEqual(self.f.batches, 1); self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)
        task = key('task', review.task_scope(self.packet)); self.assertIsNotNone(self.f.port.rows[task].value['active_run'])

    def test_unknown_native_outcome_without_saved_rows_never_retries_after_expiry(self):
        payload = self.payload()
        with patch.object(saving, '_batch', side_effect=saving.ConnectionError('assessment_write_uncertain')), \
             self.assertRaises(saving.ConnectionError): self.save(payload)
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book); self.f.port.advance(301)
        with patch.object(saving, 'save', side_effect=AssertionError('Readback must not call Save')), \
             patch.object(saving, '_batch', side_effect=AssertionError('Never repeat unknown write')), \
             patch.object(self.f.port, 'commit', side_effect=AssertionError('No recovery state write')):
            with self.assertRaises(Conflict): self.readback(payload)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book); self.assertEqual(self.f.batches, 0)

    def test_already_saved_cannot_acknowledge_an_edited_full_packet_or_corrupted_rows(self):
        payload = self.payload(); self.save(payload); original = deepcopy(self.f.book)
        changed = deepcopy(payload); changed['packet']['comparison_status'] = 'Changed after original review'
        changed['review_basis']['packet_sha256'] = content_hash(changed['packet'])
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)): self.save(changed)
        self.assertEqual(self.f.book, original); self.assertEqual(self.f.batches, 1)
        self.f.book['Answers']['rows'][0][1]['conceptual_feedback'] = 'Corrupted saved evidence'
        with self.assertRaises((ValueError, Conflict, saving.ConnectionError)): self.save(payload)
        self.assertEqual(self.f.batches, 1)

    def test_real_http_prepare_save_and_explicit_readback_use_guarded_existing_routes(self):
        prepared = self.http('/api/assessment-review', {'packet': self.packet})
        self.assertEqual(prepared['status'], '200 OK', prepared)
        self.assertEqual(prepared['body']['packet'], self.packet)
        payload = {'request_id': 'fictional-http-first-review', 'packet': prepared['body']['packet'],
                   'review_basis': prepared['body']['review_basis']}
        saved = self.http('/api/assessment-save', payload)
        self.assertEqual(saved['status'], '200 OK', saved)
        self.assertTrue(saved['body']['readback_verified']); self.assertEqual(self.f.batches, 1)
        self.assertEqual(self.f.book['Submissions']['rows'][0][1], self.intake)
        state, book = deepcopy(self.f.port.rows), deepcopy(self.f.book)
        with patch.object(saving, 'save', side_effect=AssertionError('Explicit readback is not Save')), \
             patch.object(self.f.port, 'commit', side_effect=AssertionError('Readback must be read-only')):
            observed = self.http('/api/assessment-review', {'mode': 'first_readback',
                'packet': payload['packet'], 'review_basis': payload['review_basis']})
        self.assertEqual(observed['status'], '200 OK', observed)
        self.assertTrue(observed['body']['readback_verified']); self.assertEqual(observed['body']['live_model_calls'], 0)
        self.assertEqual(self.f.port.rows, state); self.assertEqual(self.f.book, book)

    def test_http_origin_method_and_sign_in_holds_have_no_mutations(self):
        with patch.object(initial, 'prepare') as prepare:
            self.assertEqual(self.http('/api/assessment-review', {'packet': self.packet}, method='GET')['status'], '405 Method Not Allowed')
            self.assertEqual(self.http('/api/assessment-review', {'packet': self.packet}, origin='https://other.example.invalid')['status'], '403 Forbidden')
            prepare.assert_not_called()
        with patch.object(saving, 'read_records', side_effect=saving.ConnectionError('sign_in_required')), \
             patch.object(grading_records, 'read_records', side_effect=saving.ConnectionError('sign_in_required')), \
             patch.object(saving, 'capture') as capture:
            denied = self.http('/api/assessment-review', {'packet': self.packet}, assertion=None)
        self.assertEqual(denied['status'], '403 Forbidden'); capture.assert_not_called()
        self.assertEqual(self.f.port.rows, {}); self.assertEqual(self.f.book, self.original)


if __name__ == '__main__':
    unittest.main()
