"""Exact teacher Form subject policy on fictional Agenda rows only."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from hub_v2 import completion

PHOTO = 'FICTIONAL_PHOTO_FORM_123'
CELL = 'FICTIONAL_CELL_FORM_123'


def agenda(forms=None, topic='Continue Photosynthesis and start Cellular Respiration'):
    lessons = [{'topic': topic, 'forms': forms if forms is not None else [
        {'form_id': PHOTO, 'label': '📤 Photosynthesis — Turn in'},
        {'form_id': CELL, 'label': '📤 Cellular Respiration & Fermentation — Turn in'}]}]
    return SimpleNamespace(lessons=lambda cls, week: lessons if week == 'Wk 07' else [])


class ExactFormPhotosynthesisPolicyTests(unittest.TestCase):
    def test_two_named_forms_in_same_mixed_topic_have_opposite_exact_policies(self):
        source = agenda()
        self.assertTrue(completion.student_coding_required(source, 'biology', 'Wk 07', PHOTO))
        self.assertFalse(completion.student_coding_required(source, 'biology', 'Wk 07', CELL))

    def test_wrong_missing_blank_duplicate_or_ambiguous_form_scope_stays_strict(self):
        source = agenda()
        for fid in ('FOREIGN_FORM_123', None, ''):
            self.assertTrue(completion.student_coding_required(source, 'biology', 'Wk 07', fid))
        self.assertTrue(completion.student_coding_required(source, 'biology', 'Wk 06', CELL))
        duplicate = [{'form_id': CELL, 'label': 'Cellular Respiration'}, {'form_id': CELL, 'label': 'Cellular Respiration'}]
        self.assertTrue(completion.student_coding_required(agenda(duplicate), 'biology', 'Wk 07', CELL))
        ambiguous = SimpleNamespace(lessons=lambda cls, week: [
            {'topic': 'Cellular Respiration', 'forms': [{'form_id': CELL, 'label': 'Cellular Respiration'}]},
            {'topic': 'Photosynthesis', 'forms': [{'form_id': CELL, 'label': 'Photosynthesis'}]}])
        self.assertTrue(completion.student_coding_required(ambiguous, 'biology', 'Wk 07', CELL))

    def test_raw_form_urls_and_generic_labels_cannot_override_mixed_photo_topic(self):
        for label in ('https://docs.google.com/forms/d/' + CELL + '/edit', 'Turn it in',
                      'Week 06 submission', 'C06', 'Cycle 06', 'FIRST', 'Notes / Revision',
                      'Biology — Week 06 — Turn it in', 'A4 Bio — C06 — Submission', 'Science class — Cycle 06', '', None):
            source = agenda([{'form_id': CELL, 'label': label}])
            self.assertTrue(completion.student_coding_required(source, 'biology', 'Wk 07', CELL), str(label))
        self.assertFalse(completion.student_coding_required(
            agenda([{'form_id': CELL, 'label': 'Biology — Cellular Respiration — Turn in'}]), 'biology', 'Wk 07', CELL))

    def test_actual_single_photo_topic_fallback_remains_strict(self):
        for label in ('Turn it in', '', 'https://docs.google.com/forms/d/' + PHOTO + '/viewform'):
            self.assertTrue(completion.student_coding_required(
                agenda([{'form_id': PHOTO, 'label': label}], topic='Photosynthesis'), 'biology', 'Wk 07', PHOTO))

    def test_other_classes_and_named_non_photo_subject_do_not_inherit_photo_exception(self):
        for cls in ('forensics', 'anatomy'):
            self.assertFalse(completion.student_coding_required(agenda(), cls, 'Wk 07', PHOTO))
        self.assertFalse(completion.student_coding_required(agenda(), 'biology', 'Wk 07', CELL))

    def test_student_filename_and_other_form_label_are_not_policy_inputs(self):
        source = agenda([{'form_id': CELL, 'label': 'Cellular Respiration',
                          'students': [{'files': [{'name': 'Copy of Photosynthesis - Fictional Alex'}]}]},
                         {'form_id': PHOTO, 'label': 'Photosynthesis'}])
        before = deepcopy(source.lessons('biology', 'Wk 07'))
        self.assertFalse(completion.student_coding_required(source, 'biology', 'Wk 07', CELL))
        self.assertEqual(source.lessons('biology', 'Wk 07'), before)


if __name__ == '__main__':
    unittest.main()
