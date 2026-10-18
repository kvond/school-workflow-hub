"""Native readiness is limited to the exact ordinary Agenda teacher, never a sibling."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch
from hub_v2 import audit, completion, google, work
from test_native_teacher_source import TEMPLATE, metadata, native

FORM = 'FICTIONAL_FORM_1234567890'
FOLDER = 'FICTIONAL_FOLDER_1234567890'


class OrdinaryNativeAuditTests(TestCase):
    def run_audit(self, *, change=None, exported=403, drift=None, held=False, policy=False, native_value=None, sibling=False):
        completion._templates.clear(); completion._native_teacher_templates.clear()
        meta = metadata(parents=[FOLDER])
        folder = {'id': FOLDER, 'name': 'Teacher folder', 'mimeType': google.FOLDER,
                  'trashed': False, 'version': '1', 'modifiedTime': 'same'}
        lesson = {'topic': 'Fictional tissue', 'forms': [{'form_id': FORM}], 'materials': [
            {'label': 'Teacher deck', 'url': 'https://docs.google.com/presentation/d/' + TEMPLATE + '/edit'}]}
        source = {'decks': [{'id': TEMPLATE, 'metadata': deepcopy(meta)}], 'folders': [
            {'id': FOLDER, 'name': 'Teacher folder', 'deck_ids': [TEMPLATE]}], 'stale_links': [], 'problem': None}
        if change: change(lesson, source, meta)
        ag = Mock(); ag.classes = {'anatomy': {'name': 'Anatomy', 'cells': {},
            'layout': {'columns': {}, 'rows': [(8, 'Wk 08')]}}}
        ag.week_for.return_value = {'label': 'Wk 08'}
        other = deepcopy(lesson)
        other['forms'].append({'form_id': 'SIBLING_FORM_123'})
        if sibling: ag.classes['anatomy']['layout']['rows'].append((9, 'Wk 09'))
        calls = 0
        def resolve(*_args, **kwargs):
            nonlocal calls
            calls += bool(kwargs.get('metadata_fields'))
            value = deepcopy(source)
            if drift == 'collection' and calls > 1: value['folders'][0]['name'] = 'Changed'
            return value
        def get(_token, fid, *_args):
            value = deepcopy(folder if fid == FOLDER else meta)
            if drift == 'folder' and calls > 1 and fid == FOLDER: value['version'] = '2'
            if drift == 'consumed' and fid == TEMPLATE: value['version'] = '2'
            return value
        with patch.object(audit, 'build_lesson', side_effect=lambda _c,_l,row,_e: deepcopy(other if sibling and row == 9 else lesson)), patch.object(audit, '_titles', return_value={}), \
             patch.object(audit, '_published', return_value={}), patch.object(work, 'form_teacher_sources', return_value=None), \
             patch.object(work, 'week_policy', return_value={'excluded': policy, 'problem': None, 'school_year': '2026-27', 'reason': ''}), \
             patch.object(completion, 'lesson_sources', side_effect=resolve), patch.object(google, 'drive_file', side_effect=get), \
             patch.object(google, 'deck_bytes', side_effect=google.GoogleError('Export unavailable', exported)), \
             patch.object(google, 'native_presentation', return_value=native() if native_value is None else native_value) as native_get, \
             patch.object(audit.completion_holds, 'check', return_value={'held': held, 'label': 'Held', 'problem': 'Teacher hold', 'action': 'Review'}), \
             patch.object(google, 'append_rows') as writes:
            rows = audit.check('read', ag, 'anatomy', {})['rows']
            result = [r['release'] for r in rows] if sibling else rows[0]['release']
        writes.assert_not_called()
        return result, native_get

    def test_exact_single_form_teacher_uses_bounded_native_read(self):
        result, get = self.run_audit()
        self.assertEqual(result['status'], 'READY'); get.assert_called_once_with('read', TEMPLATE)
        self.assertFalse(result['student_copies_checked']); self.assertNotIn('_teacher_source_metadata', str(result))

    def test_missing_or_ambiguous_teacher_proof_preserves_export_error(self):
        cases = [lambda l,s,m:l['forms'].append({'form_id': 'SIBLING_FORM_123'}),
                 lambda l,s,m:l['forms'].append(deepcopy(l['forms'][0])),
                 lambda l,s,m:l['materials'][0].update(label='Student copy'),
                 lambda l,s,m:s['decks'][0]['metadata'].update(id='FORGED_ID'),
                 lambda l,s,m:s['decks'][0]['metadata'].update(trashed=True),
                 lambda l,s,m:s['decks'][0]['metadata'].pop('version'),
                 lambda l,s,m:s['decks'].append(deepcopy(s['decks'][0]))]
        for case in cases:
            with self.subTest(case=case):
                result, get = self.run_audit(change=case)
                self.assertEqual(result['status'], 'NOT_READY'); get.assert_not_called()
                self.assertIn('Export unavailable', result['problem'])

    def test_source_and_folder_and_consumed_metadata_changes_fail_closed(self):
        for drift in ('collection', 'folder', 'consumed'):
            with self.subTest(drift=drift):
                result, get = self.run_audit(drift=drift)
                self.assertEqual(result['status'], 'NOT_READY'); self.assertTrue(get.called)
                self.assertRegex(result['problem'], 'changed|differs')

    def test_holds_policy_other_http_and_unsupported_native_content_cannot_become_ready(self):
        for options in ({'held': True}, {'policy': True}, {'exported': 404},
                        {'native_value': {'presentationId': TEMPLATE, 'slides': [None]}}):
            with self.subTest(options=options):
                result, get = self.run_audit(**options)
                self.assertEqual(result['status'], 'NOT_READY')
                if 'native_value' not in options: get.assert_not_called()

    def test_same_deck_native_success_cannot_be_borrowed_by_ambiguous_sibling_row(self):
        results, get = self.run_audit(sibling=True)
        self.assertEqual(results[0]['status'], 'READY')
        self.assertEqual(results[1]['status'], 'NOT_READY')
        self.assertIn('Export unavailable', results[1]['problem'])
        get.assert_called_once_with('read', TEMPLATE)
