"""Week-ahead lesson preflight on a fictional Agenda, Drive, Slides and state.

Every provider call is a fake: no live Google, Schoology, model or school write
is reachable, and Form responses are never read.
"""
import io
import json
import re
import unittest
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from copy import deepcopy
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from hub_v2 import agenda, app, audit, completion, deck_repair, google, native_feedback, print_ready_email
from hub_v2 import daily_feedback_packet as daily, weekly_packet, weekly_preflight as wp
from school_workflow_hub.core.cloud_state import key
from tests.test_cloud_state import MemoryCAS
from tests.test_hub_v2 import pptx

SHEET = 'FICTIONALAGENDASHEET0001'
ORIGIN = 'https://hub.fictional.example'
COUNTS = {'A4 Biology': 22, 'A2 Forensics': 14, 'A3 Forensics': 15, 'B3 A&P': 18}
PERMISSIONS = [{'type': 'user', 'role': 'owner', 'emailAddress': 'reader@fictional.example'}]

BIO_W08, BIO_DECK, BIO_NEXT, BIO_W11 = ('FICTIONALBIODECKWEEK0008', 'FICTIONALBIODECKWEEK0009',
                                        'FICTIONALBIODECKWEEK0010', 'FICTIONALBIODECKWEEK0011')
FOR_W08, FOR_DECK, FOR_DECK2, FOR_SECOND = ('FICTIONALFORDECKWEEK0008', 'FICTIONALFORDECKWEEK0009',
                                            'FICTIONALFORDECKLAB00009', 'FICTIONALFORSECONDDECK009')
AP_W08, AP_DECK, AP_NEXT = 'FICTIONALAPDECKWEEK00008', 'FICTIONALAPDECKWEEK00009', 'FICTIONALAPDECKWEEK00010'
BIO_FOLDER, BIO_FOLDER2, BIO_W11F = 'FictionalBioFolder09', 'FictionalBioFolder10', 'FictionalBioFolder11'
FOR_FOLDER, AP_FOLDER, AP_FOLDER2, OLD_FOLDER = ('FictionalForFolder09', 'FictionalApFolder09',
                                                 'FictionalApFolder10', 'FictionalOldFolder08')
BIO_FORM, FOR_FORM, AP_FORM, BIO_FORM11 = ('FICTIONALBIOFORM00000000001', 'FICTIONALFORFORM00000000001',
                                           'FICTIONALAPFORM000000000001', 'FICTIONALBIOFORM00000000011')
STRAY = 'FICTIONALSTRAYDECK000001'
HANDOUT, KEYFILE = 'FICTIONALHANDOUTCER00001', 'FICTIONALHANDOUTKEY00001'
LABELS = {'student': '🎞 Student copy ↗', 'teacher': '👩‍🏫 Teacher ↗', 'folder': '📁 Cycle folder ↗',
          'activity': 'My print set ↗', 'second': '🎞 2nd deck ↗'}


def cell(value='', **extra):
    return {'formattedValue': value, **extra}


def deck_url(ident):
    return 'https://docs.google.com/presentation/d/%s/edit' % ident


def folder_url(ident):
    return 'https://drive.google.com/drive/folders/%s' % ident


def form_url(ident):
    return 'https://docs.google.com/forms/d/%s/viewform' % ident


def shown(hidden, row, label, url):
    """A visible label the way the Agenda builds it from its hidden URL column."""
    value = cell(label if url else '—', userEnteredValue={
        'formulaValue': '=IF($%s%d="","—",HYPERLINK($%s%d,"%s"))' % (hidden, row, hidden, row, label)})
    if url:
        value['hyperlink'] = url
    return value


def headers(days, forensics=False):
    out = ['Wk', 'Dates & closures', 'Topic']
    for day in days:
        out += ['%s — plan' % day, '📝 %s note' % day]
    out += ['✅ To-do', '🧪 Lab / prep', '📄 Student deck', '👩‍🏫 Teacher deck']
    if forensics:  # the extra, unnamed hidden 2nd-deck URL slot sits between _teacherURL and _folderURL
        return out + ['2nd deck', '📁 Folder', '_studentURL', '_teacherURL', '', '_folderURL', '_extraURL',
                      '📤 Turn it in', '_turnInURL']
    return out + ['📁 Folder', '🧪 Activity / print set', '_studentURL', '_teacherURL', '_folderURL', '_extraURL',
                  '📤 Turn it in', '_turnInURL']


def lesson_row(row, week, topic, plans, days, *, deck='', folder='', form='', second='', prep='', forensics=False):
    cells = {1: cell(week), 2: cell(''), 3: cell(topic), 11: cell(prep)}
    for i, day in enumerate(days):
        cells[4 + 2 * i] = cell(plans.get(day, ''))
    copy = ('https://docs.google.com/presentation/d/%s/copy' % deck) if deck else ''
    student = cell(copy, userEnteredValue={'formulaValue': '=IF(Q%d="","",REGEXREPLACE(Q%d,"/edit$","/copy"))' % (row, row)})
    turnin = cell('📤 %s — Turn in' % topic, userEnteredValue={
        'formulaValue': '=HYPERLINK("%s","📤 %s — Turn in")' % (form_url(form), topic)}) if form else cell('')
    if forensics:
        cells.update({12: shown('P', row, LABELS['student'], copy), 13: shown('Q', row, LABELS['teacher'], deck and deck_url(deck)),
                      14: shown('R', row, LABELS['second'], second and deck_url(second)),
                      15: shown('S', row, LABELS['folder'], folder and folder_url(folder)),
                      16: student, 17: cell(deck and deck_url(deck)), 18: cell(second and deck_url(second)),
                      19: cell(folder and folder_url(folder)), 21: turnin, 22: cell(form and form_url(form))})
    else:
        cells.update({12: shown('P', row, LABELS['student'], copy), 13: shown('Q', row, LABELS['teacher'], deck and deck_url(deck)),
                      14: shown('R', row, LABELS['folder'], folder and folder_url(folder)),
                      15: shown('S', row, LABELS['activity'], ''),
                      16: student, 17: cell(deck and deck_url(deck)), 18: cell(folder and folder_url(folder)),
                      20: turnin, 21: cell(form and form_url(form))})
    return {(row, col): value for col, value in cells.items() if value.get('formattedValue') or value.get('userEnteredValue')}


def tab(days, rows, forensics=False):
    grid = {(1, 1): cell('Instructions')}
    grid.update({(3, c): cell(h) for c, h in enumerate(headers(days, forensics), 1) if h})
    for row in rows:
        grid.update(row)
    return grid


BIO_PREP = ('🧪 LAB (Mon/Wed) Yeast Fermentation Lab (CO₂) · BUY: Active dry yeast, sugar, balloons · '
            'PULL: Plastic bottles, measuring spoons, warm-water bath · ✔ on order — yeast, balloons, water bath, straws; 🛒 sugar')
AP_PREP = '⚠ PREP by Thu 10/22 → LAB (Tue/Thu/Fri) Bone Fractures with Carrots · BUY: Carrots · PULL: Trays'
MWF, TTF = ('Mon', 'Wed', 'Fri'), ('Tue', 'Thu', 'Fri')
WORKSHEET_TAB = '📝 Worksheets'
WORKSHEET_HEADERS = ('Course', 'Wk', 'Topic', 'Document', 'Type', 'Link', '_fileID')


def worksheet_grid(rows=(), headers=WORKSHEET_HEADERS, header_row=1):
    grid = {(header_row, c): cell(h) for c, h in enumerate(headers, 1)}
    for r, values in enumerate(rows, header_row + 1):
        grid.update({(r, c): v if isinstance(v, dict) else cell(v) for c, v in enumerate(values, 1)})
    return grid


def grids():
    bio = tab(MWF, [
        lesson_row(4, 'Wk 08', 'Enzymes', {'Mon': 'Enzyme notes', 'Wed': 'Enzyme lab'}, MWF, deck=BIO_W08, folder=OLD_FOLDER),
        lesson_row(5, 'Wk 09', 'Cellular respiration', {
            'Mon': 'Yeast lab intro (moved from Fri 10/23 — in-service)', 'Wed': '⚠️ VERIFY DAY Fermentation lab', 'Fri': ''},
            MWF, deck=BIO_DECK, folder=BIO_FOLDER, form=BIO_FORM, prep=BIO_PREP),
        lesson_row(6, 'Wk 10', 'Photosynthesis', {'Mon': 'Light reactions', 'Wed': 'Calvin cycle', 'Fri': 'B Friday — no class'},
                   MWF, deck=BIO_NEXT, folder=BIO_FOLDER2, form=BIO_FORM),
        lesson_row(7, 'Wk 11', 'Genetics', {'Mon': 'Punnett squares', 'Wed': 'Pedigrees', 'Fri': 'Review'},
                   MWF, deck=BIO_W11, folder=BIO_W11F, form=BIO_FORM11)])
    # The Student copy label in row 5 lost its formula: plain text, still showing its label.
    bio[5, 12] = cell(LABELS['student'])
    # Row 7: a Teacher label reading the wrong hidden column, and a typed note over the folder label.
    bio[7, 13] = shown('P', 7, LABELS['teacher'], 'https://docs.google.com/presentation/d/%s/copy' % BIO_W11)
    bio[7, 14] = cell('see shared drive')
    forensics = tab(MWF, [
        lesson_row(4, 'Wk 08', 'Crime scenes', {'Mon': 'Scene notes', 'Wed': 'Sketching'}, MWF, deck=FOR_W08,
                   folder=OLD_FOLDER, forensics=True),
        lesson_row(5, 'Wk 09', 'Fingerprints', {'Mon': 'Fingerprint patterns', 'Wed': 'Lifting prints',
                                                'Fri': 'B Friday — Forensics does not meet'}, MWF, deck=FOR_DECK,
                   folder=FOR_FOLDER, form=FOR_FORM, forensics=True),
        lesson_row(6, 'Wk 09', 'Fingerprint lab', {'Mon': 'Lab setup', 'Wed': 'Lab'}, MWF, deck=FOR_DECK2,
                   folder=FOR_FOLDER, form=FOR_FORM, second=FOR_SECOND, forensics=True),
        lesson_row(7, 'Wk 10', '[CLOSED — field trip]', {}, MWF, forensics=True)], forensics=True)
    anatomy = tab(TTF, [
        lesson_row(4, 'Wk 08', 'Tissues', {'Tue': 'Tissue notes', 'Thu': 'Tissue lab'}, TTF, deck=AP_W08, folder=OLD_FOLDER),
        lesson_row(5, 'Wk 09', 'Bone structure', {'Tue': 'Bone tissue notes', 'Thu': 'Bone tissue lab', 'Fri': 'Fracture types'},
                   TTF, deck=AP_DECK, folder=AP_FOLDER, form=AP_FORM, prep=AP_PREP),
        lesson_row(6, 'Wk 10', 'Joints', {'Tue': 'Joint types', 'Thu': 'Joint lab', 'Fri': 'Joint review'},
                   TTF, deck=AP_NEXT, folder=AP_FOLDER2)])
    weeks = {(2, 2): cell('course row'), (2, 3): cell('week'), (2, 4): cell('Monday'), (2, 5): cell('dates & closures')}
    for row, (label, monday, closures) in enumerate([
            ('Wk 08', '10/12/2026', 'Oct 12 – Oct 16, 2026\n⚠ Fri 10/16 In-Service — Schools Closed\n'
                                    'Fri 10/16 = A Friday — Bio + Forensics lose it'),
            ('Wk 09', '10/19/2026', 'Oct 19 – Oct 23, 2026\nFri 10/23 = A Friday → Bio + Forensics'),
            ('Wk 10', '10/26/2026', 'Oct 26 – Oct 30, 2026\nFri 10/30 = B Friday → A&P'),
            ('Wk 11', '11/02/2026', 'Nov 2 – Nov 6, 2026')], 3):
        weeks.update({(row, 3): cell(label), (row, 4): cell(monday), (row, 5): cell(closures)})
    return {'🗂 Weeks': weeks, '🌿 Biology': bio, '🔬 Forensics': forensics, '🫀 A&P': anatomy,
            WORKSHEET_TAB: worksheet_grid()}


TABS = {'🗂 Weeks': 1, '🌿 Biology': 2, '🔬 Forensics': 3, '🫀 A&P': 4, WORKSHEET_TAB: 5}


def ready_deck(cycle):
    return [[('inventory', '[[MARKER-INVENTORY:%s]] NOTES=1 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q1 '
                           'DRAFT_IDS=Q1 OPTIONAL_IDS=-' % cycle)],
            [('question', 'Explain what you observed.'), ('notes', '[[NOTES:%s:Q1]]' % cycle),
             ('draft', '[[DRAFT:%s:Q1]]' % cycle)]]


BIO_SLIDES = [
    [('inventory', '[[MARKER-INVENTORY:C06]] NOTES=2 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q1,Q2 DRAFT_IDS=Q1 OPTIONAL_IDS=-')],
    [('question', 'What did the yeast release?'), ('notes', '[[NOTES:C06:Q1]]'), ('draft', '[[DRAFT:C06:Q1]]')],
    [('question', 'Why did the balloon inflate?'), ('notes', '[[NOTES:C06:Q3]]')],
    [('reference', 'TEACHER REFERENCE\nTO PRINT:\n• CER worksheet (1 per student)\n• CER worksheet answer key (teacher)\n'
                   '• Lab safety card (1 per group)\n• Support copies of vocabulary list\nBUY AHEAD\nBaking soda\n'
                   'PREPARE\nWarm the water bath to 40°C\nPUT OUT FOR STUDENTS\nGoggles\nNOTES FOR TEACHER\nnot an item')]]


def pdf_bytes(width, pages=1):
    writer, out = PdfWriter(), io.BytesIO()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=792)
    writer.write(out)
    return out.getvalue()


def native(ident, slides, version):
    return {'presentationId': ident, 'revisionId': 'rev-' + version,
            'pageSize': {'width': {'magnitude': 900, 'unit': 'PT'},
                         'height': {'magnitude': 600, 'unit': 'PT'}}, 'slides': [
        {'objectId': '%s_p%d' % (ident[-4:], n), 'pageElements': [
            {'objectId': '%s_s%d_%d' % (ident[-4:], n, i),
             'size': {'width': {'magnitude': 900, 'unit': 'PT'}, 'height': {'magnitude': 50, 'unit': 'PT'}},
             'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 0, 'translateY': 60 * i, 'unit': 'PT'},
             'shape': {'text': {'textElements': [
                {'textRun': {'content': line + '\n'}} for line in text.split('\n')]}}}
            for i, (_, text) in enumerate(shapes)]} for n, shapes in enumerate(slides, 1)]}


class FakeSources:
    """Fictional Drive, Forms and Slides as the Hub's read token sees them."""

    def __init__(self):
        self.files, self.decks, self.children, self.complete, self.forms, self.pdfs = {}, {}, {}, {}, {}, {}
        self.calls = Counter()
        for ident, cycle, parent in ((BIO_NEXT, 'C07', BIO_FOLDER2), (BIO_W11, 'C08', BIO_W11F),
                                     (FOR_DECK, 'F09', FOR_FOLDER), (FOR_DECK2, 'F09', FOR_FOLDER),
                                     (FOR_SECOND, 'F09', FOR_FOLDER), (AP_DECK, 'A09', AP_FOLDER),
                                     (AP_NEXT, 'A10', AP_FOLDER2)):
            self.deck(ident, 'Cycle %s — %s deck' % (cycle, ident[-6:]), ready_deck(cycle), parent)
        self.deck(BIO_DECK, 'Cycle 06 — Cellular respiration', BIO_SLIDES, BIO_FOLDER)
        self.deck(STRAY, 'Cycle 07 — Mitosis (spare)', ready_deck('C07'), BIO_FOLDER)
        for folder in (BIO_FOLDER, BIO_FOLDER2, BIO_W11F, FOR_FOLDER, AP_FOLDER, AP_FOLDER2):
            self.add(folder, 'Lesson folder ' + folder[-6:], google.FOLDER)
        self.add(HANDOUT, 'Cycle 06 — CER worksheet.pdf', 'application/pdf', [BIO_FOLDER])
        self.add(KEYFILE, 'Cycle 06 — CER worksheet answer key.pdf', 'application/pdf', [BIO_FOLDER])
        self.pdfs.update({HANDOUT: pdf_bytes(601), KEYFILE: pdf_bytes(603, 2)})
        for form, title in ((BIO_FORM, 'Week 10 Photosynthesis — Turn in'), (FOR_FORM, 'Fingerprints — Turn in'),
                            (AP_FORM, 'Bones — Turn in'), (BIO_FORM11, 'Genetics — Turn in')):
            self.add(form, title, 'application/vnd.google-apps.form')
            self.forms[form] = title

    def add(self, ident, name, mime, parents=()):
        self.files[ident] = {'id': ident, 'name': name, 'mimeType': mime, 'trashed': False, 'parents': list(parents),
                             'modifiedTime': '2026-10-01T12:00:00Z', 'version': '1'}
        for parent in parents:
            self.children.setdefault(parent, []).append(ident)
        return self.files[ident]

    def deck(self, ident, name, slides, parent):
        self.add(ident, name, completion.SLIDES, [parent])
        self.decks[ident] = slides

    def bump(self, ident):
        self.files[ident]['version'] = str(int(self.files[ident]['version']) + 1)
        self.files[ident]['modifiedTime'] = '2026-10-0%sT12:00:00Z' % self.files[ident]['version']

    def drive_file(self, token, ident, fields=None):
        self.calls['meta:' + ident] += 1
        if ident not in self.files:
            raise google.GoogleError("Not found, or not shared with the dashboard's account.", 404)
        return deepcopy(self.files[ident])

    def folder_listing(self, token, ident, max_pages=20):
        self.calls['listing:' + ident] += 1
        if ident not in self.files:
            raise google.GoogleError('Not found', 404)
        return ([deepcopy(self.files[c]) for c in self.children.get(ident, []) if not self.files[c]['trashed']],
                self.complete.get(ident, True))

    def form_info(self, token, ident):
        return {'info': {'title': self.forms[ident]},
                'responderUri': 'https://docs.google.com/forms/d/e/1FAIpQLSfictional%s/viewform' % ident[-8:]}

    def native_presentation(self, token, ident):
        self.calls['native:' + ident] += 1
        return native(ident, self.decks[ident], self.files[ident]['version'])

    def deck_bytes(self, token, ident, mime):
        self.calls['export:' + ident] += 1
        return pptx([[(name, text, (0, 60 * i, 900, 50)) for i, (name, text) in enumerate(slide)]
                     for slide in self.decks[ident]])

    def printable_pdf_bytes(self, token, ident, mime):
        self.calls['pdf:' + ident] += 1
        return self.pdfs[ident]


