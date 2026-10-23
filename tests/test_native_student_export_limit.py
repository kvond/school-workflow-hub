"""Exact export-size failures may read unchanged native submissions, never access denials."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import completion, google, native_teacher_source
from test_native_teacher_source import native, metadata, shape
from test_completion_readiness import STUDENT, TEMPLATE


class NativeStudentExportLimitTests(unittest.TestCase):
    def setUp(self):
        self.meta = metadata(id=STUDENT)
        self.native = native(); self.native['presentationId'] = STUDENT
        self.size_error = google.GoogleError('Export size limit', 403, 'exportSizeLimitExceeded')

    def read(self, *, value=None, bookends=None, error=None):
        with patch.object(google, 'deck_bytes', side_effect=error or self.size_error), \
             patch.object(google, 'drive_file', side_effect=bookends or [self.meta, self.meta]), \
             patch.object(google, 'native_presentation', return_value=self.native if value is None else value):
            return completion._read('reader', STUDENT, self.meta)

    def test_exact_size_failure_preserves_full_native_source_student_role_and_revision(self):
        before = deepcopy(self.native)
        read = self.read()
        self.assertEqual(read['source_kind'], 'native_student_source')
        self.assertEqual(read['native_source_id'], STUDENT)
        self.assertEqual(read['native_source_revision'], '1')
        self.assertEqual(read['native_source'], before)
        self.assertEqual(read['slides'][1]['units'][0]['text'], 'Explain the observation.')
        self.assertEqual(self.native, before)
        self.assertEqual(completion.student_readiness(read, revision='1')['status'], 'READY')

    def test_access_generic_quota_timeout_and_other_status_never_fall_back(self):
        for error in (google.GoogleError('Denied',403,'insufficientFilePermissions'),
                      google.GoogleError('Unknown',403), google.GoogleError('Quota',403,'downloadQuotaExceeded'),
                      google.GoogleError('Missing',404,'exportSizeLimitExceeded'),
                      google.GoogleError('Timeout',reason='read_timeout')):
            with self.subTest(reason=error.reason), patch.object(google, 'deck_bytes', side_effect=error), \
                 patch.object(google, 'drive_file') as metadata_read, patch.object(google, 'native_presentation') as native_read:
                with self.assertRaises(google.GoogleError):completion._read('reader',STUDENT,self.meta)
                metadata_read.assert_not_called(); native_read.assert_not_called()

    def test_pptx_and_teacher_roles_keep_existing_reader_and_no_student_fallback(self):
        for meta, role in (({**self.meta,'mimeType':google.PPTX},'student'),(self.meta,'teacher')):
            with self.subTest(role=role), patch.object(google,'deck_bytes',side_effect=self.size_error), \
                 patch.object(google,'native_presentation') as native_read:
                with self.assertRaises(google.GoogleError):completion._read('reader',STUDENT,meta,source_role=role)
                native_read.assert_not_called()

    def test_successful_export_is_preferred_without_additional_native_reads(self):
        expected={'slides':[], 'size':(1,1)}
        with patch.object(google,'deck_bytes',return_value=b'export'), patch.object(completion,'read_deck',return_value=expected), \
             patch.object(google,'native_presentation') as native_read, patch.object(google,'drive_file') as metadata_read:
            self.assertIs(completion._read('reader',STUDENT,self.meta),expected)
            native_read.assert_not_called(); metadata_read.assert_not_called()

    def test_stale_missing_wrong_identity_and_trashed_before_metadata_hold(self):
        for changes in ({'version':'2'},{'id':'OTHER'},{'trashed':True},{'version':''},{'modifiedTime':''},
                        {'mimeType':google.PPTX}):
            with self.subTest(changes=changes), patch.object(google,'deck_bytes',side_effect=self.size_error), \
                 patch.object(google,'drive_file',return_value={**self.meta,**changes}), \
                 patch.object(google,'native_presentation') as native_read:
                with self.assertRaises(completion.Unreadable):completion._read('reader',STUDENT,self.meta)
                native_read.assert_not_called()

    def test_changed_version_location_or_fields_after_native_read_hold(self):
        for changes in ({'version':'2'},{'parents':['moved']},{'name':'Changed'}):
            with self.subTest(changes=changes), self.assertRaises(completion.Unreadable):
                self.read(bookends=[self.meta,{**self.meta,**changes}])

    def test_wrong_native_identity_empty_slides_and_unsupported_table_hold(self):
        table=deepcopy(self.native); table['slides'][1]['pageElements'].append({'objectId':'table','table':{}})
        for value in ({**self.native,'presentationId':'OTHER'},{**self.native,'slides':[]},table):
            with self.subTest(value=value), self.assertRaises(completion.Unreadable):self.read(value=value)

    def test_native_access_failure_is_not_hidden_or_retried(self):
        with patch.object(google,'deck_bytes',side_effect=self.size_error), \
             patch.object(google,'drive_file',return_value=self.meta), \
             patch.object(google,'native_presentation',side_effect=google.GoogleError('Denied',403)) as native_read:
            with self.assertRaises(google.GoogleError):completion._read('reader',STUDENT,self.meta)
            self.assertEqual(native_read.call_count,1)

    def test_native_original_response_is_counted_but_printed_text_and_blank_revision_are_not(self):
        teacher=native_teacher_source.read(native(),presentation_id=TEMPLATE,revision='1')
        self.native['slides'][1]['pageElements'][1]['shape']['text']['textElements'][0]['textRun']['content']='The original observation supports this explanation.'
        result=completion.assess(self.read(),teacher,found_codes_only=True)
        self.assertEqual(result['notes_score'],10)
        self.assertEqual(result['revision_score'],0)
        self.assertEqual(result['counts']['NOTES']['required'],1)
        self.assertEqual(result['counts']['DRAFT']['required'],1)
        self.assertEqual(completion.assess(teacher,teacher,found_codes_only=True)['notes_score'],0)

    def test_teacher_fallback_stays_teacher_role_and_invalid_native_role_refused(self):
        with self.assertRaises(native_teacher_source.Unreadable):
            native_teacher_source.read(self.native,presentation_id=STUDENT,revision='1',source_role='unknown')
        self.assertEqual(native_teacher_source.read(native(),presentation_id=TEMPLATE,revision='1')['source_kind'],'native_teacher_source')


if __name__ == '__main__':unittest.main()
