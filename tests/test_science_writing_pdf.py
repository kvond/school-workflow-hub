"""Meaningful source text/one-page/overflow checks for the separate student report."""
import copy
import io
import unittest
from pypdf import PdfReader
from hub_v2.feedback_pdf import render_science_writing
from hub_v2.native_feedback import Held


def payload():
    lessons = [{'title': 'Fictional energy transfer', 'source_date': '2026-09-25', 'source_url': 'https://example.test/source-one'},
               {'title': 'Fictional matter cycle', 'source_date': '2026-10-06', 'source_url': 'https://example.test/source-two'}]
    return {'schema': 'science-writing/v1', 'report_type': 'science_writing', 'prepared_at': '2026-10-09',
            'student': {'name': 'Ana Rivera', 'section': 'Fictional Biology'}, 'lessons': lessons,
            'dimensions': [{'key': key, 'label': label, 'level': level, 'example': quote,
                            'explanation': explanation, 'lesson': lessons[index % 2]['title'],
                            'source_date': lessons[index % 2]['source_date'], 'question_id': 'Q' + str(index + 1)}
                           for index, (key, label, level, quote, explanation) in enumerate([
                               ('vocabulary', 'Science vocabulary', 'Proficient', 'organisms release carbon dioxide', 'You use the scientific term accurately.'),
                               ('conceptual', 'Constructing an answer', 'Developing', 'the sun gives energy', 'Name the process and connect it to the question.'),
                               ('evidence', 'Specific evidence', 'Beginning', 'I saw the plant grow', 'Name the observed change and include a measurement.'),
                               ('reasoning', 'Explaining evidence', 'Developing', 'because it needs light', 'Explain how the observation supports your scientific idea.')])],
            'target': {'title': 'Your next writing move', 'text': 'Use one measured observation, then explain how it supports your answer.'},
            'note': 'This report describes the writing in the listed work. It is not a grade or a claim about who wrote it.'}


class ScienceWritingPdfTests(unittest.TestCase):
    def test_one_page_retains_four_distinct_examples_dates_and_target_without_changing_payload(self):
        value = payload(); before = copy.deepcopy(value)
        raw = render_science_writing(value)
        reader = PdfReader(io.BytesIO(raw), strict=True)
        self.assertFalse(reader.is_encrypted)
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        self.assertIn('Ana Rivera', text)
        for item in value['dimensions']:
            for field in ('label', 'example', 'explanation', 'source_date'):
                self.assertIn(item[field], text)
        self.assertIn(value['target']['text'], text)
        self.assertNotIn('Teacher notes', text)
        self.assertEqual(value, before)
        self.assertEqual(render_science_writing(value), raw)

    def test_overflow_and_excessive_quote_are_held_without_truncating_the_student_evidence(self):
        value = payload(); value['dimensions'][0]['example'] = 'quoted ' * 26
        with self.assertRaisesRegex(Held, 'short exact example'):
            render_science_writing(value)
        value = payload(); value['target']['text'] = 'A long observation. ' * 500
        with self.assertRaisesRegex(Held, 'without dropping evidence'):
            render_science_writing(value)

    def test_duplicate_dimensions_do_not_masquerade_as_the_four_writing_skills(self):
        value = payload(); value['dimensions'][3]['key'] = 'vocabulary'
        with self.assertRaisesRegex(Held, 'four distinct'):
            render_science_writing(value)

    def test_located_work_without_attributable_explanation_is_unavailable_not_a_low_score(self):
        value = payload()
        for dimension in value['dimensions']:
            dimension.update(level='Evidence unavailable', example='', explanation='The located work has no response to assess.')
        text = PdfReader(io.BytesIO(render_science_writing(value))).pages[0].extract_text()
        self.assertIn('No attributable science explanation in the located work.', text)
        self.assertNotIn('Your words: ""', text)
        self.assertNotIn('Beginning', text)


if __name__ == '__main__':
    unittest.main()