class Base(unittest.TestCase):
    def setUp(self):
        self.src, self.port = FakeSources(), MemoryCAS()
        self.grids = grids()
        self.config = {'HUB_AGENDA_SPREADSHEET_ID': SHEET, 'HUB_STATE_PROBE_ORIGINS': ORIGIN,
                       'HUB_ALLOWED_EMAILS': 'teacher@school.example'}
        self.writes = []
        self.index_reads = Counter()
        self.index_dimensions = {'rowCount': 200, 'columnCount': 8}
        patches = [patch.dict(completion._templates, {}, clear=True),
                   patch.dict(completion._native_teacher_templates, {}, clear=True),
                   patch.object(google, 'drive_file', side_effect=self.src.drive_file),
                   patch.object(google, 'folder_listing', side_effect=self.src.folder_listing),
                   patch.object(google, 'form_info', side_effect=self.src.form_info),
                   patch.object(google, 'native_presentation', side_effect=self.src.native_presentation),
                   patch.object(google, 'deck_bytes', side_effect=self.src.deck_bytes),
                   patch.object(google, 'printable_pdf_bytes', side_effect=self.src.printable_pdf_bytes),
                   patch.object(google, 'form_responses', side_effect=AssertionError('submissions are never read')),
                   patch.object(google, 'update_cells', side_effect=AssertionError('no text cell is written')),
                   patch.object(google, 'append_rows', side_effect=AssertionError('no grade or ledger write')),
                   patch.object(google, 'slides_batch_update', side_effect=AssertionError('no deck write')),
                   patch.object(google, 'sheet_grid', side_effect=self.sheet_grid),
                   patch.object(google, 'get_json', side_effect=self.sheet_properties),
                   patch.object(google, 'batch_update', side_effect=self.batch_update),
                   patch.object(wp, 'configured_store', return_value=self.port),
                   patch.object(weekly_packet, 'configured_store', return_value=self.port)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def agenda(self):
        return agenda.Agenda(SHEET, 'Fictional Agenda', TABS, deepcopy(self.grids))

    def run_check(self, start='2026-10-19', weeks=1, **kwargs):
        return wp.run('read-token', self.config, self.agenda(), start, weeks, COUNTS, port=self.port, **kwargs)

    # A fictional Sheets API over self.grids for the guarded link repair.
    def sheet_properties(self, url, token, params=None, **kwargs):
        self.assertTrue(url.startswith('https://sheets.googleapis.com/v4/spreadsheets/'))
        self.assertEqual(params, {'fields': 'sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))'})
        self.index_reads['dimensions'] += 1
        return {'sheets': [{'properties': {'sheetId': 5, 'title': WORKSHEET_TAB,
                                          'gridProperties': self.index_dimensions}}]}

    def sheet_grid(self, token, spreadsheet_id, ranges):
        out = {}
        for value in ranges:
            match = re.fullmatch(r"'(.+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", value)
            name, c1, r1 = match.group(1).replace("''", "'"), match.group(2), int(match.group(3))
            if name == WORKSHEET_TAB:
                self.index_reads['grid'] += 1
            if name not in self.grids:
                continue
            c2, r2 = match.group(4) or c1, int(match.group(5) or r1)
            first, last = wp._column_index(c1), wp._column_index(c2)
            target = out.setdefault(name, {})
            for (r, c), v in self.grids[name].items():
                if r1 <= r <= r2 and first <= c <= last:
                    target[r, c] = deepcopy(v)
        return out

    def batch_update(self, token, spreadsheet_id, requests):
        self.writes.append(deepcopy(requests))
        names = {v: k for k, v in TABS.items()}
        for request in requests:
            update = request['updateCells']
            grid = self.grids[names[update['range']['sheetId']]]
            row, col = update['range']['startRowIndex'] + 1, update['range']['startColumnIndex'] + 1
            value = update['rows'][0]['values'][0]
            if 'stringValue' in value['userEnteredValue']:  # a linked label
                label = value['userEnteredValue']['stringValue']
                grid[row, col] = cell(label, userEnteredValue={'stringValue': label},
                                      textFormatRuns=deepcopy(value['textFormatRuns']))
                continue
            formula = value['userEnteredValue']['formulaValue']
            hidden, label = re.search(r'HYPERLINK\(\$([A-Z]+)\d+,"([^"]+)"', formula).groups()
            url = agenda.first_link(grid.get((row, wp._column_index(hidden))))
            grid[row, col] = cell(label, userEnteredValue={'formulaValue': formula}, hyperlink=url)
        return {}

    def lesson(self, result, class_key, row):
        return next(l for l in result['lessons'] if l['class_key'] == class_key and l['row'] == row)

    def exceptions(self, result, class_key=None, row=None):
        return [e for e in result['exceptions'] if (class_key is None or e['class_key'] == class_key)
                and (row is None or e['row'] == row)]


class WorksheetIndexTests(Base):
    DOC1, DOC2 = 'FICTIONALWORKSHEET000001', 'FICTIONALWORKSHEET000002'

    def setUp(self):
        super().setUp()
        for ident in (self.DOC1, self.DOC2):
            self.src.add(ident, 'Worksheet ' + ident[-1], google.DOC)

    def index_row(self, *, course='Biology', week='W09', document='Fermentation guide', ident=None, url=None):
        ident = self.DOC1 if ident is None else ident
        url = 'https://docs.google.com/document/d/%s/edit' % ident if url is None else url
        return [course, week, 'Historical topic', document, 'Guide', url, ident]

    def diagnostics(self, result, kind=None):
        return [e for e in result['exceptions'] if e['kind'].startswith('worksheet_') and (not kind or e['kind'] == kind)]

    def coverage(self, result, number=0):
        return result['coverage']['worksheet_index'][number]

    def test_real_shaped_index_and_reordered_named_columns_preserve_row_provenance(self):
        row = self.index_row(url=cell('Open guide', hyperlink='https://docs.google.com/document/d/%s/edit' % self.DOC1))
        order = (6, 3, 0, 4, 5, 1, 2)
        self.grids[WORKSHEET_TAB] = worksheet_grid([[row[i] for i in order]],
                                                  [WORKSHEET_HEADERS[i] for i in order], header_row=3)
        table = agenda.read_worksheets('read-token', self.agenda())
        self.assertEqual((table['status'], table['header_row']), ('complete', 3))
        self.assertEqual((table['rows'][0]['row'], table['rows'][0]['link_cell'], table['rows'][0]['id_cell']), (4, 'E4', 'A4'))
        self.assertIn('gid=5&range=A4:H4', table['rows'][0]['source_url'])
        result = self.run_check()
        self.assertEqual((self.coverage(result)['scoped_rows'], self.coverage(result)['status']), (1, 'complete'))
        self.assertEqual(self.diagnostics(result), [])

    def test_index_load_is_lazy_once_per_run_and_not_cached_on_agenda(self):
        with patch.object(google, 'sheet_tabs', return_value=('Fictional Agenda', TABS)):
            loaded = agenda.load('read', SHEET)
        loaded.day(wp.date(2026, 10, 19))
        self.assertEqual(self.index_reads, {})
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row()])
        wp.run('read', self.config, loaded, '2026-10-19', 2, COUNTS, port=self.port)
        self.assertEqual(self.index_reads, {'dimensions': 1, 'grid': 1})
        self.grids[WORKSHEET_TAB][2, 6] = cell('')
        result = wp.run('read', self.config, loaded, '2026-10-19', 2, COUNTS, port=self.port)
        self.assertEqual(self.index_reads, {'dimensions': 2, 'grid': 2})
        self.assertTrue(self.diagnostics(result, 'worksheet_reference_conflict'))

    def test_missing_ambiguous_failed_and_absent_grids_are_not_successful_empty(self):
        for change in ('missing_tab', 'ambiguous_tab', 'missing_grid', 'read_failure', 'dimensions'):
            with self.subTest(change=change):
                source = self.agenda()
                if change == 'missing_tab':
                    del source.tabs[WORKSHEET_TAB]
                elif change == 'ambiguous_tab':
                    source.tabs['Worksheets'] = 6
                with patch.object(google, 'sheet_grid', return_value={}) if change == 'missing_grid' else patch.object(
                        google, 'sheet_grid', side_effect=self.sheet_grid):
                    failure = google.GoogleError('Unavailable', 403)
                    with patch.object(google, 'get_json', side_effect=failure) if change == 'read_failure' else patch.object(
                            google, 'get_json', return_value={}) if change == 'dimensions' else patch.object(
                            google, 'get_json', side_effect=self.sheet_properties):
                        table = agenda.read_worksheets('read', source)
                self.assertEqual(table['status'], 'unavailable')
        with patch.object(google, 'sheet_grid', return_value={}):
            result = self.run_check()
        self.assertIsNone(self.coverage(result)['scoped_rows'])
        self.assertEqual(self.coverage(result)['status'], 'unverified')
        self.assertTrue(self.diagnostics(result, 'worksheet_index_unverified'))

    def test_malformed_duplicate_headers_and_valid_empty_are_distinct(self):
        for headers in (WORKSHEET_HEADERS[:-1], WORKSHEET_HEADERS + ('Document',), ('Course', 'Document')):
            with self.subTest(headers=headers):
                self.grids[WORKSHEET_TAB] = worksheet_grid(headers=headers)
                self.assertEqual(agenda.read_worksheets('read', self.agenda())['status'], 'malformed')
        self.grids[WORKSHEET_TAB] = worksheet_grid()
        result = self.run_check()
        self.assertEqual((self.coverage(result)['table_status'], self.coverage(result)['scoped_rows']), ('complete', 0))
        self.assertEqual(self.diagnostics(result), [])

    def test_bounded_grid_coverage_reports_truncation_without_discarding_read_rows(self):
        self.index_dimensions = {'rowCount': 1200, 'columnCount': 40}
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(url='')])
        result = self.run_check()
        self.assertEqual((self.coverage(result)['table_status'], self.coverage(result)['rows_read']), ('truncated', 1000))
        self.assertEqual(self.coverage(result)['scoped_rows'], 1)
        self.assertTrue(self.diagnostics(result, 'worksheet_reference_conflict'))

    def test_suffix_weeks_and_courses_are_explicit_historical_locators(self):
        self.grids['🗂 Weeks'][4, 3] = cell('Wk 09b')
        self.grids[WORKSHEET_TAB] = worksheet_grid([
            self.index_row(week='W09', url=''), self.index_row(week='Week 09b', course='A&P', url=''),
            self.index_row(week='W09b', course='Biology club', url=''), self.index_row(week='W10', url='')])
        result = self.run_check()
        findings = self.diagnostics(result, 'worksheet_reference_conflict')
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]['class_key'], findings[0]['row']), ('anatomy', None))
        self.assertIn('Worksheets index Week 09b, row 3', findings[0]['reason'])
        self.assertEqual(self.coverage(result)['unscoped_rows'], 1)
        self.assertNotEqual(wp._worksheet_week('W08'), wp._worksheet_week('W08b'))
        self.assertEqual({wp._worksheet_week(v) for v in ('W8b', 'Wk 08b', 'Week08B')}, {'08b'})
        self.assertIsNone(wp._worksheet_week('W08b Ecology'))

    def test_no_matching_historical_week_does_not_claim_no_current_worksheets(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(week='W08')])
        result = self.run_check()
        self.assertEqual(self.coverage(result)['scoped_rows'], 0)
        self.assertIn('not current lesson membership', self.coverage(result)['scope'])
        self.assertEqual(self.diagnostics(result), [])
        self.assertEqual(self.src.calls['meta:' + self.DOC1], 0)

    def test_missing_references_malformed_ids_and_exact_conflicts_are_reported(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([
            self.index_row(url='', ident=''), self.index_row(url=''),
            self.index_row(ident='', url=deck_url(self.DOC1)),
            self.index_row(ident='bad ID', url=deck_url(self.DOC1)),
            self.index_row(ident=self.DOC2, url=deck_url(self.DOC1)),
            self.index_row(url=cell(deck_url(self.DOC1), hyperlink=deck_url(self.DOC2)))])
        result = self.run_check()
        findings = self.diagnostics(result, 'worksheet_reference_conflict')
        self.assertEqual(len(findings), 6)
        self.assertTrue(all(not e['blocking'] and e['row'] is None for e in findings))
        self.assertEqual(findings[-1]['evidence']['worksheet_rows'][0]['row'], 7)
        self.assertIn('neither reference', findings[-1]['reason'])
        self.assertEqual((self.src.calls['meta:' + self.DOC1], self.src.calls['meta:' + self.DOC2]), (1, 1))

    def test_same_id_url_variants_do_not_conflict_or_trigger_duplicate_within_one_row(self):
        urls = [deck_url(self.DOC1), deck_url(self.DOC1).replace('/edit', '/copy#slide=id.a'),
                'https://drive.google.com/open?id=' + self.DOC1]
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(url=' '.join(urls))])
        result = self.run_check()
        self.assertEqual(self.diagnostics(result), [])
        self.assertEqual(self.src.calls['meta:' + self.DOC1], 1)

    def test_unsupported_links_are_unverified_not_missing_and_are_never_fetched(self):
        urls = ['https://public.example/guide', 'http://public.example/guide', 'https://forms.gle/fictional',
                'https://docs.google.com/document/d/short', 'https://evil.example/document/d/' + self.DOC1]
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(url=u, ident='') for u in urls])
        result = self.run_check()
        self.assertEqual(len(self.diagnostics(result, 'worksheet_reference_unverified')), len(urls))
        self.assertEqual(self.diagnostics(result, 'worksheet_reference_conflict'), [])
        self.assertEqual((self.coverage(result)['metadata_checked'], self.coverage(result)['unsupported_rows']), (0, len(urls)))

    def test_duplicate_candidates_keep_all_rows_across_selected_weeks_and_exact_titles(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([
            self.index_row(), self.index_row(week='W10'),
            self.index_row(ident=self.DOC2, document='  FERMENTATION   guide '),
            self.index_row(ident=self.DOC2, document='Other guide')])
        result = self.run_check(weeks=2)
        repeated = [e for e in self.diagnostics(result, 'worksheet_duplicate_candidate') if 'Repeated' in e['reason']]
        titles = [e for e in self.diagnostics(result, 'worksheet_duplicate_candidate') if 'Identical' in e['reason']]
        self.assertEqual({tuple(r['row'] for r in e['evidence']['worksheet_rows']) for e in repeated}, {(2, 3), (4, 5)})
        self.assertTrue(all([r['row'] for r in e['evidence']['worksheet_rows']] == [2, 3, 4] for e in titles))
        self.assertTrue(all(not e['blocking'] for e in repeated + titles))
        self.assertEqual((self.src.calls['meta:' + self.DOC1], self.src.calls['meta:' + self.DOC2]), (1, 1))

    def test_unreadable_trashed_or_incomplete_metadata_is_not_healthy(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(), self.index_row(ident=self.DOC2)])
        self.src.files[self.DOC1]['trashed'] = True
        del self.src.files[self.DOC2]
        result = self.run_check()
        findings = self.diagnostics(result, 'worksheet_source_unverified')
        self.assertEqual(len(findings), 2)
        self.assertTrue(any('in the trash' in e['reason'] for e in findings))
        self.assertEqual(self.coverage(result)['metadata_unverified'], 1)
        self.src.files[self.DOC1].pop('trashed')
        result = self.run_check()
        self.assertEqual(self.coverage(result)['metadata_unverified'], 2)

    def test_cell_edits_change_fingerprint_and_metadata_is_refreshed_next_run(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(url='')])
        first = self.diagnostics(self.run_check(), 'worksheet_reference_conflict')[0]
        self.grids[WORKSHEET_TAB][2, 4] = cell('Changed guide name')
        second = self.diagnostics(self.run_check(), 'worksheet_reference_conflict')[0]
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.src.files[self.DOC1]['trashed'] = True
        self.assertTrue(self.diagnostics(self.run_check(), 'worksheet_source_unverified'))
        self.assertEqual(self.src.calls['meta:' + self.DOC1], 3)

    def test_folder_and_shortcut_references_are_not_verified_worksheet_files(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(), self.index_row(ident=self.DOC2)])
        self.src.files[self.DOC1]['mimeType'] = google.FOLDER
        self.src.files[self.DOC2].update(mimeType=google.SHORTCUT,
                                        shortcutDetails={'targetId': 'FICTIONALTARGETNOTFOLLOWED0001'})
        result = self.run_check()
        findings = self.diagnostics(result, 'worksheet_source_unverified')
        self.assertEqual(len(findings), 2)
        self.assertEqual(self.coverage(result)['metadata_unverified'], 2)
        self.assertTrue(any('folder, not a worksheet file' in e['reason'] for e in findings))
        self.assertTrue(any('was not followed' in e['reason'] for e in findings))
        self.assertEqual(self.src.calls['meta:FICTIONALTARGETNOTFOLLOWED0001'], 0)

    def test_same_id_is_memoized_even_when_row_metadata_is_unreadable(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(), self.index_row(week='W10')])
        del self.src.files[self.DOC1]
        result = self.run_check(weeks=2)
        self.assertEqual(self.src.calls['meta:' + self.DOC1], 1)
        self.assertEqual(len(self.diagnostics(result, 'worksheet_source_unverified')), 2)

    def test_different_titles_are_not_duplicate_candidates_based_on_partial_words(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(document='Sketching worksheet'),
            self.index_row(document='Evidence collection worksheet', ident=self.DOC2)])
        self.assertEqual(self.diagnostics(self.run_check(), 'worksheet_duplicate_candidate'), [])

    def test_metadata_budget_is_shared_across_weeks_and_retry_is_explicit(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(), self.index_row(week='W10', ident=self.DOC2)])
        with patch.object(wp, 'WORKSHEET_METADATA_LIMIT', 1):
            result = self.run_check(weeks=2)
        self.assertEqual(self.coverage(result, 1)['pending_ids'], 1)
        self.assertEqual(self.src.calls['meta:' + self.DOC2], 0)
        done = self.run_check(weeks=2)
        self.assertEqual(self.coverage(done, 1)['pending_ids'], 0)
        with patch.object(google, 'get_json', side_effect=AssertionError('no read after deadline')):
            pending = agenda.read_worksheets('read', self.agenda(), can_read=lambda: False)
        self.assertEqual(pending['status'], 'pending')

    def test_bounded_row_audit_keeps_unchecked_rows_explicit(self):
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(), self.index_row(week='W10', ident=self.DOC2)])
        with patch.object(wp, 'WORKSHEET_ROW_LIMIT', 1):
            result = self.run_check(weeks=2)
        self.assertEqual((self.coverage(result, 1)['scoped_rows'], self.coverage(result, 1)['rows_checked'],
                          self.coverage(result, 1)['deferred_rows']), (1, 0, 1))
        self.assertEqual(self.coverage(result, 1)['status'], 'unverified')
        self.assertEqual(self.src.calls['meta:' + self.DOC2], 0)

    def test_index_never_changes_lesson_forms_print_membership_or_teacher_controls(self):
        before = self.run_check()
        self.grids[WORKSHEET_TAB] = worksheet_grid([self.index_row(url=''), self.index_row(ident=self.DOC2)])
        after = self.run_check()
        for field in ('lessons', 'print_items', 'supplies', 'status'):
            self.assertEqual(before[field], after[field])
        self.assertTrue(all(e['kind'] not in {'needs_content', 'calendar_conflict', 'calendar_friday_unknown',
                                            'print_item_ambiguous'} for e in self.diagnostics(after)))
        self.assertEqual((after['external_writes'], after['model_calls'], self.writes), (0, 0, []))
        self.index_reads.clear()
        saved = wp.status(self.config, SHEET, '2026-10-19', 1, port=self.port)
        self.assertEqual(self.coverage(saved), self.coverage(after))
        self.assertEqual(self.index_reads, {})
        old = deepcopy(self.port.read([wp.week_id(SHEET, '2026-10-19', 'latest')]).documents[
            wp.week_id(SHEET, '2026-10-19', 'latest')].value)
        old.pop('worksheet_index')
        self.assertEqual(wp.compose([old], '2026-10-19', 1)['coverage']['worksheet_index'][0]['table_status'], 'not_checked')


