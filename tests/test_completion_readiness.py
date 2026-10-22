"""Completion coding is a strict, source-bound gate, separate from VT design."""
from copy import deepcopy
from unittest.mock import patch
import unittest

from hub_v2 import audit, completion, google
from test_hub_v2 import pptx, fixture, handout

FORM = 'FICTIONALFORMID1234567890abc'
TEMPLATE = 'FICTIONALDECK1234567890'
STUDENT = 'STUDENTDECK123'
HEADER = ('[[MARKER-INVENTORY:F06]]\n'
          'NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0\n'
          'NOTES_IDS=Q-MASTERY DRAFT_IDS=Q-MASTERY OPTIONAL_IDS=-')


def source(answer='', *, header=HEADER, codes=True):
    return completion.read_deck(pptx([
        [('inventory', header, (0, 0, 900, 80))],
        [('question', 'Explain the observation.', (0, 0, 900, 40)),
         ('first', 'Your first answer', (0, 60, 900, 30)),
         ('answer', answer, (0, 100, 900, 100)),
         ('revised', 'Your revised answer', (0, 240, 900, 30)),
         ('revision', '', (0, 280, 900, 100))] +
        ([('notes', '[[NOTES:F06:Q-MASTERY]]', (0, 80, 10, 10)),
          ('draft', '[[DRAFT:F06:Q-MASTERY]]', (0, 260, 10, 10))] if codes else [])
    ]))


def work_result():
    return {'lessons': [{'topic': 'Scene photography', 'forms': [{'form_id': FORM,
        'label': 'Photography — Turn in', 'students': [{'files': [
            {'id': STUDENT, 'name': 'Copy of Scene photography - Fictional student.pptx', 'mime': google.PPTX}]}]}]}]}


class ReadinessTests(unittest.TestCase):
    def test_multiline_counts_and_zero_dash_sentinel_are_valid_without_teacher_contract(self):
        deck = source()
        ready = completion.readiness(deck)
        self.assertEqual(ready['status'], 'READY')
        self.assertEqual(ready['declared_counts'], {'NOTES': 1, 'DRAFT': 1, 'OPTIONAL': 0, 'BANK': 0})
        self.assertEqual(ready['issues'], [])
        self.assertEqual(completion.inventory(deck)[2], 'header')
        self.assertEqual(completion.inventory(deck)[1]['OPTIONAL'], [])

    def test_inventory_split_across_text_boxes_and_multiline_id_lists_is_valid(self):
        deck = source()
        deck['slides'][0]['units'] = [
            {'key': 'h', 'text': '[[MARKER-INVENTORY:F06]]', 'box': (0, 0, 900, 30)},
            {'key': 'n', 'text': 'VERSION=example\nNOTES=1\nDRAFT=1\nOPTIONAL=0\nBANK_TERMS=0', 'box': (0, 40, 900, 90)},
            {'key': 'l', 'text': 'NOTES_IDS=\nQ-MASTERY\nDRAFT_IDS=\nQ-MASTERY\nOPTIONAL_IDS=-', 'box': (0, 150, 900, 200)}]
        self.assertEqual(completion.readiness(deck)['status'], 'READY')

    def test_adjacent_lesson_prose_is_not_part_of_last_empty_id_list(self):
        deck = source()
        deck['slides'][0]['units'].append({'key':'prompt', 'text':'Explain how the evidence supports a conclusion.', 'box':(0,120,900,200)})
        self.assertEqual(completion.readiness(deck)['status'], 'READY')
        self.assertEqual(completion.inventory(deck)[1]['OPTIONAL'], [])
        deck['slides'].insert(0, {'number':1,'units':[]})
        for i, slide in enumerate(deck['slides'],1):slide['number']=i
        held=completion.readiness(deck)
        self.assertEqual(held['issues'], ['marker_inventory_not_on_first_slide'])
        self.assertEqual(held['missing_ids'], [])
        self.assertIn('slide 2, rather than slide 1', held['problem'])

    def test_missing_or_later_inventory_is_not_reconstructed_from_markers(self):
        for deck in (source(header='Lesson title'), source()):
            if deck['slides'][0]['units'][0]['text'].startswith('[[MARKER'):
                deck['slides'].insert(0, {'number': 1, 'units': []})
                for i, slide in enumerate(deck['slides'], 1):
                    slide['number'] = i
            ready = completion.readiness(deck)
            self.assertEqual(ready['status'], 'NOT_READY')
            self.assertTrue(ready['counts'])  # diagnostic markers never become approval

    def test_missing_numeric_count_is_held_even_when_census_matches(self):
        ready = completion.readiness(source(header=HEADER.replace('BANK_TERMS=0', '')))
        self.assertEqual(ready['issues'][0], 'marker_inventory_counts_unparsed')
        self.assertEqual(ready['label'], 'Needs embedded codes')
        self.assertTrue(ready['problem'].startswith('Needs embedded codes.'))
        self.assertNotIn('marker_inventory_counts_unparsed', ready['problem'])
        self.assertIn('BANK_TERMS', ready['problem'])

    def test_missing_question_id_is_explicit_and_never_silent_zero(self):
        ready = completion.readiness(source(header=HEADER.replace('NOTES_IDS=Q-MASTERY', 'NOTES_IDS=Q-OTHER')))
        self.assertEqual(ready['status'], 'NOT_READY')
        self.assertEqual(ready['missing_ids'], ['NOTES Q-OTHER'])
        self.assertEqual(ready['extra_ids'], ['NOTES Q-MASTERY'])

    def test_duplicate_markers_and_false_empty_sentinel_are_held(self):
        deck = source()
        deck['slides'][1]['units'].append(deepcopy(deck['slides'][1]['units'][-2]))
        self.assertIn('duplicate_feedback_marker', completion.readiness(deck)['issues'])
        invalid = source(header=HEADER.replace('OPTIONAL=0', 'OPTIONAL=1'))
        self.assertEqual(completion.readiness(invalid)['status'], 'NOT_READY')
        self.assertEqual(completion.inventory(invalid)[1]['OPTIONAL'], ['-'])

    def test_valid_bank_count_does_not_require_unrelated_curriculum_headings(self):
        deck = source(header=HEADER.replace('BANK_TERMS=0', 'BANK_TERMS=1'))
        deck['slides'][1]['units'].append({'key': 'bank', 'text': '[[BANK:F06:Observation]]', 'box': (0, 400, 10, 10)})
        self.assertEqual(completion.readiness(deck)['status'], 'READY')


