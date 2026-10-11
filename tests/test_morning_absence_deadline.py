"""Missing-work zeros require a passed verified deadline, never just a start."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import json
import unittest

from hub_v2 import morning
from test_hub_morning import FORM, ROW

START = datetime(2026, 10, 5, 4, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 10, 19, 3, 59, 59, tzinfo=timezone.utc)


class MissingWorkDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.form = deepcopy(FORM)
        self.form.update(students=[], missing=[deepcopy(ROW)], late=[], excused=[], elsewhere=[],
                         window={'from': START.isoformat(), 'at': CUTOFF.isoformat()})

    def check_none(self, now, *, recheck=False):
        before = deepcopy(self.form)
        values = morning.rows(self.form, [], now, recheck=recheck)
        self.assertEqual(len(values), 1)
        self.assertEqual((values[0]['kind'], values[0]['notes'], values[0]['revision']), ('none', None, None))
        self.assertEqual(self.form, before)
        return values[0]

    def test_before_start_and_open_two_week_window_never_prepare_absence_zero(self):
        for now in (START - timedelta(seconds=1), START, START + timedelta(days=1), CUTOFF - timedelta(seconds=1)):
            with self.subTest(now=now):
                self.check_none(now)

    def test_exact_deadline_is_not_past_deadline(self):
        result = self.check_none(CUTOFF)
        self.assertIn('deadline has not passed', result['reason'])

    def test_only_after_valid_cutoff_retains_explicit_missing_zero_policy(self):
        before = deepcopy(self.form)
        values = morning.rows(self.form, [], CUTOFF + timedelta(microseconds=1))
        self.assertEqual((values[0]['kind'], values[0]['notes'], values[0]['revision']), ('zero', 0, 0))
        self.assertEqual(self.form, before)

    def test_missing_malformed_or_inconsistent_window_never_invents_deadline_zero(self):
        for window in (None, {}, [], 'unconfirmed', {'from': START.isoformat()}, {'at': 'unreadable'},
                       {'from': 'unreadable', 'at': CUTOFF.isoformat()},
                       {'from': (CUTOFF + timedelta(days=1)).isoformat(), 'at': CUTOFF.isoformat()}):
            with self.subTest(window=window):
                self.form['window'] = window
                value = self.check_none(CUTOFF + timedelta(days=2))
                self.assertIn('unconfirmed', value['reason'])

    def test_entered_assignment_recheck_never_creates_new_missing_zero_after_cutoff(self):
        for now in (START, CUTOFF, CUTOFF + timedelta(days=1)):
            with self.subTest(now=now):
                value = self.check_none(now, recheck=True)
                self.assertIn('no missing-work zero', value['reason'])

    def test_real_intake_checks_other_assignment_and_excused_rows_still_cannot_be_zero(self):
        self.form['missing'] = [dict(ROW, last='Unsorted', unsorted=True),
                                dict(ROW, last='Wronglesson'), dict(ROW, last='Accepted')]
        self.form['elsewhere'] = [{'who': 'Wronglesson, Ann', 'section': ROW['section']},
                                  {'who': 'Accepted, Ann', 'section': ROW['section'], 'accepted_intake': True}]
        self.form['excused'] = [dict(ROW, last='Excused')]
        for now in (START + timedelta(days=1), CUTOFF + timedelta(seconds=1)):
            with self.subTest(now=now):
                values = {r['last']: r for r in morning.rows(self.form, [], now)}
                self.assertEqual({k: v['kind'] for k, v in values.items()}, {
                    'Unsorted': 'check', 'Wronglesson': 'check', 'Accepted': 'none', 'Excused': 'excused'})
                self.assertTrue(all(r['notes'] is None and r['revision'] is None for r in values.values()))

    def test_current_numeric_work_and_missing_code_advisory_are_unchanged_while_absence_waits(self):
        current = dict(ROW, last='Current', files=[])
        self.form['students'] = [current]
        numeric = {'notes_score': 7.5, 'revision_score': 10,
                   'missing_code_warning': 'Uses only the coded boxes actually found.'}
        values = {r['last']: r for r in morning.rows(self.form, [(current, numeric)], START + timedelta(days=1))}
        self.assertEqual((values['Current']['kind'], values['Current']['notes'], values['Current']['revision']),
                         ('score', 7.5, 10))
        self.assertEqual((values['Avery']['kind'], values['Avery']['notes'], values['Avery']['revision']),
                         ('none', None, None))

    def test_make_list_does_not_emit_zero_only_list_until_verified_deadline_passed(self):
        agenda = Mock(classes={'biology': {'name': 'Fictional Biology'}})
        found = {'lessons': [{'topic': 'Exact fictional lesson', 'forms': [self.form]}], 'problems': []}
        config = {'HUB_IC_ASSIGNMENT_MAP': json.dumps([{'class_key': 'biology',
                 'form_id': self.form['form_id'], 'targets': []}])}
        for now, expected in ((CUTOFF, 'no_scores'), (CUTOFF + timedelta(seconds=1), 'ready')):
            with self.subTest(now=now), patch.object(morning, 'datetime', wraps=datetime) as clock, \
                    patch.object(morning.work, 'week_policy', return_value={'problem': None, 'excluded': False}), \
                    patch.object(morning.completion, 'assignment_readiness', return_value={'status': 'READY'}), \
                    patch.object(morning.ledger, 'read', return_value={'configured': True}):
                clock.now.return_value = now
                value = morning.make_list('fictional-read', config, agenda, 'biology', 'Wk 06',
                                          self.form['form_id'], Mock(return_value=found), Mock(return_value=[]))
                self.assertEqual(value['code'], expected)
                if expected == 'ready':
                    self.assertEqual(value['list']['status'], 'closed')
                    self.assertEqual([(r['kind'], r['notes'], r['revision']) for r in value['list']['rows']],
                                     [('zero', 0, 0)])
                else: self.assertNotIn('list', value)


if __name__ == '__main__':
    unittest.main()
