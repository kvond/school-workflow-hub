"""Synthetic original Form events and real parser output; no provider writes."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock, patch
import unittest

from hub_v2 import app, completion, google, morning
from test_completion_readiness import FORM, TEMPLATE, source, single_assignment_agenda

HELD='FICTIONAL_AMBIGUOUS_ORIGINAL'
READY='FICTIONAL_READY_ORIGINAL'
NOW=datetime(2026,10,23,15,tzinfo=timezone.utc)
AT='2026-09-22T14:00:00Z'


def row(last,fid):
    return {'first':'Alex','last':last,'section':'A2 Fictional','matched':True,'matched_by':'email',
        'source':'form','email':last.lower()+'@fictional.example','submitted_at':AT,
        'files':[{'id':fid,'mime':google.PPTX,'name':'Original.pptx','source_form_id':FORM,
                  'source_response_id':'response-'+last,'source_submitted_at':AT}]}


def ambiguous():
    deck=source('Original answer preserved.')
    deck['slides'][1]['units'].append(deepcopy(deck['slides'][1]['units'][-2]))
    return deck


def metadata(fid):
    return {'id':fid,'name':'Exact original','mimeType':google.PPTX,'version':'4',
            'modifiedTime':'2026-10-22T14:00:00Z','trashed':False,'parents':['FICTIONAL_PARENT']}


class LocalReadinessTests(unittest.TestCase):
    def setUp(self):
        completion._results.clear()
        self.rows=[row('Avery',READY),row('Brook',HELD)]
        self.form={'form_id':FORM,'label':'Photography — Turn in','students':self.rows,
            'missing':[],'late':[],'excused':[],'elsewhere':[],'problem':None,
            'window':{'from':'2026-09-01T00:00:00Z','at':'2026-09-30T00:00:00Z'}}
        self.found={'lessons':[{'topic':'Photography','forms':[self.form]}],'problems':[]}
        self.config={'HUB_IC_ASSIGNMENT_MAP':json.dumps([{'class_key':'forensics','form_id':FORM,'targets':[]}])}
        self.ready={'status':'READY'}
        self.decks={HELD:ambiguous(),READY:source('Complete original answer.')}
        self.meta=metadata
        self.teacher=source()
        self.strict=False
    def tearDown(self):completion._results.clear()
    def patches(self,stack):
        stack.enter_context(patch.object(completion,'student_coding_required',side_effect=lambda *a:self.strict))
        stack.enter_context(patch.object(completion,'_teacher_template',side_effect=lambda *a:(metadata(TEMPLATE),self.teacher)))
        stack.enter_context(patch.object(google,'drive_file',side_effect=lambda token,fid,fields:self.meta(fid)))
        stack.enter_context(patch.object(completion,'_read',side_effect=lambda token,fid,meta:self.decks[fid]))
        stack.enter_context(patch.object(completion,'assignment_readiness',return_value=self.ready))
        stack.enter_context(patch.object(morning.ledger,'read',return_value={'configured':True}))
        clock=stack.enter_context(patch.object(morning,'datetime',wraps=datetime));clock.now.return_value=NOW
    def make(self):
        with ExitStack() as stack:
            self.patches(stack)
            return morning.make_list('read',self.config,single_assignment_agenda(),'forensics','Wk 04',FORM,
                                     Mock(return_value=self.found),app._score_rows)
    def score(self,fid=HELD):
        with ExitStack() as stack:
            self.patches(stack)
            return completion.score_file('read',single_assignment_agenda(),'forensics','Wk 04',FORM,fid,self.found)
    def test_duplicate_codes_hold_only_exact_student_and_preserve_answers(self):
        before=deepcopy(self.decks);result=self.make()
        self.assertEqual(result['code'],'ready')
        self.assertEqual([(r['last'],r['kind'],r['notes']) for r in result['list']['rows']],
                         [('Avery','score',10),('Brook','check',None)])
        self.assertIsNone(result['list']['rows'][1]['revision'])
        self.assertEqual(self.decks,before)
        self.assertFalse(any(key[0]==HELD for key in completion._results))
    def test_typed_hold_is_not_a_cached_or_deserializable_authority(self):
        held=self.score()
        self.assertTrue(completion.is_student_content_hold(self.rows[1],held,FORM))
        self.assertFalse(completion.is_student_content_hold(self.rows[1],json.loads(json.dumps(held)),FORM))
        for field,value in [('file_version','5'),('file_id',READY),('file_updated','changed'),('notes_score',0)]:
            changed=deepcopy(held);changed[field]=value
            self.assertFalse(completion.is_student_content_hold(self.rows[1],changed,FORM))
        self.assertFalse(completion.is_student_content_hold(self.rows[0],held,FORM))
        for field,value in [('email','other@fictional.example'),('last','Other'),('matched',False),('matched_by','file name')]:
            changed=deepcopy(self.rows[1]);changed[field]=value
            self.assertFalse(completion.is_student_content_hold(changed,held,FORM))
    def test_incomplete_original_identity_and_event_remain_assignment_holds(self):
        for field,value in [('matched_by','file name'),('email',''),('source','folder')]:
            before=deepcopy(self.rows[1]);self.rows[1][field]=value
            with self.subTest(field=field):self.assertEqual(self.make()['code'],'coverage_incomplete')
            self.rows[1].clear();self.rows[1].update(before)
        self.rows[1]['files'][0]['source_response_id']=''
        self.assertEqual(self.make()['code'],'coverage_incomplete')
    def test_original_read_and_final_metadata_failures_remain_global(self):
        original=self.decks
        class Unavailable(dict):
            def __getitem__(self,key):
                if key==HELD:raise google.GoogleError('provider read unavailable',status=503)
                return super().__getitem__(key)
        self.decks=Unavailable(original)
        self.assertEqual(self.make()['code'],'coverage_incomplete')
        self.decks=original
        count={}
        def changed(fid):
            count[fid]=count.get(fid,0)+1
            return {**metadata(fid),**({'version':'5'} if fid==HELD and count[fid]>1 else {})}
        self.meta=changed
        self.assertEqual(self.make()['code'],'coverage_incomplete')
        def fail_final_teacher(fid):
            if fid==TEMPLATE:raise google.GoogleError('teacher source bookend unavailable',status=503)
            return metadata(fid)
        self.meta=fail_final_teacher
        self.assertEqual(self.make()['code'],'coverage_incomplete')
    def test_teacher_ambiguity_missing_source_and_student_wrong_cycle_stay_global(self):
        self.teacher=ambiguous();self.assertEqual(self.make()['code'],'coverage_incomplete')
        self.teacher=source()
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(completion,'_teacher_template',side_effect=google.GoogleError('unavailable')))
            held=completion.score_file('read',single_assignment_agenda(),'forensics','Wk 04',FORM,HELD,self.found)
        self.assertEqual(held['code'],'deck_not_ready')
        self.assertNotIn('_student_content_hold',held)
        self.decks[HELD]=ambiguous()
        self.decks[HELD]['slides'][1]['units'][-1]['text']='[[NOTES:OTHER:Q-MASTERY]]'
        self.assertEqual(self.make()['code'],'coverage_incomplete')
    def test_unreadable_registered_teacher_cannot_be_replaced_by_same_title_sibling(self):
        missing='FICTIONAL_UNREADABLE_REGISTERED_TEACHER'
        sibling='FICTIONAL_SAME_TITLE_REPLACEMENT'
        wrong=source()
        for slide in wrong['slides']:
            for unit in slide['units']:
                unit['text']=unit['text'].replace('F06','OTHER')
        def teacher(token,fid):
            if fid==missing:
                raise google.GoogleError('registered teacher unavailable')
            return metadata(fid),wrong if fid==TEMPLATE else source()
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(completion,'lesson_decks',return_value=[TEMPLATE,missing]))
            stack.enter_context(patch.object(completion,'_teacher_template',side_effect=teacher))
            stack.enter_context(patch.object(completion,'original_title',return_value='Exact original'))
            siblings=stack.enter_context(patch.object(completion,'_siblings',return_value=[
                {'id':sibling,'name':'Exact original'}]))
            held=completion.score_file('read',single_assignment_agenda(),'forensics','Wk 04',FORM,HELD,self.found)
        self.assertEqual(held['code'],'deck_not_ready')
        self.assertNotIn('_student_content_hold',held)
        siblings.assert_not_called()

    def test_truncated_boundary_never_localizes(self):
        self.decks[HELD]['slides'] += [{'number':i,'units':[]} for i in range(3,201)]
        self.assertEqual(len(self.decks[HELD]['slides']),completion.MAX_SLIDES)
        self.assertEqual(self.make()['code'],'coverage_incomplete')
    def test_photosynthesis_strict_copy_policy_and_global_source_holds_unchanged(self):
        self.strict=True;self.assertEqual(self.make()['code'],'coverage_incomplete')
        self.strict=False
        self.found['problems']=['canonical roster unavailable']
        self.assertEqual(self.make()['code'],'coverage_incomplete')
        self.found['problems']=[];self.form['problem']='partial Form response census'
        self.assertEqual(self.make()['code'],'coverage_incomplete')
    def test_all_held_and_repeated_missing_row_never_zero(self):
        self.form['students']=[self.rows[1]]
        self.form['missing']=[{k:self.rows[1][k] for k in ('first','last','section')}]
        self.assertEqual(self.make(),{'code':'source_scores_held','source_holds':{'checks':1}})
    def test_ordinary_missing_codes_still_na_without_zero_or_local_hold(self):
        self.decks[HELD]=source(header='Unmarked original',codes=False)
        result=self.make();self.assertEqual(result['code'],'ready')
        self.assertEqual([r['kind'] for r in result['list']['rows']],['score','none'])
        held=self.score();self.assertNotIn('_student_content_hold',held)
        self.assertIsNone(held['notes_score']);self.assertIsNone(held['revision_score'])


if __name__=='__main__':unittest.main()