def single_assignment_agenda():
    """This scoring fixture models one assignment, not the shared-row routing fixture."""
    result = fixture()
    lessons = result.lessons('forensics', 'Wk 04')
    for lesson in lessons:
        lesson['forms'] = [f for f in lesson['forms'] if f.get('form_id') == FORM]
    result.lessons = lambda cls, week: deepcopy(lessons)
    return result


class ScoringGateTests(unittest.TestCase):
    def setUp(self):
        completion._results.clear(); completion._templates.clear()

    def score(self, student, teacher=None, source_version='1', strict_student=True):
        teacher = teacher or source()
        metadata = {'id': TEMPLATE, 'name': 'Scene photography', 'mimeType': google.PPTX,
                    'modifiedTime': 'same-modified-time', 'version': 'teacher-1'}
        with patch.object(completion, 'student_coding_required', return_value=strict_student), \
             patch.object(completion, '_template', return_value=(metadata, teacher)), \
             patch.object(google, 'drive_file', return_value={'id': STUDENT, 'name': 'Student copy',
                 'mimeType': google.PPTX, 'modifiedTime': 'same-modified-time', 'version': source_version}), \
             patch.object(completion, '_read', return_value=student):
            return completion.score_file('read', single_assignment_agenda(), 'forensics', 'Wk 04', FORM, STUDENT, work_result())

    def test_unmarked_student_copy_stops_before_cached_zero_or_assessment_and_preserves_history(self):
        legacy = {(STUDENT, 'same-modified-time'): {'template': (TEMPLATE, 'same-modified-time'),
                    'result': {'notes_score': 0, 'revision_score': 0}}}
        completion._results.update(deepcopy(legacy))
        with patch.object(completion, 'assess') as assess:
            result = self.score(source(header='Distributed before coding', codes=False))
        self.assertEqual(result['code'], 'deck_not_ready')
        self.assertEqual(result['label'], 'Needs embedded codes')
        self.assertNotIn('notes_score', result)
        self.assertNotIn('revision_score', result)
        self.assertIn('Forensics · Wk 04 · Photography — Turn in', result['problem'])
        self.assertIn('student copy', result['problem'])
        self.assertIn('preserving its answers', result['action'])
        self.assertIn('rerun grading', result['action'])
        assess.assert_not_called()
        self.assertEqual(completion._results, legacy)

    def test_valid_student_still_holds_malformed_handed_out_inventory_before_assess(self):
        teacher = source(header=HEADER.replace('NOTES=1', 'NOTES=2'))
        with patch.object(completion, 'assess') as assess:
            result = self.score(source(), teacher)
        self.assertEqual(result['code'], 'deck_not_ready')
        self.assertIn('handed-out deck', result['problem'])
        self.assertEqual(completion._results, {})
        assess.assert_not_called()

    def test_current_source_version_prevents_cache_reuse_despite_identical_modified_time(self):
        first = self.score(source('The scene must be recorded.'), source_version='1')
        second = self.score(source(), source_version='2')
        self.assertEqual(first['notes_score'], 10)
        self.assertEqual(second['notes_score'], 0)
        self.assertEqual(len(completion._results), 2)

    def test_ready_cached_score_cannot_bypass_new_copy_marker_read(self):
        self.assertIsNone(self.score(source('The scene must be recorded.'))['problem'])
        with patch.object(completion, 'assess') as assess:
            blocked = self.score(source(header='Codes no longer readable', codes=False))
        self.assertEqual(blocked['code'], 'deck_not_ready')
        self.assertNotIn('notes_score', blocked)
        assess.assert_not_called()

    def test_template_cache_uses_revision_even_when_modified_time_is_unchanged(self):
        with patch.object(google, 'drive_file', side_effect=[
                {'modifiedTime': 'same', 'version': '1'}, {'modifiedTime': 'same', 'version': '2'}]), \
             patch.object(completion, '_read', side_effect=[source(), source(header='Unmarked')]) as read:
            first = completion._template('read', TEMPLATE)[1]
            second = completion._template('read', TEMPLATE)[1]
        self.assertEqual(read.call_count, 2)
        self.assertEqual(completion.readiness(first)['status'], 'READY')
        self.assertEqual(completion.readiness(second)['status'], 'NOT_READY')

    def test_existing_uploaded_pptx_original_is_found_in_actual_folder_without_conversion(self):
        found = work_result()
        uploaded = found['lessons'][0]['forms'][0]['students'][0]['files'][0]
        uploaded['name'] = 'Copy of Scene photography - Fictional student.pptx'
        original_id = 'FICTIONALPPTXORIGINAL123456'
        def template(token, ident):
            return ({'id': ident, 'name': 'Scene photography.pptx', 'mimeType': google.PPTX,
                     'modifiedTime': 't', 'version': '1', 'parents':['FICTIONALLESSONFOLDER']},
                    source() if ident == original_id else completion.read_deck(handout()))
        with patch.object(completion, '_template', side_effect=template), \
             patch.object(google, 'drive_file', return_value={'mimeType': google.PPTX, 'modifiedTime': 't', 'version': '1'}), \
             patch.object(completion, '_read', return_value=source('The scene must be recorded.')), \
             patch.object(completion, '_siblings', return_value=[{'id':original_id,'name':'Scene photography.pptx'}]), \
             patch.object(google, 'files_named') as searched:
            scored = completion.score_file('read', single_assignment_agenda(), 'forensics', 'Wk 04', FORM, STUDENT, found)
        self.assertIsNone(scored['problem'])
        self.assertEqual(scored['template_id'], original_id)
        self.assertEqual(scored['notes_score'], 10)
        searched.assert_not_called()

    def test_pptx_sibling_is_supported_but_same_title_different_cycle_cannot_score(self):
        completion._folders.clear()
        original = {'id': 'FICTIONALPPTXORIGINAL123456', 'name': 'Scene photography.pptx', 'mimeType': google.PPTX}
        with patch.object(google, 'folder_children', return_value=[original, {'id': 'DOC', 'mimeType': 'application/vnd.google-apps.document'}]):
            self.assertEqual(completion._siblings('read', 'FICTIONALLESSONFOLDER'), [original])
        self.assertIsNone(completion.pick_template(source(), [completion.read_deck(handout())]))


