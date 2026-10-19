"""Native feedback acceptance on fictional work and an isolated CAS store."""
import io
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from hub_v2 import app, combined_assessment, completion, feedback_pdf, google, native_feedback as fb
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
            'teacher_notes': ['Private concern never printed.'],
            'teacher_dimensions': [{'dimension': dimension, 'level': 'Developing',
                'question_id': 'CA1-MASTERY', 'stage': 'DRAFT', 'quote': 'Carbon comes from the air.',
                'explanation': 'Private science-writing evidence for the teacher.'}
                for dimension in fb.TEACHER_DIMENSIONS]}


class NativeFeedbackTests(unittest.TestCase):
    def test_course_question_prompt_uses_actual_revisions_and_exact_course_context(self):
        self.model.stop()
        for course, focus in [('anatomy', 'structure/function'), ('forensics', 'observation versus inference'), ('biology', None)]:
            data = inputs(); data['scope']['class'] = course
            with self.subTest(course=course), patch.object(fb.assessment_provider, '_call', return_value=('{}', {})) as provider:
                fb._call(OPENAI_CONFIG, data)
                policy, user = provider.call_args.args[1:3]
                payload = json.loads(user)
                self.assertEqual(payload['aspects'], data['aspects'])
                self.assertEqual(payload['printed_lesson_reference'], data['reference']['text'])
                if focus:
                    self.assertIn(focus, policy)
                    self.assertIn('actual supplied\nrevised answer', policy)
                    self.assertIn('Do not invent a misconception', policy)
                    self.assertEqual(payload['course_context']['class'], course)
                    self.assertEqual(payload['course_context']['assignment'], data['assignment'])
                else:
                    self.assertEqual(policy, fb.POLICY + '\nCURRENT TEACHER POLICY:\n' + data['prompt'])
                    self.assertNotIn('course_context', payload)

    def test_new_course_question_policy_is_cached_without_another_call(self):
        for course in ('anatomy', 'forensics'):
            data = inputs(); data['scope']['class'] = course
            draft = fb.generate(OPENAI_CONFIG, data)
            value = fb.get_saved(OPENAI_CONFIG, draft['id']).value
            self.assertEqual(value['question_policy_version'], fb.QUESTION_POLICY_VERSION)
            self.assertEqual(fb.generate(OPENAI_CONFIG, data)['id'], draft['id'])
        self.assertEqual(self.call.call_count, 2)

    def test_prior_course_policy_draft_and_uncertain_call_are_preserved(self):
        data = inputs(); data['scope']['class'] = 'anatomy'
        with patch.object(fb, '_policy', return_value=fb.POLICY):
            old = fb.generate(OPENAI_CONFIG, data)
        self.assertEqual(fb.generate(OPENAI_CONFIG, data)['id'], old['id'])
        self.assertEqual(self.call.call_count, 1)

        data['source']['sha256'] = 'c' * 64
        with patch.object(fb, '_policy', return_value=fb.POLICY), \
                patch.object(fb, '_call', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(fb.Held): fb.generate(OPENAI_CONFIG, data)
        with self.assertRaisesRegex(fb.Held, 'No duplicate AI call'):
            fb.generate(OPENAI_CONFIG, data)
        self.assertEqual(self.call.call_count, 1)

    def test_course_question_upgrade_retains_capture_bound_original_event(self):
        from tests.test_source_repair_provenance import case
        data, _, _ = case()
        data['scope']['class'] = 'forensics'
        with patch.object(fb, '_policy', return_value=fb.POLICY):
            old = fb.generate(OPENAI_CONFIG, data)
        again = fb.generate(OPENAI_CONFIG, data)
        self.assertEqual(again['id'], old['id'])
        self.assertTrue(again['cached'])
        changed = deepcopy(data)
        changed['source']['raw_sha256'] = 'f' * 64
        with self.assertRaises(fb.Held):
            fb.generate(OPENAI_CONFIG, changed)
        self.assertEqual(self.call.call_count, 1)

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

    def test_successful_student_filing_updates_the_original_feedback_day_packet_without_rebilling(self):
        from hub_v2 import daily_feedback_packet, science_writing_native, student_archive
        with patch.object(fb, 'now', return_value='2026-10-07T18:00:00Z'):
            draft = fb.generate(OPENAI_CONFIG, inputs())
        artifact = {'status': 'verified', 'feedback_id': draft['id'], 'drive_file_id': 'FictionalPdf1234567890',
                    'student_folder_id': 'FictionalStudentFolder123', 'class_key': 'biology'}
        with patch.object(fb, 'now', return_value='2026-10-07T18:00:00Z'), \
                patch.object(student_archive, 'enabled', return_value=True), \
                patch.object(student_archive, 'file_feedback', return_value=artifact), \
                patch.object(science_writing_native, 'ensure_saved', return_value={'status': 'verified'}), \
                patch.object(daily_feedback_packet, 'enabled', return_value=True), \
                patch.object(daily_feedback_packet, 'build', return_value={'status': 'ready', 'date': '2026-10-07'}) as build:
            saved = fb.save(OPENAI_CONFIG, draft['id'], inputs(), token='read', write_token='write')
            self.assertEqual(saved['daily_packet']['date'], '2026-10-07')
            build.assert_called_once_with('read', 'write', OPENAI_CONFIG, '2026-10-07')
            build.side_effect = daily_feedback_packet.Held('Daily folder readback needs review.')
            held = fb.save(OPENAI_CONFIG, draft['id'], inputs(), token='read', write_token='write')
        self.assertEqual(held['daily_packet']['status'], 'held')
        self.assertEqual(held['archive'], artifact)
        self.assertEqual(held['saved_at'], saved['saved_at'])
        self.assertTrue(held['pdf_url'])
        self.assertEqual(self.call.call_count, 1)

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

    def test_nested_records_class_packets_use_verified_location_not_unverified_root_parent(self):
        from hub_v2 import student_archive
        root, records = 'FictionalRecordsRoot123', 'FictionalPrivateContainer123'
        folders = {cls: 'Fictional' + cls + 'Folder123' for cls in fb.FEEDBACK_FOLDERS}
        config = {fb.FEEDBACK_ROOT_CONFIG: root, fb.FEEDBACK_FOLDER_CONFIG: json.dumps(folders),
                  student_archive.ROOT_CONFIG: root, student_archive.COURSES_CONFIG: json.dumps(folders),
                  student_archive.RECORDS_CONFIG: records}
        def location(token, config, cls):
            return root, {'id': folders[cls], 'name': fb.FEEDBACK_COURSE_NAMES[cls],
                          'mimeType': google.FOLDER, 'parents': [records]}
        packet = {'id': 'FictionalClassPacket123', 'name': 'Biology Wk 04 — Student feedback.pdf',
                  'mimeType': 'application/pdf', 'createdTime': '2026-10-08T14:00:00Z'}
        with patch.object(student_archive, 'feedback_location', side_effect=location) as privacy_check, \
                patch.object(student_archive, 'list_feedback', return_value=[]), \
                patch.object(google, 'folder_children', side_effect=lambda token, ident: [packet] if ident == folders['biology'] else []):
            listed = fb.today_files('token', config, date_et='2026-10-08')
        self.assertEqual(privacy_check.call_count, 3)
        self.assertEqual([item['id'] for item in listed['files']], [packet['id']])

    def test_provider_timeout_keeps_a_verified_saved_daily_snapshot_available(self):
        from hub_v2 import daily_feedback_packet
        saved = {'status': 'ready', 'configured': True, 'date': '2026-10-07',
                 'id': 'dailyfeedbackpacket_' + 'a' * 64, 'pages': 2}
        error = google.GoogleError("Google didn't answer in time. Try again.")
        with patch.object(fb, '_feedback_files', side_effect=error), \
                patch.object(daily_feedback_packet, 'status', return_value=saved):
            result = fb.today_files('token', {}, date_et='2026-10-08')
        self.assertEqual(result['date'], '2026-10-07')
        self.assertEqual(result['requested_date'], '2026-10-08')
        self.assertEqual(result['files'], [])
        self.assertEqual(result['daily_packet'], saved)
        self.assertEqual(result['source_problem'], str(error))
        with patch.object(fb, '_feedback_files', side_effect=error), \
                patch.object(daily_feedback_packet, 'status', return_value={'status': 'not_saved'}):
            with self.assertRaises(google.GoogleError):
                fb.today_files('token', {}, date_et='2026-10-08')

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
        for dimension in response['teacher_dimensions']:
            dimension['quote'] = data['aspects'][0]['answer']
        self.call.return_value = json.dumps(response), {}
        first = fb.generate(OPENAI_CONFIG, data)
        quote = first['report']['aspects'][0]['quote']
        self.assertEqual(len(quote.split()), 25)
        self.assertIn(quote, data['aspects'][0]['answer'])
        self.assertTrue(all(len(row['quote'].split()) == 25 for row in first['report']['teacher_dimensions']))
        self.assertTrue(fb.generate(OPENAI_CONFIG, data)['cached'])
        self.assertEqual(self.call.call_count, 1)
        response['aspects'][0]['quote'] += 'invented ending'
        with self.assertRaises(fb.Held): fb.validate_report(response, data)

    def test_blank_response_never_receives_a_level_or_quote(self):
        blank = inputs(); blank['aspects'][0].update(answer='', available=False)
        with self.assertRaises(fb.Held):
            fb.validate_report(report(), blank)
        expected = report(); expected['aspects'][0].update(quote='', current_level='Evidence unavailable')
        for dimension in expected['teacher_dimensions']:
            dimension.update(quote='', level='Evidence unavailable', explanation='No attributable answer is available.')
        self.assertEqual(fb.validate_report(expected, blank), expected)

    def test_teacher_identity_must_come_from_unique_school_email_form(self):
        row = {'files': [{'id': 'file', 'mime': google.PPTX}], 'matched': True, 'matched_by': 'email', 'source': 'form', 'late': False}
        found = {'lessons': [{'forms': [{'form_id': 'form', 'students': [row]}]}]}
        self.assertEqual(fb._row(found, 'form', 'file')[0], row)
        for field, value in [('matched_by', 'name typed in the Form'), ('matched', False), ('source', 'folder')]:
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
        self.row = {'files': [{'id': 'file', 'mime': google.PPTX, 'source_form_id': 'form',
                              'source_response_id': 'FictionalResponse12345',
                              'source_submitted_at': '2026-09-30T15:00:00Z'}], 'matched': True,
                    'matched_by': 'email', 'source': 'form', 'late': False,
                    'submitted_at': '2026-09-30T15:00:00Z',
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

    def combined_selection(self, *, selected_problem=False, duplicate_owner=False, global_problem=False):
        form = self.found['lessons'][0]['forms'][0]
        selected = deepcopy(self.row)
        selected['combined_components'] = [{'file': deepcopy(selected['files'][0]),
            'submitted_at': selected['submitted_at'], 'late': False}]
        other = deepcopy(selected)
        other.update(first='Bea', last='Liu', email='other@example.test')
        other['files'][0]['id'] = 'other-file'
        other['combined_components'][0]['file']['id'] = 'file' if duplicate_owner else 'other-file'
        other['selection_problem'] = 'A different original upload could not be read.'
        if duplicate_owner:
            other.pop('selection_problem')
        if selected_problem:
            selected['selection_problem'] = 'This selected original needs review.'
        form['students'] = [selected, other]
        form['combined_assessment'] = {'on_time': [selected, other], 'current': [selected, other],
            'problem': 'Assignment-wide source scope changed.' if global_problem else None}
        return form

    def test_other_combined_student_selection_hold_does_not_block_selected_evidence(self):
        self.combined_selection()
        evidence = self.read()
        self.assertEqual(evidence['scope'], self.scope)
        self.assertEqual(evidence['aspects'][0]['answer'], 'Carbon moves.')

    def test_selected_combined_student_selection_hold_still_blocks_before_scoring(self):
        self.combined_selection(selected_problem=True)
        with patch.object(fb.completion, 'score_file') as score:
            with self.assertRaisesRegex(fb.Held, 'original submission selection'):
                fb.evidence('signed', 'token', {}, SimpleNamespace(), self.scope, self.found)
        score.assert_not_called()

    def test_combined_selection_still_requires_one_exact_file_owner(self):
        self.combined_selection(duplicate_owner=True)
        with self.assertRaisesRegex(fb.Held, 'exactly one student'):
            fb._row(self.found, 'form', 'file')

    def test_assignment_wide_combined_selection_hold_still_blocks_selected_row(self):
        self.combined_selection(global_problem=True)
        with self.assertRaisesRegex(fb.Held, 'original submission selection'):
            fb._row(self.found, 'form', 'file')

    def test_combined_listing_surfaces_only_the_selected_rows_selection_hold(self):
        self.combined_selection()
        with patch.object(fb, 'configured_store', return_value=MemoryCAS()):
            listed = fb.listing({}, self.scope, self.found)
        cards = {card['file_id']: card for card in listed['students']}
        self.assertEqual(cards['file']['hold'], '')
        self.assertIn('original submission selection', cards['other-file']['hold'])
        self.assertIsNone(listed['problem'])

    def test_combined_listing_preserves_distinct_component_source_cards(self):
        form = self.combined_selection()
        component = deepcopy(form['combined_assessment']['on_time'][0]['combined_components'][0])
        component['file']['id'] = 'earlier-component'
        form['combined_assessment']['on_time'][0]['combined_components'].append(component)
        with patch.object(fb, 'configured_store', return_value=MemoryCAS()):
            listed = fb.listing({}, self.scope, self.found)
        selected = [card for card in listed['students'] if card['name'] == 'Ana R.']
        self.assertEqual({card['file_id'] for card in selected}, {'file', 'earlier-component'})
        self.assertTrue(all(card['hold'] == '' for card in selected))

    def test_late_only_feedback_preserves_original_event_and_grading_candidates(self):
        form = self.found['lessons'][0]['forms'][0]
        self.row['late'] = True
        form.update(students=[], late=[self.row])
        before = deepcopy(self.found)
        self.assertEqual(completion.scoring_candidates(form), [])
        selected, _ = fb._row(self.found, 'form', 'file')
        self.assertTrue(selected['late'])
        receipt = fb._submission_receipt(selected, self.scope)
        self.assertEqual(receipt['submitted_at'], self.row['submitted_at'])
        self.assertEqual(receipt['source_response_id'], 'FictionalResponse12345')
        with patch.object(fb, 'configured_store', return_value=MemoryCAS()):
            listed = fb.listing({}, self.scope, self.found)
        self.assertEqual([(r['name'], r['file_id'], r['hold']) for r in listed['students']], [('Ana R.', 'file', '')])
        self.read()
        self.assertEqual(self.scoring_call.kwargs, {'include_late': True})
        self.assertEqual(self.found, before)
        self.assertEqual(completion.scoring_candidates(form), [])

    def test_feedback_selects_meaningful_late_revision_without_promoting_old_source(self):
        original = deepcopy(self.row)
        later = deepcopy(self.row)
        later.update(submitted_at='2026-10-01T15:00:00Z', late=True)
        later['files'][0].update(id='new-file', source_response_id='NewFictionalResponse12345',
                                source_submitted_at=later['submitted_at'])
        self.row['earlier'] = [later]
        before = deepcopy(self.found)
        self.scope['file'] = 'new-file'
        selected, _ = fb._row(self.found, 'form', 'new-file')
        self.assertTrue(selected['late'])
        self.assertEqual(fb._submission_receipt(selected, self.scope)['source_response_id'], 'NewFictionalResponse12345')
        with self.assertRaisesRegex(fb.Held, 'exactly one student'):
            fb._row(self.found, 'form', original['files'][0]['id'])
        self.assertEqual(completion.scoring_candidates(self.found['lessons'][0]['forms'][0])[0]['files'], original['files'])
        self.assertEqual(self.found, before)

    def test_late_feedback_keeps_identity_history_and_original_receipt_holds(self):
        for issue in ('identity', 'receipt', 'conflicting_history'):
            with self.subTest(issue=issue):
                row = deepcopy(self.row); row['late'] = True
                if issue == 'identity': row['matched_by'] = 'file name'
                if issue == 'receipt': row['files'][0]['source_response_id'] = ''
                if issue == 'conflicting_history':
                    wrong = deepcopy(row); wrong['email'] = 'different@example.test'
                    row['earlier'] = [wrong]
                self.found['lessons'][0]['forms'][0].update(students=[], late=[row])
                with patch.object(fb.completion, 'score_file') as score:
                    with self.assertRaises(fb.Held):
                        fb.evidence('signed', 'token', {}, SimpleNamespace(), self.scope, self.found)
                score.assert_not_called()

    def test_combined_late_feedback_selects_each_current_component_with_default_unchanged(self):
        form = self.found['lessons'][0]['forms'][0]
        form['window'] = {'at': '2026-09-30T23:59:59Z'}
        def event(fid, at, late):
            value = deepcopy(self.row); value.update(submitted_at=at, late=late, earlier=[])
            value['files'][0].update(id=fid, source_submitted_at=at, source_response_id='FictionalResponse-' + fid,
                                    from_folder=False, from_shared=False, trashed=False)
            return value
        first = event('file', '2026-09-30T15:00:00Z', False)
        second = event('other-component', '2026-09-30T16:00:00Z', False)
        later = event('new-file', '2026-10-01T15:00:00Z', True)
        first['earlier'] = [second, later, deepcopy(later)]
        form['students'] = [first]
        scope = {'form_id': 'form', 'components': [{'cycle': 'A'}, {'cycle': 'B'}]}
        sources = {'file': {'cycle': 'A', 'basis': {}}, 'new-file': {'cycle': 'A', 'basis': {}},
                   'other-component': {'cycle': 'B', 'basis': {}}}
        on_time = combined_assessment.select_rows(form, scope, sources)
        current = combined_assessment.select_rows(form, scope, sources, include_late=True)
        form['combined_assessment'] = {'on_time': on_time, 'current': current}
        before = deepcopy(self.found)
        self.assertEqual({r['files'][0]['id'] for r in combined_assessment.feedback_rows(form)}, {'file', 'other-component'})
        rows = fb._feedback_students(form)
        self.assertEqual({r['files'][0]['id'] for r in rows}, {'new-file', 'other-component'})
        chosen = next(r for r in rows if r['files'][0]['id'] == 'new-file')
        self.assertTrue(chosen['late'])
        self.scope['file'] = 'new-file'
        self.assertEqual(fb._submission_receipt(chosen, self.scope)['submitted_at'], later['submitted_at'])
        with patch.object(fb, 'configured_store', return_value=MemoryCAS()):
            cards = fb.listing({}, self.scope, self.found)['students']
        self.assertEqual({r['file_id'] for r in cards}, {'new-file', 'other-component'})
        self.assertTrue(all(r['hold'] == '' for r in cards))
        self.read()
        self.assertEqual(self.scoring_call.kwargs, {'include_late': True, 'component_only': True})
        self.assertEqual(self.found, before)

    def read(self, decks=None):
        with patch.object(fb.completion, 'score_file', return_value=self.score) as scoring, \
             patch.object(fb.grading_records, 'read_records', return_value={'assignments': [self.assignment]}), \
             patch.object(fb.completion, 'lesson_decks', return_value=['template'] if decks is None else decks), \
             patch.object(fb.assessment_save, 'capture', side_effect=self.captures), \
             patch.object(fb.feedback, 'load_prompt', return_value=('Live policy', 'policy-url')):
            agenda = getattr(self, 'agenda', SimpleNamespace(lessons=lambda *args: self.found['lessons']))
            result = fb.evidence('signed', 'token', {}, agenda, self.scope, self.found)
            self.scoring_call = scoring.call_args
            return result

    def registered_sources(self, mime=google.PPTX):
        self.binding = {'source_folder_id': 'FictionalFolder', 'teacher_ids': ['template']}
        self.found['lessons'][0]['forms'][0]['teacher_source_binding'] = self.binding
        self.agenda = SimpleNamespace(lessons=lambda course, week: self.found['lessons'])
        meta = {'id': 'template', 'name': 'Exact teacher', 'mimeType': mime,
                'modifiedTime': 'lesson-time', 'version': '7', 'trashed': False,
                'parents': ['FictionalFolder']}
        self.sources = {'decks': [{'id': 'template', 'metadata': meta}],
                        'problem': None, 'binding': self.binding,
                        'folders': [{'id': 'FictionalFolder', 'deck_ids': ['template']}]}
        self.captures[1]['file'] = {'id': 'template', 'name': 'Exact teacher', 'mime_type': mime,
                                    'modified_at': 'lesson-time', 'version': '7'}

    def test_registered_native_and_pptx_teachers_need_no_direct_agenda_presentation_link(self):
        for mime in (fb.completion.SLIDES, google.PPTX):
            with self.subTest(mime=mime):
                self.registered_sources(mime)
                with patch.object(fb.work, 'form_teacher_sources', return_value=self.binding), \
                        patch.object(fb.completion, 'lesson_sources', return_value=self.sources) as sources:
                    result = self.read(decks=[])
                self.assertEqual(result['reference']['file']['id'], 'template')
                self.assertEqual(sources.call_count, 2)
                self.assertTrue(all(call.kwargs['binding'] == self.binding for call in sources.call_args_list))

    def test_registered_source_change_or_capture_disagreement_holds(self):
        for issue in ('membership', 'deleted', 'version', 'id', 'mime', 'name', 'resolution'):
            with self.subTest(issue=issue):
                self.registered_sources()
                after = deepcopy(self.sources)
                if issue == 'membership': after['folders'][0]['deck_ids'] = []
                elif issue == 'deleted': after['problem'] = 'Teacher is unavailable'
                elif issue == 'resolution':
                    self.found['lessons'][0]['forms'][0]['grade_readiness'] = {
                        'source_resolution': {**self.sources, 'problem': 'Stale resolution'}}
                else: self.captures[1]['file'][{'mime': 'mime_type'}.get(issue, issue)] = 'changed'
                with patch.object(fb.work, 'form_teacher_sources', return_value=self.binding), \
                        patch.object(fb.completion, 'lesson_sources', side_effect=[self.sources, after]):
                    with self.assertRaises(fb.Held): self.read(decks=[])
                self.found['lessons'][0]['forms'][0].pop('grade_readiness', None)

    def test_registered_declaration_removal_or_foreign_form_never_falls_back(self):
        for declaration in (None, {'source_folder_id': 'OtherFolder', 'teacher_ids': ['template']}):
            self.registered_sources()
            with self.subTest(declaration=declaration), \
                    patch.object(fb.work, 'form_teacher_sources', return_value=declaration), \
                    patch.object(fb.completion, 'lesson_sources') as sources:
                with self.assertRaisesRegex(fb.Held, 'declaration changed'): self.read()
                sources.assert_not_called()

    def test_registered_membership_does_not_override_registry_or_missing_source_proof(self):
        for issue in ('registry', 'foreign_template', 'problem', 'unverified_binding'):
            with self.subTest(issue=issue):
                self.registered_sources()
                if issue == 'registry': self.assignment['source_deck_id'] = 'other'
                elif issue == 'foreign_template': self.sources['decks'][0]['id'] = 'other'
                elif issue == 'problem': self.sources['problem'] = 'Cannot verify source folder'
                elif issue == 'unverified_binding': self.sources.pop('binding')
                with patch.object(fb.work, 'form_teacher_sources', return_value=self.binding), \
                        patch.object(fb.completion, 'lesson_sources', return_value=self.sources):
                    with self.assertRaises(fb.Held): self.read(decks=[])
                self.assignment['source_deck_id'] = 'template'

    def test_unconfigured_form_cannot_borrow_readiness_sources_without_direct_link(self):
        self.found['lessons'][0]['forms'][0]['grade_readiness'] = {
            'source_resolution': {'decks': [{'id': 'template'}]}}
        with patch.object(fb.completion, 'lesson_sources', side_effect=AssertionError('unconfigured source expansion')):
            with self.assertRaisesRegex(fb.Held, 'assessment-register'): self.read(decks=[])

    def test_unconfigured_feedback_cannot_borrow_another_forms_row_sources(self):
        self.found['lessons'][0]['forms'].append({'form_id': 'SiblingForm', 'students': []})
        with self.assertRaisesRegex(fb.Held, 'exact teacher-source ownership'):
            self.read()

    def test_bound_feedback_does_not_inherit_sibling_form_source_failure(self):
        self.registered_sources()
        self.found['lessons'][0]['forms'].append({'form_id': 'SiblingForm', 'students': [],
                                               'problem': 'Sibling source unavailable'})
        with patch.object(fb.work, 'form_teacher_sources', return_value=self.binding), \
                patch.object(fb.completion, 'lesson_sources', return_value=self.sources):
            self.assertEqual(self.read(decks=[])['reference']['file']['id'], 'template')

    def reference(self, text, drafts):
        self.captures[1]['text'] = text
        self.score['items'] = [{'category': 'DRAFT', 'id': qid, 'status': 'done', 'answer': qid, 'slide': n}
                               for n, qid in enumerate(drafts, 2)]

    def test_unit_numbered_singular_heading_pairs_by_order(self):
        # A&P teacher references number the aspect within the unit and use the
        # singular heading; the deck's own revision box is still CA1.
        self.reference('TEACHER REFERENCE — do not project\nCRITICAL ASPECT\n'
                       '3.  Structure fits function — why a tissue is built the way it is\n'
                       'WEEK QUESTION\nIf you could see the cells, could you predict the job?\n'
                       '[[MARKER-INVENTORY:A07]]', ['CA1-MASTERY'])
        aspects = self.read()['aspects']
        self.assertEqual([(a['question_id'], a['name']) for a in aspects],
                         [('CA1-MASTERY', 'Structure fits function — why a tissue is built the way it is')])

    def test_numbers_that_match_revision_boxes_pair_by_number(self):
        self.reference('CRITICAL ASPECTS\n1. Storing energy\n2. Releasing energy\n[[MARKER-INVENTORY:C09]]',
                       ['CA2-MASTERY', 'CA1-MASTERY'])
        aspects = self.read()['aspects']
        self.assertEqual([(a['question_id'], a['name'], a['answer']) for a in aspects],
                         [('CA1-MASTERY', 'Storing energy', 'CA1-MASTERY'),
                          ('CA2-MASTERY', 'Releasing energy', 'CA2-MASTERY')])

    def test_aspects_without_one_revision_box_each_still_refuse(self):
        self.reference('CRITICAL ASPECT\n3. Structure fits function\n4. Layers protect\n[[MARKER-INVENTORY:A07]]',
                       ['CA1-MASTERY'])
        with self.assertRaisesRegex(fb.Held, 'revised-answer field'):
            self.read()

    def test_partly_matching_numbers_refuse_instead_of_guessing(self):
        self.reference('CRITICAL ASPECTS\n1. Storing energy\n2. Releasing energy\n[[MARKER-INVENTORY:C09]]',
                       ['CA1-MASTERY', 'CA3-MASTERY'])
        with self.assertRaisesRegex(fb.Held, 'revised-answer field'):
            self.read()

    def test_missing_list_still_refuses(self):
        self.reference('TEACHER REFERENCE\nWEEK QUESTION\nWhy?', ['CA1-MASTERY'])
        with self.assertRaisesRegex(fb.Held, 'numbered critical-aspects list'):
            self.read()

    def question_reference(self, course='forensics'):
        self.scope['class'] = course
        self.assignment['class_key'] = course
        titles = ['How does this observation support your inference?',
                  'Which alternative explanation fits the evidence?',
                  'What additional evidence would distinguish the explanations?']
        self.captures[1]['text'] = '\n'.join(titles)
        self.score['items'] = [{'category': 'DRAFT', 'id': 'Q-MASTERY-%d' % n,
                               'question': title, 'at': n, 'slide': n + 1,
                               'status': 'done', 'answer': 'My revised explanation %d.' % n}
                              for n, title in enumerate(titles, 2)]

    def test_question_identified_courses_keep_exact_teacher_labels_ids_and_answers(self):
        for course in ('anatomy', 'forensics'):
            with self.subTest(course=course):
                self.question_reference(course)
                result = self.read()
                for aspect, item in zip(result['aspects'], self.score['items']):
                    self.assertEqual(aspect, {'question_id': item['id'], 'name': item['question'],
                                             'answer': item['answer'], 'available': True, 'slide': item['slide']})
                response = report()
                response['aspects'] = [{'question_id': a['question_id'], 'name': a['name'],
                                       'current_level': 'Developing', 'quote': a['answer'],
                                       'explanation': 'Explain how the evidence supports this inference.'}
                                      for a in result['aspects']]
                response.pop('teacher_dimensions')
                fb.validate_report(response, result)
                response['aspects'][0]['name'] = 'Invented critical aspect'
                with self.assertRaises(fb.Held): fb.validate_report(response, result)

    def test_question_identified_blank_answer_remains_unavailable(self):
        self.question_reference()
        self.score['items'][0].update(status='blank', answer='')
        self.assertFalse(self.read()['aspects'][0]['available'])

    def test_question_identified_fallback_never_bypasses_biology_or_assignment_registry(self):
        self.question_reference('biology')
        with self.assertRaisesRegex(fb.Held, 'numbered critical-aspects list'): self.read()
        self.question_reference()
        self.assignment['source_deck_id'] = 'other-template'
        with self.assertRaisesRegex(fb.Held, 'assessment-register'): self.read()

    def test_question_identified_fallback_rejects_ambiguous_or_unverified_bindings(self):
        for issue in ('no_rows', 'too_many', 'duplicate_id', 'duplicate_title', 'duplicate_slide',
                      'missing_title', 'generic_title', 'numeric_title', 'foreign_title', 'missing_slide',
                      'mixed_ids', 'uncompared', 'missing_template'):
            with self.subTest(issue=issue):
                self.question_reference()
                rows = self.score['items']
                if issue == 'no_rows': self.score['items'] = []
                elif issue == 'too_many': rows.extend([deepcopy(rows[0]), deepcopy(rows[1])])
                elif issue == 'duplicate_id': rows[1]['id'] = rows[0]['id']
                elif issue == 'duplicate_title': rows[1]['question'] = rows[0]['question']
                elif issue == 'duplicate_slide': rows[1]['at'] = rows[0]['at']
                elif issue == 'missing_title': rows[0].pop('question')
                elif issue in ('generic_title', 'numeric_title'):
                    rows[0]['question'] = 'Question 2' if issue == 'generic_title' else '123'
                    self.captures[1]['text'] += '\n' + rows[0]['question']
                elif issue == 'foreign_title': rows[0]['question'] = 'A title absent from the teacher source'
                elif issue == 'missing_slide': rows[0].pop('at')
                elif issue == 'mixed_ids': rows[0]['id'] = 'CA1-MASTERY'
                elif issue == 'uncompared': self.score['compared'] = False
                elif issue == 'missing_template': self.score['template_id'] = ''
                with self.assertRaises(fb.Held): self.read()
                self.score['compared'] = True
                self.score['template_id'] = 'template'

    def test_question_fallback_does_not_replace_partial_critical_aspect_mapping(self):
        self.question_reference()
        self.captures[1]['text'] += '\nCRITICAL ASPECTS\n1. Evidence supports an inference\n[[MARKER-INVENTORY:F01]]'
        with self.assertRaisesRegex(fb.Held, 'revised-answer field'): self.read()

    def test_exact_agenda_deck_registry_and_live_prompt_bind_feedback(self):
        result = self.read()
        self.assertEqual(result['aspects'][0]['answer'], 'Carbon moves.')
        self.assertEqual(result['prompt'], 'Live policy')
        self.assertEqual(result['person']['name'], 'Ana R.')
        self.assertEqual(result['archive_identity']['school_email'], self.row['email'])
        self.assertEqual(result['archive_identity']['matched_by'], 'email')
        self.assertEqual(result['archive_identity']['source'], 'form')
        self.assertNotIn('school_email', result['person'])
        self.assertEqual(result['submitted_at'], self.row['submitted_at'])
        self.assertEqual(result['submission_receipt'], {
            'source_file_id': 'file', 'source_form_id': 'form',
            'source_response_id': 'FictionalResponse12345', 'assignment_form_id': 'form',
            'submitted_at': '2026-09-30T15:00:00Z'})

    def test_original_exact_file_receipt_survives_verified_assignment_form_routing(self):
        self.row['files'][0].update(source_form_id='FictionalOriginalForm', assignment_form_id='form')
        result = self.read()
        self.assertEqual(result['submission_receipt']['source_form_id'], 'FictionalOriginalForm')
        self.assertEqual(result['submission_receipt']['assignment_form_id'], 'form')
        self.assertEqual(result['submitted_at'], '2026-09-30T15:00:00Z')

    def test_unknown_incomplete_or_disagreeing_file_receipt_is_held_before_source_capture(self):
        original = deepcopy(self.row)
        changes = [lambda row: row['files'][0].pop('source_response_id'),
                   lambda row: row['files'][0].pop('source_form_id'),
                   lambda row: row['files'][0].pop('source_submitted_at'),
                   lambda row: row['files'][0].update(source_submitted_at='2026-09-29T15:00:00Z'),
                   lambda row: row['files'][0].update(source_form_id='UnknownForm'),
                   lambda row: row['files'][0].update(assignment_form_id='DifferentAssignmentForm'),
                   lambda row: row.update(submitted_at='2026-09-30T15:00:00'),
                   lambda row: row['files'][0].update(source_submitted_at='not-a-date')]
        for change in changes:
            self.row.clear(); self.row.update(deepcopy(original)); change(self.row)
            with self.subTest(change=change), patch.object(fb.assessment_save, 'capture') as capture:
                with self.assertRaises(fb.Held):
                    fb.evidence('signed', 'token', {}, {}, self.scope, self.found)
                capture.assert_not_called()

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
