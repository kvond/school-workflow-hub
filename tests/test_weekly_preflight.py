"""Week-ahead lesson preflight on a fictional Agenda, Drive, Slides and state.

Every provider call is a fake: no live Google, Schoology, model or school write
is reachable, and Form responses are never read.
"""
import io
import json
import re
import unittest
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
    return {'🗂 Weeks': weeks, '🌿 Biology': bio, '🔬 Forensics': forensics, '🫀 A&P': anatomy}


TABS = {'🗂 Weeks': 1, '🌿 Biology': 2, '🔬 Forensics': 3, '🫀 A&P': 4}


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
    return {'presentationId': ident, 'revisionId': 'rev-' + version, 'slides': [
        {'objectId': '%s_p%d' % (ident[-4:], n), 'pageElements': [
            {'objectId': '%s_s%d_%d' % (ident[-4:], n, i), 'shape': {'text': {'textElements': [
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
        patches = [patch.object(google, 'drive_file', side_effect=self.src.drive_file),
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
    def sheet_grid(self, token, spreadsheet_id, ranges):
        out = {}
        for value in ranges:
            match = re.fullmatch(r"'(.+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", value)
            name, c1, r1 = match.group(1).replace("''", "'"), match.group(2), int(match.group(3))
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

    def test_agenda_shorthand_gives_way_to_the_folder_name(self):
        self.assertEqual(wp.lesson_name(['Cycle 06 — Cellular Respiration & Fermentation (Unit 1)'], 'Fermentation'),
                         'Cellular Respiration & Fermentation')
        self.assertEqual(wp.lesson_name(['Week 08 - History of Forensics', 'Week 08 - History of Forensics',
                                         'Week 08b - Crime Scene to Courtroom (Mock Trial)'], 'Unit 1 Review'),
                         'History of Forensics + Crime Scene to Courtroom (Mock Trial)')
        self.assertEqual(wp.lesson_name(['Week 07'], 'Body Tissues (cont.)'), 'Body Tissues (cont.)')
        self.assertEqual(wp.lesson_name([], 'Body Tissues (cont.)'), 'Body Tissues (cont.)')

    def test_a_deck_kept_in_another_weekly_folder_joins_the_name_and_shorthand_is_kept(self):
        class Ctx:
            def file(self, ident):
                return {'FICTIONALCOURTFOLDER0001': {'name': 'Week 08b - Crime Scene to Courtroom (Mock Trial)'},
                        'FICTIONALGENERALFOLDER01': {'name': '03 FORENSICS (Public)'}}.get(ident, {'error': 'x'})
        lesson = {'topic': 'Unit 1 Review', 'exceptions': [{'lesson': 'Unit 1 Review'}], 'supply_rows': [],
                  'sources': [
                      {'id': 'FICTIONALHISTFOLDER0001', 'roles': ['folder'], 'accessible': True, 'trashed': False,
                       'name': 'Week 08 - History of Forensics'},
                      {'id': 'FICTIONALHISTDECK000001', 'roles': ['teacher_deck'], 'accessible': True,
                       'parents': ['FICTIONALHISTFOLDER0001', 'FICTIONALGENERALFOLDER01']},
                      {'id': 'FICTIONALCOURTDECK00001', 'roles': ['second_deck'], 'accessible': True,
                       'parents': ['FICTIONALCOURTFOLDER0001']}]}
        wp._name_lesson(Ctx(), lesson)
        self.assertEqual(lesson['topic'], 'History of Forensics + Crime Scene to Courtroom (Mock Trial)')
        self.assertEqual(lesson['agenda_topic'], 'Unit 1 Review')
        self.assertEqual(lesson['exceptions'][0]['lesson'], lesson['topic'])

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


class PreflightRunTests(Base):
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

    def test_a_blank_or_typed_cell_in_a_column_with_no_formula_still_needs_a_decision(self):
        self.no_formula_column('—')
        [blank] = self.plan(6, 'forensics')
        self.assertNotIn('plan', blank)
        self.assertIn('no label to link', blank['reason'])
        self.no_formula_column('see the lab binder')
        [typed] = self.plan(6, 'forensics')
        self.assertNotIn('plan', typed)
        self.assertIn('typed text', typed['reason'])
        # The label the column's other rows show as links is a label, not typed text.
        self.no_formula_column('2nd deck')
        self.grids['🔬 Forensics'][5, 14] = cell('2nd deck', textFormatRuns=[{'format': {'link': {'uri': deck_url(FOR_DECK2)}}}])
        [label] = self.plan(6, 'forensics')
        self.assertEqual((label['plan']['kind'], label['plan']['label']), ('linked_label', '2nd deck'))


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
