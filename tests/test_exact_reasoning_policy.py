"""The dealt-with Reasoning rule is exact, read-only and uses fictional scopes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import app, completion, google, ledger, morning, review_context, work
from test_form_source_bindings_and_case_policy import Agenda as BaseAgenda, excluded, CASE, SAFETY, STUDENT

REASONING = 'FICTIONAL_REASONING_FORM_12345'
OBSERVATION = 'FICTIONAL_OBSERVATION_FORM_12345'


class Agenda(BaseAgenda):
    def __init__(self):
        super().__init__()
        self.classes = deepcopy(self.classes)
        self.weeks = deepcopy(self.weeks) + [{'label': 'Wk 03', 'monday': '2026-09-14'}]
        for cls in self.classes.values():
            cls['layout']['rows'].append((13, 'Wk 03'))
        self.rows['Wk 03'] = [{'row': 13, 'topic': 'Observation and Inductive and Deductive Reasoning',
            'materials': [], 'edit_url': 'https://fictional.example/agenda', 'forms': [
                {'form_id': OBSERVATION, 'label': 'Observation Skills — Turn in', 'problem': None},
                {'form_id': REASONING, 'label': '📤 Inductive and Deductive Reasoning — Turn in', 'problem': None}]}]


def declaration(agenda, **changes):
    rule = dict(school_year='2026-27', agenda_id=agenda.spreadsheet_id, class_key='forensics',
                week='Wk 03', form_id=REASONING, purpose='reasoning_dealt_with',
                reason='The teacher says Inductive and Deductive Reasoning is dealt with; retain all original work and Saved grades.')
    rule.update(changes)
    return rule


def settings(agenda, **changes):
    return {'HUB_ASSIGNMENT_EXCLUSIONS': json.dumps([declaration(agenda, **changes)])}


class ExactReasoningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        self.config = settings(self.agenda)

    def test_exact_current_reasoning_excludes_without_changing_catalog_or_other_lessons(self):
        old = deepcopy(self.agenda.rows)
        rule = work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)
        self.assertTrue(rule['excluded']); self.assertIsNone(rule['problem'])
        self.assertEqual(rule['purpose'], 'reasoning_dealt_with')
        for cls, wk, fid in [('forensics', 'Wk 03', OBSERVATION), ('forensics', 'Wk 01', SAFETY),
                             ('forensics', 'Wk 01', CASE), ('biology', 'Wk 03', REASONING)]:
            self.assertFalse(work.assignment_policy(self.config, self.agenda, cls, wk, fid)['excluded'])
        self.assertEqual(morning.catalog(self.agenda, self.config), morning.catalog(self.agenda, {}))
        self.assertEqual(self.agenda.rows, old)

    def test_wrong_year_agenda_class_week_form_or_purpose_cannot_exclude(self):
        for changes in ({'school_year': '2025-26'}, {'school_year': '2027-28'},
                        {'agenda_id': 'FICTIONAL_OTHER_AGENDA_12345'}, {'class_key': 'biology'},
                        {'week': 'Wk 01'}, {'form_id': OBSERVATION}, {'purpose': 'all_week03'},
                        {'reason': ''}, {'reason': 'bad\nreason'}):
            with self.subTest(changes=changes):
                result = work.assignment_policy(settings(self.agenda, **changes), self.agenda, 'forensics', 'Wk 03', REASONING)
                self.assertFalse(result['excluded'])
        self.agenda.weeks[-1]['monday'] = '2027-09-13'
        self.assertFalse(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])

    def test_unique_meaningful_current_form_title_required_not_topic_or_filename(self):
        form = self.agenda.rows['Wk 03'][0]['forms'][1]
        for title in ('https://docs.google.com/forms/d/' + REASONING + '/viewform', 'Turn in',
                      'Observation Skills', 'Inductive and Deductive Reasoning and Observation', '', 'Lab Safety'):
            form['label'] = title
            with self.subTest(title=title):
                self.assertFalse(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])
        form['label'] = 'Inductive and Deductive Reasoning'
        form['problem'] = 'Cannot read linked Form'
        self.assertFalse(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])
        form['problem'] = None
        self.agenda.rows['Wk 03'][0]['forms'].append(deepcopy(form))
        self.assertFalse(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])
        self.agenda.rows['Wk 03'][0]['forms'] = [self.agenda.rows['Wk 03'][0]['forms'][0]]
        self.assertFalse(work.assignment_policy(self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])

    def test_append_preserves_case_exclusion_and_duplicate_rule_holds(self):
        cases = json.loads(excluded()['HUB_ASSIGNMENT_EXCLUSIONS'])
        config = {'HUB_ASSIGNMENT_EXCLUSIONS': json.dumps(cases + [declaration(self.agenda)])}
        self.assertTrue(work.assignment_policy(config, self.agenda, 'forensics', 'Wk 01', CASE)['excluded'])
        self.assertFalse(work.assignment_policy(config, self.agenda, 'forensics', 'Wk 01', SAFETY)['excluded'])
        self.assertTrue(work.assignment_policy(config, self.agenda, 'forensics', 'Wk 03', REASONING)['excluded'])
        config['HUB_ASSIGNMENT_EXCLUSIONS'] = json.dumps(cases + [declaration(self.agenda)] * 2)
        self.assertTrue(work.assignment_policy(config, self.agenda, 'forensics', 'Wk 03', REASONING)['problem'])

    def test_list_readiness_and_direct_score_stop_before_source_or_assessment(self):
        load, score = Mock(side_effect=AssertionError('no discovery')), Mock(side_effect=AssertionError('no score'))
        with patch.object(completion, 'assignment_readiness', side_effect=AssertionError('no teacher reads')):
            result = morning.make_list('read', self.config, self.agenda, 'forensics', 'Wk 03', REASONING, load, score)
        self.assertEqual(result['code'], 'excluded_week'); load.assert_not_called(); score.assert_not_called()
        self.assertEqual(completion.assignment_readiness('read', self.config, self.agenda, 'forensics', 'Wk 03', REASONING)['code'], 'excluded_week')
        query = {'class': ['forensics'], 'week': ['Wk 03'], 'form': [REASONING], 'file': [STUDENT]}
        with patch.object(google, 'service_token', return_value='read'), patch.object(app, 'load_agenda', return_value=self.agenda), \
             patch.object(app, 'lesson_work', side_effect=AssertionError('no discovery')):
            self.assertEqual(app.api('api/score', query, self.config)['code'], 'excluded_week')

    def test_stamp_denied_and_review_nonaction_with_original_history_literal(self):
        payload = {'class': 'forensics', 'week': 'Wk 03', 'form': REASONING, 'kind': 'ic'}
        with patch.object(app, '_same_origin_json', return_value=None), patch.object(app, '_json_body', return_value=payload), \
             patch.object(google, 'service_token', return_value='read'), patch.object(app, 'load_agenda', return_value=self.agenda), \
             patch.object(app, '_form_context', side_effect=AssertionError('no discovery')), patch.object(ledger, 'record') as record:
            response = app.record_post({}, lambda status, body: (status, body), self.config)
        self.assertEqual((response[0], response[1]['code']), ('409 Conflict', 'excluded_week')); record.assert_not_called()
        current = review_context.scope(self.agenda, self.config, 'forensics', 'Wk 03', REASONING)
        found = {'class_key': 'forensics', 'week': 'Wk 03', 'lessons': [{'forms': [
            {'form_id': REASONING, 'students': [], 'late': [], 'earlier': [{'id': 'FICTIONAL_HISTORY_12345'}]}]}]}
        old = deepcopy(found)
        result = review_context.build(current, found)
        self.assertEqual((result['status'], result['notices']), ('excluded', []))
        self.assertFalse(result['source_selection']['assessment_performed']); self.assertEqual(found, old)
        self.assertIn('Saved grades', result['limitations'][0])


if __name__ == '__main__':
    unittest.main()
