"""Fictional request-local capture, fresh metadata and provider-error boundaries."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hub_v2 import app, combined_assessment as combined, completion, google, morning


class RequestCapturesTests(unittest.TestCase):
    def setUp(self):
        self.meta = {'id': 'FictionalSource0001', 'mimeType': completion.SLIDES, 'trashed': False,
                     'version': '7', 'modifiedTime': '2026-10-01T12:00:00Z', 'parents': ['FictionalFolder0001'], 'properties': {}}
        self.get = patch.object(google, 'drive_file', side_effect=lambda *_: deepcopy(self.meta)).start()
        self.raw = patch.object(google, 'deck_bytes', return_value=b'fictional-source').start()
        self.parser = patch.object(completion, 'read_deck', return_value={'slides': [{'units': []}]}).start()
        self.addCleanup(patch.stopall)

    def read(self, token='fictional-token'):
        return combined.read_source(token, self.meta['id'])

    def test_one_request_one_export_each_reuse_and_return_reads_current_metadata(self):
        with combined.source_request():
            first = self.read(); second = self.read()
            self.assertEqual(first, second)
            self.assertEqual(self.get.call_count, 4)
        self.assertEqual(self.get.call_count, 5)
        self.assertEqual(self.raw.call_count, 1)
        with combined.source_request(): self.read()
        self.assertEqual(self.raw.call_count, 2)
        self.assertIsNone(combined._request_captures.get())

    def test_capture_cannot_be_mutated_by_a_worker_or_survive_scope(self):
        with combined.source_request():
            first=self.read(); first['deck']['slides'].clear()
            self.assertEqual(len(self.read()['deck']['slides']),1)
        with self.assertRaises(combined.Held):
            with combined.source_request():
                self.read(); self.read('different-token')
        self.assertIsNone(combined._request_captures.get())

    def test_each_full_metadata_change_holds_before_reusing_teacher_or_student(self):
        for field,new in [('version','8'),('modifiedTime','2026-10-02T12:00:00Z'),('parents',['other']),('properties',{'new':'value'})]:
            original=deepcopy(self.meta)
            with self.subTest(field=field),self.assertRaises(combined.Held):
                with combined.source_request():
                    self.read(); self.meta[field]=new; self.read()
            self.meta=original

    def test_reuse_mid_read_change_and_request_end_change_both_hold(self):
        for call in (4,5):
            self.get.reset_mock()
            counter=[0]
            def metadata(*_):
                counter[0]+=1;value=deepcopy(self.meta)
                if counter[0] == call:value['version']='8'
                return value
            self.get.side_effect=metadata
            with self.subTest(call=call),self.assertRaises(combined.Held):
                with combined.source_request():self.read();self.read()

    def test_genuine_permission_loss_during_reuse_or_final_bookend_stays_held(self):
        for call in (3,5):
            counter=[0]
            def metadata(*_):
                counter[0]+=1
                if counter[0]==call:raise google.GoogleError('Actual permission denied',403,'insufficientFilePermissions')
                return deepcopy(self.meta)
            self.get.side_effect=metadata
            with self.subTest(call=call),self.assertRaises(google.GoogleError):
                with combined.source_request():self.read();self.read()

    def office(self, checksum=True):
        self.meta['mimeType']=google.PPTX
        if checksum:
            self.meta.update(md5Checksum=hashlib.md5(b'fictional-source').hexdigest(),size=str(len(b'fictional-source')))

    def test_office_reuse_requires_current_checksum_and_size(self):
        self.office()
        with combined.source_request():self.read();self.read()
        self.assertEqual(self.raw.call_count,1)
        for field,new in [('md5Checksum','0'*32),('size','999')]:
            with self.subTest(field=field),self.assertRaises(combined.Held):
                with combined.source_request():self.read();self.meta[field]=new;self.read()
            self.office()

    def test_changed_office_bytes_cannot_match_declared_checksum_or_size(self):
        self.office();self.raw.return_value=b'changed-office-bytes'
        with self.assertRaises(combined.Held):
            with combined.source_request():self.read()
        self.assertEqual(self.raw.call_count,1)

    def test_office_without_checksum_reads_actual_bytes_each_use_and_exposes_change(self):
        self.office(checksum=False);self.raw.side_effect=[b'old',b'new']
        with combined.source_request():
            before=self.read();after=self.read()
        self.assertEqual(self.raw.call_count,2)
        self.assertNotEqual(combined.source_basis(before),combined.source_basis(after))

    def test_concurrent_scoring_workers_share_only_the_current_request_capture(self):
        rows=[{'matched':True,'files':[{'id':self.meta['id'],'mime':completion.SLIDES}]} for _ in range(4)]
        def score(*_,**__):self.read();return {'notes_score':10,'revision_score':None}
        with patch.object(completion,'scoring_candidates',return_value=rows),patch.object(completion,'score_file',side_effect=score):
            with combined.source_request():
                self.assertEqual(len(app._score_rows('fictional-token',None,'fictional','Wk 1','FictionalForm0001',{},{})),4)
        self.assertEqual(self.raw.call_count,1)
        self.assertIsNone(combined._request_captures.get())

    def test_failed_provider_read_never_automatically_retries_or_returns_ready(self):
        self.raw.side_effect=google.GoogleError('Actual quota reason',403,'rateLimitExceeded')
        def inner(*_,**__):self.read();return {'code':'ready'}
        with patch.object(morning,'_make_list',side_effect=inner):
            result=morning.make_list('fictional-token',{},None,'fictional','Wk 1','FictionalForm0001',None,None)
        self.assertEqual(result['code'],'coverage_incomplete');self.assertEqual(result['provider_reason'],'rateLimitExceeded')
        self.assertEqual(self.raw.call_count,1)
        self.assertIsNone(combined._request_captures.get())

    def test_source_read_problem_is_not_relabelled_as_manual_scoring(self):
        form={'students':[{'matched':True,'section':'fictional','first':'Student','last':'Example','files':[{'id':'Fictional0001'}]}],
              'late':[],'missing':[],'excused':[]}
        scored=[(form['students'][0],{'problem':'Google rejected the request (403); its access or request limit needs review.'})]
        with patch.object(completion,'scoring_candidates',return_value=form['students']):
            result=morning.rows(form,scored,None)
        self.assertEqual(result[0]['kind'],'check');self.assertEqual(result[0]['reason'],scored[0][1]['problem'])


    def test_long_multiline_source_or_selection_problem_has_valid_plain_helper_reason(self):
        problem = "Actual source read problem:\n\t" + "\x00Review\x7f  " * 30 + "📄" * 100
        for category in ('source', 'selection'):
            held = {'matched': True, 'section': 'fictional', 'first': 'Held', 'last': 'Example'}
            ready = {'matched': True, 'section': 'fictional', 'first': 'Ready', 'last': 'Example'}
            if category == 'selection': held['selection_problem'] = problem
            form = {'students': [held, ready], 'late': [], 'missing': [], 'excused': []}
            scored = [(held, {'problem': problem}), (ready, {'notes_score': 5.3, 'revision_score': 10})]
            original = deepcopy(scored)
            with self.subTest(category=category):
                result = morning.rows(form, scored, None)
                self.assertEqual(result[0]['kind'], 'check')
                self.assertTrue(result[0]['reason'].startswith('Actual source read problem: Review'))
                self.assertEqual(len(result[0]['reason'].encode('utf-16-le')) // 2, 200)
                self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in result[0]['reason']))
                self.assertEqual(result[1], {'last': 'Example', 'first': 'Ready', 'section': 'fictional',
                                            'kind': 'score', 'notes': 5.3, 'revision': 10, 'reason': ''})
                self.assertEqual(scored, original)
        emoji_form = {'students': [held], 'late': [], 'missing': [], 'excused': []}
        held.pop('selection_problem', None)
        emoji_result = morning.rows(emoji_form, [(held, {'problem': '📄' * 150})], None)
        self.assertEqual(emoji_result[0]['reason'], '📄' * 100)


class ActualProviderReasonTests(unittest.TestCase):
    def response(self,reason):
        payload={'error':{'errors':[{'reason':reason}]}} if reason else {}
        return SimpleNamespace(status_code=403,json=lambda:payload)

    def test_actual_quota_reason_is_distinct_from_permission_loss(self):
        quota=google._response_error(self.response('rateLimitExceeded'))
        permission=google._response_error(self.response('insufficientFilePermissions'))
        self.assertEqual(quota.status,403);self.assertEqual(quota.reason,'rateLimitExceeded')
        self.assertIn('limiting requests',str(quota));self.assertNotIn('access',str(quota))
        self.assertIn("doesn't have access",str(permission))

    def test_unknown_or_ambiguous403_is_not_a_quota_or_permission_cause_claim(self):
        error=google._response_error(self.response(None))
        self.assertIsNone(error.reason);self.assertNotIn("doesn't have access",str(error))
        self.assertNotIn('limiting requests',str(error))
        self.assertNotIn('no entry',str(error))


if __name__=='__main__':unittest.main()
