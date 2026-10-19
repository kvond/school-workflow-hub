"""Lessons → Notes "Summarize this lesson": fictional students, isolated CAS store."""
import io
import json
import unittest
from unittest.mock import patch

from hub_v2 import app, google, lesson_summary as ls, native_feedback as fb
from school_workflow_hub.core.cloud_state import Conflict
from tests.test_assessment_provider import OPENAI_CONFIG
from tests.test_cloud_state import MemoryCAS

FORM = 'FictionalForm123456789012345'
SCOPE = {'class': 'biology', 'form': FORM}
CONFIG = {**OPENAI_CONFIG, 'HUB_STATE_PROBE_ORIGINS': 'https://hub.example'}
REAL_CALL = ls._call
STUDENTS = [('Ana', 'Rivera', 'FictionalStudentAna1234567'), ('Ben', 'Okafor', 'FictionalStudentBen1234567'),
            ('Cy', 'Lindqvist', 'FictionalStudentCy12345678')]


def found():
    rows = [{'first': first, 'last': last, 'section': 'A4 Biology', 'matched': True,
             'files': [{'id': file_id}]} for first, last, file_id in STUDENTS]
    return {'lessons': [{'forms': [{'form_id': FORM, 'label': 'Fictional carbon lesson', 'students': rows}]}]}


def reply(misunderstandings=None, questions=None, epistemic=''):
    return json.dumps({
        'misunderstandings': ['Several notes conflate the carbon in sugar with carbon from the soil.']
        if misunderstandings is None else misunderstandings,
        'questions': questions or ['Where does the mass of a tree come from?',
                                   'What would change if the air had no carbon dioxide?',
                                   'How could we test whether soil mass drops as a plant grows?'],
        'epistemic_reframing': epistemic})


class LessonSummaryTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        storage = patch.object(ls, 'configured_store', lambda c: self.port)
        storage.start(); self.addCleanup(storage.stop)
        model = patch.object(ls, '_call', return_value=(reply(), {'input_tokens': 90, 'output_tokens': 40}))
        self.call = model.start(); self.addCleanup(model.stop)

    def draft(self, index, notes, saved=True):
        first, last, file_id = STUDENTS[index]
        ident = 'feedbackdraft_' + ('%x' % (index + 10)) * 64
        ident = ident[:len('feedbackdraft_') + 64]
        self.port.commit({
            fb._ref({'class': 'biology', 'form': FORM, 'file': file_id}): ({'id': ident}, None),
            ident: ({'schema': fb.VERSION, 'person': {'name': first + ' ' + last[0] + '.'},
                     'report': {'teacher_notes': notes}, 'saved_at': '2026-10-18T12:00:00Z' if saved else None}, None)})

    def generate(self, data=None, config=CONFIG):
        return ls.generate(config, dict(SCOPE), data or found(), actor='teacher@school.example')

    def test_one_call_with_names_removed_then_unchanged_lesson_never_rebills(self):
        self.draft(0, ['Ana thinks plants take their mass from the soil.'])
        self.draft(1, ['Says soil is the carbon source; ask Ben Okafor about air.', 'Unsure what "fixed" means.'])
        first = self.generate()
        self.assertFalse(first['cached'])
        payload = self.call.call_args.args[1]
        sent = json.dumps(payload)
        for name in ('Ana', 'Ben', 'Okafor', 'Rivera'):
            self.assertNotIn(name, sent)
        self.assertEqual(len(payload['teacher_notes']), 3)
        self.assertEqual(payload['reviewed_feedback_count'], 2)
        self.assertIn('What students conflated or misunderstood', first['summary'])
        self.assertIn('1. Where does the mass of a tree come from?', first['summary'])
        self.assertNotIn('Epistemic reframing', first['summary'])
        self.assertEqual(first['coverage'], {'reviewed_feedback': 2, 'teacher_notes': 3, 'students_on_form': 3})
        again = self.generate()
        self.assertTrue(again['cached'])
        self.assertEqual(again['summary'], first['summary'])
        self.assertEqual(self.call.call_count, 1)

    def test_teacher_edits_and_free_notes_survive_reclick_and_new_summary(self):
        self.draft(0, ['Mixes up photosynthesis and respiration.'])
        first = self.generate()
        edited = ls.save(CONFIG, dict(SCOPE), 'My edited summary', 'My own reminders', first['revision'],
                         actor='teacher@school.example')
        self.assertEqual(self.generate()['summary'], 'My edited summary')
        self.assertEqual(self.call.call_count, 1)
        self.draft(1, ['Thinks oxygen comes from the sugar.'])
        self.call.return_value = (reply(epistemic='Ask what observation would show where the mass came from.'), {})
        newer = self.generate()
        self.assertEqual(self.call.call_count, 2)
        self.assertEqual(newer['notes'], 'My own reminders')
        self.assertIn('Epistemic reframing: Ask what observation', newer['summary'])
        histories = [d.value['summary'] for i, d in self.port.rows.items() if i.startswith('lessonsummaryhistory_')]
        self.assertIn(edited['summary'], histories)

    def test_only_saved_reviewed_feedback_counts_and_no_notes_makes_no_call(self):
        self.draft(0, ['Unreviewed concern.'], saved=False)
        self.draft(1, [])
        with self.assertRaisesRegex(ls.Held, 'Nothing was sent'):
            self.generate()
        self.assertEqual(self.call.call_count, 0)
        self.assertEqual(ls.read(CONFIG, dict(SCOPE))['summary'], '')

    def test_interrupted_call_is_held_and_never_automatically_rebilled(self):
        self.draft(0, ['Confuses mass and weight.'])
        self.call.side_effect = TimeoutError()
        for _ in range(3):
            with self.assertRaises(ls.Held):
                self.generate()
        self.assertEqual(self.call.call_count, 1)

    def test_concurrent_click_cannot_duplicate_the_call(self):
        self.draft(0, ['Confuses mass and weight.'])
        def call(*args, **kwargs):
            with self.assertRaisesRegex(ls.Held, 'No duplicate AI call'):
                self.generate()
            return reply(), {}
        self.call.side_effect = call
        self.generate()
        self.assertEqual(self.call.call_count, 1)

    def test_invalid_reply_is_stored_for_review_and_not_rebilled(self):
        self.draft(0, ['Confuses mass and weight.'])
        self.call.return_value = (json.dumps({'questions': ['Only one?']}), {})
        for _ in range(2):
            with self.assertRaisesRegex(ls.Held, 'not be billed again'):
                self.generate()
        self.assertEqual(self.call.call_count, 1)

    def test_names_in_the_reply_are_removed_before_saving(self):
        self.draft(0, ['Thinks soil is food.'])
        self.call.return_value = (reply(misunderstandings=['Ana Rivera and Cy think soil is food.']), {})
        summary = self.generate()['summary']
        for name in ('Ana', 'Rivera', 'Cy'):
            self.assertNotIn(name, summary)
        self.assertIn('a student', summary)

    def test_uses_the_same_school_provider_path_as_feedback_drafts(self):
        with patch.object(ls.assessment_provider, '_call', return_value=(reply(), {})) as provider:
            REAL_CALL(CONFIG, {'teacher_notes': ['x']})
        system, user = provider.call_args.args[1:3]
        self.assertEqual(system, ls.POLICY)
        self.assertEqual(json.loads(user), {'teacher_notes': ['x']})
        self.assertEqual(provider.call_args.kwargs['schema'], ls.OUTPUT_SCHEMA)
        self.assertIn('Never invent a misconception', ls.POLICY)
        self.assertIn('ONLY if the notes show', ls.POLICY)

    def test_unconfigured_provider_makes_no_call(self):
        self.draft(0, ['Confuses mass and weight.'])
        with self.assertRaisesRegex(ls.Held, 'not configured'):
            self.generate(config={'HUB_AI_PROVIDER': 'openai'})
        self.assertEqual(self.call.call_count, 0)

    def test_stale_edit_cannot_erase_newer_notes(self):
        first = ls.save(CONFIG, dict(SCOPE), '', 'First', '', actor='teacher@school.example')
        ls.save(CONFIG, dict(SCOPE), '', 'Newer', first['revision'], actor='teacher@school.example')
        with self.assertRaises(Conflict):
            ls.save(CONFIG, dict(SCOPE), '', 'Old tab', first['revision'], actor='teacher@school.example')
        self.assertEqual(ls.read(CONFIG, dict(SCOPE))['notes'], 'Newer')
        for bad in [('x' * 20001, ''), ('ok', 'invented')]:
            with self.assertRaises(ValueError):
                ls.save(CONFIG, dict(SCOPE), bad[0], '', bad[1], actor='teacher@school.example')
        with self.assertRaises(ValueError):
            ls.read(CONFIG, {'class': 'biology', 'form': 'bad form'})

    def request(self, path, method='GET', payload=None, query='', origin='https://hub.example'):
        raw = json.dumps(payload).encode() if payload is not None else b''
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': query,
               'HTTP_ORIGIN': origin, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        result = {}
        def respond(status, headers): result['status'] = status
        with patch.object(google, 'verify_teacher', return_value='teacher@school.example'), \
             patch.object(google, 'service_token', return_value='token') as token, \
             patch.object(app, 'load_agenda', return_value=object()), \
             patch.object(app, 'lesson_work', return_value=found()):
            result['body'] = json.loads(b''.join(app.application(env, respond, CONFIG)))
            result['token_reads'] = token.call_count
        return result

    def test_http_read_save_and_click_contract(self):
        read = self.request('/v2/api/lessons/summary', query='class=biology&form=' + FORM)
        self.assertEqual(read['status'], '200 OK')
        self.assertEqual(read['token_reads'], 0)
        self.assertEqual(self.request('/v2/api/lessons/summary/save', 'POST', {'class': 'biology', 'form': FORM,
            'summary': '', 'notes': 'x', 'revision': ''}, origin='https://other.example')['status'], '403 Forbidden')
        saved = self.request('/v2/api/lessons/summary/save', 'POST', {'class': 'biology', 'form': FORM,
            'summary': '', 'notes': 'Reminder', 'revision': ''})
        self.assertEqual(saved['status'], '200 OK')
        self.assertEqual(saved['token_reads'], 0)
        self.assertEqual(self.request('/v2/api/lessons/summary/save', 'POST', {'class': 'biology', 'form': FORM,
            'summary': '', 'notes': 'Stale', 'revision': ''})['status'], '409 Conflict')
        self.assertEqual(self.request('/v2/api/lessons/summary', 'POST',
            {'class': 'biology', 'week': 'Wk 04', 'form': FORM})['status'], '409 Conflict')  # no reviewed notes yet
        self.draft(0, ['Confuses mass and weight.'])
        made = self.request('/v2/api/lessons/summary', 'POST', {'class': 'biology', 'week': 'Wk 04', 'form': FORM})
        self.assertEqual(made['status'], '200 OK')
        self.assertEqual(made['body']['notes'], 'Reminder')
        moved = self.request('/v2/api/lessons/summary', 'POST', {'class': 'biology', 'week': 'Wk 05', 'form': FORM})
        self.assertTrue(moved['body']['cached'])
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.request('/v2/api/lessons/summary', 'POST',
            {'class': 'biology', 'week': 'Wk 04', 'form': FORM, 'extra': 1})['status'], '400 Bad Request')


if __name__ == '__main__':
    unittest.main()
