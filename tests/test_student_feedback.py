import unittest
from dataclasses import replace

from school_workflow_hub.workflows.completion_policy import ReadinessAssessment, AssessedResponse
from school_workflow_hub.workflows.student_feedback import (
    FeedbackCase, QuestionFeedback, ResponseEvidence, HEADINGS,
    build_student_feedback, build_feedback_batch,
)


def case():
    ids = {'NOTES': ('Q1',), 'BANK': (), 'DRAFT': ()}
    ready = ReadinessAssessment('[[MARKER-INVENTORY: NOTES=1]]', True, ids, ids,
                               True, False, False, 'inventory:one')
    source = ResponseEvidence('e:current', 'revision:2', 'Q1', 'NOTES',
                              'Food chains', 'Energy moves from plants to rabbits.', 'current')
    q = QuestionFeedback('Q1', 'Where does energy go?', 'e:current', source.text,
                         'Almost there', 'I traced energy from plants to rabbits.',
                         'In the next lesson, I will trace where energy goes after the rabbit.',
                         'In our pond food-web activity, I will draw the next energy arrow.',
                         'I can follow the energy into another animal.', 'review:1', 'revision:2', True)
    return FeedbackCase('class:one', 'assignment:one', 'learner:one', 'Alex F.',
                        'revision:2', 'prompt:existing', 'final', ready,
                        (AssessedResponse('NOTES', 'Q1', 'meaningful_attempt', 'e:current'),),
                        (source,), (q,), True)


def run(c):
    return build_student_feedback(c, batch_key='batch:one')


class StudentFeedbackTests(unittest.TestCase):
    def test_exact_headings_quote_line_and_blank_reflection(self):
        r = run(case())['report']
        self.assertEqual(r['status'], 'prepared_for_review')
        self.assertEqual([x['heading'] for x in r['sections']], list(HEADINGS))
        self.assertIn('\nEnergy moves from plants to rabbits.\n', r['text'])
        self.assertEqual(r['sections'][-1]['text'].count('____________________________'), 3)
        self.assertIn('current NOTES response', r['text'])

    def test_latest_only_never_claims_growth(self):
        r = run(case())
        self.assertIn('timing is unavailable', r['report']['text'])
        self.assertEqual(r['compiled_record']['timing_evidence'], 'unknown')
        self.assertEqual(r['compiled_record']['completion']['NOTES']['completed'], 1)

    def earlier_case(self, text):
        c = case()
        e = ResponseEvidence('e:earlier', 'revision:1', 'Q1', 'NOTES', 'Food chains',
                             text, 'earlier', 'teacher:time')
        return replace(c, evidence=c.evidence + (e,), questions=(replace(
            c.questions[0], earlier_reference=e.reference,
            change_description='I now name both the plant and the rabbit.'),))

    def test_blank_baseline_is_later_completion(self):
        r = run(self.earlier_case(''))
        self.assertIn('No earlier answer was recorded', r['report']['text'])
        self.assertEqual(r['compiled_record']['timing_evidence'], 'known_later_completion')

    def test_documented_revision_and_unchanged(self):
        self.assertEqual(run(self.earlier_case('It goes somewhere.'))['compiled_record']
                         ['timing_evidence'], 'documented_revision')
        r = run(self.earlier_case(case().evidence[0].text))
        self.assertIn('My answer stayed the same.', r['report']['text'])

    def test_unverified_timing_holds(self):
        c = self.earlier_case('Earlier')
        c = replace(c, evidence=(c.evidence[0], replace(c.evidence[1], timing_reference=None)))
        self.assertEqual(run(c)['report']['sections'], [])

    def test_quote_fabrication_stale_assessment_or_unreviewed_holds(self):
        for changes in ({'quote': 'Invented words'}, {'assessment_revision': 'revision:old'},
                        {'reviewed': False}):
            c = case()
            r = run(replace(c, questions=(replace(c.questions[0], **changes),)))
            self.assertEqual(r['report']['status'], 'needs_review')
            self.assertEqual(r['compiled_record']['understanding'], [])

    def test_readiness_failure_and_unreadable_completion_hold(self):
        c = case()
        bad = replace(c, readiness=replace(c.readiness, inventory_verified=False))
        self.assertEqual(run(bad)['report']['status'], 'needs_review')
        bad = replace(c, responses=(replace(c.responses[0], judgment='review'),))
        self.assertIsNone(run(bad)['compiled_record']['completion'])

    def test_verified_misconception_and_copy_flag(self):
        c = self.earlier_case('Plants eat soil.')
        earlier = replace(c.evidence[1], printed_text='Plants eat soil.')
        q = replace(c.questions[0], misconception_key='idea:soil',
                    misconception_description='Plants get food from soil',
                    misconception_reference=earlier.reference, misconception_quote=earlier.text)
        r = run(replace(c, questions=(q,), evidence=(c.evidence[0], earlier)))
        self.assertEqual(r['compiled_record']['misconceptions'][0]['timing'], 'earlier')
        self.assertEqual(len(r['report']['teacher_only_flags']), 1)
        self.assertNotIn('Teacher only', r['report']['text'])

    def test_initial_bank_is_not_due(self):
        r = run(replace(case(), stage='initial'))
        self.assertFalse(r['compiled_record']['completion']['BANK']['due'])

    def test_names_not_inferred_and_full_surnames_rejected(self):
        self.assertEqual(run(replace(case(), filename_identity_verified=False))['report']['status'], 'needs_review')
        with self.assertRaises(ValueError):
            run(replace(case(), display_name='Alex Fictional'))

    def test_missing_question_and_fabricated_misconception_hold(self):
        c = case()
        ids = {'NOTES': ('Q1', 'Q2'), 'BANK': (), 'DRAFT': ()}
        expanded = replace(c, readiness=replace(c.readiness, required_ids=ids, observed_ids=ids),
                           responses=c.responses + (AssessedResponse('NOTES', 'Q2', 'blank', 'e:second'),))
        self.assertIn('feedback_question_coverage_incomplete', run(expanded)['report']['review_reasons'])
        fabricated = replace(c, questions=(replace(c.questions[0], misconception_key='idea:x',
                    misconception_description='Idea', misconception_reference='e:current',
                    misconception_quote='Never written'),))
        self.assertIn('misconception_evidence_unverified', run(fabricated)['report']['review_reasons'])

    def test_compiled_line_and_provenance(self):
        result = build_feedback_batch([case()], batch_key='batch:one')
        self.assertIn('NOTES=1/1', result['compiled_lines'][0])
        self.assertEqual(result['reports'][0]['readiness']['status'], 'READY')
        self.assertEqual(result['reports'][0]['assessment_references'], ['review:1'])

    def test_batch_limits_scope_and_duplicates(self):
        with self.assertRaisesRegex(ValueError, 'twenty'):
            build_feedback_batch([case()] * 21, batch_key='batch:one')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            build_feedback_batch([case()] * 2, batch_key='batch:one')
        r = build_feedback_batch([case()], batch_key='batch:one')
        self.assertEqual(r['external_writes'], 0)
        self.assertFalse(r['execution_supported'])


if __name__ == '__main__':
    unittest.main()
