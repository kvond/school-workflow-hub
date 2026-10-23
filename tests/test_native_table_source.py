"""Bounded native table geometry; real structure with synthetic IDs and text."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hub_v2 import completion, google, native_teacher_source, response_binding


FIXTURE = Path(__file__).parent / 'fixtures' / 'native_unmerged_table.json'
ID = 'FICTIONAL_TABLE_DECK'


def native():
    return json.loads(FIXTURE.read_text())


def element(value):
    return value['slides'][0]['pageElements'][0]


def read(value, role='teacher'):
    return native_teacher_source.read(value, presentation_id=ID, revision='7', source_role=role)


class NativeTableTests(unittest.TestCase):
    def test_real_shaped_complete_grid_retains_exact_cell_dimensions_text_and_style(self):
        original = native()
        before = deepcopy(original)
        for role in ('teacher', 'student'):
            result = read(original, role)
            units = result['slides'][0]['units']
            self.assertEqual(len(units), 15)
            self.assertEqual(units[0]['box'], (533400, 1695450, 2692400, 400050))
            self.assertEqual(units[-1]['box'], (5918200, 3438525, 2692400, 447675))
            self.assertEqual(units[0]['text'], 'Heading 0')
            self.assertEqual(units[5]['text'], '')
            self.assertEqual(units[5]['key'], 'fictional_table/r1c2')
            self.assertTrue(all(u['panel_geometry_unverified'] for u in units))
            self.assertEqual(result['source_kind'], 'native_' + role + '_source')
            self.assertEqual(result['native_source_revision'], '7')
            self.assertEqual(result['native_source'], before)
            self.assertEqual(units[0]['native_style'], element(original)['table']['tableRows'][0]['tableCells'][0])
            self.assertEqual(original, before)
            result['native_source']['slides'].clear()
            units[0]['native_style']['rowSpan'] = 9
            self.assertEqual(original, before)

    def test_nominal_page_element_size_is_not_a_cell_rescaling_basis(self):
        original = native()
        expected = read(original)['slides']
        element(original)['size'] = {'width': {'magnitude': 99, 'unit': 'PT'},
                                    'height': {'magnitude': 123, 'unit': 'PT'}}
        self.assertEqual(read(original)['slides'], expected)
        # RowHeight is the actual supplied height, not minRowHeight.
        element(original)['table']['tableRows'][0]['tableRowProperties']['minRowHeight']['magnitude'] = 1
        self.assertEqual(read(original)['slides'], expected)

    def test_missing_nonfinite_partial_or_merged_grid_is_unreadable(self):
        mutations = [
            lambda e: e['table'].update(rows=True),
            lambda e: e['table'].update(rows=0),
            lambda e: e['table'].update(columns=4),
            lambda e: e['table'].pop('tableRows'),
            lambda e: e['table']['tableRows'].pop(),
            lambda e: e['table']['tableColumns'].pop(),
            lambda e: e['table']['tableColumns'][0].pop('columnWidth'),
            lambda e: e['table']['tableColumns'][0]['columnWidth'].update(magnitude=0),
            lambda e: e['table']['tableColumns'][0]['columnWidth'].update(magnitude=float('nan')),
            lambda e: e['table']['tableColumns'][0]['columnWidth'].update(unit='PX'),
            lambda e: e['table']['tableRows'][0].pop('rowHeight'),
            lambda e: e['table']['tableRows'][0]['rowHeight'].update(magnitude=-1),
            lambda e: e['table']['tableRows'][0]['rowHeight'].update(magnitude=float('inf')),
            lambda e: e['table']['tableRows'][0]['tableCells'].pop(),
            lambda e: e['table']['tableRows'][0]['tableCells'][1].update(location={}),
            lambda e: e['table']['tableRows'][0]['tableCells'][1].update(location={'columnIndex': 5}),
            lambda e: e['table']['tableRows'][0]['tableCells'][1].update(location={'rowIndex': True, 'columnIndex': 1}),
            lambda e: e['table']['tableRows'][0]['tableCells'][0].pop('location'),
            lambda e: e['table']['tableRows'][0]['tableCells'][0].update(rowSpan=2),
            lambda e: e['table']['tableRows'][0]['tableCells'][0].update(columnSpan=2),
            lambda e: e['table']['tableRows'][0]['tableCells'][0].update(rowSpan=True),
            lambda e: e['table']['tableRows'][0]['tableCells'][0].pop('columnSpan'),
            lambda e: e['table']['tableRows'][0].update(unknownGeometry={}),
            lambda e: e['table'].update(unknownGeometry={}),
            lambda e: e.update(shape={}),
        ]
        for mutation in mutations:
            value = native(); mutation(element(value))
            with self.subTest(mutation=mutation), self.assertRaises(native_teacher_source.Unreadable):
                read(value)

    def test_scaled_rotated_sheared_or_grouped_tables_need_the_export(self):
        for transform in ({'scaleX': 2}, {'scaleY': -1}, {'scaleX': 0},
                          {'shearX': .1}, {'shearY': .1}, {'translateX': float('nan')}):
            value = native(); element(value)['transform'].update(transform)
            with self.subTest(transform=transform), self.assertRaises(native_teacher_source.Unreadable):
                read(value)
        value = native()
        table = element(value)
        value['slides'][0]['pageElements'] = [{'objectId': 'group', 'transform': {
            'scaleX': 1, 'scaleY': 1, 'unit': 'EMU'}, 'elementGroup': {'children': [table]}}]
        with self.assertRaises(native_teacher_source.Unreadable):
            read(value)

    def test_unsupported_present_text_or_activity_metadata_is_not_silently_dropped(self):
        changes = [None, {}, {'textElements': None}, {'textElements': [{'autoText': {'type': 'SLIDE_NUMBER'}}]},
                   {'textElements': [{'textRun': {'content': 1}}]},
                   {'textElements': [{'textRun': {}, 'paragraphMarker': {}}]},
                   {'textElements': [{'textRun': {'content': 'known'}, 'unknownText': {}}]}]
        for text in changes:
            value = native(); element(value)['table']['tableRows'][1]['tableCells'][2]['text'] = text
            with self.subTest(text=text), self.assertRaises(native_teacher_source.Unreadable):
                read(value)
        for description in (response_binding.PREFIX + '{}', response_binding.PREFIX + 'invalid'):
            value = native(); element(value)['description'] = description
            with self.assertRaises(native_teacher_source.Unreadable):
                read(value)

    def test_emu_and_point_cell_dimensions_are_equivalent_and_translation_is_applied_once(self):
        value = native(); before = read(value)
        for row in element(value)['table']['tableRows']:
            row['rowHeight']['magnitude'] /= 12700
            row['rowHeight']['unit'] = 'PT'
        for col in element(value)['table']['tableColumns']:
            col['columnWidth']['magnitude'] /= 12700
            col['columnWidth']['unit'] = 'PT'
        self.assertEqual([u['box'] for u in read(value)['slides'][0]['units']],
                         [u['box'] for u in before['slides'][0]['units']])

    def test_teacher_fallback_reads_table_only_after_existing_export_and_revision_gates(self):
        completion._native_teacher_templates.clear()
        meta = {'id': ID, 'name': 'Fictional source', 'mimeType': completion.SLIDES,
                'trashed': False, 'version': '7', 'modifiedTime': 'unchanged'}
        with patch.object(completion, '_template', side_effect=google.GoogleError('Export denied', 403)), \
             patch.object(google, 'drive_file', return_value=meta), \
             patch.object(google, 'native_presentation', return_value=native()) as get:
            _, parsed = completion._teacher_template('read', ID)
        self.assertEqual(len(parsed['slides'][0]['units']), 15)
        get.assert_called_once_with('read', ID)
        completion._native_teacher_templates.clear()
        with patch.object(completion, '_template', side_effect=google.GoogleError('Export denied', 403)), \
             patch.object(google, 'drive_file', side_effect=[meta, {**meta, 'version': '8'}]), \
             patch.object(google, 'native_presentation', return_value=native()), \
             self.assertRaises(completion.Unreadable):
            completion._teacher_template('read', ID)
        self.assertEqual(completion._native_teacher_templates, {})

    def test_existing_found_code_policy_keeps_blank_denominators_and_uncoded_na(self):
        teacher = native()
        empty = completion.assess(read(teacher, 'student'), read(teacher), found_codes_only=True)
        self.assertIsNone(empty['notes_score'])
        self.assertEqual(empty['counts']['NOTES']['required'], 0)
        cell = element(teacher)['table']['tableRows'][1]['tableCells'][2]
        cell['text'] = {'textElements': [{'textRun': {'content': '[[NOTES:X:ONE]]'}}]}
        student = deepcopy(teacher)
        blank = completion.assess(read(student, 'student'), read(teacher), found_codes_only=True)
        self.assertEqual(blank['counts']['NOTES'], {'done': 0, 'required': 1})
        self.assertEqual(blank['notes_score'], 0)
        self.assertIsNone(blank['revision_score'])
        element(student)['table']['tableRows'][1]['tableCells'][2]['text']['textElements'][0]['textRun']['content'] += ' My own answer.'
        filled = completion.assess(read(student, 'student'), read(teacher), found_codes_only=True)
        self.assertEqual(filled['counts']['NOTES'], {'done': 1, 'required': 1})
        self.assertEqual(filled['notes_score'], 10)
        self.assertIsNone(filled['revision_score'])


if __name__ == '__main__':
    unittest.main()
