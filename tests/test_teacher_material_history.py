"""Synthetic historical originals; actual source guard, no school connection."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import unittest
from unittest.mock import Mock, patch

from hub_v2 import agenda, google, teacher_material as tm, teacher_material_history as history, work
from hub_v2.source_operations import SourceOperations, Held
from school_workflow_hub.core.cloud_state import Conflict, Document, LeaseLost, StoreUnavailable
from school_workflow_hub.core.runs import content_hash
from test_cloud_state import MemoryCAS
from test_exact_teacher_material import CONFIG, TEACHER, FORM, FILE, OTHER, RID, AT
from test_hub_v2 import fixture, response


class GuardedMemory(MemoryCAS):
    def __init__(self):
        super().__init__()
        self.guarded_writes = []
        self.lose_response_at = None
        self.before_auth = lambda: None

    def commit_guarded(self, changes, *, before_write):
        self.before_auth()
        before_write()
        self.guarded_writes.append(deepcopy(changes))
        self.commit(changes)
        if len(self.guarded_writes) == self.lose_response_at:
            raise StoreUnavailable('synthetic uncertain commit')


class HistoricalTeacherMaterialTests(unittest.TestCase):
    def setUp(self):
        self.a = fixture()
        self.port = GuardedMemory()
        self.port.now = datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc)
        self.content = b'complete fictional previously reviewed teacher PowerPoint bytes'
        self.md = {'id': FILE, 'name': 'Fictional teaching example.pptx', 'mimeType': google.PPTX,
                   'trashed': False, 'version': '5', 'modifiedTime': AT,
                   'parents': ['FICTIONAL_UPLOAD_FOLDER'], 'size': str(len(self.content)),
                   'md5Checksum': hashlib.md5(self.content).hexdigest()}
        self.event = response(TEACHER, AT, (FILE, 'Teacher example'), (OTHER, 'Student original'))
        self.event['responseId'] = RID
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tm, 'configured_store', return_value=self.port))
        self.stack.enter_context(patch.object(history, 'configured_store', return_value=self.port))
        self.stack.enter_context(patch.object(google, 'get_json', side_effect=lambda *a, **k: deepcopy(self.event)))
        self.stack.enter_context(patch.object(google, 'drive_file', side_effect=lambda *a, **k: deepcopy(self.md)))
        self.stack.enter_context(patch.object(google, 'deck_bytes', side_effect=lambda *a, **k: self.content))
        self.stack.enter_context(patch.object(agenda, 'load', side_effect=lambda *a, **k: self.a))
        proof = tm.capture('read', CONFIG, self.a, 'forensics', 'Wk 04', FORM, RID, FILE)
        self.original = {'schema': tm.SCHEMA, 'record_key': tm.record_key(proof), 'proof': proof,
                         'classification': 'verified_teacher_material', 'operator': TEACHER,
                         'instruction_sha256': 'a'*64, 'evidence': 'Previously reviewed complete fictional teacher original.',
                         'verified_at': '2026-10-21T10:00:00Z'}
        tm.import_verified('read', CONFIG, self.a, self.original, port=self.port)
        self.original_docs = deepcopy(self.port.rows)
        self.md.update(trashed=True, version='6')
        self.activity = Mock(return_value=None)

    def proposal(self):
        value = history.prepare('read', CONFIG, self.a, self.original['record_key'], operator=TEACHER,
            instruction_sha256='b'*64, evidence='Retain the previously proven exact teacher original without restoring it.')
        value['verified_at'] = self.port.now.isoformat()
        return value

    def guard(self, value, *, owner='history-worker', request='historical-request'):
        return SourceOperations.acquire(self.port, source_scope='fictional-all-preparation-sources',
            owner=owner, request_id=request, input_hash=content_hash(value), check_activity=self.activity)

    def publish(self, value=None):
        value = value or self.proposal()
        guard = self.guard(value)
        result = history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(result, value['record_key'])
        return value, guard

    def entries(self, *, current=True):
        rows = work.submissions([deepcopy(self.event)], FORM)
        return [{'current': current, 'week': 'Wk 04', 'form': {'form_id': FORM, 'found': rows}, 'found': rows}]

    def classify(self, entries):
        return tm.classify_entries('read', CONFIG, self.a, 'forensics', entries)

    def test_prepare_reads_complete_bytes_and_is_read_only(self):
        before = deepcopy(self.port.rows)
        value = self.proposal()
        self.assertEqual(self.port.rows, before)
        self.assertEqual(value['proof']['content_sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(value['prior_record_sha256'], content_hash(self.original))
        self.assertEqual(value['proof']['metadata'], self.md)
        self.assertTrue(self.md['trashed'])

    def test_publication_preserves_immutable_original_and_index_and_reads_back(self):
        value, _ = self.publish()
        self.assertEqual(len(self.port.guarded_writes), 2)
        for k, doc in self.original_docs.items():
            self.assertEqual(self.port.rows[k], doc)
        self.assertEqual(self.port.rows[value['record_key']].value, value)
        self.assertEqual(self.port.rows[history.intent_key(value['record_key'])].value['state'], 'verified')
        self.assertEqual(self.md['version'], '6'); self.assertIs(self.md['trashed'], True)

    def test_existing_verified_publication_is_read_only(self):
        value, guard = self.publish(); before = deepcopy(self.port.rows)
        history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(before, self.port.rows)
        self.assertEqual(len(self.port.guarded_writes), 2)

    def test_registered_trashed_file_without_disposition_stays_held(self):
        entries = self.entries(); before = deepcopy(entries)
        self.assertIn('verification is unavailable', self.classify(entries))
        self.assertEqual(entries, before)

    def test_exact_mixed_response_preserves_student_attachment_and_original_event(self):
        value, _ = self.publish(); original = deepcopy(self.event)
        entries = self.entries()
        self.assertIsNone(self.classify(entries))
        self.assertEqual([f['id'] for f in entries[0]['found'][0]['files']], [OTHER])
        info = entries[0]['form']['teacher_uploads'][0]
        self.assertEqual(info['verification_record'], value['record_key'])
        self.assertEqual([f['id'] for f in info['files']], [FILE])
        self.assertEqual(self.event, original)
        self.assertEqual(len(self.port.guarded_writes), 2)

    def test_nearby_scope_only_filters_that_exact_teacher_upload(self):
        self.publish(); entries = self.entries(current=False)
        self.assertIsNone(self.classify(entries))
        self.assertEqual([f['id'] for f in entries[0]['found'][0]['files']], [OTHER])
        self.assertNotIn('teacher_uploads', entries[0]['form'])

    def test_other_responses_and_unregistered_files_are_never_inferred_teacher_material(self):
        self.publish(); self.event['responseId'] = 'FICTIONAL_OTHER_RESPONSE'
        entries = self.entries(); before = deepcopy(entries)
        self.assertIsNone(self.classify(entries)); self.assertEqual(entries, before)

    def test_same_size_changed_bytes_or_missing_bytes_never_prepare(self):
        for content in (b'x'*len(self.content), b'', self.content[:-1]):
            with self.subTest(content=content), patch.object(google, 'deck_bytes', return_value=content):
                with self.assertRaises(tm.Unverified): self.proposal()
        self.assertEqual(self.port.guarded_writes, [])

    def test_other_metadata_changes_and_nonadvancing_versions_hold(self):
        changes = [('name', 'renamed.pptx'), ('parents', ['FICTIONAL_OTHER_FOLDER']), ('mimeType', tm.SLIDES),
                   ('size', '1'), ('md5Checksum', 'f'*32), ('modifiedTime', '2026-10-21T10:00:00Z'),
                   ('version', '5'), ('version', '4'), ('version', '06'), ('trashed', False)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                old = self.md[field]; self.md[field] = value
                with self.assertRaises(tm.Unverified): self.proposal()
                self.md[field] = old

    def test_changed_event_or_agenda_blocks_preparation(self):
        prior = deepcopy(self.event)
        self.event['answers']['new'] = {'textAnswers': {'answers': [{'value': 'Changed teacher event'}]}}
        with self.assertRaises(tm.Unverified): self.proposal()
        self.event = prior
        self.a.classes['forensics']['cells'][(5, 3)] = {'formattedValue': 'Changed lesson'}
        with self.assertRaises(tm.Unverified): self.proposal()

    def test_changed_non_teacher_respondent_cannot_bypass_registered_history(self):
        self.publish(); self.event['respondentEmail'] = 'student@school.example'
        entries = self.entries(); before = deepcopy(entries)
        self.assertIn('verification is unavailable', self.classify(entries))
        self.assertEqual(entries, before)

    def test_unregistered_student_responses_keep_their_original_scope(self):
        self.event['respondentEmail'] = 'student@school.example'
        self.event['responseId'] = 'FICTIONAL_STUDENT_RESPONSE'
        entries = self.entries(); before = deepcopy(entries)
        self.assertIsNone(self.classify(entries)); self.assertEqual(entries, before)

    def test_metadata_changes_during_full_download_hold(self):
        def download(*args):
            self.md['version'] = '7'
            return self.content
        with patch.object(google, 'deck_bytes', side_effect=download):
            with self.assertRaises(tm.Unverified): self.proposal()

    def test_original_receipt_or_index_changes_during_download_hold(self):
        for which in ('receipt', 'index'):
            with self.subTest(which=which):
                old = deepcopy(self.port.rows)
                target = self.original['record_key'] if which == 'receipt' else next(k for k in old if k.startswith('teachermaterialindex_'))
                def download(*args):
                    doc = self.port.rows[target]
                    self.port.rows[target] = Document(doc.value, 'changed-version')
                    return self.content
                with patch.object(google, 'deck_bytes', side_effect=download):
                    with self.assertRaises(tm.Unverified): self.proposal()
                self.port.rows = old

    def test_unavailable_content_keeps_registered_scope_held(self):
        self.publish(); entries = self.entries(); before = deepcopy(entries)
        with patch.object(google, 'deck_bytes', side_effect=google.GoogleError('unavailable')):
            self.assertIn('verification is unavailable', self.classify(entries))
        self.assertEqual(entries, before)

    def test_changed_content_after_publication_cannot_keep_classification(self):
        self.publish(); self.content = b'x'*len(self.content)
        entries = self.entries(); before = deepcopy(entries)
        self.assertIn('verification is unavailable', self.classify(entries)); self.assertEqual(entries, before)

    def test_new_revision_cannot_inherit_historical_disposition(self):
        self.publish(); self.md['version'] = '7'
        entries = self.entries(); before = deepcopy(entries)
        self.assertIn('verification is unavailable', self.classify(entries)); self.assertEqual(entries, before)

    def test_restored_or_replaced_provenance_stays_held_after_disposition(self):
        self.publish()
        original = deepcopy(self.md)
        for change in ({'version': '7', 'trashed': False}, {'parents': ['FICTIONAL_MOVED_FOLDER']},
                       {'name': 'Different title.pptx'}, {'mimeType': tm.SLIDES}):
            with self.subTest(change=change):
                self.md = {**original, **change}; entries = self.entries(); before = deepcopy(entries)
                self.assertIn('verification is unavailable', self.classify(entries)); self.assertEqual(entries, before)

    def test_source_change_during_shared_activity_prevents_intent_write(self):
        value = self.proposal(); guard = self.guard(value)
        self.activity.side_effect = lambda: self.md.update(version='7')
        with self.assertRaises(tm.Unverified): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_guard_must_be_actual_bound_shared_guard(self):
        value = self.proposal()
        with self.assertRaises(tm.Unverified): history.import_verified('read', CONFIG, self.a, value, guard=Mock())
        wrong = deepcopy(value); wrong['evidence'] += ' Different proposal.'
        guard = self.guard(wrong)
        with self.assertRaises(tm.Unverified): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_shared_guard_cutoff_prevents_writes(self):
        self.port.now = datetime(2026, 10, 22, 10, 56, tzinfo=timezone.utc)
        value = self.proposal(); guard = self.guard(value)
        with self.assertRaises((Held, LeaseLost)): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def maintenance_guard(self, value):
        authority = dict(schema='school-source-maintenance/v1', authorization_ref='fictional-decision',
            authorization_revision='revision-1', status='authorized', source_scope='fictional-all-preparation-sources',
            owner='history-worker', request_id='maintenance-request', input_hash=content_hash(value),
            operation_scope='source_repair', starts_at='2026-10-22T18:00:00Z', expires_at='2026-10-23T02:00:00Z')
        guard = SourceOperations.acquire(self.port, source_scope=authority['source_scope'], owner=authority['owner'],
            request_id=authority['request_id'], input_hash=authority['input_hash'], check_activity=self.activity,
            operation_scope='source_repair', maintenance_authorization=deepcopy(authority),
            read_maintenance_authorization=lambda ref: deepcopy(authority))
        return guard, authority

    def test_explicit_maintenance_window_retains_history_and_final_readback(self):
        self.port.now = datetime(2026, 10, 22, 19, tzinfo=timezone.utc)
        value = self.proposal(); guard, _ = self.maintenance_guard(value)
        self.assertEqual(history.import_verified('read', CONFIG, self.a, value, guard=guard), value['record_key'])
        self.assertEqual(len(self.port.guarded_writes), 2)
        for k, doc in self.original_docs.items(): self.assertEqual(self.port.rows[k], doc)
        self.assertEqual(self.port.rows[value['record_key']].value, value)
        self.assertTrue(self.md['trashed'])

    def test_final_maintenance_authorization_reread_blocks_change_or_expiry_after_bookend(self):
        for change in ('revision', 'expiry'):
            with self.subTest(change=change):
                self.setUp(); self.port.now = datetime(2026, 10, 22, 19, tzinfo=timezone.utc)
                value = self.proposal(); guard, authority = self.maintenance_guard(value)
                original = history.bookend
                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if self.activity.call_count >= 2:
                        if change == 'revision': authority['authorization_revision'] = 'revision-2'
                        else: self.port.now = datetime(2026, 10, 23, 2, tzinfo=timezone.utc)
                    return result
                with patch.object(history, 'bookend', side_effect=changed):
                    with self.assertRaises((Held, LeaseLost)):
                        history.import_verified('read', CONFIG, self.a, value, guard=guard)
                self.assertEqual(self.port.guarded_writes, [])

    def test_helper_frozen_inputs_prevent_writes(self):
        value = self.proposal(); guard = self.guard(value)
        self.activity.side_effect = Held('helper_inputs_frozen')
        with self.assertRaises(Held): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_final_bookend_can_neither_outlive_lease_nor_keep_insufficient_budget(self):
        for delay in (211, 331):
            with self.subTest(delay=delay):
                value = self.proposal(); guard = self.guard(value)
                original = history.bookend
                def check(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if self.activity.call_count >= 2:
                        self.port.advance(delay)
                    return result
                with patch.object(history, 'bookend', side_effect=check):
                    with self.assertRaises((tm.Unverified, LeaseLost)):
                        history.import_verified('read', CONFIG, self.a, value, guard=guard)
                self.assertEqual(self.port.guarded_writes, [])
                self.setUp()

    def test_final_owner_read_refuses_takeover(self):
        value = self.proposal(); guard = self.guard(value); original = history.bookend
        def check(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.activity.call_count >= 2:
                self.port.advance(331)
                SourceOperations.acquire(self.port, source_scope='fictional-all-preparation-sources',
                    owner='other-worker', request_id='takeover-request', input_hash='different-input',
                    check_activity=lambda: None)
            return result
        with patch.object(history, 'bookend', side_effect=check):
            with self.assertRaises(LeaseLost): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_slow_final_response_exhausts_reserved_check_interval(self):
        value = self.proposal(); guard = self.guard(value)
        with patch.object(history, 'monotonic', side_effect=[0, 0, 181]):
            with self.assertRaises(tm.Unverified): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_authentication_time_is_before_final_shared_reservation(self):
        value = self.proposal(); guard = self.guard(value)
        self.port.before_auth = lambda: self.port.advance(1900)
        with self.assertRaises((Held, LeaseLost)): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(self.port.guarded_writes, [])

    def test_lost_intent_response_stops_and_never_retries_publication(self):
        value = self.proposal(); guard = self.guard(value); self.port.lose_response_at = 1
        with self.assertRaises(StoreUnavailable): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertTrue(guard.stopped)
        self.assertNotIn(value['record_key'], self.port.rows)
        with self.assertRaises(StoreUnavailable): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        self.assertEqual(len(self.port.guarded_writes), 1)

    def test_lost_receipt_response_can_only_read_back_exact_success(self):
        value = self.proposal(); guard = self.guard(value); self.port.lose_response_at = 2
        with self.assertRaises(StoreUnavailable): history.import_verified('read', CONFIG, self.a, value, guard=guard)
        before = deepcopy(self.port.rows)
        self.assertEqual(history.import_verified('read', CONFIG, self.a, value, guard=guard), value['record_key'])
        self.assertEqual(self.port.rows, before); self.assertEqual(len(self.port.guarded_writes), 2)

    def test_changed_attribution_cannot_overwrite_existing_disposition(self):
        value, guard = self.publish()
        altered = deepcopy(value); altered['evidence'] += ' Changed explanation.'
        guard.input_hash = content_hash(altered)
        with self.assertRaises(Conflict): history.import_verified('read', CONFIG, self.a, altered, guard=guard)
        self.assertEqual(self.port.rows[value['record_key']].value, value)

    def test_no_partial_batch_classification_when_later_verification_fails(self):
        self.publish(); entries = self.entries(); before = deepcopy(entries)
        original = history.bookend; calls = []
        def check(*args, **kwargs):
            calls.append(True)
            if len(calls) == 3:
                raise tm.Unverified('later batch bookend unavailable')
            return original(*args, **kwargs)
        with patch.object(history, 'bookend', side_effect=check):
            self.assertIn('verification is unavailable', self.classify(entries))
        self.assertEqual(entries, before)


if __name__ == '__main__':
    unittest.main()