class CalendarTests(unittest.TestCase):
    def setUp(self):
        weeks = agenda.Agenda(SHEET, 'Fictional Agenda', TABS, grids()).weeks
        self.calendars = {w['label']: wp.week_calendar(w) for w in weeks}

    def test_weeks_cell_is_the_calendar_for_fridays_and_closed_dates(self):
        wk08, wk09, wk10, wk11 = (self.calendars[k] for k in ('Wk 08', 'Wk 09', 'Wk 10', 'Wk 11'))
        self.assertEqual(wk08['closed'].keys(), {'2026-10-16'})
        self.assertEqual(wk08['fridays'], {'2026-10-16': 'A'})
        self.assertEqual((wk09['fridays'], wk09['closed']), ({'2026-10-23': 'A'}, {}))
        self.assertEqual(wk10['fridays'], {'2026-10-30': 'B'})
        self.assertEqual((wk11['fridays'], wk11['closed']), ({}, {}))

    def test_sections_meet_on_their_A_or_B_days_and_an_unstated_friday_is_unknown(self):
        def meets(week, cls):
            return {d['day']: d['meets'] for d in wp.class_days(self.calendars[week], cls)}
        self.assertEqual(meets('Wk 09', 'biology'), {'Mon': True, 'Tue': False, 'Wed': True, 'Thu': False, 'Fri': True})
        self.assertEqual(meets('Wk 09', 'anatomy'), {'Mon': False, 'Tue': True, 'Wed': False, 'Thu': True, 'Fri': False})
        self.assertEqual(meets('Wk 10', 'anatomy')['Fri'], True)
        self.assertFalse(meets('Wk 08', 'forensics')['Fri'], 'in-service closes the A Friday')
        self.assertIsNone(meets('Wk 11', 'biology')['Fri'])
        self.assertEqual([d['date'] for d in wp.class_days(self.calendars['Wk 09'], 'biology') if d['meets']],
                         ['2026-10-19', '2026-10-21', '2026-10-23'])

    def test_plan_cells_read_claims_about_this_class_only(self):
        monday = wp.date(2026, 10, 19)
        self.assertTrue(wp.plan_reading('B Friday — Forensics does not meet', 'forensics', 'Fri', monday)['not_meeting'])
        self.assertFalse(wp.plan_reading('A&P does not meet; Bio lab', 'biology', 'Fri', monday)['not_meeting'])
        self.assertFalse(wp.plan_reading('Closed-toe shoes required for the lab', 'biology', 'Mon', monday)['not_meeting'])
        reading = wp.plan_reading('Lab (moved from Fri 10/23 — in-service)', 'biology', 'Mon', monday)
        self.assertFalse(reading['not_meeting'])
        self.assertTrue(reading['content'])
        self.assertEqual([(c['date'], c['closed']) for c in reading['dated']], [('2026-10-23', True)])


class LessonNameTests(unittest.TestCase):
    def test_weekly_folder_names_are_the_lesson_names(self):
        self.assertEqual(wp.folder_lesson_name('Cycle 06 — Cellular Respiration & Fermentation (Unit 1)'),
                         'Cellular Respiration & Fermentation')
        self.assertEqual(wp.folder_lesson_name('Week 08 - History of Forensics'), 'History of Forensics')
        self.assertEqual(wp.folder_lesson_name('Week 08b - Crime Scene to Courtroom (Mock Trial)'),
                         'Crime Scene to Courtroom (Mock Trial)')
        self.assertEqual(wp.folder_lesson_name('Week 07'), '')

    def test_explicit_agenda_topic_wins_over_retained_folder_names(self):
        self.assertEqual(wp.lesson_name(['Cycle 06 — Cellular Respiration & Fermentation (Unit 1)'], 'Fermentation'),
                         'Fermentation')
        self.assertEqual(wp.lesson_name(['Week 08 - History of Forensics', 'Week 08 - History of Forensics',
                                         'Week 08b - Crime Scene to Courtroom (Mock Trial)'], 'Unit 1 Review'),
                         'Unit 1 Review')
        self.assertEqual(wp.lesson_name(['Week 07'], 'Body Tissues (cont.)'), 'Body Tissues (cont.)')
        self.assertEqual(wp.lesson_name([], 'Body Tissues (cont.)'), 'Body Tissues (cont.)')

    def test_blank_agenda_topic_retains_deduplicated_folder_fallback(self):
        folders = ['Week 08 - History of Forensics', 'Week 08 - History of Forensics',
                   'Week 08b - Crime Scene to Courtroom (Mock Trial)']
        for topic in ('', '  ', None):
            with self.subTest(topic=topic):
                self.assertEqual(wp.lesson_name(folders, topic),
                                 'History of Forensics + Crime Scene to Courtroom (Mock Trial)')
        self.assertEqual(wp.lesson_name(['Week 07'], ''), '')
        self.assertEqual(wp.lesson_name([], ''), '')

    def test_retained_reference_folders_cannot_rename_an_explicit_agenda_lesson(self):
        class Ctx:
            def file(self, ident):
                return {'FICTIONALCOURTFOLDER0001': {'name': 'Week 08b - Crime Scene to Courtroom (Mock Trial)'},
                        'FICTIONALGENERALFOLDER01': {'name': '03 FORENSICS (Public)'}}.get(ident, {'error': 'x'})
        lesson = {'topic': 'Evidence Documentation', 'exceptions': [{'lesson': 'Evidence Documentation'}],
                  'supply_rows': [{'lesson': 'Evidence Documentation', 'item': 'Paper'}],
                  'lesson_id': 'fictional-lesson', 'refs': {'forms': ['FICTIONALFORM00000000001']},
                  'sources': [
                      {'id': 'FICTIONALHISTFOLDER0001', 'roles': ['folder'], 'accessible': True, 'trashed': False,
                       'name': 'Week 08 - History of Forensics'},
                      {'id': 'FICTIONALHISTDECK000001', 'roles': ['teacher_deck'], 'accessible': True,
                       'parents': ['FICTIONALHISTFOLDER0001', 'FICTIONALGENERALFOLDER01']},
                      {'id': 'FICTIONALCOURTDECK00001', 'roles': ['second_deck'], 'accessible': True,
                       'parents': ['FICTIONALCOURTFOLDER0001']}]}
        before = deepcopy(lesson)
        wp._name_lesson(Ctx(), lesson)
        self.assertEqual(lesson['topic'], 'Evidence Documentation')
        self.assertEqual(lesson['agenda_topic'], 'Evidence Documentation')
        self.assertEqual(lesson['exceptions'][0]['lesson'], lesson['topic'])
        self.assertEqual(lesson['supply_rows'][0]['lesson'], lesson['topic'])
        for field in ('lesson_id', 'refs', 'sources'):
            self.assertEqual(lesson[field], before[field])

    def test_soft_line_breaks_split_a_resource_list(self):
        runs = [{'textRun': {'content': 'Activity and resource links\nWorld Lab — Yeast Balloons\u000b'}},
                {'textRun': {'content': 'https://example.org/yeast-balloons\u000bOther — Thing\u000bhttps://example.org/other\n'}}]
        page = {'objectId': 'p1', 'pageElements': [{'shape': {'text': {'textElements': runs}}}]}
        outline = wp.outline_native({'slides': [page]})
        links = wp.web_links(outline[0])
        self.assertEqual(wp.match_link('World Lab — Yeast Balloons', links)['url'], 'https://example.org/yeast-balloons')


class DeckSupplyTests(unittest.TestCase):
    def test_deck_lines_are_split_and_kept_only_for_this_weeks_activity(self):
        week = wp._week_text({'topic': 'Fermentation', 'prep': 'LAB Yeast Fermentation Lab',
                              'plans': [{'plan': 'Fermentation lesson, yeast-balloon quick demo', 'note': ''}]})
        self.assertEqual(wp.deck_supply_items(
            'Active dry yeast (1 packet per class), sugar, balloons, and small clear plastic bottles '
            '\u2014 yeast balloon lab (Day 2)', week, 'buy'),
            ['Active dry yeast (1 packet per class)', 'sugar', 'balloons', 'small clear plastic bottles'])
        self.assertEqual(wp.deck_supply_items(
            'Marshmallows, bamboo skewers or metal tongs, and a lighter \u2014 burning-marshmallow demo (Day 1)',
            week, 'buy'), [])
        self.assertEqual(wp.deck_supply_items('Safety glasses and paper towels', week, 'put_out'),
                         ['Safety glasses', 'paper towels'])

    def test_handouts_on_a_put_out_line_are_not_supplies(self):
        self.assertEqual(wp.deck_supply_items('CA1 / CA2 worksheets in the pickup tray', set(), 'put_out'), [])
        self.assertEqual(wp.deck_supply_items('Bottles, balloons, yeast, sugar, and funnels at each lab station (Day 2)',
                                              set(), 'put_out'),
                         ['Bottles', 'balloons', 'yeast', 'sugar', 'funnels'])


WEB_PAGE = 'https://science.example.org/activities/yeast-inflated-balloons'


def outline_slide(number, *texts):
    return {'number': number, 'object_id': 'page%d' % number, 'texts': list(texts), 'links': []}


class DeckTextTests(unittest.TestCase):
    def test_linked_text_typed_urls_and_table_cells_name_their_web_links(self):
        runs = [{'textRun': {'content': 'Activity and resource links\n'}},
                {'textRun': {'content': 'Science World — Yeast-Inflated Balloons',
                             'style': {'link': {'url': WEB_PAGE}}}},
                {'textRun': {'content': '\nGo to slide 3', 'style': {'link': {'slideIndex': 2}}}},
                {'textRun': {'content': '\nElephant toothpaste demo: https://demos.example.org/toothpaste.\n'}}]
        cells = [{'text': {'textElements': [{'textRun': {'content': 'Density column demo\n'}}]}},
                 {'text': {'textElements': [{'textRun': {'content': 'https://demos.example.org/density\n'}}]}}]
        outline = wp.outline_native({'slides': [{'objectId': 'links', 'pageElements': [
            {'objectId': 's1', 'shape': {'text': {'textElements': runs}}},
            {'objectId': 't1', 'table': {'tableRows': [{'tableCells': cells}]}}]}]})
        self.assertEqual([(l['label'], l['url'], l['slide']) for l in wp.deck_summary(outline)['web_links']], [
            ('Science World — Yeast-Inflated Balloons', WEB_PAGE, 1),
            ('Elephant toothpaste demo', 'https://demos.example.org/toothpaste', 1),
            ('Density column demo', 'https://demos.example.org/density', 1)])

    def test_a_print_line_matches_one_named_web_link_or_none(self):
        links = [{'label': 'Science World — Yeast-Inflated Balloons', 'url': WEB_PAGE, 'slide': 5},
                 {'label': 'Balloon safety video', 'url': 'https://video.example.org/balloons', 'slide': 5}]
        self.assertEqual(wp.match_link('Science World — Yeast-Inflated Balloons (1 per group)', links)['url'], WEB_PAGE)
        self.assertIsNone(wp.match_link('Balloons', links), 'one shared word is not a match')
        self.assertIsNone(wp.match_link('Lab safety card', links))
        twins = links + [{'label': 'Science World: Yeast Inflated Balloons', 'url': 'https://mirror.example.org/y', 'slide': 6}]
        self.assertIsNone(wp.match_link('Science World — Yeast-Inflated Balloons', twins), 'two pages, no guess')
        line = {'text': 'Gel diagram (1 per student)', 'url': 'https://diagrams.example.org/gel.pdf'}
        self.assertEqual(wp.web_source(line, links), {'url': 'https://diagrams.example.org/gel.pdf', 'from': 'the print line'})
        self.assertEqual(wp.web_source({'text': 'Gel diagram https://diagrams.example.org/gel.pdf'}, [])['url'],
                         'https://diagrams.example.org/gel.pdf')
        self.assertIsNone(wp.web_source({'text': 'Lab safety card (1 per group)'}, links))

    def test_authoring_notes_on_student_slides_are_found_once_per_slide(self):
        draft = '[DRAFT — confirm which unit exam is used for the end-of-unit test before this deck is shared]'
        summary = wp.deck_summary([
            outline_slide(1, 'Unit review', draft),
            outline_slide(2, 'Enzymes speed reactions [added — example from the lab]', 'Rate [corrected: mL/min]'),
            outline_slide(3, '“Enzymes are catalysts” [verify verbatim against the text]'),
            outline_slide(4, 'TEACHER REFERENCE\n[DRAFT — swap the demo]\n[TODO: buy yeast]'),
            outline_slide(5, '[Note: use mL]', '[Verify your prediction with a partner]', '[Type your answer here]',
                          '[Draft — your first idea]', 'Your answer [[DRAFT:C06:Q1]] [TODO: widen box]',
                          '[[NOTES:C06:Q1]]', '[ ]'),
            outline_slide(6, '[TODO: add the graph]', '[NOTE — Friday is a B Friday]')])
        self.assertEqual(summary['authoring_notes'], [
            {'slide': 1, 'count': 1, 'note': draft[:79].rstrip() + '…'},
            {'slide': 2, 'count': 2, 'note': '[added — example from the lab]'},
            {'slide': 3, 'count': 1, 'note': '[verify verbatim against the text]'},
            {'slide': 6, 'count': 2, 'note': '[TODO: add the graph]'}])
        self.assertEqual(summary['teacher_only_slides'], [4])


class StudentDayDateTests(Base):
    def scope(self, class_key='biology', monday='2026-10-19', closures='Fri 10/23 = B Friday'):
        calendar = wp.week_calendar({'label': 'Fictional week', 'monday': monday, 'closures': closures})
        return {'monday': monday, 'days': wp.class_days(calendar, class_key),
                'calendar_problems': calendar['problems']}

    def check_headings(self, texts, **scope):
        summary = wp.deck_summary([outline_slide(i + 1, text) for i, text in enumerate(texts)])
        return wp.student_day_date_check(summary['student_day_headings'], self.scope(**scope))

    def test_whole_student_headings_only(self):
        summary = wp.deck_summary([
            outline_slide(1, 'Day 1 — Monday 10/19/2026'),
            outline_slide(2, 'TEACHER REFERENCE', 'Day 2 — Tuesday 10/20/2026'),
            outline_slide(3, 'The historical event was Tue 10/20/2026', 'Notes due Tue 10/20/2026',
                          'Day 2 — Tue 10/20/2026 or Wed 10/21/2026',
                          'Day 2 — Tue 10/20/2026: analyze the example', 'Tue 10/20/2026'),
            outline_slide(4, 'Day 2: Wednesday 10/21', 'Day 2: Wednesday 10/21')])
        self.assertEqual([h['slide'] for h in summary['student_day_headings']], [1, 4])
        result = wp.student_day_date_check(summary['student_day_headings'], self.scope())
        self.assertEqual((result['status'], result['checked'], result['findings']), ('checked', 2, []))

    def test_calendar_conflicts_preserve_exact_origin_and_reason(self):
        result = self.check_headings(['Day 1 — Mon 10/20/2026', 'Day 2 — Fri 10/23/2026',
                                      'Day 3 — Sat 10/24/2026'])
        self.assertEqual(result['checked'], 3)
        self.assertEqual(result['findings'][0]['problems'], ['weekday_mismatch', 'not_a_class_meeting'])
        self.assertEqual(result['findings'][1]['calendar_reason'], 'B Friday')
        self.assertEqual(result['findings'][2]['calendar_reason'], 'weekend')
        ap = self.check_headings(['Day 1 — Tue 10/20', 'Day 2 — Fri 10/23'], class_key='anatomy')
        self.assertEqual((ap['checked'], ap['findings']), (2, []))

    def test_closures_and_uncertain_calendars_are_not_inferred(self):
        closed = self.check_headings(['Day 1 — Mon 10/19'], closures='Mon 10/19 Schools Closed')
        self.assertIn('closed', closed['findings'][0]['calendar_reason'])
        unknown = self.check_headings(['Day 1 — Fri 10/23'], closures='')
        self.assertEqual((unknown['status'], unknown['checked'], unknown['findings']), ('unverified', 0, []))
        malformed = self.check_headings(['Day 1 — Mon 10/19'], closures='Tue 10/19 Schools Closed')
        self.assertEqual(malformed['unverified'][0]['reason'], 'calendar_unverified')
        self.assertEqual(malformed['findings'], [])

    def test_invalid_and_other_week_dates_are_unverified_with_no_lesson_guess(self):
        result = self.check_headings(['Day 1 — Mon 10/12/2026', 'Day 2 — Tue 2/30/2026',
                                      'Day 3 — Wed 10/28', 'Day 4 — Mon 10/19/2025'])
        self.assertEqual((result['status'], result['checked'], result['findings']), ('unverified', 0, []))
        self.assertEqual(len(result['unverified']), 4)
        self.assertEqual(result['unverified'][1]['reason'], 'invalid_date')
        new_year = self.check_headings(['Day 1 — Thu 1/1'], class_key='anatomy', monday='2026-12-28', closures='')
        self.assertEqual(new_year['findings'][0]['date'], '2027-01-01')
        self.assertEqual(new_year['findings'][0]['problems'], ['weekday_mismatch'])

    def test_same_deck_date_is_scoped_independently_without_rewriting(self):
        summary = wp.deck_summary([outline_slide(1, 'Day 1 — Tue 10/20/2026')])
        headings = deepcopy(summary['student_day_headings'])
        bio = wp.student_day_date_check(headings, self.scope())
        ap = wp.student_day_date_check(headings, self.scope(class_key='anatomy'))
        later = wp.student_day_date_check(headings, self.scope(monday='2026-10-26', closures=''))
        self.assertEqual(len(bio['findings']), 1)
        self.assertEqual(ap['findings'], [])
        self.assertEqual(later['status'], 'unverified')
        self.assertEqual(headings, summary['student_day_headings'])

    def test_saved_nonblocking_diagnostic_revision_and_calendar_refresh(self):
        original = self.src.native_presentation
        def presentation(token, ident):
            pres = original(token, ident)
            if ident == FOR_DECK:
                pres['slides'][0]['pageElements'].append({'shape': {'text': {'textElements': [
                    {'textRun': {'content': 'Day 1 — Tue 10/20/2026'}}]}}})
            return pres
        with patch.object(google, 'native_presentation', side_effect=presentation):
            result = self.run_check()
            found = [e for e in result['exceptions'] if e['kind'] == 'student_day_date_conflict']
            self.assertEqual(len(found), 1)
            item = found[0]
            self.assertFalse(item['blocking'])
            self.assertEqual((item['class_key'], item['row'], item['slides']), ('forensics', 5, [1]))
            self.assertIn('#slide=id.', item['source_url'])
            self.assertNotIn(item['fingerprint'], self.lesson(result, 'forensics', 6)['exceptions'])
            saved = wp.status(self.config, SHEET, '2026-10-19', 1, port=self.port)
            self.assertIn(item, saved['exceptions'])
            calls = self.src.calls['native:' + FOR_DECK]
            again = self.run_check()
            self.assertEqual(self.src.calls['native:' + FOR_DECK], calls)
            self.grids['🗂 Weeks'][4, 5] = cell('Tue 10/20 Schools Closed\nFri 10/23 = A Friday')
            calendar_changed = [e for e in self.run_check()['exceptions'] if e['kind'] == 'student_day_date_conflict']
            self.assertIn('closed', calendar_changed[0]['evidence']['calendar_reason'])
            self.assertNotEqual(calendar_changed[0]['fingerprint'], item['fingerprint'])
            self.assertEqual(self.src.calls['native:' + FOR_DECK], calls)
            self.src.bump(FOR_DECK)
            changed = [e for e in self.run_check()['exceptions'] if e['kind'] == 'student_day_date_conflict']
            self.assertNotEqual(changed[0]['fingerprint'], item['fingerprint'])
        self.assertEqual((result['external_writes'], result['model_calls'], len(self.writes)), (0, 0, 0))


