"""Fictional retained source captures for repeated feedback reads (#240).

No provider, school source or writer calls. Export counts are asserted, not
only output shapes.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import threading
import unittest
from unittest.mock import patch

from hub_v2 import app, combined_assessment as combined, completion, google
from test_combined_assessment import captures, declaration, event, form, T1, T2, S1

TOKEN = 'fictional-token'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RetainedSourcesTests(unittest.TestCase):
    def setUp(self):
        self.values = captures()
        self.students = ['FICTIONAL_RETAINED_COPY_%02d' % i for i in range(12)]
        for fid in self.students:
            self.values[fid] = deepcopy(self.values[S1])
            self.values[fid]['metadata'].update(id=fid, mimeType=completion.SLIDES)
        for teacher in (T1, T2):
            self.values[teacher]['metadata']['mimeType'] = completion.SLIDES
        self.metadata_calls, self.exports = Counter(), Counter()
        self.lock = threading.Lock()
        self.quota = None
        self.clock = Clock()
        self.store = combined.RetainedSources(clock=self.clock)
        self.addCleanup(patch.stopall)

        def metadata(_token, fid, fields):
            self.assertEqual(fields, combined.SOURCE_FIELDS)
            with self.lock:
                self.metadata_calls[fid] += 1
            return deepcopy(self.values[fid]['metadata'])

        def raw(_token, fid, _mime):
            with self.lock:
                if self.quota is not None:
                    if self.quota <= 0:
                        raise google.GoogleError('Google is rate-limiting requests.', 429)
                    self.quota -= 1
                self.exports[fid] += 1
            return fid.encode()

        self.metadata = patch.object(google, 'drive_file', side_effect=metadata).start()
        self.raw = patch.object(google, 'deck_bytes', side_effect=raw).start()
        patch.object(completion, 'read_deck',
                     side_effect=lambda raw: deepcopy(self.values[raw.decode()]['deck'])).start()
        patch.object(google, 'get_json', side_effect=AssertionError('No provider calls in fictional tests')).start()

    def assignment(self):
        value = form()
        value['students'] = [event(fid, first='Student%d' % i, last='Example',
                                   email='s.student%d.example@fictional.example' % i)
                             for i, fid in enumerate(self.students)]
        return value

    def bind(self, store=None):
        value = self.assignment()
        if store is None:
            combined.bind_form(TOKEN, value, declaration())
        else:
            with combined.retained_sources(store):
                combined.bind_form(TOKEN, value, declaration())
        return value

    def read(self, fid, token=TOKEN):
        with combined.retained_sources(self.store):
            return combined.read_source(token, fid)

    def test_repeated_whole_form_reads_hit_quota_without_retention(self):
        # Former path: every listing, draft and save exports every original.
        self.quota = len(self.students) + 2
        first = self.bind()
        self.assertIsNone(first['combined_assessment']['problem'])
        second = self.bind()
        self.assertIn('rate-limiting', second['combined_assessment']['problem'])
        self.assertEqual(second['grade_readiness']['code'], 'coverage_incomplete')

    def test_retained_reads_export_once_and_fresh_check_every_reuse(self):
        self.quota = len(self.students) + 2
        first = self.bind(self.store)
        self.assertEqual(self.exports, Counter({fid: 1 for fid in self.students + [T1, T2]}))
        calls = sum(self.metadata_calls.values())
        second = self.bind(self.store)
        third = self.bind(self.store)
        for value in (second, third):
            self.assertIsNone(value['combined_assessment']['problem'])
            self.assertEqual(value['combined_assessment']['sources'], first['combined_assessment']['sources'])
            self.assertEqual(value['combined_assessment']['teachers'], first['combined_assessment']['teachers'])
        self.assertEqual(self.exports, Counter({fid: 1 for fid in self.students + [T1, T2]}))
        # Each reuse still reads current metadata before and after.
        self.assertGreaterEqual(sum(self.metadata_calls.values()) - calls, 2 * 2 * (len(self.students) + 2))

    def test_changed_metadata_re_exports_and_never_serves_the_old_capture(self):
        fid = self.students[0]
        first = self.read(fid)
        for field, new in [('version', '99'), ('modifiedTime', '2026-10-09T12:00:00Z'),
                           ('parents', ['FictionalOtherFolder01']), ('properties', {'moved': 'yes'})]:
            with self.subTest(field=field):
                self.values[fid]['metadata'][field] = new
                self.values[fid]['deck'] = {'slides': [{'units': [{'text': field}]}]}
                again = self.read(fid)
                self.assertEqual(again['metadata'][field], new)
                self.assertEqual(again['deck'], self.values[fid]['deck'])
                self.assertNotEqual(again['deck'], first['deck'])
        self.assertEqual(self.exports[fid], 5)

    def test_change_during_reuse_holds_and_evicts(self):
        fid = self.students[0]
        self.read(fid)
        counter = [0]
        original = self.metadata.side_effect

        def changes_after_first(token, file_id, fields):
            counter[0] += 1
            value = original(token, file_id, fields)
            if counter[0] == 2:
                value['version'] = '100'
            return value

        self.metadata.side_effect = changes_after_first
        with self.assertRaises(combined.Held):
            self.read(fid)
        self.metadata.side_effect = original
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)

    def test_unreadable_trashed_or_unsupported_source_is_never_hidden(self):
        fid = self.students[0]
        self.read(fid)
        for change in ({'trashed': True}, {'mimeType': 'application/pdf'}, {'version': ''}):
            with self.subTest(change=change):
                saved = deepcopy(self.values[fid]['metadata'])
                self.values[fid]['metadata'].update(change)
                with self.assertRaises(combined.Held):
                    self.read(fid)
                self.values[fid]['metadata'] = saved
        self.metadata.side_effect = google.GoogleError('Actual permission denied', 403, 'insufficientFilePermissions')
        with self.assertRaises(google.GoogleError):
            self.read(fid)
        self.assertEqual(self.exports[fid], 1)

    def test_reader_identity_scopes_every_capture(self):
        fid = self.students[0]
        self.read(fid)
        self.read(fid, token='other-fictional-token')
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        self.assertFalse(any(TOKEN in str(key) for key in self.store.values))

    def test_office_bytes_are_retained_only_with_the_provider_checksum(self):
        fid = self.students[0]
        self.values[fid]['metadata']['mimeType'] = google.PPTX
        self.read(fid); self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        self.values[fid]['metadata'].update(md5Checksum=hashlib.md5(fid.encode()).hexdigest(),
                                            size=str(len(fid.encode())))
        self.read(fid); self.read(fid)
        self.assertEqual(self.exports[fid], 3)

    def test_expiry_and_entry_bound(self):
        fid = self.students[0]
        self.read(fid)
        self.clock.now += combined.RETAIN_SECONDS
        self.read(fid)
        self.assertEqual(self.exports[fid], 2)
        small = combined.RetainedSources(entries=3, clock=self.clock)
        with combined.retained_sources(small):
            for i, student in enumerate(self.students[:5]):
                self.clock.now += 1
                combined.read_source(TOKEN, student)
        self.assertEqual(len(small.values), 3)
        self.assertEqual({key[1] for key in small.values}, set(self.students[2:5]))

    def test_captures_cannot_be_mutated_through_a_returned_copy(self):
        fid = self.students[0]
        self.read(fid)['deck']['slides'].clear()
        self.assertEqual(self.read(fid)['deck'], self.values[fid]['deck'])

    def test_identical_concurrent_reads_share_one_export(self):
        fid = self.students[0]
        started, release = threading.Event(), threading.Event()
        raw = self.raw.side_effect

        def slow(token, file_id, mime):
            started.set()
            release.wait(timeout=3)
            return raw(token, file_id, mime)

        self.raw.side_effect = slow
        results, errors = [], []

        def worker():
            try:
                results.append(self.read(fid))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        threads[0].start()
        started.wait(timeout=3)
        for thread in threads[1:]:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        self.assertEqual(self.exports[fid], 1)
        self.assertEqual(self.store.flights, {})

    def test_request_scope_bookend_still_checks_retained_reuse(self):
        fid = self.students[0]
        self.read(fid)
        with self.assertRaises(combined.Held):
            with combined.retained_sources(self.store), combined.source_request():
                combined.read_source(TOKEN, fid)
                self.values[fid]['metadata']['version'] = '101'
        self.assertEqual(self.exports[fid], 1)

    def test_no_store_keeps_the_former_export_per_read(self):
        fid = self.students[0]
        combined.read_source(TOKEN, fid)
        combined.read_source(TOKEN, fid)
        self.assertEqual(self.exports[fid], 2)

    def test_app_feedback_scope_is_one_shared_store(self):
        with app.feedback_sources():
            first = combined._retained_store.get()
        with app.feedback_sources():
            self.assertIs(combined._retained_store.get(), first)
        self.assertIsNone(combined._retained_store.get())
        self.assertIsInstance(first, combined.RetainedSources)


if __name__ == '__main__':
    unittest.main()
