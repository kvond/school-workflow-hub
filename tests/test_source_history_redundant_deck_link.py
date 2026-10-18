"""A repeated fictional teacher link preserves only exact saved review history."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

from hub_v2 import completion, google, review_context, source_history_dispositions as dispositions
from school_workflow_hub.core.cloud_state import Document, Snapshot
from test_form_source_bindings_and_case_policy import Agenda, SECOND, STUDENT, TEACHER, binding, metadata, settings
from test_source_history_dispositions import OLDER, captures, configured, receipt

NOW = datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
DECK = 'https://docs.google.com/presentation/d/' + TEACHER + '/edit?slide=id.fictional#slide=id.fictional'
OTHER = 'FICTIONAL_OTHER_TEACHER_12345'


class RedundantDeckHistoryTests(unittest.TestCase):
    def setUp(self):
        self.row, self.found, self.scope, self.roster, _ = captures()
        self.agenda = Agenda()
        self.lesson = self.agenda.rows['Wk 06'][0]
        self.lesson['links'] = {'deck1': self.lesson['materials'][0]['url'], 'deck2': ''}
        self.lesson['materials'].append({'label': 'Registered teacher deck', 'url': DECK})
        self.agenda.rows['Wk 06'].append({'topic': 'Another lesson', 'forms': [], 'materials': [],
                                         'links': {'deck1': '', 'deck2': ''}})
        self.scope['sources'] = [{**review_context.source_link(DECK), 'label': 'Registered teacher source'}]
        self.config = settings()
        self.value, _, self.detail = receipt(native_at=NOW - timedelta(minutes=5))
        self.config['HUB_IC_ASSIGNMENT_MAP'] = json.dumps([{'class_key': 'forensics', 'form_id': SECOND,
                                                         'targets': self.value['native_saved']['targets']}])
        self.capture_history()

    def current(self):
        basis = dispositions.current_basis(self.agenda, self.config, self.scope, self.found, self.row, self.roster)
        basis['native_instruction_sha256'] = self.value['instruction_sha256']
        basis['expected_targets'] = deepcopy(self.value['native_saved']['targets'])
        return basis

    def capture_history(self):
        basis = self.current()
        for field in ('scope', 'roster_sha256', 'candidate_sha256'):
            self.value[field] = deepcopy(basis[field])
        self.value['record_key'] = dispositions.receipt_key(self.value)

    def promote(self):
        self.lesson['links']['deck2'] = DECK

    def verify(self, *, now=NOW, basis=None, detail=None, md=None):
        return dispositions.verify_current(self.value, basis or self.current(), detail or self.detail,
                                           md or {fid: metadata(fid) for fid in (STUDENT, OLDER)}, now=now)

    def test_exact_full_context_reconstruction_accepts_both_receipt_generations(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.setUp()
                if legacy:
                    self.scope['sources'][0].pop('resource_type')
                    self.capture_history()
                    self.scope['sources'][0]['resource_type'] = 'slides'
                self.promote()
                before = deepcopy((self.value, self.agenda.rows, self.scope, self.config))
                current = self.current()
                self.assertNotEqual(self.value['scope'], current['scope'])
                self.assertEqual(self.verify(), self.value)
                self.assertEqual((self.value, self.agenda.rows, self.scope, self.config), before)
                # An exact new-generation receipt remains valid too.
                self.capture_history()
                self.assertEqual(self.verify(), self.value)

    def test_full_url_and_explicit_current_teacher_role_are_required(self):
        for change in ('no_binding', 'other_teacher', 'no_scope_source', 'other_scope_source',
                       'different_anchor', 'different_query', 'material_in_other_lesson', 'no_material'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'no_binding':
                    self.config.pop('HUB_FORM_TEACHER_SOURCES')
                elif change == 'other_teacher':
                    self.config.update(settings({**binding(), 'teacher_ids': [OTHER]}))
                elif change == 'no_scope_source':
                    self.scope['sources'] = []
                elif change == 'other_scope_source':
                    self.scope['sources'][0]['file_id'] = OTHER
                elif change == 'different_anchor':
                    self.lesson['materials'][-1]['url'] = DECK.replace('#slide=id.fictional', '#slide=id.other')
                elif change == 'different_query':
                    self.lesson['materials'][-1]['url'] = DECK.replace('?slide=id.fictional', '?slide=id.other')
                elif change == 'material_in_other_lesson':
                    self.agenda.rows['Wk 06'][1]['materials'].append(self.lesson['materials'].pop())
                else:
                    self.lesson['materials'].pop()
                # The historical context is otherwise exactly reconstructible:
                # a missing eligibility gate would incorrectly accept it.
                self.capture_history()
                self.promote()
                self.assertNotIn('empty_deck2_sha256', self.current()['scope_compatibility'])
                with self.assertRaises(ValueError):
                    self.verify()

    def test_non_native_or_malformed_url_cannot_be_promoted(self):
        for url in ('https://drive.google.com/file/d/' + TEACHER + '/view',
                    'https://docs.google.com/document/d/' + TEACHER + '/edit',
                    'http://docs.google.com/presentation/d/' + TEACHER + '/edit',
                    DECK + ' ', DECK.replace('docs.google.com', 'docs.google.com.fictional.example')):
            with self.subTest(url=url):
                self.setUp()
                self.lesson['materials'][-1]['url'] = url
                self.capture_history()
                self.lesson['links']['deck2'] = url
                with self.assertRaises(ValueError):
                    self.verify()

    def test_absent_null_or_nonempty_old_slot_is_not_guessed(self):
        for old in ('absent', None, DECK.replace('id.fictional', 'id.previous')):
            with self.subTest(old=old):
                self.setUp()
                if old == 'absent':
                    self.lesson['links'].pop('deck2')
                else:
                    self.lesson['links']['deck2'] = old
                self.capture_history()
                self.promote()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_every_other_context_operand_stays_exact(self):
        for change in ('topic', 'material_label', 'material_anchor', 'teacher_added', 'teacher_removed',
                       'teacher_folder', 'source_id', 'source_label', 'source_url', 'grade_policy',
                       'form_id', 'form_label', 'mapping', 'deck1', 'other_lesson', 'second_promotion', 'new_file'):
            with self.subTest(change=change):
                self.setUp()
                self.promote()
                if change == 'topic':
                    self.lesson['topic'] = 'Changed topic'
                elif change == 'material_label':
                    self.lesson['materials'][-1]['label'] = 'Different role'
                elif change == 'material_anchor':
                    self.lesson['materials'][-1]['url'] = DECK.replace('id.fictional', 'id.other')
                    self.lesson['links']['deck2'] = self.lesson['materials'][-1]['url']
                elif change == 'teacher_added':
                    self.config.update(settings({**binding(), 'teacher_ids': [TEACHER, OTHER]}))
                elif change == 'teacher_removed':
                    self.config.pop('HUB_FORM_TEACHER_SOURCES')
                elif change == 'teacher_folder':
                    self.config.update(settings({**binding(), 'source_folder_id': 'FICTIONAL_OTHER_FOLDER_12345'}))
                elif change in ('source_id', 'source_label', 'source_url'):
                    field = {'source_id': 'file_id', 'source_label': 'label', 'source_url': 'url'}[change]
                    self.scope['sources'][0][field] = OTHER
                elif change == 'grade_policy':
                    self.scope['grade_policy']['changed'] = True
                elif change == 'form_id':
                    self.scope['form_id'] = 'FICTIONAL_OTHER_FORM_12345'
                elif change == 'form_label':
                    self.lesson['forms'][1]['label'] = 'Changed lesson Form'
                elif change == 'mapping':
                    maps = json.loads(self.config['HUB_IC_ASSIGNMENT_MAP'])
                    maps[0]['targets'][0]['assignment_id'] = '999'
                    self.config['HUB_IC_ASSIGNMENT_MAP'] = json.dumps(maps)
                elif change == 'deck1':
                    self.lesson['links']['deck1'] = DECK
                elif change == 'other_lesson':
                    self.agenda.rows['Wk 06'][1]['topic'] = 'Changed unrelated lesson'
                elif change == 'second_promotion':
                    self.agenda.rows['Wk 06'][1]['links']['deck2'] = DECK
                else:
                    new_url = DECK.replace(TEACHER, OTHER)
                    self.lesson['materials'][-1]['url'] = self.lesson['links']['deck2'] = new_url
                    self.scope['sources'][0] = {**review_context.source_link(new_url), 'label': 'Registered teacher source'}
                    self.config.update(settings({**binding(), 'teacher_ids': [OTHER]}))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_duplicate_form_or_promotion_in_unrelated_lesson_is_rejected(self):
        for change in ('duplicate_same_lesson', 'duplicate_other_lesson', 'unrelated_promotion'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'unrelated_promotion':
                    self.agenda.rows['Wk 06'][1]['materials'] = deepcopy(self.lesson['materials'])
                    self.capture_history()
                    self.agenda.rows['Wk 06'][1]['links']['deck2'] = DECK
                else:
                    self.promote()
                    index = 0 if change == 'duplicate_same_lesson' else 1
                    self.agenda.rows['Wk 06'][index]['forms'].append(deepcopy(self.lesson['forms'][1]))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_identity_roster_candidate_event_instruction_target_and_issue_guards_remain(self):
        for change in ('email', 'identity', 'roster', 'new_original', 'original_event',
                       'instruction', 'target', 'issue_kind', 'issue_reason', 'scope_binding'):
            with self.subTest(change=change):
                self.setUp()
                self.promote()
                detail = deepcopy(self.detail)
                if change == 'email':
                    self.row['email'] = 'other@fictional.example'
                elif change == 'identity':
                    self.row['matched'] = False
                elif change == 'roster':
                    self.roster[0]['forensics'].append({'first': 'Other', 'last': 'Fictional', 'section': 'A2 Fictional'})
                elif change == 'new_original':
                    self.row['earlier'].append({'files': [{'id': 'FICTIONAL_NEW_ORIGINAL_12345'}]})
                elif change == 'original_event':
                    self.row['files'][0]['source_response_id'] = 'fictional-new-event'
                elif change == 'issue_kind':
                    detail['issue_kind'] = 'source_issue'
                elif change == 'issue_reason':
                    detail['reason'] = 'A new source question.'
                with self.assertRaises(ValueError):
                    basis = self.current()
                    if change == 'instruction':
                        basis['native_instruction_sha256'] = 'b' * 64
                    elif change == 'target':
                        basis['expected_targets'][0]['assignment_id'] = '999'
                    elif change == 'scope_binding':
                        basis['scope']['context_sha256'] = 'c' * 64
                    self.verify(basis=basis, detail=detail)

    def test_raw_source_metadata_stays_exact(self):
        self.promote()
        for change in ({'version': '5'}, {'parents': ['FICTIONAL_OTHER_PARENT_12345']}, {'trashed': True},
                       {'md5Checksum': 'different-content'}, {'modifiedTime': '2026-10-15T15:59:00Z'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                md = {fid: metadata(fid) for fid in (STUDENT, OLDER)}
                md[STUDENT].update(change)
                self.verify(md=md)

    def test_maximum_readback_still_expires_after_thirty_minutes_or_eastern_midnight(self):
        self.value['basis'] = 'maximum_saved_as_of'
        self.capture_history()
        self.promote()
        self.verify()
        self.verify(now=NOW + timedelta(minutes=25))
        with self.assertRaises(ValueError):
            self.verify(now=NOW + timedelta(minutes=25, seconds=1))
        at = datetime(2026, 10, 16, 3, 55, tzinfo=timezone.utc)
        self.value['verified_at'] = at.isoformat()
        self.value['native_saved']['verified_at'] = at.isoformat()
        self.value['native_saved']['save_verified_at'] = at.isoformat()
        self.value['record_key'] = dispositions.receipt_key(self.value)
        self.verify(now=at)
        with self.assertRaises(ValueError):
            self.verify(now=at + timedelta(minutes=10))
        with self.assertRaises(ValueError):
            self.verify(now=at - timedelta(seconds=1))

    def test_review_read_keeps_as_of_evidence_without_writes_or_regrading(self):
        at = (NOW - timedelta(days=10)).isoformat()
        self.value['verified_at'] = at
        self.value['native_saved']['verified_at'] = at
        self.value['native_saved']['save_verified_at'] = at
        self.capture_history()
        self.promote()
        before = deepcopy((self.value, self.found, self.scope, self.agenda.rows))
        store = Mock()
        store.read.return_value = Snapshot({self.value['record_key']: Document(self.value, '1')}, NOW.isoformat())
        with patch.object(dispositions, 'configured_store', return_value=store), \
             patch.object(dispositions, '_source_metadata', side_effect=lambda token, fid, fields: metadata(fid)), \
             patch.object(completion, 'assess') as assess, patch.object(completion, 'score_file') as score, \
             patch.object(google, 'deck_bytes') as byte_read, patch.object(google, 'append_rows') as append:
            output = review_context._read_found('read', {**self.config, **configured(self.value)}, self.agenda,
                                                self.scope, self.found, roster_snapshot=self.roster)
        self.assertEqual(output['status'], 'history_retained')
        self.assertEqual(output['notices'], [])
        self.assertIn(at, output['retained_history'][0]['reason'])
        self.assertNotIn('currently', output['retained_history'][0]['reason'])
        self.assertFalse(output['source_selection']['assessment_performed'])
        self.assertEqual((self.value, self.found, self.scope, self.agenda.rows), before)
        assess.assert_not_called()
        score.assert_not_called()
        byte_read.assert_not_called()
        append.assert_not_called()
        store.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