class NumberedSlideLinkTests(Base):
    def native(self, runs, ids=('origin', 'destination', 'third')):
        return {'slides': [{'objectId': ident, 'pageElements': ([
            {'elementGroup': {'children': [{'table': {'tableRows': [{'tableCells': [
                {'text': {'textElements': runs}}]}]}}]}}] if i == 0 else [])}
            for i, ident in enumerate(ids)]}

    def run_text(self, value, target=None):
        return {'textRun': {'content': value, 'style': {'link': target} if target else {}}}

    def check(self, runs, ids=('origin', 'destination', 'third')):
        return wp.deck_summary(wp.outline_native(self.native(runs, ids)))['slide_reference_check']

    def test_exact_native_page_target_and_zero_based_index(self):
        for target in ({'pageObjectId': 'destination'}, {'slideIndex': 1}):
            with self.subTest(target=target):
                result = self.check([self.run_text('Slide 3', target)])
                self.assertEqual(result['checked'], 1)
                self.assertEqual(result['findings'][0], {'slide': 1, 'label': 'Slide 3',
                    'displayed_slide': 3, 'target_slide': 2, 'target': target, 'problem': 'number_mismatch'})
                self.assertEqual(self.check([self.run_text('Slide 2', target)])['findings'], [])
        result = self.check([self.run_text('Go to slide 2.', {'slideIndex': 0})])
        self.assertEqual(result['findings'][0]['target_slide'], 1)

    def test_adjacent_style_runs_join_only_for_the_same_target(self):
        target = {'pageObjectId': 'destination'}
        runs = [self.run_text('See '), self.run_text('Sli', target), self.run_text('de 3', target)]
        self.assertEqual(len(self.check(runs)['findings']), 1)
        runs[2]['textRun']['style']['link'] = {'pageObjectId': 'third'}
        self.assertEqual(self.check(runs)['findings'], [])
        runs[2] = self.run_text('de 3')
        self.assertEqual(self.check(runs)['findings'], [])

    def test_partial_or_ambiguous_labels_never_borrow_a_target(self):
        target = {'pageObjectId': 'destination'}
        for runs in (
            [self.run_text('Slide 3', target), self.run_text('0')],
            [self.run_text('Slide 3', target), self.run_text('–4')],
            [self.run_text('Slide 3', target), self.run_text(' and 4')],
            [self.run_text('Slide 3', target), self.run_text(', slide 4')],
            [self.run_text('Slide 3', target), self.run_text(' to 4')],
            [self.run_text('Slide 3', target), self.run_text(' through 4')],
            [self.run_text('Slide 3', target), self.run_text(' & 4')],
            [self.run_text('Slide 3', target), self.run_text(' or 4')],
            [self.run_text('Slide 3', target), self.run_text(' and slides 4–5')],
            [self.run_text('Slide 3–4', target)], [self.run_text('Slides 3 and 4', target)],
            [self.run_text('Explain slide 3 in your own words', target)],
            [self.run_text('Slide 3', {'url': 'https://docs.google.com/presentation/d/FICTIONALOTHERDECK00001/edit'})],
            [self.run_text('Slide 3', {'relativeLink': 'NEXT_SLIDE'})],
            [self.run_text('Slide 3', {'slideIndex': True})],
            [self.run_text('Slide 3', {'slideIndex': -1})],
            [self.run_text('Slide 3', {'slideIndex': 1, 'pageObjectId': 'destination'})],
        ):
            with self.subTest(runs=runs):
                self.assertEqual(self.check(runs)['findings'], [])

    def test_missing_target_is_separate_and_no_replacement_is_invented(self):
        for target in ({'pageObjectId': 'deleted'}, {'slideIndex': 12}):
            result = self.check([self.run_text('Slide 2', target)])
            self.assertEqual(result['findings'][0]['problem'], 'missing_target')
            self.assertIsNone(result['findings'][0]['target_slide'])

    def test_missing_duplicate_page_ids_and_non_native_coverage(self):
        for ids in (('origin', '', 'third'), ('origin', 'origin', 'third')):
            result = self.check([self.run_text('Slide 3', {'slideIndex': 1})], ids)
            self.assertEqual(result['status'], 'unverified')
            self.assertEqual(result['findings'], [])
        self.assertEqual(wp.deck_summary([outline_slide(1, 'Slide 3')])['slide_reference_check']['status'],
                         'not_supported')

    def test_saved_diagnostic_has_exact_lesson_revision_and_source_slide(self):
        def read(token, ident):
            pres = self.src.native_presentation(token, ident)
            if ident == FOR_DECK:
                target = pres['slides'][1]['objectId']
                pres['slides'][0]['pageElements'].append({'shape': {'text': {'textElements': [
                    self.run_text('Slide 1', {'pageObjectId': target})]}}})
            return pres
        with patch.object(google, 'native_presentation', side_effect=read):
            result = self.run_check()
            found = [e for e in self.exceptions(result, 'forensics') if e['kind'] == 'deck_slide_reference_mismatch']
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]['row'], found[0]['slides'], found[0]['evidence']['target_slide']), (5, [1], 2))
            self.assertEqual(found[0]['source_url'], deck_url(FOR_DECK) + '#slide=id.' + FOR_DECK[-4:] + '_p1')
            self.assertNotIn(found[0]['fingerprint'], self.lesson(result, 'forensics', 6)['exceptions'])
            count = self.src.calls['native:' + FOR_DECK]
            same = [e for e in self.run_check()['exceptions'] if e['kind'] == 'deck_slide_reference_mismatch']
            self.assertEqual(same[0]['fingerprint'], found[0]['fingerprint'])
            self.assertEqual(self.src.calls['native:' + FOR_DECK], count)
            self.src.bump(FOR_DECK)
            changed = [e for e in self.run_check()['exceptions'] if e['kind'] == 'deck_slide_reference_mismatch']
            self.assertNotEqual(changed[0]['fingerprint'], found[0]['fingerprint'])
        self.assertEqual((result['external_writes'], result['model_calls'], len(self.writes)), (0, 0, 0))


class DeckLinkTargetTests(Base):
    shown_url = 'https://docs.google.com/document/d/FICTIONALDISPLAYEDFILE0001/edit'
    actual_url = 'https://drive.google.com/file/d/FICTIONALOTHERFILE00000001/view'

    def linked_runs(self, shown=None, target=None):
        shown, target = shown or self.shown_url, target or self.actual_url
        split = len(shown) // 2
        return [{'textRun': {'content': 'Open this resource: '}}] + [
            {'textRun': {'content': part, 'style': {'link': {'url': target}}}}
            for part in (shown[:split], shown[split:])]

    def presentation_with_link(self, token, ident):
        pres = self.src.native_presentation(token, ident)
        if ident == FOR_DECK:
            pres['slides'][0]['pageElements'].append({'shape': {'text': {'textElements': self.linked_runs()}}})
        return pres

    def test_native_split_runs_groups_and_tables_keep_exact_hyperlinks(self):
        shape = {'shape': {'text': {'textElements': self.linked_runs()}}}
        table = {'table': {'tableRows': [{'tableCells': [{'text': {'textElements': self.linked_runs()}}]}]}}
        outline = wp.outline_native({'slides': [{'objectId': 'actual-slide', 'pageElements': [
            {'elementGroup': {'children': [shape, table]}}]}]})
        self.assertEqual(wp.deck_summary(outline)['link_mismatches'], [
            {'slide': 1, 'displayed_url': self.shown_url, 'target_url': self.actual_url}])
        self.assertEqual(outline[0]['links'][0]['at'], len('Open this resource: '))

    def test_equivalent_addresses_labels_unsupported_hosts_and_partial_spans_do_not_guess(self):
        variants = [
            (self.shown_url, self.shown_url.replace('/edit', '/copy#slide=id.abc')),
            (self.shown_url, 'https://drive.google.com/open?id=FICTIONALDISPLAYEDFILE0001'),
            ('Worksheet', self.actual_url),
            (self.shown_url, 'https://elsewhere.example/file/d/FICTIONALOTHERFILE00000001/view'),
            (self.shown_url.replace('docs.google.com', 'docs.google.com.evil.example'), self.actual_url),
            ('https://docs.google.com/forms/d/e/FICTIONALPUBLISHEDFORM0001/viewform', self.actual_url)]
        for shown, target in variants:
            with self.subTest(shown=shown, target=target):
                outline = wp.outline_native({'slides': [{'pageElements': [
                    {'shape': {'text': {'textElements': self.linked_runs(shown, target)}}}]}]})
                self.assertEqual(wp.deck_link_mismatches(outline), [])
        runs = self.linked_runs()
        runs[-1]['textRun'].pop('style')
        outline = wp.outline_native({'slides': [{'pageElements': [{'shape': {'text': {'textElements': runs}}}]}]})
        self.assertEqual(wp.deck_link_mismatches(outline), [], 'a partial URL link is not a whole-address target')

    def test_adjacent_links_never_borrow_the_other_targets(self):
        runs = self.linked_runs(self.shown_url, self.shown_url)
        runs += [{'textRun': {'content': ' then '}}] + self.linked_runs(self.actual_url, self.actual_url)
        outline = wp.outline_native({'slides': [{'pageElements': [{'shape': {'text': {'textElements': runs}}}]}]})
        self.assertEqual(wp.deck_link_mismatches(outline), [])
        runs[-1]['textRun']['style']['link']['url'] = self.shown_url
        self.assertEqual(wp.deck_link_mismatches(wp.outline_native({'slides': [{'pageElements': [
            {'shape': {'text': {'textElements': runs}}}]}]})), [], 'different targets across one URL are ambiguous')

    def linked_pptx(self):
        raw = pptx([[('first', self.shown_url, (0, 0, 900, 50))], [('second', 'Other slide', (0, 0, 900, 50))]])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(parts['ppt/slides/slide1.xml'])
        tree = root.find(completion.P + 'cSld/' + completion.P + 'spTree')
        shape = tree.find(completion.P + 'sp')
        body = shape.find(completion.P + 'txBody')
        body.clear()
        paragraph = ET.SubElement(body, completion.A + 'p')
        for text in (self.shown_url[:30], self.shown_url[30:]):
            run = ET.SubElement(paragraph, completion.A + 'r')
            props = ET.SubElement(run, completion.A + 'rPr')
            ET.SubElement(props, completion.A + 'hlinkClick', {completion.R + 'id': 'link1'})
            ET.SubElement(run, completion.A + 't').text = text
        tree.remove(shape)
        group = ET.SubElement(tree, completion.P + 'grpSp')
        group.append(shape)
        # A table text body has the DrawingML namespace, unlike a shape body.
        frame = ET.SubElement(tree, completion.P + 'graphicFrame')
        graphic = ET.SubElement(frame, completion.A + 'graphic')
        data = ET.SubElement(graphic, completion.A + 'graphicData')
        table = ET.SubElement(data, completion.A + 'tbl')
        row = ET.SubElement(table, completion.A + 'tr', {'h': '50'})
        cell_node = ET.SubElement(row, completion.A + 'tc')
        table_body = deepcopy(body)
        table_body.tag = completion.A + 'txBody'
        cell_node.append(table_body)
        parts['ppt/slides/slide1.xml'] = ET.tostring(root)
        rels = ET.Element('Relationships', {'xmlns': 'http://schemas.openxmlformats.org/package/2006/relationships'})
        ET.SubElement(rels, 'Relationship', {'Id': 'link1', 'Type':
            'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
            'TargetMode': 'External', 'Target': self.actual_url})
        parts['ppt/slides/_rels/slide1.xml.rels'] = ET.tostring(rels)
        # Presentation order differs from slide filenames; the diagnostic must follow it.
        pres = ET.fromstring(parts['ppt/presentation.xml'])
        order = pres.find(completion.P + 'sldIdLst')
        order[:] = list(reversed(list(order)))
        parts['ppt/presentation.xml'] = ET.tostring(pres)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for name, data in parts.items():
                archive.writestr(name, data)
        return out.getvalue()

    def test_pptx_groups_tables_and_presentation_order_retain_link_pairs(self):
        raw = self.linked_pptx()
        deck = completion.read_deck(raw)
        outline = wp.outline_deck(deck, raw)
        self.assertEqual([s['texts'] for s in outline], [[u['text'] for u in s['units']] for s in deck['slides']])
        self.assertEqual(wp.deck_summary(outline)['link_mismatches'], [
            {'slide': 2, 'displayed_url': self.shown_url, 'target_url': self.actual_url}])
        node = self.src.files[FOR_DECK]
        node['mimeType'] = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        with patch.object(google, 'deck_bytes', return_value=raw):
            summary = wp._outline('read-token', wp._node(wp._Run('read-token', self.port, 10**20, lambda *a: None),
                                                        'teacher_deck', FOR_DECK, deck_url(FOR_DECK)))
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['link_mismatches'][0]['slide'], 2)

    def test_live_path_preserves_lesson_scope_revision_cache_and_native_slide_link(self):
        with patch.object(google, 'native_presentation', side_effect=self.presentation_with_link):
            result = self.run_check()
            found = [e for e in self.exceptions(result, 'forensics') if e['kind'] == 'deck_link_target_mismatch']
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]['row'], found[0]['slides']), (5, [1]))
            self.assertEqual(found[0]['evidence']['target_url'], self.actual_url)
            self.assertEqual(found[0]['source_url'], deck_url(FOR_DECK) + '#slide=id.' + FOR_DECK[-4:] + '_p1')
            self.assertNotIn(found[0]['fingerprint'], self.lesson(result, 'forensics', 6)['exceptions'])
            count = self.src.calls['native:' + FOR_DECK]
            self.assertEqual([e['fingerprint'] for e in self.run_check()['exceptions']
                              if e['kind'] == 'deck_link_target_mismatch'], [found[0]['fingerprint']])
            self.assertEqual(self.src.calls['native:' + FOR_DECK], count)
            self.src.bump(FOR_DECK)
            updated = [e for e in self.run_check()['exceptions'] if e['kind'] == 'deck_link_target_mismatch']
            self.assertNotEqual(updated[0]['fingerprint'], found[0]['fingerprint'])
            self.assertEqual(self.src.calls['native:' + FOR_DECK], count + 1)
        self.assertEqual((result['external_writes'], result['model_calls'], len(self.writes)), (0, 0, 0))

    def test_same_source_in_two_lesson_rows_keeps_two_scoped_findings(self):
        cells = self.grids['🔬 Forensics']
        cells[6, 17] = cell(deck_url(FOR_DECK))
        cells[6, 13] = shown('Q', 6, LABELS['teacher'], deck_url(FOR_DECK))
        cells[6, 16] = cell(deck_url(FOR_DECK).replace('/edit', '/copy'))
        cells[6, 12] = shown('P', 6, LABELS['student'], deck_url(FOR_DECK).replace('/edit', '/copy'))
        with patch.object(google, 'native_presentation', side_effect=self.presentation_with_link):
            found = [e for e in self.run_check()['exceptions'] if e['kind'] == 'deck_link_target_mismatch']
        self.assertEqual([e['row'] for e in found], [5, 6])
        self.assertEqual(len({e['fingerprint'] for e in found}), 2)

    def test_wrong_native_identity_and_source_change_during_outline_are_not_cached(self):
        for failure in ('wrong_identity', 'changed_source'):
            with self.subTest(failure=failure):
                ctx = wp._Run('read-token', self.port, 10**20, lambda *a: None)
                node = wp._node(ctx, 'teacher_deck', FOR_DECK, deck_url(FOR_DECK))
                def read(token, ident):
                    pres = self.presentation_with_link(token, ident)
                    if failure == 'wrong_identity':
                        pres['presentationId'] = FOR_DECK2
                    else:
                        self.src.bump(ident)
                    return pres
                with patch.object(google, 'native_presentation', side_effect=read):
                    result = ctx.check('outline', node, wp._outline)
                self.assertFalse(result['ok'])
                self.assertNotIn('link_mismatches', result)
                cache = wp.check_id('outline', node['id'], node['revision'])
                self.assertIsNone(self.port.read([cache]).documents[cache])

    def test_malformed_pptx_relationships_are_unreadable_not_a_clean_link_check(self):
        raw = self.linked_pptx()
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w') as target:
            for name in source.namelist():
                target.writestr(name, b'<broken' if name.endswith('slide1.xml.rels') else source.read(name))
        malformed = out.getvalue()
        with self.assertRaises(completion.Unreadable):
            wp.outline_deck(completion.read_deck(malformed), malformed)


    def test_unproven_pptx_presentation_order_never_reports_a_guessed_slide(self):
        raw = self.linked_pptx()  # the displayed link belongs to slide 2, not filename slide1.xml
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        relpath = 'ppt/_rels/presentation.xml.rels'
        for failure in ('missing_relationships', 'malformed_relationships', 'unresolved_reference',
                        'missing_slide_part', 'duplicate_reference', 'missing_slide_list'):
            with self.subTest(failure=failure):
                parts = dict(original)
                if failure == 'missing_relationships':
                    del parts[relpath]
                elif failure == 'malformed_relationships':
                    parts[relpath] = b'<broken'
                elif failure == 'missing_slide_part':
                    del parts['ppt/slides/slide2.xml']
                else:
                    pres = ET.fromstring(parts['ppt/presentation.xml'])
                    order = pres.find(completion.P + 'sldIdLst')
                    if failure == 'unresolved_reference':
                        order[0].set(completion.R + 'id', 'missingRelationship')
                    elif failure == 'duplicate_reference':
                        order[0].set(completion.R + 'id', order[1].get(completion.R + 'id'))
                    else:
                        pres.remove(order)
                    parts['ppt/presentation.xml'] = ET.tostring(pres)
                out = io.BytesIO()
                with zipfile.ZipFile(out, 'w') as archive:
                    for name, data in parts.items():
                        archive.writestr(name, data)
                malformed = out.getvalue()
                # The legacy completion reader remains tolerant; this new diagnostic must not be.
                parsed = completion.read_deck(malformed)
                with self.assertRaisesRegex(completion.Unreadable, 'presentation order'):
                    wp.outline_deck(parsed, malformed)
                self.src.files[FOR_DECK]['mimeType'] = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                ctx = wp._Run('read-token', self.port, 10**20, lambda *a: None)
                node = wp._node(ctx, 'teacher_deck', FOR_DECK, deck_url(FOR_DECK))
                with patch.object(google, 'deck_bytes', return_value=malformed):
                    result = ctx.check('outline', node, wp._outline)
                self.assertFalse(result['ok'])
                self.assertIn('presentation order', result['problem'])
                self.assertNotIn('link_mismatches', result)
                cache = wp.check_id('outline', node['id'], node['revision'])
                self.assertIsNone(self.port.read([cache]).documents[cache])


    def test_old_rules_and_changed_outline_source_do_not_reuse_a_clean_cached_check(self):
        with patch.object(wp, 'RULES', 'weekly-preflight-rules/4'):
            self.run_check()
        with patch.object(google, 'native_presentation', side_effect=self.presentation_with_link):
            found = [e for e in self.run_check()['exceptions'] if e['kind'] == 'deck_link_target_mismatch']
        self.assertEqual(len(found), 1)
        node = wp._node(wp._Run('read-token', self.port, 10**20, lambda *a: None),
                        'teacher_deck', FOR_DECK, deck_url(FOR_DECK))
        self.src.bump(FOR_DECK)
        ctx = wp._Run('read-token', self.port, 10**20, lambda *a: None)
        result = ctx.check('outline', node, wp._outline)
        self.assertFalse(result['ok'])
        self.assertIn('changed', result['problem'])


