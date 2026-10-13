"""School-email outbox acceptance with fictional Drive, real PDFs/MIME and CAS.

No provider, school source, model, grade or teacher-delivery write is available.
"""
import base64
import hashlib
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from threading import Barrier
from unittest.mock import patch

from pypdf import PdfReader

from hub_v2 import app, daily_feedback_packet as daily, feedback_delivery, google
from hub_v2 import native_feedback, print_ready_email as email
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash
from tests.test_daily_feedback_packet import FakeDrive, MemoryCAS, pdf_bytes
from tests.test_student_archive import config as archive_config

TEACHER = 'teacher@school.example'
FOLDER = 'FictionalEmailPacketFolder001'
ORIGIN = 'https://hub.fictional.example'
MESSAGE = '0123456789abcdef'


class PrintReadyEmailTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.sources, self.sent_raw = FakeDrive(), MemoryCAS(), [], {}
        self.clock = datetime(2026, 10, 13, 13, tzinfo=timezone.utc)
        self.config = {**archive_config(), daily.FOLDER_CONFIG: FOLDER,
                       'HUB_ALLOWED_EMAILS': TEACHER, 'HUB_STATE_PROBE_ORIGINS': ORIGIN}
        self.drive.add(FOLDER, 'Private new print forms', google.FOLDER)
        self.delivery_id = key('feedbackdelivery', 'fictional teacher-only marks')
        self.port.commit({self.delivery_id: ({'printed_attested': True, 'handed_attested': False}, None)})
        self.delivery_before = deepcopy(self.port.rows[self.delivery_id])
        self.patches = [
            patch.object(email, 'configured_store', return_value=self.port),
            patch.object(email, 'now', side_effect=lambda: self.clock.isoformat()),
            patch.object(google, 'drive_file', side_effect=self.drive.metadata),
            patch.object(google, 'archive_file_id', side_effect=self.drive.reserve),
            patch.object(google, 'archive_create', side_effect=self.drive.create),
            patch.object(google, 'printable_pdf_bytes', side_effect=lambda token, ident, mime: self.drive.contents[ident]),
            patch.object(native_feedback, '_feedback_files', side_effect=lambda token, config: self.catalog()),
            patch.object(native_feedback, '_call', side_effect=AssertionError('No model is authorized')),
            patch.object(google, 'append_rows', side_effect=AssertionError('No grades or delivery writes')),
        ]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    def tearDown(self):
        self.assertEqual(self.port.rows[self.delivery_id], self.delivery_before)
        self.assertEqual(self.drive.moved, [])
        self.assertEqual(self.drive.copied, [])
        self.assertEqual(self.drive.renamed, [])

    def catalog(self):
        return {'configured': True, 'files': deepcopy(self.sources), 'problem': None}

    def add(self, number=1, *, historical=False, course='biology', report_type='critical_aspects'):
        self.clock += timedelta(minutes=1)
        saved_at = '2026-10-12T12:00:00+00:00' if historical else self.clock.isoformat()
        file_id, raw = 'FictionalEmailReport%03d' % number, pdf_bytes(600 + number)
        folder_id = 'FictionalEmailStudentFolder%03d' % number
        self.drive.add(folder_id, 'Fictional student records', google.FOLDER)
        meta = self.drive.add(file_id, 'Fictional report %d.pdf' % number, daily.PDF, [folder_id])
        meta.update(md5Checksum=hashlib.md5(raw).hexdigest(), size=str(len(raw)),
                    createdTime=saved_at, modifiedTime=saved_at)
        self.drive.contents[file_id] = raw
        source = {'id': file_id, 'name': meta['name'], 'course': course, 'mime': daily.PDF,
                  'feedback_id': 'feedbacksource_' + format(number, '064x'), 'archived': True,
                  'saved_at': saved_at, 'created_at': saved_at,
                  'created_day': saved_at[:10], 'feedback_day': saved_at[:10],
                  'report_type': report_type, 'pdf_sha256': hashlib.sha256(raw).hexdigest(),
                  'student_folder_url': 'https://drive.google.com/drive/folders/' + folder_id}
        if report_type == 'missing_work_contract':
            source.update(contract_status='open', contract_checked_at=saved_at,
                          contract_evidence_expires_at=(self.clock + timedelta(minutes=10)).isoformat())
        self.sources.append(source)
        return source

    def activate(self, **kwargs):
        return email.activate('read', self.config, TEACHER, actor=TEACHER, **kwargs)

    def prepare(self):
        return email.prepare('read', 'write', self.config, actor=TEACHER)

    def batch(self, result):
        return self.port.read([result['id']]).documents[result['id']].value

    def raw_message(self, batch, *, attachment=None, to=TEACHER, sender=TEACHER,
                    subject=None, filename=None, subtype='pdf', extra=None):
        message = EmailMessage()
        message['From'], message['To'] = sender, to
        message['Subject'] = batch['subject'] if subject is None else subject
        message.set_content('New school forms are attached for the teacher to print.')
        message.add_attachment(self.drive.contents[batch['drive_file_id']] if attachment is None else attachment,
                               maintype='application', subtype=subtype,
                               filename=batch['name'] if filename is None else filename)
        if extra:
            extra(message)
        return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip('=')

    def begin(self, prepared):
        return email.begin_send('read', self.config, prepared['id'], actor=TEACHER)

    def confirm(self, claim, *, raw=None, labels=None, message_id=MESSAGE, claim_token=None):
        batch = self.batch(claim)
        if claim['id'] not in self.sent_raw:
            self.sent_raw[claim['id']] = self.raw_message(batch)
        return email.confirm_sent(self.config, claim['id'],
                                  claim['claim_token'] if claim_token is None else claim_token,
                                  message_id, ['SENT'] if labels is None else labels,
                                  self.sent_raw[claim['id']] if raw is None else raw, actor=TEACHER)

    def seed(self, source):
        batch = {'drive_file_id': source['id'], 'name': source['name'],
                 'subject': 'Fictional manually verified print forms', 'pdf_sha256': source['pdf_sha256']}
        return {'feedback_id': source['feedback_id'], 'file_id': source['id'],
                'pdf_sha256': source['pdf_sha256'], 'class_key': source['course'],
                'student_key': 'archivestudent_' + 'b' * 64,
                'raw_base64': self.raw_message(batch),
                'receipt': {'verified_sent_and_inbox': True, 'send_count': 1, 'to': TEACHER,
                            'from': TEACHER, 'gmail_labels': ['SENT'],
                            'subject': batch['subject'], 'attachment_filename': batch['name'],
                            'attachment_sha256': source['pdf_sha256'], 'source_drive_file_id': source['id'],
                            'gmail_message_id': MESSAGE}}

    def test_unconfigured_and_idle_outbox_neither_uploads_nor_changes_delivery(self):
        before = deepcopy(self.port.rows)
        self.assertEqual(email.status(self.config)['status'], 'unconfigured')
        self.assertEqual(self.port.rows, before)
        self.activate()
        before = deepcopy(self.port.rows)
        for _ in range(2):
            self.assertEqual(self.prepare()['status'], 'idle')
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.drive.created, [])

    def test_initial_baseline_excludes_existing_reports_even_when_old_copy_metadata_changes(self):
        source = self.add(historical=True)
        self.activate()
        source['saved_at'] = (self.clock + timedelta(minutes=1)).isoformat()
        self.drive.items[source['id']]['version'] = '5'
        self.assertEqual(self.prepare()['status'], 'idle')
        self.assertEqual(sum(email.status(self.config)['counts'].values()), 0)
        self.assertEqual(self.drive.created, [])

    def test_exact_initial_bookended_snapshot_retains_its_cutoff_without_resending_history(self):
        self.add(historical=True)
        files = sorted(deepcopy(self.sources), key=lambda item: item['id'])
        values, unknown, _ = feedback_delivery.source_identities('read', files)
        self.assertFalse(unknown)
        initial = {'schema': 'hub-print-ready-email-initial-baseline/v1', 'complete_bookended': True,
                   'files': files, 'baseline_sha256': content_hash(values), 'observed_at': self.clock.isoformat()}
        self.clock += timedelta(minutes=1)
        self.assertEqual(self.activate(initial_baseline=initial)['activated_at'], initial['observed_at'])
        self.assertEqual(self.prepare()['status'], 'idle')

    def test_changed_initial_snapshot_cannot_silently_expand_historical_exclusion(self):
        initial = {'schema': 'hub-print-ready-email-initial-baseline/v1', 'complete_bookended': True,
                   'files': [], 'baseline_sha256': content_hash({}), 'observed_at': self.clock.isoformat()}
        self.add()
        before = deepcopy(self.port.rows)
        with self.assertRaises(email.Held):
            self.activate(initial_baseline=initial)
        self.assertEqual(self.port.rows, before)

    def test_partial_catalog_cannot_activate_a_historical_baseline(self):
        for catalog in ({'configured': False, 'files': [], 'problem': None},
                        {'configured': True, 'files': [], 'problem': 'One class is unreadable'}):
            with self.subTest(catalog=catalog), patch.object(native_feedback, '_feedback_files', return_value=catalog):
                with self.assertRaises(email.Held):
                    self.activate()
        self.assertEqual(len(self.port.rows), 1)

    def test_activation_catalog_bookend_rejects_a_newly_arriving_report(self):
        self.add()
        first, later = {'configured': True, 'files': [], 'problem': None}, self.catalog()
        with patch.object(native_feedback, '_feedback_files', side_effect=[first, later]), self.assertRaises(email.Held):
            self.activate()
        self.assertEqual(len(self.port.rows), 1)
        self.assertEqual(self.drive.created, [])

    def test_manual_verified_send_seed_prevents_duplicate_when_report_enters_catalog_later(self):
        source = self.add()
        self.sources.clear()
        seed = self.seed(source)
        self.activate(manual_seed=seed)
        self.sources.append(source)
        self.clock += timedelta(minutes=1)
        source['saved_at'] = self.clock.isoformat()
        self.assertEqual(self.prepare()['status'], 'idle')
        self.assertEqual(email.status(self.config)['counts']['sent'], 1)
        self.assertEqual(self.drive.created, [])

    def test_manual_seed_needs_exact_recipient_single_send_labels_and_attachment(self):
        source = self.add()
        valid = self.seed(source)
        for field, value in (('to', 'other@school.example'), ('from', 'other@school.example'),
                             ('send_count', 2), ('gmail_labels', ['INBOX']),
                             ('attachment_sha256', '0' * 64), ('verified_sent_and_inbox', False)):
            seed = deepcopy(valid); seed['receipt'][field] = value
            with self.subTest(field=field), self.assertRaises(email.Held):
                self.activate(manual_seed=seed)
        self.assertEqual(len(self.port.rows), 1)

    def test_manual_seed_claimed_digest_cannot_replace_actual_sent_attachment_proof(self):
        source = self.add(); seed = self.seed(source)
        batch = {'drive_file_id': source['id'], 'name': source['name'],
                 'subject': seed['receipt']['subject']}
        seed['raw_base64'] = self.raw_message(batch, attachment=pdf_bytes(710))
        with self.assertRaises(email.Held):
            self.activate(manual_seed=seed)
        self.assertEqual(len(self.port.rows), 1)

    def test_unauthorized_actor_cannot_enqueue_a_new_report_before_prepare_denies(self):
        self.activate(); self.add()
        before = deepcopy(self.port.rows)
        with self.assertRaises(email.Held):
            email.prepare('read', 'write', self.config, actor='other@school.example')
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.drive.created, [])

    def test_a_then_a_plus_b_sends_only_b_after_confirmed_a(self):
        self.activate(); a = self.add(1, report_type='science_writing')
        source_before = deepcopy(self.drive.items[a['id']])
        first = self.prepare(); claim = self.begin(first)
        self.assertTrue(claim['send_allowed_once'])
        self.assertTrue(claim['claim_token'])
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        policy_id = email._policy_id(self.config)
        policy = self.port.read([policy_id]).documents[policy_id].value
        self.assertEqual(policy['events'], {})
        self.assertEqual(policy['sent_total'], 1)
        event_id = key('printemailreport', [policy_id, TEACHER, a['feedback_id']])
        self.assertEqual(self.port.read([event_id]).documents[event_id].value['status'], 'sent')
        self.assertEqual(self.prepare()['status'], 'idle')
        b = self.add(2, course='forensics')
        second = self.prepare(); batch = self.batch(second)
        self.assertEqual([item['feedback_id'] for item in batch['manifest']], [b['feedback_id']])
        self.assertEqual(second['report_count'], 1)
        self.assertEqual(len(PdfReader(io.BytesIO(self.drive.contents[batch['drive_file_id']])).pages), 1)
        self.assertEqual(self.drive.items[a['id']], source_before)
        self.assertEqual(self.confirm(self.begin(second))['status'], 'sent')
        self.assertEqual(email.status(self.config)['counts']['sent'], 2)
        self.assertEqual(self.port.read([policy_id]).documents[policy_id].value['events'], {})
        self.assertEqual(self.port.read([policy_id]).documents[policy_id].value['sent_total'], 2)
        before = deepcopy(self.port.rows)
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(self.port.rows, before)
        self.assertEqual(len(self.drive.created), 2)

    def test_renamed_system_email_and_daily_pdfs_never_reenter_the_real_legacy_catalog(self):
        self.activate(); source = self.add(); prepared = self.prepare()
        root_id = 'FictionalLegacyFeedbackRoot001'
        self.drive.add(root_id, 'Student feedback - Private', google.FOLDER)
        folders = {course: 'FictionalLegacy' + course + 'Folder001' for course in native_feedback.FEEDBACK_FOLDERS}
        for course, folder_id in folders.items():
            self.drive.add(folder_id, native_feedback.FEEDBACK_FOLDERS[course], google.FOLDER, [root_id])
        legacy_config = {native_feedback.FEEDBACK_ROOT_CONFIG: root_id,
                         native_feedback.FEEDBACK_FOLDER_CONFIG: json.dumps(folders)}
        # A real prepared outbox PDF is renamed to an eligible student-feedback
        # title and placed beside legacy sources. Its immutable system tag wins.
        aggregate = self.drive.items[prepared['drive_file_id']]
        aggregate.update(name='Student feedback - Fictional renamed learner.pdf', parents=[folders['biology']])
        aggregate['properties']['hubFeedbackManifest'] = 'invalid-unless-skipped-before-proof'
        daily_id = 'FictionalTaggedDailyAggregate001'
        self.drive.items[daily_id] = {**deepcopy(aggregate), 'id': daily_id,
                                     'properties': {daily.TAG: 'dailyfeedbackpacket_' + 'd' * 64,
                                                    'hubFeedbackManifest': 'invalid-unless-skipped-before-proof'}}
        self.drive.contents[daily_id] = self.drive.contents[prepared['drive_file_id']]
        control_id = 'FictionalActualLegacyReport001'
        control = self.drive.add(control_id, 'Student feedback - Fictional original learner.pdf',
                                 daily.PDF, [folders['biology']])
        control['properties'] = {'hubStudentFeedbackOnly': 'true'}
        control.update(md5Checksum=hashlib.md5(self.drive.contents[source['id']]).hexdigest(),
                       size=str(len(self.drive.contents[source['id']])))
        self.drive.contents[control_id] = self.drive.contents[source['id']]
        with patch.object(google, 'folder_children', side_effect=self.drive.children):
            catalog = native_feedback._legacy_feedback_files('read', legacy_config)
        self.assertTrue(catalog['configured'])
        self.assertEqual([item['id'] for item in catalog['files']], [control_id])
        for aggregate_id in (prepared['drive_file_id'], daily_id):
            forged_individual = {**deepcopy(source), 'id': aggregate_id,
                                 'pdf_sha256': prepared['pdf_sha256']}
            with self.subTest(tagged_id=aggregate_id), self.assertRaises(email.Held):
                email._report('read', forged_individual)
        self.assertEqual(len(self.drive.created), 1)

    def test_same_logical_report_in_two_identical_private_copies_is_one_event(self):
        self.activate(); a = self.add()
        copy_id = 'FictionalDuplicateReport001'
        self.drive.items[copy_id] = {**deepcopy(self.drive.items[a['id']]), 'id': copy_id}
        self.drive.contents[copy_id] = self.drive.contents[a['id']]
        self.sources.append({**deepcopy(a), 'id': copy_id})
        prepared = self.prepare()
        self.assertEqual(prepared['report_count'], 1)
        self.assertEqual(email.status(self.config)['counts']['reserved'], 1)

    def test_conflicting_copies_of_one_logical_report_hold_before_packet_upload(self):
        self.activate(); a = self.add()
        self.sources.append({**deepcopy(a), 'id': 'FictionalConflictingCopy001', 'pdf_sha256': '0' * 64})
        with self.assertRaises(email.Held):
            self.prepare()
        self.assertEqual(self.drive.created, [])

    def test_version_only_drift_needs_actual_byte_proof_and_preserves_one_batch(self):
        self.activate(); source = self.add(); prepared = self.prepare()
        self.drive.items[source['id']]['version'] = '2'
        with patch.object(google, 'printable_pdf_bytes', wraps=google.printable_pdf_bytes) as download:
            claim = self.begin(prepared)
            self.assertIn(source['id'], [call.args[1] for call in download.call_args_list])
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(len(self.drive.created), 1)

    def test_source_content_privacy_location_and_content_metadata_drift_deny_send(self):
        self.activate(); source = self.add(); prepared = self.prepare()
        original = deepcopy(self.drive.items[source['id']]); raw = self.drive.contents[source['id']]
        changes = ({'permissions': [{'type': 'anyone', 'role': 'reader'}]},
                   {'parents': ['FictionalDifferentPrivateFolder001']},
                   {'modifiedTime': '2026-10-13T15:00:00Z'})
        for change in changes:
            self.drive.items[source['id']] = {**deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises((email.Held, daily.Held, ValueError)):
                self.begin(prepared)
            self.assertEqual(self.batch(prepared)['status'], 'prepared')
        self.drive.items[source['id']] = original
        self.drive.contents[source['id']] = pdf_bytes(650)
        with self.assertRaises(email.Held):
            self.begin(prepared)
        self.drive.contents[source['id']] = raw

    def test_saved_date_course_and_logical_identity_drift_deny_send(self):
        self.activate(); source = self.add(); prepared = self.prepare()
        original = deepcopy(source)
        for field, value in (('saved_at', '2026-10-13T15:00:00Z'), ('feedback_day', '2026-10-14'),
                             ('course', 'anatomy'), ('feedback_id', 'feedbacksource_' + 'f' * 64)):
            source.clear(); source.update(original); source[field] = value
            with self.subTest(field=field), self.assertRaises(email.Held):
                self.begin(prepared)
        source.clear(); source.update(original)

    def test_source_mutation_during_download_cannot_commit_event_or_upload(self):
        self.activate(); source = self.add()
        before = deepcopy(self.port.rows)
        def changed(token, ident, mime):
            self.drive.items[ident]['version'] = 'changed'
            return self.drive.contents[ident]
        with patch.object(google, 'printable_pdf_bytes', side_effect=changed), self.assertRaises(email.Held):
            self.prepare()
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.drive.created, [])

    def test_catalog_mutation_during_selection_cannot_commit_event_or_upload(self):
        self.activate(); self.add()
        first, later = self.catalog(), self.catalog()
        later['files'][0]['saved_at'] = '2026-10-13T15:00:00Z'
        before = deepcopy(self.port.rows)
        with patch.object(native_feedback, '_feedback_files', side_effect=[first, later]), self.assertRaises(email.Held):
            self.prepare()
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.drive.created, [])

    def test_expired_and_closed_contracts_do_not_block_unrelated_fresh_reports(self):
        self.activate()
        stale = self.add(1, report_type='missing_work_contract')
        stale['contract_evidence_expires_at'] = self.clock.isoformat()
        closed = self.add(2, report_type='missing_work_contract'); closed['contract_status'] = 'closed'
        fresh = self.add(3, report_type='science_writing')
        prepared = self.prepare()
        self.assertEqual([item['feedback_id'] for item in self.batch(prepared)['manifest']], [fresh['feedback_id']])
        self.assertEqual(prepared['report_count'], 1)

    def test_fresh_contract_and_science_writing_are_distinct_forms_in_one_incremental_email(self):
        self.activate()
        contract = self.add(1, report_type='missing_work_contract')
        writing = self.add(2, report_type='science_writing')
        prepared = self.prepare()
        self.assertEqual({item['feedback_id'] for item in self.batch(prepared)['manifest']},
                         {contract['feedback_id'], writing['feedback_id']})
        self.assertEqual(prepared['report_count'], 2)

    def test_contract_expiry_after_prepare_denies_send_without_another_upload(self):
        self.activate(); self.add(report_type='missing_work_contract'); prepared = self.prepare()
        self.clock += timedelta(minutes=11)
        with self.assertRaises(email.Held):
            self.begin(prepared)
        self.assertEqual(self.batch(prepared)['status'], 'prepared')
        self.assertEqual(len(self.drive.created), 1)

    def test_contract_expiring_during_packet_download_cannot_receive_a_send_claim(self):
        self.activate(); self.add(report_type='missing_work_contract'); prepared = self.prepare()
        packet_id, original = prepared['drive_file_id'], google.printable_pdf_bytes
        def download(token, ident, mime):
            raw = original(token, ident, mime)
            if ident == packet_id:
                self.clock += timedelta(minutes=11)
            return raw
        with patch.object(google, 'printable_pdf_bytes', side_effect=download), self.assertRaises(email.Held):
            self.begin(prepared)
        self.assertEqual(self.batch(prepared)['status'], 'prepared')

    def test_removed_or_closed_current_contract_denies_an_already_prepared_send(self):
        self.activate(); self.add(report_type='missing_work_contract'); prepared = self.prepare()
        self.sources.clear()
        with self.assertRaises(email.Held):
            self.begin(prepared)
        self.assertEqual(self.batch(prepared)['status'], 'prepared')

    def test_uncertain_state_commit_is_recovered_by_exact_readback_without_another_send_claim(self):
        self.port.fail_after_commit = True
        self.activate(); self.add(); prepared = self.prepare()
        self.port.fail_after_commit = True
        claim = self.begin(prepared)
        with self.assertRaises(email.Held):
            self.begin(prepared)
        self.port.fail_after_commit = True
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(len(self.drive.created), 1)

    def test_concurrent_send_claims_from_same_prepared_snapshot_allow_only_one_worker(self):
        self.activate(); self.add(); prepared = self.prepare()
        barrier, original = Barrier(2), email._commit
        def commit(port, changes):
            if changes.get(prepared['id'], ({}, None))[0].get('status') == 'send_reserved':
                barrier.wait(timeout=5)
            return original(port, changes)
        def attempt(_):
            try:
                return self.begin(prepared)
            except (email.Held, Conflict, StoreUnavailable):
                return None
        with patch.object(email, '_commit', side_effect=commit), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        allowed = [outcome for outcome in outcomes if outcome and outcome.get('send_allowed_once')]
        self.assertEqual(len(allowed), 1)
        self.assertEqual(self.batch(prepared)['status'], 'send_reserved')

    def test_prepare_cas_race_cannot_upload_the_same_incremental_selection_twice(self):
        self.activate(); self.add()
        email.reconcile('read', self.config)
        barrier, original = Barrier(2), email._commit
        def commit(port, changes):
            if any(value.get('schema') == email.SCHEMA and value.get('status') == 'creating'
                   for value, _ in changes.values()):
                barrier.wait(timeout=5)
            return original(port, changes)
        def attempt(_):
            try:
                return self.prepare()
            except (email.Held, Conflict, StoreUnavailable):
                return None
        with patch.object(email, '_commit', side_effect=commit), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertTrue(any(outcome for outcome in outcomes))
        self.assertEqual(len(self.drive.created), 1)

    def test_unconfirmed_batch_reservation_never_reaches_drive_upload(self):
        self.activate(); self.add()
        original = self.port.commit
        def unavailable(changes):
            if any(value.get('status') == 'creating' for value, _ in changes.values()):
                raise StoreUnavailable('Fictional failure before commit')
            return original(changes)
        with patch.object(self.port, 'commit', side_effect=unavailable), self.assertRaises(StoreUnavailable):
            self.prepare()
        self.assertEqual(self.drive.created, [])
        self.assertIsNone(email.status(self.config)['active_batch'])
        self.assertEqual(email.status(self.config)['counts']['ready'], 1)
        self.assertEqual(self.prepare()['status'], 'prepared')
        self.assertEqual(len(self.drive.created), 1)

    def test_uncertain_upload_is_read_only_reconciled_and_never_repeated(self):
        self.activate(); self.add(); self.drive.fail_create = True
        with self.assertRaises((email.Held, google.GoogleError)):
            self.prepare()
        self.drive.fail_create = False
        with self.assertRaises((email.Held, google.GoogleError)):
            self.prepare()
        self.assertEqual(len(self.drive.created), 1)
        self.assertEqual(email.status(self.config)['counts']['reserved'], 1)

    def test_lost_successful_upload_response_recovers_exact_packet_once(self):
        self.activate(); self.add(); self.drive.fail_after_create = True
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(self.prepare()['id'], prepared['id'])
        self.assertEqual(len(self.drive.created), 1)

    def test_interrupted_multi_chunk_retirement_recovers_unsent_fresh_forms(self):
        self.activate()
        contracts = [self.add(number, report_type='missing_work_contract') for number in range(1, 32)]
        fresh = self.add(32, report_type='science_writing')
        for source in contracts:
            source['contract_evidence_expires_at'] = (self.clock + timedelta(minutes=2)).isoformat()
        prepared = self.prepare()
        self.assertEqual(prepared['report_count'], 32)
        self.clock += timedelta(minutes=3)
        original, interrupted = email._commit, False
        def commit(port, changes):
            nonlocal interrupted
            result = original(port, changes)
            if not interrupted and sum(value.get('status') == 'held' for value, _ in changes.values()) >= 30:
                interrupted = True
                raise StoreUnavailable('Fictional stop after first retirement chunk')
            return result
        with patch.object(email, '_commit', side_effect=commit), self.assertRaises(StoreUnavailable):
            self.prepare()
        self.assertTrue(interrupted)
        self.assertEqual(self.batch(prepared)['status'], 'prepared')
        next_batch = self.prepare()
        self.assertEqual([source['feedback_id'] for source in self.batch(next_batch)['manifest']], [fresh['feedback_id']])
        self.assertEqual(self.batch(prepared)['status'], 'retired_before_send')
        self.assertEqual(len(self.drive.created), 2)

    def partial_confirmation(self):
        self.activate()
        for number in range(1, 32):
            self.add(number)
        claim = self.begin(self.prepare())
        original, interrupted = email._commit, False
        def commit(port, changes):
            nonlocal interrupted
            result = original(port, changes)
            if not interrupted and sum(ident.startswith('printemailreport_') and value.get('status') == 'sent'
                                       for ident, (value, _) in changes.items()) >= 30:
                interrupted = True
                raise StoreUnavailable('Fictional stop after first Sent receipt chunk')
            return result
        with patch.object(email, '_commit', side_effect=commit), self.assertRaises(StoreUnavailable):
            self.confirm(claim)
        self.assertTrue(interrupted)
        self.assertEqual(self.batch(claim)['status'], 'sent_unrecorded')
        return claim

    def test_partial_confirmation_resumes_exact_receipt_without_another_send(self):
        claim = self.partial_confirmation()
        with self.assertRaises(email.Held):
            self.prepare()
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(email.status(self.config)['counts']['sent'], 31)
        self.assertEqual(len(self.drive.created), 1)

    def test_partial_sent_receipt_cannot_be_replaced_by_a_different_message(self):
        claim = self.partial_confirmation()
        before = deepcopy(self.port.rows)
        with self.assertRaises(email.Held):
            self.confirm(claim, message_id='fedcba9876543210')
        self.assertEqual(self.port.rows, before)

    def test_uncertain_send_remains_fenced_until_exact_sent_readback_resolves_it(self):
        self.activate(); self.add(); prepared = self.prepare(); claim = self.begin(prepared)
        held = email.hold_uncertain(self.config, claim['id'], actor=TEACHER, claim_token=claim['claim_token'])
        self.assertEqual(held['status'], 'uncertain')
        for action in (self.prepare, lambda: self.begin(prepared)):
            with self.assertRaises(email.Held):
                action()
        self.assertEqual(self.confirm(claim)['status'], 'sent')
        self.assertEqual(self.prepare()['status'], 'idle')

    def test_claim_token_is_required_for_confirm_and_uncertainty_and_never_allows_another_send(self):
        self.activate(); self.add(); claim = self.begin(self.prepare())
        with self.assertRaises(email.Held):
            self.confirm(claim, claim_token='0' * 64)
        with self.assertRaises(email.Held):
            email.hold_uncertain(self.config, claim['id'], actor=TEACHER, claim_token='0' * 64)
        self.assertEqual(self.batch(claim)['status'], 'send_reserved')

    def test_confirmation_requires_actual_sent_label_message_id_and_raw_mime(self):
        self.activate(); self.add(); claim = self.begin(self.prepare())
        for kwargs in ({'labels': ['INBOX']}, {'message_id': 'not-a-gmail-id'}, {'raw': '@@@'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(email.Held):
                self.confirm(claim, **kwargs)
        self.assertEqual(self.batch(claim)['status'], 'send_reserved')
        self.assertEqual(email.status(self.config)['counts']['sent'], 0)

    def test_raw_mime_rejects_wrong_recipient_sender_subject_pdf_filename_or_type(self):
        self.activate(); self.add(); claim = self.begin(self.prepare()); batch = self.batch(claim)
        variants = ({'to': 'other@school.example'}, {'to': TEACHER + ', other@school.example'},
                    {'sender': 'other@school.example'}, {'subject': 'Different batch'},
                    {'attachment': pdf_bytes(700)}, {'filename': 'other.pdf'}, {'subtype': 'octet-stream'})
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(email.Held):
                self.confirm(claim, raw=self.raw_message(batch, **variant))
        self.assertEqual(self.batch(claim)['status'], 'send_reserved')

    def test_raw_mime_rejects_cc_bcc_duplicate_headers_and_extra_attachments(self):
        self.activate(); self.add(); claim = self.begin(self.prepare()); batch = self.batch(claim)
        extras = (lambda message: message.__setitem__('Cc', 'other@school.example'),
                  lambda message: message.__setitem__('Bcc', 'other@school.example'),
                  lambda message: message.add_attachment(b'%PDF other', maintype='application', subtype='pdf', filename='extra.pdf'))
        for extra in extras:
            with self.subTest(extra=extra), self.assertRaises(email.Held):
                self.confirm(claim, raw=self.raw_message(batch, extra=extra))
        raw = base64.urlsafe_b64decode(self.raw_message(batch) + '==')
        duplicate = b'To: ' + TEACHER.encode() + b'\n' + raw
        with self.assertRaises(email.Held):
            self.confirm(claim, raw=base64.urlsafe_b64encode(duplicate).decode())

    def test_raw_mime_without_attachment_cannot_confirm_a_send(self):
        self.activate(); self.add(); claim = self.begin(self.prepare()); batch = self.batch(claim)
        message = EmailMessage()
        message['To'], message['From'], message['Subject'] = TEACHER, TEACHER, batch['subject']
        message.set_content('Attachment is unavailable.')
        with self.assertRaises(email.Held):
            self.confirm(claim, raw=base64.urlsafe_b64encode(message.as_bytes()).decode())
        self.assertEqual(email.status(self.config)['counts']['sent'], 0)

    def test_exact_sent_receipt_is_idempotent_and_conflicting_receipt_is_not_adopted(self):
        self.activate(); self.add(); claim = self.begin(self.prepare())
        first = self.confirm(claim)
        before = deepcopy(self.port.rows)
        self.assertEqual(self.confirm(claim)['id'], first['id'])
        self.assertEqual(self.port.rows, before)
        with self.assertRaises(email.Held):
            self.confirm(claim, message_id='fedcba9876543210')
        self.assertEqual(self.port.rows, before)

    def test_delayed_same_receipt_confirmation_cannot_clear_a_newer_batch_fence(self):
        self.activate(); self.add(1); claim = self.begin(self.prepare())
        original, interleaved = email._read, {}
        def read(port, ident):
            current = port.rows.get(claim['id'])
            if (ident == claim['id'] and not interleaved and current
                    and current.value['status'] == 'sent_unrecorded'
                    and all(port.rows[event].value['status'] == 'sent' for event in current.value['event_ids'])):
                interleaved['started'] = True
                # Another confirmer finishes A, then the next worker prepares B
                # while this confirmer is paused before its final pointer read.
                self.confirm(claim)
                self.add(2)
                interleaved['next'] = self.prepare()
            return original(port, ident)
        with patch.object(email, '_read', side_effect=read):
            try:
                self.confirm(claim)
            except (email.Held, Conflict, StoreUnavailable):
                pass  # A stale confirmer may stop; it may not alter B's fence.
        self.assertIn('next', interleaved)
        next_batch = interleaved['next']
        self.assertEqual(email.status(self.config)['active_batch'], next_batch['id'])
        self.assertTrue(self.begin(next_batch)['send_allowed_once'])


class PrintReadyEmailAuthTests(unittest.TestCase):
    def request(self, payload=None, *, method='POST', path='/v2/api/feedback/email/outbox',
                assertion='signed', origin=ORIGIN, query=''):
        raw = json.dumps(payload or {'action': 'prepare'}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
               'wsgi.input': io.BytesIO(raw), 'HTTP_ORIGIN': origin,
               'HTTP_X_GOOG_IAP_JWT_ASSERTION': assertion}
        seen = {}
        body = b''.join(app.application(env, lambda status, headers: seen.update(status=status, headers=dict(headers)),
                                       {'HUB_STATE_PROBE_ORIGINS': ORIGIN, 'HUB_ALLOWED_EMAILS': TEACHER}))
        return seen, body

    def test_missing_teacher_auth_denies_get_post_and_pdf_before_access(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()), \
                patch.object(google, 'service_token', side_effect=AssertionError('No unauthenticated token')), \
                patch.object(email, 'status') as status, patch.object(email, 'prepare') as prepare, \
                patch.object(email, 'packet_pdf') as pdf:
            for method, path in (('POST', '/v2/api/feedback/email/outbox'),
                                 ('GET', '/v2/api/feedback/email/outbox'), ('GET', '/v2/api/feedback/email/pdf')):
                with self.subTest(method=method, path=path):
                    self.assertTrue(self.request(method=method, path=path, assertion=None)[0]['status'].startswith('401'))
            status.assert_not_called(); prepare.assert_not_called(); pdf.assert_not_called()

    def test_cross_origin_and_missing_origin_deny_mutation_before_service_token(self):
        with patch.object(google, 'verify_teacher', return_value=TEACHER), \
                patch.object(google, 'service_token', side_effect=AssertionError('No cross-origin token')), \
                patch.object(email, 'prepare') as prepare:
            for origin in ('https://other.fictional.example', None):
                with self.subTest(origin=origin):
                    self.assertTrue(self.request(origin=origin)[0]['status'].startswith('403'))
            prepare.assert_not_called()

    def test_api_passes_verified_teacher_and_exact_claim_token_to_confirm_and_hold(self):
        ident, nonce = 'printemailbatch_' + 'a' * 64, 'b' * 64
        with patch.object(google, 'verify_teacher', return_value=TEACHER), \
                patch.object(email, 'confirm_sent', return_value={'status': 'sent'}) as confirm, \
                patch.object(email, 'hold_uncertain', return_value={'status': 'uncertain'}) as hold:
            payload = {'action': 'confirm_sent', 'id': ident, 'claim_token': nonce,
                       'message_id': MESSAGE, 'labels': ['SENT'], 'raw_base64': 'fictional-raw'}
            self.assertEqual(self.request(payload)[0]['status'], '200 OK')
            self.assertEqual(confirm.call_args.args[2], nonce)
            self.assertEqual(confirm.call_args.kwargs, {'actor': TEACHER})
            self.assertEqual(self.request({'action': 'hold_uncertain', 'id': ident, 'claim_token': nonce})[0]['status'], '200 OK')
            self.assertEqual(hold.call_args.args[2], nonce)
            self.assertEqual(hold.call_args.kwargs, {'actor': TEACHER})

    def test_api_rejects_missing_claim_and_extra_recipients_before_mutation(self):
        with patch.object(google, 'verify_teacher', return_value=TEACHER), \
                patch.object(google, 'service_token', side_effect=AssertionError('No invalid request token')), \
                patch.object(email, 'confirm_sent') as confirm, patch.object(email, 'hold_uncertain') as hold:
            for payload in ({'action': 'hold_uncertain', 'id': 'private'},
                            {'action': 'prepare', 'recipient': 'other@school.example'},
                            {'action': 'confirm_sent', 'id': 'private', 'message_id': MESSAGE,
                             'labels': ['SENT'], 'raw_base64': 'fictional'}):
                with self.subTest(payload=payload):
                    self.assertTrue(self.request(payload)[0]['status'].startswith('400'))
            confirm.assert_not_called(); hold.assert_not_called()

    def test_authenticated_private_pdf_is_no_store_and_never_creates_or_sends(self):
        with patch.object(google, 'verify_teacher', return_value=TEACHER), \
                patch.object(google, 'service_token', return_value='read') as token, \
                patch.object(email, 'packet_pdf', return_value=(b'%PDF fictional', 'New forms.pdf')) as pdf, \
                patch.object(email, 'prepare') as prepare, patch.object(email, 'begin_send') as begin:
            seen, body = self.request(method='GET', path='/v2/api/feedback/email/pdf', query='id=printemailbatch_' + 'a' * 64)
            self.assertEqual(seen['status'], '200 OK')
            self.assertEqual(seen['headers']['Cache-Control'], 'no-store')
            self.assertEqual(seen['headers']['Content-Type'], 'application/pdf')
            self.assertEqual(body, b'%PDF fictional')
            self.assertEqual(token.call_args.kwargs, {})
            pdf.assert_called_once(); prepare.assert_not_called(); begin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
