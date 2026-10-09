"""Current grouped work checks preserve lesson context and student writing space."""
import io
import unittest
from copy import deepcopy

from pypdf import PdfReader
from hub_v2.missing_work_contract_pdf import render
from hub_v2.missing_work_contracts import SCHEMA
from hub_v2.native_feedback import Held


def contracts():
    person = {'name': 'Rivera, Ana', 'section': 'Fictional Biology', 'student_key': 'fictional-student'}
    return [{'schema': SCHEMA, 'report_type': 'missing_work_contract', 'status': 'open',
             'class_key': 'biology', 'prepared_at': '2026-10-09', 'student': person,
             'assignment_title': title, 'original_due_date': due, 'contract_key': 'fictional-contract-' + str(i),
             'source_proof': {'reference': 'fictional-verified-current-check'}, 'grade_change': False,
             'delivery_changed': False, 'note': 'Existing extensions, deadlines and grades stay unchanged.'}
            for i, (title, due) in enumerate([('Fictional ecosystem explanation', '2026-09-16'),
                                            ('Fictional matter cycle', '2026-10-05')])]


class MissingWorkContractPdfTests(unittest.TestCase):
    def test_one_page_lists_each_original_due_date_and_preserves_unscored_writing_space(self):
        value = contracts(); prior = deepcopy(value)
        raw = render(value); reader = PdfReader(io.BytesIO(raw), strict=True)
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        for item in value:
            self.assertIn(item['assignment_title'], text)
            self.assertIn(item['original_due_date'], text)
        for phrase in ('Work not located as of 2026-10-09', 'My next step', 'When I plan to do it',
                       'Help I need', 'Student signature', 'Existing extensions'):
            self.assertIn(phrase, text)
        self.assertNotIn('Beginning', text)
        self.assertNotIn('late', text.lower())
        self.assertEqual(value, prior)
        self.assertEqual(raw, render(value))

    def test_mixed_student_dates_duplicate_lessons_and_grade_changes_are_rejected(self):
        for field, changed in (('student', {'name': 'Different student', 'section': 'Fictional Biology', 'student_key': 'other'}),
                               ('prepared_at', '2026-10-10'), ('contract_key', 'fictional-contract-0'),
                               ('grade_change', True), ('source_proof', {})):
            value = contracts(); value[1][field] = changed
            with self.subTest(field=field), self.assertRaises(Held):
                render(value)
