"""Fictional exact Form collections remain separate in read-only Agenda readiness."""
from copy import deepcopy
from datetime import date
import json
import unittest
from unittest.mock import patch
from hub_v2 import audit, completion, google
from test_hub_v2 import cell, fixture, handout

FIRST = 'FICTIONALFORMID1234567890abc'
SECOND = 'FICTIONALFORMID0987654321xyz'
SEARCH = 'FICTIONAL_SEARCH_TEACHER_12345'
PHOTO = 'FICTIONAL_PHOTO_TEACHER_12345'
EVIDENCE = 'FICTIONAL_EVIDENCE_TEACHER_12345'
SKETCH = 'FICTIONAL_SKETCH_TEACHER_12345'
FIRST_FOLDER = 'FICTIONAL_FIRST_FOLDER_12345'
SECOND_FOLDER = 'FICTIONAL_SECOND_FOLDER_12345'
GROUPS = {FIRST: (FIRST_FOLDER, [SEARCH, PHOTO]), SECOND: (SECOND_FOLDER, [EVIDENCE, SKETCH])}


def declaration(fid):
    folder, teachers = GROUPS[fid]
    return dict(school_year='2026-27', agenda_id='FICTIONALSHEET', class_key='forensics',
                week='Wk 04', form_id=fid, source_folder_id=folder, teacher_ids=teachers)


def metadata(fid):
    titles = {FIRST: 'Searching and Photographing — Turn in', SECOND: 'Sketching and Evidence — Turn in',
              SEARCH: 'Week 04 Search', PHOTO: 'Week 04 Photography',
              EVIDENCE: 'Week 05 Evidence', SKETCH: 'Week 05 Sketching',
              FIRST_FOLDER: 'Actual first collection', SECOND_FOLDER: 'Actual second collection'}
    folder = next((folder for folder, ids in GROUPS.values() if fid in ids), None)
    return dict(id=fid, name=titles.get(fid, 'Fictional resource'),
                mimeType=google.FOLDER if fid in (FIRST_FOLDER, SECOND_FOLDER) else
                    'application/vnd.google-apps.form' if fid in GROUPS else completion.SLIDES,
                trashed=False, parents=[folder] if folder else [], version='4', modifiedTime='2032-01-01T12:00:00Z')


