"""Reason-only controls; no change to eligibility, scores or source history."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from hub_v2 import morning

NOW=datetime(2026,10,11,12,tzinfo=timezone.utc)
ROW={'first':'Fictional','last':'Student','section':'A3 Forensics','matched':True}
FORM='exact-current-Form';OTHER='exact-other-Form'

def fixture():
    return {'form_id':FORM,'window':{'from':'2026-09-01T00:00:00Z','at':'2026-09-20T23:59:00Z'},'students':[],'late':[], 'missing':[deepcopy(ROW)],'excused':[],
            'elsewhere':[{'who':'Student, Fictional','section':ROW['section'],'to':'Wk 03 · Authentic other lesson — Turn In','accepted_intake':False,'source_form_id':FORM,'assignment_form_id':OTHER,'url':'https://docs.google.com/presentation/d/authentic-original/edit','file':'Misleading current-lesson filename'}]}

class HeldRouteReason(unittest.TestCase):
    def call(self,f,scores=None):return morning.rows(f,[(r,(scores or {}).get(morning.person(r),{})) for r in f["students"]],now=NOW,recheck=True)
    def test_exact_other_lesson_named_without_changing_check_or_source(self):
        f=fixture();before=deepcopy(f);rows=self.call(f)
        self.assertEqual(len(rows),1);r=rows[0];self.assertEqual(r['kind'],'check');self.assertIsNone(r['notes']);self.assertIsNone(r['revision'])
        self.assertIn('Authentic other lesson',r['reason']);self.assertIn('original turn-in route',r['reason']);self.assertIn('preserve current grades',r['reason']);self.assertNotIn('Misleading',r['reason']);self.assertEqual(f,before)
    def test_unverified_or_ambiguous_route_never_guesses_lesson(self):
        for mode in ('wrong-current','missing-target','same-target','ambiguous','filename-only'):
            f=fixture();item=f['elsewhere'][0]
            if mode=='wrong-current':item['source_form_id']='another-current-Form'
            elif mode=='missing-target':item.pop('assignment_form_id')
            elif mode=='same-target':item['assignment_form_id']=FORM
            elif mode=='ambiguous':f['elsewhere'].append(dict(item,to='Wk 04 · Second other lesson'))
            else:item['to']=''
            with self.subTest(mode=mode):
                r=self.call(f)[0];self.assertEqual(r['kind'],'check');self.assertNotIn('Authentic other lesson',r['reason']);self.assertIn('confirm its original lesson',r['reason'])
    def test_missing_or_blank_current_Form_cannot_authenticate_a_route(self):
        for current in (None, ""):
            for source in (None, ""):
                f=fixture()
                if current is None:f.pop("form_id")
                else:f["form_id"]=current
                if source is None:f["elsewhere"][0].pop("source_form_id")
                else:f["elsewhere"][0]["source_form_id"]=source
                before=deepcopy(f)
                with self.subTest(current=current, source=source):
                    r=self.call(f)[0]
                    self.assertEqual(r["kind"],"check")
                    self.assertIsNone(r["notes"]);self.assertIsNone(r["revision"])
                    self.assertNotIn("Authentic other lesson",r["reason"])
                    self.assertIn("confirm its original lesson",r["reason"])
                    self.assertEqual(f,before)

    def test_existing_numeric_and_history_check_rows_keep_exact_behavior(self):
        f=fixture();f['students']=[dict(ROW,selection_problem='Submission history has no verified original Form event; confirm the current revision.')];f['missing']=[]
        r=self.call(f)[0];self.assertEqual(r['reason'],f['students'][0]['selection_problem']);self.assertEqual(r['kind'],'check')
        f['students'][0].pop('selection_problem');score={'notes_score':10,'revision_score':None};scores={morning.person(ROW):score}
        r=self.call(f,scores)[0];self.assertEqual(r['kind'],'score');self.assertEqual(r['notes'],10);self.assertIsNone(r['revision'])
    def test_accepted_elsewhere_and_other_person_remain_no_new_grade(self):
        f=fixture();f['elsewhere'][0]['accepted_intake']=True;r=self.call(f)[0];self.assertEqual(r['kind'],'none');self.assertIn('accepted through this Form',r['reason'])
        f=fixture();f['elsewhere'][0]['section']='A2 Forensics';r=self.call(f)[0];self.assertEqual(r['kind'],'none');self.assertNotIn('Authentic',r['reason'])
    def test_long_multiline_label_keeps_plain200_and_next_action(self):
        f=fixture();f['elsewhere'][0]['to']='Other lesson\n'+('x'*300)+'\x00bad'
        r=self.call(f)[0];self.assertEqual(r['kind'],'check');self.assertLessEqual(len(r['reason'].encode('utf-16-le')),400);self.assertNotIn('\n',r['reason']);self.assertNotIn('\x00',r['reason']);self.assertIn('preserve current grades',r['reason'])

if __name__=='__main__':unittest.main()