class NativeReadinessTests(Base):
    def context(self, role='teacher_deck', ident=AP_DECK):
        ctx = wp._Run('read-token', self.port, float('inf'), lambda _: None)
        return ctx, wp._node(ctx, role, ident, deck_url(ident))

    def denied_export(self, token, ident, mime):
        self.src.calls['export:' + ident] += 1
        raise google.GoogleError('Export denied', 403)

    def assert_unavailable(self, ctx, node, result):
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertIn('readability', result)
        cache_id = wp.check_id(*wp._check_identity('readiness', node))
        self.assertIsNone(self.port.read([cache_id]).documents[cache_id])

    def test_exact_agenda_teacher_and_second_deck_read_native_after_export_403(self):
        denied = {AP_DECK, FOR_SECOND}

        def export(token, ident, mime):
            return (self.denied_export if ident in denied else self.src.deck_bytes)(token, ident, mime)

        with patch.object(google, 'deck_bytes', side_effect=export):
            result = self.run_check()
            for cls, row, ident, role in (('anatomy', 5, AP_DECK, 'teacher_deck'),
                                           ('forensics', 6, FOR_SECOND, 'second_deck')):
                with self.subTest(role=role):
                    node = next(n for n in self.lesson(result, cls, row)['sources'] if n['id'] == ident)
                    self.assertIn(role, node['roles'])
                    self.assertEqual(node['readiness']['status'], 'READY')
                    cache_id = wp.check_id(*wp._check_identity('readiness', node))
                    saved = self.port.read([cache_id]).documents[cache_id].value['result']
                    self.assertEqual(saved['counts'], {'NOTES': 1, 'DRAFT': 1})
                    self.assertEqual(node['readiness']['missing_ids'], [])
                    self.assertEqual(node['readiness']['extra_ids'], [])
                    self.assertEqual(self.src.calls['native:' + ident], 2, 'readiness and outline')
                    self.assertNotIn('_source_metadata', node, 'private bookends are not report fields')
            before = self.src.calls.copy()
            self.run_check()
            self.assertFalse(any(k.startswith(('native:', 'export:')) for k in self.src.calls - before))
        self.assertEqual(self.src.calls['native:' + STRAY], 0, 'folder neighbours are not teacher authority')
        self.assertEqual(result['external_writes'], 0)
        self.assertEqual(self.writes, [])

    def test_native_read_still_reports_actual_missing_and_extra_codes(self):
        with patch.object(google, 'deck_bytes', side_effect=self.denied_export):
            result = self.run_check()
        node = next(n for n in self.lesson(result, 'biology', 5)['sources'] if n['id'] == BIO_DECK)
        self.assertEqual(node['readiness']['status'], 'NOT_READY')
        self.assertEqual(node['readiness']['missing_ids'], ['NOTES Q2'])
        self.assertEqual(node['readiness']['extra_ids'], ['NOTES Q3'])
        self.assertFalse(node['readiness']['readability'])
        self.assertIn('needs_embedded_codes', {e['kind'] for e in self.exceptions(result, 'biology', 5)})

    def test_teacher_cache_cannot_authorize_same_revision_student_only_or_unrelated_link(self):
        with patch.object(google, 'deck_bytes', side_effect=self.denied_export):
            ctx, teacher = self.context()
            self.assertEqual(ctx.check('readiness', teacher, wp._readiness)['status'], 'READY')
            before = self.src.calls['native:' + AP_DECK]
            for role in ('student_copy', 'extra_material'):
                with self.subTest(role=role):
                    ctx, node = self.context(role)
                    result = ctx.check('readiness', node, wp._readiness)
                    self.assert_unavailable(ctx, node, result)
                    self.assertEqual(result['readability']['http_status'], 403)
                    self.assertEqual(self.src.calls['native:' + AP_DECK], before)
                    if role == 'extra_material':
                        self.assertEqual(wp._content_kinds(node), [])

    def test_non_403_export_failures_do_not_fall_back_to_native(self):
        ctx, node = self.context()
        with patch.object(google, 'deck_bytes', side_effect=google.GoogleError('Unavailable', 429)):
            result = ctx.check('readiness', node, wp._readiness)
        self.assert_unavailable(ctx, node, result)
        self.assertEqual(result['readability']['http_status'], 429)
        self.assertEqual(self.src.calls['native:' + AP_DECK], 0)

    def test_incomplete_discovery_metadata_never_reads_or_caches_ready(self):
        original = deepcopy(self.src.files[AP_DECK])
        for field in ('id', 'name', 'mimeType', 'trashed', 'version', 'modifiedTime', 'parents'):
            with self.subTest(field=field):
                self.src.files[AP_DECK] = deepcopy(original)
                self.src.files[AP_DECK].pop(field)
                ctx, node = self.context()
                result = ctx.check('readiness', node, wp._readiness)
                self.assert_unavailable(ctx, node, result)
                self.assertIn('incomplete current source metadata', result['problem'])
        self.assertEqual(self.src.calls['export:' + AP_DECK], 0)
        self.assertEqual(self.src.calls['native:' + AP_DECK], 0)

    def test_changed_discovery_metadata_never_reads_or_caches_ready(self):
        ctx, node = self.context()
        self.src.bump(AP_DECK)
        result = ctx.check('readiness', node, wp._readiness)
        self.assert_unavailable(ctx, node, result)
        self.assertIn('changed since source discovery', result['problem'])
        self.assertEqual(self.src.calls['export:' + AP_DECK], 0)

    def test_revision_name_and_parent_changes_during_native_read_are_not_cached(self):
        original = deepcopy(self.src.files[AP_DECK])
        for field, value in (('version', '2'), ('name', 'Changed teacher title'), ('parents', ['DifferentFolder'])):
            with self.subTest(field=field):
                self.src.files[AP_DECK] = deepcopy(original)
                completion._native_teacher_templates.clear()
                ctx, node = self.context()

                def native_then_change(token, ident):
                    response = self.src.native_presentation(token, ident)
                    self.src.files[ident][field] = value
                    return response

                with patch.object(google, 'deck_bytes', side_effect=self.denied_export), \
                        patch.object(google, 'native_presentation', side_effect=native_then_change):
                    result = ctx.check('readiness', node, wp._readiness)
                self.assert_unavailable(ctx, node, result)
                self.assertIn('changed', result['problem'])

    def test_the_consumed_teacher_metadata_must_match_the_discovered_source(self):
        ctx, node = self.context()
        meta, deck = completion._teacher_template('read-token', AP_DECK)
        meta['parents'] = ['UnrelatedFolder']
        with patch.object(completion, '_teacher_template', return_value=(meta, deck)):
            result = ctx.check('readiness', node, wp._readiness)
        self.assert_unavailable(ctx, node, result)
        self.assertIn('consumed by the code check does not match', result['problem'])

    def test_a_cached_ready_requires_a_fresh_source_bookend(self):
        ctx, node = self.context()
        self.assertEqual(ctx.check('readiness', node, wp._readiness)['status'], 'READY')
        cached, same_node = self.context()
        self.src.bump(AP_DECK)
        result = cached.check('readiness', same_node, wp._readiness)
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertIn('changed since source discovery', result['problem'])
        self.assertEqual(self.src.calls['export:' + AP_DECK], 1, 'a stale cache is not recomputed against a new source')
        current, current_node = self.context()
        self.assertEqual(current.check('readiness', current_node, wp._readiness)['revision'], '2')

    def test_native_identity_mismatch_or_incomplete_geometry_is_not_ready(self):
        for defect in ('wrong_id', 'no_geometry'):
            with self.subTest(defect=defect):
                presentation = self.src.native_presentation('read-token', AP_DECK)
                if defect == 'wrong_id':
                    presentation['presentationId'] = STRAY
                else:
                    presentation.pop('pageSize')
                ctx, node = self.context()
                with patch.object(google, 'deck_bytes', side_effect=self.denied_export), \
                        patch.object(google, 'native_presentation', return_value=presentation):
                    self.assert_unavailable(ctx, node, ctx.check('readiness', node, wp._readiness))


class InventoryContextTests(Base):
    def codes(self, result):
        return next(e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'needs_embedded_codes')

    def test_missing_inventory_has_required_first_slide_and_saved_context(self):
        self.src.decks[BIO_DECK] = ready_deck('C06')[1:]
        result = self.run_check()
        finding = self.codes(result)
        self.assertEqual(finding['slides'], [1])
        self.assertEqual(finding['evidence']['inventory_slides'], [])
        self.assertEqual(finding['evidence']['required_inventory_slide'], 1)
        self.assertIn('No inventory marker was found', finding['reason'])
        self.assertEqual(finding['source_url'], deck_url(BIO_DECK) + '#slide=id.' + BIO_DECK[-4:] + '_p1')
        self.assertTrue(finding['blocking'])
        saved = wp.status(self.config, SHEET, '2026-10-19', 1, port=self.port)
        self.assertEqual(self.codes(saved), finding)
        self.assertEqual(result['external_writes'], 0)
        self.assertEqual(result['model_calls'], 0)

    def test_misplaced_inventory_names_both_required_and_actual_slides(self):
        self.src.decks[BIO_DECK] = list(reversed(ready_deck('C06')))
        finding = self.codes(self.run_check())
        self.assertEqual(finding['slides'], [1, 2])
        self.assertEqual(finding['evidence']['inventory_slides'], [2])
        self.assertIn('Existing inventory markers: slides 2.', finding['reason'])

    def test_duplicate_inventory_preserves_all_locations(self):
        deck = ready_deck('C06')
        self.src.decks[BIO_DECK] = deck + [deepcopy(deck[0])]
        finding = self.codes(self.run_check())
        self.assertEqual(finding['slides'], [1, 3])
        self.assertEqual(finding['evidence']['inventory_slides'], [1, 3])
        self.assertIn('found 2', finding['reason'])

    def test_unparsed_inventory_and_response_mismatch_keep_existing_locations(self):
        self.src.decks[BIO_DECK] = [[('inventory', '[[MARKER-INVENTORY:C06]]')], ready_deck('C06')[1]]
        first = self.codes(self.run_check())
        self.assertEqual(first['slides'], [1])
        self.assertIn('marker_inventory_counts_unparsed', first['evidence']['issues'])
        self.src.decks[BIO_DECK] = deepcopy(BIO_SLIDES)
        self.src.bump(BIO_DECK)
        second = self.codes(self.run_check())
        self.assertEqual(second['slides'], [1, 3])
        self.assertIn('NOTES Q3 (slide 3)', second['reason'])

    def test_unreadable_outline_does_not_invent_inventory_locations_or_absence(self):
        self.src.decks[BIO_DECK] = list(reversed(ready_deck('C06')))
        with patch.object(wp, '_outline', return_value={'ok': False, 'stage': 'parse', 'problem': 'unreadable'}):
            finding = self.codes(self.run_check())
        self.assertEqual(finding['slides'], [])
        self.assertNotIn('inventory_slides', finding['evidence'])
        self.assertNotIn('No inventory marker was found', finding['reason'])
        self.assertEqual(finding['source_url'], deck_url(BIO_DECK))

    def test_pptx_locations_never_manufacture_a_native_slide_link(self):
        self.src.decks[BIO_DECK] = list(reversed(ready_deck('C06')))
        self.src.files[BIO_DECK]['mimeType'] = google.PPTX
        finding = self.codes(self.run_check())
        self.assertEqual(finding['slides'], [1, 2])
        self.assertEqual(finding['evidence']['inventory_slides'], [2])
        self.assertEqual(finding['source_url'], 'https://drive.google.com/open?id=' + BIO_DECK)

    def test_repaired_new_revision_removes_old_inventory_failure(self):
        self.src.decks[BIO_DECK] = ready_deck('C06')[1:]
        self.codes(self.run_check())
        self.src.decks[BIO_DECK] = ready_deck('C06')
        self.src.bump(BIO_DECK)
        result = self.run_check()
        self.assertNotIn('needs_embedded_codes', {e['kind'] for e in self.exceptions(result, 'biology', 5)})