class FormAuditReadinessTests(unittest.TestCase):
    def setUp(self):
        self.agenda = fixture()
        entry = self.agenda.classes['forensics']
        cols, cells = entry['layout']['columns'], entry['cells']
        cells[5, cols['teacher_url']] = cell('https://docs.google.com/presentation/d/' + SEARCH + '/edit')
        cells[5, cols['student_url']] = cell('https://docs.google.com/presentation/d/' + SEARCH + '/copy')
        cells[5, cols['folder_url']] = cell('https://drive.google.com/drive/folders/' + FIRST_FOLDER)
        cols['second_deck'] = 22
        cells[5, 22] = cell('https://docs.google.com/presentation/d/' + EVIDENCE + '/edit')
        self.config = {'HUB_FORM_TEACHER_SOURCES': json.dumps([declaration(FIRST), declaration(SECOND)])}
        self.changed = {}

    def run_check(self, *, configured=True, export_error=None, consume=None, check_hold=None):
        template = completion.read_deck(handout())
        def drive(_t, fid, _fields=None):
            return {**metadata(fid), **self.changed.get(fid, {})}
        def teacher(_t, fid):
            meta = drive(_t, fid)
            if consume:
                consume(fid, meta)
            return meta, deepcopy(template)
        with patch.object(google, 'drive_file', side_effect=drive), \
             patch.object(google, 'form_info', side_effect=lambda _t, fid: {'info': {'title': metadata(fid)['name']}}), \
             patch.object(google, 'folder_children', side_effect=lambda _t, fid: [metadata(i) for i in GROUPS[FIRST if fid == FIRST_FOLDER else SECOND][1]]), \
             patch.object(completion, '_teacher_template', side_effect=teacher) as templates, \
             patch.object(google, 'deck_bytes', side_effect=export_error, return_value=handout()) as exports, \
             patch.object(completion, 'score_file', side_effect=AssertionError('audit must not assess student work')), \
             patch.object(google, 'append_rows', side_effect=AssertionError('audit must not write')), \
             patch.object(google, 'update_cells', side_effect=AssertionError('audit must not write')), \
             patch.object(audit, 'date') as clock:
            clock.today.return_value = date(2026, 9, 23)
            if check_hold:
                with patch.object(audit.completion_holds, 'check', side_effect=check_hold):
                    result = audit.check('readonly', self.agenda, 'forensics', self.config if configured else {})
            else:
                result = audit.check('readonly', self.agenda, 'forensics', self.config if configured else {})
        return next(r for r in result['rows'] if r['week'] == 'Wk 04'), result, templates, exports

    def forms(self, row):
        return {r['form_id']: r for r in row['release']['form_releases']}

    def test_two_forms_use_their_own_current_collections_not_primary_folder_union(self):
        before = deepcopy(self.agenda.classes['forensics']['cells'])
        row, result, templates, exports = self.run_check()
        self.assertEqual(row['release']['status'], 'READY')
        self.assertEqual(row['release']['scope'], 'form_collections')
        checks = self.forms(row)
        for fid, (folder, teachers) in GROUPS.items():
            value = checks[fid]
            self.assertEqual(value['status'], 'READY')
            self.assertEqual([d['deck_id'] for d in value['linked_decks']], teachers)
            self.assertEqual([f['id'] for f in value['source_resolution']['folders']], [folder])
            self.assertFalse(value['student_copies_checked'])
            self.assertNotIn('_teacher_source_metadata', str(value))
        self.assertEqual(set(c.args[1] for c in templates.call_args_list), {SEARCH, PHOTO, EVIDENCE, SKETCH})
        exports.assert_not_called()
        self.assertFalse(any(f['kind'] in ('stale-deck-link', 'deck-week') for f in result['flags']))
        self.assertEqual(self.agenda.classes['forensics']['cells'], before)

    def test_missing_agenda_links_are_separate_exact_informational_discrepancies(self):
        row, _, _, _ = self.run_check()
        checks = self.forms(row)
        self.assertEqual(checks[FIRST]['agenda_link_discrepancy']['source_ids'], [PHOTO])
        self.assertEqual(checks[FIRST]['agenda_link_discrepancy']['folder_ids'], [])
        self.assertEqual(checks[SECOND]['agenda_link_discrepancy']['source_ids'], [SKETCH])
        self.assertEqual(checks[SECOND]['agenda_link_discrepancy']['folder_ids'], [SECOND_FOLDER])
        self.assertNotIn('next_action', checks[FIRST]['agenda_link_discrepancy'])
        self.assertEqual(row['release']['issues'], [])

    def test_unregistered_primary_folder_cannot_block_verified_form_collections(self):
        entry = self.agenda.classes['forensics']
        entry['cells'][5, entry['layout']['columns']['folder_url']] = cell('https://drive.google.com/drive/folders/FICTIONAL_OLD_FOLDER_12345')
        row, _, _, _ = self.run_check()
        self.assertEqual(row['release']['status'], 'READY')
        self.assertEqual(self.forms(row)[FIRST]['agenda_link_discrepancy']['folder_ids'], [FIRST_FOLDER])

    def test_bound_native_fallback_is_not_borrowed_by_unbound_form(self):
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps([{**declaration(FIRST), 'teacher_ids': [SEARCH]}])
        entry = self.agenda.classes['forensics']
        entry['cells'].pop((5, 22))
        row, _, templates, exports = self.run_check(export_error=google.GoogleError('Export unavailable.', 403))
        checks = self.forms(row)
        self.assertEqual(checks[FIRST]['status'], 'READY')
        self.assertEqual(checks[SECOND]['status'], 'NOT_READY')
        self.assertEqual(checks[SECOND]['readability']['http_status'], 403)
        self.assertEqual(row['release']['status'], 'NOT_READY')
        templates.assert_called_once_with('readonly', SEARCH)
        exports.assert_called_once()

    def test_unbound_audit_contract_and_export_path_remain_ordinary(self):
        row, _, templates, exports = self.run_check(configured=False)
        self.assertEqual(row['release']['status'], 'READY')
        self.assertNotIn('form_releases', row['release'])
        self.assertEqual(row['release']['scope'], 'linked_deck')
        templates.assert_not_called()
        exports.assert_called_once()

    def test_invalid_duplicate_binding_fails_before_any_teacher_content_read(self):
        self.config['HUB_FORM_TEACHER_SOURCES'] = json.dumps([declaration(FIRST), declaration(FIRST)])
        row, _, templates, exports = self.run_check()
        self.assertEqual(row['release']['status'], 'NOT_READY')
        self.assertTrue(all(v['status'] == 'NOT_READY' for v in self.forms(row).values()))
        templates.assert_not_called(); exports.assert_not_called()

    def test_trash_foreign_parent_or_wrong_id_block_only_the_affected_form(self):
        for change in ({'trashed': True}, {'parents': [FIRST_FOLDER]}, {'id': SEARCH}):
            with self.subTest(change=change):
                self.changed[EVIDENCE] = change
                row, _, templates, _ = self.run_check()
                checks = self.forms(row)
                self.assertEqual(checks[FIRST]['status'], 'READY')
                self.assertEqual(checks[SECOND]['status'], 'NOT_READY')
                self.assertEqual(row['release']['status'], 'NOT_READY')
                self.assertNotIn(EVIDENCE, [c.args[1] for c in templates.call_args_list])

    def test_trashed_registered_folder_cannot_borrow_another_form_folder(self):
        self.changed[SECOND_FOLDER] = {'trashed': True}
        row, _, _, _ = self.run_check()
        self.assertEqual(self.forms(row)[FIRST]['status'], 'READY')
        self.assertEqual(self.forms(row)[SECOND]['status'], 'NOT_READY')

    def test_final_consumed_source_bookend_rejects_late_metadata_or_parent_drift(self):
        for change in ({'version': '5'}, {'parents': [FIRST_FOLDER]}):
            with self.subTest(change=change):
                self.changed.clear()
                def drift(fid, _meta):
                    if fid == EVIDENCE:
                        self.changed[fid] = change
                row, _, _, _ = self.run_check(consume=drift)
                checks = self.forms(row)
                self.assertEqual(checks[FIRST]['status'], 'READY')
                self.assertEqual(checks[SECOND]['status'], 'NOT_READY')
                self.assertNotIn('agenda_link_discrepancy', checks[SECOND])

    def test_consumed_template_metadata_must_equal_declared_source_bookend(self):
        def foreign(fid, meta):
            if fid == EVIDENCE:
                meta['id'] = SEARCH
        row, _, _, _ = self.run_check(consume=foreign)
        self.assertEqual(self.forms(row)[FIRST]['status'], 'READY')
        self.assertEqual(self.forms(row)[SECOND]['status'], 'NOT_READY')
        self.assertIn('consumed teacher', self.forms(row)[SECOND]['problem'])

    def test_source_read_failure_keeps_exact_availability_error_without_borrowed_readiness(self):
        def unavailable(fid, _meta):
            if fid == EVIDENCE:
                raise google.GoogleError('The exact native source is temporarily unavailable.', 503)
        row, _, _, _ = self.run_check(consume=unavailable)
        checks = self.forms(row)
        self.assertEqual(checks[FIRST]['status'], 'READY')
        self.assertEqual(checks[SECOND]['status'], 'NOT_READY')
        self.assertEqual(checks[SECOND]['readability']['http_status'], 503)
        self.assertIn('temporarily unavailable', checks[SECOND]['problem'])
        self.assertNotIn('Every exact registered teacher', checks[SECOND]['problem'])

    def test_teacher_hold_stays_scoped_to_its_exact_form(self):
        def hold(_config, _ag, _cls, _week, fid):
            return {'held': fid == SECOND, 'label': 'Needs check', 'problem': 'Exact teacher decision.', 'action': 'Check this exact source.'}
        row, _, _, _ = self.run_check(check_hold=hold)
        checks = self.forms(row)
        self.assertEqual(checks[FIRST]['status'], 'READY')
        self.assertEqual(checks[SECOND]['status'], 'NOT_READY')
        self.assertEqual(checks[SECOND]['issues'][0], 'teacher_readiness_hold')


if __name__ == '__main__':
    unittest.main()
