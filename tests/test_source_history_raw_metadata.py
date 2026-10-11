"""Raw provider fingerprints remain strict while display-only names may differ."""
from copy import deepcopy
from urllib.parse import unquote
import unittest
from unittest.mock import Mock, patch

from hub_v2 import completion, google, review_context, source_history_dispositions as d
from school_workflow_hub.core.cloud_state import Document, Snapshot
from school_workflow_hub.core.runs import content_hash
from test_source_history_dispositions import receipt, captures, configured, NOW, STUDENT, OLDER
from test_form_source_bindings_and_case_policy import Agenda

def fixture(prefixed=True):
    value, _, _ = receipt()
    row, found, scope, roster, _ = captures()
    for snapshot in [row] + row['earlier']:
        for upload in snapshot['files']:
            upload['mime'] = google.PPTX
    result = review_context.build(scope, found)
    basis = d.current_basis(Agenda(), {}, scope, found, row, roster)
    value['scope'], value['roster_sha256'], value['candidate_sha256'] = basis['scope'], basis['roster_sha256'], basis['candidate_sha256']
    raw = {}
    for source in value['sources']:
        prefix = 'Fictional.Inv5. '
        md = {**source['metadata'], 'mimeType': google.PPTX, 'name': 'Copy LIVE Investigative.pptx',
              'properties': {}, 'md5Checksum': 'a' * 32, 'size': '12345',
              'owners': [{'emailAddress': 's.ann.avery@fictional.example'}]}
        if prefixed:
            md['name'] = prefix + md['name']
            md['properties'][google.PREFIX_TAG] = google._prefix_tag(prefix)
        source['metadata'] = raw[source['file_id']] = md
        source['metadata_sha256'] = content_hash(md)
        source['identity_attribution']['metadata_sha256'] = source['metadata_sha256']
        source['identity_sha256'] = content_hash(source['identity_attribution'])
    value['record_key'] = d.receipt_key(value)
    basis.update(expected_targets=value['native_saved']['targets'], native_instruction_sha256=value['instruction_sha256'])
    return value, basis, result['notices'][0], found, scope, roster, result, raw

def apply_raw(data, change=None, final_drift=False):
    value, basis, detail, found, scope, roster, result, raw = data
    store = Mock()
    store.read.return_value = Snapshot({value['record_key']: Document(value, '1')}, NOW.isoformat())
    counts = {}
    def read(url, token, params, timeout):
        fid = unquote(url.rsplit('/', 1)[1])
        counts[fid] = counts.get(fid, 0) + 1
        if params != {'fields': d.META, 'supportsAllDrives': 'true'} or timeout != 10:
            raise AssertionError('Only exact raw metadata GET permitted')
        md = deepcopy(raw[fid])
        if fid == STUDENT and change:md.update(deepcopy(change))
        if final_drift and counts[fid] > 2:md['version'] = 'changed'
        return md
    with patch.object(d, 'configured_store', return_value=store), \
         patch.object(google, 'get_json', side_effect=read), \
         patch.object(google, 'drive_file', side_effect=AssertionError('display reader forbidden')), \
         patch.object(completion, 'assess', side_effect=AssertionError('no assessment')), \
         patch.object(google, 'deck_bytes', side_effect=AssertionError('no source bytes')), \
         patch.object(google, 'append_rows', side_effect=AssertionError('no school write')):
        out = d.apply('fictional-readonly', configured(value), Agenda(), scope, found, result, roster)
    store.commit.assert_not_called()
    return out

class RawSourceMetadataTests(unittest.TestCase):
    def test_prefixed_PPTX_display_shape_does_not_replace_raw_fingerprint(self):
        data = fixture();value, basis, detail, *_, raw = data
        shown = {fid: google.as_read(deepcopy(md)) for fid, md in raw.items()}
        self.assertTrue(all('shown_name' in md and md['name'] != raw[fid]['name'] for fid, md in shown.items()))
        with self.assertRaises(ValueError):d.verify_current(value, basis, detail, shown, now=NOW)
        before = deepcopy(data)
        out = apply_raw(data)
        self.assertEqual(out['status'], 'history_retained')
        self.assertEqual(out['notices'], [])
        self.assertEqual(out['retained_history'][0]['sources'], detail['sources'])
        self.assertFalse(out['source_selection']['assessment_performed'])
        self.assertEqual(data, before)

    def test_plain_PPTX_provider_shape_stays_literal(self):
        data = fixture(prefixed=False)
        raw = data[-1]
        self.assertEqual({fid: google.as_read(deepcopy(md)) for fid, md in raw.items()}, raw)
        self.assertEqual(apply_raw(data)['status'], 'history_retained')

    def test_real_metadata_change_or_spoofed_display_field_retains_notice(self):
        data = fixture()
        variants = [{'name': 'A genuinely renamed file.pptx'}, {'modifiedTime': '2026-10-11T22:00:00Z'},
                    {'version': '99'}, {'md5Checksum': 'b' * 32}, {'size': '54321'},
                    {'parents': ['DIFFERENT_PARENT_12345']}, {'trashed': True},
                    {'owners': [{'emailAddress': 'other@fictional.example'}]}, {'properties': {}},
                    {'name': 'Different.pptx', 'shown_name': data[-1][STUDENT]['name']}]
        for change in variants:
            with self.subTest(change=change):
                out = apply_raw(data, change)
                self.assertEqual(out['notices'], data[6]['notices'])
                self.assertFalse(out['retained_history'])

    def test_raw_final_bookend_drift_discards_partial_history(self):
        data = fixture()
        out = apply_raw(data, final_drift=True)
        self.assertEqual(out['notices'], data[6]['notices'])
        self.assertFalse(out.get('retained_history'))
        self.assertIn('could not be checked', ' '.join(out['limitations']))

    def test_transport_is_exact_GET_fields_no_display_or_mutation(self):
        raw = fixture()[-1][STUDENT];before = deepcopy(raw)
        with patch.object(google, 'get_json', return_value=raw) as get, \
             patch.object(google, 'drive_file', side_effect=AssertionError('no display helper')):
            self.assertIs(d._source_metadata('read', STUDENT), raw)
            get.assert_called_once_with('https://www.googleapis.com/drive/v3/files/' + STUDENT,
                'read', {'fields': d.META, 'supportsAllDrives': 'true'}, timeout=10)
            self.assertEqual(raw, before)
            for ident, fields in (('../permissions', d.META), (STUDENT, 'permissions')):
                with self.assertRaises(ValueError):d._source_metadata('read', ident, fields)
            self.assertEqual(get.call_count, 1)