class PreflightRunTests(Base):
    def test_changed_agenda_titles_read_back_without_merging_same_week_lessons(self):
        self.src.files[FOR_FOLDER]['name'] = 'Week 08 - Earlier retained lesson'
        before = self.run_check()
        titles = {5: 'Evidence Documentation', 6: 'Evidence Documentation Lab'}
        tab_name = self.agenda().classes['forensics']['tab']
        for row, title in titles.items():
            self.grids[tab_name][row, 3] = cell(title)
        result = self.run_check()
        saved = wp.status(self.config, SHEET, '2026-10-19', 1, port=self.port)
        for row, title in titles.items():
            with self.subTest(row=row):
                old = self.lesson(before, 'forensics', row)
                current = self.lesson(result, 'forensics', row)
                self.assertEqual((current['topic'], current['agenda_topic']), (title, title))
                self.assertEqual(self.lesson(saved, 'forensics', row)['topic'], title)
                self.assertEqual(current['lesson_id'], old['lesson_id'])
                self.assertEqual(current['sources'], old['sources'])
                self.assertEqual(current['exceptions'], old['exceptions'])
                self.assertTrue(all(e['lesson'] == title for e in self.exceptions(result, 'forensics', row)))
        self.assertNotEqual(self.lesson(result, 'forensics', 5)['lesson_id'],
                            self.lesson(result, 'forensics', 6)['lesson_id'])
        self.assertEqual((result['external_writes'], result['model_calls'], self.writes), (0, 0, []))

    def test_calendar_conflicts_needs_content_and_prep_deadlines(self):
        result = self.run_check()
        bio = {(e['kind'], e['date']) for e in self.exceptions(result, 'biology', 5)}
        self.assertIn(('calendar_conflict', '2026-10-23'), bio, 'moved for an in-service the calendar does not have')
        self.assertIn(('needs_content', '2026-10-21'), bio, 'VERIFY DAY')
        self.assertIn(('needs_content', '2026-10-23'), bio, 'empty plan on a real A Friday')
        moved = next(e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'calendar_conflict')
        self.assertIn('normal school day', moved['reason'])
        self.assertTrue(moved['source_url'].endswith('&range=D5'))
        forensics = [e for e in self.exceptions(result, 'forensics', 5) if e['kind'] == 'calendar_conflict']
        self.assertEqual([e['date'] for e in forensics], ['2026-10-23'])
        self.assertIn("doesn't meet", forensics[0]['reason'])
        anatomy = [e for e in self.exceptions(result, 'anatomy', 5) if e['kind'] == 'calendar_conflict']
        self.assertEqual([e['date'] for e in anatomy], ['2026-10-23'], 'A&P plan content on an A Friday only')
        self.assertFalse(any(e['date'] == '2026-10-22' for e in self.exceptions(result)), 'PREP by is not a meeting claim')
        carrots = next(s for s in result['supplies'] if s['item'] == 'Carrots')
        self.assertEqual((carrots['prep_by'], carrots['use_dates'], carrots['status']), ('2026-10-22', [], 'buy'))
        for item in result['exceptions']:
            self.assertIn(item['status'], wp.EXCEPTION_STATUSES)
            self.assertTrue(item['reason'] and item['next_action'] and item['source_url'].startswith('https://'))
            self.assertRegex(item['fingerprint'], r'^[a-f0-9]{64}$')
        self.assertEqual(self.lesson(result, 'biology', 5)['status'], 'needs_decision')
        self.assertEqual((result['model_calls'], result['external_writes']), (0, 0))

    def test_a_friday_the_weeks_tab_does_not_name_is_a_decision_not_a_meeting(self):
        result = self.run_check('2026-11-02')
        unknown = [e for e in self.exceptions(result, 'biology', 7) if e['kind'] == 'calendar_friday_unknown']
        self.assertEqual([(e['date'], e['blocking']) for e in unknown], [('2026-11-06', True)])
        self.assertIn('Fri 11/6 = A Friday', unknown[0]['next_action'])
        self.assertNotEqual(self.lesson(result, 'biology', 7)['status'], 'ready')
        self.assertFalse([e for e in self.exceptions(result, 'biology', 7) if e['kind'] == 'calendar_conflict'])

    def test_supplies_merge_names_keep_sources_and_never_claim_in_hand(self):
        result = self.run_check()
        items = {s['item']: s for s in result['supplies']}
        self.assertEqual(items['Active dry yeast']['also_named'], ['yeast'])
        self.assertEqual(items['Active dry yeast']['status'], 'on order (not verified in hand)')
        self.assertEqual(items['warm-water bath']['also_named'], ['water bath'])
        self.assertEqual(items['sugar']['status'], 'buy')
        self.assertEqual(items['straws']['status'], 'on order (not verified in hand)')
        self.assertEqual(items['Plastic bottles']['status'], 'pull')
        self.assertEqual(items['Goggles']['status'], 'stock unknown')
        self.assertEqual(items['Baking soda']['status'], 'buy')
        self.assertEqual(items['Active dry yeast']['use_dates'], ['2026-10-19', '2026-10-21'])
        self.assertEqual(len(items['Active dry yeast']['sources']), 2)
        self.assertTrue(all(s['in_hand_verified'] is False for s in result['supplies']))
        self.assertTrue(all(s['quantity'] == 'quantity not stated' for s in result['supplies']))

    def test_two_lessons_share_one_folder_and_one_form_without_a_flag(self):
        result = self.run_check()
        first, second = self.lesson(result, 'forensics', 5), self.lesson(result, 'forensics', 6)
        for lesson in (first, second):
            ids = {n['id']: n for n in lesson['sources']}
            self.assertIn(FOR_FOLDER, ids)
            self.assertEqual(ids[FOR_FORM]['editor_id'], FOR_FORM)
            self.assertTrue(ids[FOR_FORM]['published_id'] and ids[FOR_FORM]['published_id'] != FOR_FORM)
        self.assertEqual(self.src.calls['listing:' + FOR_FOLDER], 1)
        kinds = {e['kind'] for e in self.exceptions(result, 'forensics')}
        self.assertEqual(kinds, {'calendar_conflict'})
        self.assertEqual(second['status'], 'ready')
        self.assertEqual({n['id']: n['roles'] for n in second['sources']}[FOR_SECOND], ['second_deck'])

    def test_forensics_folder_in_the_column_after_the_hidden_second_deck_slot_is_correct(self):
        found = wp.link_findings(self.agenda(), 'forensics', [4, 5, 6])
        self.assertEqual(found, [])

    def test_a_form_shared_across_consecutive_weeks_or_naming_the_next_week_is_not_flagged(self):
        result = self.run_check(weeks=2)
        bio = [l for l in result['lessons'] if l['class_key'] == 'biology']
        self.assertEqual([l['row'] for l in bio], [5, 6])
        self.assertTrue(all(BIO_FORM in {n['id'] for n in l['sources']} for l in bio))
        self.assertFalse([e for e in result['exceptions'] if BIO_FORM in json.dumps(e) or 'form' in e['kind']])
        self.assertEqual(self.lesson(result, 'biology', 6)['status'], 'ready')

    def test_an_excluded_week_skips_form_checks_but_still_checks_teaching_sources(self):
        self.config['HUB_WEEK_EXCLUSIONS'] = json.dumps([{'school_year': '2026-27', 'class_key': 'biology',
                                                          'weeks': ['Wk 09'], 'reason': 'Fictional skipped week'}])
        result = self.run_check()
        lesson = self.lesson(result, 'biology', 5)
        self.assertNotIn(BIO_FORM, {n['id'] for n in lesson['sources']})
        self.assertIn(BIO_DECK, {n['id'] for n in lesson['sources']})
        self.assertEqual(result['coverage']['excluded_weeks'],
                         [{'class': 'Biology', 'week': 'Wk 09', 'reason': 'Fictional skipped week'}])
        self.assertIn(FOR_FORM, {n['id'] for n in self.lesson(result, 'forensics', 5)['sources']})

    def test_untaught_lessons_read_no_submissions_and_never_flag_missing_work(self):
        result = self.run_check(weeks=2)  # google.form_responses would raise
        self.assertFalse([e for e in result['exceptions'] if re.search('missing|submission|zero', e['kind'])])

    def test_student_copy_link_is_the_teacher_file_in_the_template_role(self):
        result = self.run_check()
        sources = [n for n in self.lesson(result, 'biology', 5)['sources'] if n['id'] == BIO_DECK]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['roles'], ['teacher_deck', 'student_copy_template'])
        self.assertEqual(sources[0]['access_evidence'], wp.ACCESS)
        self.assertNotIn('student_link_not_copy', {e['kind'] for e in result['exceptions']})

    def test_a_trashed_linked_deck_is_inaccessible_and_never_ready(self):
        self.src.files[BIO_NEXT]['trashed'] = True
        result = self.run_check('2026-10-26')
        lesson = self.lesson(result, 'biology', 6)
        self.assertEqual(lesson['status'], 'inaccessible')
        trashed = [e for e in self.exceptions(result, 'biology', 6) if e['kind'] == 'source_trashed']
        self.assertEqual([e['status'] for e in trashed], ['inaccessible'])
        self.assertEqual(self.src.calls['export:' + BIO_NEXT], 0)
        self.assertNotEqual(result['status'], 'ready')

    def test_an_incomplete_folder_listing_is_incomplete_and_never_ready(self):
        self.src.complete[BIO_FOLDER2] = False
        result = self.run_check('2026-10-26')
        self.assertEqual(self.lesson(result, 'biology', 6)['status'], 'incomplete')
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('folder_listing_incomplete', {e['kind'] for e in result['exceptions']})
        complete = wp.week_id(SHEET, '2026-10-26', 'complete')
        self.assertIsNone(self.port.read([complete]).documents[complete])
        self.assertEqual(wp.status(self.config, SHEET, '2026-10-26', 1, port=self.port)['status'], 'incomplete')

    def test_needs_embedded_codes_names_the_ids_and_slides(self):
        result = self.run_check()
        codes = next(e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'needs_embedded_codes')
        self.assertTrue(codes['reason'].startswith('Needs embedded codes: '))
        self.assertIn('declared but not embedded: NOTES Q2', codes['reason'])
        self.assertIn('NOTES Q3 (slide 3)', codes['reason'])
        self.assertEqual(codes['slides'], [1, 3])
        deck = next(n for n in self.lesson(result, 'biology', 5)['sources'] if n['id'] == BIO_DECK)
        self.assertEqual(deck['outline']['slide_count'], 4)
        self.assertEqual(deck['outline']['teacher_only_slides'], [4])

    def test_print_list_quantities_files_sides_and_teacher_only(self):
        result = self.run_check()
        items = {p['text']: p for p in result['print_items']}
        worksheet = items['CER worksheet (1 per student)']
        self.assertEqual(worksheet['per_section'], {'A4 Biology': 22})
        self.assertEqual((worksheet['file']['id'], worksheet['file']['pages'], worksheet['file']['sides']),
                         (HANDOUT, 1, 'single-sided'))
        self.assertFalse(worksheet['teacher_only'])
        key_item = items['CER worksheet answer key (teacher)']
        self.assertTrue(key_item['teacher_only'])
        self.assertEqual((key_item['file']['id'], key_item['file']['sides']), (KEYFILE, 'double-sided'))
        card = items['Lab safety card (1 per group)']
        self.assertEqual(card['per_section'], {'A4 Biology': 6})
        self.assertIn('ceil(students/4)', card['basis'])
        self.assertIsNone(card['file'])
        self.assertTrue(items['Support copies of vocabulary list']['optional'])
        missing = [e for e in result['exceptions'] if e['kind'] == 'print_item_no_file']
        self.assertEqual([(e['lesson'], e['slides'], e['blocking']) for e in missing],
                         [('Cellular respiration', [4], True)])
        lesson = self.lesson(result, 'biology', 5)
        self.assertEqual(lesson['teacher_prep'], [{'text': 'Warm the water bath to 40°C', 'slide': 4,
                                                   'deck': 'Cycle 06 — Cellular respiration'}])
        stray = next(e for e in result['exceptions'] if e['kind'] == 'unlinked_deck_in_folder')
        self.assertIn('Cycle 07 — Mitosis (spare)', stray['reason'])
        self.assertFalse(stray['blocking'])

    def test_a_print_item_with_no_file_prints_from_the_web_page_the_deck_or_line_names(self):
        slides = deepcopy(BIO_SLIDES)
        slides[3] = [('reference', slides[3][0][1].replace(
            '• Lab safety card (1 per group)\n', '• Lab safety card (1 per group)\n'
            '• Science World — Yeast-Inflated Balloons (1 per group)\n'
            '• Gel diagram (1 per student)\n  https://diagrams.example.org/gel-diagram.pdf\n'))]
        slides.append([('links', 'TEACHER NAVIGATION\nActivity and resource links\n'
                                 'Science World — Yeast-Inflated Balloons\n' + WEB_PAGE)])
        self.src.decks[BIO_DECK] = slides
        result = self.run_check()
        items = {p['text']: p for p in result['print_items']}
        balloons = items['Science World — Yeast-Inflated Balloons (1 per group)']
        self.assertEqual(balloons['web'], {'url': WEB_PAGE, 'label': 'Science World — Yeast-Inflated Balloons',
                                           'slide': 5, 'from': "the deck's link list"})
        self.assertEqual((balloons['instruction'], balloons['file'], balloons['per_section']),
                         ('print from web: ' + WEB_PAGE, None, {'A4 Biology': 6}))
        gel = items['Gel diagram (1 per student)']
        self.assertEqual(gel['instruction'], 'print from web: https://diagrams.example.org/gel-diagram.pdf')
        self.assertFalse([p for p in result['print_items'] if p['text'].startswith('https://')])
        missing = [e for e in result['exceptions'] if e['kind'] == 'print_item_no_file']
        self.assertEqual(len(missing), 1, 'no URL anywhere: still a decision')
        self.assertIn('Lab safety card', missing[0]['reason'])

    def test_an_authoring_note_on_a_student_slide_is_one_decision_per_slide(self):
        note = '[DRAFT — confirm which unit exam is used for the test; the old one had a different key]'
        self.src.decks[BIO_DECK] = BIO_SLIDES + [
            [('question', 'Which reactant limits the yeast? ' + note), ('aside', '[added — balloon photo]')],
            [('question', '[Note: give your answer in mL]'), ('notes', '[[NOTES:C06:Q4]]')]]
        result = self.run_check()
        found = [e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'authoring_note_on_student_slide']
        self.assertEqual([(e['status'], e['blocking'], e['slides']) for e in found], [('needs_decision', True, [5])])
        self.assertIn(note[:79].rstrip() + '…', found[0]['reason'])
        self.assertIn('and 1 more on that slide', found[0]['reason'])
        self.assertEqual(found[0]['next_action'], 'Remove or move this note to speaker notes.')
        self.assertEqual(found[0]['source_url'], deck_url(BIO_DECK) + '#slide=id.0009_p5')
        again = self.run_check()
        self.assertEqual([e['fingerprint'] for e in self.exceptions(again, 'biology', 5)
                          if e['kind'] == 'authoring_note_on_student_slide'], [found[0]['fingerprint']])

    def test_unchanged_sources_reuse_checks_and_a_new_revision_rechecks_only_that_source(self):
        self.run_check('2026-10-26')
        first = Counter({k: v for k, v in self.src.calls.items() if k.startswith(('export:', 'native:'))})
        self.assertEqual(first['export:' + BIO_NEXT], 1)
        self.assertEqual(first['native:' + AP_NEXT], 1)
        again = self.run_check('2026-10-26')
        self.assertEqual(Counter({k: v for k, v in self.src.calls.items() if k.startswith(('export:', 'native:'))}), first)
        self.assertEqual(again['coverage']['run']['checked'], 0)
        self.src.bump(BIO_NEXT)
        self.run_check('2026-10-26')
        now = Counter({k: v for k, v in self.src.calls.items() if k.startswith(('export:', 'native:'))})
        self.assertEqual(now - first, Counter({'export:' + BIO_NEXT: 1, 'native:' + BIO_NEXT: 1}))

    def test_an_interrupted_run_is_incomplete_and_the_next_run_resumes_without_rework(self):
        ticks = iter(range(0, 100000, 10))
        with patch.object(wp.time, 'monotonic', side_effect=lambda: next(ticks)):
            partial = self.run_check(budget_seconds=35)
        self.assertEqual(partial['status'], 'incomplete')
        self.assertGreater(partial['coverage']['run']['pending'], 0)
        self.assertGreater(partial['coverage']['run']['checked'], 0)
        self.assertTrue(any(l['status'] == 'incomplete' for l in partial['lessons']))
        done = self.run_check()
        self.assertNotEqual(done['status'], 'incomplete')
        self.assertEqual(done['coverage']['run']['cached'], partial['coverage']['run']['checked'])
        exports = {k: v for k, v in self.src.calls.items() if k.startswith('export:')}
        self.assertTrue(exports and all(v == 1 for v in exports.values()), exports)

    def test_reruns_create_no_duplicate_records(self):
        self.run_check(weeks=2)
        before = set(self.port.rows)
        self.run_check(weeks=2)
        self.assertEqual(set(self.port.rows), before)
        stored = wp.status(self.config, SHEET, '2026-10-19', 2, port=self.port)
        self.assertEqual(len(stored['lessons']), len({l['lesson_id'] for l in stored['lessons']}))
        self.assertEqual(len(stored['exceptions']), len({e['fingerprint'] for e in stored['exceptions']}))
        self.assertTrue(stored['last_successful_check'])

    def test_reviews_bind_to_revisions_and_show_needs_re_review_after_a_change(self):
        result = self.run_check('2026-10-26')
        lesson = self.lesson(result, 'biology', 6)
        deck = next(n for n in lesson['sources'] if n['id'] == BIO_NEXT)
        payload = {'kind': 'meeting_flow', 'class': 'biology', 'week': 'Wk 10', 'row': 6, 'date': '2026-10-26',
                   'sources': [{'id': BIO_NEXT, 'revision': deck['revision']}],
                   'text': '0-10 Do now\n10-40 Light reactions model\n40-80 Station work\n80-90 Exit ticket'}
        saved = wp.save_review(self.config, SHEET, payload, 'teacher@school.example', port=self.port)
        self.assertTrue(saved['changed'])
        self.assertFalse(wp.save_review(self.config, SHEET, payload, 'teacher@school.example', port=self.port)['changed'])
        reviews = self.lesson(wp.status(self.config, SHEET, '2026-10-26', 1, port=self.port), 'biology', 6)['reviews']
        self.assertEqual(reviews['meeting_flow:2026-10-26']['state'], 'verified')
        self.assertEqual(reviews['meeting_flow:2026-10-28']['state'], 'not_reviewed')
        self.src.bump(BIO_NEXT)
        rechecked = self.run_check('2026-10-26')
        self.assertEqual(self.lesson(rechecked, 'biology', 6)['reviews']['meeting_flow:2026-10-26']['state'],
                         'needs_re_review')
        with self.assertRaisesRegex(wp.Held, 'changed since the check'):
            wp.save_review(self.config, SHEET, payload, 'teacher@school.example', port=self.port)


