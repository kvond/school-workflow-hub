"""Saved views over actual discovery/combined algorithms, with fictional IO."""
from contextlib import ExitStack
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from hub_v2 import combined_assessment as combined, completion, google, roster, saved_completion as saved, teacher_material, work
from tests.test_cloud_state import MemoryCAS
from tests import test_combined_assessment as combined_fixture
from tests.test_hub_v2 import ROSTER, response, single_assignment_agenda


class DiscoverySnapshotIntegrationTests(unittest.TestCase):
    def test_real_normal_discovery_and_read_only_discovery_share_the_saved_basis(self):
        agenda = single_assignment_agenda()
        form_id = 'FICTIONALFORMID1234567890abc'
        source_id, teacher_id = 'FictionalSubmittedDeck001', 'FICTIONALDECK1234567890'
        stamp = '2026-09-22T14:00:00Z'
        config = {'HUB_STATE_BACKEND': 'firestore', 'HUB_SCHOOL_EMAIL_DOMAINS': 'school.example',
                  'HUB_READER_SERVICE_ACCOUNT': 'reader@fictional.example'}
        students, _ = roster.parse_tab(ROSTER, 'forensics', 'A2 Forensics')
        metadata = {fid: {'id': fid, 'name': 'Fictional topic', 'mimeType': completion.SLIDES,
                          'trashed': False, 'version': '7', 'modifiedTime': stamp,
                          'parents': ['FictionalOriginalFolder001']} for fid in (source_id, teacher_id)}
        def meta(token, fid, fields=None):
            return deepcopy(metadata.get(fid, {'id': fid, 'name': 'Fictional topic',
                'mimeType': completion.SLIDES, 'trashed': False, 'parents': [],
                'modifiedTime': stamp, 'version': '7'}))
        receipts = [response('s.ana.rivera@school.example', stamp, (source_id, 'Fictional topic - Ana Rivera'))]
        port = MemoryCAS()
        with ExitStack() as stack:
            stack.enter_context(patch.object(teacher_material, 'configured_store', return_value=port))
            stack.enter_context(patch.object(roster, 'load', side_effect=lambda *_: ({'forensics': deepcopy(students)}, [])))
            stack.enter_context(patch.object(google, 'drive_file', side_effect=meta))
            stack.enter_context(patch.object(google, 'form_responses', side_effect=lambda token, fid: deepcopy(receipts) if fid == form_id else []))
            stack.enter_context(patch.object(google, 'folder_children', return_value=[]))
            stack.enter_context(patch.object(google, 'get_json', side_effect=AssertionError('No external provider request')))
            ready = stack.enter_context(patch.object(completion, 'assignment_readiness', return_value={'status': 'READY'}))
            normal = work.lesson_work('read', config, agenda, 'forensics', 'Wk 04')
            self.assertTrue(ready.called)
            ready.reset_mock()
            readonly = work.lesson_work('read', config, agenda, 'forensics', 'Wk 04', review_only=True)
            ready.assert_not_called()
            self.assertIsNotNone(normal['roster_check'])
            self.assertIsNone(readonly['roster_check'])
            self.assertIn('grade_readiness', normal['lessons'][0]['forms'][0])
            self.assertNotIn('grade_readiness', readonly['lessons'][0]['forms'][0])
            scope = saved._scope(agenda, 'forensics', 'Wk 04', form_id, source_id)
            self.assertEqual(saved._basis(config, agenda, scope, normal), saved._basis(config, agenda, scope, readonly))
            counts = {category: {'done': 1, 'required': 2} for category in ('NOTES', 'BANK', 'DRAFT')}
            result = {'counts': counts, 'notes_score': 5, 'revision_score': 5, 'code': 'C04',
                      'scoring_basis': 'found_codes', 'file_updated': stamp,
                      'template_id': teacher_id, 'template_updated': stamp}
            cache = {(source_id, stamp, '7'): {'template': (teacher_id, stamp, '7'),
                                             'student_coding_required': False, 'result': deepcopy(result)}}
            stack.enter_context(patch.object(saved, 'configured_store', return_value=port))
            stack.enter_context(patch.dict(completion._results, cache, clear=True))
            with saved.recording(config):
                saved.retain('read', agenda, 'forensics', 'Wk 04', form_id, source_id, normal, result)
            self.assertEqual(len(port.rows), 1)
            with patch.object(completion, 'assess', side_effect=AssertionError('Read cannot assess')), \
                    patch.object(google, 'deck_bytes', side_effect=AssertionError('Read cannot export')):
                output = saved.read('read', config, agenda, 'forensics', 'Wk 04', form_id, source_id, readonly)
            self.assertEqual(output['status'], 'saved', output)
            self.assertEqual(output['notes_score'], 5)


class CombinedSnapshotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.port.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.config = {'HUB_STATE_BACKEND': 'firestore',
                       combined.CONFIG: json.dumps([combined_fixture.declaration()])}
        self.agenda = combined_fixture.agenda()
        self.captures = combined_fixture.captures()
        for capture in self.captures.values():
            capture['metadata']['parents'] = [combined_fixture.FOLDER]
        self.raw_form = combined_fixture.form()
        self.actual_assess = completion.assess
        self.bound_form = deepcopy(self.raw_form)
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(self.captures[fid])):
            combined.bind_form('read', self.bound_form, combined_fixture.declaration())
            self.row = completion.scoring_candidates(self.bound_form)[0]
            self.result = combined.score_row('read', self.bound_form, self.row)
        self.assertFalse(self.result.get('problem'), self.result)
        self.assertEqual(self.result['code'], 'combined')
        self.normal = {'class_key': 'forensics', 'week': 'Wk 06', 'problems': [], 'other_files': [],
                       'lessons': [{'forms': [self.bound_form]}]}
        self.readonly = {'class_key': 'forensics', 'week': 'Wk 06', 'problems': [], 'other_files': [],
                         'lessons': [{'forms': [self.raw_form]}]}
        for context in (
                patch.object(saved, 'configured_store', return_value=self.port),
                patch.object(google, 'drive_file', side_effect=lambda token, fid, fields=None: deepcopy(self.captures[fid]['metadata'])),
                patch.object(completion, 'assess', side_effect=AssertionError('Saved read cannot assess')),
                patch.object(combined, 'read_source', side_effect=AssertionError('Saved read cannot capture bytes')),
                patch.object(google, 'deck_bytes', side_effect=AssertionError('Saved read cannot export'))):
            context.start(); self.addCleanup(context.stop)
        with saved.recording(self.config):
            returned = saved.retain('read', self.agenda, 'forensics', 'Wk 06', combined_fixture.FORM,
                                    combined_fixture.S1, self.normal, self.result)
        self.assertIs(returned, self.result)
        self.assertEqual(len(self.port.rows), 2, 'Both exact represented component aliases must be retained')

    def read(self):
        return saved.read('read', self.config, self.agenda, 'forensics', 'Wk 06', combined_fixture.FORM,
                          combined_fixture.S1, self.readonly)

    def assert_hidden(self, result):
        self.assertIn(result['status'], ('stale', 'unavailable'), result)
        self.assertNotIn('counts', result)
        self.assertNotIn('notes_score', result)

    def resave_form(self, raw_form):
        self.raw_form = raw_form
        self.bound_form = deepcopy(raw_form)
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(self.captures[fid])), \
                patch.object(completion, 'assess', side_effect=self.actual_assess):
            combined.bind_form('read', self.bound_form, combined_fixture.declaration())
            self.row = completion.scoring_candidates(self.bound_form)[0]
            self.result = combined.score_row('read', self.bound_form, self.row)
        self.assertEqual(self.result['code'], 'combined')
        self.normal['lessons'][0]['forms'] = [self.bound_form]
        self.readonly['lessons'][0]['forms'] = [self.raw_form]
        self.port.rows.clear()
        with saved.recording(self.config):
            returned = saved.retain('read', self.agenda, 'forensics', 'Wk 06', combined_fixture.FORM,
                                    combined_fixture.S1, self.normal, self.result)
        self.assertIs(returned, self.result)
        self.assertEqual(len(self.port.rows), 2)

    def shared_event_form(self):
        """One actual Form event may carry both differently coded components."""
        first = combined_fixture.event(combined_fixture.S1, '2026-10-02T12:00:00Z')
        second = combined_fixture.event(combined_fixture.S2, first['submitted_at'])['files'][0]
        second['source_response_id'] = first['files'][0]['source_response_id']
        first['files'].append(second)
        value = combined_fixture.form()
        value['students'] = [first]
        return value

    def test_real_combined_result_reads_without_assessing_or_exporting(self):
        output = self.read()
        self.assertEqual(output['status'], 'saved', output)
        self.assertEqual(output['counts'], {name: self.result['counts'][name] for name in ('NOTES', 'BANK', 'DRAFT')})
        self.assertEqual(output['notes_score'], self.result['notes_score'])
        self.assertEqual(output['component_coverage'], self.result['component_coverage'])
        self.assertNotIn('items', output)

    def test_nonanchor_component_version_change_hides_all_combined_scores(self):
        self.captures[combined_fixture.S2]['metadata']['version'] = '2'
        self.assert_hidden(self.read())

    def test_each_teacher_revision_or_parent_change_hides_combined_scores(self):
        for fid, field, value in ((combined_fixture.T1, 'version', '2'),
                                  (combined_fixture.T2, 'parents', ['ChangedFictionalFolder'])):
            original = deepcopy(self.captures[fid]['metadata'])
            self.captures[fid]['metadata'][field] = value
            with self.subTest(fid=fid, field=field):
                self.assert_hidden(self.read())
            self.captures[fid]['metadata'] = original

    def test_new_component_history_changes_selection_basis_and_hides_old_scores(self):
        self.raw_form['students'][0]['earlier'].append(combined_fixture.event(combined_fixture.S3, '2026-10-03T12:00:00Z'))
        self.assert_hidden(self.read())

    def test_reverse_component_event_order_keeps_the_current_raw_file_alias_readable(self):
        newer = combined_fixture.event(combined_fixture.S2, '2026-10-02T12:00:00Z')
        newer['earlier'] = [combined_fixture.event(combined_fixture.S1)]
        value = combined_fixture.form()
        value['students'] = [newer]
        self.resave_form(value)
        self.assertEqual(self.row['files'][0]['id'], combined_fixture.S1)
        self.assertEqual(self.raw_form['students'][0]['files'][0]['id'], combined_fixture.S2)
        output = saved.read_many('read', self.config, self.agenda, 'forensics', 'Wk 06',
                                 combined_fixture.FORM, self.readonly)
        self.assertEqual(output['status'], 'available', output)
        self.assertEqual(set(output['results']), {combined_fixture.S1, combined_fixture.S2})
        self.assertEqual(output['results'][combined_fixture.S2]['status'], 'saved', output)
        self.assertEqual(output['results'][combined_fixture.S2]['notes_score'], self.result['notes_score'])

    def test_batch_reuses_all_combined_component_metadata_and_finally_bookends_it(self):
        self.resave_form(self.shared_event_form())
        calls = Counter()
        def metadata(token, fid, fields=None):
            calls[fid] += 1
            return deepcopy(self.captures[fid]['metadata'])
        with patch.object(google, 'drive_file', side_effect=metadata), \
                patch.object(self.port, 'read', wraps=self.port.read) as cas_read:
            output = saved.read_many('read', self.config, self.agenda, 'forensics', 'Wk 06',
                                     combined_fixture.FORM, self.readonly)
        self.assertEqual(output['status'], 'available', output)
        self.assertEqual(set(output['results']), {combined_fixture.S1, combined_fixture.S2})
        self.assertTrue(all(result['status'] == 'saved' for result in output['results'].values()))
        self.assertEqual(cas_read.call_count, 1)
        self.assertEqual(calls, Counter({fid: 2 for fid in (combined_fixture.S1, combined_fixture.S2,
                                                         combined_fixture.T1, combined_fixture.T2)}))

    def test_final_nonanchor_change_discards_every_result_in_a_shared_metadata_batch(self):
        self.resave_form(self.shared_event_form())
        calls = Counter()
        def metadata(token, fid, fields=None):
            calls[fid] += 1
            value = deepcopy(self.captures[fid]['metadata'])
            if fid == combined_fixture.S2 and calls[fid] > 1:
                value['version'] = '2'
            return value
        with patch.object(google, 'drive_file', side_effect=metadata):
            output = saved.read_many('read', self.config, self.agenda, 'forensics', 'Wk 06',
                                     combined_fixture.FORM, self.readonly)
        self.assertEqual(set(output['results']), {combined_fixture.S1, combined_fixture.S2})
        for result in output['results'].values():
            self.assert_hidden(result)


if __name__ == '__main__':
    unittest.main()
