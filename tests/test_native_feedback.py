"""Native feedback acceptance on fictional work and an isolated CAS store."""
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from hub_v2 import app, feedback_pdf, google, native_feedback as fb
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_assessment_provider import OPENAI_CONFIG


def inputs():
    return {'scope': fb.target('biology', 'Wk 04', 'FictionalForm123456789012345', 'FictionalStudent1234567890'),
            'person': {'name': 'Ana R.', 'section': 'A4 Biology', 'identity_hash': 'fictional'},
            'assignment': {'assignment_key': 'biology:C04', 'assignment_title': 'Fictional carbon lesson'},
            'source': {'file': {'id': 'FictionalStudent1234567890', 'modified_at': '2026-10-01T10:00:00Z'}, 'sha256': 'a' * 64, 'text': 'Student writing'},
            'reference': {'file': {'id': 'FictionalLesson1234567890'}, 'sha256': 'b' * 64, 'text': 'Printed lesson'},
            'aspects': [{'question_id': 'CA1-MASTERY', 'name': 'Carbon moves', 'answer': 'Carbon comes from the air.', 'available': True, 'slide': 3}],
            'answers': [], 'prompt': 'Live teacher policy', 'prompt_url': 'https://example.test/teacher-policy'}


def report():
    return {'student_summary': 'You explain where carbon comes from.', 'question': 'How does the carbon become part of a plant?',
            'aspects': [{'question_id': 'CA1-MASTERY', 'name': 'Carbon moves', 'current_level': 'Developing',
                         'quote': 'Carbon comes from the air.', 'explanation': 'A mechanism is still needed.'}],
            'teacher_notes': ['Private concern never printed.']}


