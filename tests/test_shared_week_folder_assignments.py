"""Synthetic shared-folder reviews keep exact assignment identity intact."""
import unittest
from copy import deepcopy
from hub_v2 import work


def form(ident, label='Membrane Structure — Turn in', files=('A',)):
    return {'form_id': ident, 'label': label,
            'found': [{'files': [{'id': fid} for fid in files]}]}


class SharedWeekFolderAssignmentsTests(unittest.TestCase):
    def reviews(self, forms):
        return work.shared_folder_reviews(forms, {'FOLDER': 'Biology Wk 04 — Shared'},
                                          {'A': 'FOLDER', 'B': 'FOLDER', 'C': 'FOLDER'})

    def test_same_display_label_does_not_collapse_distinct_assignments(self):
        reviews = self.reviews([form('FormAlpha'), form('FormBeta', files=('B',))])
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]['assignments'], ['Membrane Structure', 'Membrane Structure'])
        self.assertEqual(reviews[0]['message'].count('(1 file)'), 2)

    def test_repeated_exact_form_with_different_labels_is_one_assignment(self):
        forms = [form('FormAlpha'), form('FormAlpha', 'New display label', files=('B',))]
        self.assertEqual(self.reviews(forms), [])

    def test_duplicate_references_do_not_inflate_file_counts(self):
        forms = [form('FormAlpha', files=('A', 'A')), form('FormAlpha', files=('A', 'B')),
                 form('FormBeta', 'Membrane Transport — Turn in', files=('C',))]
        reviews = self.reviews(forms)
        self.assertEqual(len(reviews), 1)
        self.assertIn('Membrane Structure (2 files)', reviews[0]['message'])
        self.assertIn('Membrane Transport (1 file)', reviews[0]['message'])

    def test_missing_form_identity_keeps_uncertainty_visible(self):
        for forms in ([form(None), form(None, files=('B',))],
                      [form('FormAlpha'), form('', files=('B',))]):
            with self.subTest(forms=forms):
                self.assertEqual(len(self.reviews(forms)), 1)

    def test_teacher_collection_and_receipts_do_not_prove_file_lesson_allocation(self):
        forms = [form('FormAlpha'), form('FormBeta', files=('B',))]
        for item in forms:
            item.update(_teacher_source_verified=True,
                        teacher_source_binding={'teacher_ids': ['ExactTeacher']})
            submission = item['found'][0]
            submission.update(source='form', key='Response', submitted_at='2026-10-01T15:00:00Z')
            submission['files'][0].update(source_form_id=item['form_id'], source_response_id='Response',
                                          source_submitted_at='2026-10-01T15:00:00Z')
        before = deepcopy(forms)
        self.assertEqual(len(self.reviews(forms)), 1)
        self.assertEqual(forms, before)

    def test_files_allocated_to_two_forms_still_require_review(self):
        self.assertEqual(len(self.reviews([form('FormAlpha'), form('FormBeta')])), 1)

    def test_nested_parent_and_file_response_exclusion_are_preserved(self):
        forms = [form('FormAlpha'), form('FormBeta', files=('B',))]
        parents = {'A': 'SUB', 'B': 'SUB', 'SUB': 'FOLDER'}
        self.assertEqual(len(work.shared_folder_reviews(forms, {'FOLDER': 'Biology Wk 04 — Shared'}, parents)), 1)
        self.assertEqual(work.shared_folder_reviews(forms, {'FOLDER': 'Biology Wk 04 (File responses)'}, parents), [])

    def test_empty_sibling_does_not_invent_an_ownership_review(self):
        self.assertEqual(self.reviews([form('FormAlpha'), form('FormBeta', files=())]), [])