class LinkRepairTests(Base):
    def plan(self, row=5, class_key='biology'):
        return wp.link_findings(self.agenda(), class_key, [row])

    def test_an_unclickable_label_gets_its_neighbours_formula_and_a_second_run_is_a_no_op(self):
        [finding] = self.plan()
        self.assertEqual(finding['kind'], 'visible_link_not_clickable')
        self.assertEqual(finding['plan']['formula'], '=IF($P5="","—",HYPERLINK($P5,"🎞 Student copy ↗"))')
        preview = wp.repair_link('read', None, self.config, self.agenda(), finding['fingerprint'], port=self.port)
        self.assertEqual((preview['status'], preview['dry_run'], self.writes), ('planned', True, []))
        done = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'], dry_run=False,
                              port=self.port)
        self.assertEqual(done['status'], 'repaired_and_verified')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0][0]['updateCells']['fields'], 'userEnteredValue')
        self.assertEqual(self.plan(), [])
        again = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'], dry_run=False,
                               port=self.port)
        self.assertFalse(again['changed'])
        self.assertEqual(len(self.writes), 1)
        result = self.run_check()
        repaired = [e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'visible_link_not_clickable']
        self.assertEqual([(e['status'], e['blocking']) for e in repaired], [('repaired_and_verified', False)])

    def test_formula_link_and_typed_cells_are_never_overwritten(self):
        found = {f['column']: f for f in self.plan(7)}
        self.assertEqual(found['M']['kind'], 'visible_link_disagrees')
        self.assertNotIn('plan', found['M'])
        self.assertEqual(found['N']['kind'], 'visible_link_not_clickable')
        self.assertNotIn('plan', found['N'])
        self.assertIn('typed text', found['N']['reason'])
        for finding in found.values():
            with self.assertRaisesRegex(wp.Held, 'decision'):
                wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'], dry_run=False,
                               port=self.port)
        self.assertEqual(self.writes, [])

    def test_a_cell_changed_after_the_check_is_left_alone(self):
        [finding] = self.plan()
        stale = self.agenda()
        self.grids['🌿 Biology'][5, 12] = cell('🎞 Student copy (edited)')
        with self.assertRaisesRegex(wp.Held, 'changed since the check'):
            wp.repair_link('read', 'write', self.config, stale, finding['fingerprint'], dry_run=False, port=self.port)
        self.assertEqual(self.writes, [])

    def no_formula_column(self, label=LABELS['second']):
        """The Forensics 2nd-deck display column N with no formula in any row,
        and N6 plain text while its hidden R6 holds the deck link."""
        grid = self.grids['🔬 Forensics']
        for row in (4, 5, 7):
            grid.pop((row, 14), None)
        grid[6, 14] = cell(label)
        return grid

    def test_a_label_in_a_column_with_no_formula_is_linked_in_place_and_a_second_run_is_a_no_op(self):
        grid = self.no_formula_column()
        [finding] = self.plan(6, 'forensics')
        self.assertEqual(finding['plan'], {'kind': 'linked_label', 'column': 'N', 'row': 6, 'label': LABELS['second'],
                                           'url': deck_url(FOR_SECOND), 'pattern_from': 'the label cell N6 already shows'})
        before = self.run_check()
        item = next(e for e in self.exceptions(before, 'forensics', 6) if e['kind'] == 'visible_link_not_clickable')
        self.assertEqual((item['status'], item['fingerprint']), ('repairable', finding['fingerprint']))
        self.assertIn('link the label “🎞 2nd deck ↗” in N6 to its hidden R6 URL', item['next_action'])
        preview = wp.repair_link('read', None, self.config, self.agenda(), finding['fingerprint'], port=self.port)
        self.assertEqual((preview['status'], preview['cell'], preview['formula'], self.writes), ('planned', 'N6', None, []))
        self.assertEqual(preview['writes'], 'the label “🎞 2nd deck ↗” linked to ' + deck_url(FOR_SECOND))
        done = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'], dry_run=False,
                              port=self.port)
        self.assertEqual(done['status'], 'repaired_and_verified')
        [[request]] = self.writes
        self.assertEqual(request['updateCells'], {
            'range': {'sheetId': 3, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 13, 'endColumnIndex': 14},
            'rows': [{'values': [{'userEnteredValue': {'stringValue': LABELS['second']},
                                  'textFormatRuns': [{'startIndex': 0, 'format': {'link': {'uri': deck_url(FOR_SECOND)}}}]}]}],
            'fields': 'userEnteredValue,textFormatRuns'})
        self.assertEqual(agenda.text(grid[6, 14]), LABELS['second'], 'the same visible text')
        self.assertEqual(self.plan(6, 'forensics'), [])
        again = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'], dry_run=False,
                               port=self.port)
        self.assertEqual((again['changed'], len(self.writes)), (False, 1))
        after = self.run_check()
        repaired = [e for e in self.exceptions(after, 'forensics', 6) if e['kind'] == 'visible_link_not_clickable']
        self.assertEqual([(e['status'], e['blocking'], e['evidence']) for e in repaired],
                         [('repaired_and_verified', False, {'label': LABELS['second'], 'url': deck_url(FOR_SECOND)})])
        self.assertEqual(self.lesson(after, 'forensics', 6)['status'], 'repaired_and_verified')

    def test_a_linked_label_repair_never_overwrites_a_cell_that_became_a_link_or_formula(self):
        self.no_formula_column()
        [finding] = self.plan(6, 'forensics')
        stale = self.agenda()
        for fixed in (shown('R', 6, LABELS['second'], deck_url(FOR_SECOND)),
                      cell(LABELS['second'], textFormatRuns=[{'format': {'link': {'uri': deck_url(FOR_SECOND)}}}])):
            self.grids['🔬 Forensics'][6, 14] = fixed
            with self.assertRaisesRegex(wp.Held, 'changed since the check'):
                wp.repair_link('read', 'write', self.config, stale, finding['fingerprint'], dry_run=False, port=self.port)
        self.assertEqual(self.writes, [])

    def test_typed_content_in_a_column_with_no_formula_still_needs_a_decision(self):
        self.no_formula_column('see the lab binder')
        [typed] = self.plan(6, 'forensics')
        self.assertNotIn('plan', typed)
        self.assertIn('typed text', typed['reason'])
        # The label the column's other rows show as links is a label, not typed text.
        self.no_formula_column('2nd deck')
        self.grids['🔬 Forensics'][5, 14] = cell('2nd deck', textFormatRuns=[{'format': {'link': {'uri': deck_url(FOR_DECK2)}}}])
        [label] = self.plan(6, 'forensics')
        self.assertEqual((label['plan']['kind'], label['plan']['label']), ('linked_label', '2nd deck'))

    def test_second_deck_placeholder_previews_and_repairs_only_the_exact_visible_cell(self):
        grid = self.no_formula_column('—')
        before = deepcopy(self.grids)
        [finding] = self.plan(6, 'forensics')
        self.assertEqual(finding['plan']['source_check'], 'exact_second_deck')
        preview = wp.repair_link('read', None, self.config, self.agenda(), finding['fingerprint'], port=self.port)
        self.assertEqual((preview['status'], preview['dry_run'], self.writes), ('planned', True, []))
        self.assertEqual((preview['label'], preview['url'], preview['target_source']['id']),
                         ('2nd deck ↗', deck_url(FOR_SECOND), FOR_SECOND))
        self.assertEqual(self.grids, before)
        done = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                              dry_run=False, port=self.port)
        self.assertEqual((done['status'], done['changed']), ('repaired_and_verified', True))
        [[request]] = self.writes
        self.assertEqual(request['updateCells']['range'], {
            'sheetId': 3, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 13, 'endColumnIndex': 14})
        self.assertEqual(agenda.links(grid[6, 14]), [(deck_url(FOR_SECOND), '2nd deck ↗')])
        after = deepcopy(self.grids)
        after['🔬 Forensics'][6, 14] = before['🔬 Forensics'][6, 14]
        self.assertEqual(after, before, 'all lesson, Form, folder, hidden URL and unrelated cells are preserved')
        receipt_id = key('weeklypreflightrepair', finding['fingerprint'])
        self.assertEqual(self.port.read([receipt_id]).documents[receipt_id].value['target_source'],
                         self.src.files[FOR_SECOND])
        again = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                               dry_run=False, port=self.port)
        self.assertEqual((again['changed'], len(self.writes)), (False, 1))

    def test_only_second_deck_blank_or_dash_placeholders_gain_a_neutral_label(self):
        for placeholder in ('', '—', '–', '-'):
            self.no_formula_column(placeholder)
            [finding] = self.plan(6, 'forensics')
            self.assertEqual(finding['plan']['label'], '2nd deck ↗')
        for prose in ('?', 'see the lab binder', 'Do not link yet'):
            self.no_formula_column(prose)
            [finding] = self.plan(6, 'forensics')
            self.assertNotIn('plan', finding)
        grid = self.grids['🌿 Biology']
        for row in (4, 5, 7):
            grid.pop((row, 15), None)
        grid[6, 15], grid[6, 19] = cell('—'), cell(deck_url(FOR_SECOND))
        finding = next(f for f in self.plan(6, 'biology') if f['role'] == 'Extra material')
        self.assertNotIn('plan', finding, 'a hidden Slides URL is not enough outside the exact second-deck role')

    def test_second_deck_ambiguous_columns_and_multiple_or_non_slides_urls_have_no_plan(self):
        original = deepcopy(self.no_formula_column('—'))
        cases = [((3, 24), cell('2nd deck')),
                 ((6, 18), cell(deck_url(FOR_SECOND) + ' ' + deck_url(FOR_DECK))),
                 ((6, 18), cell('https://docs.google.com/document/d/' + FOR_SECOND + '/edit')),
                 ((6, 18), cell('https://untrusted.example/presentation/d/' + FOR_SECOND + '/edit'))]
        for position, value in cases:
            with self.subTest(position=position, value=value):
                self.grids['🔬 Forensics'] = deepcopy(original)
                self.grids['🔬 Forensics'][position] = value
                [finding] = self.plan(6, 'forensics')
                self.assertNotIn('plan', finding)
        self.grids['🔬 Forensics'] = deepcopy(original)
        self.grids['🔬 Forensics'][3, 18] = cell('2nd deck URL')
        self.grids['🔬 Forensics'][3, 24] = cell('second deck URL')
        [finding] = self.plan(6, 'forensics')
        self.assertNotIn('plan', finding)

    def test_placeholder_target_must_be_exact_readable_untrashed_slides_with_complete_metadata(self):
        self.no_formula_column('—')
        [finding] = self.plan(6, 'forensics')
        original = deepcopy(self.src.files[FOR_SECOND])
        for field, value in (('id', FOR_DECK), ('mimeType', google.DOC), ('trashed', True),
                             ('version', ''), ('modifiedTime', ''), ('name', ''), ('parents', None)):
            with self.subTest(field=field):
                self.src.files[FOR_SECOND] = {**original, field: value}
                for dry_run in (True, False):
                    with self.assertRaisesRegex(wp.Held, 'complete current metadata'):
                        wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                                       dry_run=dry_run, port=self.port)
        with patch.object(google, 'drive_file', side_effect=google.GoogleError('Access denied', 403)):
            with self.assertRaisesRegex(wp.Held, 'could not be verified'):
                wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                               dry_run=False, port=self.port)
        self.assertEqual(self.writes, [])

    def test_placeholder_repair_holds_changed_hidden_url_lesson_and_visible_content(self):
        original = deepcopy(self.no_formula_column('—'))
        [finding] = self.plan(6, 'forensics')
        stale = self.agenda()
        for position, value in (((6, 18), cell(deck_url(FOR_DECK))), ((6, 3), cell('A different lesson')),
                                ((6, 14), cell('Teacher notes')),
                                ((6, 14), shown('R', 6, LABELS['second'], deck_url(FOR_SECOND)))):
            with self.subTest(position=position):
                self.grids['🔬 Forensics'] = deepcopy(original)
                self.grids['🔬 Forensics'][position] = value
                with self.assertRaisesRegex(wp.Held, 'changed'):
                    wp.repair_link('read', 'write', self.config, stale, finding['fingerprint'],
                                   dry_run=False, port=self.port)
        self.assertEqual(self.writes, [])

    def test_placeholder_source_and_row_changes_during_bookends_stop_before_write(self):
        original = deepcopy(self.no_formula_column('—'))
        original_source = deepcopy(self.src.files[FOR_SECOND])
        [finding] = self.plan(6, 'forensics')
        for change in ('version', 'name', 'parents', 'hidden_url'):
            with self.subTest(change=change):
                self.grids['🔬 Forensics'] = deepcopy(original)
                self.src.files[FOR_SECOND] = deepcopy(original_source)
                calls = []

                def changing_read(token, ident, fields=None):
                    calls.append(ident)
                    if len(calls) == 2:
                        if change == 'hidden_url':
                            self.grids['🔬 Forensics'][6, 18] = cell(deck_url(FOR_DECK))
                        else:
                            self.src.files[ident][change] = ['DifferentFolder'] if change == 'parents' else 'changed'
                    return self.src.drive_file(token, ident, fields)

                with patch.object(google, 'drive_file', side_effect=changing_read):
                    with self.assertRaisesRegex(wp.Held, 'changed'):
                        wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                                       dry_run=False, port=self.port)
        self.assertEqual(self.writes, [])

    def test_placeholder_write_requires_exact_url_source_and_row_readback(self):
        original = deepcopy(self.no_formula_column('—'))
        original_source = deepcopy(self.src.files[FOR_SECOND])
        [finding] = self.plan(6, 'forensics')
        for change in ('visible_url', 'source_revision', 'hidden_url', 'read_failure'):
            with self.subTest(change=change):
                self.grids['🔬 Forensics'] = deepcopy(original)
                self.src.files[FOR_SECOND] = deepcopy(original_source)
                self.writes.clear()

                def write_then_change(token, sheet, requests):
                    self.batch_update(token, sheet, requests)
                    if change == 'visible_url':
                        self.grids['🔬 Forensics'][6, 14]['textFormatRuns'][0]['format']['link']['uri'] += '?different=1'
                    elif change == 'source_revision':
                        self.src.bump(FOR_SECOND)
                    elif change == 'hidden_url':
                        self.grids['🔬 Forensics'][6, 18] = cell(deck_url(FOR_DECK))
                    else:
                        self.src.files.pop(FOR_SECOND)

                with patch.object(google, 'batch_update', side_effect=write_then_change):
                    result = wp.repair_link('read', 'write', self.config, self.agenda(), finding['fingerprint'],
                                            dry_run=False, port=self.port)
                self.assertEqual((result['status'], result['changed'], len(self.writes)), ('unconfirmed', True, 1))
                receipt_id = key('weeklypreflightrepair', finding['fingerprint'])
                self.assertIsNone(self.port.read([receipt_id]).documents[receipt_id])


class PrintSourcePreferenceTests(Base):
    EXTRA = 'FICTIONALEXPLICITGUIDE001'
    COPY = 'FICTIONALFOLDERGUIDE0001'

    def extra(self, ident=EXTRA, title='Cycle 06 — CER worksheet', parent=None):
        self.src.add(ident, title, google.DOC, [parent] if parent else [])
        self.src.pdfs[ident] = pdf_bytes(605)
        self.grids['🌿 Biology'][5, 19] = cell('https://docs.google.com/document/d/%s/edit' % ident)

    def test_unique_current_agenda_extra_breaks_only_a_tied_print_match(self):
        self.extra()
        before = deepcopy(self.src.files)
        result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual(item['file']['id'], self.EXTRA)
        self.assertFalse([e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'print_item_ambiguous'])
        self.assertEqual(self.src.files, before, 'both originals are preserved')
        self.assertEqual(self.writes, [])
        self.assertNotIn('Printed', json.dumps(result))
        self.assertNotIn('Handed', json.dumps(result))

    def test_unique_better_folder_match_outranks_explicit_less_precise_match(self):
        self.extra(title='Cycle 06 — CER worksheet teacher answer key')
        result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual(item['file']['id'], HANDOUT)

    def test_stale_same_id_folder_alias_cannot_promote_a_worse_current_agenda_source(self):
        self.extra(title='Cycle 06 — CER worksheet teacher answer key', parent=BIO_FOLDER)
        before = deepcopy(self.src.files)

        def stale_listing(token, ident):
            items, complete = self.src.folder_listing(token, ident)
            for item in items:
                if item['id'] == self.EXTRA:
                    item['name'] = 'Cycle 06 — CER worksheet'
            return items, complete

        with patch.object(google, 'folder_listing', side_effect=stale_listing):
            result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual(item['file']['id'], HANDOUT)
        self.assertFalse([e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'print_item_ambiguous'])
        self.assertEqual(self.src.calls['pdf:' + self.EXTRA], 0, 'the stale alias must not trigger an export of the wrong handout')
        self.assertEqual(self.src.files, before)
        self.assertEqual(self.writes, [])

    def test_explicit_current_name_controls_matching_before_alias_deduplication_in_either_order(self):
        correct = {'id': HANDOUT, 'name': 'CER worksheet'}
        alias = {'id': self.EXTRA, 'name': 'CER worksheet'}
        for current_name in ('CER worksheet teacher answer key', 'Lab safety teacher notes'):
            explicit = {'id': self.EXTRA, 'name': current_name, 'agenda_extra_material': True}
            for files in ([alias, correct, explicit], [explicit, correct, alias]):
                with self.subTest(current_name=current_name, explicit_first=files[0] == explicit):
                    best, score, ambiguous = wp.match_file('CER worksheet', files)
                    self.assertEqual((best['id'], score, ambiguous), (HANDOUT, 1.0, False))

    def test_multiple_explicit_choices_and_folder_only_ties_remain_ambiguous(self):
        one = {'id': self.EXTRA, 'name': 'CER worksheet', 'agenda_extra_material': True}
        two = {'id': self.COPY, 'name': 'CER worksheet', 'agenda_extra_material': True}
        self.assertTrue(wp.match_file('CER worksheet', [one, two])[2])
        self.assertTrue(wp.match_file('CER worksheet', [{**one, 'agenda_extra_material': False},
                                                       {**two, 'agenda_extra_material': False}])[2])
        worse = {'id': 'FICTIONALWORSEGUIDE0001', 'name': 'CER worksheet teacher answer key',
                 'agenda_extra_material': True}
        self.assertTrue(wp.match_file('CER worksheet', [{**one, 'agenda_extra_material': False},
                                                       {**two, 'agenda_extra_material': False}, worse])[2])

    def test_one_file_linked_in_the_agenda_and_folder_does_not_tie_with_itself(self):
        self.extra(ident=HANDOUT, title=self.src.files[HANDOUT]['name'])
        result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual(item['file']['id'], HANDOUT)
        self.assertFalse([e for e in self.exceptions(result, 'biology', 5) if e['kind'] == 'print_item_ambiguous'])

    def test_same_id_duplicate_references_do_not_hide_two_explicit_choices(self):
        one = {'id': self.EXTRA, 'name': 'CER worksheet', 'agenda_extra_material': True}
        two = {'id': self.COPY, 'name': 'CER worksheet', 'agenda_extra_material': True}
        self.assertTrue(wp.match_file('CER worksheet', [one, one, two])[2])
        best, _, ambiguous = wp.match_file('CER worksheet', [{**one, 'agenda_extra_material': False}, one])
        self.assertEqual((best['id'], ambiguous), (self.EXTRA, False))

    def test_selected_explicit_material_read_failure_does_not_fall_back_to_folder_copy(self):
        self.extra()

        def unavailable(token, ident, mime):
            if ident == self.EXTRA:
                raise google.GoogleError('Temporarily unavailable', 429)
            return self.src.printable_pdf_bytes(token, ident, mime)

        with patch.object(google, 'printable_pdf_bytes', side_effect=unavailable):
            result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual((item['file']['id'], item['file']['pages']), (self.EXTRA, None))
        self.assertIn('print_file_unreadable', {e['kind'] for e in self.exceptions(result, 'biology', 5)})
        self.assertEqual(self.src.calls['pdf:' + HANDOUT], 0)

    def test_duplicate_folder_metadata_cannot_bypass_current_explicit_source_read_failure(self):
        self.extra(ident=HANDOUT, title=self.src.files[HANDOUT]['name'])

        def stale_listing(token, ident):
            items, complete = self.src.folder_listing(token, ident)
            for item in items:
                if item['id'] == HANDOUT:
                    item.update(mimeType=wp.DOCX, version='old-folder-revision')
            return items, complete

        def unavailable(token, ident, mime):
            if ident == HANDOUT:
                raise google.GoogleError('Current source unavailable', 403)
            return self.src.printable_pdf_bytes(token, ident, mime)

        with patch.object(google, 'folder_listing', side_effect=stale_listing), \
                patch.object(google, 'printable_pdf_bytes', side_effect=unavailable):
            result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual((item['file']['id'], item['file']['mimeType'], item['file']['pages']),
                         (HANDOUT, google.DOC, None))
        self.assertNotIn('print_from_folder', item['file'])
        self.assertIn('print_file_unreadable', {e['kind'] for e in self.exceptions(result, 'biology', 5)})

    def test_duplicate_folder_reference_cannot_mask_unavailable_current_metadata(self):
        self.extra(ident=HANDOUT, title=self.src.files[HANDOUT]['name'])

        def unavailable(token, ident, fields=None):
            if ident == HANDOUT:
                raise google.GoogleError('Current metadata unavailable', 403)
            return self.src.drive_file(token, ident, fields)

        with patch.object(google, 'drive_file', side_effect=unavailable):
            result = self.run_check()
        item = next(p for p in result['print_items'] if p['text'] == 'CER worksheet (1 per student)')
        self.assertEqual((item['file']['id'], item['file']['pages']), (HANDOUT, None))
        self.assertEqual(self.src.calls['pdf:' + HANDOUT], 0)
        self.assertIn('source_inaccessible', {e['kind'] for e in self.exceptions(result, 'biology', 5)})


class FolderListingTests(unittest.TestCase):
    def test_hitting_the_page_cap_with_another_page_is_reported_incomplete(self):
        page = {'files': [{'id': 'FictionalChild001', 'name': 'a', 'mimeType': 'application/pdf'}], 'nextPageToken': 'more'}
        with patch.object(google, 'get_json', return_value=page) as read:
            items, complete = google.folder_listing('read', 'FictionalFolder01')
        self.assertFalse(complete)
        self.assertEqual(read.call_count, google.LISTING_PAGES)
        self.assertEqual(len(items), google.LISTING_PAGES)
        with patch.object(google, 'get_json', return_value=page):
            self.assertEqual(len(google.folder_children('read', 'FictionalFolder01')), google.LISTING_PAGES)

    def test_trashed_items_are_excluded_and_shortcuts_name_their_target(self):
        page = {'files': [{'id': 'FictionalChild001', 'name': 'kept.pdf', 'mimeType': 'application/pdf'},
                          {'id': 'FictionalChild002', 'name': 'gone.pdf', 'mimeType': 'application/pdf', 'trashed': True},
                          {'id': 'FictionalShortcut1', 'name': 'Deck', 'mimeType': google.SHORTCUT,
                           'shortcutDetails': {'targetId': BIO_DECK, 'targetMimeType': completion.SLIDES}}]}
        with patch.object(google, 'get_json', return_value=page) as read:
            items, complete = google.folder_listing('read', 'FictionalFolder01')
        self.assertTrue(complete)
        self.assertEqual([i['id'] for i in items], ['FictionalChild001', 'FictionalShortcut1'])
        self.assertEqual(items[1]['target'], {'id': BIO_DECK, 'mimeType': completion.SLIDES})
        params = read.call_args.args[2]
        self.assertIn('trashed = false', params['q'])
        self.assertIn('shortcutDetails(targetId,targetMimeType)', params['fields'])


class FakeArchive:
    """The private packet folder and created files, as the write token sees them."""

    def __init__(self, src):
        self.src, self.items, self.contents, self.created, self.sequence = src, {}, {}, [], 0
        self.add('FictionalPacketsFolder01', 'Private packets', google.FOLDER)

    def add(self, ident, name, mime, parents=()):
        self.items[ident] = {'id': ident, 'name': name, 'mimeType': mime, 'parents': list(parents), 'trashed': False,
                             'permissions': deepcopy(PERMISSIONS), 'capabilities': {'canAddChildren': True},
                             'properties': {}, 'createdTime': '2026-10-14T12:00:00Z',
                             'modifiedTime': '2026-10-14T12:00:00Z', 'version': '1'}
        return self.items[ident]

    def metadata(self, token, ident, fields=None):
        if ident in self.items:
            return deepcopy(self.items[ident])
        return self.src.drive_file(token, ident, fields)

    def reserve(self, token, *, shortcut=False):
        self.sequence += 1
        return 'FictionalReservedPacket%03d' % self.sequence

    def create(self, token, metadata, raw):
        import hashlib
        self.created.append(deepcopy(metadata))
        item = self.add(metadata['id'], metadata['name'], metadata['mimeType'], metadata['parents'])
        item.update(properties=deepcopy(metadata['properties']), md5Checksum=hashlib.md5(raw).hexdigest(),
                    size=str(len(raw)))
        self.contents[metadata['id']] = raw
        return deepcopy(item)

    def pdf(self, token, ident, mime):
        return self.contents[ident] if ident in self.contents else self.src.printable_pdf_bytes(token, ident, mime)


