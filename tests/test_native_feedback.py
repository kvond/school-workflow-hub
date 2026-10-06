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

    def test_wrong_registry_source_or_agenda_deck_is_held_before_model(self):
        self.assignment['source_deck_id'] = 'another-deck'
        with self.assertRaises(fb.Held): self.read()
        self.assignment['source_deck_id'] = 'template'
        with self.assertRaises(fb.Held): self.read(decks=['another-deck'])

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
