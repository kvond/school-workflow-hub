import unittest
from school_workflow_hub.workflows.missing_work_feedback import build_missing_work_feedback

def note(**changes):
    args=dict(class_key='c:1',assignment_key='a:1',learner_key='l:1',display_name='Jamie P.',
              checked_at='2026-09-15T12:00:00-04:00',evidence_reference='scan:1',
              missingness_verified=True,roster_identity_verified=True)
    return build_missing_work_feedback(**{**args,**changes})

class MissingFeedbackTests(unittest.TestCase):
    def test_failed_source_cannot_become_missing_feedback(self):
        for changes in ({'missingness_verified':False},{'roster_identity_verified':False}):
            with self.assertRaises(ValueError):note(**changes)
    def test_note_has_contract_but_no_inferred_understanding_or_grade(self):
        r=note()
        self.assertIsNone(r['understanding'])
        self.assertIsNone(r['explanation'])
        self.assertFalse(r['grade_change'])
        self.assertTrue(any('signature' in line for line in r['contract_lines']))
        self.assertTrue(any('Help I need' in line for line in r['contract_lines']))