class PacketTests(Base):
    def setUp(self):
        super().setUp()
        self.archive = FakeArchive(self.src)
        self.config[daily.FOLDER_CONFIG] = 'FictionalPacketsFolder01'
        for item in (patch.object(google, 'drive_file', side_effect=self.archive.metadata),
                     patch.object(google, 'archive_file_id', side_effect=self.archive.reserve),
                     patch.object(google, 'archive_create', side_effect=self.archive.create),
                     patch.object(google, 'printable_pdf_bytes', side_effect=self.archive.pdf)):
            item.start()
            self.addCleanup(item.stop)
        self.run_check()

    def build(self):
        return weekly_packet.build('read', 'write', self.config, '2026-10-19', 1, port=self.port)

    def test_identical_inputs_return_the_same_packet_and_teacher_only_pages_come_last(self):
        one, again = self.build(), self.build()
        self.assertEqual(one['id'], again['id'])
        self.assertTrue(again['cached'])
        self.assertEqual(len(self.archive.created), 1)
        self.assertEqual((one['kind'], one['student_feedback'], one['email_eligible']), ('weekly_lesson_packet', False, False))
        raw, name = weekly_packet.pdf('read', self.config, one['id'], port=self.port)
        self.assertIn('Weekly lesson packet', name)
        self.assertNotIn('feedback', name.casefold())
        pages = PdfReader(io.BytesIO(raw)).pages
        widths = [round(float(page.mediabox.width)) for page in pages]
        handout, separator = widths.index(601), widths.index(601) + 1
        self.assertTrue(all(w == 612 for w in widths[:handout]))
        self.assertEqual(widths[separator:], [612, 603, 603])
        self.assertIn('TEACHER-ONLY', pages[separator].extract_text())
        cover = pages[0].extract_text()
        self.assertIn('Monday preparation packet', cover)
        self.assertIn(one['version'][:16], cover)
        text = '\n'.join(pages[i].extract_text() for i in range(handout))
        self.assertIn('Flow not yet reviewed', text)
        self.assertIn('A4 Biology: 22', text)
        self.assertIn('packet pages %d-%d' % (handout + 1, handout + 1), text)
        self.assertIn('Supply list', text)
        self.assertNotIn('answer key', text.split('Supply list')[0].split('Print')[-1])
        self.assertEqual(self.archive.created[0]['properties'][weekly_packet.TAG], one['id'])
        self.assertEqual(weekly_packet.saved(self.config, '2026-10-19', 1, port=self.port)['id'], one['id'])

    def test_the_saved_packet_is_served_as_a_pdf_to_the_signed_in_teacher_only(self):
        packet = self.build()
        environ = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/v2/api/weekly-preflight/packet.pdf',
                   'QUERY_STRING': 'id=' + packet['id'], 'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional-assertion'}
        result = {}

        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        with patch.object(google, 'verify_teacher', return_value='teacher@school.example'), \
                patch.object(google, 'service_token', return_value='fictional-token'):
            body = b''.join(app.application(environ, start, self.config))
        self.assertEqual((result['status'], result['headers']['Content-Type']), ('200 OK', 'application/pdf'))
        self.assertTrue(body.startswith(b'%PDF'))
        with patch.object(google, 'verify_teacher', side_effect=google.SignInRequired()):
            b''.join(app.application(environ, start, self.config))
        self.assertEqual(result['status'], '401 Unauthorized')

    def test_a_changed_source_revision_makes_a_new_version_and_keeps_the_old_one(self):
        before = self.build()
        self.src.bump(HANDOUT)
        self.run_check()
        after = self.build()
        self.assertNotEqual(before['id'], after['id'])
        self.assertEqual(len(self.archive.created), 2)
        self.assertTrue(weekly_packet.pdf('read', self.config, before['id'], port=self.port)[0].startswith(b'%PDF'))

    def test_a_web_print_item_is_a_print_instruction_in_the_packet_not_a_handout(self):
        slides = deepcopy(BIO_SLIDES)
        slides[3] = [('reference', slides[3][0][1].replace(
            '• Lab safety card (1 per group)\n', '• Science World — Yeast-Inflated Balloons (1 per group)\n'))]
        slides.append([('links', 'TEACHER NAVIGATION\nScience World — Yeast-Inflated Balloons\n' + WEB_PAGE)])
        self.src.decks[BIO_DECK] = slides
        self.src.bump(BIO_DECK)
        self.run_check()
        packet = self.build()
        raw, _ = weekly_packet.pdf('read', self.config, packet['id'], port=self.port)
        text = re.sub(r'\s+', '', ''.join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages))
        self.assertIn('Printfromweb:%sasthepageprints' % WEB_PAGE, text)

    def test_a_handout_changed_since_the_check_stops_the_build(self):
        self.src.bump(HANDOUT)
        with self.assertRaisesRegex(weekly_packet.Held, 'changed since the week was checked'):
            self.build()
        self.assertEqual(self.archive.created, [])

    def test_an_unfinished_check_never_becomes_a_packet(self):
        with self.assertRaisesRegex(weekly_packet.Held, 'to completion'):
            weekly_packet.build('read', 'write', self.config, '2026-11-02', 1, port=self.port)

    def test_the_packet_never_enters_the_feedback_catalog_or_print_ready_email(self):
        packet = self.build()
        root = 'FictionalLegacyFeedbackRoot1'
        self.archive.add(root, 'Student feedback - Private', google.FOLDER)
        folders = {course: 'FictionalLegacy%sFolder1' % course for course in native_feedback.FEEDBACK_FOLDERS}
        for course, ident in folders.items():
            self.archive.add(ident, native_feedback.FEEDBACK_FOLDERS[course], google.FOLDER, [root])
        moved = self.archive.items[packet_file(self.port, packet['id'])]
        moved.update(name='Student feedback - Fictional learner.pdf', parents=[folders['biology']])
        control = self.archive.add('FictionalRealFeedback001', 'Student feedback - Fictional original.pdf',
                                   'application/pdf', [folders['biology']])
        control['properties'] = {'hubStudentFeedbackOnly': 'true'}
        legacy = {native_feedback.FEEDBACK_ROOT_CONFIG: root, native_feedback.FEEDBACK_FOLDER_CONFIG: json.dumps(folders)}

        def children(token, parent):
            return [deepcopy(i) for i in self.archive.items.values() if parent in i['parents']]
        with patch.object(google, 'folder_children', side_effect=children):
            catalog = native_feedback._legacy_feedback_files('read', legacy)
            email_catalog = print_ready_email._catalog('read', legacy)
        self.assertEqual([item['id'] for item in catalog['files']], ['FictionalRealFeedback001'])
        self.assertEqual([item['id'] for item in email_catalog], ['FictionalRealFeedback001'])


def packet_file(port, ident):
    return port.read([ident]).documents[ident].value['drive_file_id']


class RouteTests(Base):
    def call(self, method, path, body=None, query='', origin=ORIGIN):
        raw = json.dumps(body).encode() if body is not None else b''
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
                   'HTTP_X_GOOG_IAP_JWT_ASSERTION': 'fictional-assertion', 'HTTP_ORIGIN': origin,
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        result = {}

        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        result['body'] = b''.join(app.application(environ, start, self.config))
        return result

    def setUp(self):
        super().setUp()
        for item in (patch.object(google, 'verify_teacher', return_value='teacher@school.example'),
                     patch.object(google, 'service_token', return_value='fictional-token'),
                     patch.object(app, 'load_agenda', side_effect=lambda token, config, fresh=False: self.agenda()),
                     patch.object(app, 'section_counts', return_value=COUNTS)):
            item.start()
            self.addCleanup(item.stop)

    def test_get_reads_saved_state_and_posts_need_the_dashboard_origin(self):
        empty = self.call('GET', '/v2/api/weekly-preflight', query='start=2026-10-19&weeks=1')
        self.assertEqual(empty['status'], '200 OK')
        self.assertEqual(json.loads(empty['body'])['status'], 'not_checked')
        self.assertEqual(self.port.rows, {})
        refused = self.call('POST', '/v2/api/weekly-preflight/run', {'start': '2026-10-19'}, origin='https://elsewhere.example')
        self.assertEqual(refused['status'], '403 Forbidden')
        ran = self.call('POST', '/v2/api/weekly-preflight/run', {'start': '2026-10-19', 'weeks': 1})
        self.assertEqual(ran['status'], '200 OK')
        stored = json.loads(self.call('GET', '/v2/api/weekly-preflight', query='start=2026-10-21&weeks=1')['body'])
        self.assertEqual(stored['start'], '2026-10-19')
        self.assertEqual(stored['packet'], {'status': 'unconfigured'})
        self.assertTrue(stored['lessons'])
        bad = self.call('GET', '/v2/api/weekly-preflight', query='start=2026-10-19&weeks=5')
        self.assertEqual(bad['status'], '409 Conflict')

    def test_repair_route_is_a_dry_run_unless_asked(self):
        [finding] = wp.link_findings(self.agenda(), 'biology', [5])
        preview = self.call('POST', '/v2/api/weekly-preflight/repair', {'fingerprint': finding['fingerprint']})
        self.assertEqual(json.loads(preview['body'])['status'], 'planned')
        self.assertEqual(self.writes, [])
        applied = self.call('POST', '/v2/api/weekly-preflight/repair', {'fingerprint': finding['fingerprint'],
                                                                         'dry_run': False})
        self.assertEqual(json.loads(applied['body'])['status'], 'repaired_and_verified')
        self.assertEqual(len(self.writes), 1)
        bad = self.call('POST', '/v2/api/weekly-preflight/repair', {'fingerprint': finding['fingerprint'], 'dry_run': 'no'})
        self.assertEqual(bad['status'], '400 Bad Request')

    def test_deck_repair_route_is_a_dry_run_unless_asked(self):
        body = {'op': 'replace_text', 'deck_id': BIO_NEXT,
                'replacements': [{'find': 'Explain what you observed.', 'replace': 'Explain what the plant did.',
                                  'slide_numbers': [2]}]}
        planned = json.loads(self.call('POST', '/v2/api/weekly-preflight/deck-repair', body)['body'])
        self.assertEqual((planned['status'], planned['revision_id']), ('planned', 'rev-1'))
        refused = self.call('POST', '/v2/api/weekly-preflight/deck-repair',
                            {**body, 'replacements': [{'find': '[[NOTES:C07:Q1]]', 'replace': 'x', 'slide_numbers': [2]}]})
        self.assertEqual(refused['status'], '409 Conflict')
        self.assertEqual(self.call('POST', '/v2/api/weekly-preflight/deck-repair', {**body, 'op': 'other'})['status'],
                         '400 Bad Request')

    def test_review_route_saves_one_record_for_the_signed_in_teacher(self):
        result = self.run_check('2026-10-26')
        deck = next(n for n in self.lesson(result, 'anatomy', 6)['sources'] if n['id'] == AP_NEXT)
        body = {'kind': 'visual_check', 'class': 'anatomy', 'week': 'Wk 10', 'row': 6,
                'sources': [{'id': AP_NEXT, 'revision': deck['revision']}], 'text': 'Slides 2-3 read cleanly.',
                'verdict': 'ok'}
        saved = json.loads(self.call('POST', '/v2/api/weekly-preflight/review', body)['body'])
        self.assertEqual(saved['current']['reviewer'], 'teacher@school.example')
        self.assertEqual(self.call('POST', '/v2/api/weekly-preflight/review', {**body, 'kind': 'nope'})['status'],
                         '409 Conflict')


class NativeDeck:
    """A fictional Slides deck with revisions, applying the requests the repair sends."""

    def __init__(self, slides):
        self.revision, self.requests = 1, []
        self.pages = [{'objectId': 'page%d' % n, 'pageElements': [
            {'objectId': 'shape%d_%d' % (n, i), 'shape': {'text': {'textElements': [{'textRun': {'content': text + '\n'}}]}}}
            for i, text in enumerate(shapes)]} for n, shapes in enumerate(slides, 1)]

    def text(self, slide, index):
        return self.pages[slide - 1]['pageElements'][index]['shape']['text']['textElements'][0]['textRun']['content']

    def set(self, slide, index, value):
        self.pages[slide - 1]['pageElements'][index]['shape']['text']['textElements'] = [{'textRun': {'content': value}}]

    def read(self, token, ident):
        return {'presentationId': DECK_ID, 'revisionId': 'rev%d' % self.revision, 'slides': deepcopy(self.pages)}

    def meta(self, token, ident, fields=None):
        return {'id': DECK_ID, 'name': 'Fictional deck', 'mimeType': completion.SLIDES, 'trashed': False,
                'version': str(self.revision), 'modifiedTime': '2026-10-14T12:00:00Z'}

    def update(self, token, ident, requests, required):
        if required != 'rev%d' % self.revision:
            raise google.GoogleError('Google rejected the request (a tab or range may have been renamed).', 400)
        self.requests.append(deepcopy(requests))
        for request in requests:
            if 'replaceAllText' in request:
                body = request['replaceAllText']
                for page in self.pages:
                    if page['objectId'] in body['pageObjectIds']:
                        for i, _ in enumerate(page['pageElements']):
                            n = self.pages.index(page) + 1
                            self.set(n, i, self.text(n, i).replace(body['containsText']['text'], body['replaceText']))
            elif 'deleteText' in request:
                self._find(request['deleteText']['objectId'], '')
            elif 'insertText' in request:
                self._find(request['insertText']['objectId'], request['insertText']['text'] + '\n')
        self.revision += 1
        return {}

    def _find(self, object_id, value):
        for n, page in enumerate(self.pages, 1):
            for i, element in enumerate(page['pageElements']):
                if element['objectId'] == object_id:
                    self.set(n, i, value)


DECK_ID = 'FICTIONALREPAIRDECK00001'


class DeckRepairTests(unittest.TestCase):
    def deck(self, slides):
        deck = NativeDeck(slides)
        for item in (patch.object(google, 'native_presentation', side_effect=deck.read),
                     patch.object(google, 'drive_file', side_effect=deck.meta),
                     patch.object(google, 'slides_batch_update', side_effect=deck.update)):
            item.start()
            self.addCleanup(item.stop)
        return deck

    def replace(self, find, replace, slides, **kwargs):
        return deck_repair.replace_text('read', 'write', DECK_ID, [{'find': find, 'replace': replace,
                                                                     'slide_numbers': slides}], **kwargs)

    def test_a_replacement_changes_only_the_listed_slides_and_reads_back(self):
        deck = self.deck([['Title'], ['Due Week 3', 'Week 3 lab'], ['Due Week 3']])
        planned = self.replace('Week 3', 'Week 4', [2])
        self.assertEqual((planned['status'], deck.requests), ('planned', []))
        done = self.replace('Week 3', 'Week 4', [2], dry_run=False)
        self.assertEqual(done['status'], 'applied_and_verified')
        self.assertEqual(deck.requests[0][0]['replaceAllText']['pageObjectIds'], ['page2'])
        self.assertTrue(deck.requests[0][0]['replaceAllText']['containsText']['matchCase'])
        self.assertEqual([deck.text(2, 0), deck.text(2, 1), deck.text(3, 0)], ['Due Week 4\n', 'Week 4 lab\n', 'Due Week 3\n'])
        self.assertEqual(done['receipts'], [{'slide': 2, 'find': 'Week 3', 'replace': 'Week 4', 'before_count': 2,
                                             'after_count': 0, 'status': 'applied'}])
        self.assertEqual((done['previous_revision_id'], done['revision_id']), ('rev1', 'rev2'))

    def test_a_match_inside_a_shape_with_response_codes_refuses_the_whole_request(self):
        deck = self.deck([['Explain Week 3 [[NOTES:C06:Q1]]', 'Week 3 title']])
        with self.assertRaisesRegex(deck_repair.Refused, 'response codes'):
            self.replace('Week 3', 'Week 4', [1], dry_run=False)
        self.assertEqual(deck.requests, [])

    def test_a_repeated_replacement_is_already_applied_and_writes_nothing(self):
        deck = self.deck([['Due Week 3']])
        self.replace('Week 3', 'Week 4', [1], dry_run=False)
        again = self.replace('Week 3', 'Week 4', [1], dry_run=False)
        self.assertEqual((again['status'], again['changed']), ('nothing_to_do', False))
        self.assertEqual(again['receipts'][0]['status'], 'already_applied')
        self.assertEqual(len(deck.requests), 1)
        with self.assertRaisesRegex(deck_repair.Refused, "isn't on slide"):
            self.replace('Week 9', 'Week 10', [1], dry_run=False)

    def test_a_changed_revision_refuses_before_any_write(self):
        deck = self.deck([['Due Week 3']])
        with self.assertRaisesRegex(deck_repair.Refused, 'changed since'):
            self.replace('Week 3', 'Week 4', [1], dry_run=False, expected_revision_id='rev0')
        self.assertEqual(deck.requests, [])

    def test_the_inventory_is_rebuilt_from_embedded_codes_in_order(self):
        deck = self.deck([
            ['Teacher codes [[MARKER-INVENTORY:C06]] NOTES=2 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q1,Q2 '
             'DRAFT_IDS=Q1 OPTIONAL_IDS=-'],
            ['Question one', '[[NOTES:C06:Q1]]', '[[DRAFT:C06:Q1]]'],
            ['Question three', '[[NOTES:C06:Q3]]']])
        before = deck_repair.sync_inventory('read', 'write', DECK_ID)
        self.assertEqual((before['status'], before['missing_before'], before['extra_before']),
                         ('planned', ['NOTES Q2'], ['NOTES Q3']))
        done = deck_repair.sync_inventory('read', 'write', DECK_ID, dry_run=False)
        self.assertEqual(done['status'], 'applied_and_verified')
        self.assertEqual(deck.text(1, 0), 'Teacher codes [[MARKER-INVENTORY:C06]] NOTES=2 DRAFT=1 OPTIONAL=0 '
                                          'BANK_TERMS=0 NOTES_IDS=Q1,Q3 DRAFT_IDS=Q1 OPTIONAL_IDS=-\n')
        self.assertEqual((done['missing_after'], done['extra_after']), ([], []))
        self.assertEqual([deck.text(2, 1), deck.text(3, 1)], ['[[NOTES:C06:Q1]]\n', '[[NOTES:C06:Q3]]\n'])
        self.assertEqual([r['deleteText']['objectId'] if 'deleteText' in r else r['insertText']['objectId']
                          for r in deck.requests[0]], ['shape1_0', 'shape1_0'])
        again = deck_repair.sync_inventory('read', 'write', DECK_ID, dry_run=False)
        self.assertEqual((again['status'], len(deck.requests)), ('nothing_to_do', 1))

    def test_a_matching_inventory_writes_nothing(self):
        deck = self.deck([[deck_repair.inventory_line('C06', [('NOTES', 'Q1'), ('DRAFT', 'Q1')])],
                          ['[[NOTES:C06:Q1]]', '[[DRAFT:C06:Q1]]']])
        self.assertEqual(completion.readiness({'slides': [{'number': 1, 'units': [{'key': 'a', 'text': deck.text(1, 0), 'box': None}]},
                                                          {'number': 2, 'units': [{'key': 'b', 'text': '[[NOTES:C06:Q1]]', 'box': None},
                                                                                  {'key': 'c', 'text': '[[DRAFT:C06:Q1]]', 'box': None}]}]})['status'],
                         'READY')
        result = deck_repair.sync_inventory('read', 'write', DECK_ID, dry_run=False)
        self.assertEqual((result['status'], deck.requests), ('nothing_to_do', []))

    def test_an_inventory_sharing_a_box_with_answer_codes_is_refused(self):
        deck = self.deck([['[[MARKER-INVENTORY:C06]] NOTES=1 DRAFT=0 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=Q1 [[NOTES:C06:Q1]]']])
        with self.assertRaisesRegex(deck_repair.Refused, 'response codes'):
            deck_repair.sync_inventory('read', 'write', DECK_ID, dry_run=False)
        self.assertEqual(deck.requests, [])


if __name__ == '__main__':
    unittest.main()
