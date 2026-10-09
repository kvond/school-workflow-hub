"""Teacher-scoped intake and early-week assessment policy on fictional sources."""
import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from hub_v2 import completion, google, morning, work

TYPES = 'FICTIONAL_TYPES_FORM_123456'
INVESTIGATIVE = 'FICTIONAL_INVEST_FORM_123456'
TIME = '2026-09-22T14:00:00Z'
STUDENT = {'first': 'Ann', 'last': 'Avery', 'section': 'A2 Forensics'}


class Agenda:
    def __init__(self, year=2026):
        self.classes = {k: {'name': k.title(), 'layout': {'rows': [(4, 'Wk 01'), (5, 'Wk 02'), (8, 'Wk 05')]}}
                        for k in ('biology', 'forensics', 'anatomy')}
        self.weeks = [{'label': 'Wk 01', 'monday': '%d-08-31' % year},
                      {'label': 'Wk 02', 'monday': '%d-09-07' % year},
                      {'label': 'Wk 05', 'monday': '%d-09-21' % year}]
        self.lesson = {'row': 8, 'topic': 'Two lessons', 'dates': '', 'plans': [], 'materials': [],
                       'edit_url': 'https://fictional.example/agenda', 'forms': [
                           {'label': 'Types of Evidence — Turn in', 'form_id': TYPES, 'problem': None, 'url': 'types'},
                           {'label': 'Investigative Processes (Steps of CSI) — Turn in', 'form_id': INVESTIGATIVE,
                            'problem': None, 'url': 'investigative'}]}

    def lessons(self, cls, week):
        return [copy.deepcopy(self.lesson)] if week == 'Wk 05' else []

    def week_options(self, cls):
        return self.weeks


def response(*names, stamp=TIME, ident='FICTIONAL_RESPONSE'):
    return {'responseId': ident, 'respondentEmail': 's.ann.avery@school.example', 'lastSubmittedTime': stamp,
            'answers': {'upload': {'fileUploadAnswers': {'answers': [
                {'fileId': 'F' + str(i), 'fileName': name, 'mimeType': completion.SLIDES}
                for i, name in enumerate(names)]}}}}


def route_config():
    return {'HUB_ACCEPTED_FORM_ROUTES': json.dumps([{'school_year': '2026-27', 'class_key': 'forensics',
            'week': 'Wk 05', 'source_form_id': INVESTIGATIVE, 'assignment_form_id': TYPES}])}


def exclusion(cls='biology', year='2026-27', weeks=None):
    return {'HUB_WEEK_EXCLUSIONS': json.dumps([{'school_year': year, 'class_key': cls,
            'weeks': weeks or ['Wk 01', 'Wk 02'], 'reason': 'Teacher excluded these Agenda weeks this year.'}])}


