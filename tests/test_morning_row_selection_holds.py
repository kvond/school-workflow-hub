"""One uncertain student's history cannot hide another current source.

Fictional local sources only. Existing global readiness and final-entry receipt
guards remain separate from row-level selection review.
"""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import json
import unittest

from hub_v2 import app, completion, google, morning
from test_completion_readiness import FORM, TEMPLATE, source
from test_completion_readiness import single_assignment_agenda as fixture

NOW = datetime(2026, 10, 11, 14, tzinfo=timezone.utc)
VALID = 'FICTIONAL_VALID_CURRENT_123'
HELD = 'FICTIONAL_HELD_CURRENT_123'


def row(last, ident):
    return {'last': last, 'first': 'Alex', 'section': 'A2 Fictional',
            'matched': True, 'source': 'folder', 'matched_by': 'file name',
            'email': '', 'submitted_at': '2026-09-22T14:00:00Z',
            'files': [{'id': ident, 'mime': google.PPTX, 'name': last + ' original.pptx'}]}


def work_fixture():
    held = row('Brook', HELD)
    held['earlier'] = [{'source': 'folder', 'matched_by': 'file name', 'email': '',
                       'submitted_at': '2026-09-20T14:00:00Z',
                       'files': [{'id': 'FICTIONAL_UNORDERED_HISTORY_123',
                                  'mime': google.PPTX, 'name': 'Brook older original.pptx'}]}]
    form = {'form_id': FORM, 'label': 'Observations — Turn in', 'problem': None,
            'students': [row('Avery', VALID), held], 'late': [], 'missing': [],
            'excused': [], 'elsewhere': [], 'grade_readiness': {'status': 'READY'},
            'window': {'from': '2026-09-01T04:00:00Z', 'at': '2026-09-30T03:59:59Z'}}
    return form, {'lessons': [{'topic': 'Observations', 'forms': [form]}], 'problems': []}


