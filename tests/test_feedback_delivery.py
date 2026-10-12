"""Fictional provider/CAS controls for exact teacher delivery; no live actions."""
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from hub_v2 import app, feedback_delivery as delivery, native_feedback, daily_feedback_packet as daily, google
from school_workflow_hub.core.cloud_state import Document, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_student_archive import FakeDrive
DAY, TEACHER, PACKET = '2026-10-11', 'teacher@school.example', 'dailyfeedbackpacket_' + 'a' * 64

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port = FakeDrive(), MemoryCAS()
        self.config = {'HUB_ALLOWED_EMAILS': TEACHER}
        self.files = []
        for n in range(6):
            ident = 'FictionalFeedbackDelivery%02d' % n
            meta = self.drive.add(ident, 'student feedback.pdf', daily.PDF)
            meta.update(md5Checksum=str(n) * 32, size='100', createdTime=DAY + 'T12:00:00Z')
            self.files.append({'id': ident, 'course': 'forensics', 'course_name': 'Forensics',
                'mime': daily.PDF, 'name': meta['name'], 'feedback_day': DAY,
                'created_at': meta['createdTime'], 'created_day': DAY, 'saved_at': DAY + 'T10:00:00Z',
                'archived': True, 'feedback_id': 'feedbacksource_' + ('1' if n == 5 else str(n)) * 64,
                'pdf_sha256': str(n) * 64, 'url': 'https://drive.google.com/open?id=' + ident})
        patches = [patch.object(delivery, 'configured_store', return_value=self.port),
            patch.object(native_feedback, '_feedback_files', side_effect=lambda *a: {'configured': True, 'files': deepcopy(self.files), 'problem': None}),
            patch.object(google, 'drive_file', side_effect=self.drive.metadata),
            patch.object(daily, 'delivery_packet', side_effect=lambda *a: self.packet()),
            patch.object(google, 'append_rows', side_effect=AssertionError('no grade/checklist write')),
            patch.object(google, 'printable_pdf_bytes', side_effect=AssertionError('no source export')),
            patch.object(native_feedback, '_call', side_effect=AssertionError('no model call'))]
        for p in patches: p.start(); self.addCleanup(p.stop)
    def packet(self):
        files = self.files[:5]
        return {'status': 'ready', 'id': PACKET, 'date': DAY, 'manifest_hash': 'b' * 64, 'source_count': 5, 'individual_reports': 5, 'legacy_packets': 0,
            'report_counts': {'science_writing': 5, 'missing_work_contract': 0, 'critical_aspects': 0, 'other_sources': 0},
            'source_file_ids': [f['id'] for f in files],
            'source_revisions': {f['id']: daily._revision(self.drive.items[f['id']]) for f in files},
            'source_pdf_sha256': {f['id']: f['pdf_sha256'] for f in files},
            'source_identities': {f['id']: {'course': f['course'], 'mime': f['mime'], 'feedback_id': f['feedback_id'],
                'included_feedback_ids': [], 'feedback_date': f['feedback_day'], 'saved_at': f['saved_at'],
                'created_at': f['created_at']} for f in files}}
    def prepare(self, handed=True): return delivery.prepare('read', self.config, DAY, PACKET if handed else None)
    def save(self, snapshot, handed=True):
        return delivery.confirm('read', self.config, DAY, PACKET if handed else None,
            delivery.digest(snapshot), True, handed, actor=TEACHER)
    def current(self): return delivery.current_projection('read', self.config, self.files)
    def test_six_files_five_handed_atomic_replay_preserves_two_physical_shared_report_copies(self):
        snapshot = self.prepare(); initial = deepcopy(self.files)
        one = self.save(snapshot); before = deepcopy(self.port.rows); again = self.save(snapshot)
        self.assertEqual(one['capture_id'], again['capture_id']); self.assertTrue(again['cached'])
        self.assertEqual(self.port.rows, before); self.assertEqual(len(self.port.rows), 2)
        projected = self.current()
        self.assertEqual(len(projected['printed_file_ids']), 6); self.assertEqual(len(projected['handed_file_ids']), 5)
        self.assertEqual(projected['pending_file_ids'], []); self.assertEqual(self.files, initial)
        self.assertEqual(self.files[1]['feedback_id'], self.files[5]['feedback_id'])
    def test_version_checksum_name_parent_trash_or_pdf_digest_drift_holds_before_commit(self):
        snapshot = self.prepare()
        for field, value in [('version', '2'), ('modifiedTime', DAY + 'T15:00:00Z'), ('md5Checksum', 'e' * 32),
                ('name', 'revised feedback.pdf'), ('parents', ['newprivateparent']), ('trashed', True)]:
            old = deepcopy(self.drive.items[self.files[0]['id']]); self.drive.items[self.files[0]['id']][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.save(snapshot)
            self.assertFalse(self.port.rows); self.drive.items[self.files[0]['id']] = old
        self.files[0]['pdf_sha256'] = 'e' * 64
        with self.assertRaises(delivery.Held): self.save(snapshot)
        self.assertFalse(self.port.rows)
    def test_new_report_cannot_expand_prior_review_hash(self):
        snapshot = self.prepare(False); added = deepcopy(self.files[0]); added['id'] = 'FictionalAddedDelivery123'
        self.drive.items[added['id']] = {**self.drive.items[self.files[0]['id']], 'id': added['id']}; self.files.append(added)
        with self.assertRaises(delivery.Held): self.save(snapshot, False)
        self.assertFalse(self.port.rows)
    def test_older_revised_report_is_pending_even_with_later_day_and_history_remains_immutable(self):
        self.files[0]['feedback_day'] = self.files[0]['created_day'] = '2026-09-24'
        self.save(self.prepare(False), False); before = deepcopy(self.port.rows)
        self.drive.items[self.files[0]['id']]['version'] = '2'
        with patch.object(daily, 'enabled', return_value=False), patch.object(daily, 'status', return_value={'status': 'not_saved'}):
            current = native_feedback.today_files('read', self.config, date_et=DAY)
        self.assertEqual(len(current['files']), 5)
        self.assertEqual([f['id'] for f in current['pending_files']], [self.files[0]['id']])
        self.assertEqual(self.port.rows, before)
    def test_final_metadata_bookend_drift_holds_before_capture(self):
        original, seen = self.drive.metadata, {}
        def drift(token, ident, fields=None):
            seen[ident] = seen.get(ident, 0) + 1
            if seen[ident] == 2: self.drive.items[ident]['version'] = '2'
            return original(token, ident, fields)
        with patch.object(google, 'drive_file', side_effect=drift), self.assertRaises(delivery.Held): self.prepare(False)
        self.assertFalse(self.port.rows)
    def test_unknown_content_and_state_failures_remain_pending(self):
        self.drive.items[self.files[0]['id']].pop('md5Checksum')
        snapshot = self.prepare(False); self.assertEqual(len(snapshot['unknown']), 1); self.save(snapshot, False)
        self.assertEqual(self.current()['pending_file_ids'], [self.files[0]['id']])
        with patch.object(self.port, 'read', side_effect=StoreUnavailable('fictional')): result = self.current()
        self.assertEqual(result['status'], 'unavailable'); self.assertEqual(len(result['pending_file_ids']), 6)
        self.assertEqual(result['printed_file_ids'], [])
    def test_uncertain_commit_only_resolved_by_exact_readback_without_retry(self):
        snapshot = self.prepare(); self.port.fail_after_commit = True
        with patch.object(self.port, 'commit', wraps=self.port.commit) as commit: result = self.save(snapshot)
        self.assertEqual(commit.call_count, 1); self.assertEqual(result['status'], 'recorded')
        other = self.prepare(False)
        with patch.object(self.port, 'commit', side_effect=StoreUnavailable('uncommitted')) as commit:
            with self.assertRaises(StoreUnavailable): self.save(other, False)
        self.assertEqual(commit.call_count, 1); self.assertEqual(len(self.port.rows), 2)
    def test_malformed_persisted_attestations_neither_confirm_nor_suppress(self):
        snapshot = self.prepare(); result = self.save(snapshot); record = deepcopy(self.port.rows[result['capture_id']].value)
        for change in ({'schema': 'wrong'}, {'status': 'wrong'}, {'printed_attested': False}, {'handed_attested': False}):
            self.port.rows[result['capture_id']] = Document({**record, **change}, '1')
            with self.subTest(change=change), self.assertRaises(StoreUnavailable): self.save(snapshot)
            projected = self.current(); self.assertEqual(projected['status'], 'unavailable')
            self.assertEqual(len(projected['pending_file_ids']), 6)
    def test_separate_dates_actor_and_typed_attestation(self):
        snapshot = self.prepare()
        for actor, printed, handed in [('intruder@school.example', True, True), (TEACHER, 1, True), (TEACHER, True, 1)]:
            with self.assertRaises(delivery.Held): delivery.confirm('read', self.config, DAY, PACKET, delivery.digest(snapshot), printed, handed, actor=actor)
        other = delivery.prepare('read', self.config, '2026-10-12', PACKET)
        self.assertEqual(other['handed_packet']['date'], DAY); self.assertEqual(other['through'], '2026-10-12'); self.assertFalse(self.port.rows)
    def test_packet_projection_keeps_exact_saved_source_manifest(self):
        self.save(self.prepare()); packet = self.packet(); before = deepcopy(packet)
        self.assertEqual(delivery.packet_projection(self.config, packet)['pending_file_ids'], [])
        self.assertEqual(packet, before); packet['source_revisions'][self.files[0]['id']]['version'] = '2'
        self.assertEqual(delivery.packet_projection(self.config, packet)['pending_file_ids'], [self.files[0]['id']])
    def test_self_consistent_malformed_capture_members_fail_open_to_all_pending(self):
        clean = self.prepare(False)
        for slot in ('printed', 'handed'):
            for malformed in (None, 'text', 17, []):
                snapshot = deepcopy(clean); snapshot[slot] = [malformed]
                ref = delivery.key('feedbackdelivery', delivery.digest(snapshot))
                record = {'schema': delivery.SCHEMA, 'status': 'teacher_attested', 'teacher': TEACHER, 'recorded_at': DAY + 'T16:00:00Z',
                    'printed_attested': True, 'handed_attested': slot == 'handed', 'snapshot': snapshot, 'snapshot_sha256': delivery.digest(snapshot)}
                self.port.rows = {ref: Document(record, '1'), delivery.INDEX: Document({'schema': delivery.SCHEMA, 'captures': [ref]}, '1')}
                with self.subTest(slot=slot, malformed=malformed):
                    result = self.current(); self.assertEqual(result['status'], 'unavailable'); self.assertEqual(len(result['pending_file_ids']), 6)
    def test_invalid_persisted_actor_or_time_neither_confirms_nor_suppresses(self):
        snapshot = self.prepare(); result = self.save(snapshot); record = deepcopy(self.port.rows[result['capture_id']].value)
        for change in ({'teacher': 'outsider@school.example'}, {'recorded_at': ''}, {'recorded_at': 'not-a-date'},
                {'recorded_at': '2026-10-11T12:00:00'}, {'recorded_at': '2099-10-11T12:00:00Z'}):
            self.port.rows[result['capture_id']] = Document({**record, **change}, '1')
            with self.subTest(change=change), self.assertRaises(StoreUnavailable): self.save(snapshot)
            self.assertEqual(self.current()['status'], 'unavailable'); self.assertEqual(len(self.current()['pending_file_ids']), 6)
    def test_older_unverified_or_duplicate_packets_cannot_bypass_existing_preview_boundary(self):
        self.files[0].update(archived=False, student_only=False, feedback_day='2026-09-24', created_day='2026-09-24')
        snapshot = self.prepare(False); self.assertEqual(len(snapshot['unknown']), 1)
        with patch.object(daily, 'enabled', return_value=True), patch.object(daily, 'status', return_value={'status': 'not_saved'}):
            current = native_feedback.today_files('read', self.config, date_et=DAY)
            self.assertNotIn(self.files[0]['id'], [item['id'] for item in current['pending_files']])
            self.assertEqual(current['pending_source_problems'][0]['date'], '2026-09-24')
            with self.assertRaises(native_feedback.Held): native_feedback.print_today('read', self.config, [self.files[0]['id']], date_et=DAY)
        self.files[0].update(student_only=True, canonical_source_id=self.files[1]['id'])
        with patch.object(daily, 'enabled', return_value=True), patch.object(daily, 'status', return_value={'status': 'not_saved'}):
            current = native_feedback.today_files('read', self.config, date_et=DAY)
            self.assertNotIn(self.files[0]['id'], [item['id'] for item in current['pending_files']])
            with self.assertRaises(native_feedback.Held): native_feedback.print_today('read', self.config, [self.files[0]['id']], date_et=DAY)
    def test_cutoff_uses_eastern_day_and_unknown_unzoned_timestamp_is_not_printed(self):
        ident = self.files[0]['id']; self.drive.items[ident]['modifiedTime'] = '2026-10-12T02:00:00Z'
        self.assertEqual(self.prepare(False)['unknown'], [])
        self.drive.items[ident]['modifiedTime'] = '2026-10-12T04:00:00Z'
        self.assertEqual(len(self.prepare(False)['unknown']), 1)
        self.drive.items[ident]['modifiedTime'] = '2026-10-11T12:00:00'
        self.assertEqual(len(self.prepare(False)['unknown']), 1)
    def test_invalid_handed_intent_is_denied_before_any_provider_read(self):
        with patch.object(google, 'drive_file', side_effect=AssertionError('no provider')):
            for invalid in (False, 0, '', [], {}):
                with self.subTest(intent=invalid), self.assertRaises(delivery.Held): delivery.prepare('read', self.config, DAY, invalid)
    def test_old_report_date_does_not_mark_a_new_post_cutoff_content_version_printed(self):
        ident = self.files[0]['id']; self.drive.items[ident]['modifiedTime'] = '2026-10-12T08:00:00Z'
        snapshot = self.prepare(False); self.assertEqual([x['file_id'] for x in snapshot['unknown']], [ident])
        self.files[0].update(archived=True, drive_md5=self.drive.items[ident]['md5Checksum'])
        proven = self.prepare(False); self.assertEqual(proven['unknown'], [])
        self.files[0]['drive_md5'] = 'f' * 32
        self.assertEqual(len(self.prepare(False)['unknown']), 1)
    def test_handled_packet_must_be_five_science_reports_and_bad_handed_subset_is_unavailable(self):
        packet = self.packet(); packet['report_counts']['other_sources'] = 1
        with patch.object(daily, 'delivery_packet', return_value=packet), self.assertRaises(delivery.Held): self.prepare()
        snapshot = self.prepare(); snapshot['handed'][0] = deepcopy(snapshot['handed'][0]); snapshot['handed'][0]['revision']['version'] = '9'
        ref = delivery.key('feedbackdelivery', delivery.digest(snapshot))
        value = {'schema': delivery.SCHEMA, 'status': 'teacher_attested', 'teacher': TEACHER, 'recorded_at': DAY + 'T16:00:00Z',
            'printed_attested': True, 'handed_attested': True, 'snapshot': snapshot, 'snapshot_sha256': delivery.digest(snapshot)}
        self.port.rows[ref] = Document(value, '1'); self.port.rows[delivery.INDEX] = Document({'schema': delivery.SCHEMA, 'captures': [ref]}, '1')
        self.assertEqual(self.current()['status'], 'unavailable'); self.assertEqual(len(self.current()['pending_file_ids']), 6)
    def test_315_source_snapshot_fits_real_store_bound_and_one_atomic_two_document_commit(self):
        original = deepcopy(self.files[0]); self.files = []
        for n in range(315):
            item = {**original, 'id': 'FictionalBoundedFeedback%03d' % n}; self.files.append(item)
            self.drive.items[item['id']] = {**deepcopy(self.drive.items[original['id']]), 'id': item['id'],
                'permissions': deepcopy(self.drive.items[original['id']]['permissions']) * 2,
                'capabilities': {'canEdit': True, 'canReadRevisions': True, 'longFixture': 'x' * 1000}}
        snapshot = self.prepare(False)
        self.assertLess(len(json.dumps(snapshot).encode()), 250000)
        self.assertEqual(self.save(snapshot, False)['printed_count'], 315); self.assertEqual(len(self.port.rows), 2)

class DeliveryAuthTests(unittest.TestCase):
    def invoke(self, *, method='POST', path='/v2/api/feedback/delivery/capture', origin='https://hub.fictional.example', assertion='signed'):
        raw = json.dumps({'action': 'prepare', 'through': DAY, 'handed_packet_id': None}).encode(); captured = []
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
            'wsgi.input': io.BytesIO(raw), 'HTTP_ORIGIN': origin, 'HTTP_X_GOOG_IAP_JWT_ASSERTION': assertion}
        body = b''.join(app.application(env, lambda status, headers: captured.append(status), {'HUB_STATE_PROBE_ORIGINS': 'https://hub.fictional.example'}))
        return captured[0], body
    def test_teacher_origin_and_page_guard_precede_token_or_mutation(self):
        def verify(assertion, config):
            if assertion != 'signed': raise google.SignInRequired()
            return TEACHER
        with patch.object(google, 'verify_teacher', side_effect=verify), patch.object(google, 'service_token', side_effect=AssertionError('no token')):
            self.assertTrue(self.invoke(assertion=None)[0].startswith('401')); self.assertTrue(self.invoke(origin='https://other.example')[0].startswith('403'))
            self.assertTrue(self.invoke(path='/v2/feedback/delivery', method='GET', assertion=None)[0].startswith('401'))
            status, body = self.invoke(path='/v2/feedback/delivery', method='GET'); self.assertEqual(status, '200 OK'); self.assertIn(b'Record confirmed delivery', body)
    def test_prepare_requests_reader_only_and_no_state_write(self):
        with patch.object(google, 'verify_teacher', return_value=TEACHER), patch.object(google, 'service_token', return_value='read') as token, patch.object(delivery, 'prepare', return_value={'mock': 1}), patch.object(delivery, 'review_reply', return_value={'status': 'review'}):
            self.assertEqual(self.invoke()[0], '200 OK'); self.assertEqual(token.call_args.kwargs, {})