class TeacherIntakeTests(unittest.TestCase):
    def scan(self, config, responses, source=INVESTIGATIVE):
        config = {**config, 'HUB_SCHOOL_EMAIL_DOMAINS': 'school.example'}
        names = {u['fileId']: u['fileName'] for r in responses for a in r['answers'].values()
                 for u in a['fileUploadAnswers']['answers']}
        names.update({TYPES: 'Types of Evidence Form',
                      INVESTIGATIVE: 'Investigative Processes & Types of Evidence Form'})
        with patch.object(work.roster, 'load', return_value=({'forensics': [STUDENT]}, [])), \
                patch.object(google, 'form_responses', side_effect=lambda t, f: responses if f == source else []), \
                patch.object(google, 'drive_file', side_effect=lambda t, f: {'id': f, 'name': names.get(f, f),
                    'mimeType': completion.SLIDES, 'trashed': False}), \
                patch.object(work, 'lesson_folders', return_value=({}, None)), \
                patch.object(work, 'nearby_forms', return_value=[]):
            return work.lesson_work('read-only', config, Agenda(), 'forensics', 'Wk 05')

    def test_approved_intake_preserves_original_form_response_and_submission_date(self):
        found = self.scan(route_config(), [response('Copy of Types of Evidence - Ann Avery')])
        types, investigative = found['lessons'][0]['forms']
        row = types['students'][0]
        file = row['files'][0]
        self.assertEqual((file['id'], file['source_form_id'], file['source_response_id'], file['source_submitted_at']),
                         ('F0', INVESTIGATIVE, 'FICTIONAL_RESPONSE', TIME))
        self.assertEqual(file['assignment_form_id'], TYPES)
        self.assertTrue(file['accepted_intake'])
        self.assertEqual((row['email'], row['matched_by'], row['submitted_at']),
                         ('s.ann.avery@school.example', 'email', TIME))
        self.assertTrue(investigative['elsewhere'][0]['accepted_intake'])
        self.assertEqual(investigative['elsewhere'][0]['source_form_id'], INVESTIGATIVE)
        investigative['window'] = {'from': '2026-09-22T04:00:00Z'}
        values = morning.rows(investigative, [], datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(values[0]['kind'], 'none')
        self.assertIsNone(values[0]['notes'])

    def test_unapproved_or_different_year_routes_remain_review_not_accepted(self):
        for config in ({}, {'HUB_ACCEPTED_FORM_ROUTES': route_config()['HUB_ACCEPTED_FORM_ROUTES'].replace('2026-27', '2027-28')}):
            found = self.scan(config, [response('Copy of Types of Evidence - Ann Avery')])
            self.assertFalse(found['lessons'][0]['forms'][1]['elsewhere'][0]['accepted_intake'])
            found['lessons'][0]['forms'][1]['window'] = {'from': '2026-09-22T04:00:00Z'}
            values = morning.rows(found['lessons'][0]['forms'][1], [], datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(values[0]['kind'], 'check')

    def test_accepted_intake_does_not_hide_another_unresolved_source_for_same_student(self):
        found = self.scan(route_config(), [response('Types of Evidence - Ann Avery')])
        investigative = found['lessons'][0]['forms'][1]
        investigative['window'] = {'from': '2026-09-22T04:00:00Z'}
        for unresolved in ('elsewhere', 'unsorted'):
            form = copy.deepcopy(investigative)
            if unresolved == 'elsewhere':
                form['elsewhere'].append({**form['elsewhere'][0], 'accepted_intake': False})
            else:
                form['missing'][0]['unsorted'] = True
            values = morning.rows(form, [], datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(values[0]['kind'], 'check')

    def test_approval_does_not_route_mixed_or_unknown_files(self):
        for names in [('Types of Evidence - Ann Avery', 'Investigative Processes - Ann Avery'), ('My work - Ann Avery',)]:
            found = self.scan(route_config(), [response(*names)])
            types, investigative = found['lessons'][0]['forms']
            self.assertEqual(types['students'], [])
            self.assertEqual(len(investigative['students'][0]['files']), len(names))
            self.assertFalse(any(f.get('accepted_intake') for f in investigative['students'][0]['files']))

    def test_approval_is_not_reciprocal(self):
        found = self.scan(route_config(), [response('Investigative Processes - Ann Avery')], source=TYPES)
        self.assertFalse(found['lessons'][0]['forms'][0]['elsewhere'][0]['accepted_intake'])

    def test_earlier_response_preserves_its_own_original_form_and_date(self):
        old = response('Types of Evidence - Ann Avery', stamp='2026-09-21T12:00:00Z', ident='OLD_RESPONSE')
        old['answers']['upload']['fileUploadAnswers']['answers'][0]['fileId'] = 'OLD_FILE'
        row = self.scan(route_config(), [old, response('Types of Evidence - Ann Avery')])['lessons'][0]['forms'][0]['students'][0]
        earlier = row['earlier'][0]
        self.assertEqual((earlier['submitted_at'], earlier['files'][0]['source_form_id'],
                          earlier['files'][0]['source_response_id']), ('2026-09-21T12:00:00Z', INVESTIGATIVE, 'OLD_RESPONSE'))

    def test_stale_registered_route_fails_closed(self):
        config = route_config()
        values = json.loads(config['HUB_ACCEPTED_FORM_ROUTES'])
        values[0]['assignment_form_id'] = 'FICTIONAL_REMOVED_FORM_1234'
        config['HUB_ACCEPTED_FORM_ROUTES'] = json.dumps(values)
        found = self.scan(config, [response('Types of Evidence - Ann Avery')])
        self.assertIn('alternate submission Form mapping', found['problems'][0])
        self.assertEqual(found['assessment_problem'], found['problems'][0])
        self.assertFalse(found['lessons'][0]['forms'][1]['elsewhere'][0]['accepted_intake'])
        with patch.object(completion, '_template') as template, patch.object(completion, '_read') as read, \
                patch.object(google, 'drive_file') as metadata:
            score = completion.score_file('token', Agenda(), 'forensics', 'Wk 05', TYPES, 'F0', found)
        self.assertEqual(score['code'], 'coverage_incomplete')
        template.assert_not_called(); read.assert_not_called(); metadata.assert_not_called()


class EarlyWeekPolicyTests(unittest.TestCase):
    def test_no_default_early_week_exemption(self):
        for cls in Agenda().classes:
            for week in ('Wk 01', 'Wk 02'):
                self.assertFalse(work.week_policy({}, Agenda(), cls, week)['excluded'])

    def test_exact_course_year_and_agenda_labels_only(self):
        config = exclusion()
        self.assertTrue(work.week_policy(config, Agenda(), 'biology', 'Wk 01')['excluded'])
        for cls, week, source in [('forensics', 'Wk 01', Agenda()), ('biology', 'Wk 05', Agenda()),
                                  ('biology', 'Week 1', Agenda()), ('biology', 'Cycle 02', Agenda()),
                                  ('biology', 'Wk 01', Agenda(2027))]:
            self.assertFalse(work.week_policy(config, source, cls, week)['excluded'])

    def test_invalid_policy_blocks_assessment_without_inventing_exclusion(self):
        config = {'HUB_WEEK_EXCLUSIONS': '{invalid'}
        policy = work.week_policy(config, Agenda(), 'biology', 'Wk 01')
        self.assertFalse(policy['excluded'])
        self.assertTrue(policy['problem'])
        loader, scorer = Mock(), Mock()
        result = morning.make_list('token', config, Agenda(), 'biology', 'Wk 01', TYPES, loader, scorer)
        self.assertEqual(result['code'], 'coverage_incomplete')
        loader.assert_not_called(); scorer.assert_not_called()

    def test_configured_week_with_missing_or_ambiguous_year_fails_closed(self):
        for weeks in ([], [dict(Agenda().weeks[0]), dict(Agenda(2027).weeks[0])]):
            source = Agenda()
            source.weeks = weeks
            self.assertTrue(work.week_policy(exclusion(), source, 'biology', 'Wk 01')['problem'])
        invalid = exclusion(weeks=['Cycle 02'])
        self.assertTrue(work.week_policy(invalid, Agenda(), 'biology', 'Wk 01')['problem'])

    def test_expired_labels_do_not_block_a_different_school_year(self):
        config = exclusion(weeks=['Wk 16b'])
        policy = work.week_policy(config, Agenda(2027), 'biology', 'Wk 01')
        self.assertFalse(policy['excluded'])
        self.assertIsNone(policy['problem'])
        self.assertTrue(work.week_policy(config, Agenda(), 'biology', 'Wk 01')['problem'])
        routes = json.loads(route_config()['HUB_ACCEPTED_FORM_ROUTES'])
        routes[0]['week'] = 'Wk 16b'
        settings = {'HUB_ACCEPTED_FORM_ROUTES': json.dumps(routes)}
        forms = Agenda().lesson['forms']
        self.assertEqual(work.accepted_form_routes(settings, Agenda(2027), 'forensics', 'Wk 05', forms), (set(), None))
        self.assertTrue(work.accepted_form_routes(settings, Agenda(), 'forensics', 'Wk 05', forms)[1])

    def test_exclusion_stops_morning_scoring_and_inferred_zeros_before_source_reads(self):
        loader, scorer = Mock(), Mock()
        with patch.object(morning.ledger, 'read') as ledger:
            result = morning.make_list('token', exclusion(), Agenda(), 'biology', 'Wk 01', TYPES, loader, scorer)
        self.assertEqual(result['code'], 'excluded_week')
        loader.assert_not_called(); scorer.assert_not_called(); ledger.assert_not_called()

    def test_catalog_omits_only_excluded_assignments_and_explains_scope(self):
        source = Agenda()
        source.lessons = lambda cls, week: [copy.deepcopy(source.lesson)]
        with patch.object(morning.ledger, 'eastern_now', return_value='2026-10-09T08:00:00'):
            catalog = morning.catalog(source, exclusion())
        self.assertEqual([(x['class'], x['week']) for x in catalog['exclusions']], [('biology', 'Wk 01'), ('biology', 'Wk 02')])
        self.assertTrue(any(x['class'] == 'biology' and x['week'] == 'Wk 05' for x in catalog['assignments']))
        self.assertTrue(any(x['class'] == 'forensics' and x['week'] == 'Wk 01' for x in catalog['assignments']))

    def test_historical_sources_still_discovered_and_score_guard_reads_no_decks(self):
        found = TeacherIntakeTests().scan({**route_config(), **exclusion('forensics', weeks=['Wk 05'])},
                                         [response('Types of Evidence - Ann Avery')])
        self.assertTrue(found['week_policy']['excluded'])
        file = found['lessons'][0]['forms'][0]['students'][0]['files'][0]
        with patch.object(completion, '_template') as template, patch.object(completion, '_read') as read, \
                patch.object(google, 'drive_file') as metadata:
            score = completion.score_file('token', Agenda(), 'forensics', 'Wk 05', TYPES, file['id'], found)
        self.assertEqual(score['code'], 'excluded_week')
        template.assert_not_called(); read.assert_not_called(); metadata.assert_not_called()
        self.assertEqual(file['source_submitted_at'], TIME)


if __name__ == '__main__':
    unittest.main()
