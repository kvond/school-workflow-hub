"""Fictional original-response repairs; no school data or live connections."""
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from hub_v2 import completion, embedded_code_repair as repair
from tests.test_cloud_state import MemoryCAS

FILE = 'FictionalOriginalPresentation12345'
STYLE = {'fontFamily': 'Arial', 'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'bold': False}
PARAGRAPH = {'style': {'alignment': 'START', 'spaceAbove': {'magnitude': 0, 'unit': 'PT'}}}


def text_elements(chars):
    """Normalize/split runs differently from the source while retaining styles."""
    output, start = [], 0
    for n, ((unit, style), paragraph) in enumerate(chars):
        output.append({'startIndex': n, 'endIndex': n + 1, 'textRun': {
            'content': bytes.fromhex(unit).decode('utf-16-le', errors='surrogatepass'), 'style': deepcopy(style)}})
        if unit == '0a00':
            output.append({'startIndex': start, 'endIndex': n + 1, 'paragraphMarker': deepcopy(paragraph)})
            start = n + 1
    # Rejoin surrogate pairs; Google never emits a half-character text run.
    joined = []
    for entry in output:
        if ('textRun' in entry and joined and 'textRun' in joined[-1]
                and joined[-1]['textRun']['style'] == entry['textRun']['style']):
            prior = joined[-1]
            value = prior['textRun']['content'] + entry['textRun']['content']
            prior['textRun']['content'] = value.encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='surrogatepass')
            prior['endIndex'] = entry['endIndex']
        else:
            joined.append(entry)
    return joined


def shape(ident, text, y, x=20, width=400, height=15):
    if not text.endswith('\n'):
        text += '\n'
    values = [[[unit.hex(), deepcopy(STYLE)], deepcopy(PARAGRAPH)] for unit in repair._units(text)]
    return {'objectId': ident, 'size': {'width': {'magnitude': width, 'unit': 'PT'},
                                      'height': {'magnitude': height, 'unit': 'PT'}},
            'transform': {'scaleX': 1, 'scaleY': 1, 'shearX': 0, 'shearY': 0,
                          'translateX': x, 'translateY': y, 'unit': 'PT'},
            'shape': {'shapeType': 'TEXT_BOX', 'shapeProperties': {'contentAlignment': 'TOP',
                      'autofit': {'autofitType': 'NONE', 'fontScale': 1}},
                      'text': {'textElements': text_elements(values)}}}


def pptx(native):
    """The real normal parser reads an actual synthetic OOXML archive."""
    P, A = completion.P, completion.A
    out = BytesIO()
    with ZipFile(out, 'w') as archive:
        pres = ET.Element(P + 'presentation')
        ET.SubElement(pres, P + 'sldSz', cx='9144000', cy='5143500')
        archive.writestr('ppt/presentation.xml', ET.tostring(pres))
        for index, page in enumerate(native['slides'], 1):
            root = ET.Element(P + 'sld')
            tree = ET.SubElement(ET.SubElement(root, P + 'cSld'), P + 'spTree')
            for element in page['pageElements']:
                if 'shape' not in element:
                    continue
                sp = ET.SubElement(tree, P + 'sp')
                nv = ET.SubElement(sp, P + 'nvSpPr')
                ET.SubElement(nv, P + 'cNvPr', name=element['objectId'], id=str(len(tree)))
                x, y, w, h = repair._box(element)
                xf = ET.SubElement(ET.SubElement(sp, P + 'spPr'), A + 'xfrm')
                ET.SubElement(xf, A + 'off', x=str(int(x)), y=str(int(y)))
                ET.SubElement(xf, A + 'ext', cx=str(int(w)), cy=str(int(h)))
                body = ET.SubElement(sp, P + 'txBody')
                for line in repair._text(element).rstrip('\n').split('\n'):
                    p = ET.SubElement(body, A + 'p')
                    for n, segment in enumerate(line.split('\v')):
                        if n:
                            ET.SubElement(p, A + 'br')
                        ET.SubElement(ET.SubElement(p, A + 'r'), A + 't').text = segment
            archive.writestr('ppt/slides/slide%d.xml' % index, ET.tostring(root))
    return out.getvalue()