class MorningRowSelectionHoldTests(unittest.TestCase):
    def setUp(self):
        self.form, self.found = work_fixture()
        self.config = {'HUB_IC_ASSIGNMENT_MAP': json.dumps([{
            'class_key': 'forensics', 'form_id': FORM, 'targets': []}])}
        self.ready = {'status': 'READY', 'source_resolution': {'decks': [{'id': TEMPLATE}]}}
        self.form['grade_readiness'] = deepcopy(self.ready)
        self.mark = {'configured': True, 'entered_at': {'at': '2026-10-02T12:00:00Z'}}
        completion._results.clear()

    def tearDown(self):
        completion._results.clear()

    def make(self, scorer=app._score_rows, ready=None, mark=None):
        with patch.object(morning, 'datetime', wraps=datetime) as clock, \
                patch.object(morning.ledger, 'read', return_value=self.mark if mark is None else mark), \
                patch.object(completion, 'assignment_readiness', return_value=self.ready if ready is None else ready):
            clock.now.return_value = NOW
            agenda = fixture()
            agenda.week_options = lambda cls: [{'label': 'Wk 04', 'monday': '2026-09-21'}]
            return morning.make_list('fictional-read', self.config, agenda, 'forensics', 'Wk 04',
                                     FORM, Mock(return_value=self.found), scorer)

    def source_patches(self, stack):
        from contextlib import ExitStack
        self.assertIsInstance(stack, ExitStack)
        stack.enter_context(patch.object(completion, 'student_coding_required', return_value=False))
        stack.enter_context(patch.object(completion, 'lesson_sources', return_value=self.ready['source_resolution']))
        stack.enter_context(patch.object(completion, '_teacher_template', return_value=({
            'id': TEMPLATE, 'name': 'Observations', 'version': '1'}, source())))
        metadata = stack.enter_context(patch.object(google, 'drive_file', side_effect=lambda token, fid, fields: {
            'id': fid, 'mimeType': google.PPTX, 'version': '1', 'modifiedTime': 'unchanged'}))
        read = stack.enter_context(patch.object(completion, '_read', return_value=source('I recorded the scene carefully.')))
        return metadata, read

    def test_valid_unique_folder_source_scores_beside_true_unordered_history_check(self):
        from contextlib import ExitStack
        before = deepcopy(self.found)
        with ExitStack() as stack:
            metadata, read = self.source_patches(stack)
            result = self.make()
        self.assertEqual(result['code'], 'ready')
        values = {r['last']: r for r in result['list']['rows']}
        self.assertEqual((values['Avery']['kind'], values['Avery']['notes']), ('score', 10))
        self.assertEqual((values['Brook']['kind'], values['Brook']['notes'], values['Brook']['revision']),
                         ('check', None, None))
        self.assertIn('verified original Form event', values['Brook']['reason'])
        self.assertEqual((values['Brook']['first'], values['Brook']['section']), ('Alex', 'A2 Fictional'))
        self.assertEqual((result['list']['class_name'], result['list']['week'], result['list']['assignment']),
                         ('Forensics', 'Wk 04', 'Observations — Turn in'))
        self.assertEqual([c.args[1] for c in metadata.call_args_list], [VALID])
        read.assert_called_once()
        self.assertFalse(any(r['kind'] == 'zero' for r in result['list']['rows']))
        self.assertEqual(self.found, before)

    def test_direct_request_for_uncertain_file_still_stops_before_source_read(self):
        with patch.object(completion, '_teacher_template') as teacher, \
                patch.object(google, 'drive_file') as metadata, patch.object(completion, '_read') as read:
            result = completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                           FORM, HELD, self.found, include_late=True)
        self.assertEqual(result['code'], 'coverage_incomplete')
        self.assertIn('Forensics · Wk 04 · Observations — Turn in · Brook, Alex (A2 Fictional)', result['problem'])
        self.assertNotIn('notes_score', result)
        teacher.assert_not_called(); metadata.assert_not_called(); read.assert_not_called()

    def test_unknown_file_is_not_authorized_by_an_unrelated_history_hold(self):
        with patch.object(google, 'drive_file') as metadata:
            with self.assertRaises(ValueError):
                completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                      FORM, 'FICTIONAL_UNSELECTED_123', self.found, include_late=True)
        metadata.assert_not_called()

    def test_exact_known_historical_original_of_held_student_returns_that_hold_without_read(self):
        with patch.object(completion, '_teacher_template') as teacher, \
                patch.object(google, 'drive_file') as metadata, patch.object(completion, '_read') as read:
            result = completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                           FORM, 'FICTIONAL_UNORDERED_HISTORY_123', self.found, include_late=True)
        self.assertEqual(result['code'], 'coverage_incomplete')
        self.assertIn('Brook, Alex (A2 Fictional)', result['problem'])
        self.assertIn('verified original Form event', result['problem'])
        self.assertNotIn('notes_score', result)
        teacher.assert_not_called(); metadata.assert_not_called(); read.assert_not_called()

    def test_known_unselected_original_of_verified_student_is_not_graded(self):
        valid = self.form['students'][0]
        valid.update(source='form', matched_by='email', email='alex.avery@fictional.example',
                     submitted_at='2026-09-25T14:00:00Z')
        valid['files'][0].update(source_form_id=FORM, source_response_id='NEW_FICTIONAL_EVENT',
                                 source_submitted_at=valid['submitted_at'])
        old = deepcopy(valid)
        old.pop('earlier', None)
        old.update(submitted_at='2026-09-22T14:00:00Z')
        old['files'][0].update(id='FICTIONAL_VALID_OLD_123', source_response_id='OLD_FICTIONAL_EVENT',
                              source_submitted_at=old['submitted_at'])
        valid['earlier'] = [old]
        selected = completion.recheck_candidates(self.form, now=NOW)[0]
        self.assertNotIn('selection_problem', selected)
        self.assertEqual(selected['files'][0]['id'], VALID)
        with patch.object(completion, '_teacher_template') as teacher, patch.object(google, 'drive_file') as metadata:
            with self.assertRaises(ValueError):
                completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                      FORM, 'FICTIONAL_VALID_OLD_123', self.found, include_late=True)
        teacher.assert_not_called(); metadata.assert_not_called()

    def test_requested_current_file_also_in_another_held_history_keeps_shared_lineage_hold(self):
        from contextlib import ExitStack
        self.form['students'][1]['earlier'][0]['files'][0]['id'] = VALID
        with ExitStack() as stack:
            metadata, read = self.source_patches(stack)
            result = self.make()
        self.assertEqual(result, {'code': 'source_scores_held', 'source_holds': {'checks': 2}})
        metadata.assert_not_called(); read.assert_not_called()

    def test_shared_current_file_remains_held_for_every_attributed_student(self):
        from contextlib import ExitStack
        self.form['students'][1]['files'][0]['id'] = VALID
        with ExitStack() as stack:
            metadata, read = self.source_patches(stack)
            result = self.make()
        self.assertEqual(result, {'code': 'source_scores_held', 'source_holds': {'checks': 2}})
        metadata.assert_not_called(); read.assert_not_called()
        with patch.object(google, 'drive_file') as metadata:
            result = completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                           FORM, VALID, self.found, include_late=True)
        self.assertEqual(result['code'], 'coverage_incomplete')
        self.assertIn('different students', result['problem'])
        metadata.assert_not_called()

    def test_empty_latest_upload_hold_survives_scorer_without_file_indexing(self):
        valid = self.form['students'][0]
        held = self.form['students'][1]
        held.update(source='form', matched_by='email', email='alex.brook@fictional.example',
                    submitted_at='2026-10-05T14:00:00Z', files=[])
        held['earlier'] = [{'source': 'form', 'matched_by': 'email', 'email': held['email'],
                            'submitted_at': '2026-09-22T14:00:00Z', 'files': [{
                                'id': HELD, 'mime': google.PPTX, 'source_form_id': FORM,
                                'source_response_id': 'FICTIONAL_EVENT_123',
                                'source_submitted_at': '2026-09-22T14:00:00Z'}]}]
        with patch.object(completion, 'score_file', return_value={'notes_score': 8}) as score:
            scored = app._score_rows('fictional-read', fixture(), 'forensics', 'Wk 04',
                                     FORM, self.form, self.found, include_late=True)
        self.assertEqual([r['last'] for r, _ in scored], ['Avery', 'Brook'])
        self.assertEqual(scored[1][1]['code'], 'coverage_incomplete')
        self.assertIn('no uploaded file', scored[1][1]['problem'])
        score.assert_called_once()
        self.assertEqual(score.call_args.args[5], valid['files'][0]['id'])

    def test_held_row_cannot_cancel_matching_file_attribution_hold(self):
        # A direct candidate list may carry the shared-file hold on only one row.
        # The requested-file gate must still inspect all matching attributions.
        candidates = deepcopy(self.form['students'])
        candidates[1]['files'][0]['id'] = VALID
        candidates[1]['selection_problem'] = 'Shared source attribution is uncertain.'
        with patch.object(completion, 'scoring_candidates', return_value=candidates), \
                patch.object(google, 'drive_file') as metadata:
            result = completion.score_file('fictional-read', fixture(), 'forensics', 'Wk 04',
                                           FORM, VALID, self.found, include_late=True)
        self.assertEqual(result['code'], 'coverage_incomplete')
        self.assertIn('Shared source attribution', result['problem'])
        metadata.assert_not_called()

    def test_global_provider_teacher_photo_and_submission_resolution_failures_still_stop(self):
        scorer = Mock()
        for ready in ({'status': 'NOT_READY', 'problem': 'Teacher source cannot be read.'},
                      {'status': 'NOT_READY', 'problem': 'Photosynthesis copies need embedded codes.'}):
            with self.subTest(ready=ready):
                self.assertEqual(self.make(scorer, ready=ready)['code'], 'coverage_incomplete')
                scorer.assert_not_called()
        self.found['problems'] = ['Roster provider is unavailable.']
        self.assertEqual(self.make(scorer)['code'], 'coverage_incomplete'); scorer.assert_not_called()
        self.found['problems'] = []
        self.found['other_files'] = [{'files': [{'id': 'FICTIONAL_UNRESOLVED_123'}]}]
        self.assertEqual(self.make(scorer)['code'], 'coverage_incomplete'); scorer.assert_not_called()
        self.found.pop('other_files')
        self.assertEqual(self.make(scorer, mark={'configured': False})['code'], 'receipt_unconfirmed')
        scorer.assert_not_called()

    def test_submitted_deck_readiness_and_final_whole_assignment_record_gate_stay_strict(self):
        scored = [(self.form['students'][0], {'code': 'deck_not_ready', 'problem': 'Current source is unreadable.'})]
        self.assertEqual(self.make(Mock(return_value=scored))['code'], 'coverage_incomplete')
        with patch.object(completion, '_template') as read:
            result = completion.submitted_readiness('fictional-read', self.form,
                                                    require_complete_codes=False, include_late=True)
        self.assertEqual((result['status'], result['code']), ('NOT_READY', 'coverage_incomplete'))
        read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
