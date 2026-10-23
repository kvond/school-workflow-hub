"""One original inventory ID correction; synthetic native/PPTX/CAS only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import completion, embedded_code_repair as repair
from tests import test_embedded_code_repair as fixtures

OLD, NEW, PAIR = '[[NOTES:C09:TYPO]]', '[[NOTES:C09:Q1]]', '[[DRAFT:C09:Q1]]'


class ExactIDTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepairTests.setUp(self)
        elements = repair._elements(self.source.native)
        for ident, text, y in (
            ('InitialResponse', 'An original answer 😀 stays.\nt ' + OLD + 'pl\n', 70),
            ('RevisedResponse', 'An original revision 🌿 stays. ' + PAIR, 160),
            ('BellResponse', '[[NOTES:C09:BELL]] Existing bell answer.', 70),
        ):
            elements[ident][2].clear()
            elements[ident][2].update(fixtures.shape(ident, text, y, height=60))
        self.reference = deepcopy(self.source)
        self.reference.meta['id'] = self.reference.native['presentationId'] = fixtures.REFERENCE
        self.set_text('InitialResponse', 'Your first answer ' + NEW, teacher=True)
        self.set_text('RevisedResponse', 'Your revised answer ' + PAIR, teacher=True)
        self.spec = self.bind_teacher()

    def set_text(self, ident, text, *, teacher=False):
        source = self.reference if teacher else self.source
        element = repair._elements(source.native)[ident][2]
        template = fixtures.shape(ident, text, 0)
        element['shape']['text'] = template['shape']['text']

    def bind_teacher(self):
        cap = repair.capture('read', fixtures.REFERENCE)
        spec = {'file_id': fixtures.REFERENCE, 'revision': cap['native']['revisionId'],
                'native_sha256': cap['native_sha256'], 'metadata_sha256': cap['metadata_sha256']}
        self.proof.update(registered_teacher_sha256=repair.digest(spec), registered_teacher_proof_sha256='c' * 64)
        self.guard_result.update({k: self.proof[k] for k in ('registered_teacher_sha256', 'registered_teacher_proof_sha256')})
        return spec

    def operation(self):
        text = repair._text(repair._elements(self.source.native)['InitialResponse'][2])
        at = len(repair._units(text[:text.index(OLD)]))
        return {'placement': repair.EXACT_ID_REPLACEMENT, 'marker': NEW, 'original_marker': OLD,
                'page_id': 'AnswerPage', 'label_object_id': 'InitialResponse', 'source_object_id': 'InitialResponse',
                'paired_object_id': 'RevisedResponse', 'response_object_ids': ['InitialResponse'],
                'page_response_object_ids': ['InitialResponse', 'RevisedResponse'],
                'remove': [{'start': at, 'end': at + len(repair._units(OLD)), 'text': OLD}]}

    def plan(self, operations=None, *, spec=True):
        cap = repair.capture('read', fixtures.FILE)
        return repair.prepare('read', fixtures.FILE, self.proof, cap['native']['revisionId'],
                              cap['native_sha256'], cap['metadata_sha256'],
                              [self.operation()] if operations is None else operations,
                              guard=self.guard, registered_teacher=self.spec if spec else None)

    apply = fixtures.RepairTests.apply

    def test_full_real_reader_apply_preserves_every_non_token_character_style_and_count(self):
        before = repair.capture('read', fixtures.FILE)
        plan = self.plan()
        self.assertEqual([next(iter(r)) for r in plan['requests']], ['deleteText', 'insertText', 'updateTextStyle'])
        self.assertEqual(self.source.writes, 0)
        self.assertFalse(plan['grading_authorized'])
        self.assertEqual(self.apply(plan)['status'], 'verified')
        after = repair.capture('read', fixtures.FILE)
        repair._preserved(before, after, plan['edits'])
        self.assertEqual(completion.readiness(after['parsed'])['status'], 'READY')
        self.assertEqual(completion.readiness(before['parsed'])['counts'], completion.readiness(after['parsed'])['counts'])
        old_text = repair._text(repair._elements(before['native'])['InitialResponse'][2])
        new_text = repair._text(repair._elements(after['native'])['InitialResponse'][2])
        self.assertEqual(new_text, old_text.replace(OLD, NEW))
        self.assertIn('\nt ' + NEW + 'pl\n', new_text)

    def test_token_can_span_styles_with_non_bmp_neighbors_without_changing_surrounding_styles(self):
        element = repair._elements(self.source.native)['InitialResponse'][2]
        chars = repair._chars(element)
        at = self.operation()['remove'][0]['start']
        for index in range(at + 4, at + 8):
            chars[index][0][1]['italic'] = True
        chars[at - 1][0][1]['bold'] = True
        chars[at + len(repair._units(OLD))][0][1]['underline'] = True
        element['shape']['text']['textElements'] = fixtures.text_elements(chars)
        plan = self.plan(); before = plan['before']
        self.apply(plan)
        repair._preserved(before, repair.capture('read', fixtures.FILE), plan['edits'])

    def test_range_must_cover_only_whole_token_and_not_split_unicode(self):
        for change in ('prefix', 'suffix', 'inside', 'surrogate', 'text'):
            op = self.operation(); span = op['remove'][0]
            if change == 'prefix': span['start'] -= 1
            if change == 'suffix': span['end'] += 1
            if change == 'inside': span['start'] += 1
            if change == 'text': span['text'] = OLD + 'pl'
            if change == 'surrogate':
                text = repair._text(repair._elements(self.source.native)['InitialResponse'][2])
                span['start'] = len(repair._units(text[:text.index('😀')])) + 1
            with self.subTest(change=change), self.assertRaises(repair.Refused): self.plan([op])
        self.assertEqual(self.source.writes, 0)

    def test_repeated_original_target_paired_and_extra_missing_codes_refuse(self):
        native = deepcopy(self.source.native)
        cases = [('InitialResponse', OLD + OLD), ('InitialResponse', OLD + NEW),
                 ('RevisedResponse', PAIR + PAIR), ('RevisedResponse', 'uncoded revision'),
                 ('BellResponse', 'uncoded bell'), ('BellResponse', '[[NOTES:C09:OTHER]]'),
                 ('BellResponse', '[[NOTES:C08:BELL]]')]
        for ident, text in cases:
            self.source.native = deepcopy(native); self.set_text(ident, text)
            with self.subTest(ident=ident, text=text), self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_wrong_category_cycle_or_bank_is_not_a_token_id_repair(self):
        for marker in ('[[DRAFT:C09:Q1]]', '[[NOTES:C08:Q1]]', '[[BANK:C09:Q1]]', OLD):
            op = self.operation(); op['marker'] = marker
            with self.subTest(marker=marker), self.assertRaises(repair.Refused): self.plan([op])

    def test_wrong_teacher_object_page_question_or_code_refuses(self):
        native = deepcopy(self.reference.native)
        for change in ('object', 'page', 'context', 'code', 'duplicate', 'extra_context'):
            self.reference.native = deepcopy(native)
            if change == 'object': repair._elements(self.reference.native)['InitialResponse'][2]['objectId'] = 'OtherResponse'
            if change == 'page': self.reference.native['slides'][1]['objectId'] = 'OtherPage'
            if change == 'context': self.set_text('InitialLabel', 'Another original question', teacher=True)
            if change == 'code': self.set_text('InitialResponse', '[[NOTES:C09:OTHER]]', teacher=True)
            if change == 'duplicate': self.set_text('InitialResponse', NEW + NEW, teacher=True)
            if change == 'extra_context': self.reference.native['slides'][1]['pageElements'].append(fixtures.shape('OtherPrompt', 'new task', 280))
            self.spec = self.bind_teacher()
            with self.subTest(change=change), self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_original_inventory_not_teacher_task_set_is_authority(self):
        self.set_text('OriginalInventory', '[[MARKER-INVENTORY:C09]] NOTES=2 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 '
                      'NOTES_IDS=OTHER,BELL DRAFT_IDS=OTHER OPTIONAL_IDS=-')
        with self.assertRaises(repair.Refused): self.plan()

    def test_original_id_declared_for_another_category_is_not_typo(self):
        self.set_text('OriginalInventory', '[[MARKER-INVENTORY:C09]] NOTES=2 DRAFT=1 OPTIONAL=1 BANK_TERMS=0 '
                      'NOTES_IDS=Q1,BELL DRAFT_IDS=Q1 OPTIONAL_IDS=TYPO')
        self.source.native['slides'][2]['pageElements'].append(fixtures.shape('Optional', '[[OPTIONAL:C09:TYPO]]', 220))
        with self.assertRaisesRegex(repair.Refused, 'declared task'): self.plan()

    def test_missing_or_changed_teacher_proof_refuses_before_source_write(self):
        with self.assertRaises(repair.Refused): self.plan(spec=False)
        self.reference.native['revisionId'] = 'changed-teacher'
        with self.assertRaises(repair.Refused): self.plan()
        self.reference.native['revisionId'] = self.spec['revision']
        self.guard_result['registered_teacher_proof_sha256'] = 'd' * 64
        with self.assertRaises(repair.Refused): self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_changed_source_and_incomplete_census_cannot_apply(self):
        plan = self.plan(); self.source.native['revisionId'] = 'changed-original'
        with self.assertRaises(repair.Refused): self.apply(plan)
        self.source.native['revisionId'] = plan['manifest']['revision']
        self.proof['question_set_status'] = self.guard_result['question_set_status'] = 'incomplete'
        plan = self.plan()
        self.assertFalse(plan['apply_eligible'])
        with self.assertRaises(repair.Refused): self.apply(plan)
        self.assertEqual(self.source.writes, 0)

    def test_changed_teacher_at_last_native_bookend_prevents_dispatch_and_keeps_fence(self):
        plan = self.plan(); original = repair.google.drive_file
        def changed(token, ident, *args):
            result = original(token, ident, *args)
            if ident == fixtures.REFERENCE and self.port.rows:
                result['version'] = '99'
            return result
        with patch.object(repair.google, 'drive_file', side_effect=changed):
            with self.assertRaises(repair.Uncertain): self.apply(plan)
        self.assertEqual(self.source.writes, 0)
        with self.assertRaises(repair.Refused): self.apply(plan)

    def test_uncertain_write_never_retried(self):
        plan = self.plan(); self.source.fail_write = True
        with self.assertRaises(repair.Uncertain): self.apply(plan)
        with self.assertRaises(repair.Refused): self.apply(plan)
        self.assertEqual(self.source.writes, 1)

    def test_unrelated_answer_style_media_acl_mutation_stays_uncertain(self):
        plan = self.plan()
        self.source.after_write = lambda source: source.native['slides'][0].update(speakerNotes='unexpected')
        with self.assertRaises(repair.Uncertain): self.apply(plan)
        self.assertEqual(self.source.writes, 1)

    def test_unknown_placement_mixed_operations_and_extra_fields_refuse(self):
        for change in ('unknown', 'mixed', 'panel', 'extra', 'responses'):
            op = self.operation(); ops = [op]
            if change == 'unknown': op['placement'] = 'replace_any_text'
            if change == 'mixed': ops.append(deepcopy(op))
            if change == 'panel': op['panel_object_id'] = 'InitialResponse'
            if change == 'extra': op['allow_answers'] = True
            if change == 'responses': op['response_object_ids'].append('RevisedResponse')
            with self.subTest(change=change), self.assertRaises(repair.Refused): self.plan(ops)

    def test_existing_append_path_still_refuses_valid_undeclared_code(self):
        op = self.operation()
        for k in ('original_marker', 'paired_object_id'): op.pop(k)
        op.update(placement='in_place', source_object_id=None, remove=[])
        cap = repair.capture('read', fixtures.FILE)
        with self.assertRaisesRegex(repair.Refused, 'outside the declared inventory'):
            repair._plan(cap, [op])

    def test_draft_typo_uses_original_notes_pair_and_same_original_denominator(self):
        self.set_text('InitialResponse', 'Existing answer ' + NEW)
        typo = '[[DRAFT:C09:TYPO]]'
        self.set_text('RevisedResponse', 'Retained 🌿 t ' + typo + 'pl\n')
        text = repair._text(repair._elements(self.source.native)['RevisedResponse'][2])
        at = len(repair._units(text[:text.index(typo)]))
        op = {'placement': repair.EXACT_ID_REPLACEMENT, 'marker': PAIR, 'original_marker': typo,
              'page_id': 'AnswerPage', 'label_object_id': 'RevisedResponse', 'source_object_id': 'RevisedResponse',
              'paired_object_id': 'InitialResponse', 'response_object_ids': ['RevisedResponse'],
              'page_response_object_ids': ['InitialResponse', 'RevisedResponse'],
              'remove': [{'start': at, 'end': at + len(repair._units(typo)), 'text': typo}]}
        self.assertEqual(self.apply(self.plan([op]))['status'], 'verified')

    def test_incomplete_export_is_not_complete_original_census(self):
        cap = repair.capture('read', fixtures.FILE)
        teacher = repair.capture('read', fixtures.REFERENCE)
        cap['native']['slides'].append({'objectId': 'OmittedOriginalPage', 'pageElements': []})
        with self.assertRaisesRegex(repair.Refused, 'slide coverage'):
            repair._plan(cap, [self.operation()], registered_teacher=teacher)


if __name__ == '__main__':
    unittest.main()