class Source:
    def __init__(self):
        self.native = {'presentationId': FILE, 'revisionId': 'revision-7',
            'pageSize': {'width': {'magnitude': 720, 'unit': 'PT'}, 'height': {'magnitude': 405, 'unit': 'PT'}},
            'slides': [
                {'objectId': 'ReferencePage', 'pageElements': [shape('OriginalInventory',
                    '[[MARKER-INVENTORY:C09]] NOTES=2 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 '
                    'NOTES_IDS=Q1,BELL DRAFT_IDS=Q1 OPTIONAL_IDS=-', 10)]},
                {'objectId': 'AnswerPage', 'pageElements': [
                    shape('InitialLabel', 'Your first answer', 50),
                    shape('InitialResponse', '[[NOTES:C0  9:Q1]]\nThe original answer 😀 stays.', 70, height=50),
                    shape('RevisedLabel', 'Your revised answer', 140),
                    shape('RevisedResponse', 'Your revised answer [[DR\nThe revised answer 🌿 stays.\nAFT:C09:Q1]]', 160, height=70)]},
                {'objectId': 'BellPage', 'pageElements': [
                    shape('BellLabel', 'Bellringer', 50),
                    shape('BellResponse', 'What does the original question ask? The existing answer remains.', 70, height=60)]}],
            'masters': [], 'layouts': [], 'notesMaster': {}}
        self.meta = {'id': FILE, 'name': 'Fictional original', 'mimeType': completion.SLIDES,
                     'trashed': False, 'parents': ['FictionalPrivateFolder'], 'version': '7',
                     'modifiedTime': '2026-10-01T10:00:00Z', 'owners': [{'emailAddress': 'fictional@school.example'}],
                     'permissions': [{'id': 'fictional-owner', 'role': 'owner', 'type': 'user'}]}
        self.writes = 0
        self.after_write = None
        self.fail_write = False

    def write(self, token, file_id, requests, revision):
        assert revision == self.native['revisionId'] and file_id == FILE
        self.writes += 1
        objects = repair._elements(self.native)
        for request in requests:
            kind, operation = next(iter(request.items()))
            element = objects[operation['objectId']][2]
            chars = repair._chars(element)
            if kind == 'insertText':
                at = operation['insertionIndex']
                style, paragraph = deepcopy(chars[max(at - 1, 0)])
                chars[at:at] = [[[c.hex(), deepcopy(style[1])], deepcopy(paragraph)] for c in repair._units(operation['text'])]
            elif kind == 'deleteText':
                span = operation['textRange']
                del chars[span['startIndex']:span['endIndex']]
            elif kind == 'updateTextStyle':
                span = operation['textRange']
                for ch in chars[span['startIndex']:span['endIndex']]:
                    ch[0][1].update(deepcopy(operation['style']))
            else:
                raise AssertionError(kind)
            element['shape']['text']['textElements'] = text_elements(chars)
        self.native['revisionId'] = 'revision-8'
        self.meta.update(version='8', modifiedTime='2026-10-01T10:01:00Z')
        if self.after_write:
            self.after_write(self)
        if self.fail_write:
            raise OSError('uncertain Slides response')
        return {}

    def operations(self):
        elements = repair._elements(self.native)
        def op(marker, page, label, source, fragments, responses, all_responses):
            text = repair._text(elements[source][2]) if source else ''
            spans = []
            for fragment in fragments:
                index = text.index(fragment)
                start = len(repair._units(text[:index]))
                spans.append({'start': start, 'end': start + len(repair._units(fragment)), 'text': fragment})
            return {'marker': marker, 'page_id': page, 'label_object_id': label,
                    'source_object_id': source, 'remove': spans, 'response_object_ids': responses,
                    'page_response_object_ids': all_responses}
        fields = ['InitialResponse', 'RevisedResponse']
        return [op('[[NOTES:C09:Q1]]', 'AnswerPage', 'InitialLabel', 'InitialResponse',
                   ['[[NOTES:C0  9:Q1]]'], ['InitialResponse'], fields),
                op('[[DRAFT:C09:Q1]]', 'AnswerPage', 'RevisedLabel', 'RevisedResponse',
                   ['[[DR', 'AFT:C09:Q1]]'], ['RevisedResponse'], fields),
                op('[[NOTES:C09:BELL]]', 'BellPage', 'BellLabel', None, [], ['BellResponse'], ['BellResponse'])]


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.source, self.port = Source(), MemoryCAS()
        self.proof = {'class': 'fictional-bio', 'week': 'Wk 09', 'school_year': '2026-27',
                      'assignment_form_id': 'FictionalForm', 'source_form_id': 'FictionalForm',
                      'source_response_id': 'FictionalResponse', 'source_file_id': FILE,
                      'submitted_at': '2026-10-01T09:59:00Z', 'operator': 'operator@school.example',
                      'original_proof_sha256': 'a' * 64, 'question_set_sha256': 'b' * 64,
                      'question_set_status': 'complete'}
        self.guard_result = {k: self.proof[k] for k in ('original_proof_sha256', 'question_set_sha256', 'question_set_status')}
        self.guard = lambda: deepcopy(self.guard_result)
        patches = [patch.object(repair.google, 'drive_file', side_effect=lambda *a: deepcopy(self.source.meta)),
                   patch.object(repair.google, 'native_presentation', side_effect=lambda *a: deepcopy(self.source.native)),
                   patch.object(repair.google, 'deck_bytes', side_effect=lambda *a: pptx(self.source.native)),
                   patch.object(repair.google, 'slides_batch_update', side_effect=self.source.write)]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def plan(self, operations=None, snapshot=None):
        snap = snapshot or repair.capture('read', FILE)
        return repair.prepare('read', FILE, self.proof, snap['native']['revisionId'],
                              snap['native_sha256'], snap['metadata_sha256'],
                              self.source.operations() if operations is None else operations, guard=self.guard)

    def apply(self, plan=None):
        return repair.apply('read', 'write', plan or self.plan(), self.port, guard=self.guard, dry_run=False)

    def test_dry_run_is_default_and_writes_no_source_or_state(self):
        plan = self.plan()
        result = repair.apply('read', None, plan, None, guard=self.guard)
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['full_census_complete'])
        self.assertFalse(result['grading_authorized'])
        self.assertEqual(self.source.writes, 0)
        self.assertFalse(self.port.rows)

    def test_complete_exact_repairs_preserve_answers_styles_and_real_parser_binding(self):
        before = repair.capture('read', FILE)
        plan = self.plan(snapshot=before)
        result = self.apply(plan)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['repaired_codes'], 3)
        self.assertEqual(self.source.writes, 1)
        after = repair.capture('read', FILE)
        repair._preserved(before, after, plan['edits'])
        repair._bindings(after, plan['manifest']['operations'])
        self.assertEqual(completion.readiness(after['parsed'])['status'], 'READY')
        self.assertEqual(self.source.meta['parents'], before['metadata']['parents'])
        self.assertEqual(self.source.meta['permissions'], before['metadata']['permissions'])
        self.assertIn('🌿', repair._text(repair._elements(self.source.native)['RevisedResponse'][2]))
        self.assertFalse(result['full_census_complete'])
        self.assertFalse(result['grading_authorized'])

    def test_incomplete_question_census_can_plan_but_never_apply(self):
        self.proof['question_set_status'] = self.guard_result['question_set_status'] = 'incomplete'
        plan = self.plan()
        self.assertFalse(plan['apply_eligible'])
        with self.assertRaisesRegex(repair.Refused, 'census is unfinished'):
            self.apply(plan)
        self.assertEqual(self.source.writes, 0)
        self.assertFalse(self.port.rows)

    def test_changed_guard_census_or_form_proof_holds(self):
        for field in ('original_proof_sha256', 'question_set_sha256', 'question_set_status'):
            with self.subTest(field=field):
                original = self.guard_result[field]
                self.guard_result[field] = 'changed'
                with self.assertRaises(repair.Refused):
                    self.plan()
                self.guard_result[field] = original
        self.assertEqual(self.source.writes, 0)

    def test_stale_revision_or_capture_refused(self):
        snap = repair.capture('read', FILE)
        self.source.native['revisionId'] = 'revision-changed'
        with self.assertRaisesRegex(repair.Refused, 'changed since'):
            self.plan(snapshot=snap)
        self.assertEqual(self.source.writes, 0)

    def test_changed_content_same_revision_refused(self):
        snap = repair.capture('read', FILE)
        self.source.native['slides'][0]['slideProperties'] = {'notesChanged': True}
        with self.assertRaisesRegex(repair.Refused, 'changed since'):
            self.plan(snapshot=snap)

    def test_uncertain_write_is_never_retried_or_cleared(self):
        plan = self.plan()
        self.source.fail_write = True
        with self.assertRaises(repair.Uncertain):
            self.apply(plan)
        self.assertEqual(self.source.writes, 1)
        self.assertEqual(next(iter(self.port.rows.values())).value['status'], 'attempting')
        with self.assertRaises(repair.Refused):
            self.apply(plan)
        self.assertEqual(self.source.writes, 1)

    def test_native_readback_change_keeps_uncertainty_fence(self):
        def change(source):
            source.native['slides'][0]['slideProperties'] = {'changed': True}
        self.source.after_write = change
        with self.assertRaisesRegex(repair.Uncertain, 'native content changed'):
            self.apply()
        self.assertEqual(next(iter(self.port.rows.values())).value['status'], 'attempting')

    def test_changed_existing_text_style_is_not_hidden_by_code_edit(self):
        def change(source):
            element = repair._elements(source.native)['InitialResponse'][2]
            for run in element['shape']['text']['textElements']:
                if 'textRun' in run:
                    run['textRun']['style']['bold'] = True
        self.source.after_write = change
        with self.assertRaisesRegex(repair.Uncertain, 'styling changed'):
            self.apply()

    def test_access_parent_or_owner_change_stops_verification(self):
        for field, value in (('parents', ['OtherFolder']), ('owners', [{'emailAddress': 'other@example.com'}]),
                             ('permissions', [{'type': 'anyone', 'role': 'reader'}])):
            with self.subTest(field=field):
                self.source, self.port = Source(), MemoryCAS()
                # The bound method patch is replaced per subcase.
                with patch.object(repair.google, 'slides_batch_update', side_effect=self.source.write):
                    self.source.after_write = lambda source: source.meta.update({field: value})
                    with self.assertRaisesRegex(repair.Uncertain, 'access or location'):
                        self.apply()

    def test_code_in_student_writing_region_is_refused(self):
        ops = self.source.operations()
        ops[0]['label_object_id'] = 'InitialResponse'
        with self.assertRaisesRegex(repair.Refused, 'separate verified label'):
            self.plan(ops)

    def test_wrong_label_geometry_and_parser_binding_refused(self):
        ops = self.source.operations()
        ops[0]['label_object_id'] = 'RevisedLabel'
        with self.assertRaisesRegex(repair.Refused, 'above its response'):
            self.plan(ops)

    def test_wrong_cycle_undeclared_id_and_duplicate_refused(self):
        for marker in ('[[NOTES:C10:Q1]]', '[[NOTES:C09:NEW]]'):
            ops = self.source.operations(); ops[0]['marker'] = marker
            with self.subTest(marker=marker), self.assertRaises(repair.Refused):
                self.plan(ops)
        ops = self.source.operations(); ops.append(deepcopy(ops[0]))
        with self.assertRaisesRegex(repair.Refused, 'duplicated'):
            self.plan(ops)

    def test_general_answer_replacement_and_partial_fragments_refused(self):
        ops = self.source.operations()
        text = repair._text(repair._elements(self.source.native)['InitialResponse'][2])
        ops[0]['remove'] = [{'start': 0, 'end': len(repair._units(text[:-1])), 'text': text[:-1]}]
        with self.assertRaises(repair.Refused):
            self.plan(ops)
        ops = self.source.operations(); ops[1]['remove'][1]['text'] = 'student answer'
        with self.assertRaises(repair.Refused):
            self.plan(ops)

    def test_missing_original_or_table_target_refused(self):
        ops = self.source.operations(); ops[0]['source_object_id'] = 'AbsentOriginal'
        ops[0]['response_object_ids'] = ['AbsentOriginal']; ops[0]['page_response_object_ids'].append('AbsentOriginal')
        with self.assertRaises(repair.Refused):
            self.plan(ops)
        original = repair._elements(self.source.native)['InitialLabel'][2]
        original['table'] = {'tableRows': []}
        with self.assertRaisesRegex(repair.Refused, 'tables and groups'):
            self.plan()

    def test_unicode_surrogate_boundary_refused(self):
        text = 'A😀B'
        self.assertEqual(repair._boundary(text, 3), 2)
        with self.assertRaisesRegex(repair.Refused, 'splits an original Unicode'):
            repair._boundary(text, 2)

    def test_utf16_fragment_indices_after_non_bmp_prefix(self):
        element = repair._elements(self.source.native)['InitialResponse'][2]
        replacement = shape('InitialResponse', '😀 Before [[NOTES:C0  9:Q1]]\nThe answer stays.', 70, height=50)
        element.clear(); element.update(replacement)
        ops = self.source.operations()
        self.assertEqual(ops[0]['remove'][0]['start'], 10)
        self.assertEqual(self.apply(self.plan(ops))['status'], 'verified')

    def test_normal_parser_unavailable_before_write_is_not_bypassed(self):
        with patch.object(repair.google, 'deck_bytes', return_value=b'not a presentation'):
            with self.assertRaises(completion.Unreadable):
                self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_native_soft_break_is_preserved_but_normal_parser_binding_still_verified(self):
        element = repair._elements(self.source.native)['RevisedResponse'][2]
        replacement = shape('RevisedResponse', 'Your revised answer [[DR\vOriginal answer 🌿.\nAFT:C09:Q1]]', 160, height=70)
        element.clear(); element.update(replacement)
        self.assertEqual(self.apply()['status'], 'verified')
        self.assertIn('\vOriginal answer 🌿.\n', repair._text(element))

    def test_duplicate_existing_code_or_wrong_cycle_anywhere_refused(self):
        for marker in ('[[NOTES:C09:OTHER]]\n[[NOTES:C09:OTHER]]', '[[NOTES:C10:OTHER]]'):
            with self.subTest(marker=marker):
                self.source.native['slides'][0]['pageElements'].append(shape('ExtraCode', marker, 50))
                with self.assertRaisesRegex(repair.Refused, 'duplicate or wrong-cycle'):
                    self.plan()
                self.source.native['slides'][0]['pageElements'].pop()

    def test_label_order_fallback_is_not_accepted_as_verified_binding(self):
        objects = repair._elements(self.source.native)
        for ident, text in (('InitialLabel', 'First question'), ('RevisedLabel', 'Second question'),
                            ('RevisedResponse', '[[DR\nExisting response\nAFT:C09:Q1]]')):
            old = objects[ident][2]
            replacement = shape(ident, text, old['transform']['translateY'], height=old['size']['height']['magnitude'])
            old.clear(); old.update(replacement)
        with self.assertRaisesRegex(repair.Refused, 'ambiguous label distance'):
            self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_unknown_text_container_metadata_is_not_silently_removed_from_proof(self):
        element = repair._elements(self.source.native)['InitialResponse'][2]
        element['shape']['text']['unknownMetadata'] = 'preserve'
        with self.assertRaisesRegex(repair.Refused, 'text container metadata'):
            self.plan()

    def test_plan_tampering_cannot_change_apply_requests(self):
        plan = self.plan()
        plan['requests'] = [{'deleteObject': {'objectId': 'InitialResponse'}}]
        self.assertEqual(self.apply(plan)['status'], 'verified')
        self.assertIn('InitialResponse', repair._elements(self.source.native))

    def test_post_write_normal_parser_binding_mismatch_is_uncertain(self):
        plan = self.plan()
        real = repair._bindings
        def binding(snapshot, operations):
            if snapshot['native']['revisionId'] == 'revision-8':
                raise repair.Refused('normal parsed response binding changed')
            return real(snapshot, operations)
        with patch.object(repair, '_bindings', side_effect=binding):
            with self.assertRaisesRegex(repair.Uncertain, 'response binding changed'):
                self.apply(plan)
        self.assertEqual(self.source.writes, 1)
        self.assertEqual(next(iter(self.port.rows.values())).value['status'], 'attempting')

    def test_receipt_commit_uncertainty_is_reconciled_read_only(self):
        self.source.after_write = lambda source: setattr(self.port, 'fail_after_commit', True)
        self.assertEqual(self.apply()['status'], 'verified')
        self.assertEqual(self.source.writes, 1)

    def test_changed_or_unavailable_media_is_not_ignored(self):
        url = 'https://fictional.googleusercontent.com/image'
        self.source.native['slides'][0]['pageElements'].append({'objectId': 'OriginalImage', 'image': {'contentUrl': url}})
        content = {'sha256': 'a' * 64, 'basis': 'image_pixels'}
        with patch.object(repair.student_archive, '_native_image_proof', side_effect=lambda u: deepcopy(content)):
            self.source.after_write = lambda source: content.update(sha256='b' * 64)
            with self.assertRaisesRegex(repair.Uncertain, 'native content changed'):
                self.apply()

    def test_ephemeral_media_url_with_same_pixels_does_not_change_content(self):
        image = {'objectId': 'OriginalImage', 'image': {'contentUrl': 'https://fictional.googleusercontent.com/one'}}
        self.source.native['slides'][0]['pageElements'].append(image)
        with patch.object(repair.student_archive, '_native_image_proof', return_value={'basis': 'image_pixels', 'sha256': 'c' * 64}):
            self.source.after_write = lambda source: image['image'].update(contentUrl='https://fictional.googleusercontent.com/two')
            self.assertEqual(self.apply()['status'], 'verified')

    def test_fence_commit_uncertainty_does_not_write_source(self):
        self.port.fail_after_commit = True
        with self.assertRaises(Exception):
            self.apply()
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(next(iter(self.port.rows.values())).value['status'], 'attempting')

    def test_active_label_autofit_is_refused_before_any_write(self):
        label = repair._elements(self.source.native)['InitialLabel'][2]
        label['shape']['shapeProperties']['autofit'] = {'autofitType': 'TEXT_AUTOFIT'}
        with self.assertRaisesRegex(repair.Refused, 'auto-fitting label'):
            self.plan()
        self.assertEqual(self.source.writes, 0)

    def test_active_response_autofit_refused_before_preparing_a_write(self):
        for ident in ('InitialResponse', 'RevisedResponse'):
            for kind in ('TEXT_AUTOFIT', 'SHAPE_AUTOFIT'):
                with self.subTest(ident=ident, kind=kind):
                    self.source = Source()
                    response = repair._elements(self.source.native)[ident][2]
                    response['shape']['shapeProperties']['autofit'] = {'autofitType': kind}
                    with self.assertRaisesRegex(repair.Refused, 'auto-fitting response'):
                        self.plan()
                    self.assertEqual(self.source.writes, 0)
                    self.assertFalse(self.port.rows)

    def test_unresolved_or_effective_autofit_refused_for_every_mutated_shape(self):
        for ident in ('InitialLabel', 'InitialResponse', 'RevisedResponse'):
            for autofit in (None, {}, {'fontScale': .8}, {'autofitType': 'AUTOFIT_TYPE_UNSPECIFIED'},
                            {'autofitType': 'NONE', 'fontScale': .8},
                            {'autofitType': 'NONE', 'lineSpacingReduction': 5}):
                with self.subTest(ident=ident, autofit=autofit):
                    self.source = Source()
                    element = repair._elements(self.source.native)[ident][2]
                    element['shape']['placeholder'] = {'parentObjectId': 'InheritedLayoutShape'}
                    props = element['shape']['shapeProperties']
                    if autofit is None:
                        props.pop('autofit')
                    else:
                        props['autofit'] = deepcopy(autofit)
                    with self.assertRaisesRegex(repair.Refused, 'effective autofit'):
                        self.plan()
                    self.assertEqual(self.source.writes, 0)
                    self.assertFalse(self.port.rows)

    def test_unedited_response_autofit_is_preserved(self):
        response = repair._elements(self.source.native)['BellResponse'][2]
        response['shape']['shapeProperties']['autofit'] = {
            'autofitType': 'TEXT_AUTOFIT', 'fontScale': .8, 'lineSpacingReduction': 5}
        before = deepcopy(response)
        self.assertEqual(self.apply()['status'], 'verified')
        self.assertEqual(response, before)

    def test_invalid_declared_inventory_refused_even_when_missing_id_is_known(self):
        changes = (
            ('NOTES_IDS=Q1,BELL', 'NOTES_IDS=Q1,Q1'),
            ('NOTES_IDS=Q1,BELL', 'NOTES_IDS=Q1,Q1,BELL'),
            ('NOTES=2', 'NOTES=3'),
            ('DRAFT=1', 'DRAFT=2'),
            ('NOTES_IDS=Q1,BELL', 'NOTES_IDS=Q1,BELL NOTES_IDS=Q1,BELL'),
            ('NOTES=2', 'NOTES=2 NOTES=2'),
            ('NOTES=2', 'NOTES=2.5'),
            ('NOTES=2', 'NOTES=-2'),
            ('BANK_TERMS=0', ''),
            ('BANK_TERMS=0', 'BANK_TERMS=1'),
            ('DRAFT_IDS=Q1', 'DRAFT_IDS=UNPAIRED'),
            ('OPTIONAL_IDS=-', 'OPTIONAL_IDS=UNDECLARED'),
        )
        for old, new in changes:
            with self.subTest(old=old, new=new):
                self.source = Source()
                inventory = repair._elements(self.source.native)['OriginalInventory'][2]
                value = repair._text(inventory).replace(old, new)
                inventory.clear(); inventory.update(shape('OriginalInventory', value, 10))
                with self.assertRaisesRegex(repair.Refused, 'declared inventory|declared ID list'):
                    self.plan()
                self.assertEqual(self.source.writes, 0)
                self.assertFalse(self.port.rows)

    def test_inventory_on_another_slide_refused_before_preparing_a_write(self):
        first, answer = self.source.native['slides'][:2]
        answer['pageElements'].append(first['pageElements'].pop())
        with self.assertRaisesRegex(repair.Refused, 'inventory must be unique and on slide 1'):
            self.plan()
        self.assertEqual(self.source.writes, 0)
        self.assertFalse(self.port.rows)

    def test_valid_observed_bank_count_without_id_list_still_supported(self):
        inventory = repair._elements(self.source.native)['OriginalInventory'][2]
        value = repair._text(inventory).replace('BANK_TERMS=0', 'BANK_TERMS=1')
        inventory.clear(); inventory.update(shape('OriginalInventory', value, 10))
        self.source.native['slides'].append({'objectId': 'BankPage', 'pageElements': [
            shape('Term', '[[BANK:C09:Term]]', 30)]})
        self.assertEqual(self.apply()['status'], 'verified')

    def test_mixed_original_character_styles_survive_shifted_utf16_ranges(self):
        response = repair._elements(self.source.native)['RevisedResponse'][2]
        chars = repair._chars(response)
        for item in chars[30:40]:
            item[0][1].update({'bold': True, 'italic': True})
        response['shape']['text']['textElements'] = text_elements(chars)
        before = repair.capture('read', FILE)
        plan = self.plan(snapshot=before)
        self.assertEqual(self.apply(plan)['status'], 'verified')
        repair._preserved(before, repair.capture('read', FILE), plan['edits'])

    def test_lost_fence_stops_before_source_write(self):
        real_read = self.port.read
        calls = 0
        def read(keys):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.port.rows.clear()
            return real_read(keys)
        with patch.object(self.port, 'read', side_effect=read):
            with self.assertRaisesRegex(repair.Uncertain, 'fence changed'):
                self.apply()
        self.assertEqual(self.source.writes, 0)

    def test_changed_metadata_at_last_bookend_stops_before_source_write(self):
        original = self.port.commit
        def commit(changes):
            original(changes)
            self.source.meta['parents'] = ['ChangedFolder']
        with patch.object(self.port, 'commit', side_effect=commit):
            with self.assertRaisesRegex(repair.Uncertain, 'changed before apply'):
                self.apply()
        self.assertEqual(self.source.writes, 0)


if __name__ == '__main__':
    unittest.main()
