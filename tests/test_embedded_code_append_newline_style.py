"""Synthetic provider expansion of UpdateTextStyleRequest across newlines.

The Slides API documents that ranges may extend to adjacent newlines:
https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request#updatetextstylerequest
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import embedded_code_repair as repair
from tests import test_embedded_code_repair as fixtures
from tests import test_embedded_code_id_replacement as replacements


def expanded_write(source, token, file_id, requests, revision):
    """Emulate newline expansion and field-mask clearing, not desired behavior."""
    assert revision == source.native['revisionId'] and file_id == fixtures.FILE
    source.writes += 1
    objects = repair._elements(source.native)
    for request in requests:
        kind, op = next(iter(request.items()))
        element = objects[op['objectId']][2]
        if kind == 'updatePageElementAltText':
            element['description'] = op['description']
            continue
        chars = repair._chars(element)
        if kind == 'insertText':
            at = op['insertionIndex']; style, paragraph = deepcopy(chars[max(at - 1, 0)])
            chars[at:at] = [[[u.hex(), deepcopy(style[1])], deepcopy(paragraph)]
                           for u in repair._units(op['text'])]
        elif kind == 'deleteText':
            del chars[op['textRange']['startIndex']:op['textRange']['endIndex']]
        elif kind == 'updateTextStyle':
            start, end = op['textRange']['startIndex'], op['textRange']['endIndex']
            while start and chars[start - 1][0][0] == '0a00':start -= 1
            while end < len(chars) and chars[end][0][0] == '0a00':end += 1
            for ch in chars[start:end]:
                for field in op['fields'].split(','):
                    if field in op['style']:ch[0][1][field] = deepcopy(op['style'][field])
                    else:ch[0][1].pop(field, None)
        else:
            raise AssertionError(kind)
        element['shape']['text']['textElements'] = fixtures.text_elements(chars)
    source.native['revisionId'] = 'revision-8'
    source.meta.update(version='8', modifiedTime='2026-10-01T10:01:00Z')
    if source.after_write:source.after_write(source)
    return {}


class AppendNewlineTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepairTests.setUp(self)
        p = patch.object(repair.google, 'slides_batch_update', side_effect=lambda *args:expanded_write(self.source, *args))
        p.start();self.addCleanup(p.stop)

    plan = fixtures.RepairTests.plan
    apply = fixtures.RepairTests.apply

    def target(self, text='An original answer 😀 stays.\n', *, in_place=True):
        op = self.source.operations()[2]
        if in_place:
            op.update(placement='in_place', label_object_id='BellResponse')
        element = repair._elements(self.source.native)[op['label_object_id']][2]
        element['shape']['text'] = fixtures.shape('same', text, 0)['shape']['text']
        return op, element

    def set_newline_style(self, element, index, size=13, color=None):
        chars = repair._chars(element)
        style = chars[index][0][1]
        style['fontSize'] = {'magnitude': size, 'unit': 'PT'}
        style['foregroundColor'] = {'opaqueColor': {'rgbColor': color or {'green': .7}}}
        style['italic'] = True
        element['shape']['text']['textElements'] = fixtures.text_elements(chars)

    def verify(self, op):
        before = repair.capture('read', fixtures.FILE);plan = self.plan([op])
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(self.apply(plan)['status'], 'verified')
        self.assertEqual(self.source.writes, 1)
        after = repair.capture('read', fixtures.FILE)
        repair._preserved(before, after, plan['edits'])
        return before, after, plan

    def test_terminal_original_newline_restored_in_same_batch_with_utf16_offset(self):
        op, element = self.target();at = len(repair._units(repair._text(element))) - 1
        self.set_newline_style(element, at)
        before, after, plan = self.verify(op)
        insertion = ' ' + op['marker'];last = plan['requests'][-1]['updateTextStyle']
        self.assertEqual(last['textRange'], {'type':'FIXED_RANGE','startIndex':at + len(repair._units(insertion)),
                                           'endIndex':at + len(repair._units(insertion)) + 1})
        self.assertEqual(last['style']['fontSize'], {'magnitude':13,'unit':'PT'})
        self.assertEqual(last['fields'], 'fontSize,foregroundColor')
        old = repair._elements(before['native'])[op['label_object_id']][2]
        new = repair._elements(after['native'])[op['label_object_id']][2]
        self.assertEqual(repair._chars(old)[-1], repair._chars(new)[-1])

    def test_both_adjacent_original_newlines_keep_different_styles_and_all_body_text(self):
        op, element = self.target('\nAn original 😀 answer\n\n')
        chars = repair._chars(element);self.set_newline_style(element, len(chars)-2, 21, {'blue':.5})
        self.set_newline_style(element, len(chars)-1, 13, {'green':.7})
        before, after, plan = self.verify(op)
        self.assertEqual(len(plan['requests']), 4)
        self.assertEqual([r['updateTextStyle']['style']['fontSize']['magnitude']for r in plan['requests'][2:]], [21,13])

    def test_inherited_original_marker_style_fields_are_reset_not_forced_to_defaults(self):
        op, element = self.target();chars = repair._chars(element)
        chars[-1][0][1].pop('fontSize');chars[-1][0][1].pop('foregroundColor', None)
        element['shape']['text']['textElements'] = fixtures.text_elements(chars)
        _, _, plan = self.verify(op)
        self.assertEqual(plan['requests'][-1]['updateTextStyle']['style'], {})

    def test_uniform_contiguous_original_newlines_are_restored_as_one_block(self):
        op, element = self.target('An original 😀 answer\n\n\n')
        for index in (-3,-2):self.set_newline_style(element, index, 20, {'blue':.4})
        self.set_newline_style(element, -1, 13)
        _, _, plan = self.verify(op)
        left = plan['requests'][-2]['updateTextStyle']['textRange']
        self.assertEqual(left['endIndex'] - left['startIndex'], 2)

    def test_mixed_contiguous_original_newline_fields_refuse_before_any_post(self):
        op, element = self.target('An original answer\n\n\n')
        self.set_newline_style(element, -3, 20);self.set_newline_style(element, -2, 21)
        with self.assertRaisesRegex(repair.Refused, 'Differently styled adjacent original newlines'):
            self.plan([op])
        self.assertEqual(self.source.writes, 0)

    def test_separate_original_label_append_uses_same_newline_protection(self):
        op, element = self.target('Original printed label 😀\n', in_place=False)
        self.set_newline_style(element, -1)
        self.verify(op)

    def test_other_original_style_change_still_closes_uncertainty_fence_without_retry(self):
        op, element = self.target();self.set_newline_style(element, -1)
        def changed(source):
            element = repair._elements(source.native)[op['label_object_id']][2]
            chars = repair._chars(element);chars[-1][0][1]['italic'] = False
            element['shape']['text']['textElements'] = fixtures.text_elements(chars)
        self.source.after_write = changed;plan = self.plan([op])
        with self.assertRaises(repair.Uncertain):self.apply(plan)
        with self.assertRaises(repair.Refused):self.apply(plan)
        self.assertEqual(self.source.writes, 1)


class ReplacementNewlineTests(unittest.TestCase):
    set_text = replacements.ExactIDTests.set_text
    bind_teacher = replacements.ExactIDTests.bind_teacher
    operation = replacements.ExactIDTests.operation
    plan = replacements.ExactIDTests.plan
    apply = fixtures.RepairTests.apply

    def setUp(self):
        replacements.ExactIDTests.setUp(self)
        p = patch.object(repair.google, 'slides_batch_update', side_effect=lambda *args:expanded_write(self.source, *args))
        p.start();self.addCleanup(p.stop)

    def test_exact_id_replacement_keeps_original_adjacent_newlines_after_utf16_length_change(self):
        self.set_text('InitialResponse', 'An original answer 😀 stays.\n' + replacements.OLD + '\n')
        element = repair._elements(self.source.native)['InitialResponse'][2]
        chars = repair._chars(element);at = self.operation()['remove'][0]['start']
        for index, size in ((at - 1, 21), (len(chars) - 1, 13)):
            chars[index][0][1]['fontSize'] = {'magnitude':size,'unit':'PT'}
            chars[index][0][1]['foregroundColor'] = {'opaqueColor':{'rgbColor':{'green':.6}}}
        element['shape']['text']['textElements'] = fixtures.text_elements(chars)
        before = repair.capture('read', fixtures.FILE);plan = self.plan()
        self.assertEqual([next(iter(r))for r in plan['requests']],
                         ['deleteText','insertText','updateTextStyle','updateTextStyle','updateTextStyle'])
        self.assertEqual(self.apply(plan)['status'], 'verified')
        repair._preserved(before, repair.capture('read', fixtures.FILE), plan['edits'])
        self.assertEqual(self.source.writes, 1)


if __name__ == '__main__':unittest.main()
