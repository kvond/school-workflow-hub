"""Native regenerated exports are not a new source; Office bytes stay strict."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from hub_v2 import app, combined_assessment as combined, completion, google, morning, work
from test_combined_assessment import agenda, captures, declaration, form, FORM, S1, S2, T1, T2
from test_hub_v2 import pptx


def native_captures(raw='a'):
    values = captures()
    for fid in (T2, S2):
        values[fid]['metadata']['mimeType'] = completion.SLIDES
        values[fid]['sha256'] = raw * 64
    return values


class NativeCombinedSourceBasisTests(unittest.TestCase):
    def bind(self, values):
        value = form()
        value['students'][0]['earlier'][0]['files'][0]['mime'] = completion.SLIDES
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(values[fid])):
            combined.bind_form('fictional-read', value, declaration())
        self.assertIsNone(value['combined_assessment']['problem'])
        return value

    def test_actual_native_export_bytes_remain_audited_while_full_parsed_basis_is_stable(self):
        first = pptx([[('prompt', 'Exact original question', (0, 0, 900, 80))]])
        stream = BytesIO(first)
        with ZipFile(stream, 'a') as archive:
            archive.comment = b'Provider regenerated this export container'
        second = stream.getvalue()
        self.assertNotEqual(first, second)
        metadata = native_captures()[T2]['metadata']
        with patch.object(google, 'drive_file', return_value=metadata) as meta, \
                patch.object(google, 'deck_bytes', side_effect=[first, second]):
            before = combined.read_source('fictional-read', T2)
            after = combined.read_source('fictional-read', T2)
        self.assertEqual(meta.call_count, 4)
        self.assertEqual(before['sha256'], hashlib.sha256(first).hexdigest())
        self.assertEqual(after['sha256'], hashlib.sha256(second).hexdigest())
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertEqual(before['deck'], after['deck'])
        self.assertEqual(combined.source_basis(before), combined.source_basis(after))
        self.assertEqual(combined.source_basis(before)['sha256'], combined.digest(before['deck']))
        self.assertEqual(combined.source_basis(before)['hash_basis'], 'parsed_completion_deck')

    def test_same_native_content_allows_fresh_scoring_and_identical_morning_list_revision(self):
        first, next_export = native_captures('a'), native_captures('b')
        bound = self.bind(first)
        before = deepcopy(bound)
        with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(next_export[fid])):
            scored = combined.score_row('fictional-read', bound, combined.candidates(bound)[0])
        self.assertEqual((scored['notes_score'], scored['revision_score']), (5, 5))
        self.assertEqual(bound, before)
        configuration = {'HUB_IC_ASSIGNMENT_MAP': json.dumps([{
            'class_key': 'forensics', 'form_id': FORM, 'targets': []}])}
        results = []
        for values in (first, next_export):
            original = form()
            original['students'][0]['earlier'][0]['files'][0]['mime'] = completion.SLIDES
            def load(*args, **kwargs):
                combined.bind_form('fictional-read', original, declaration())
                return {'lessons': [{'topic': 'Searching and Photography', 'forms': [original]}], 'problems': []}
            with patch.object(combined, 'read_source', side_effect=lambda token, fid: deepcopy(values[fid])), \
                    patch.object(work, 'week_policy', return_value={'excluded': False, 'problem': None}), \
                    patch.object(completion, 'assignment_readiness', return_value={'status': 'READY'}), \
                    patch.object(morning.ledger, 'read', return_value={'configured': True}), \
                    patch.object(morning, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
                results.append(morning.make_list('fictional-read', configuration, agenda(), 'forensics',
                                                 'Wk 06', FORM, load, app._score_rows))
        self.assertTrue(all(value['code'] == 'ready' for value in results))
        self.assertEqual(results[0]['list']['rows'], results[1]['list']['rows'])
        self.assertEqual(results[0]['revision'], results[1]['revision'])

    def test_native_marker_answer_geometry_style_metadata_or_version_change_holds_before_assess(self):
        original = native_captures()
        value = self.bind(original)
        for fid in (T2, S2):
            for change in ('marker', 'answer', 'box', 'style', 'version', 'name', 'MIME', 'parents'):
                with self.subTest(fid=fid, change=change):
                    changed = deepcopy(original)
                    capture = changed[fid]
                    if change == 'marker':
                        capture['deck']['slides'][1]['units'][5]['text'] = '[[NOTES:F06b:CHANGED-QUESTION]]'
                    elif change == 'answer':
                        capture['deck']['slides'][1]['units'][2]['text'] += ' A new actual response.'
                    elif change == 'box':
                        unit = capture['deck']['slides'][1]['units'][2]
                        unit['box'] = (unit['box'][0] + 1, *unit['box'][1:])
                    elif change == 'style':
                        capture['deck']['slides'][1]['units'][2]['style'] = 'Changed original source style'
                    elif change == 'MIME': capture['metadata']['mimeType'] = google.PPTX
                    elif change == 'parents': capture['metadata']['parents'] = ['DIFFERENT_FICTIONAL_PARENT']
                    else: capture['metadata'][change] = 'Changed'
                    with patch.object(combined, 'read_source', side_effect=lambda token, source: deepcopy(changed[source])), \
                            patch.object(completion, 'assess') as assess:
                        held = combined.score_row('fictional-read', value, combined.candidates(value)[0])
                    self.assertEqual(held['code'], 'coverage_incomplete')
                    assess.assert_not_called()

    def test_office_byte_change_with_same_metadata_and_parsed_deck_still_holds(self):
        original = native_captures()
        value = self.bind(original)
        for fid in (T1, S1):
            with self.subTest(fid=fid):
                changed = deepcopy(original)
                changed[fid]['sha256'] = 'f' * 64
                self.assertEqual(changed[fid]['metadata'], original[fid]['metadata'])
                self.assertEqual(changed[fid]['deck'], original[fid]['deck'])
                self.assertEqual(combined.source_basis(changed[fid])['hash_basis'], 'original_file_bytes')
                with patch.object(combined, 'read_source', side_effect=lambda token, source: deepcopy(changed[source])), \
                        patch.object(completion, 'assess') as assess:
                    held = combined.score_row('fictional-read', value, combined.candidates(value)[0])
                self.assertEqual(held['code'], 'coverage_incomplete'); assess.assert_not_called()

    def test_native_metadata_bookend_change_and_unsupported_type_are_not_export_serialization(self):
        raw = pptx([[('prompt', 'Exact original question', (0, 0, 900, 80))]])
        metadata = native_captures()[T2]['metadata']
        with patch.object(google, 'drive_file', side_effect=[metadata, dict(metadata, version='2')]), \
                patch.object(google, 'deck_bytes', return_value=raw), self.assertRaises(combined.Held):
            combined.read_source('fictional-read', T2)
        for mime in ('application/pdf', '', None):
            changed = native_captures()[T2]
            changed['metadata']['mimeType'] = mime
            with self.subTest(mime=mime), self.assertRaises(combined.Held):
                combined.source_basis(changed)
            with patch.object(google, 'drive_file', return_value=changed['metadata']), \
                    patch.object(google, 'deck_bytes') as read, self.assertRaises(combined.Held):
                combined.read_source('fictional-read', T2)
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
