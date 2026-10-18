"""Exact Form ownership isolates multiple assignments without source writes."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import completion, google, review_context, work
from test_completion_readiness import source, STUDENT
from test_shared_intake_catalog import PRIMARY, ADDITIONAL, FORM_A, FORM_B, KEY_A, KEY_B, named_forms, form_url, url


def fixture():
    cells = {(3, 16): {'formattedValue': '_studentURL'},
             (3, 20): {'formattedValue': 'Turn it in'}, (3, 21): {'formattedValue': '_turnInURL'},
             (4, 16): {'formattedValue': url(PRIMARY)}, (4, 15): {'formattedValue': url(ADDITIONAL)},
             (4, 20): named_forms(), (4, 21): {'formattedValue': form_url(FORM_A)}}
    lesson = {'row': 4, 'topic': 'Two unrelated assignments',
              'forms': [{'form_id': FORM_A}, {'form_id': FORM_B}],
              'materials': [{'label': 'Student copy', 'url': url(PRIMARY)},
                            {'label': '2nd deck', 'url': url(ADDITIONAL)}]}
    agenda = SimpleNamespace(spreadsheet_id='FictionalAgenda',
        classes={'forensics': {'name': 'Forensics', 'cells': cells}}, lessons=lambda *args: [lesson])
    config = {'HUB_INTAKE_DECK_ASSIGNMENTS': {PRIMARY: KEY_A, ADDITIONAL: KEY_B},
              'HUB_ASSIGNMENT_FORM_IDS': {KEY_A: FORM_A, KEY_B: FORM_B}}
    return agenda, lesson, config


class FormSourceIsolationTests(unittest.TestCase):
    def setUp(self):
        completion._results.clear()
        self.addCleanup(completion._results.clear)

    def mapped_score(self, *, change=None, consumed_change=None, missing_ownership=False):
        agenda, lesson, config = fixture()
        ownership = completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', FORM_A)
        meta = {'id': PRIMARY, 'name': 'Own exact teacher', 'mimeType': google.PPTX,
                'trashed': False, 'modifiedTime': 'same-time', 'version': '1', 'parents': ['ExactParent']}
        resolution = {'problem': None, 'decks': [{'id': PRIMARY, 'metadata': meta}],
                      'folders': [], 'stale_links': []}
        after = deepcopy(resolution)
        if change: after['decks'][0]['metadata'].update(change)
        ready = {'status': 'READY', 'source_resolution': resolution, 'source_ownership': ownership}
        if missing_ownership: ready.pop('source_ownership')
        form = {'form_id': FORM_A, 'grade_readiness': ready, 'students': [
            {'files': [{'id': STUDENT, 'mime': google.PPTX}]}]}
        with patch.object(completion, 'scoped_lesson_sources', side_effect=[resolution, after]), \
                patch.object(completion, '_teacher_template', return_value=({**meta, **(consumed_change or {})}, source())) as teacher, \
                patch.object(completion, 'student_coding_required', return_value=False), \
                patch.object(google, 'drive_file', return_value={'modifiedTime': 'student-time', 'version': '1'}), \
                patch.object(completion, '_read', return_value=source('Observed evidence supports this inference.')), \
                patch.object(completion, '_siblings', side_effect=AssertionError('borrowed a sibling source')):
            result = completion.score_file('token', agenda, 'forensics', 'Wk 03', FORM_A, STUDENT,
                                           {'lessons': [{'forms': [form]}]})
        return result, teacher

    def test_mapped_scoring_reads_only_own_teacher_and_requires_ownership_even_with_ready_sources(self):
        result, teacher = self.mapped_score()
        self.assertIsNone(result['problem'])
        self.assertEqual(result['template_id'], PRIMARY)
        teacher.assert_called_once_with('token', PRIMARY)
        held, teacher = self.mapped_score(missing_ownership=True)
        self.assertIn('exact teacher-source ownership', held['problem'])
        teacher.assert_not_called()

    def test_mapped_scoring_bookends_source_metadata_and_consumed_teacher_bytes(self):
        for change in ({'version': '2'}, {'mimeType': completion.SLIDES}, {'modifiedTime': 'later'},
                       {'parents': ['OtherParent']}, {'trashed': True}):
            with self.subTest(change=change):
                held, _ = self.mapped_score(change=change)
                self.assertIsNotNone(held['problem'])
                self.assertNotIn('notes_score', held)
        held, _ = self.mapped_score(consumed_change={'version': 'different-export'})
        self.assertIn('Consumed teacher bytes', held['problem'])

    def test_mapped_source_metadata_requests_provider_version_without_another_export(self):
        with patch.object(completion, 'lesson_sources', return_value={}) as sources:
            completion.scoped_lesson_sources('token', {}, ownership={'source_ids': [PRIMARY]})
        self.assertIn('version', sources.call_args.kwargs['metadata_fields'])

    def test_exact_existing_assignment_maps_partition_sources_without_new_declarations(self):
        agenda, lesson, config = fixture()
        for form, teacher in ((FORM_A, PRIMARY), (FORM_B, ADDITIONAL)):
            ownership = completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', form)
            self.assertEqual(ownership['source_ids'], [teacher])
            scoped = completion.form_source_scope(agenda, 'forensics', 'Wk 03', form, ownership=ownership)
            self.assertEqual([m['url'] for m in scoped['materials']], [url(teacher)])
            self.assertEqual(completion.lesson_decks(agenda, 'forensics', 'Wk 03', form, ownership=ownership), [teacher])
        self.assertEqual(len(lesson['materials']), 2)

    def test_unconfigured_multiple_forms_refuse_before_any_source_or_score_read(self):
        agenda, lesson, _ = fixture()
        found = {'lessons': [{'forms': [{'form_id': FORM_A, 'students': [{'files': [
            {'id': STUDENT, 'mime': google.PPTX}]}]}]}]}
        with patch.object(completion, 'lesson_sources', side_effect=AssertionError('sibling sources read')), \
                patch.object(completion, '_teacher_template', side_effect=AssertionError('teacher read')), \
                patch.object(google, 'drive_file', side_effect=AssertionError('source read')):
            ready = completion.assignment_readiness('token', {}, agenda, 'forensics', 'Wk 03', FORM_A)
            scored = completion.score_file('token', agenda, 'forensics', 'Wk 03', FORM_A, STUDENT, found)
        self.assertEqual(ready['status'], 'NOT_READY')
        self.assertIn(FORM_A, ready['action'])
        self.assertIn('exact teacher-source ownership', scored['problem'])
        self.assertNotIn('notes_score', scored)
        self.assertEqual(completion.lesson_decks(agenda, 'forensics', 'Wk 03', FORM_A), [])
        with patch.object(completion, 'lesson_sources', side_effect=AssertionError('review read sibling source')):
            cached, limitations = review_context.cached_reviews('token', agenda, 'forensics', 'Wk 03', FORM_A, [])
        self.assertEqual(cached, {})
        self.assertIn('exact teacher-source ownership', limitations[0])

    def test_bound_assignment_ignores_unreadable_sibling_and_combined_teacher_set_stays_intact(self):
        agenda, lesson, _ = fixture()
        binding = {'form_id': FORM_A, 'teacher_ids': [PRIMARY], 'source_folder_id': 'FictionalFolder'}
        resolution = {'problem': None, 'decks': [{'id': PRIMARY}], 'binding': binding}
        with patch.object(work, 'assignment_policy', return_value={}), \
                patch.object(work, 'form_teacher_sources', return_value=binding), \
                patch('hub_v2.completion_holds.check', return_value={'held': False}), \
                patch.object(completion, 'lesson_sources', return_value=resolution) as read, \
                patch.object(completion, '_registered_teacher_bookend'), \
                patch.object(completion, '_teacher_template', return_value=({'version': '1'}, source())), \
                patch.object(completion, 'teacher_readiness', return_value={'status': 'READY'}), \
                patch.object(completion, 'student_coding_required', return_value=False):
            ready = completion.assignment_readiness('token', {}, agenda, 'forensics', 'Wk 03', FORM_A)
        self.assertEqual(ready['status'], 'READY')
        self.assertEqual(read.call_args.kwargs['binding']['teacher_ids'], [PRIMARY])
        combined = {**binding, 'teacher_ids': [PRIMARY, ADDITIONAL]}
        self.assertEqual(completion.lesson_decks(agenda, 'forensics', 'Wk 03', FORM_A, binding=combined),
                         [PRIMARY, ADDITIONAL])

    def test_stale_ownership_foreign_form_and_conflicting_explicit_bindings_refuse(self):
        agenda, lesson, config = fixture()
        ownership = completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', FORM_A)
        with self.assertRaises(completion.Unreadable):
            completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_B, ownership=ownership)
        for malformed in ([None], [{}], [PRIMARY, PRIMARY]):
            with self.subTest(malformed=malformed), self.assertRaises(completion.Unreadable):
                completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_A,
                                             ownership={**ownership, 'source_ids': malformed})
        lesson['materials'][0]['url'] = url('z' * 24)
        with self.assertRaises(completion.Unreadable):
            completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_A, ownership=ownership)
        agenda, lesson, config = fixture()
        with self.assertRaises(completion.Unreadable):
            completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', FORM_A,
                binding={'teacher_ids': [ADDITIONAL]})

    def test_single_form_explicit_foreign_mapping_holds_but_unrelated_mappings_do_not(self):
        agenda, lesson, config = fixture()
        lesson['forms'] = [{'form_id': FORM_A}]
        lesson['materials'] = [lesson['materials'][0]]
        config['HUB_ASSIGNMENT_FORM_IDS'][KEY_A] = FORM_B
        with self.assertRaisesRegex(completion.Unreadable, 'another Form'):
            completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', FORM_A)
        unrelated = {'HUB_INTAKE_DECK_ASSIGNMENTS': {'z' * 24: KEY_B},
                     'HUB_ASSIGNMENT_FORM_IDS': {KEY_B: FORM_B}}
        self.assertIsNone(completion.form_source_ownership(unrelated, agenda, 'forensics', 'Wk 03', FORM_A))
        self.assertIs(completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_A), lesson)

    def test_single_form_absent_form_target_preserves_direct_authority_but_invalid_targets_hold(self):
        agenda, lesson, config = fixture()
        lesson['forms'] = [{'form_id': FORM_A}]
        lesson['materials'] = [lesson['materials'][0]]
        config['HUB_ASSIGNMENT_FORM_IDS'].pop(KEY_A)
        self.assertIsNone(completion.form_source_ownership(config, agenda, 'forensics', 'Wk 03', FORM_A))
        for key, target in (('', None), ('biology:OtherClass', None), (KEY_A, ''), (KEY_A, ['invalid'])):
            broken = deepcopy(config)
            broken['HUB_INTAKE_DECK_ASSIGNMENTS'][PRIMARY] = key
            if target is not None: broken['HUB_ASSIGNMENT_FORM_IDS'][key] = target
            with self.subTest(key=key, target=target), self.assertRaises(completion.Unreadable):
                completion.form_source_ownership(broken, agenda, 'forensics', 'Wk 03', FORM_A)

    def test_single_form_sources_and_repeated_ambiguous_form_rows_keep_their_rules(self):
        agenda, lesson, _ = fixture()
        lesson['forms'] = [{'form_id': FORM_A}]
        self.assertIs(completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_A), lesson)
        self.assertEqual(completion.lesson_decks(agenda, 'forensics', 'Wk 03', FORM_A), [PRIMARY, ADDITIONAL])
        agenda.lessons = lambda *args: [lesson, deepcopy(lesson)]
        with self.assertRaises(completion.Unreadable):
            completion.form_source_scope(agenda, 'forensics', 'Wk 03', FORM_A)
