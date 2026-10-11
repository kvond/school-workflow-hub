"""A final-entry receipt checks the actual current Form-selected submission."""
from copy import deepcopy
from unittest.mock import Mock, patch
import unittest

from hub_v2 import app, completion, google, ledger, work
from test_completion_readiness import HEADER, source

FORM = 'FICTIONAL_CURRENT_FORM_12345'
OLD = 'FICTIONAL_OLDER_COPY_12345'
NEW = 'FICTIONAL_CURRENT_COPY_12345'


def submission(file_id, at, *, email='ann@fictional.example'):
    return {'matched': True, 'first': 'Ann', 'last': 'Avery', 'section': 'A4 Fictional Biology',
            'source': 'form', 'submitted_at': at, 'matched_by': 'email', 'email': email,
            'files': [{'id': file_id, 'name': file_id, 'mime': completion.SLIDES,
                       'source_form_id': FORM, 'source_response_id': 'RESPONSE_' + file_id,
                       'source_submitted_at': at}]}


def current_form():
    original = submission(OLD, '2001-09-01T12:00:00Z')
    replacement = submission(NEW, '2001-10-02T12:00:00Z')
    replacement['late'] = True
    original['earlier'] = [replacement]
    return {'form_id': FORM, 'label': 'Fictional Biology — Turn in', 'students': [original], 'late': [],
            'window': {'at': '2001-09-30T23:59:00Z'}, 'missing': [], 'excused': []}


def old_conflicting_deck():
    deck = source()
    deck['slides'][0]['units'].append({'key': 'older-other-cycle', 'text': HEADER.replace('F06', 'F07'), 'box': (0, 100, 900, 80)})
    return deck


