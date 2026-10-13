"""Exact review display choices preserve fictional originals and source failures."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import review_context as rc
from test_review_context import found, row, scope, FORM, SOURCE, fixture
from test_exact_reasoning_policy import Agenda, settings, REASONING, OBSERVATION


class ReviewActionabilityTests(unittest.TestCase):
    def test_waiting_and_received_are_distinct_without_repair_requests(self):
        empty = rc.build(scope(), found([]))
        self.assertEqual((empty['status'], empty['intake_status'], empty['teacher_actionable']),
                         ('waiting', 'waiting', False))
        received = rc.build(scope(), found())
        self.assertEqual((received['status'], received['intake_status'], received['teacher_actionable']),
                         ('not_assessed', 'received', False))
        self.assertEqual(received['source_selection']['submitted_originals'], 1)

    def test_current_original_issue_is_actionable_and_source_history_is_literal(self):
        source = found([row(selection_problem='Current original history is ambiguous.')])
        before = deepcopy(source)
        result = rc.build(scope(), source)
        self.assertEqual((result['status'], result['intake_status'], result['teacher_actionable']),
                         ('review', 'received', True))
        self.assertEqual(result['notices'][0]['sources'][0]['file_id'], SOURCE)
        self.assertEqual(source, before)

    def test_failed_intake_is_unavailable_never_empty_or_a_teacher_repair_request(self):
        for change in ({'folder_note': 'The exact upload-folder read failed (HTTP 403).'},
                       {'problems': ['Shared Form intake could not be read.']},
                       {'review_unavailable': ['Current source scan failed.']}):
            with self.subTest(change=change), patch.object(rc, 'cached_reviews', side_effect=AssertionError('no cached review')):
                result = rc._read_found('readonly', {}, fixture(), scope(), found([], **change))
            self.assertEqual((result['status'], result['intake_status'], result['teacher_actionable']),
                             ('unavailable', 'unavailable', False))
            self.assertIsNone(result['source_selection']['submitted_originals'])
            self.assertTrue(result['notices'])
            self.assertNotIn('Waiting for submissions', str(result))

    def test_verified_exact_reasoning_policy_precedes_provider_failure_and_cache_reads(self):
        agenda = Agenda()
        current = rc.scope(agenda, settings(agenda), 'forensics', 'Wk 03', REASONING)
        with patch.object(rc, 'cached_reviews', side_effect=AssertionError('no source reads')):
            result = rc._read_found('readonly', settings(agenda), agenda, current,
                                    {'review_unavailable': ['Provider failed']})
        self.assertEqual((result['status'], result['intake_status'], result['teacher_actionable']),
                         ('excluded', 'excluded', False))
        self.assertEqual(result['notices'], [])
        self.assertNotIn('Provider failed', str(result))
        self.assertEqual(rc._unavailable(current, 'Roster read unavailable')['status'], 'excluded')

    def history(self, target=REASONING, *, label='A display label is not a policy', url=None):
        return {'status': 'not_assessed', 'notices': [],
                'source_selection': {'submitted_originals': 1, 'assessment_performed': False},
                'matched_other_lessons': [{'kind': 'matched_other_lesson', 'sources': [{'file_id': SOURCE}],
                    'student': {'verified': True, 'first': 'Alex', 'last': 'Rivera', 'section': 'Fictional'},
                    'target': {'class_key': 'forensics', 'week': 'Wk 03', 'form_id': target, 'lesson': label,
                        'url': url or 'https://docs.google.com/forms/d/' + target + '/edit'}}]}

    def test_exact_excluded_matched_target_moves_to_nonactionable_history_preserving_all_details(self):
        agenda = Agenda()
        current = rc.scope(agenda, settings(agenda), 'forensics', 'Wk 03', OBSERVATION)
        result = self.history()
        before = deepcopy(result)
        output = rc._exclude_resolved_targets(settings(agenda), agenda, current, result)
        self.assertEqual(output['matched_other_lessons'], [])
        self.assertEqual(len(output['excluded_assignment_history']), 1)
        retained = output['excluded_assignment_history'][0]
        self.assertFalse(retained['teacher_actionable'])
        for key, value in before['matched_other_lessons'][0].items():
            self.assertEqual(retained[key], value)
        self.assertEqual(result, before)

    def test_similar_label_wrong_form_url_or_invalid_policy_never_hides_a_target(self):
        agenda = Agenda()
        current = rc.scope(agenda, settings(agenda), 'forensics', 'Wk 03', OBSERVATION)
        cases = [(settings(agenda), agenda, self.history(OBSERVATION, label='Inductive and Deductive Reasoning')),
                 ({}, agenda, self.history()),
                 (settings(agenda), agenda, self.history(url='https://docs.google.com/presentation/d/' + REASONING + '/edit'))]
        invalid = Agenda()
        invalid.rows['Wk 03'][0]['forms'][1]['label'] = 'Mixed topic and reasoning'
        cases.append((settings(invalid), invalid, self.history()))
        for config, actual_agenda, result in cases:
            with self.subTest(config=config, result=result):
                self.assertEqual(rc._exclude_resolved_targets(config, actual_agenda, current, result), result)

    def test_live_review_path_retains_exact_excluded_route_as_history_without_mutating_discovery(self):
        agenda = Agenda()
        config = settings(agenda)
        current = rc.scope(agenda, config, 'forensics', 'Wk 03', OBSERVATION)
        source = found([])
        source['week'] = 'Wk 03'
        form = source['lessons'][0]['forms'][0]
        form.update(form_id=OBSERVATION, missing=[row(files=[])], elsewhere=[{
            'who': 'Rivera, Alex', 'section': 'A2 Fictional', 'accepted_intake': False,
            'source_form_id': OBSERVATION, 'assignment_form_id': REASONING,
            'to': 'Exact excluded lesson', 'url': 'https://drive.google.com/open?id=' + SOURCE}])
        source['lessons'][0]['forms'].append({'form_id': REASONING, 'label': 'Inductive and Deductive Reasoning',
                                             'students': [], 'late': [], 'problem': None})
        before = deepcopy(source)
        with patch.object(rc, 'cached_reviews', return_value=({}, [])), \
             patch.object(rc, '_former_context', side_effect=lambda token, cfg, scope, found, result, roster: result):
            output = rc._read_found('readonly', config, agenda, current, source)
        self.assertEqual(output['matched_other_lessons'], [])
        self.assertEqual(output['excluded_assignment_history'][0]['sources'][0]['file_id'], SOURCE)
        self.assertFalse(output['teacher_actionable'])
        self.assertEqual(source, before)

    def test_non_slide_resources_never_receive_unverified_slide_annotations(self):
        urls = [('folder', 'https://drive.google.com/drive/folders/' + FORM),
                ('form', 'https://docs.google.com/forms/d/' + FORM + '/edit'),
                ('document', 'https://docs.google.com/document/d/' + FORM + '/edit'),
                ('spreadsheet', 'https://docs.google.com/spreadsheets/d/' + FORM + '/edit')]
        for kind, url in urls:
            with self.subTest(kind=kind):
                detail = rc.notice(scope(), {}, 'Source read is unavailable.', files=[{'id': FORM, 'url': url}])
                self.assertEqual(detail['sources'][0]['resource_type'], kind)
                self.assertEqual(detail['slides'], [])
                self.assertEqual(detail['slide_note'], '')
        detail = rc.notice(scope(), row(), 'Slide numbers are not verified.')
        self.assertEqual(detail['sources'][0]['resource_type'], 'file')
        self.assertEqual(detail['slides'][0]['numbers'], [])
        self.assertIn('could not be verified', detail['slide_note'])


if __name__ == '__main__':
    unittest.main()
