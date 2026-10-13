"""Initial comparison, latest-only narration, full source history and paper CER."""
import copy
import io
import unittest

from pypdf import PdfReader
from hub_v2.feedback_pdf import _science_writing_history, render_science_writing
from hub_v2.science_writing_display import context, validate
from hub_v2.native_feedback import Held, TEACHER_DIMENSIONS
from hub_v2.student_archive import _science_assessment, _science_presentation_context


def fixture(count=2):
    value = {'schema': 'science-writing/v1', 'report_type': 'science_writing', 'class_key': 'biology',
        'prepared_at': '2026-10-13', 'student': {'name': 'Fictional Student', 'section': 'Fictional Biology', 'student_key': 'person-one'},
        'lessons': [{'title': 'Fictional lesson %s' % (i // 2), 'source_date': '2026-10-01',
                     'source_file_id': 'source-%s' % i, 'source_revision': '1'} for i in range(count)],
        'dimensions': [{'key': key, 'label': key, 'level': 'Proficient', 'example': 'Earlier explanation.',
                        'explanation': 'Earlier selected explanation.', 'lesson': 'Fictional lesson 0', 'source_date': '2026-10-01'}
                       for key in ('vocabulary', 'conceptual', 'evidence', 'reasoning')],
        'target': {'title': 'Historical target', 'text': 'The unchanged old thinking question.'}, 'note': 'Not a grade.',
        'presentation_context': {'original_submission_times': [{'source_file_id': 'source-%s' % i, 'source_revision': '1',
                'submitted_at': '2026-10-01T%s:%s:00Z' % (12 + i // 60, str(i % 60).zfill(2))} for i in range(count)]},
        'provenance': {'source_observations': []}}
    for i, lesson in enumerate(value['lessons']):
        for dimension in TEACHER_DIMENSIONS:
            value['provenance']['source_observations'].append({'student_key': 'person-one', 'section': 'Fictional Biology',
                'source_file_id': lesson['source_file_id'], 'source_revision': '1', 'lesson': lesson['title'],
                'date': lesson['source_date'], 'dimension': dimension, 'level': 'Proficient' if i % 2 == 0 else 'Developing',
                'quote': 'Earlier sample.' if i == 0 else 'Latest sample %s.' % i,
                'explanation': 'Accurate earlier mechanism.' if i == 0 else 'Later explanation omits the mechanism.'})
    return value


def practice():
    return {'question': 'Which fictional model best explains this observation?', 'evidence': [
        'The first supplied practice observation.', 'The second supplied practice observation.',
        'The third supplied practice observation.'], 'reference_url': 'https://example.test/teacher-lesson'}


class WritingDisplayTests(unittest.TestCase):
    def test_initial_comparison_explains_actual_decline_and_keeps_assessment_and_old_target(self):
        value = fixture(); before = copy.deepcopy(value)
        value['presentation_context'] = context(value, practice(), comparison=True)
        self.assertEqual(_science_assessment(value), _science_assessment(before))
        reader = PdfReader(io.BytesIO(render_science_writing(value)))
        self.assertEqual(len(reader.pages), 3)
        first = reader.pages[0].extract_text()
        for text in ('Earlier sample.', 'Latest sample 1.', 'Accurate earlier mechanism.',
                     'Later explanation omits the mechanism.', 'Proficient to Developing'):
            self.assertIn(text, first)
        last = reader.pages[-1].extract_text()
        self.assertNotIn('One question I want to discuss', last)
        self.assertNotIn(before['target']['text'], last)
        for text in ['Claim:', 'Evidence:', 'Reasoning:', 'not assessed in this report', *practice()['evidence']]:
            self.assertIn(text, last)
        self.assertEqual(value['dimensions'], before['dimensions'])
        self.assertEqual(value['provenance'], before['provenance'])

    def test_subsequent_narrative_only_latest_but_graph_has_every_distinct_submission_of_same_lesson(self):
        value = fixture(10); before = copy.deepcopy(value)
        value['presentation_context'] = context(value, practice())
        reader = PdfReader(io.BytesIO(render_science_writing(value)))
        self.assertEqual(len(reader.pages), 4)
        first = reader.pages[0].extract_text()
        self.assertIn('Latest sample 9.', first); self.assertNotIn('Earlier sample.', first)
        slots, charts = _science_writing_history(value)
        self.assertEqual(len(slots), 10)
        for chart in charts:
            self.assertEqual(chart['points'], [3, 2] * 5)
            self.assertEqual(len(chart['segments']), 9)
        combined = '\n'.join(page.extract_text() for page in reader.pages[1:-1])
        for i in range(1, 11):
            self.assertIn('S%d |' % i, combined)
        self.assertEqual(value['provenance'], before['provenance'])

    def test_missing_latest_observation_stays_unassessed_not_stale_proficient_or_zero(self):
        value = fixture()
        value['provenance']['source_observations'] = [r for r in value['provenance']['source_observations']
            if not (r['source_file_id'] == 'source-1' and r['dimension'] == TEACHER_DIMENSIONS[0])]
        value['presentation_context'] = context(value, practice())
        reader = PdfReader(io.BytesIO(render_science_writing(value)))
        self.assertIn('Not assessed', reader.pages[0].extract_text())
        self.assertEqual(_science_writing_history(value)[1][0]['points'], [3, None])
        self.assertEqual(_science_writing_history(value)[1][0]['segments'], [])

    def test_new_unreviewed_submission_is_visible_as_gap_and_does_not_replace_latest_reviewed_cer(self):
        value = fixture(3)
        value['provenance']['source_observations'] = [r for r in value['provenance']['source_observations']
                                                    if r['source_file_id'] != 'source-2']
        value['presentation_context'] = context(value, practice())
        self.assertEqual(value['presentation_context']['writing_display']['latest_source']['source_file_id'], 'source-1')
        self.assertEqual(_science_writing_history(value)[1][0]['points'], [3, 2, None])
        reader = PdfReader(io.BytesIO(render_science_writing(value)))
        self.assertIn('S3 | Fictional lesson 1', reader.pages[1].extract_text())
        self.assertIn('unassessed', reader.pages[1].extract_text())
        self.assertIn('Latest sample 1.', reader.pages[0].extract_text())
        self.assertNotIn('Latest sample 2.', reader.pages[0].extract_text())

    def test_cer_requires_latest_exact_source_three_distinct_facts_and_verified_chronology(self):
        for mutate in (lambda r: r['presentation_context']['writing_display']['latest_source'].update(source_revision='2'),
                       lambda r: r['presentation_context']['writing_display']['cer']['evidence'].pop(),
                       lambda r: r['presentation_context']['writing_display']['cer']['evidence'].append('Fourth fact.'),
                       lambda r: r['presentation_context']['writing_display'].update(mode='guessed'),
                       lambda r: r['presentation_context'].update(original_submission_times=[])):
            value = fixture(); value['presentation_context'] = context(value, practice())
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(Held):
                render_science_writing(value)

    def test_archive_overlay_validates_original_receipts_and_does_not_permit_rerating(self):
        value = fixture(); old = copy.deepcopy(value)
        value['presentation_context'] = context(value, practice(), comparison=True)
        sources = [{'source_file_id': r['source_file_id'], 'source_version': r['source_revision'],
                    'submitted_at': r['submitted_at'], 'date': '2026-10-01', 'date_basis': 'form_receipt'}
                   for r in value['presentation_context']['original_submission_times']]
        _science_presentation_context(value, sources)
        self.assertEqual(_science_assessment(old), _science_assessment(value))
        sources[0]['submitted_at'] = '2026-10-01T12:30:00Z'
        with self.assertRaisesRegex(ValueError, 'original verified Form receipt'):
            _science_presentation_context(value, sources)

    def test_bad_or_overlong_cer_does_not_silently_drop_evidence_or_lines(self):
        value = fixture(); value['presentation_context'] = context(value, practice())
        value['presentation_context']['writing_display']['cer']['evidence'][0] = 'long ' * 100
        with self.assertRaises(Held):
            render_science_writing(value)
        value['presentation_context']['writing_display']['cer']['evidence'][0] = '\nunsafe'
        with self.assertRaises(Held):
            validate(value)


if __name__ == '__main__':
    unittest.main()
