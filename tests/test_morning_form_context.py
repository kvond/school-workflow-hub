"""Repeated Forms use an explicit source context, never row order or titles."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import morning

AGENDA = 'FICTIONAL_CONTEXT_AGENDA_123'
FORM = 'FICTIONAL_REPEATED_FORM_123'
OTHER = 'FICTIONAL_INDEPENDENT_FORM_123'
OLDER = 'FICTIONAL_OLDER_TEACHER_123'
CURRENT = 'FICTIONAL_CURRENT_TEACHER_123'
FOLDER = 'FICTIONAL_CURRENT_FOLDER_123'
TODAY = '2026-10-23T08:00:00-04:00'


class Agenda:
    spreadsheet_id = AGENDA
    weeks = [{'label': 'Wk 07', 'monday': '2026-10-12'},
             {'label': 'Wk 08', 'monday': '2026-10-19'}]
    classes = {'forensics': {'name': 'Fictional Forensics',
               'layout': {'rows': [(17, 'Wk 07'), (18, 'Wk 08')]}}}

    def __init__(self):
        self.weeks = deepcopy(self.weeks)
        self.classes = deepcopy(self.classes)
        self.rows = {}
        for label, teacher in [('Wk 07', OLDER), ('Wk 08', CURRENT)]:
            self.rows[label] = [{'topic': 'A title cannot select the context',
                'materials': [{'label': 'Teacher deck',
                    'url': 'https://docs.google.com/presentation/d/' + teacher + '/edit'}],
                'forms': [{'form_id': FORM, 'label': 'Same Form', 'problem': None}]}]
        self.rows['Wk 07'].append({'topic': 'Independent lesson', 'materials': [],
                'forms': [{'form_id': OTHER, 'label': 'Independent lesson', 'problem': None}]})

    def week_options(self, cls):
        return deepcopy(self.weeks)

    def lessons(self, cls, week):
        return deepcopy(self.rows.get(week, []))


def declaration(**changes):
    value = {'school_year': '2026-27', 'agenda_id': AGENDA, 'class_key': 'forensics',
             'week': 'Wk 08', 'form_id': FORM, 'source_folder_id': FOLDER,
             'teacher_ids': [CURRENT]}
    value.update(changes)
    return value


def config(entries=None):
    return {'HUB_FORM_TEACHER_SOURCES': json.dumps([declaration()] if entries is None else entries)}


class CurrentFormContextTests(unittest.TestCase):
    def setUp(self):
        self.agenda = Agenda()
        patcher = patch.object(morning.ledger, 'eastern_now', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def catalog(self, settings=None):
        return morning.catalog(self.agenda, config() if settings is None else settings)

    def make(self, week, settings=None, readiness=None):
        form = {'form_id': FORM, 'students': [], 'missing': [], 'late': [], 'elsewhere': [],
                'excused': [], 'problem': None, 'window': None}
        loader = Mock(return_value={'lessons': [{'topic': 'Exact current source', 'forms': [form]}],
                                   'problems': []})
        scorer = Mock(return_value=[])
        with patch.object(morning.completion, 'assignment_readiness', return_value=readiness or {'status': 'READY'}) as ready, \
             patch.object(morning.ledger, 'read', return_value={'configured': True}):
            result = morning.make_list('read', config() if settings is None else settings, self.agenda,
                                       'forensics', week, FORM, loader, scorer)
        return result, loader, scorer, ready

    def assert_held(self, settings):
        catalog = self.catalog(settings)
        self.assertTrue(catalog['problems'])
        self.assertEqual({x['form'] for x in catalog['assignments']}, {FORM, OTHER})
        for week in ('Wk 07', 'Wk 08'):
            result, loader, scorer, ready = self.make(week, settings)
            self.assertEqual(result['code'], 'coverage_incomplete')
            self.assertNotIn('list', result)
            loader.assert_not_called()
            scorer.assert_not_called()
            ready.assert_not_called()

    def test_exact_declared_current_context_preserves_both_agenda_rows(self):
        before = deepcopy(self.agenda.rows)
        result = self.catalog()
        self.assertEqual(result['problems'], [])
        self.assertEqual({x['form']: x['week'] for x in result['assignments']},
                         {FORM: 'Wk 08', OTHER: 'Wk 07'})
        self.assertEqual(self.agenda.rows, before)
        # No chronology heuristic: reversing rows gives the same explicit choice.
        self.agenda.weeks.reverse()
        self.assertEqual(next(x for x in self.catalog()['assignments'] if x['form'] == FORM)['week'], 'Wk 08')

    def test_direct_historical_request_cannot_bypass_catalog(self):
        result, loader, scorer, ready = self.make('Wk 07')
        self.assertEqual(result['code'], 'coverage_incomplete')
        loader.assert_not_called()
        scorer.assert_not_called()
        ready.assert_not_called()
        current, loader, scorer, ready = self.make('Wk 08')
        self.assertEqual(current['code'], 'no_scores')
        self.assertEqual(loader.call_args.args[4], 'Wk 08')
        self.assertEqual(ready.call_args.args[4], 'Wk 08')
        scorer.assert_called_once()

    def test_missing_duplicate_or_multiple_declarations_hold_only_duplicate(self):
        for entries in ([], [declaration(), declaration()],
                        [declaration(), declaration(week='Wk 07', teacher_ids=[OLDER])]):
            with self.subTest(entries=entries):
                self.assert_held(config(entries))
        self.assert_held({})

    def test_stale_foreign_invalid_or_nonexistent_scope_never_selects(self):
        for changes in ({'school_year': '2025-26'}, {'agenda_id': 'FICTIONAL_OTHER_AGENDA'},
                        {'class_key': 'biology'}, {'form_id': OTHER}, {'week': 'Wk 99'},
                        {'teacher_ids': []}, {'teacher_ids': [CURRENT, CURRENT]},
                        {'source_folder_id': ''}):
            with self.subTest(changes=changes):
                self.assert_held(config([declaration(**changes)]))
        self.assert_held({'HUB_FORM_TEACHER_SOURCES': '{bad'})

    def test_future_context_is_not_discarded_before_uniqueness_check(self):
        self.agenda.weeks[1]['monday'] = '2026-10-26'
        self.assert_held(config())
        self.assert_held(config([declaration(), declaration(week='Wk 07', teacher_ids=[OLDER])]))

    def test_excluded_old_duplicate_is_not_resurrected_in_current_context(self):
        settings = config()
        settings['HUB_WEEK_EXCLUSIONS'] = json.dumps([{'school_year': '2026-27',
            'class_key': 'forensics', 'weeks': ['Wk 07'], 'reason': 'Exact teacher exclusion.'}])
        result = self.catalog(settings)
        self.assertEqual(len(result['exclusions']), 1)
        self.assertTrue(result['problems'])
        self.assertEqual(result['assignments'][0]['form'], FORM)
        held, loader, scorer, _ = self.make('Wk 08', settings)
        self.assertEqual(held['code'], 'coverage_incomplete')
        loader.assert_not_called(); scorer.assert_not_called()
        old, _, _, _ = self.make('Wk 07', settings)
        self.assertEqual(old['code'], 'excluded_week')

    def test_same_week_repetition_and_missing_date_are_not_unique(self):
        self.agenda.rows['Wk 08'].append(deepcopy(self.agenda.rows['Wk 08'][0]))
        self.assert_held(config())
        self.agenda = Agenda()
        self.agenda.weeks[1]['monday'] = None
        self.assert_held(config())

    def test_source_unavailable_still_blocks_after_valid_context_resolution(self):
        result, loader, scorer, ready = self.make('Wk 08', readiness={
            'status': 'NOT_READY', 'problem': 'Exact source or parent read unavailable.'})
        self.assertEqual(result['code'], 'coverage_incomplete')
        loader.assert_called_once(); ready.assert_called_once(); scorer.assert_not_called()
        self.assertNotIn('list', result)

    def test_context_fingerprint_changes_without_changing_measured_scores(self):
        row = {'first': 'Ann', 'last': 'Fictional', 'section': 'A2 Fictional',
               'matched': True, 'files': []}
        form = {'form_id': FORM, 'students': [row], 'missing': [], 'late': [], 'elsewhere': [],
                'excused': [], 'problem': None, 'window': None}
        settings = config()
        settings['HUB_IC_ASSIGNMENT_MAP'] = json.dumps([{
            'class_key': 'forensics', 'form_id': FORM, 'targets': []}])
        def run():
            with patch.object(morning.completion, 'assignment_readiness', return_value={'status': 'READY'}), \
                 patch.object(morning.ledger, 'read', return_value={'configured': True}):
                return morning.make_list('read', settings, self.agenda, 'forensics', 'Wk 08', FORM,
                    Mock(return_value={'lessons': [{'topic': 'Current', 'forms': [form]}], 'problems': []}),
                    Mock(return_value=[(row, {'notes_score': 8, 'revision_score': None})]))
        first = run()
        self.assertEqual(first['code'], 'ready')
        self.agenda.rows['Wk 07'][0]['materials'][0]['url'] += '?changed=1'
        second = run()
        self.assertEqual(second['list']['rows'], first['list']['rows'])
        self.assertNotEqual(second['revision'], first['revision'])

    def test_independent_form_still_prepares_while_duplicate_is_held(self):
        form = {'form_id': OTHER, 'students': [], 'missing': [], 'late': [], 'elsewhere': [],
                'excused': [], 'problem': None, 'window': None}
        scorer = Mock(return_value=[])
        with patch.object(morning.completion, 'assignment_readiness', return_value={'status': 'READY'}), \
             patch.object(morning.ledger, 'read', return_value={'configured': True}):
            result = morning.make_list('read', config([]), self.agenda, 'forensics', 'Wk 07', OTHER,
                Mock(return_value={'lessons': [{'topic': 'Independent', 'forms': [form]}], 'problems': []}), scorer)
        self.assertEqual(result['code'], 'no_scores')
        scorer.assert_called_once()

    def test_removed_current_form_does_not_silently_reuse_old_declaration(self):
        # The old request must not gain authority when the selected Form vanishes.
        self.agenda.rows['Wk 08'][0]['forms'] = []
        for week in ('Wk 07', 'Wk 08'):
            result, loader, scorer, _ = self.make(week)
            self.assertEqual(result['code'], 'coverage_incomplete')
            loader.assert_not_called(); scorer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