class AuditReadabilityTests(unittest.TestCase):
    def test_uploaded_pptx_readiness_needs_no_teacher_reference_or_prompt2(self):
        raw = pptx([[('h', HEADER, (0, 0, 900, 60))],
                    [('n', '[[NOTES:F06:Q-MASTERY]]', (0, 0, 10, 10)),
                     ('d', '[[DRAFT:F06:Q-MASTERY]]', (0, 20, 10, 10))]])
        with patch.object(google, 'drive_file', return_value={'id': TEMPLATE, 'mimeType': google.PPTX,
                         'name': 'Fictional lesson.pptx', 'version': '2'}), \
             patch.object(google, 'deck_bytes', return_value=raw) as read:
            result = audit._release_deck('read', TEMPLATE)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['workflow'], 'completion')
        self.assertFalse(result['student_copies_checked'])
        read.assert_called_once_with('read', TEMPLATE, google.PPTX)

    def test_metadata_404_is_not_reported_as_ppt_conversion_or_parser_failure(self):
        with patch.object(google, 'drive_file', side_effect=google.GoogleError('Not found or not shared.', 404)), \
             patch.object(google, 'deck_bytes') as read:
            result = audit._release_deck('read', TEMPLATE)
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['readability']['stage'], 'metadata')
        self.assertEqual(result['readability']['http_status'], 404)
        self.assertEqual(result['label'], 'Cannot read lesson')
        self.assertNotIn('Needs embedded codes', result['problem'])
        self.assertIn('before file contents', result['problem'])
        self.assertNotIn('convert', result['problem'])
        read.assert_not_called()

    def test_actual_invalid_pptx_is_a_parse_failure(self):
        with patch.object(google, 'drive_file', return_value={'mimeType': google.PPTX}), \
             patch.object(google, 'deck_bytes', return_value=b'not a zip'):
            result = audit._release_deck('read', TEMPLATE)
        self.assertEqual(result['readability']['stage'], 'parse')
        self.assertEqual(result['label'], 'Cannot read lesson')
        self.assertEqual(result['readability']['mime'], google.PPTX)