class NativeFeedbackTests(unittest.TestCase):
    def test_learning_report_handoff_does_not_need_source_reads_and_rejects_external_links(self):
        with patch.object(google, 'service_token', side_effect=AssertionError('unnecessary source access')):
            self.assertEqual(app.api('api/learning-report', {}, {}), {'configured': False})
            self.assertEqual(app.learning_report({'HUB_GROWTH_REPORT_URL': 'https://example.test/report'}), {'configured': False})
            url = 'https://docs.google.com/spreadsheets/d/FictionalReport12345/edit'
            result = app.api('api/learning-report', {}, {'HUB_GROWTH_REPORT_URL': url,
                'HUB_GROWTH_TEACHING_URL': url + '#gid=123', 'HUB_GROWTH_REPORT_AS_OF': '2026-10-08'})
            self.assertTrue(result['configured'])
            self.assertEqual(result['teaching_url'], url + '#gid=123')
            self.assertFalse(result['automatic_refresh'])
            self.assertEqual(app.learning_report({'HUB_GROWTH_REPORT_URL': url,
                'HUB_GROWTH_REPORT_AS_OF': 'not a date', 'HUB_GROWTH_TEACHING_URL': 'javascript:alert(1)'})['teaching_url'], url)

    def setUp(self):
        self.port = MemoryCAS()
        self.storage = patch.object(fb, 'configured_store', lambda c: self.port)
        self.model = patch.object(fb, '_call', return_value=(json.dumps(report()), {'input_tokens': 100, 'output_tokens': 50}))
        self.storage.start()
        self.call = self.model.start()
        self.addCleanup(self.storage.stop)
        self.addCleanup(self.model.stop)

    def test_generate_save_reload_print_and_reuse_without_grade_writes(self):
        with patch.object(fb.assessment_save, 'save', side_effect=AssertionError('grade write')):
            draft = fb.generate(OPENAI_CONFIG, inputs())
            self.assertFalse(draft['cached'])
            self.assertIsNone(draft['pdf_url'])
            saved = fb.save(OPENAI_CONFIG, draft['id'], inputs())
            again = fb.generate(OPENAI_CONFIG, inputs())
            self.assertTrue(saved['readback_verified'])
            self.assertTrue(again['cached'])
            self.assertEqual(saved['saved_at'], again['saved_at'])
            self.assertEqual(self.call.call_count, 1)
            value = fb.get_saved(OPENAI_CONFIG, saved['id']).value
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(feedback_pdf.render(value)))
            self.assertEqual(len(pdf.pages), 1)
            text = pdf.pages[0].extract_text()
            self.assertIn(feedback_pdf.HAND_BACK, text)
            self.assertIn('HOW I CAN IMPROVE', text)
            self.assertNotIn('Private concern', text)
            self.assertNotIn('completion score', text)
            value['created_at'] = '2026-10-06T00:04:00+00:00'
            text = PdfReader(io.BytesIO(feedback_pdf.render(value))).pages[0].extract_text()
            self.assertIn('2026-10-05', text)
            self.assertNotIn('2026-10-06', text)

    def test_enabled_student_archive_files_existing_reviewed_pdf_once_without_regeneration_or_delivery_changes(self):
        from hub_v2 import student_archive
        old = inputs()
        draft = fb.generate(OPENAI_CONFIG, old)
        current = deepcopy(old)
        current['archive_identity'] = {'school_email': 'ana.rivera@school.example', 'first': 'Ana', 'last': 'Rivera',
                                       'section': 'A4 Biology', 'matched_by': 'email', 'source': 'form'}
        artifact = {'status': 'verified', 'feedback_id': draft['id'], 'drive_file_id': 'FictionalPdf1234567890',
                    'student_folder_id': 'FictionalStudentFolder123', 'class_key': 'biology',
                    'created_at': '2026-10-08T14:00:00Z', 'url': 'https://drive.google.com/open?id=FictionalPdf1234567890'}
        delivery = {'printed': False, 'handed_to_student': True}
        self.port.commit({'fictional-delivery-state': (delivery, None)})
        with patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'file_feedback', return_value=artifact) as file_feedback, \
                patch.object(google, 'append_rows') as delivery_write:
            saved = fb.save(OPENAI_CONFIG, draft['id'], current, token='read', write_token='write')
            again = fb.save(OPENAI_CONFIG, draft['id'], current, token='read', write_token='write')
            reused = fb.generate(OPENAI_CONFIG, current)
        file_feedback.assert_called_once()
        args = file_feedback.call_args.args
        self.assertEqual(args[:3], ('read', 'write', OPENAI_CONFIG))
        self.assertEqual(args[3]['archive_identity'], current['archive_identity'])
        self.assertEqual(args[3]['id'], draft['id'])
        from pypdf import PdfReader
        printed = PdfReader(io.BytesIO(args[4])).pages[0].extract_text()
        self.assertNotIn('Private concern', printed)
        self.assertEqual(saved['archive'], artifact)
        self.assertEqual(again['archive'], artifact)
        self.assertEqual(reused['id'], draft['id'])
        self.assertTrue(reused['cached'])
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.port.read(['fictional-delivery-state']).documents['fictional-delivery-state'].value, delivery)
        delivery_write.assert_not_called()

    def test_enabled_filing_missing_writer_or_held_identity_preserves_saved_feedback_and_never_rebills(self):
        from hub_v2 import student_archive
        draft = fb.generate(OPENAI_CONFIG, inputs())
        with patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'file_feedback', return_value={'status': 'held', 'problem': 'School email needs verification.'}) as file_feedback:
            with self.assertRaisesRegex(fb.Held, 'feedback is saved.*authorized Drive writer'):
                fb.save(OPENAI_CONFIG, draft['id'], inputs())
            file_feedback.assert_not_called()
            with self.assertRaisesRegex(fb.Held, 'feedback is saved.*School email needs verification'):
                fb.save(OPENAI_CONFIG, draft['id'], inputs(), token='read', write_token='write')
        self.assertTrue(fb.get_saved(OPENAI_CONFIG, draft['id']).value['saved_at'])
        self.assertTrue(fb.generate(OPENAI_CONFIG, inputs())['cached'])
        self.assertEqual(self.call.call_count, 1)

    def test_archive_disabled_keeps_private_saved_pdf_usable_without_drive_tokens(self):
        from hub_v2 import student_archive
        draft = fb.generate(OPENAI_CONFIG, inputs())
        with patch.object(student_archive, 'enabled', return_value=False), \
                patch.object(student_archive, 'file_feedback') as file_feedback:
            result = fb.save(OPENAI_CONFIG, draft['id'], inputs())
        self.assertTrue(result['readback_verified'])
        self.assertTrue(result['pdf_url'])
        file_feedback.assert_not_called()

    def test_today_feedback_lists_only_print_ready_files_created_today_in_their_course_folder(self):
        folders = {'biology': 'BIOLOGY_FOLDER_ID', 'forensics': 'FORENSICS_FOLDER_ID',
                   'anatomy': 'ANATOMY_FOLDER_ID'}
        config = {fb.FEEDBACK_ROOT_CONFIG: 'FEEDBACK_ROOT_ID', fb.FEEDBACK_FOLDER_CONFIG: json.dumps(folders)}
        metadata = {
            'FEEDBACK_ROOT_ID': {'id': 'FEEDBACK_ROOT_ID', 'name': 'Student Feedback — Private 2026–27', 'mimeType': google.FOLDER},
            'BIOLOGY_FOLDER_ID': {'id': 'BIOLOGY_FOLDER_ID', 'name': fb.FEEDBACK_FOLDERS['biology'], 'mimeType': google.FOLDER, 'parents': ['FEEDBACK_ROOT_ID']},
            'FORENSICS_FOLDER_ID': {'id': 'FORENSICS_FOLDER_ID', 'name': fb.FEEDBACK_FOLDERS['forensics'], 'mimeType': google.FOLDER, 'parents': ['FEEDBACK_ROOT_ID']},
            'ANATOMY_FOLDER_ID': {'id': 'ANATOMY_FOLDER_ID', 'name': fb.FEEDBACK_FOLDERS['anatomy'], 'mimeType': google.FOLDER, 'parents': ['FEEDBACK_ROOT_ID']},
        }
        children = {
            'BIOLOGY_FOLDER_ID': [
                {'id': 'bio-new', 'name': 'Feedback — Biology Wk 06.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:00:00Z'},
                {'id': 'bio-draft', 'name': 'DRAFT Feedback — Biology.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:01:00Z'},
                {'id': 'bio-old', 'name': 'Feedback — Biology Wk 05.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T02:00:00Z'},
                {'id': 'bio-summary', 'name': 'Class summary and feedback.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:02:00Z'},
                {'id': 'bio-teacher', 'name': 'TeacherNotes — feedback.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:02:00Z'},
                {'id': 'bio-growth', 'name': 'Growth record — feedback.pdf', 'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:02:00Z'},
            ], 'FORENSICS_FOLDER_ID': [], 'ANATOMY_FOLDER_ID': [],
        }
        with patch.object(fb.google, 'drive_file', side_effect=lambda token, ident, fields: metadata[ident]), \
                patch.object(fb.google, 'folder_children', side_effect=lambda token, ident: children[ident]):
            result = fb.today_files('token', config, date_et='2026-10-08')
            later = fb.today_files('token', config, date_et='2026-10-09')
        self.assertTrue(result['configured'])
        self.assertEqual([item['id'] for item in result['files']], ['bio-new'])
        self.assertEqual(result['files'][0]['course_folder'], fb.FEEDBACK_FOLDERS['biology'])
        self.assertEqual(later['date'], '2026-10-08')
        self.assertEqual(later['requested_date'], '2026-10-09')
        self.assertEqual([item['id'] for item in later['files']], ['bio-new'])

    def test_print_today_merges_current_packet_in_course_order(self):
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        source = BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=612, height=792); writer.write(source)
        files = [
            {'id': 'bio', 'name': 'Feedback Biology', 'course': 'biology', 'mime': 'application/pdf', 'created_at': '2026-10-08T13:00:00Z'},
            {'id': 'forensics', 'name': 'Feedback Forensics', 'course': 'forensics', 'mime': 'application/pdf', 'created_at': '2026-10-08T14:00:00Z'},
        ]
        with patch.object(fb, 'today_files', return_value={'configured': True, 'date': '2026-10-08', 'files': files}), \
                patch.object(fb.google, 'printable_pdf_bytes', return_value=source.getvalue()) as read_pdf:
            packet, name = fb.print_today('token', {}, ['forensics', 'bio'], date_et='2026-10-08')
        self.assertEqual(name, 'New-Student-Feedback-2026-10-08.pdf')
        self.assertEqual(len(PdfReader(BytesIO(packet)).pages), 2)
        self.assertEqual([call.args[1] for call in read_pdf.call_args_list], ['bio', 'forensics'])
        with patch.object(fb, 'today_files', return_value={'configured': True, 'date': '2026-10-08', 'files': files}):
            with self.assertRaises(fb.Held):
                fb.print_today('token', {}, ['bio', 'bio'], date_et='2026-10-08')

    def test_today_finds_nested_archived_student_pdfs_deduplicates_legacy_and_excludes_teacher_reports(self):
        from hub_v2 import student_archive
        def archived(ident, name, **extra):
            return {'status': 'verified', 'drive_file_id': ident, 'name': name, 'class_key': 'biology',
                    'student_folder_id': 'FictionalStudentFolder123', 'student_folder_name': 'Rivera, Ana',
                    'class_folder_id': 'FictionalBiologyFolder123', 'class_folder_name': 'Biology',
                    'created_at': '2026-10-08T14:00:00Z', 'mime': 'application/pdf',
                    'url': 'https://drive.google.com/open?id=' + ident, 'feedback_id': 'feedbackdraft_' + 'a' * 64, **extra}
        index = [archived('same-pdf', 'Feedback — Ana Rivera — carbon.pdf'),
                 archived('nested-pdf', 'Feedback — Sam Avery — carbon.pdf'),
                 archived('teacher-notes', 'TeacherNotes — feedback.pdf'),
                 archived('growth', 'Growth record — feedback.pdf'),
                 archived('teacher-report', 'Teacher report — feedback.pdf'),
                 archived('held', 'Feedback — held.pdf', status='held'),
                 archived('not-pdf', 'Feedback — document', mime='application/vnd.google-apps.document')]
        legacy = {'configured': True, 'problem': None, 'files': [
            {'id': 'same-pdf', 'name': 'Feedback — earlier class packet.pdf', 'course': 'biology',
             'mime': 'application/pdf', 'created_at': '2026-10-08T14:00:00Z', 'created_day': '2026-10-08'}]}
        from pypdf import PdfReader, PdfWriter
        source = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=612, height=792); writer.write(source)
        with patch.object(fb, '_legacy_feedback_files', return_value=legacy), \
                patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'list_feedback', return_value=index), \
                patch.object(google, 'printable_pdf_bytes', return_value=source.getvalue()) as read_pdf:
            today = fb.today_files('token', {}, date_et='2026-10-08')
            packet, _ = fb.print_today('token', {}, [item['id'] for item in today['files']], date_et='2026-10-08')
        self.assertEqual({item['id'] for item in today['files']}, {'same-pdf', 'nested-pdf'})
        self.assertTrue(all(item['archived'] for item in today['files']))
        self.assertEqual(len(PdfReader(io.BytesIO(packet)).pages), 2)
        self.assertEqual(read_pdf.call_count, 2)
        self.assertEqual(today['files'][0]['course_folder_url'], 'https://drive.google.com/drive/folders/FictionalBiologyFolder123')

    def test_archive_only_print_listing_works_without_legacy_feedback_roots(self):
        from hub_v2 import student_archive
        item = {'status': 'verified', 'drive_file_id': 'NestedFeedback123', 'name': 'Feedback — Ana.pdf',
                'class_key': 'biology', 'class_folder_id': 'FictionalBiologyFolder123',
                'student_folder_id': 'FictionalStudentFolder123', 'created_at': '2026-10-07T14:00:00Z',
                'mime': 'application/pdf'}
        with patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'list_feedback', return_value=[item]), \
                patch.object(google, 'drive_file') as legacy_read:
            listed = fb.today_files('token', {}, date_et='2026-10-08')
        self.assertTrue(listed['configured'])
        self.assertEqual(listed['date'], '2026-10-07')
        self.assertEqual([file['id'] for file in listed['files']], ['NestedFeedback123'])
        legacy_read.assert_not_called()

    def test_direct_legacy_class_packets_remain_printable_under_verified_student_records_root(self):
        from hub_v2 import student_archive
        root = 'FictionalRecordsRoot123'
        folders = {cls: 'Fictional' + cls + 'Folder123' for cls in fb.FEEDBACK_FOLDERS}
        config = {fb.FEEDBACK_ROOT_CONFIG: root, fb.FEEDBACK_FOLDER_CONFIG: json.dumps(folders),
                  student_archive.ROOT_CONFIG: root, student_archive.COURSES_CONFIG: json.dumps(folders)}
        def location(token, config, cls):
            return root, {'id': folders[cls], 'name': fb.FEEDBACK_COURSE_NAMES[cls], 'mimeType': google.FOLDER,
                          'parents': [root]}
        packet = {'id': 'FictionalClassPacket123', 'name': 'Biology Wk 04 — Student feedback.pdf',
                  'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:00:00Z'}
        with patch.object(student_archive, 'feedback_location', side_effect=location) as privacy_check, \
                patch.object(student_archive, 'list_feedback', return_value=[]), \
                patch.object(google, 'folder_children', side_effect=lambda token, ident: [packet] if ident == folders['biology'] else []):
            listed = fb.today_files('token', config, date_et='2026-10-08')
        self.assertEqual(privacy_check.call_count, 3)
        self.assertEqual([file['id'] for file in listed['files']], [packet['id']])
        self.assertEqual(listed['files'][0]['course_folder'], 'Biology')
        self.assertNotIn('student_folder', listed['files'][0])
        with patch.object(student_archive, 'feedback_location', side_effect=student_archive.Held('Private access unverified.')):
            with self.assertRaisesRegex(fb.Held, 'Private access unverified'):
                fb.today_files('token', config, date_et='2026-10-08')

    def test_changed_work_policy_identity_reference_or_assignment_blocks_save(self):
        draft = fb.generate(OPENAI_CONFIG, inputs())
        for area, field, value in [('source', 'sha256', 'c' * 64), ('source', 'file', {'modified_at': 'later'}),
                                    ('person', 'identity_hash', 'another student'), ('reference', 'sha256', 'd' * 64),
                                    ('assignment', 'assignment_key', 'biology:C05')]:
            fresh = deepcopy(inputs()); fresh[area][field] = value
            with self.subTest(area=area, field=field), self.assertRaises(fb.Held):
                fb.save(OPENAI_CONFIG, draft['id'], fresh)
        fresh = inputs(); fresh['prompt'] = 'Changed teacher instructions'
        with self.assertRaises(fb.Held):
            fb.save(OPENAI_CONFIG, draft['id'], fresh)
        self.assertEqual(self.call.call_count, 1)

    def test_new_content_gets_a_new_draft_but_moving_week_does_not_rebill(self):
        one = fb.generate(OPENAI_CONFIG, inputs())
        moved = inputs(); moved['scope']['week'] = 'Wk 05'
        self.assertEqual(one['id'], fb.generate(OPENAI_CONFIG, moved)['id'])
        changed = inputs(); changed['source']['sha256'] = 'c' * 64
        self.assertNotEqual(one['id'], fb.generate(OPENAI_CONFIG, changed)['id'])
        self.assertEqual(self.call.call_count, 2)

    def test_interrupted_provider_call_is_not_automatically_rebilled(self):
        self.call.side_effect = TimeoutError()
        for _ in range(2):
            with self.assertRaises(fb.Held):
                fb.generate(OPENAI_CONFIG, inputs())
        self.assertEqual(self.call.call_count, 1)

    def test_concurrent_request_cannot_duplicate_model_call(self):
        def call(*args):
            with self.assertRaises(fb.Held):
                fb.generate(OPENAI_CONFIG, inputs())
            return json.dumps(report()), {}
        self.call.side_effect = call
        fb.generate(OPENAI_CONFIG, inputs())
        self.assertEqual(self.call.call_count, 1)

    def test_invalid_quote_stays_in_review_and_does_not_rebill(self):
        wrong = report(); wrong['aspects'][0]['quote'] = 'Invented words'
        self.call.return_value = json.dumps(wrong), {}
        for _ in range(2):
            with self.assertRaises(fb.Held):
                fb.generate(OPENAI_CONFIG, inputs())
        self.assertEqual(self.call.call_count, 1)

    def test_verified_long_quote_is_shortened_without_another_model_call(self):
        data = inputs(); data['aspects'][0]['answer'] = 'Carbon\n  ' * 30
        response = report(); response['aspects'][0]['quote'] = data['aspects'][0]['answer']
        self.call.return_value = json.dumps(response), {}
        first = fb.generate(OPENAI_CONFIG, data)
        quote = first['report']['aspects'][0]['quote']
        self.assertEqual(len(quote.split()), 25)
        self.assertIn(quote, data['aspects'][0]['answer'])
        self.assertTrue(fb.generate(OPENAI_CONFIG, data)['cached'])
        self.assertEqual(self.call.call_count, 1)
        response['aspects'][0]['quote'] += 'invented ending'
        with self.assertRaises(fb.Held): fb.validate_report(response, data)

    def test_blank_response_never_receives_a_level_or_quote(self):
        blank = inputs(); blank['aspects'][0].update(answer='', available=False)
        with self.assertRaises(fb.Held):
            fb.validate_report(report(), blank)
        expected = report(); expected['aspects'][0].update(quote='', current_level='Evidence unavailable')
        self.assertEqual(fb.validate_report(expected, blank), expected)

    def test_teacher_identity_must_come_from_unique_school_email_form(self):
        row = {'files': [{'id': 'file', 'mime': google.PPTX}], 'matched': True, 'matched_by': 'email', 'source': 'form', 'late': False}
        found = {'lessons': [{'forms': [{'form_id': 'form', 'students': [row]}]}]}
        self.assertIs(fb._row(found, 'form', 'file')[0], row)
        for field, value in [('matched_by', 'name typed in the Form'), ('late', True), ('matched', False), ('source', 'folder')]:
            wrong = deepcopy(found); wrong['lessons'][0]['forms'][0]['students'][0][field] = value
            with self.subTest(field=field), self.assertRaises(fb.Held):
                fb._row(wrong, 'form', 'file')
        row['files'].append({'id': 'other', 'mime': google.PPTX})
        with self.assertRaises(fb.Held): fb._row(found, 'form', 'file')

    def test_returned_response_recovers_after_storage_failure_without_new_call(self):
        original = self.port.commit
        def fail_draft(changes):
            if next(iter(changes)).startswith('feedbackdraft_'):
                raise StoreUnavailable('offline')
            return original(changes)
        with patch.object(self.port, 'commit', fail_draft), self.assertRaises(StoreUnavailable):
            fb.generate(OPENAI_CONFIG, inputs())
        recovered = fb.generate(OPENAI_CONFIG, inputs())
        self.assertTrue(recovered['cached'])
        self.assertEqual(self.call.call_count, 1)

    def test_model_may_not_choose_another_aspect_or_question(self):
        for change in ['question_id', 'name']:
            wrong = report(); wrong['aspects'][0][change] = 'Other'
            with self.assertRaises(fb.Held): fb.validate_report(wrong, inputs())
        wrong = report(); wrong['question'] += ' And why?'
        with self.assertRaises(fb.Held): fb.validate_report(wrong, inputs())

    def test_older_request_cannot_replace_latest_submission_pointer(self):
        old = inputs(); fb.generate(OPENAI_CONFIG, old)
        new = inputs(); new['source']['sha256'] = 'e' * 64
        new['source']['file']['modified_at'] = '2026-10-02T10:00:00Z'
        latest = fb.generate(OPENAI_CONFIG, new)
        fb.generate(OPENAI_CONFIG, old)
        self.assertEqual(fb._read(self.port, fb._ref(old['scope'])).value['id'], latest['id'])


class NativeFeedbackEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.row = {'files': [{'id': 'file', 'mime': google.PPTX}], 'matched': True,
                    'matched_by': 'email', 'source': 'form', 'late': False,
                    'first': 'Ana', 'last': 'Rivera', 'email': 'fictional@example.test', 'section': 'A4'}
        self.found = {'lessons': [{'forms': [{'form_id': 'form', 'students': [self.row]}]}]}
        self.scope = fb.target('biology', 'Wk 05', 'form', 'file')
        self.assignment = {'class_key': 'biology', 'assignment_code': 'C04', 'source_deck_id': 'template'}
        self.score = {'compared': True, 'template_id': 'template', 'code': 'C04',
                      'file_updated': 'student-time', 'template_updated': 'lesson-time',
                      'items': [{'category': 'DRAFT', 'id': 'CA1-MASTERY', 'status': 'done',
                                 'answer': 'Carbon moves.', 'slide': 2}]}
        self.captures = [{'file': {'modified_at': 'student-time'}, 'sha256': 'a', 'text': 'Student'},
                         {'file': {'modified_at': 'lesson-time'}, 'sha256': 'b',
                          'text': 'CRITICAL ASPECTS\n1. Carbon moves\n[[MARKER-INVENTORY:C04]]'}]

    def read(self, decks=None):
        with patch.object(fb.completion, 'score_file', return_value=self.score), \
             patch.object(fb.grading_records, 'read_records', return_value={'assignments': [self.assignment]}), \
             patch.object(fb.completion, 'lesson_decks', return_value=['template'] if decks is None else decks), \
             patch.object(fb.assessment_save, 'capture', side_effect=self.captures), \
             patch.object(fb.feedback, 'load_prompt', return_value=('Live policy', 'policy-url')):
            return fb.evidence('signed', 'token', {}, {}, self.scope, self.found)

    def test_exact_agenda_deck_registry_and_live_prompt_bind_feedback(self):
        result = self.read()
        self.assertEqual(result['aspects'][0]['answer'], 'Carbon moves.')
        self.assertEqual(result['prompt'], 'Live policy')
        self.assertEqual(result['person']['name'], 'Ana R.')
        self.assertEqual(result['archive_identity']['school_email'], self.row['email'])
        self.assertEqual(result['archive_identity']['matched_by'], 'email')
        self.assertEqual(result['archive_identity']['source'], 'form')
        self.assertNotIn('school_email', result['person'])

    def test_wrong_registry_source_or_agenda_deck_is_held_before_model(self):
        self.assignment['source_deck_id'] = 'another-deck'
        with self.assertRaises(fb.Held): self.read()
        self.assignment['source_deck_id'] = 'template'
        with self.assertRaises(fb.Held): self.read(decks=['another-deck'])

    def test_only_an_explicitly_verified_native_ic_identity_is_carried_to_archive_filing(self):
        self.row['ic_student_id'] = '424242'
        self.assertNotIn('ic_student_id', self.read()['archive_identity'])
        self.row['ic_identity_verified'] = True
        self.assertEqual(self.read()['archive_identity']['ic_student_id'], '424242')
        self.row['ic_student_id'] = 'unknown'
        self.assertNotIn('ic_student_id', self.read()['archive_identity'])

    def test_source_changed_during_capture_is_held(self):
        self.captures[0]['file']['modified_at'] = 'changed'
        with self.assertRaises(fb.Held): self.read()


