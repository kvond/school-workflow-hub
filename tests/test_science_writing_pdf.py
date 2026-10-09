"""Source-bound charts, two-page writing practice and evidence-preserving limits."""
import copy
import io
import unittest
from pypdf import PdfReader
from hub_v2.feedback_pdf import _science_writing_history, render_science_writing
from hub_v2.native_feedback import Held, TEACHER_DIMENSIONS


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
    def test_two_pages_retain_evidence_and_individual_challenge_without_changing_payload(self):
        value = payload(); before = copy.deepcopy(value)
        raw = render_science_writing(value)
        reader = PdfReader(io.BytesIO(raw), strict=True)
        self.assertFalse(reader.is_encrypted)
        self.assertEqual(len(reader.pages), 2)
        text = reader.pages[0].extract_text()
        self.assertIn('Ana Rivera', text)
        for item in value['dimensions']:
            for field in ('label', 'example', 'explanation', 'source_date'):
                self.assertIn(item[field], text)
        second = reader.pages[1].extract_text()
        self.assertNotIn(value['target']['text'], text)
        self.assertIn(value['target']['title'], second)
        self.assertIn(value['target']['text'], second)
        self.assertIn('My explanation', second)
        self.assertIn('Bring this page back', second)
        self.assertIn('One rated observation. No trend.', text)
        self.assertNotIn('Teacher notes', text)
        self.assertEqual(value, before)
        self.assertEqual(render_science_writing(value), raw)

    def history_payload(self, dates=('2026-09-25', '2026-10-01', '2026-10-06')):
        value = payload(); value['student']['student_key'] = 'fictional-person-one'
        value['lessons'] = [{'title': 'Fictional source ' + str(i), 'source_date': date,
                             'source_file_id': 'fictional-source-' + str(i), 'source_revision': '3'}
                            for i, date in enumerate(dates)]
        rows = [{'student_key': 'fictional-person-one', 'source_file_id': lesson['source_file_id'],
                 'source_revision': '3', 'lesson': lesson['title'], 'date': lesson['source_date'],
                 'dimension': dimension, 'level': ['Beginning', 'Developing', 'Proficient'][i],
                 'quote': 'A fictional observed science explanation.'}
                for i, lesson in enumerate(value['lessons']) for dimension in TEACHER_DIMENSIONS]
        value['provenance'] = {'source_observations': rows}
        return value

    def test_charts_use_actual_per_area_observations_and_never_join_across_an_unassessed_sample(self):
        value = self.history_payload()
        value['provenance']['source_observations'] = [row for row in value['provenance']['source_observations']
                                                    if not (row['source_file_id'] == 'fictional-source-1'
                                                            and row['dimension'] == TEACHER_DIMENSIONS[2])]
        _, charts = _science_writing_history(value)
        evidence = next(chart for chart in charts if chart['key'] == 'evidence')
        self.assertEqual(evidence['points'], [1, None, 3])
        self.assertEqual(evidence['segments'], [])
        self.assertEqual(charts[0]['points'], [1, 2, 3])
        self.assertEqual(charts[0]['segments'], [(0, 1), (1, 2)])

    def test_same_day_lessons_need_original_timestamps_before_a_line_implies_order(self):
        value = self.history_payload(('2026-10-01', '2026-10-01', '2026-10-06'))
        _, charts = _science_writing_history(value)
        self.assertNotIn((0, 1), charts[0]['segments'])
        value['lessons'][0]['submitted_at'] = '2026-10-01T18:00:00Z'
        value['lessons'][1]['submitted_at'] = '2026-10-01T17:00:00Z'
        slots, charts = _science_writing_history(value)
        self.assertEqual([slot['source_file_id'] for slot in slots[:2]], ['fictional-source-1', 'fictional-source-0'])
        self.assertEqual(charts[0]['points'], [2, 1, 3])
        self.assertIn((0, 1), charts[0]['segments'])

    def test_wrong_person_revision_date_or_duplicate_observation_holds_without_relabeling(self):
        for field, wrong in [('student_key', 'other-person'), ('source_revision', '9'), ('date', '2026-10-09')]:
            value = self.history_payload(); value['provenance']['source_observations'][0][field] = wrong
            with self.subTest(field=field), self.assertRaisesRegex(Held, 'student and original source revision'):
                _science_writing_history(value)
        value = self.history_payload(); value['provenance']['source_observations'].append(
            copy.deepcopy(value['provenance']['source_observations'][0]))
        # Use two sources so the duplicated row reaches the conflict check, not the reading bound.
        value['lessons'].pop(); value['provenance']['source_observations'] = [r for r in value['provenance']['source_observations']
                                                                            if r['source_file_id'] != 'fictional-source-2']
        with self.assertRaisesRegex(Held, 'never averaged'):
            _science_writing_history(value)
        value = self.history_payload(); value['lessons'][0]['submitted_at'] = '2026-09-25T01:00:00Z'
        with self.assertRaisesRegex(Held, 'original work date'):
            _science_writing_history(value)

    def test_presentation_time_context_is_exact_and_cannot_change_original_source_fields(self):
        value = self.history_payload(('2026-10-01', '2026-10-01', '2026-10-06'))
        original = copy.deepcopy(value['lessons'])
        events = [{'source_file_id': 'fictional-source-' + str(i), 'source_revision': '3',
                   'submitted_at': '2026-10-01T%d:00:00Z' % (18 - i)} for i in range(2)]
        value['presentation_context'] = {'original_submission_times': events}
        slots, charts = _science_writing_history(value)
        self.assertEqual(value['lessons'], original)
        self.assertEqual(slots[0]['source_file_id'], 'fictional-source-1')
        self.assertIn((0, 1), charts[0]['segments'])
        events[0]['source_revision'] = '4'
        with self.assertRaisesRegex(Held, 'listed original source revision'):
            _science_writing_history(value)
        events[0]['source_revision'] = '3'; events.append(copy.deepcopy(events[0]))
        with self.assertRaisesRegex(Held, 'duplicated or missing'):
            _science_writing_history(value)

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

    def test_a_distinct_student_target_stays_exactly_on_the_second_page(self):
        value = payload()
        value['target'] = {'title': 'Trace the atoms accurately',
                           'text': 'Trace carbon into plant material separately from the oxygen released.'}
        pages = PdfReader(io.BytesIO(render_science_writing(value))).pages
        self.assertNotIn(value['target']['title'], pages[0].extract_text())
        self.assertIn(value['target']['title'], pages[1].extract_text())
        self.assertIn(value['target']['text'], pages[1].extract_text())
        self.assertNotIn('Check one rule in two cases', pages[1].extract_text())


if __name__ == '__main__':
    unittest.main()
