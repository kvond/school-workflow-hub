"""Synthetic same-submission repairs, forged proof controls and billing safety."""
import base64
from copy import deepcopy
import hashlib
import json
import unittest
import zlib
from unittest.mock import patch

from hub_v2 import native_feedback as fb, source_repair_provenance as provenance
from tests.test_assessment_provider import OPENAI_CONFIG
from tests.test_cloud_state import MemoryCAS
from school_workflow_hub.core.cloud_state import bounded_json
from tests.test_native_feedback import inputs, report


def case():
    old = inputs()
    scope, file_id = old['scope'], old['scope']['file']
    form = {'source_file_id': file_id, 'source_form_id': scope['form'],
            'source_response_id': 'FictionalResponse123', 'assignment_form_id': scope['form'],
            'submitted_at': '2026-10-01T09:00:00Z'}
    old.update(submission_receipt=form, submitted_at=form['submitted_at'])
    old_meta = {'id': file_id, 'name': 'Fictional source', 'mimeType': fb.completion.SLIDES,
                'version': '7', 'modifiedTime': '2026-10-01T10:00:00Z', 'trashed': False,
                'parents': ['FictionalPrivateFolder'], 'owners': [{'emailAddress': 'owner@school.example'}],
                'permissions': [{'id': 'fictional-owner', 'type': 'user', 'role': 'owner'}]}
    new_meta = {**deepcopy(old_meta), 'version': '8', 'modifiedTime': '2026-10-10T10:00:00Z'}
    native = {'presentationId': file_id, 'revisionId': 'revision-7',
              'pageSize': {'width': {'magnitude': 720, 'unit': 'PT'}, 'height': {'magnitude': 405, 'unit': 'PT'}},
              'slides': [{'objectId': 'FictionalPage1', 'pageElements': [
                  {'objectId': 'FictionalAnswer1', 'size': {'width': {'magnitude': 100, 'unit': 'PT'},
                                                           'height': {'magnitude': 20, 'unit': 'PT'}},
                   'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 10, 'translateY': 10, 'unit': 'PT'},
                   'shape': {'shapeType': 'TEXT_BOX', 'text': {'textElements': [
                       {'textRun': {'content': 'Carbon comes from the air.', 'style': {'bold': False}}}]}}}]}]}
    after_native = deepcopy(native)
    after_native['revisionId'] = 'revision-8'
    after_native['slides'][0]['pageElements'][0]['description'] = '[[DRAFT:C04:CA1-MASTERY]]'
    old['source']['file'] = {'id': file_id, 'name': old_meta['name'], 'mime_type': old_meta['mimeType'],
                             'modified_at': old_meta['modifiedTime'], 'version': old_meta['version']}
    old['source']['sha256'] = hashlib.sha256(old['source']['text'].encode()).hexdigest()
    old['source']['raw_sha256'] = old['source']['sha256']
    old['source']['metadata_sha256'] = provenance.json_hash(old_meta)
    current = deepcopy(old)
    current['source']['file'].update(version='8', modified_at=new_meta['modifiedTime'])
    current['source']['metadata_sha256'] = provenance.json_hash(new_meta)
    def captured(source, meta, deck):
        raw = json.dumps(deck, ensure_ascii=False, separators=(',', ':')).encode()
        return {'metadata': meta, 'capture': {k: deepcopy(source[k]) for k in
                                             ('file', 'sha256', 'raw_sha256', 'metadata_sha256')},
                'native_raw_encoding': 'zlib',
                'native_raw_base64': base64.b64encode(zlib.compress(raw)).decode(),
                'native_raw_sha256': hashlib.sha256(raw).hexdigest()}
    receipt = {'schema': provenance.SCHEMA, 'record_key': provenance.receipt_key(scope, old['source'], current['source']),
               'scope': {k: scope[k] for k in ('class', 'form', 'file')}, 'submission_receipt': deepcopy(form),
               'before': captured(old['source'], old_meta, native),
               'after': captured(current['source'], new_meta, after_native),
               'operations': [{'kind': 'code_alt_text', 'object_id': 'FictionalAnswer1', 'before': {},
                               'after': {'description': '[[DRAFT:C04:CA1-MASTERY]]'}}],
               'attestation': {'kind': 'private_host_source_readback', 'operator': 'operator@school.example',
                               'verified_at': '2026-10-10T10:05:00Z',
                               'pages': [{'page_id': 'FictionalPage1', 'before_render_sha256': 'c' * 64,
                                          'after_render_sha256': 'd' * 64}]}}
    return old, current, receipt


