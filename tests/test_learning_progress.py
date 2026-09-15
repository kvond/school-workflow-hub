import copy
import tempfile
import unittest
from pathlib import Path
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.workflows.learning_progress import *

def observation(**changes):
    row=dict(observation_id='o:1',class_key='c:1',learner_key='l:1',assignment_key='a:1',
             concept_scope='foodchains:causal',concept_label='Energy in food chains',stage='final',
             rubric_version='explanation:v1',prompt_version='feedback:v2',submission_revision='r:1',
             evidence_reference='e:1',assessment_reference='assessment:1',reviewed=True,
             provenance='synthetic',understanding='Almost there',explanation='Connects ideas',
             quote='The rabbit eats grass.',source_text='The rabbit eats grass.',date='2026-09-01')
    return {**row,**changes}

class LearningProgressTests(unittest.TestCase):
    def test_history_persists_and_rerun_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.db'
            db=initialize_database(path)
            self.assertTrue(record_observation(db,observation()))
            self.assertFalse(record_observation(db,observation()))
            with self.assertRaisesRegex(ValueError,'conflict'):
                record_observation(db,observation(understanding='Proficient'))
            db.close();db=initialize_database(path)
            self.assertEqual(len(read_observations(db,class_key='c:1',start_date='2026-09-01',end_date='2026-09-15',provenance='synthetic')),1)
            db.close()
    def test_scope_and_rubric_changes_split_series(self):
        result=build_learning_progress([observation(),observation(observation_id='o:2',date='2026-09-02',rubric_version='explanation:v2')],class_key='c:1',provenance='synthetic')
        self.assertEqual(len(result['series']),2)
        self.assertFalse(any(s['trend_available'] for s in result['series']))
    def test_missing_evidence_is_not_a_low_score(self):
        result=build_learning_progress([observation(understanding=None,explanation=None,quote='',source_text='')],class_key='c:1',provenance='synthetic')
        self.assertEqual(result['distributions'][0]['understanding']['Unavailable'],1)
        self.assertEqual(result['distributions'][0]['understanding']['Developing'],0)
    def test_rejects_fabricated_quote_and_cross_class_and_duplicates(self):
        for row in [observation(quote='Never said'),observation(class_key='c:2'),observation(provenance='source_read')]:
            with self.assertRaises(ValueError):build_learning_progress([row],class_key='c:1',provenance='synthetic')
        with self.assertRaisesRegex(ValueError,'ambiguous_learning_date'):
            build_learning_progress([observation(),observation(observation_id='o:2')],class_key='c:1',provenance='synthetic')
