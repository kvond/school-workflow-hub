"""Teacher policy: count found student codes; Photosynthesis stays strict."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import unittest

from hub_v2 import completion, morning
import test_completion_readiness as input_fixture
from test_completion_readiness import source, HEADER, STUDENT


class StudentFoundCodesTests(unittest.TestCase):
    def setUp(self):
        completion._results.clear()
        completion._templates.clear()
        self.port = input_fixture.ScoringGateTests()
        self.read_number = 0

    def score(self, deck, teacher=None):
        self.read_number += 1
        return self.port.score(deck, teacher, source_version=str(self.read_number), strict_student=False)

    def test_missing_inventory_counts_the_existing_meaningful_answer_only(self):
        deck = source('An incorrect but independently written explanation.', header='An older handout')
        result = self.score(deck)
        self.assertEqual((result['notes_score'], result['revision_score']), (10.0, 0.0))
        self.assertEqual(result['counts']['NOTES'], {'done': 1, 'required': 1})
        self.assertEqual(result['scoring_basis'], 'found_codes')
        self.assertIsNone(result['note'])
        self.assertIn('missing codes do not block', result['missing_code_warning'])

    def test_stale_unparsed_counts_and_wrong_inventory_ids_cannot_add_requirements(self):
        for header in (HEADER.replace('NOTES=1', 'NOTES=19'),
                       HEADER.replace('NOTES=1', ''),
                       HEADER.replace('NOTES_IDS=Q-MASTERY', 'NOTES_IDS=OLD-QUESTION')):
            with self.subTest(header=header):
                result = self.score(source('My own observation.', header=header))
                self.assertEqual((result['notes_score'], result['revision_score']), (10.0, 0.0))
                self.assertEqual(result['counts']['NOTES']['required'], 1)
                self.assertIsNone(result['problem'])

    def test_zero_actual_codes_are_na_not_lent_teacher_codes_or_a_missing_zero(self):
        result = self.score(source('An answer in an uncoded box.', header='An older handout', codes=False))
        self.assertEqual((result['notes_score'], result['revision_score']), (None, None))
        self.assertTrue(all(result['counts'][c]['required'] == 0 for c in completion.CATEGORIES))
        self.assertIn('N/A', result['missing_code_warning'])
        row = {'last': 'Avery', 'first': 'Ann', 'section': 'A2 Fictional', 'matched': True}
        form = {'students': [row], 'missing': [], 'late': [], 'excused': [], 'elsewhere': []}
        routed = morning.rows(form, [(row, result)], datetime(2026, 10, 10, tzinfo=timezone.utc))
        self.assertEqual((routed[0]['kind'], routed[0]['notes'], routed[0]['revision']), ('none', None, None))

    def test_source_teacher_readiness_and_photosynthesis_strict_copy_gate_remain(self):
        incomplete = source('My own observation.', header=HEADER.replace('NOTES=1', 'NOTES=19'))
        strict = self.port.score(incomplete)
        self.assertEqual(strict['code'], 'deck_not_ready')
        self.assertNotIn('notes_score', strict)
        malformed_teacher = source(header=HEADER.replace('DRAFT=1', 'DRAFT=19'))
        result = self.score(source('My own observation.'), malformed_teacher)
        self.assertEqual(result['code'], 'deck_not_ready')
        self.assertIn('handed-out deck', result['problem'])

    def test_conflicting_or_ambiguous_actual_markers_are_not_coverage_waivers(self):
        duplicate = source()
        duplicate['slides'][1]['units'].append(deepcopy(duplicate['slides'][1]['units'][-1]))
        conflicting = source()
        conflicting['slides'][0]['units'].append(deepcopy(conflicting['slides'][0]['units'][0]))
        conflicting['slides'][0]['units'][-1]['text'] = conflicting['slides'][0]['units'][-1]['text'].replace('F06', 'OTHER')
        wrong_cycle = source()
        wrong_cycle['slides'][1]['units'][-1]['text'] = '[[DRAFT:OTHER:Q-MASTERY]]'
        for deck in (duplicate, conflicting, wrong_cycle):
            with self.subTest(deck=deck):
                result = self.score(deck)
                self.assertEqual(result['code'], 'deck_not_ready')
                self.assertNotIn('notes_score', result)

    def test_later_inventory_warning_does_not_hide_a_retained_duplicate_response_code(self):
        deck = source('My own observation.')
        deck['slides'][0]['number'] = 4
        deck['slides'][1]['units'].append(deepcopy(deck['slides'][1]['units'][-1]))
        check = completion.student_readiness(deck, require_complete_codes=False)
        self.assertEqual(check['status'], 'NOT_READY')
        self.assertIn('marker_inventory_not_on_first_slide', check['issues'])
        self.assertIn('duplicate_feedback_marker', check['issues'])
        self.assertIn('DRAFT Q-MASTERY on slides 2', check['problem'])
        self.assertNotIn('rather than slide 1', check['problem'])
        self.assertIn("preserving the student's answers", check['action'])

    def test_missing_notes_marker_keeps_source_bound_revision_and_not_first_answer_credit(self):
        for first, revised, expected in [('My first answer.', '', 0.0), ('', 'My revised answer.', 10.0)]:
            deck = source(first)
            deck['slides'][1]['units'][-2]['text'] = ''
            deck['slides'][1]['units'][4]['text'] = revised
            result = self.score(deck)
            self.assertIsNone(result['notes_score'])
            self.assertEqual(result['revision_score'], expected)
            self.assertIsNone(result['note'])

    def test_orphan_draft_with_changed_original_region_still_needs_response_review(self):
        deck = source('My first answer.')
        deck['slides'][1]['units'][-2]['text'] = ''
        deck['slides'][1]['units'][-1]['box'] = (0, 80, 10, 10)
        result = self.score(deck)
        self.assertEqual((result['notes_score'], result['revision_score']), (None, None))
        self.assertIn('revision response', result['note'])

    def test_editing_the_printed_revision_label_does_not_count_as_a_revision_answer(self):
        deck = source()
        deck['slides'][1]['units'][-2]['text'] = ''
        deck['slides'][1]['units'][3]['text'] = 'Independently typed words in the printed revised-answer label.'
        result = self.score(deck)
        self.assertEqual(result['revision_score'], 0.0)
        self.assertIsNone(result['note'])

    def test_tiny_original_code_in_printed_label_is_not_itself_a_blank_response_region(self):
        teacher = source()
        teacher['slides'][1]['units'][-1]['box'] = (0,260,1,1)
        deck = deepcopy(teacher)
        deck['slides'][1]['units'][-2]['text'] = ''
        deck['slides'][1]['units'][3]['text'] = 'Independently typed words in the printed revised-answer label.'
        result = self.score(deck, teacher)
        self.assertEqual(result['revision_score'], 0.0)
        self.assertIsNone(result['note'])

    def test_tiny_metadata_shape_without_an_original_revision_field_needs_interpretation(self):
        teacher = source()
        teacher['slides'][1]['units'] = [u for u in teacher['slides'][1]['units'] if u['key'] != 'revision']
        teacher['slides'][1]['units'][-1]['box'] = (0,260,1,1)
        deck = deepcopy(teacher)
        deck['slides'][1]['units'][-2]['text'] = ''
        result = self.score(deck, teacher)
        self.assertIsNone(result['revision_score'])
        self.assertIn('revision response', result['note'])

    def test_new_revision_text_box_inside_the_original_blank_region_is_counted(self):
        deck = source()
        deck['slides'][1]['units'][-2]['text'] = ''
        deck['slides'][1]['units'].append({'key':'new-revision','text':'My independent revision.', 'box':(20,290,400,50)})
        result = self.score(deck)
        self.assertEqual(result['revision_score'], 10.0)
        self.assertIsNone(result['note'])

    def test_repeated_same_cycle_inventory_is_a_coding_mistake_not_an_extra_requirement(self):
        deck = source('My own explanation.')
        deck['slides'][0]['units'].append(deepcopy(deck['slides'][0]['units'][0]))
        result = self.score(deck)
        self.assertEqual((result['notes_score'], result['revision_score']), (10.0, 0.0))
        self.assertIsNone(result['problem'])

    def test_wrong_cycle_with_no_codes_cannot_borrow_the_linked_teacher_identity(self):
        result = self.score(source(header=HEADER.replace('F06', 'OTHER'), codes=False))
        self.assertIn('No handed-out copy', result['problem'])
        self.assertNotIn('notes_score', result)

    def test_photosynthesis_exception_uses_registered_agenda_scope_not_student_name(self):
        agenda = Mock()
        agenda.lessons.return_value = [{'topic': 'Photosynthesis', 'forms': [{'form_id': 'CANONICAL_FORM_123'}]}]
        self.assertTrue(completion.student_coding_required(agenda, 'biology', 'Wk 06', 'CANONICAL_FORM_123'))
        self.assertFalse(completion.student_coding_required(agenda, 'forensics', 'Wk 06', 'CANONICAL_FORM_123'))
        self.assertTrue(completion.student_coding_required(agenda, 'biology', 'Wk 06', 'WRONG_FORM_123'))
        agenda.lessons.return_value[0]['topic'] = 'Carbon energy'
        self.assertFalse(completion.student_coding_required(agenda, 'biology', 'Wk 06', 'CANONICAL_FORM_123'))

    def test_current_submitted_readiness_uses_the_same_policy_without_source_writes(self):
        deck = source(header='An older handout')
        form = {'students': [{'matched': True, 'files': [{'id': STUDENT}]}]}
        with patch.object(completion, '_template', return_value=({'version': '4'}, deck)):
            loose = completion.submitted_readiness('read', form, require_complete_codes=False)
            strict = completion.submitted_readiness('read', form, require_complete_codes=True)
        self.assertEqual((loose['status'], strict['status']), ('READY', 'NOT_READY'))
        self.assertEqual(loose['copies'][0]['revision'], '4')
        self.assertEqual(loose['copies'][0]['external_writes'], 0)


if __name__ == '__main__':
    unittest.main()