class CurrentReceiptTests(unittest.TestCase):
    def setUp(self):
        self.form = current_form()
        self.history = deepcopy(self.form)
        self.old = old_conflicting_deck()
        self.current = source('A genuine independently written attempt.')

    def template(self, token, file_id):
        return ({'version': '9'}, self.current if file_id == NEW else self.old)

    def test_current_recheck_reads_only_verified_latest_copy_and_preserves_history(self):
        with patch.object(completion, '_template', side_effect=self.template) as read, patch.object(completion, 'assess') as score:
            result = completion.submitted_readiness('read', self.form, require_complete_codes=False, include_late=True)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual([(c['file_id'], c['revision']) for c in result['copies']], [(NEW, '9')])
        read.assert_called_once_with('read', NEW); score.assert_not_called()
        self.assertEqual(self.form, self.history)

    def test_nonrecheck_gate_retains_original_source_checks(self):
        with patch.object(completion, '_template', side_effect=self.template) as read:
            result = completion.submitted_readiness('read', self.form, require_complete_codes=False)
        self.assertEqual(result['status'], 'NOT_READY')
        read.assert_called_once_with('read', OLD)

    def test_unverified_history_conflicting_identity_and_multiple_current_decks_hold_before_reads(self):
        variants = []
        unverified = deepcopy(self.form); unverified['students'][0]['earlier'][0]['files'][0].pop('source_response_id'); variants.append(unverified)
        conflict = deepcopy(self.form); conflict['students'][0]['earlier'][0]['email'] = 'another@fictional.example'; variants.append(conflict)
        multiple = deepcopy(self.form); multiple['students'][0]['earlier'][0]['files'].append({**multiple['students'][0]['earlier'][0]['files'][0], 'id': 'FICTIONAL_SECOND_COPY_12345'}); variants.append(multiple)
        for form in variants:
            with self.subTest(form=form), patch.object(completion, '_template') as read:
                result = completion.submitted_readiness('read', form, require_complete_codes=False, include_late=True)
            self.assertEqual((result['status'], result['code']), ('NOT_READY', 'coverage_incomplete'))
            self.assertIn('Ann', result['problem']); read.assert_not_called()

    def test_same_latest_time_different_files_and_future_events_hold(self):
        equal = deepcopy(self.form); equal['students'][0]['submitted_at'] = '2001-10-02T12:00:00Z'; equal['students'][0]['files'][0]['source_submitted_at'] = equal['students'][0]['submitted_at']
        future = deepcopy(self.form); future['students'][0]['earlier'][0]['submitted_at'] = '9999-10-02T12:00:00Z'; future['students'][0]['earlier'][0]['files'][0]['source_submitted_at'] = '9999-10-02T12:00:00Z'
        for form in (equal, future):
            with patch.object(completion, '_template') as read:
                result = completion.submitted_readiness('read', form, require_complete_codes=False, include_late=True)
            self.assertEqual(result['code'], 'coverage_incomplete'); read.assert_not_called()

    def test_photosynthesis_strictness_and_current_unreadability_are_not_waived(self):
        for strict, effect in [(True, ({'version': '9'}, source(header='Unmarked older handout'))),
                               (False, completion.Unreadable('Current original unavailable.'))]:
            with patch.object(completion, '_template', side_effect=effect if isinstance(effect, Exception) else None,
                              return_value=None if isinstance(effect, Exception) else effect):
                result = completion.submitted_readiness('read', self.form, require_complete_codes=strict, include_late=True)
            self.assertEqual(result['status'], 'NOT_READY')
            self.assertEqual(result['copies'][0]['file_id'], NEW)

    def endpoint(self, *, mark=None, strict=False, found=None, teacher=None):
        payload = {'class': 'biology', 'week': 'Wk 02', 'form': FORM, 'kind': 'ic'}
        mark = mark if mark is not None else {'configured': True, 'problem': None, 'entered_at': {'at': '2001-10-02T13:00:00Z'}}
        with patch.object(app, '_same_origin_json', return_value=None), patch.object(app, '_json_body', return_value=payload), \
                patch.object(google, 'service_token', return_value='read') as tokens, \
                patch.object(app, 'load_agenda', return_value=Mock()), patch.object(work, 'week_policy', return_value={'excluded': False}), \
                patch.object(app, '_form_context', return_value=({'form_id': FORM}, self.form, found or {})) as context, \
                patch.object(completion, 'assignment_readiness', return_value=teacher or {'status': 'READY'}), \
                patch.object(completion, 'student_coding_required', return_value=strict), \
                patch.object(completion, '_template', side_effect=self.template) as read, \
                patch.object(ledger, 'read', return_value=mark), patch.object(ledger, 'record') as record:
            result = app.record_post({}, lambda status, value: (status, value), {})
        return result, tokens, context, read, record

    def test_record_after_existing_final_entry_uses_fresh_form_and_current_candidate(self):
        result, tokens, context, read, record = self.endpoint()
        self.assertEqual(result[0], '200 OK'); record.assert_called_once()
        self.assertTrue(context.call_args.kwargs['fresh'])
        read.assert_called_once_with('read', NEW)
        self.assertEqual(sum(bool(c.kwargs.get('write')) for c in tokens.call_args_list), 1)
        self.assertEqual(self.form, self.history)

    def test_no_final_entry_or_unreadable_entry_history_does_not_authorize_latest_late_copy(self):
        for mark in ({'configured': True, 'entered_at': {'at': '2001-09-01T12:00:00Z'}},
                     {'configured': True, 'problem': 'Entry history unreadable.'}, {'configured': False}):
            result, tokens, _, read, record = self.endpoint(mark=mark)
            self.assertEqual(result[0], '409 Conflict'); record.assert_not_called()
            read.assert_called_once_with('read', OLD)
            self.assertFalse(any(c.kwargs.get('write') for c in tokens.call_args_list))

    def test_teacher_and_intake_holds_precede_copy_or_record_writes(self):
        for kwargs in ({'teacher': {'status': 'NOT_READY', 'problem': 'Teacher source held.'}},
                       {'found': {'assessment_problem': 'Wrong Form intake still held.'}}):
            result, tokens, _, read, record = self.endpoint(**kwargs)
            self.assertEqual(result[0], '409 Conflict'); read.assert_not_called(); record.assert_not_called()
            self.assertFalse(any(c.kwargs.get('write') for c in tokens.call_args_list))


if __name__ == '__main__': unittest.main()