def change_native(receipt, side, transform):
    item = receipt[side]
    raw = base64.b64decode(item['native_raw_base64'])
    native = json.loads(zlib.decompress(raw) if item['native_raw_encoding'] == 'zlib' else raw)
    transform(native)
    raw = json.dumps(native, separators=(',', ':')).encode()
    item['native_raw_base64'] = base64.b64encode(zlib.compress(raw) if item['native_raw_encoding'] == 'zlib' else raw).decode()
    item['native_raw_sha256'] = hashlib.sha256(raw).hexdigest()


class VerifiedRepairTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.storage = patch.object(fb, 'configured_store', return_value=self.port)
        self.model = patch.object(fb, '_call', return_value=(json.dumps(report()), {'input_tokens': 100, 'output_tokens': 50}))
        self.storage.start(); self.call = self.model.start()
        self.addCleanup(self.storage.stop); self.addCleanup(self.model.stop)
        self.old, self.current, self.receipt = case()

    def save_old(self):
        draft = fb.generate(OPENAI_CONFIG, self.old)
        fb.save(OPENAI_CONFIG, draft['id'], self.old)
        return draft['id']

    def attest(self, receipt=None):
        value = receipt or self.receipt
        self.port.commit({provenance.receipt_key(self.old['scope'], self.old['source'], self.current['source']): (value, None)})

    def test_verified_code_only_repair_reopens_original_report_without_rebilling_or_growth(self):
        ident = self.save_old()
        delivery = {'Printed': True, 'Handed out': False}
        self.port.commit({'fictional-delivery': (delivery, None)})
        self.attest()
        before = deepcopy(self.port.read([ident]).documents[ident].value)
        result = fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(result['id'], ident)
        self.assertTrue(result['cached'])
        self.assertEqual(result['source_change']['classification'], 'same_submission_code_repair')
        self.assertEqual(result['source_change']['original_submitted_at'], self.old['submitted_at'])
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.port.read([ident]).documents[ident].value, before)
        self.assertEqual(self.port.read(['fictional-delivery']).documents['fictional-delivery'].value, delivery)
        # A historical report is never relabelled with repaired bytes or allowed
        # to bypass the existing fresh-source Save check.
        with self.assertRaisesRegex(fb.Held, 'changed after this draft'):
            fb.save(OPENAI_CONFIG, ident, self.current)

    def test_missing_private_receipt_or_client_claims_hold_before_another_provider_reservation(self):
        ident = self.save_old()
        before = deepcopy(self.port.rows)
        self.current['repair_receipt'] = self.receipt
        self.current['source']['code_repair_verified'] = True
        with self.assertRaisesRegex(fb.Held, 'trusted technical-repair provenance'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.port.rows, before)
        self.assertTrue(fb.get_saved(OPENAI_CONFIG, ident).value['saved_at'])

    def test_new_codes_or_unavailable_old_analysis_require_correction_review(self):
        self.save_old(); self.attest()
        self.current['answers'] = [{'category': 'NOTES', 'id': 'NEW-REAL-ACTIVITY', 'status': 'done',
                                    'answer': 'The retained original response is now assessable.'}]
        with self.assertRaisesRegex(fb.Held, 'corrected analysis of the original submission'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(self.call.call_count, 1)

    def test_preexisting_report_without_raw_provenance_is_never_backfilled_or_silently_reused(self):
        self.old['source'].pop('raw_sha256'); self.old['source'].pop('metadata_sha256')
        self.receipt['record_key'] = provenance.receipt_key(self.old['scope'], self.old['source'], self.current['source'])
        ident = self.save_old(); self.attest()
        before = deepcopy(fb.get_saved(OPENAI_CONFIG, ident).value)
        with self.assertRaisesRegex(fb.Held, 'corrected analysis'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(fb.get_saved(OPENAI_CONFIG, ident).value, before)
        self.assertEqual(self.call.call_count, 1)

    def test_real_later_form_revision_does_not_inherit_the_old_technical_repair(self):
        ident = self.save_old(); self.attest()
        newer = deepcopy(self.current)
        newer['submission_receipt'].update(source_response_id='FictionalResponse124', submitted_at='2026-10-11T09:00:00Z')
        newer['submitted_at'] = newer['submission_receipt']['submitted_at']
        newer['source']['text'] = 'A genuinely later student explanation.'
        newer['source']['sha256'] = hashlib.sha256(newer['source']['text'].encode()).hexdigest()
        newer['source']['raw_sha256'] = newer['source']['sha256']
        newer['source']['file'].update(version='9', modified_at='2026-10-11T09:00:00Z')
        generated = fb.generate(OPENAI_CONFIG, newer)
        self.assertNotEqual(generated['id'], ident)
        self.assertEqual(self.call.call_count, 2)
        with self.assertRaises(fb.Held):
            fb.save(OPENAI_CONFIG, generated['id'], self.current)

    def test_authenticated_resubmission_can_retain_the_same_form_response_id(self):
        ident = self.save_old(); self.attest()
        self.current['submission_receipt']['submitted_at'] = '2026-10-11T09:00:00Z'
        self.current['submitted_at'] = self.current['submission_receipt']['submitted_at']
        self.current['source']['sha256'] = 'f' * 64
        self.assertNotEqual(fb.generate(OPENAI_CONFIG, self.current)['id'], ident)
        self.assertEqual(self.call.call_count, 2)

    def test_later_real_revision_with_unchanged_extracted_text_keeps_its_exact_new_source_version(self):
        ident = self.save_old()
        self.current['submission_receipt'].update(source_response_id='FictionalResponse124', submitted_at='2026-10-11T09:00:00Z')
        self.current['submitted_at'] = self.current['submission_receipt']['submitted_at']
        # Geometry/media changes can retain every extracted text byte. They
        # must not retrieve the earlier revision merely because text matches.
        result = fb.generate(OPENAI_CONFIG, self.current)
        self.assertNotEqual(result['id'], ident)
        saved = fb.save(OPENAI_CONFIG, result['id'], self.current)
        value = fb.get_saved(OPENAI_CONFIG, saved['id']).value
        self.assertEqual(value['source'], {k: self.current['source'][k] for k in ('file', 'sha256', 'raw_sha256', 'metadata_sha256')})
        self.assertEqual(value['submission_receipt'], self.current['submission_receipt'])
        self.assertEqual(fb.generate(OPENAI_CONFIG, self.current)['id'], result['id'])
        self.assertEqual(self.call.call_count, 2)

    def test_later_real_revision_does_not_reuse_a_text_identical_legacy_report_without_raw_hashes(self):
        self.old['source'].pop('raw_sha256'); self.old['source'].pop('metadata_sha256')
        ident = self.save_old()
        self.current['submission_receipt'].update(source_response_id='FictionalResponse124', submitted_at='2026-10-11T09:00:00Z')
        self.current['submitted_at'] = self.current['submission_receipt']['submitted_at']
        result = fb.generate(OPENAI_CONFIG, self.current)
        self.assertNotEqual(result['id'], ident)
        self.assertEqual(fb.get_saved(OPENAI_CONFIG, result['id']).value['source']['file']['version'], '8')
        self.assertTrue(fb.save(OPENAI_CONFIG, result['id'], self.current)['readback_verified'])
        self.assertEqual(self.call.call_count, 2)

    def test_same_event_code_repair_after_unfinished_call_cannot_bypass_the_call_anchor(self):
        self.call.side_effect = TimeoutError('fictional timeout')
        with self.assertRaisesRegex(fb.Held, 'prevent duplicate billing'):
            fb.generate(OPENAI_CONFIG, self.old)
        before = deepcopy(self.port.rows)
        with self.assertRaisesRegex(fb.Held, 'earlier feedback call against different source bytes'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(self.port.rows, before)
        self.assertEqual(self.call.call_count, 1)
        with self.assertRaisesRegex(fb.Held, 'interrupted'):
            fb.generate(OPENAI_CONFIG, self.old)
        self.assertEqual(self.call.call_count, 1)

    def test_uncertain_atomic_anchor_reservation_never_calls_the_provider_on_retry(self):
        from school_workflow_hub.core.cloud_state import StoreUnavailable
        self.port.fail_after_commit = True
        with self.assertRaises(StoreUnavailable):
            fb.generate(OPENAI_CONFIG, self.old)
        before = deepcopy(self.port.rows)
        with self.assertRaisesRegex(fb.Held, 'interrupted'):
            fb.generate(OPENAI_CONFIG, self.old)
        self.assertEqual(self.call.call_count, 0)
        self.assertEqual(self.port.rows, before)

    def test_changed_private_receipt_during_verification_holds_before_reuse_or_billing(self):
        self.save_old(); self.attest()
        ident = provenance.receipt_key(self.old['scope'], self.old['source'], self.current['source'])
        read, changed = self.port.read, False
        def concurrent_read(ids):
            nonlocal changed
            snapshot = read(ids)
            if ids == [ident] and not changed:
                changed = True
                doc = snapshot.documents[ident]
                self.port.commit({ident: (doc.value, doc.version)})
            return snapshot
        with patch.object(self.port, 'read', side_effect=concurrent_read), \
                self.assertRaisesRegex(fb.Held, 'trusted technical-repair provenance'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertTrue(changed)
        self.assertEqual(self.call.call_count, 1)

    def test_complete_native_captures_larger_than_one_megabyte_fit_only_with_lossless_compression(self):
        self.save_old()
        proof = deepcopy(self.receipt)
        for side in ('before', 'after'):
            change_native(proof, side, lambda n: n.update(completeSyntheticProviderData='original-provider-data' * 60000))
            raw = zlib.decompress(base64.b64decode(proof[side]['native_raw_base64']))
            self.assertGreater(len(raw), 1_200_000)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), proof[side]['native_raw_sha256'])
        self.assertLess(len(bounded_json(proof).encode()), 250000)
        self.attest(proof)
        self.assertEqual(fb.generate(OPENAI_CONFIG, self.current)['source_change']['classification'], 'same_submission_code_repair')

    def test_existing_state_size_boundary_is_enforced_without_expanding_the_cas_limit(self):
        self.save_old()
        proof = deepcopy(self.receipt); proof['operator_note'] = ''
        proof['operator_note'] = 'x' * (250000 - len(bounded_json(proof).encode()))
        self.assertEqual(len(bounded_json(proof).encode()), 250000)
        self.attest(proof)
        self.assertEqual(fb.generate(OPENAI_CONFIG, self.current)['source_change']['classification'], 'same_submission_code_repair')
        proof['operator_note'] += 'x'
        self.assert_unknown(proof)

    def test_compressed_bombs_truncation_extra_streams_and_corruption_hold(self):
        self.save_old()
        packed = base64.b64decode(self.receipt['after']['native_raw_base64'])
        for label, blob in {
            'bomb': zlib.compress(b'x' * (provenance.MAX_NATIVE_BYTES + 1)),
            'truncated': packed[:-2], 'extra-stream': packed + zlib.compress(b'extra'),
            'corrupt': b'not-zlib',
        }.items():
            with self.subTest(label=label):
                proof = deepcopy(self.receipt)
                proof['after']['native_raw_base64'] = base64.b64encode(blob).decode()
                self.assert_unknown(proof)

    def test_exact_off_canvas_code_addition_has_an_inverse_but_visible_or_prose_additions_hold(self):
        self.save_old()
        proof = deepcopy(self.receipt)
        code = {'objectId': 'FictionalCodes2', 'size': {'width': {'magnitude': 100, 'unit': 'PT'},
                                                    'height': {'magnitude': 20, 'unit': 'PT'}},
                'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': -200, 'translateY': 0, 'unit': 'PT'},
                'shape': {'shapeType': 'TEXT_BOX', 'text': {'textElements': [
                    {'textRun': {'content': '[[NOTES:C04:N1]]'}}]}}}
        def append(n):
            n['slides'][0]['pageElements'].append(deepcopy(code))
        change_native(proof, 'after', append)
        proof['operations'].append({'kind': 'add_off_canvas_codes', 'object_id': code['objectId'], 'page_id': 'FictionalPage1'})
        self.attest(proof)
        self.assertEqual(fb.generate(OPENAI_CONFIG, self.current)['source_change']['classification'], 'same_submission_code_repair')
        for label, change in {
            'visible': lambda n: n['slides'][0]['pageElements'][-1]['transform'].update(translateX=10),
            'prose': lambda n: n['slides'][0]['pageElements'][-1]['shape']['text']['textElements'][0]['textRun'].update(content='A copied answer would be unearned credit.'),
            'metadata-prose': lambda n: n['slides'][0]['pageElements'][-1].update(title='A copied teacher prompt'),
        }.items():
            with self.subTest(label=label):
                invalid = deepcopy(proof); change_native(invalid, 'after', change); self.assert_unknown(invalid)

    def test_receipt_for_previous_source_version_cannot_clear_a_later_unknown_edit(self):
        self.save_old(); self.attest()
        self.current['source']['file'].update(version='9', modified_at='2026-10-11T09:00:00Z')
        with self.assertRaisesRegex(fb.Held, 'trusted technical-repair provenance'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(self.call.call_count, 1)

    def test_invalid_or_stale_proofs_cannot_hide_real_answer_prompt_geometry_style_or_media_edits(self):
        self.save_old()
        controls = {
            'answer': lambda n: n['slides'][0]['pageElements'][0]['shape']['text']['textElements'][0]['textRun'].update(content='Changed student answer'),
            'prompt': lambda n: n['slides'][0].update(speakerNotes='Changed teacher prompt'),
            'geometry': lambda n: n['slides'][0]['pageElements'][0]['transform'].update(translateX=100),
            'style': lambda n: n['slides'][0]['pageElements'][0]['shape']['text']['textElements'][0]['textRun']['style'].update(bold=True),
            'media': lambda n: n['slides'][0]['pageElements'].append({'objectId': 'NewPicture', 'image': {'sourceUrl': 'https://example.test/changed'}}),
        }
        for label, change in controls.items():
            with self.subTest(label=label):
                proof = deepcopy(self.receipt)
                change_native(proof, 'after', change)
                self.assert_unknown(proof)
        for label, change in {
            'raw-hash': lambda p: p['after'].update(native_raw_sha256='0' * 64),
            'form': lambda p: p['submission_receipt'].update(source_response_id='OtherResponse'),
            'source': lambda p: p['scope'].update(file='OtherSource'),
            'version': lambda p: p['after']['metadata'].update(version='99'),
            'access': lambda p: p['after']['metadata']['permissions'].append({'type': 'anyone', 'role': 'reader'}),
            'location': lambda p: p['after']['metadata'].update(parents=['OtherFolder']),
            'visual': lambda p: p['attestation'].update(pages=[]),
            'operator': lambda p: p['attestation'].update(operator='claimed-by-client'),
            'operation': lambda p: p['operations'][0].update(kind='replace_student_text'),
        }.items():
            with self.subTest(label=label):
                proof = deepcopy(self.receipt); change(proof); self.assert_unknown(proof)

    def assert_unknown(self, proof):
        ident = provenance.receipt_key(self.old['scope'], self.old['source'], self.current['source'])
        old = self.port.read([ident]).documents[ident]
        self.port.commit({ident: (proof, old.version if old else None)})
        before = deepcopy(self.port.rows)
        with self.assertRaisesRegex(fb.Held, 'trusted technical-repair provenance'):
            fb.generate(OPENAI_CONFIG, self.current)
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.port.rows, before)


if __name__ == '__main__':
    unittest.main()
