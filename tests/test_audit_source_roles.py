"""Readiness exemptions are per exact material; graded siblings and access holds survive."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch
from hub_v2 import audit, completion, google, readiness_source_roles as roles
from test_readiness_source_roles import SOURCE, FOLDER, FORM, agenda, meta

OTHER = 'FICTIONAL_GRADED_SOURCE_1234567890'


class AuditSourceRolesTests(TestCase):
    def check(self, *, paper=False, access=False, stale=False, sibling=True):
        a = agenda(FOLDER if paper else SOURCE, folder=paper)
        lesson = a.lessons.return_value[0]
        if sibling and not paper: lesson['materials'].append({'label': '2nd deck', 'url': 'https://docs.google.com/presentation/d/'+OTHER})
        a.classes = {'forensics': {'name': 'Forensics', 'cells': {}, 'layout': {'columns': {}, 'rows': [(8, 'Wk 06')]}}}
        a.week_for.return_value = {'label': 'Wk 06'}
        ident = FOLDER if paper else SOURCE
        role = {'axes': {'assessment': None if paper else 'ungraded', 'modality': 'paper' if paper else None},
                'revision': 'a'*64, 'metadata': meta(ident, mimeType=google.FOLDER if paper else completion.SLIDES), 'member_ids': []}
        source = {'decks': [] if paper else [{'id': SOURCE}, *([{'id': OTHER}] if sibling else [])],
                  'folders': [], 'stale_links': [], 'problem': 'Cannot verify actual folder: denied' if access else
                  'Cannot verify the actual Agenda lesson folder: The readable Agenda lesson folder contains no current slide deck.' if paper else None}
        reads = 0
        def read(*args, **kwargs):
            nonlocal reads
            if args[6] != ident: return None
            reads += 1
            return None if access or stale and reads > 1 else deepcopy(role)
        with patch.object(audit, 'build_lesson', return_value=lesson), patch.object(audit, '_titles', return_value={}), \
             patch.object(audit, '_published', return_value={}), patch.object(audit, '_ordinary_native_candidate', return_value=False), \
             patch.object(audit.work, 'form_teacher_sources', return_value=None), \
             patch.object(completion, 'lesson_sources', return_value=source), \
             patch.object(audit.completion_holds, 'check', return_value={'held': False}), \
             patch.object(roles, 'read', side_effect=read), \
             patch.object(audit, '_release_deck', return_value={'status': 'NOT_READY', 'label': 'Needs embedded codes',
                 'issues': ['marker_inventory_missing'], 'counts': {}, 'problem': 'Graded sibling lacks codes'}) as codes:
            result = audit.check('read', a, 'forensics', {})['rows'][0]['release']
        return result, codes

    def test_ungraded_material_does_not_suppress_graded_sibling(self):
        result, codes = self.check()
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual([c.args[1] for c in codes.call_args_list], [OTHER])
        self.assertEqual([d['status'] for d in result['linked_decks']], ['NOT_REQUIRED', 'NOT_READY'])
        self.assertIn('Graded sibling', result['problem'])

    def test_exact_ungraded_only_is_not_required_not_fake_ready(self):
        result, codes = self.check(sibling=False)
        self.assertEqual(result['status'], 'NOT_REQUIRED'); codes.assert_not_called()
        self.assertIn('VT codes', result['action']); self.assertNotIn('rerun grading', result['action'])

    def test_paper_folder_needs_no_slide_but_unreadable_folder_retains_hold(self):
        result, codes = self.check(paper=True)
        self.assertEqual(result['status'], 'NOT_REQUIRED'); codes.assert_not_called()
        self.assertIsNone(result['source_role']['assessment'])
        result, codes = self.check(paper=True, access=True)
        self.assertEqual(result['status'], 'NOT_READY'); self.assertIn('denied', result['problem'])

    def test_role_changed_during_check_stays_held(self):
        result, _ = self.check(stale=True, sibling=False)
        self.assertEqual(result['status'], 'NOT_READY'); self.assertIn('role changed', result['problem'])
