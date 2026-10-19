"""Exact private role receipts never become assessment or whole-week exclusions."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch
from hub_v2 import completion, google, readiness_source_roles as roles
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from test_cloud_state import MemoryCAS

SOURCE = 'FICTIONAL_SOURCE_1234567890'
FOLDER = 'FICTIONAL_FOLDER_1234567890'
FORM = 'FICTIONAL_FORM_1234567890'


def agenda(source=SOURCE, folder=False):
    a = Mock(); a.spreadsheet_id = 'FICTIONAL_AGENDA_1234567890'
    a.weeks = [{'label': 'Wk 06', 'monday': '2026-10-05'}]
    lesson = {'row': 8, 'topic': 'Fictional exercise', 'forms': [{'form_id': FORM}], 'materials': [
        {'label': 'Lesson folder' if folder else 'Teacher deck', 'url': 'https://drive.google.com/' +
         ('drive/folders/' if folder else 'presentation/d/') + source}]}
    a.lessons.return_value = [lesson]
    return a


def meta(source=SOURCE, **changes):
    return {'id': source, 'name': 'Fictional source', 'mimeType': completion.SLIDES, 'trashed': False,
            'version': '1', 'modifiedTime': '2026-10-05T12:00:00Z', 'parents': [], **changes}


class SourceRoleTests(TestCase):
    def setUp(self):
        self.a = agenda(); self.port = MemoryCAS(); self.m = meta()
        self.patch = patch.object(google, 'drive_file', side_effect=lambda *_a, **_k: deepcopy(self.m))
        self.patch.start(); self.addCleanup(self.patch.stop)
        template = patch.object(completion, '_teacher_template', side_effect=lambda *_a: (deepcopy(self.m), {'slides': []}))
        template.start(); self.addCleanup(template.stop)

    def write(self, **kw):
        return roles.import_verified('read', {}, self.a, 'forensics', 'Wk 06', 8, SOURCE,
            instruction_sha256='a'*64, actor='teacher-authorized-host', port=self.port, **kw)

    def read(self, **kw):
        return roles.read('read', {}, self.a, 'forensics', 'Wk 06', 8, SOURCE, port=self.port, **kw)

    def test_axes_are_independent_and_import_has_history_and_cas(self):
        saved = self.write(modality='paper')
        self.assertEqual(saved['axes'], {'assessment': None, 'modality': 'paper'})
        self.assertEqual(self.read()['axes'], saved['axes']); self.assertEqual(len(self.port.rows), 2)
        with self.assertRaises(Conflict): self.write(assessment='ungraded')
        self.write(assessment='ungraded', expected_revision=saved['revision'])
        self.assertEqual(self.read()['axes'], {'assessment': 'ungraded', 'modality': None})
        self.assertEqual(len(self.port.rows), 3)

    def test_stale_lesson_forms_source_and_wrong_row_cannot_reuse_role(self):
        self.write(assessment='ungraded')
        for change in ('forms', 'material', 'metadata', 'year'):
            a, m = deepcopy(self.a.lessons.return_value), deepcopy(self.m)
            with self.subTest(change=change):
                if change == 'forms': self.a.lessons.return_value[0]['forms'].append({'form_id': 'OTHER_FORM_123'})
                elif change == 'material': self.a.lessons.return_value[0]['materials'][0]['url'] += '/changed'
                elif change == 'metadata': self.m['version'] = '2'
                else: self.a.weeks[0]['monday'] = '2027-10-05'
                self.assertIsNone(self.read())
                self.a.lessons.return_value = a; self.m = m; self.a.weeks[0]['monday'] = '2026-10-05'
        self.assertIsNone(roles.read('read', {}, self.a, 'forensics', 'Wk 06', 9, SOURCE, port=self.port))

    def test_unverified_or_replacement_source_and_bad_axes_never_import(self):
        for changes in ({'id': 'FOREIGN_SOURCE_123'}, {'trashed': True}, {'version': ''}):
            old = self.m; self.m = meta(**changes)
            with self.assertRaises(ValueError): self.write(modality='paper')
            self.m = old
        with self.assertRaises(ValueError): self.write(assessment='graded')
        with self.assertRaises(ValueError):
            roles.import_verified('read', {}, self.a, 'forensics', 'Wk 06', 8, FOLDER,
                modality='paper', instruction_sha256='a'*64, actor='host', port=self.port)
        self.assertEqual(self.port.rows, {})

    def test_read_failure_keeps_normal_checks_and_uncertain_write_is_not_retried(self):
        self.port.fail_after_commit = True
        with self.assertRaises(StoreUnavailable): self.write(assessment='ungraded')
        self.assertEqual(len(self.port.rows), 2)
        with patch.object(google, 'drive_file', side_effect=google.GoogleError('No access', 403)):
            self.assertIsNone(self.read())
        self.assertIsNotNone(self.read())  # reconcile actual persisted state; no second write

    def test_folder_membership_is_complete_stable_and_bound(self):
        self.a = agenda(FOLDER, folder=True); self.m = meta(FOLDER, mimeType=google.FOLDER)
        item = meta(SOURCE)
        def write():
            return roles.import_verified('read', {}, self.a, 'forensics', 'Wk 06', 8, FOLDER,
                modality='paper', instruction_sha256='a'*64, actor='host', port=self.port)
        with patch.object(google, 'folder_listing', return_value=([item], True)):
            write()
            self.assertEqual(roles.read('read', {}, self.a, 'forensics', 'Wk 06', 8, FOLDER, port=self.port)['member_ids'], [SOURCE])
        with patch.object(google, 'folder_listing', return_value=([item, meta('NEW_MEMBER_1234567890')], True)):
            self.assertIsNone(roles.read('read', {}, self.a, 'forensics', 'Wk 06', 8, FOLDER, port=self.port))
        for value in (([], False), ([item, item], True)):
            with patch.object(google, 'folder_listing', return_value=value), self.assertRaises(ValueError):
                roles.capture('read', FOLDER)

    def test_policy_change_and_corrupt_receipt_restore_normal_checks(self):
        self.write(assessment='ungraded')
        self.assertIsNone(roles.read('read', {'HUB_ASSIGNMENT_FORM_IDS': '{"changed":"target"}'},
            self.a, 'forensics', 'Wk 06', 8, SOURCE, port=self.port))
        ident = next(i for i,d in self.port.rows.items() if 'history' not in i)
        self.port.rows[ident].value['axes'] = {'assessment': 'graded', 'modality': None}
        self.assertIsNone(self.read())

    def test_folder_content_drift_during_capture_never_imports(self):
        self.a = agenda(FOLDER, folder=True); self.m = meta(FOLDER, mimeType=google.FOLDER)
        item = meta(SOURCE)
        with patch.object(google, 'folder_listing', side_effect=[([item], True), ([], True)]), self.assertRaises(ValueError):
            roles.import_verified('read', {}, self.a, 'forensics', 'Wk 06', 8, FOLDER,
                modality='paper', instruction_sha256='a'*64, actor='host', port=self.port)
        self.assertEqual(self.port.rows, {})

    def test_direct_verified_folder_member_requires_content_and_membership_not_title(self):
        self.a = agenda(FOLDER, folder=True)
        folder = meta(FOLDER, mimeType=google.FOLDER)
        item = meta(SOURCE, parents=[FOLDER])
        with patch.object(google, 'drive_file', side_effect=lambda _t, ident, *_a: deepcopy(folder if ident == FOLDER else item)), \
             patch.object(completion, '_teacher_template', return_value=(item, {'slides': [{'number': 1, 'units': []}]})), \
             patch.object(google, 'folder_listing', return_value=([item], True)):
            self.write(assessment='ungraded')
            self.assertIsNotNone(self.read())
        # Same title elsewhere, a shortcut target, or a later descendant cannot substitute.
        for members in ([], [meta('OTHER_MEMBER_1234567890', name=item['name'])],
                        [meta('SHORTCUT_MEMBER_1234567890', shortcutDetails={'targetId': SOURCE})]):
            with patch.object(google, 'drive_file', return_value=folder), \
                 patch.object(google, 'folder_listing', return_value=(members, True)):
                self.assertIsNone(self.read())
