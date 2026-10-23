"""Missing canonical codes restored in exact original boxes; synthetic only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import completion, embedded_code_repair as repair
from tests import test_embedded_code_repair as fixtures

FILE, shape = fixtures.FILE, fixtures.shape


class InPlaceRepairTests(unittest.TestCase):
    setUp = fixtures.RepairTests.setUp
    plan = fixtures.RepairTests.plan
    apply = fixtures.RepairTests.apply

    def operations(self):
        values = self.source.operations()
        for value in values:
            value['label_object_id'] = value['response_object_ids'][0]
            value.update(placement='in_place', source_object_id=None, remove=[])
        return values

    def test_capture_passes_reader_only_to_existing_bounded_image_verifier(self):
        native = {'slides': [{'pageElements': [{'image': {'contentUrl':
                  'https://docs.google.com/slides-images-rt/FictionalOpaque'}}]}]}
        proof = {'basis': 'image_pixels', 'sha256': 'f' * 64}
        with patch.object(repair.student_archive, '_native_image_proof', return_value=proof) as verify:
            self.assertEqual(repair._media(native, 'scoped-read-token'), {
                'https://docs.google.com/slides-images-rt/FictionalOpaque': proof})
            verify.assert_called_once_with('https://docs.google.com/slides-images-rt/FictionalOpaque',
                                           'scoped-read-token')

    def test_all_original_characters_styles_and_regions_survive(self):
        before = deepcopy(self.source.native)
        plan = self.plan(self.operations())
        self.assertEqual(self.source.writes, 0)
        self.assertFalse(plan['grading_authorized'])
        self.assertFalse(any('deleteText' in request for request in plan['requests']))
        result = self.apply(plan)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(self.source.writes, 1)
        after = repair.capture('read', FILE)
        self.assertEqual(completion.readiness(after['parsed'])['status'], 'READY')
        old, new = repair._elements(before), repair._elements(self.source.native)
        for op in self.operations():
            ident = op['label_object_id']
            self.assertEqual(repair._text(new[ident][2]),
                             repair._text(old[ident][2])[:-1] + ' ' + op['marker'] + '\n')

    def test_in_place_cannot_remove_even_malformed_code_fragments(self):
        ops = self.operations()
        ops[0].update(source_object_id='InitialResponse', remove=self.source.operations()[0]['remove'])
        with self.assertRaisesRegex(repair.Refused, 'no removal'):
            self.plan(ops)
        self.assertEqual(self.source.writes, 0)

    def test_does_not_invent_inventory_question_or_extend_category(self):
        for marker in ('[[NOTES:C09:NewQuestion]]', '[[BANK:C09:Term]]', '[[NOTES:C08:Q1]]'):
            with self.subTest(marker=marker):
                ops = self.operations(); ops[0]['marker'] = marker
                with self.assertRaises(repair.Refused):
                    self.plan(ops)
        self.assertEqual(self.source.writes, 0)

    def test_target_must_be_one_exact_original_response_region(self):
        for targets in (['RevisedResponse'], ['InitialResponse', 'RevisedResponse']):
            with self.subTest(targets=targets):
                ops = self.operations(); ops[0]['response_object_ids'] = targets
                with self.assertRaises(repair.Refused):
                    self.plan(ops)
        self.assertEqual(self.source.writes, 0)

    def test_existing_valid_code_is_never_duplicated(self):
        ops = self.operations()
        old = repair._elements(self.source.native)['InitialResponse'][2]
        old.clear(); old.update(shape('InitialResponse', '[[NOTES:C09:Q1]] original answer', 70, height=50))
        with self.assertRaisesRegex(repair.Refused, 'duplicated or already present'):
            self.plan(ops)
        self.assertEqual(self.source.writes, 0)

    def test_changed_answer_or_source_proof_prevents_apply(self):
        plan = self.plan(self.operations())
        self.source.native['revisionId'] = 'new-answer-revision'
        with self.assertRaises(repair.Refused):
            self.apply(plan)
        self.assertEqual(self.source.writes, 0)

    def test_incomplete_question_census_cannot_apply(self):
        self.proof['question_set_status'] = self.guard_result['question_set_status'] = 'incomplete'
        plan = self.plan(self.operations())
        self.assertFalse(plan['apply_eligible'])
        with self.assertRaisesRegex(repair.Refused, 'census'):
            self.apply(plan)
        self.assertEqual(self.source.writes, 0)

    def test_uncertain_write_is_fenced_and_never_retried(self):
        plan = self.plan(self.operations()); self.source.fail_write = True
        with self.assertRaises(repair.Uncertain):
            self.apply(plan)
        self.assertEqual(self.source.writes, 1)
        with self.assertRaises(repair.Refused):
            self.apply(plan)
        self.assertEqual(self.source.writes, 1)

    def test_new_placement_cannot_mix_panel_or_unknown_fields(self):
        for extra in ({'panel_object_id': 'InitialResponse'}, {'placement': 'guess'}):
            ops = self.operations(); ops[0].update(extra)
            with self.assertRaises(repair.Refused):
                self.plan(ops)
        self.assertEqual(self.source.writes, 0)


if __name__ == '__main__':
    unittest.main()
