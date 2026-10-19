"""Role-only code exemptions keep source reading, print, supplies and siblings."""
from unittest import TestCase
from unittest.mock import Mock, patch
from hub_v2 import completion, weekly_preflight as preflight
from test_readiness_source_roles import SOURCE, meta


class WeekAheadSourceRoleTests(TestCase):
    def run_content(self, *, access=True, outline_ok=True, modality='paper'):
        role = {'axes': {'assessment': None, 'modality': modality}, 'metadata': meta(), 'revision': 'a'*64}
        nodes = [{'id': SOURCE, 'name': 'Paper exercise', 'roles': ['teacher_deck'], 'url': 'https://example.test/paper',
                  'mimeType': completion.SLIDES, 'accessible': access, 'trashed': False, 'revision': '1', 'readiness_role': role},
                 {'id': 'GRADED_SOURCE_1234567890', 'name': 'Graded source', 'roles': ['second_deck'], 'url': 'https://example.test/graded',
                  'mimeType': completion.SLIDES, 'accessible': True, 'trashed': False, 'revision': '1'}]
        lesson = {'sources': nodes, 'pending': [], 'exceptions': [], 'print_lines': [], 'teacher_prep': [],
                  'supply_rows': [], 'meeting_dates': {}, 'sections': [], 'found': {}, 'week': 'Wk 06'}
        ctx = Mock()
        def check(kind, node, _compute):
            if kind == 'readiness': return {'status': 'NOT_READY', 'issues': ['missing_inventory'], 'problem': 'Graded codes absent'}
            return {'ok': outline_ok, 'http_status': 403 if not outline_ok else None, 'stage': 'native',
                    'problem': 'Read denied' if not outline_ok else None,
                    'blocks': {'print': [{'text': 'Print the worksheet', 'slide': 1}],
                               'put_out': [{'text': 'Scissors', 'slide': 1}]}}
        ctx.check.side_effect = check
        with patch.object(preflight, '_print_items') as prints, \
             patch.object(preflight, '_week_text', return_value=''), \
             patch.object(preflight, 'deck_supply_items', return_value=[{'item': 'Scissors'}]), \
             patch.object(preflight, '_supply_row', return_value={'item': 'Scissors'}):
            preflight._content(ctx, lesson, {})
        return lesson, ctx, prints

    def test_paper_keeps_outline_print_supplies_and_graded_sibling_code_check(self):
        lesson, ctx, prints = self.run_content()
        self.assertEqual([(c.args[0], c.args[1]['id']) for c in ctx.check.call_args_list],
                         [('outline', SOURCE), ('readiness', 'GRADED_SOURCE_1234567890'), ('outline', 'GRADED_SOURCE_1234567890')])
        self.assertEqual(lesson['sources'][0]['readiness']['status'], 'NOT_REQUIRED')
        self.assertEqual([e['kind'] for e in lesson['exceptions']], ['needs_embedded_codes'])
        self.assertTrue(lesson['print_lines']); self.assertTrue(lesson['supply_rows']); prints.assert_called_once()

    def test_source_content_read_failure_is_not_hidden_by_paper(self):
        lesson, _, _ = self.run_content(outline_ok=False)
        self.assertIn('deck_unreadable', [e['kind'] for e in lesson['exceptions']])
        self.assertTrue(any(e['blocking'] for e in lesson['exceptions']))
