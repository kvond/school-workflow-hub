"""Manual Agenda material changes reach Hub without replacing intake identities."""
from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from hub_v2 import agenda, agenda_edit, google
from test_hub_v2 import cell, fixture, DECK

NEW = 'https://docs.google.com/presentation/d/FICTIONALNEWDECK1234567890/edit'
LATER = 'https://docs.google.com/presentation/d/FICTIONALLATERDECK1234567890/edit'
FOLDER = 'https://drive.google.com/drive/u/0/folders/FICTIONALNEWFOLDER'


class VisibleMaterialUpdateTests(TestCase):
    def source(self):
        source = fixture()
        entry = source.classes['forensics']
        cells = entry['cells']
        cells[5, 12] = cell(NEW.replace('/edit', '/copy'), userEnteredValue={
            'stringValue': NEW.replace('/edit', '/copy')})
        cells[5, 13] = cell(NEW, userEnteredValue={'stringValue': NEW})
        cells[5, 14] = cell(FOLDER, userEnteredValue={'stringValue': FOLDER})
        return source, entry, cells

    def test_manual_visible_materials_override_old_hidden_slots_without_merging_forms(self):
        source, entry, cells = self.source()
        untouched = deepcopy(cells)
        original_forms = fixture().lessons('forensics', 'Wk 04')[0]['forms']
        lesson = source.day(date(2026, 9, 23))['classes'][1]['lessons'][0]
        materials = {m['label']: m['url'] for m in lesson['materials']}
        self.assertEqual(materials['Teacher deck'], NEW)
        self.assertEqual(materials['Student copy'], NEW.replace('/edit', '/copy'))
        self.assertEqual(materials['Lesson folder'], FOLDER)
        self.assertEqual(lesson['links']['deck'], NEW)
        self.assertEqual(lesson['forms'], original_forms)
        self.assertEqual(len(lesson['forms']), 2)
        self.assertEqual(cells, untouched)
        self.assertEqual(cells[5, 17]['formattedValue'], DECK)

    def test_named_manual_hyperlinks_and_rich_links_are_respected(self):
        for changed in (cell('Teacher lesson', hyperlink=NEW),
                        cell('Teacher lesson', textFormatRuns=[{
                            'startIndex': 0, 'format': {'link': {'uri': NEW}}}])):
            with self.subTest(cell=changed):
                source, _, cells = self.source()
                cells[5, 13] = changed
                lesson = source.lessons('forensics', 'Wk 04')[0]
                self.assertEqual(lesson['links']['deck'], NEW)

    def test_literal_hyperlink_formula_is_an_explicit_choice(self):
        source, _, cells = self.source()
        cells[5, 13] = cell('Teacher lesson', hyperlink=DECK, userEnteredValue={
            'formulaValue': '=HYPERLINK("%s","Teacher lesson")' % NEW})
        self.assertEqual(source.lessons('forensics', 'Wk 04')[0]['links']['deck'], NEW)

    def test_pasted_url_beats_stale_sheets_click_format(self):
        source, _, cells = self.source()
        cells[5, 13] = cell(NEW, hyperlink=DECK, userEnteredValue={'stringValue': NEW})
        self.assertEqual(source.lessons('forensics', 'Wk 04')[0]['links']['deck'], NEW)

    def test_conditional_formulas_do_not_choose_an_unselected_literal_branch(self):
        source, _, cells = self.source()
        for formula in (
                '=IF($Q5="",HYPERLINK("%s","Fallback"),HYPERLINK($Q5,"Teacher"))' % NEW,
                '=IF(FALSE,HYPERLINK("%s","Unselected"),HYPERLINK("%s","Selected"))' % (NEW, DECK)):
            with self.subTest(formula=formula):
                cells[5, 13] = cell('Teacher', hyperlink=DECK, userEnteredValue={'formulaValue': formula})
                self.assertEqual(source.lessons('forensics', 'Wk 04')[0]['links']['deck'], DECK)

    def test_formula_controls_keep_the_current_hidden_source_not_stale_click_metadata(self):
        source = fixture()
        cells = source.classes['forensics']['cells']
        cells[5, 17] = cell(NEW)
        cells[5, 16] = cell(NEW.replace('/edit', '/copy'))
        cells[5, 13] = cell('Teacher', hyperlink=DECK, userEnteredValue={
            'formulaValue': '=IF($Q5="","—",HYPERLINK($Q5,"Teacher"))'})
        cells[5, 12] = cell('Student', hyperlink=DECK.replace('/edit', '/copy'), userEnteredValue={
            'formulaValue': '=HYPERLINK($P5,"Student")'})
        lesson = source.lessons('forensics', 'Wk 04')[0]
        self.assertEqual(lesson['links']['deck'], NEW)
        self.assertEqual(next(m['url'] for m in lesson['materials'] if m['label'] == 'Student copy'),
                         NEW.replace('/edit', '/copy'))

    def test_manual_second_deck_does_not_replace_first_or_any_form(self):
        source = fixture()
        entry = source.classes['forensics']
        before = source.lessons('forensics', 'Wk 04')[0]
        entry['layout']['columns'].update(second_deck=22, second_deck_display=23)
        entry['cells'][5, 22] = cell(DECK)
        entry['cells'][5, 23] = cell('Updated second lesson', hyperlink=NEW)
        after = source.lessons('forensics', 'Wk 04')[0]
        self.assertEqual(after['links']['deck'], before['links']['deck'])
        self.assertEqual(after['links']['deck2'], NEW)
        self.assertEqual(after['forms'], before['forms'])

    def test_editor_save_updates_manual_visible_links_and_preserves_intake(self):
        source, entry, cells = self.source()
        before_forms = source.lessons('forensics', 'Wk 04')[0]['forms']
        requests = []
        def batch(token, sheet, updates):
            requests.extend(updates)
            for request in updates:
                update = request['updateCells']
                coordinate = (update['range']['startRowIndex'] + 1,
                              update['range']['startColumnIndex'] + 1)
                value = deepcopy(update['rows'][0]['values'][0])
                value['formattedValue'] = value.get('userEnteredValue', {}).get('stringValue', '')
                cells[coordinate] = value
        with patch.object(google, 'batch_update', side_effect=batch), \
                patch.object(google, 'sheet_grid', side_effect=lambda *a: {entry['tab']: cells}):
            result = agenda_edit.save('read', 'write', {}, source, 'forensics', 'Wk 04', 5,
                {'link:deck': {'from': NEW, 'to': LATER}})
        self.assertEqual(result, {'saved': ['link:deck'], 'unconfirmed': []})
        after = source.lessons('forensics', 'Wk 04')[0]
        self.assertEqual(after['links']['deck'], LATER)
        self.assertEqual(after['forms'], before_forms)
        self.assertEqual(cells[5, 13]['userEnteredFormat']['textFormat']['link']['uri'], LATER)
        self.assertEqual(cells[5, 12]['formattedValue'], LATER.replace('/edit', '/copy'))
        self.assertEqual({r['updateCells']['range']['startColumnIndex']+1 for r in requests}, {12,13,16,17})

    def test_editor_rejects_a_stale_source_without_writing(self):
        source, _, _ = self.source()
        with patch.object(google, 'batch_update') as writes, self.assertRaises(agenda_edit.Changed):
            agenda_edit.save('read', 'write', {}, source, 'forensics', 'Wk 04', 5,
                {'link:deck': {'from': DECK, 'to': LATER}})
        writes.assert_not_called()

    def test_editor_refuses_to_discard_a_second_material_link(self):
        source, _, cells = self.source()
        cells[5, 13] = cell('First / Second', textFormatRuns=[
            {'startIndex': 0, 'format': {'link': {'uri': NEW}}},
            {'startIndex': 8, 'format': {'link': {'uri': DECK}}}])
        with patch.object(google, 'batch_update') as writes, self.assertRaises(agenda_edit.Refused):
            agenda_edit.save('read', 'write', {}, source, 'forensics', 'Wk 04', 5,
                {'link:deck': {'from': NEW, 'to': LATER}})
        writes.assert_not_called()

    def test_editor_leaves_derived_controls_alone_and_refuses_explicit_formula_overwrite(self):
        source, entry, cells = self.source()
        cells[5, 13] = cell('Teacher', hyperlink=NEW, userEnteredValue={
            'formulaValue': '=HYPERLINK($Q5,"Teacher")'})
        cells[5, 12] = cell('Student', hyperlink=NEW.replace('/edit', '/copy'), userEnteredValue={
            'formulaValue': '=HYPERLINK($P5,"Student")'})
        changed = agenda_edit._link_cells(cells, entry['layout']['columns'], 5, 'deck', LATER, '')
        self.assertEqual({c for c, _, _ in changed}, {16, 17})
        cells[5, 13]['userEnteredValue']['formulaValue'] = '=HYPERLINK("%s","Teacher")' % NEW
        with self.assertRaises(agenda_edit.Refused):
            agenda_edit._link_cells(cells, entry['layout']['columns'], 5, 'deck', LATER, '')