class NativeFeedbackRouteTests(unittest.TestCase):
    def request(self, path, method='GET', body=None, origin='https://hub.example', assertion='signed', query=''):
        raw = json.dumps(body or {}).encode()
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': query, 'wsgi.input': io.BytesIO(raw),
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'HTTP_ORIGIN': origin,
               'HTTP_X_GOOG_IAP_JWT_ASSERTION': assertion}
        seen = {}
        result = b''.join(app.application(env, lambda status, headers: seen.update(status=status, headers=dict(headers)),
                                         config={'HUB_STATE_PROBE_ORIGINS': 'https://hub.example'}))
        return seen, result

    def test_auth_and_origin_rejected_before_private_access(self):
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()), patch.object(fb, 'get_saved') as private:
            seen, _ = self.request('/v2/api/feedback/pdf', query='id=anything')
            self.assertTrue(seen['status'].startswith('401'))
            private.assert_not_called()
        with patch.object(google, 'verify_teacher', return_value='teacher'), patch.object(fb, 'generate') as model:
            seen, _ = self.request('/v2/api/feedback/draft', 'POST', {}, origin='https://other.example')
            self.assertTrue(seen['status'].startswith('403'))
            model.assert_not_called()

    def test_pdf_is_authenticated_private_and_contains_no_teacher_notes(self):
        port = MemoryCAS()
        with patch.object(fb, 'configured_store', return_value=port), patch.object(fb, '_call', return_value=(json.dumps(report()), {})), patch.object(google, 'verify_teacher', return_value='teacher'):
            draft = fb.generate(OPENAI_CONFIG, inputs())
            seen, _ = self.request('/v2/api/feedback/pdf', query='id=' + draft['id'])
            self.assertTrue(seen['status'].startswith('409'))
            fb.save(OPENAI_CONFIG, draft['id'], inputs())
            seen, pdf = self.request('/v2/api/feedback/pdf', query='id=' + draft['id'])
            self.assertEqual(seen['status'], '200 OK')
            self.assertEqual(seen['headers']['Cache-Control'], 'no-store')
            self.assertEqual(seen['headers']['Content-Type'], 'application/pdf')
            self.assertTrue(pdf.startswith(b'%PDF'))
