"""Gap samples retain verified originals without manufacturing assessments."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from hub_v2 import student_archive as archive, science_writing_display as display, student_feedback_checklist
from hub_v2.native_feedback import TEACHER_DIMENSIONS
from tests import test_imported_original_archive as imported_fixture
FILE, RAW = imported_fixture.FILE, imported_fixture.RAW
from tests.test_student_archive import SOURCE, EARLIER, found, row


class WritingGapFilingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = imported_fixture.ImportedOriginalArchiveTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        result = self.fixture.register(); self.assertEqual(result['status'], 'verified')
        self.assertEqual(archive.archive_work('read', 'write', self.fixture.config, 'biology',
            found(row(files=[{'id': EARLIER, 'name': 'Earlier work', 'mime': archive.google.PPTX}],
                      submitted_at='2026-10-06T13:00:00Z')))['status'], 'verified')
        self.port, self.drive = self.fixture.port, self.fixture.drive
        self.student = deepcopy(self.port.rows[self.fixture.student_key].value)
        self.artifacts = [d.value for d in self.port.rows.values() if d.value.get('kind') == 'work' and d.value.get('status') == 'verified'
                          and d.value.get('source_kind') in {archive.IMPORTED_ORIGINAL, 'submitted_work_copy'}]
        bytes_patch = patch.object(archive, '_work_bytes', side_effect=lambda token, meta, heartbeat=None: self.drive.contents[meta['id']])
        bytes_patch.start(); self.addCleanup(bytes_patch.stop)
        checklist_patch = patch.object(student_feedback_checklist, 'refresh_run', return_value={'status': 'verified'})
        checklist_patch.start(); self.addCleanup(checklist_patch.stop)
        self.report = self.make_report()
        self.raw = b'%PDF Fictional complete chart with honest gaps'
        self.report['pdf_sha256'] = hashlib.sha256(self.raw).hexdigest()

    def make_report(self):
        lessons, events = [], []
        for artifact in self.artifacts:
            imported = artifact.get('source_kind') == archive.IMPORTED_ORIGINAL
            ident = artifact['drive_file_id'] if imported else artifact['source_file_id']
            revision = str(self.drive.items[ident]['version'])
            sample = {'title': artifact['assignment'], 'source_date': artifact['date'], 'source_file_id': ident,
                      'source_revision': revision, 'date_basis': artifact['date_basis'],
                      'reference_url': 'https://example.test/exact-teacher'}
            if artifact.get('submitted_at'):
                sample['submitted_at'] = artifact['submitted_at']
                events.append({'source_file_id': ident, 'source_revision': revision, 'submitted_at': artifact['submitted_at']})
            lessons.append(sample)
        source = next(a for a in self.artifacts if a.get('source_file_id') == SOURCE)
        observations = [{'student_key': self.student['student_key'], 'section': self.student['section'],
            'source_file_id': SOURCE, 'source_revision': '1', 'source_modified_at': source['source_modified_at'],
            'date': source['date'], 'lesson': source['assignment'], 'dimension': dimension, 'level': 'Proficient',
            'quote': 'Actual fictional authored writing.', 'explanation': 'A source-backed observation.',
            'reference_url': 'https://example.test/exact-teacher'} for dimension in TEACHER_DIMENSIONS]
        value = {'schema': 'science-writing/v1', 'report_type': 'science_writing', 'class_key': 'biology',
            'id': 'feedbacksource_' + 'd' * 64, 'prepared_at': '2026-10-09', 'saved_at': '2026-10-09T12:00:00Z',
            'student': {'student_key': self.student['student_key'], 'student_folder_id': self.student['drive_file_id'],
                        'name': self.student['name'], 'section': self.student['section']},
            'lessons': lessons, 'provenance': {'source_observations': observations},
            'presentation_context': {'original_submission_times': events}}
        value['presentation_context'] = display.context(value, {'question': 'What supports the fictional claim?',
            'evidence': ['One supplied fact.', 'A second supplied fact.', 'A third supplied fact.'],
            'reference_url': 'https://example.test/exact-teacher'}, gap_caption=display.GAP_CAPTION)
        return value

    def file(self):
        return archive.file_science_writing('read', 'write', self.fixture.config, self.report, self.raw, refresh_index=False)

    def test_unrated_form_and_imported_samples_are_filed_with_receipts_without_inventing_observations(self):
        before = deepcopy(self.report['provenance']['source_observations'])
        result = self.file(); self.assertEqual(result['status'], 'verified', result)
        sources = result['source_records']
        self.assertEqual({s['source_file_id'] for s in sources}, {SOURCE, EARLIER, FILE})
        self.assertEqual(self.report['provenance']['source_observations'], before)
        self.assertEqual(next(s for s in sources if s['source_file_id'] == EARLIER)['submitted_at'], '2026-10-06T13:00:00Z')
        imported = next(s for s in sources if s['source_file_id'] == FILE)
        self.assertIsNone(imported['submitted_at']); self.assertNotIn('source_review_sha256', imported)
        repeated = self.file(); self.assertEqual(result['drive_file_id'], repeated['drive_file_id'])

    def test_wrong_owner_identity_date_revision_or_receipt_reject_before_pdf_create(self):
        baseline = deepcopy(self.report)
        for field, value in [('source_revision', '999'), ('source_date', '2026-10-01'),
                             ('date_basis', 'filename'), ('submitted_at', '2026-10-06T14:00:00Z')]:
            self.report = deepcopy(baseline)
            next(s for s in self.report['lessons'] if s['source_file_id'] == EARLIER)[field] = value
            count = len(self.drive.created); result = self.file()
            with self.subTest(field=field):
                self.assertEqual(result['status'], 'held', result); self.assertEqual(len(self.drive.created), count)
        self.report = baseline
        artifact = next(a for a in self.artifacts if a.get('source_file_id') == EARLIER)
        artifact['identity_status'] = 'identity_unverified'
        self.assertEqual(self.file()['status'], 'held')

    def test_wrong_class_student_parent_or_named_only_artifact_cannot_supply_gap(self):
        artifact = next(a for a in self.artifacts if a.get('source_file_id') == EARLIER)
        before = deepcopy(artifact)
        for field, value in [('class_key', 'forensics'), ('student_key', 'DifferentStudent'),
                             ('parent', 'DifferentFolder'), ('source_kind', archive.NAMED_ARCHIVE)]:
            artifact.clear(); artifact.update(deepcopy(before)); artifact[field] = value
            count = len(self.drive.created); result = self.file()
            with self.subTest(field=field):
                self.assertEqual(result['status'], 'held', result); self.assertEqual(len(self.drive.created), count)
        artifact.clear(); artifact.update(before)

    def test_duplicate_or_omitted_rated_source_does_not_create_report(self):
        baseline = deepcopy(self.report)
        for change in ('duplicate', 'omit'):
            self.report = deepcopy(baseline)
            if change == 'duplicate': self.report['lessons'].append(deepcopy(self.report['lessons'][0]))
            else: self.report['lessons'] = [s for s in self.report['lessons'] if s['source_file_id'] != SOURCE]
            count = len(self.drive.created); result = self.file()
            with self.subTest(change=change):
                self.assertEqual(result['status'], 'held', result); self.assertEqual(len(self.drive.created), count)

    def test_imported_gap_bytes_privacy_and_revision_cannot_bypass_source_validation(self):
        original = deepcopy(self.drive.items[FILE]); raw = self.drive.contents[FILE]
        for change in ('bytes', 'public', 'revision'):
            self.drive.items[FILE] = deepcopy(original); self.drive.contents[FILE] = raw
            if change == 'bytes': self.drive.contents[FILE] = raw + b'edited'
            if change == 'public': self.drive.items[FILE]['permissions'].append({'type': 'anyone', 'role': 'reader'})
            if change == 'revision': self.drive.items[FILE]['version'] = '9'
            count = len(self.drive.created); result = self.file()
            with self.subTest(change=change):
                self.assertEqual(result['status'], 'held', result); self.assertEqual(len(self.drive.created), count)

    def snapshot(self, ident=EARLIER):
        artifact = next(a for a in self.artifacts if a.get('source_file_id') == ident)
        lesson = next(l for l in self.report['lessons'] if l['source_file_id'] == ident)
        lesson['title'] += ' [archived snapshot]'
        lesson['archived_snapshot'] = {'schema': 'writing-gap-archived-snapshot/v1',
            'record_key': artifact['record_key'], 'drive_file_id': artifact['drive_file_id'],
            'content_proof': deepcopy(artifact['content_proof']), 'source_file_id': ident,
            'source_version': artifact['source_version'], 'source_modified_at': artifact['source_modified_at'],
            'disclosure': 'Archived submitted revision; the current original may differ.'}
        return artifact, lesson

    def test_changed_original_pptx_can_use_explicit_unchanged_owned_snapshot_for_gap_only(self):
        artifact, lesson = self.snapshot()
        original_observations = deepcopy(self.report['provenance']['source_observations'])
        self.drive.items[EARLIER].update(version='9', modifiedTime='2026-10-08T16:00:00Z',
                                         md5Checksum='e' * 32, size='999')
        self.drive.contents[EARLIER] = b'New original writing, never substituted into the old sample'
        result = self.file(); self.assertEqual(result['status'], 'verified', result)
        source = next(s for s in result['source_records'] if s['source_file_id'] == EARLIER)
        self.assertEqual(source['source_version'], '1'); self.assertEqual(source['date'], '2026-10-06')
        self.assertTrue(source['historical_snapshot']['original_revision_changed'])
        self.assertEqual(source['historical_snapshot']['current_original_revision']['version'], '9')
        self.assertEqual(source['historical_snapshot']['drive_file_id'], artifact['drive_file_id'])
        self.assertEqual(self.report['provenance']['source_observations'], original_observations)

    def test_native_snapshot_content_proof_survives_original_version_only_drift(self):
        artifact = next(a for a in self.artifacts if a.get('source_file_id') == EARLIER)
        native = 'application/vnd.google-apps.presentation'
        proof = {'basis': 'native_presentation/v1', 'sha256': 'a' * 64, 'slides': 3, 'image_instances': 1}
        artifact['mime'] = native; artifact['content_proof'] = proof
        self.drive.items[artifact['drive_file_id']]['mimeType'] = native
        self.drive.items[EARLIER]['mimeType'] = native
        artifact['source_revision']['mimeType'] = native
        self.snapshot(); self.drive.items[EARLIER]['version'] = '2'
        with patch.object(archive, '_work_content', return_value=proof) as content:
            result = self.file()
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(any(call.args[1]['id'] == artifact['drive_file_id'] for call in content.call_args_list))

    def test_snapshot_edit_wrong_copy_receipt_revision_or_missing_disclosure_refuse(self):
        self.snapshot(); baseline = deepcopy(self.report)
        for change in ('bytes', 'copy', 'record', 'revision', 'disclosure', 'label'):
            self.report = deepcopy(baseline)
            lesson = next(l for l in self.report['lessons'] if l['source_file_id'] == EARLIER)
            if change == 'copy': lesson['archived_snapshot']['drive_file_id'] = 'DifferentCopy'
            if change == 'record': lesson['archived_snapshot']['record_key'] = 'DifferentStudentReceipt'
            if change == 'revision': lesson['archived_snapshot']['source_version'] = '9'
            if change == 'disclosure': lesson['archived_snapshot']['disclosure'] = 'Current writing'
            if change == 'label': lesson['title'] = 'Unlabeled original'
            proof = {'sha256': 'f' * 64} if change == 'bytes' else next(a for a in self.artifacts if a.get('source_file_id') == EARLIER)['content_proof']
            count = len(self.drive.created)
            with patch.object(archive, '_work_content', return_value=proof):
                result = self.file()
            with self.subTest(change=change):
                self.assertEqual(result['status'], 'held', result); self.assertEqual(len(self.drive.created), count)

    def test_snapshot_cannot_relax_rated_source_freshness(self):
        self.snapshot(SOURCE)
        count = len(self.drive.created); result = self.file()
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('unrated', result['problem']); self.assertEqual(len(self.drive.created), count)

    def test_archive_receipt_mutation_at_bookend_holds_without_creating_pdf(self):
        original = archive._science_chart_sources
        def mutate(*args):
            value = original(*args)
            next(a for a in self.artifacts if a.get('source_file_id') == EARLIER)['date'] = '2026-10-01'
            return value
        count = len(self.drive.created)
        with patch.object(archive, '_science_chart_sources', side_effect=mutate):
            result = self.file()
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('changed during filing', result['problem']); self.assertEqual(len(self.drive.created), count)

    def test_gap_source_mutation_at_bookend_holds_without_creating_pdf(self):
        original = archive._science_chart_sources
        def mutate(*args):
            value = original(*args); self.drive.items[EARLIER]['version'] = '9'; return value
        count = len(self.drive.created)
        with patch.object(archive, '_science_chart_sources', side_effect=mutate):
            result = self.file()
        self.assertEqual(result['status'], 'held', result)
        self.assertIn('changed during filing', result['problem']); self.assertEqual(len(self.drive.created), count)