class AssignmentDiagnosticContextTests(unittest.TestCase):
    def check(self, label):
        from types import SimpleNamespace
        from hub_v2 import completion_holds, work
        lesson = {'topic': 'Searching and Photographing', 'materials': [], 'forms': [
            {'form_id': 'FictionalSiblingForm001', 'label': 'Searching — Turn in'},
            {'form_id': FORM, 'label': label}]}
        agenda = SimpleNamespace(classes={'forensics': {'name': 'Forensics'}},
                                 lessons=lambda cls, week: [deepcopy(lesson)])
        with patch.object(work, 'assignment_policy', return_value={}), \
             patch.object(work, 'form_teacher_sources', return_value={'teacher_ids': [TEMPLATE]}), \
             patch.object(completion, 'form_source_ownership', return_value=None), \
             patch.object(completion_holds, 'check', return_value={'held': False}), \
             patch.object(completion, 'scoped_lesson_sources', return_value={
                 'problem': 'The native teacher source has collapsed geometry.', 'decks': []}):
            result = completion.assignment_readiness('read', {}, agenda, 'forensics', 'Wk 04', FORM)
        self.assertEqual(lesson['topic'], 'Searching and Photographing')
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['agenda_topic'], lesson['topic'])
        self.assertEqual(result['form_id'], FORM)
        return result

    def test_exact_selected_form_names_shared_week_diagnostic_without_rewriting_topic(self):
        result = self.check('📤 Sketching the Scene — Turn in')
        self.assertEqual(result['context'], 'Forensics · Wk 04 · Sketching the Scene')
        self.assertTrue(result['problem'].startswith(result['context']))
        self.assertNotIn('Searching', result['problem'])

    def test_generic_or_raw_form_label_cannot_inherit_sibling_lesson_name(self):
        for label in ('', 'Turn in', 'https://docs.google.com/forms/d/' + FORM + '/edit'):
            with self.subTest(label=label):
                result = self.check(label)
                self.assertEqual(result['context'], 'Forensics · Wk 04 · Form ' + FORM)


if __name__ == '__main__':
    unittest.main()
